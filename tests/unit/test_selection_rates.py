"""The four-fifths check, and the cases where it must refuse to give a number."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from insightlm.recruiting.domain.fairness import (
    UNDISCLOSED,
    Dimension,
    Outcome,
    SelfIdentification,
    selection_rates,
)
from insightlm.shared.domain.errors import ValidationError
from insightlm.shared.domain.ids import new_candidate_id, new_tenant_id


def outcomes(group: str, advanced: int, rejected: int) -> list[Outcome]:
    return [Outcome(group, True)] * advanced + [Outcome(group, False)] * rejected


def by_group(report: list) -> dict:  # type: ignore[type-arg]
    return {r.group: r for r in report}


def test_a_rate_below_four_fifths_of_the_best_is_flagged() -> None:
    report = by_group(selection_rates(outcomes("man", 6, 4) + outcomes("woman", 4, 6)))
    assert report["man"].rate == pytest.approx(0.6)
    assert report["woman"].impact_ratio == pytest.approx(0.4 / 0.6)
    assert report["woman"].adverse is True
    assert report["man"].adverse is False


def test_within_four_fifths_is_not_flagged() -> None:
    report = by_group(selection_rates(outcomes("man", 5, 5) + outcomes("woman", 4, 6)))
    assert report["woman"].impact_ratio == pytest.approx(0.8)
    assert report["woman"].adverse is False


def test_small_groups_get_no_ratio_rather_than_a_misleading_one() -> None:
    report = by_group(selection_rates(outcomes("man", 5, 5) + outcomes("non_binary", 0, 2)))
    assert report["non_binary"].too_few is True
    assert report["non_binary"].impact_ratio is None
    assert report["non_binary"].adverse is False


def test_undisclosed_is_reported_but_never_the_reference_or_flagged() -> None:
    report = by_group(selection_rates(outcomes("man", 3, 7) + outcomes(UNDISCLOSED, 9, 1)))
    assert report[UNDISCLOSED].impact_ratio is None
    assert report["man"].impact_ratio == pytest.approx(1.0)


def test_prefer_not_to_say_counts_as_undisclosed_and_values_are_a_closed_set() -> None:
    record = SelfIdentification(
        tenant_id=new_tenant_id(),
        candidate_id=new_candidate_id(),
        dimension=Dimension.GENDER,
        value="prefer_not_to_say",
        recorded_at=datetime(2026, 9, 19, tzinfo=UTC),
    )
    assert record.group == UNDISCLOSED
    with pytest.raises(ValidationError):
        SelfIdentification(
            tenant_id=record.tenant_id,
            candidate_id=record.candidate_id,
            dimension=Dimension.GENDER,
            value="inferred_from_name",
            recorded_at=record.recorded_at,
        )
