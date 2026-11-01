"""Identifiers for contract review, declared here rather than in the shared kernel.

The kernel's ``ids`` module still carries recruiting's identifiers from before verticals
declared their own vocabulary. A new vertical does not add to it.
"""

from __future__ import annotations

from typing import NewType
from uuid import UUID, uuid4

PlaybookId = NewType("PlaybookId", UUID)
PositionId = NewType("PositionId", UUID)
ContractReviewId = NewType("ContractReviewId", UUID)


def new_playbook_id() -> PlaybookId:
    return PlaybookId(uuid4())


def new_position_id() -> PositionId:
    return PositionId(uuid4())


def new_contract_review_id() -> ContractReviewId:
    return ContractReviewId(uuid4())
