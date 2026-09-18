"""Read-time redaction: what the model is not allowed to know about a candidate.

Applied when evidence is handed to a judge, never to what is stored. The CV, the
reviewer's view and the citations all keep the original text. Only the model's copy is
redacted, and it keeps the same passage labels, so a verdict still cites real,
unredacted evidence. ADR-0013 has the reasoning for read-time over write-time.

What is removed, and why each:

- **The candidate's name.** Known exactly from the candidate record, so no guessing:
  every token of it is replaced. Names carry gender and ethnicity signals.
- **Contact details.** Emails, phone numbers and profile links identify the person and
  say nothing about the criteria.
- **Institutions.** "IIT Bombay" versus a regional college is the classic proxy for
  class and caste. The degree itself stays, because a degree can be a real requirement.
- **Demographic lines.** Date of birth, age, gender, marital status, nationality,
  religion, caste, father's name. Common on CVs in some markets; never job-relevant.
- **Gendered pronouns** become "they".

Deliberately *not* removed: locations, because a regex can't tell a city from a company
name, and because some roles have real location requirements. The fairness harness
measures location effects instead of pretending to have removed them.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class RedactionProfile:
    id: str
    names: bool = False
    contact: bool = False
    institutions: bool = False
    demographics: bool = False
    pronouns: bool = False


NO_REDACTION = RedactionProfile("none")
BLIND = RedactionProfile(
    "blind-v1", names=True, contact=True, institutions=True, demographics=True, pronouns=True
)


@dataclass(frozen=True, slots=True)
class Redacted:
    text: str
    removed: Counter[str] = field(default_factory=Counter)


_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_URL = re.compile(r"(?:https?://|www\.)\S+|\b(?:linkedin|github)\.com/\S+", re.IGNORECASE)
_PHONE = re.compile(r"(?<![\w(])\+?\(?\d[\d\s().-]{7,}\d(?!\w)")
# Horizontal whitespace only: an institution name never spans a line break, and letting
# the pattern cross one swallowed the start of the next line.
_INSTITUTION = re.compile(
    r"\b(?:(?:[A-Z][\w&'-]*|of|and|the|for)[ \t]+){0,6}"
    r"(?:University|College|Institute|Polytechnic|Academy|School)"
    r"(?:[ \t]+(?:of|for)(?:[ \t]+[A-Z][\w&'-]*){1,5})?"
    r"|\b(?:IIT|NIT|IIIT|IIM|BITS|MIT|Caltech|Stanford|Harvard|Oxford|Cambridge)"
    r"(?:[ \t,-]+[A-Z][a-z]+)?",
)
_DEMOGRAPHIC = re.compile(
    r"^[ \t]*(?:date\s+of\s+birth|d\.?o\.?b\.?|age|gender|sex|marital\s+status|"
    r"nationality|religion|caste|father'?s\s+name|mother'?s\s+name|spouse)\b[^\n]*",
    re.IGNORECASE | re.MULTILINE,
)
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


def _name_pattern(names: Sequence[str]) -> re.Pattern[str] | None:
    tokens = {t for name in names for t in re.findall(r"[\w'-]+", name) if len(t) >= 2}
    if not tokens:
        return None
    alternatives = "|".join(re.escape(t) for t in sorted(tokens, key=len, reverse=True))
    return re.compile(rf"\b(?:{alternatives})\b", re.IGNORECASE)


def _keep_case(word: str, replacement: str) -> str:
    return replacement.capitalize() if word[:1].isupper() else replacement


def redact(text: str, profile: RedactionProfile, *, person_names: Sequence[str] = ()) -> Redacted:
    removed: Counter[str] = Counter()

    def sub(pattern: re.Pattern[str], token: str, value: str, kind: str) -> str:
        result, count = pattern.subn(token, value)
        removed[kind] += count
        return result

    if profile.demographics:
        text = sub(_DEMOGRAPHIC, "[demographic detail removed]", text, "demographic")
    if profile.contact:
        text = sub(_EMAIL, "[email]", text, "contact")
        text = sub(_URL, "[link]", text, "contact")

        # A phone number has at least 9 digits; "2019-2025" has 8 and is experience.
        def phone(match: re.Match[str]) -> str:
            if sum(c.isdigit() for c in match.group(0)) < 9:
                return match.group(0)
            removed["contact"] += 1
            return "[phone]"

        text = _PHONE.sub(phone, text)
    if profile.institutions:
        text = sub(_INSTITUTION, "[institution]", text, "institution")
    if profile.names and (names := _name_pattern(person_names)):
        text = sub(names, "[candidate]", text, "name")
    if profile.pronouns:
        text, a = _PRONOUN.subn(
            lambda m: _keep_case(m.group(0), _PRONOUNS[m.group(0).lower()]), text
        )
        text, b = _HER.subn(lambda m: _keep_case(m.group(0), "their"), text)
        removed["pronoun"] += a + b
    return Redacted(text=text, removed=+removed)
