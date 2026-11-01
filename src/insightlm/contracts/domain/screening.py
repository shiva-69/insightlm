"""Phrases that try to steer a contract review, added to the engine's scanner.

A contract is drafted by the other side. Text that tells a reviewer -- human or model --
that a clause is fine is exactly what a counterparty would hide in a schedule.
"""

from __future__ import annotations

CONTRACT_INSTRUCTION_PATTERNS: tuple[str, ...] = (
    r"\b(?:mark|treat|classify|record|report)\s+(?:this|these|the|all)\s+"
    r"(?:clauses?|contract|agreement|terms?|sections?)\s+as\s+(?:compliant|acceptable|"
    r"approved|standard|low[\s-]risk|meeting)",
    r"\b(?:approve|accept|sign\s+off)\s+(?:this|the)\s+(?:contract|agreement|clause)\s+"
    r"(?:immediately|without|as\s+is)",
    r"\bthis\s+(?:clause|contract|agreement)\s+(?:is|meets)\s+(?:fully\s+)?(?:compliant|"
    r"standard|acceptable|the\s+playbook)",
)

CONTRACT_SUBJECTS: tuple[str, ...] = (
    r"\bthis\s+(?:clause|contract|agreement|indemnity|limitation|provision|term|"
    r"schedule|annex|warranty|cap|carve[\s-]out|exclusion|obligation)\b",
)
"""What a contract judgment is about, for the reworded-instruction detector."""
