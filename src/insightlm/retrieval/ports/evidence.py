"""The port that carries the thesis.

`retrieval` offers exactly one thing to the rest of the system:

    EvidenceQuery -> EvidenceBundle

Nothing about embeddings, chunking, rank fusion, rerankers or vector SQL crosses this
line. When `recruiting` lands in Phase 4 it will depend on this Protocol and be
structurally incapable of knowing how retrieval works -- which is what makes
"recruiting is a vertical on the document engine" a property of the code rather than a
claim in a report. `import-linter`'s `module-internals-are-private` contract is what
keeps it that way.
"""

from __future__ import annotations

from typing import Protocol

from insightlm.retrieval.domain.evidence import EvidenceBundle, EvidenceQuery


class EvidencePort(Protocol):
    async def find(self, query: EvidenceQuery) -> EvidenceBundle:
        """Retrieve ranked evidence. Returns an empty bundle rather than raising when
        the corpus has nothing relevant -- "no evidence" is an answer."""
        ...
