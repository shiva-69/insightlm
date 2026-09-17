"""The hash chain: the property the whole governance story rests on."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from insightlm.governance.domain.audit import (
    GENESIS_HASH,
    AuditAction,
    AuditEntry,
    compute_entry_hash,
    verify_chain,
)
from insightlm.shared.domain.errors import InvariantViolationError
from insightlm.shared.domain.ids import (
    TenantId,
    new_audit_entry_id,
    new_tenant_id,
    new_user_id,
)

BASE_TIME = datetime(2026, 9, 18, 12, 0, tzinfo=UTC)


def build_chain(tenant_id: TenantId, length: int) -> list[AuditEntry]:
    entries: list[AuditEntry] = []
    prev = GENESIS_HASH
    actor = new_user_id()
    for i in range(length):
        entry = AuditEntry.link(
            entry_id=new_audit_entry_id(),
            tenant_id=tenant_id,
            actor_id=actor,
            action=AuditAction.USER_AUTHENTICATED,
            subject_type="user",
            subject_id=str(actor),
            payload={"index": i},
            occurred_at=BASE_TIME + timedelta(seconds=i),
            prev_hash=prev,
        )
        entries.append(entry)
        prev = entry.entry_hash
    return entries


def test_a_freshly_built_chain_verifies() -> None:
    tenant = new_tenant_id()
    assert verify_chain(tenant, build_chain(tenant, 10)).is_intact


def test_empty_chain_is_intact() -> None:
    tenant = new_tenant_id()
    result = verify_chain(tenant, [])
    assert result.is_intact
    assert result.entries_checked == 0


def test_mutating_a_payload_is_detected() -> None:
    """The headline claim: you cannot quietly rewrite what the log says happened."""
    tenant = new_tenant_id()
    entries = build_chain(tenant, 5)
    entries[2] = replace(entries[2], payload={"index": 2, "tampered": True})

    result = verify_chain(tenant, entries)
    assert not result.is_intact
    assert result.broken_at == entries[2].id
    assert "mutated" in (result.reason or "")


def test_deleting_an_entry_is_detected() -> None:
    tenant = new_tenant_id()
    entries = build_chain(tenant, 5)
    del entries[2]

    result = verify_chain(tenant, entries)
    assert not result.is_intact
    assert "deleted, reordered or re-pointed" in (result.reason or "")


def test_reordering_entries_is_detected() -> None:
    tenant = new_tenant_id()
    entries = build_chain(tenant, 5)
    entries[1], entries[3] = entries[3], entries[1]

    assert not verify_chain(tenant, entries).is_intact


def test_appending_a_forged_entry_is_detected() -> None:
    """An attacker who appends without knowing the head cannot produce a valid link."""
    tenant = new_tenant_id()
    entries = build_chain(tenant, 3)
    forged = AuditEntry.link(
        entry_id=new_audit_entry_id(),
        tenant_id=tenant,
        actor_id=new_user_id(),
        action=AuditAction.MEMBERSHIP_ROLE_CHANGED,
        subject_type="membership",
        subject_id="forged",
        payload={"to": "owner"},
        occurred_at=BASE_TIME + timedelta(hours=1),
        prev_hash=GENESIS_HASH,  # not the real head
    )
    entries.append(forged)

    result = verify_chain(tenant, entries)
    assert not result.is_intact
    assert result.broken_at == forged.id


def test_hash_is_deterministic_regardless_of_payload_key_order() -> None:
    """Canonical serialisation -- otherwise verification is a coin flip."""
    common = {
        "tenant_id": new_tenant_id(),
        "actor_id": None,
        "action": AuditAction.TENANT_CREATED,
        "subject_type": "tenant",
        "subject_id": "t1",
        "occurred_at": BASE_TIME,
        "prev_hash": GENESIS_HASH,
    }
    first = compute_entry_hash(payload={"a": 1, "b": {"x": 1, "y": 2}}, **common)
    second = compute_entry_hash(payload={"b": {"y": 2, "x": 1}, "a": 1}, **common)
    assert first == second


def test_naive_datetime_is_rejected() -> None:
    with pytest.raises(InvariantViolationError):
        AuditEntry(
            id=new_audit_entry_id(),
            tenant_id=new_tenant_id(),
            actor_id=None,
            action=AuditAction.TENANT_CREATED,
            subject_type="tenant",
            subject_id=None,
            payload={},
            occurred_at=datetime(2026, 9, 18, 12, 0),  # noqa: DTZ001 - the point of the test
            prev_hash=GENESIS_HASH,
            entry_hash="0" * 64,
        )


@given(length=st.integers(min_value=1, max_value=40), index=st.integers(min_value=0))
@settings(max_examples=50, deadline=None)
def test_property_any_single_mutation_breaks_the_chain(length: int, index: int) -> None:
    """Whatever the chain's length, tampering anywhere in it is detected."""
    tenant = new_tenant_id()
    entries = build_chain(tenant, length)
    target = index % length

    entries[target] = replace(entries[target], payload={"tampered": True})

    result = verify_chain(tenant, entries)
    assert not result.is_intact
    assert result.entries_checked == target
