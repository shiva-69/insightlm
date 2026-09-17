"""Prompts as versioned, content-hashed assets.

A prompt is an input to every answer the system produces, so it is versioned like
code and referenced by id, not pasted inline. Editing a prompt produces a new hash;
it never mutates history. That is what makes "reproduce this answer" and "re-run this
assessment under the new prompt and diff it" real operations rather than aspirations
-- and in Phase 6 it is what an auditor needs to see.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from insightlm.shared.domain.errors import InvariantViolationError

_PROMPT_DIR = Path(__file__).parent / "prompts"


@dataclass(frozen=True, slots=True)
class PromptVersion:
    name: str
    template: str
    content_hash: str

    @property
    def version_id(self) -> str:
        """What gets stamped on every answer: name plus the first 12 hex of the hash."""
        return f"{self.name}@{self.content_hash[:12]}"

    def render(self, **values: str) -> str:
        return self.template.format(**values)


@lru_cache(maxsize=32)
def load_prompt(name: str) -> PromptVersion:
    path = _PROMPT_DIR / f"{name}.md"
    if not path.is_file():
        raise InvariantViolationError(
            "prompt asset is missing from the build",
            detail={"name": name, "expected_at": str(path)},
        )
    template = path.read_text(encoding="utf-8")
    return PromptVersion(
        name=name,
        template=template,
        content_hash=hashlib.sha256(template.encode("utf-8")).hexdigest(),
    )
