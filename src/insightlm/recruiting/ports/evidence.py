"""How recruiting gets evidence -- and the only thing it knows about retrieval.

`recruiting` depends on `retrieval.ports.EvidencePort` and on the evidence *types*,
and on nothing else in that module. It cannot see embeddings, chunking, rank fusion
or vector SQL, and `import-linter` fails the build if anyone tries. That is what makes
"recruiting is a vertical on the document engine" a property of the code rather than
a sentence in a report.

The provider exists because an `EvidencePort` implementation is session- and
tenant-scoped: it has to be opened for an actor and closed afterwards, and the
composition layer -- not this module -- knows how to do that.
"""

from __future__ import annotations

from contextlib import AbstractAsyncContextManager
from typing import Protocol

from insightlm.retrieval.ports.evidence import EvidencePort
from insightlm.shared.domain.policy import Actor


class EvidenceProvider(Protocol):
    def for_actor(self, actor: Actor) -> AbstractAsyncContextManager[EvidencePort]:
        """An evidence port scoped to the actor's tenant, valid inside the block.

        Not safe for concurrent use: it holds one database session. Callers fan out
        over the *judging*, which does no database I/O, never over retrieval.
        """
        ...
