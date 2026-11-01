"""How contract review gets evidence: the engine's EvidencePort, opened per actor.

The same seam recruiting uses, declared again here because the two verticals may not
depend on each other. Nothing about chunks, vectors or search crosses it.
"""

from __future__ import annotations

from contextlib import AbstractAsyncContextManager
from typing import Protocol

from insightlm.retrieval.ports.evidence import EvidencePort
from insightlm.shared.domain.policy import Actor


class EvidenceProvider(Protocol):
    def for_actor(self, actor: Actor) -> AbstractAsyncContextManager[EvidencePort]: ...
