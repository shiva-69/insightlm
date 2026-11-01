"""Claims are released from a streaming JSON answer whole, in order, and only when complete."""

from __future__ import annotations

import json

from insightlm.retrieval.adapters.claim_stream import ClaimStreamParser

ANSWER = json.dumps(
    {
        "sufficient_evidence": True,
        "claims": [
            {"text": "Asha ran Kubernetes {in production}.", "citations": ["C1"]},
            {"text": 'She wrote "idempotent" retries \\ backoff.', "citations": ["C2", "C3"]},
        ],
        "missing_information": "",
    }
)


def test_claims_arrive_one_at_a_time_however_the_text_is_split() -> None:
    for size in (1, 2, 7, 64, len(ANSWER)):
        parser = ClaimStreamParser()
        seen = []
        for i in range(0, len(ANSWER), size):
            seen.extend(parser.feed(ANSWER[i : i + size]))
        assert [c.text for c in seen] == [
            "Asha ran Kubernetes {in production}.",
            'She wrote "idempotent" retries \\ backoff.',
        ]
        assert [c.citation_labels for c in seen] == [("C1",), ("C2", "C3")]
        assert json.loads(parser.text) == json.loads(ANSWER)


def test_an_unfinished_claim_is_never_released() -> None:
    parser = ClaimStreamParser()
    assert parser.feed('{"claims": [{"text": "half a cla') == []
    assert parser.feed('im", "citations": ["C1"]') == []
    [claim] = parser.feed("}")
    assert claim.text == "half a claim"


def test_braces_and_quotes_inside_text_do_not_end_a_claim() -> None:
    parser = ClaimStreamParser()
    claims = parser.feed('{"claims": [{"text": "a } b \\" { c", "citations": []}]}')
    assert [c.text for c in claims] == ['a } b " { c']


def test_a_refusal_with_no_claims_yields_nothing() -> None:
    parser = ClaimStreamParser()
    assert (
        parser.feed('{"sufficient_evidence": false, "claims": [], "missing_information": "x"}')
        == []
    )


def test_claims_written_before_other_keys_still_stream() -> None:
    parser = ClaimStreamParser()
    text = '{"claims": [{"text": "one", "citations": ["C1"]}], "sufficient_evidence": true}'
    assert [c.text for c in parser.feed(text)] == ["one"]
