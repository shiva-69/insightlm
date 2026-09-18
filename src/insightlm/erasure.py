"""Erasing a candidate, and proving it.

A candidate's data is spread across four modules and two stores: the candidate record
and everything hanging off it (recruiting), their documents and extracted text
(documents), chunks, embeddings and saved answers that quote them (retrieval), the
files themselves (object storage), and pipeline events that point at them. Cascading
deletes cover some of that; nothing covers all of it. "Deleted" that can't be checked is
a claim, so erasure ends with a search for what it was supposed to remove.

It runs in the composition layer because it is the one operation that has to know about
every module. It runs under the workspace's row-level security context, so a bug here
can over-delete within the workspace but can never reach another one.

Step order matters:

1. **collect** what to erase, and fingerprints to verify against: the name, email,
   document and version ids, and a few verbatim snippets of the CV's text;
2. **answers** that cite the documents or mention the person;
3. **files** in object storage;
4. **documents**, which cascades to versions, extracted text, chunks and embeddings;
5. **pipeline events** (outbox and dead letters) that name the versions;
6. **the candidate**, which cascades to applications, assessments, scorecards and stage
   history. Last on purpose: the row holds the name, and a crash before this point can
   be re-run with the fingerprints still available;
7. **verify**: every text, JSON and UUID column of every workspace table is searched for
   every fingerprint, and every file key must be gone.

Documents shared with another candidate are kept and reported, not deleted: erasing one
person must not destroy another's application. The audit log is never edited (it is
append-only and hash-chained) and is included in verification, which is why nothing
written to it may identify a candidate.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from insightlm.documents.ports.storage import ObjectStore
from insightlm.governance.domain.audit import AuditAction
from insightlm.recruiting.ports.repositories import RecruitingUnitOfWorkFactory
from insightlm.shared.adapters.db.base import set_actor_context, set_tenant_context
from insightlm.shared.domain.errors import NotFoundError
from insightlm.shared.domain.ids import CandidateId
from insightlm.shared.domain.policy import Actor, require
from insightlm.shared.domain.roles import Permission
from insightlm.shared.observability.logging import get_logger

_logger = get_logger(__name__)

# Tables that hold workspace *members*, never candidates, plus Alembic's bookkeeping.
# Excluded from the residue search so an employee who shares a candidate's name isn't
# reported as a leak. Everything else is searched.
_NOT_CANDIDATE_DATA = frozenset(
    {"alembic_version", "users", "tenants", "memberships", "invitations"}
)
# The audit log is append-only and keeps bare ids as tombstones: that an upload happened
# and was later erased is exactly what it exists to prove. An id points at nothing once
# the data is gone. Names, emails and CV text are still searched for there, and must
# never be found.
_TOMBSTONE_KINDS = frozenset({"document_id", "version_id"})
_SEARCHABLE_TYPES = ("text", "character varying", "jsonb", "json", "uuid")
_PROBES = 3
_PROBE_LENGTH = 40


@dataclass(frozen=True, slots=True)
class Residue:
    table: str
    column: str
    fingerprint: str  # which kind of fingerprint matched, never the value itself


@dataclass(slots=True)
class ErasureReport:
    candidate_id: str
    documents_erased: int = 0
    documents_kept_shared: int = 0
    files_deleted: int = 0
    answers_deleted: int = 0
    events_deleted: int = 0
    residue: list[Residue] = field(default_factory=list)

    @property
    def verified(self) -> bool:
        return not self.residue


@dataclass(frozen=True, slots=True)
class _Plan:
    name: str
    email: str | None
    documents: list[UUID]
    shared: list[UUID]
    versions: list[UUID]
    storage_keys: list[str]
    probes: list[str]


def _like(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _probes(bodies: Sequence[str]) -> list[str]:
    """A few verbatim, distinctive snippets of the CV's own text."""
    lines = [line.strip() for body in bodies for line in body.splitlines()]
    candidates = [line for line in lines if len(line) >= _PROBE_LENGTH]
    if not candidates:
        return []
    step = max(1, len(candidates) // _PROBES)
    return [line[:_PROBE_LENGTH] for line in candidates[::step][:_PROBES]]


class EraseCandidate:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        recruiting_uow: RecruitingUnitOfWorkFactory,
        store: ObjectStore,
    ) -> None:
        self._sessions = session_factory
        self._recruiting_uow = recruiting_uow
        self._store = store

    async def _session(self, actor: Actor) -> AsyncSession:
        session = self._sessions()
        await session.begin()
        await set_tenant_context(session, actor.tenant_id)
        await set_actor_context(session, actor.user_id)
        return session

    async def execute(self, actor: Actor, candidate_id: CandidateId) -> ErasureReport:
        require(actor, Permission.TENANT_MANAGE)
        plan = await self._collect(actor, candidate_id)
        report = ErasureReport(
            candidate_id=str(candidate_id), documents_kept_shared=len(plan.shared)
        )
        await self._audit(
            actor,
            AuditAction.ERASURE_REQUESTED,
            candidate_id,
            {"documents": len(plan.documents), "shared_documents_kept": len(plan.shared)},
        )

        report.answers_deleted = await self._delete_answers(actor, plan)
        report.files_deleted = await self._delete_files(plan)
        report.documents_erased = await self._delete_documents(actor, plan)
        report.events_deleted = await self._delete_events(actor, plan)
        await self._delete_candidate(actor, candidate_id)
        report.residue = await self.verify(actor, plan, candidate_id)

        await self._audit(
            actor,
            AuditAction.ERASURE_COMPLETED,
            candidate_id,
            {
                "documents_erased": report.documents_erased,
                "files_deleted": report.files_deleted,
                "answers_deleted": report.answers_deleted,
                "residue": [f"{r.table}.{r.column}:{r.fingerprint}" for r in report.residue],
                "verified": report.verified,
            },
        )
        if not report.verified:
            _logger.error(
                "erasure_residue_found", candidate_id=str(candidate_id), residue=len(report.residue)
            )
        return report

    # -- steps ---------------------------------------------------------------------------

    async def _collect(self, actor: Actor, candidate_id: CandidateId) -> _Plan:
        session = await self._session(actor)
        try:
            row = (
                await session.execute(
                    text("SELECT name, email, document_ids FROM candidates WHERE id = :id"),
                    {"id": candidate_id},
                )
            ).one_or_none()
            if row is None:
                raise NotFoundError("candidate not found")
            documents = [UUID(d) for d in row.document_ids]
            # A document attached to another candidate too stays; theirs is not ours to erase.
            shared_rows = await session.execute(
                text(
                    "SELECT DISTINCT d FROM candidates, jsonb_array_elements_text(document_ids) d "
                    "WHERE id <> :id AND d = ANY(:docs)"
                ),
                {"id": candidate_id, "docs": [str(d) for d in documents]},
            )
            shared = [UUID(r[0]) for r in shared_rows]
            erasable = [d for d in documents if d not in shared]
            versions = (
                await session.execute(
                    text(
                        "SELECT id, storage_key FROM document_versions "
                        "WHERE document_id = ANY(:docs)"
                    ),
                    {"docs": erasable},
                )
            ).all()
            bodies = (
                await session.execute(
                    text(
                        "SELECT body FROM document_texts WHERE document_version_id = ANY(:versions)"
                    ),
                    {"versions": [v.id for v in versions]},
                )
            ).scalars()
            return _Plan(
                name=row.name,
                email=row.email,
                documents=erasable,
                shared=shared,
                versions=[v.id for v in versions],
                storage_keys=[v.storage_key for v in versions],
                probes=_probes(list(bodies)),
            )
        finally:
            await session.rollback()
            await session.close()

    async def _delete_answers(self, actor: Actor, plan: _Plan) -> int:
        patterns = [_like(str(d)) for d in plan.documents] + [_like(plan.name)]
        if plan.email:
            patterns.append(_like(plan.email))
        session = await self._session(actor)
        try:
            result = await session.execute(
                text(
                    "DELETE FROM answers WHERE claims::text ILIKE ANY(:p) "
                    "OR question ILIKE ANY(:p) OR coalesce(refusal_reason, '') ILIKE ANY(:p)"
                ),
                {"p": patterns},
            )
            await session.commit()
            return int(result.rowcount or 0)  # type: ignore[attr-defined]
        finally:
            await session.close()

    async def _delete_files(self, plan: _Plan) -> int:
        deleted = 0
        for key in plan.storage_keys:
            await self._store.delete(key)  # idempotent: deleting a missing key is fine
            deleted += 1
        return deleted

    async def _delete_documents(self, actor: Actor, plan: _Plan) -> int:
        session = await self._session(actor)
        try:
            result = await session.execute(
                text("DELETE FROM documents WHERE id = ANY(:docs)"), {"docs": plan.documents}
            )
            await session.commit()
            return int(result.rowcount or 0)  # type: ignore[attr-defined]
        finally:
            await session.close()

    async def _delete_events(self, actor: Actor, plan: _Plan) -> int:
        ids = [_like(str(v)) for v in plan.versions] + [_like(str(d)) for d in plan.documents]
        if not ids:
            return 0
        session = await self._session(actor)
        try:
            outbox = await session.execute(
                text("DELETE FROM outbox_events WHERE payload::text ILIKE ANY(:p)"), {"p": ids}
            )
            dead = await session.execute(
                text(
                    "DELETE FROM dead_letters WHERE payload::text ILIKE ANY(:p) "
                    "OR subject_id ILIKE ANY(:p)"
                ),
                {"p": ids},
            )
            await session.commit()
            return int((outbox.rowcount or 0) + (dead.rowcount or 0))  # type: ignore[attr-defined]
        finally:
            await session.close()

    async def _delete_candidate(self, actor: Actor, candidate_id: CandidateId) -> None:
        session = await self._session(actor)
        try:
            await session.execute(
                text("DELETE FROM candidates WHERE id = :id"), {"id": candidate_id}
            )
            await session.commit()
        finally:
            await session.close()

    async def _audit(
        self,
        actor: Actor,
        action: AuditAction,
        candidate_id: CandidateId,
        payload: dict[str, object],
    ) -> None:
        # Ids and counts only. The audit log can never be erased, so it must never
        # hold anything that identifies the person.
        async with self._recruiting_uow(tenant_id=actor.tenant_id, user_id=actor.user_id) as uow:
            await uow.audit.record(
                tenant_id=actor.tenant_id,
                actor_id=actor.user_id,
                action=action,
                subject_type="candidate",
                subject_id=str(candidate_id),
                payload=payload,
            )
            await uow.commit()

    # -- verification --------------------------------------------------------------------

    async def verify(self, actor: Actor, plan: _Plan, candidate_id: CandidateId) -> list[Residue]:
        """Search every workspace table for every fingerprint, and every file key."""
        fingerprints: list[tuple[str, str]] = [("name", plan.name)]
        if plan.email:
            fingerprints.append(("email", plan.email))
        fingerprints += [("document_id", str(d)) for d in plan.documents]
        fingerprints += [("version_id", str(v)) for v in plan.versions]
        fingerprints += [("cv_text", p) for p in plan.probes]

        residue: list[Residue] = []
        session = await self._session(actor)
        try:
            columns = (
                await session.execute(
                    text(
                        "SELECT table_name, column_name FROM information_schema.columns "
                        "WHERE table_schema = 'public' AND data_type = ANY(:types) "
                        "ORDER BY table_name, ordinal_position"
                    ),
                    {"types": list(_SEARCHABLE_TYPES)},
                )
            ).all()
            by_table: dict[str, list[str]] = {}
            for table, column in columns:
                if table not in _NOT_CANDIDATE_DATA:
                    by_table.setdefault(table, []).append(column)

            for table, cols in by_table.items():
                for column in cols:
                    # Identifiers come from information_schema, never from input, and
                    # are quoted; the searched-for value is a bound parameter.
                    query = f'SELECT 1 FROM "{table}" WHERE "{column}"::text ILIKE :p LIMIT 1'  # noqa: S608
                    for kind, value in fingerprints:
                        if table == "audit_entries" and kind in _TOMBSTONE_KINDS:
                            continue
                        found = await session.execute(text(query), {"p": _like(value)})
                        if found.first() is not None:
                            residue.append(Residue(table, column, kind))
        finally:
            await session.rollback()
            await session.close()

        for key in plan.storage_keys:
            if await self._still_stored(key):
                residue.append(Residue("object_store", key.split("/")[0], "file"))
        return residue

    async def _still_stored(self, key: str) -> bool:
        try:
            await self._store.get(key)
        except Exception as error:
            _logger.debug(
                "erasure_file_absent", key_prefix=key.split("/")[0], error=type(error).__name__
            )
            return False
        return True
