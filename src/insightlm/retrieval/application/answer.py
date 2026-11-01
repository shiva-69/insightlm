"""Answering a question, with the citation contract enforced.

The shape of this use case *is* the thesis:

    retrieve -> generate -> **validate** -> persist provenance

Validation is not a post-hoc check that can be skipped. An answer object cannot be
constructed with an unresolvable citation, and the only path to one runs through here.
When validation fails, the system retries once with the specific failure fed back, and
then degrades to showing the evidence without prose. It never shows an ungrounded
claim.

**Streaming.** ``stream`` is the one implementation; ``execute`` consumes it to the end.
Each claim is validated the moment the model finishes it and only then released, so a
reader sees cited claims appear one by one. The decision rule is unchanged: an attempt
is accepted only if *every* claim validates. If one fails mid-stream, the claims
already shown are withdrawn (``Withdrawn``) and the corrected attempt streams afresh, so
a finished answer never contains a claim from an attempt that had an unsupported one.
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator
from dataclasses import dataclass

from insightlm.documents.domain.permissions import DocumentPermission
from insightlm.retrieval.domain.answer import (
    AnswerOutcome,
    Claim,
    GroundedAnswer,
    ProposedClaim,
    validate,
)
from insightlm.retrieval.domain.evidence import (
    EvidenceBundle,
    EvidenceQuery,
    RetrievalStrategy,
)
from insightlm.retrieval.ports.evidence import EvidencePort
from insightlm.retrieval.ports.generation import (
    ClaimProposed,
    GenerationEvent,
    GenerationFinished,
    GenerationResult,
    GroundedGenerator,
)
from insightlm.shared.domain.errors import ValidationError
from insightlm.shared.domain.ids import DocumentId
from insightlm.shared.domain.policy import Actor, require
from insightlm.shared.observability.logging import get_logger

_logger = get_logger(__name__)

MAX_VALIDATION_ATTEMPTS = 2
"""One generation, then one correction. A model that cannot cite correctly when told
exactly which ids it invented will not get there on a third try, and each attempt
costs a full request."""


@dataclass(frozen=True, slots=True)
class AnswerRequest:
    question: str
    top_k: int = 8
    document_ids: tuple[DocumentId, ...] | None = None
    strategy: RetrievalStrategy = RetrievalStrategy.DENSE


@dataclass(frozen=True, slots=True)
class AnswerOutput:
    answer: GroundedAnswer
    latency_ms: int
    validation_attempts: int
    embedding_model: str


@dataclass(frozen=True, slots=True)
class EvidenceFound:
    """Retrieval is done: the passages the answer may cite, before any is written."""

    bundle: EvidenceBundle


@dataclass(frozen=True, slots=True)
class ClaimReady:
    """A claim the model finished and the validator accepted. Safe to show."""

    claim: Claim


@dataclass(frozen=True, slots=True)
class Withdrawn:
    """Discard the claims shown so far: the attempt they came from failed validation
    and a corrected one follows."""

    reason: str


@dataclass(frozen=True, slots=True)
class Answered:
    """The final answer. Always the last event."""

    output: AnswerOutput


AnswerEvent = EvidenceFound | ClaimReady | Withdrawn | Answered


async def as_stream(
    generator: GroundedGenerator, question: str, evidence: EvidenceBundle, *, feedback: str | None
) -> AsyncIterator[GenerationEvent]:
    """Stream from any generator: natively if it can, all at once if it can't."""
    native = getattr(generator, "stream", None)
    if native is not None:
        async for event in native(question, evidence, feedback=feedback):
            yield event
        return
    result = await generator.generate(question, evidence, feedback=feedback)
    if not result.refused:
        for claim in result.claims:
            yield ClaimProposed(claim)
    yield GenerationFinished(result)


class AnswerQuestion:
    def __init__(
        self,
        evidence: EvidencePort,
        generator: GroundedGenerator,
        *,
        embedding_model: str,
    ) -> None:
        self._evidence = evidence
        self._generator = generator
        self._embedding_model = embedding_model

    async def execute(self, actor: Actor, request: AnswerRequest) -> AnswerOutput:
        async for event in self.stream(actor, request):
            if isinstance(event, Answered):
                return event.output
        raise RuntimeError("the answer stream ended without an answer")  # pragma: no cover

    async def stream(self, actor: Actor, request: AnswerRequest) -> AsyncIterator[AnswerEvent]:
        require(actor, DocumentPermission.READ)

        if not request.question.strip():
            raise ValidationError("ask a question")

        started = time.perf_counter()

        bundle = await self._evidence.find(
            EvidenceQuery(
                text=request.question,
                top_k=request.top_k,
                document_ids=request.document_ids,
                strategy=request.strategy,
            )
        )
        yield EvidenceFound(bundle)

        feedback: str | None = None
        attempts = 0
        last_result: GenerationResult | None = None

        while attempts < MAX_VALIDATION_ATTEMPTS:
            attempts += 1
            proposed: list[ProposedClaim] = []
            shown = 0
            clean = True  # no claim in this attempt has failed validation yet
            result: GenerationResult | None = None

            async for event in as_stream(
                self._generator, request.question, bundle, feedback=feedback
            ):
                if isinstance(event, GenerationFinished):
                    result = event.result
                    continue
                proposed.append(event.claim)
                if not clean:
                    continue  # the attempt is already lost; let it finish for the usage
                accepted, report = validate([event.claim], bundle)
                if report.is_valid and accepted:
                    shown += 1
                    yield ClaimReady(accepted[0])
                elif event.claim.text.strip():
                    clean = False

            if result is None:  # pragma: no cover - a generator that never finished
                raise RuntimeError("the generator stream ended without a result")
            last_result = result

            if result.refused:
                if shown:
                    yield Withdrawn("the model decided the sources do not answer this")
                yield Answered(
                    self._finish(
                        GroundedAnswer(
                            outcome=AnswerOutcome.INSUFFICIENT_EVIDENCE,
                            claims=(),
                            evidence=bundle,
                            model_version=result.model_version,
                            prompt_version=result.prompt_version,
                            refusal_reason=result.refusal_reason,
                            input_tokens=result.input_tokens,
                            output_tokens=result.output_tokens,
                            cached_input_tokens=result.cached_input_tokens,
                        ),
                        started,
                        attempts,
                    )
                )
                return

            # The decision is made over the whole attempt, exactly as before streaming:
            # accepted only if every claim resolves. Use the finished result's claims
            # when the adapter parsed them, so a claim the stream missed still counts.
            claims, report = validate(result.claims or proposed, bundle)

            if report.is_valid and claims:
                yield Answered(
                    self._finish(
                        GroundedAnswer(
                            outcome=AnswerOutcome.ANSWERED,
                            claims=claims,
                            evidence=bundle,
                            model_version=result.model_version,
                            prompt_version=result.prompt_version,
                            input_tokens=result.input_tokens,
                            output_tokens=result.output_tokens,
                            cached_input_tokens=result.cached_input_tokens,
                        ),
                        started,
                        attempts,
                    )
                )
                return

            _logger.warning(
                "citation_validation_failed",
                attempt=attempts,
                unknown_labels=list(report.unknown_labels),
                uncited_claims=len(report.uncited_claims),
            )
            if shown:
                yield Withdrawn(
                    "a claim could not be tied to the sources, so the answer is being rewritten"
                )
            feedback = report.as_feedback()

        # Both attempts failed. Rather than show prose we cannot substantiate, the
        # answer degrades to the retrieved evidence -- the user still sees the source
        # passages and can read them directly.
        yield Answered(
            self._finish(
                GroundedAnswer(
                    outcome=AnswerOutcome.VALIDATION_FAILED,
                    claims=(),
                    evidence=bundle,
                    model_version=last_result.model_version if last_result else "unknown",
                    prompt_version=last_result.prompt_version if last_result else "unknown",
                    refusal_reason=(
                        "The generated answer could not be tied to the retrieved sources, "
                        "so it was withheld. The sources are shown below."
                    ),
                    input_tokens=last_result.input_tokens if last_result else 0,
                    output_tokens=last_result.output_tokens if last_result else 0,
                    cached_input_tokens=last_result.cached_input_tokens if last_result else 0,
                ),
                started,
                attempts,
            )
        )

    def _finish(self, answer: GroundedAnswer, started: float, attempts: int) -> AnswerOutput:
        latency_ms = int((time.perf_counter() - started) * 1000)
        _logger.info(
            "question_answered",
            outcome=answer.outcome.value,
            claims=len(answer.claims),
            citations=len(answer.citations),
            latency_ms=latency_ms,
            validation_attempts=attempts,
            input_tokens=answer.input_tokens,
            output_tokens=answer.output_tokens,
        )
        return AnswerOutput(
            answer=answer,
            latency_ms=latency_ms,
            validation_attempts=attempts,
            embedding_model=self._embedding_model,
        )
