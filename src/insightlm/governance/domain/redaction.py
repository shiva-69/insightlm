"""Read-time redaction: what a model is not allowed to know about a person.

Applied when evidence is handed to a model, never to what is stored. The document, the
reviewer's view and the citations all keep the original text. Only the model's copy is
redacted, and it keeps the same passage labels, so a verdict still cites real,
unredacted evidence. ADR-0013 has the reasoning for read-time over write-time.

Governance owns the *mechanism* -- a profile is an ordered list of rules -- and the
rules that apply to any person in any document:

- **The person's name.** Known exactly from the record, so no guessing: every token of
  it is replaced. Names carry gender and ethnicity signals.
- **Contact details.** Emails, phone numbers and profile links identify the person.
- **Gendered pronouns** become "they".

A vertical adds the rules that only make sense for what it judges, and assembles its own
profiles. Recruiting adds institutions and demographic lines and builds ``blind-v1``
from them (``recruiting.domain.screening``); a contract vertical would redact
counterparty names instead. Order is part of a profile: rules run in the order listed.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Protocol


@dataclass(frozen=True, slots=True)
class RedactionContext:
    """What a rule may know about the person being redacted."""

    person_names: Sequence[str] = ()


class RedactionRule(Protocol):
    """One kind of thing to remove. Returns the new text and how many it removed."""

    @property
    def kind(self) -> str: ...

    def apply(self, text: str, context: RedactionContext) -> tuple[str, int]: ...


@dataclass(frozen=True, slots=True)
class RedactionProfile:
    id: str
    rules: tuple[RedactionRule, ...] = ()

    @property
    def is_identity(self) -> bool:
        return not self.rules


NO_REDACTION = RedactionProfile("none")


@dataclass(frozen=True, slots=True)
class Redacted:
    text: str
    removed: Counter[str] = field(default_factory=Counter)


def redact(text: str, profile: RedactionProfile, *, person_names: Sequence[str] = ()) -> Redacted:
    removed: Counter[str] = Counter()
    context = RedactionContext(person_names=tuple(person_names))
    for rule in profile.rules:
        text, count = rule.apply(text, context)
        removed[rule.kind] += count
    return Redacted(text=text, removed=+removed)


# -- general rules -------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PatternRule:
    """Replace every match of a pattern. The building block for vertical-specific rules."""

    kind: str
    pattern: re.Pattern[str]
    replacement: str

    def apply(self, text: str, context: RedactionContext) -> tuple[str, int]:
        return self.pattern.subn(self.replacement, text)


_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_URL = re.compile(r"(?:https?://|www\.)\S+|\b(?:linkedin|github)\.com/\S+", re.IGNORECASE)
_PHONE = re.compile(r"(?<![\w(])\+?\(?\d[\d\s().-]{7,}\d(?!\w)")


@dataclass(frozen=True, slots=True)
class ContactRule:
    kind: str = "contact"

    def apply(self, text: str, context: RedactionContext) -> tuple[str, int]:
        text, emails = _EMAIL.subn("[email]", text)
        text, links = _URL.subn("[link]", text)
        phones = 0

        # A phone number has at least 9 digits; "2019-2025" has 8 and is experience.
        def phone(match: re.Match[str]) -> str:
            nonlocal phones
            if sum(c.isdigit() for c in match.group(0)) < 9:
                return match.group(0)
            phones += 1
            return "[phone]"

        text = _PHONE.sub(phone, text)
        return text, emails + links + phones


@dataclass(frozen=True, slots=True)
class PersonNameRule:
    """Every token of the person's known names. The placeholder names the role the
    person plays -- ``[candidate]`` in hiring -- so the model still reads fluent text."""

    replacement: str = "[person]"
    kind: str = "name"

    def apply(self, text: str, context: RedactionContext) -> tuple[str, int]:
        tokens = {
            t for name in context.person_names for t in re.findall(r"[\w'-]+", name) if len(t) >= 2
        }
        if not tokens:
            return text, 0
        alternatives = "|".join(re.escape(t) for t in sorted(tokens, key=len, reverse=True))
        return re.compile(rf"\b(?:{alternatives})\b", re.IGNORECASE).subn(self.replacement, text)


_PRONOUNS = {
    "he": "they",
    "she": "they",
    "him": "them",
    "his": "their",
    "hers": "theirs",
    "himself": "themself",
    "herself": "themself",
}
_PRONOUN = re.compile(r"\b(" + "|".join(_PRONOUNS) + r")\b", re.IGNORECASE)
# "her" is object or possessive; "their" reads acceptably for both.
_HER = re.compile(r"\bher\b", re.IGNORECASE)


def _keep_case(word: str, replacement: str) -> str:
    return replacement.capitalize() if word[:1].isupper() else replacement


@dataclass(frozen=True, slots=True)
class PronounRule:
    kind: str = "pronoun"

    def apply(self, text: str, context: RedactionContext) -> tuple[str, int]:
        text, a = _PRONOUN.subn(
            lambda m: _keep_case(m.group(0), _PRONOUNS[m.group(0).lower()]), text
        )
        text, b = _HER.subn(lambda m: _keep_case(m.group(0), "their"), text)
        return text, a + b
