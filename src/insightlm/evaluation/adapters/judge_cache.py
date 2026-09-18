"""A criterion judge that remembers its answers, for evaluation runs.

Keyed by model, prompt version, criterion and the exact passages judged, so a cached
verdict is only ever reused for an identical question. Harness-only: production never
caches a judgment, because an assessment must be a fresh reading of the evidence.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from insightlm.recruiting.domain.assessment import Verdict
from insightlm.recruiting.domain.rubric import Criterion
from insightlm.recruiting.ports.judge import CriterionJudge, JudgeResult
from insightlm.retrieval.domain.evidence import EvidenceBundle

DEFAULT_PATH = Path("eval/.cache/judgments.json")


class JudgmentStore:
    def __init__(self, path: Path = DEFAULT_PATH, *, enabled: bool = True) -> None:
        self._path = path
        self._enabled = enabled
        self._entries: dict[str, dict[str, Any]] = (
            json.loads(path.read_text()) if enabled and path.exists() else {}
        )

    def get(self, key: str) -> dict[str, Any] | None:
        return self._entries.get(key) if self._enabled else None

    def put(self, key: str, value: dict[str, Any]) -> None:
        if self._enabled:
            self._entries[key] = value

    def save(self) -> None:
        if self._enabled:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(json.dumps(self._entries, indent=1, sort_keys=True))


def judgment_key(
    model: str, prompt: str, criterion: Criterion, evidence: EvidenceBundle, salt: str = ""
) -> str:
    # Keyed on the rendered context, not the raw passages: a change to how sources are
    # rendered is a change to the question, and must not be answered from cache.
    raw = json.dumps(
        [model, prompt, criterion.label, criterion.evidence_hint, evidence.as_context()]
        + ([salt] if salt else [])  # unsalted keys stay identical to earlier runs'
    )
    return hashlib.sha256(raw.encode()).hexdigest()


class CachedJudge:
    """Satisfies `CriterionJudge`; counts the calls it actually paid for."""

    def __init__(
        self,
        inner: CriterionJudge,
        store: JudgmentStore,
        *,
        model: str,
        prompt: str,
        salt: str = "",
    ) -> None:
        self._inner = inner
        self._store = store
        self._model = model
        self._prompt = prompt
        # A salt makes an identical question a separate cache entry: how the harness
        # asks the same thing twice to measure a model's run-to-run noise.
        self.salt = salt
        self.calls = 0

    async def judge(self, criterion: Criterion, evidence: EvidenceBundle) -> JudgeResult:
        key = judgment_key(self._model, self._prompt, criterion, evidence, self.salt)
        if (hit := self._store.get(key)) is not None:
            return JudgeResult(
                verdict=Verdict(hit["verdict"]),
                confidence=float(hit.get("confidence", 0.8)),
                rationale=hit.get("rationale", ""),
                citation_labels=tuple(hit["cited"]),
                model_version=self._model,
                prompt_version=self._prompt,
            )
        result = await self._inner.judge(criterion, evidence)
        self.calls += 1
        self._store.put(
            key,
            {
                "verdict": result.verdict.value,
                "confidence": result.confidence,
                "cited": list(result.citation_labels),
                "rationale": result.rationale,
            },
        )
        return result
