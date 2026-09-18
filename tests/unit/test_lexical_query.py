"""Building the lexical query.

The first version used AND semantics and retrieved nothing for any conversational
question on a 40-document corpus -- which made hybrid fusion add exactly zero over
dense retrieval, a null result that looked like a finding about fusion and was
really a bug in one arm. After the fix the same arm scored nDCG 0.965.
"""

from __future__ import annotations

import re

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from insightlm.retrieval.adapters.search import or_query


def test_a_conversational_question_becomes_an_or_of_its_distinctive_terms() -> None:
    expression = or_query(
        "For Kavya Pillai, what peak throughput was sustained and by how much was p99 latency cut?"
    )
    assert expression is not None
    terms = expression.split(" | ")
    assert {"kavya", "pillai", "throughput", "p99", "latency"} <= set(terms)
    # The question scaffolding is gone; it would match every document equally.
    assert "what" not in terms and "was" not in terms


def test_or_semantics_not_and() -> None:
    """The whole fix. AND required every term in one passage and matched nothing."""
    expression = or_query("reconciliation settlement ledger")
    assert expression == "reconciliation | settlement | ledger"


def test_a_hyphenated_term_splits_into_matchable_parts() -> None:
    """`PCI-DSS` is exactly the exact-term case the lexical arm exists for."""
    expression = or_query("Who holds PCI-DSS?")
    assert expression is not None
    assert {"pci", "dss"} <= set(expression.split(" | "))


def test_a_question_of_only_stopwords_yields_no_query() -> None:
    """None rather than an empty tsquery, which Postgres would reject."""
    assert or_query("What is it?") is None
    assert or_query("") is None


def test_terms_are_deduplicated() -> None:
    assert or_query("latency latency LATENCY") == "latency"


@pytest.mark.parametrize(
    "hostile",
    [
        "'); DROP TABLE chunks; --",
        "a & b | !c <-> d",
        "foo:* & bar",
        "(unbalanced",
        "\\x00 null byte",
    ],
)
def test_user_input_cannot_reach_tsquery_syntax(hostile: str) -> None:
    """The string is assembled here and then bound to `to_tsquery`.

    Binding stops SQL injection but not *tsquery* injection: `&`, `|`, `!`, `<->`,
    `:*` and parentheses are operators inside the query language itself, and a
    malformed expression is a 500. Reducing every term to lowercase alphanumerics
    makes both impossible.
    """
    expression = or_query(hostile)
    if expression is not None:
        assert re.fullmatch(r"[a-z0-9]+( \| [a-z0-9]+)*", expression)


@given(st.text(max_size=200))
@settings(max_examples=300, deadline=None)
def test_any_input_yields_a_well_formed_expression_or_none(text: str) -> None:
    expression = or_query(text)
    if expression is not None:
        assert re.fullmatch(r"[a-z0-9]+( \| [a-z0-9]+)*", expression)
