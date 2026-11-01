"""Claims out of a JSON answer that is still being written.

A schema-constrained model streams its answer as JSON text, a few tokens at a time:
``{"sufficient_evidence": true, "claims": [{"text": "...", "citations": ["C1"]}, {...``.
Waiting for the closing brace would throw away the point of streaming, so this reads the
text as it arrives and hands over each claim object the moment its own closing brace
lands. Partial objects are never guessed at: a claim is released complete or not at all,
so validation always sees exactly what the model wrote.

It only needs to understand enough JSON to find object boundaries inside the ``claims``
array: string literals (with escapes, so a ``}`` inside a claim's text is not mistaken
for the end of the object) and brace depth. The finished text is parsed properly with
``json.loads`` by the caller once the stream ends.
"""

from __future__ import annotations

import json
import re

from insightlm.retrieval.domain.answer import ProposedClaim

_CLAIMS_KEY = re.compile(r'"claims"\s*:\s*\[')


class ClaimStreamParser:
    def __init__(self) -> None:
        self._text = ""
        self._cursor: int | None = None  # where scanning resumes inside the array
        self._done = False

    @property
    def text(self) -> str:
        """Everything received so far."""
        return self._text

    def feed(self, chunk: str) -> list[ProposedClaim]:
        """Add text; return the claims completed by it, in order."""
        self._text += chunk
        if self._done:
            return []
        if self._cursor is None:
            match = _CLAIMS_KEY.search(self._text)
            if match is None:
                return []
            self._cursor = match.end()
        return self._scan(self._cursor)

    def _scan(self, start: int) -> list[ProposedClaim]:
        found: list[ProposedClaim] = []
        text = self._text
        i = start
        while i < len(text):
            c = text[i]
            if c in " \t\r\n,":
                i += 1
                continue
            if c == "]":
                self._done = True
                i += 1
                break
            if c != "{":
                # Not an object where one should be: leave it for json.loads to judge.
                self._done = True
                break
            end = _object_end(text, i)
            if end is None:
                break  # incomplete: wait for more text
            claim = _claim(text[i:end])
            if claim is not None:
                found.append(claim)
            i = end
        self._cursor = i
        return found


def _object_end(text: str, start: int) -> int | None:
    """Index just past the object starting at ``start``, or None if it is unfinished."""
    depth = 0
    in_string = False
    escaped = False
    for j in range(start, len(text)):
        c = text[j]
        if in_string:
            if escaped:
                escaped = False
            elif c == "\\":
                escaped = True
            elif c == '"':
                in_string = False
            continue
        if c == '"':
            in_string = True
        elif c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return j + 1
    return None


def _claim(raw: str) -> ProposedClaim | None:
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(value, dict):
        return None
    citations = value.get("citations") or []
    return ProposedClaim(
        text=str(value.get("text", "")),
        citation_labels=tuple(str(label) for label in citations if isinstance(label, str)),
    )
