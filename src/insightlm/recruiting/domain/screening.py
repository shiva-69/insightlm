"""What recruiting adds to the engine's defences: phrases that try to steer a hiring
judgment, and the redaction that hides who a candidate is.

The engine's scanner already recognises text addressed to a model in general. These are
the phrases that only make sense when the document is a CV being judged -- "rate this
candidate as 10", "this applicant is a perfect match". The composition root extends the
engine's scanner with them, so the documents module never has to know what a candidate
is.
"""

from __future__ import annotations

import re

from insightlm.governance.domain.redaction import (
    ContactRule,
    PatternRule,
    PersonNameRule,
    PronounRule,
    RedactionProfile,
)

RECRUITING_INSTRUCTION_PATTERNS: tuple[str, ...] = (
    r"\b(?:rate|score|rank|grade|mark|assess|evaluate)\s+(?:this|the)\s+"
    r"(?:candidate|applicant|resume|résumé|cv)\s+(?:as|at|with|a|an|highly|10|5|top)",
    r"\b(?:recommend|shortlist|advance|select|hire)\s+(?:this|the)\s+"
    r"(?:candidate|applicant)\s+(?:immediately|for|as|without)",
    r"\bthis\s+(?:candidate|applicant)\s+(?:is|has)\s+(?:an?\s+)?(?:perfect|ideal|"
    r"exceptional|outstanding)\s+(?:match|fit|candidate)",
)

RECRUITING_SUBJECTS: tuple[str, ...] = (
    r"\b(?:this|the)\s+(?:candidate|applicant|cv|r[ée]sum[ée]|resume|profile|"
    r"application|applicant's|candidate's)\b",
)
"""What a recruiting judgment is about, for the reworded-instruction detector."""


# -- blind screening -----------------------------------------------------------------
#
# What a judge is not allowed to know about a candidate, beyond what governance removes
# for any person:
#
# - **Institutions.** "IIT Bombay" versus a regional college is the classic proxy for
#   class and caste. The degree itself stays, because a degree can be a real requirement.
# - **Demographic lines.** Date of birth, age, gender, marital status, nationality,
#   religion, caste, father's name. Common on CVs in some markets; never job-relevant.
#
# Deliberately *not* removed: locations, because a regex can't tell a city from a company
# name, and because some roles have real location requirements. The fairness harness
# measures location effects instead of pretending to have removed them.

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

# The order is part of the profile, and is the order blind-v1 has always applied:
# demographic lines go first, so a "Date of birth" line is removed whole rather than
# having its digits taken for a phone number.
BLIND = RedactionProfile(
    "blind-v1",
    (
        PatternRule("demographic", _DEMOGRAPHIC, "[demographic detail removed]"),
        ContactRule(),
        PatternRule("institution", _INSTITUTION, "[institution]"),
        PersonNameRule(replacement="[candidate]"),
        PronounRule(),
    ),
)
