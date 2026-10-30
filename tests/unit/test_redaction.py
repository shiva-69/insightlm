"""Blind screening: what the judge must not see, and what must survive redaction."""

from __future__ import annotations

import pytest

from insightlm.governance.domain.redaction import NO_REDACTION, redact
from insightlm.recruiting.domain.screening import BLIND

CV = """Priya Venkatesh
priya.v@gmail.com | +91 98765 43210 | linkedin.com/in/priyav
Date of Birth: 12/03/1996
Marital Status: Single
Payments engineer, 2019-2025. Built settlement services in Python; she led the ledger migration.
B.Tech Computer Science, IIT Bombay, 2018. MSc from University of Oxford.
Studied at Government Engineering College Thrissur."""


def test_blind_removes_identity_and_proxies() -> None:
    text = redact(CV, BLIND, person_names=["Priya Venkatesh"]).text
    for leaked in (
        "Priya",
        "Venkatesh",
        "gmail",
        "98765",
        "linkedin",
        "1996",
        "Single",
        "IIT Bombay",
        "Oxford",
        "Engineering College",
        " she ",
    ):
        assert leaked not in text, leaked


def test_blind_keeps_what_the_criteria_are_about() -> None:
    text = redact(CV, BLIND, person_names=["Priya Venkatesh"]).text
    for kept in ("2019-2025", "Python", "settlement", "B.Tech Computer Science", "MSc", "they led"):
        assert kept in text, kept


def test_locations_are_deliberately_kept() -> None:
    # Documented limitation: measured by the fairness harness instead.
    assert "Thrissur" in redact(CV, BLIND, person_names=["Priya Venkatesh"]).text


@pytest.mark.parametrize(
    "text",
    [
        "Agency frontend developer.",
        "Implemented rate limiting and a school bus tracker.",
        "Offices (Delhi, Pune).",
        "Payments, 2019-2025.",
    ],
)
def test_ordinary_text_passes_through_untouched(text: str) -> None:
    assert redact(text, BLIND).text == text


def test_redaction_counts_what_it_removed() -> None:
    removed = redact(CV, BLIND, person_names=["Priya Venkatesh"]).removed
    assert removed["name"] == 2 and removed["contact"] == 3 and removed["demographic"] == 2


def test_no_redaction_is_the_identity() -> None:
    assert redact(CV, NO_REDACTION, person_names=["Priya Venkatesh"]).text == CV
