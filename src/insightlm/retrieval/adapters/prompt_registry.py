"""The retrieval module's prompts. Loading and hashing live in the shared kernel."""

from __future__ import annotations

from pathlib import Path

from insightlm.shared.adapters.prompts import PromptVersion, load_prompt_from

_PROMPT_DIR = Path(__file__).parent / "prompts"


def load_prompt(name: str) -> PromptVersion:
    return load_prompt_from(_PROMPT_DIR, name)


__all__ = ["PromptVersion", "load_prompt"]
