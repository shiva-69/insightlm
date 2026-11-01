"""Grounded generation.

The port is deliberately domain-shaped: it takes a question and evidence, and returns
*proposed claims with citation labels*. It never exposes messages, roles, temperature
or tool schemas, so swapping provider -- or dropping to a local model when the circuit
opens -- changes one adapter and nothing else.

It returns **proposed** claims, not an answer. Validation happens in the domain
(`retrieval/domain/answer.py`), where it can be tested without a network call and
cannot be skipped by a careless adapter.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Protocol

from insightlm.retrieval.domain.answer import ProposedClaim
from insightlm.retrieval.domain.evidence import EvidenceBundle


@dataclass(frozen=True, slots=True)
class GenerationResult:
    claims: list[ProposedClaim] = field(default_factory=list)
    refused: bool = False
    refusal_reason: str | None = None
    model_version: str = "unknown"
    prompt_version: str = "unknown"
    input_tokens: int = 0
    output_tokens: int = 0
    cached_input_tokens: int = 0

    @property
    def cost_usd(self) -> float:
        """Attributed per request, so the Phase 3 cost/quality curve is measured
        rather than estimated. Rates are per million tokens; cache reads are cheaper
        and are counted separately."""
        return (
            self.input_tokens * _INPUT_RATE
            + self.cached_input_tokens * _CACHE_READ_RATE
            + self.output_tokens * _OUTPUT_RATE
        ) / 1_000_000


# Claude Opus 5 list pricing, USD per million tokens.
_INPUT_RATE = 5.00
_OUTPUT_RATE = 25.00
_CACHE_READ_RATE = 0.50


class GroundedGenerator(Protocol):
    async def generate(
        self,
        question: str,
        evidence: EvidenceBundle,
        *,
        feedback: str | None = None,
    ) -> GenerationResult:
        """Answer strictly from the supplied evidence.

        ``feedback`` carries a validation failure back on the single retry: telling
        the model precisely which citation ids it invented is far more effective than
        asking it to try again.

        Refusing is a valid outcome. A generator that always produces prose is a
        generator that hallucinates when the evidence runs out.
        """
        ...


# -- streaming -----------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ClaimProposed:
    """One claim, complete, as soon as the model has finished writing it."""

    claim: ProposedClaim


@dataclass(frozen=True, slots=True)
class GenerationFinished:
    """The whole result, with every claim and the usage. Always the last event."""

    result: GenerationResult


GenerationEvent = ClaimProposed | GenerationFinished


class StreamingGenerator(Protocol):
    """A generator that can hand over claims one at a time while it writes.

    Optional. A generator without ``stream`` is wrapped by the answering use case,
    which emits its claims once ``generate`` returns -- same events, no early display.
    """

    def stream(
        self, question: str, evidence: EvidenceBundle, *, feedback: str | None = None
    ) -> AsyncIterator[GenerationEvent]: ...
