"""Every evaluation CLI runs, offline, end to end.

These harnesses produced the Findings in docs/07; a refactor that quietly breaks one
would leave those numbers unreproducible. Run in a temporary directory, so their caches
and outputs never touch the repository's.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from insightlm.cli import (
    evaluate_fairness,
    evaluate_injection,
    evaluate_judge,
    evaluate_talent,
    samples,
)


@pytest.fixture(autouse=True)
def _in_tmp(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)


def test_judge_benchmark_runs_offline(capsys: pytest.CaptureFixture[str]) -> None:
    assert evaluate_judge.main(["--set", "all"]) == 0
    out = capsys.readouterr().out
    assert "judge-all: 37 cases" in out and "offline (overlap)" in out


def test_injection_harness_runs_offline_with_assessment(capsys: pytest.CaptureFixture[str]) -> None:
    assert evaluate_injection.main(["--assess"]) == 0
    out = capsys.readouterr().out
    assert "defended:    0/18" in out
    assert "mean lift over the clean CV: undefended +30.8, defended +0.0" in out


def test_fairness_harness_runs_offline(capsys: pytest.CaptureFixture[str]) -> None:
    assert evaluate_fairness.main(["--bases", "weak"]) == 0
    assert "judge offline (overlap), redaction blind-v1" in capsys.readouterr().out


def test_talent_harness_runs_offline(capsys: pytest.CaptureFixture[str]) -> None:
    assert evaluate_talent.main([]) == 0
    assert "<- best" in capsys.readouterr().out


def test_samples_are_written(tmp_path: Path) -> None:
    assert samples.main(["--out", str(tmp_path / "s"), "--cohort", "3"]) == 0
    assert len(list((tmp_path / "s").glob("*.pdf"))) == 9
    assert len(list((tmp_path / "s" / "cohort").glob("*.pdf"))) == 3
