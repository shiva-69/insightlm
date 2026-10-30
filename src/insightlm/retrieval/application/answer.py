"""Answering a question, with the citation contract enforced.

The shape of this use case *is* the thesis:

    retrieve -> generate -> **validate** -> persist provenance

Validation is not a post-hoc check that can be skipped. An answer object cannot be
constructed with an unresolvable citation, and the only path to one runs through here.
When validation fails, the system retries once with the specific failure fed back, and
then degrades to showing the evidence without prose. It never shows an ungrounded
claim.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from insightlm.documents.domain.permissions import DocumentPermission
from insightlm.retrieval.domain.answer import (
    AnswerOutcome,
    GroundedAnswer,
    validate,
)
from insightlm.retrieval.domain.evidence import (
    EvidenceQuery,
    RetrievalStrategy,
)
from insightlm.retrieval.ports.evidence import EvidencePort
from insightlm.retrieval.ports.generation import GroundedGenerator
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

        feedback: str | None = None
        attempts = 0
        last_result = None

        while attempts < MAX_VALIDATION_ATTEMPTS:
            attempts += 1
            result = await self._generator.generate(request.question, bundle, feedback=feedback)
            last_result = result

            if result.refused:
                return self._finish(
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

            claims, report = validate(result.claims, bundle)

            if report.is_valid and claims:
                return self._finish(
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

            _logger.warning(
                "citation_validation_failed",
                attempt=attempts,
                unknown_labels=list(report.unknown_labels),
                uncited_claims=len(report.uncited_claims),
            )
            feedback = report.as_feedback()

        # Both attempts failed. Rather than show prose we cannot substantiate, the
        # answer degrades to the retrieved evidence -- the user still sees the source
        # passages and can read them directly.
        return self._finish(
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
