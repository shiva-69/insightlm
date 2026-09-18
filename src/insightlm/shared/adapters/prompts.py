"""Prompts as versioned, content-hashed assets.

A prompt is an input to every answer and every assessment, so it is versioned like
code and referenced by id rather than pasted inline. Editing a prompt changes its
hash and never mutates history -- which is what makes "reproduce this assessment" and
"re-run it under the new prompt and diff" real operations.

Shared because more than one module owns prompts: retrieval owns the grounded-answer
prompt, recruiting owns the criterion-judging one, and neither may import the other.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from insightlm.shared.domain.errors import InvariantViolationError


@dataclass(frozen=True, slots=True)
class PromptVersion:
    name: str
    template: str
    content_hash: str

    @property
    def version_id(self) -> str:
        """Stamped on every output: name plus the first 12 hex of the content hash."""
        return f"{self.name}@{self.content_hash[:12]}"

    def render(self, **values: str) -> str:
        return self.template.format(**values)


@lru_cache(maxsize=64)
def load_prompt_from(directory: Path, name: str) -> PromptVersion:
    path = directory / f"{name}.md"
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
