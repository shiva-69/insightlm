"""The composition root.

This is the *only* module that names both a port and the adapter satisfying it. Every
other module depends on Protocols alone. There is no DI framework: a container is not
needed when there is exactly one wiring site, and one readable wiring site is worth
more than a clever one.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from insightlm.ask_service import AskService
from insightlm.documents.adapters.pymupdf_parser import PyMuPdfParser
from insightlm.documents.adapters.s3_store import S3ObjectStore
from insightlm.documents.adapters.uow import SqlAlchemyDocumentsUnitOfWork
from insightlm.documents.application.upload import UploadDocument
from insightlm.documents.ports.repositories import DocumentsUnitOfWork
from insightlm.documents.ports.storage import ObjectStore
from insightlm.governance.application.audit_queries import ListAuditEntries, VerifyAuditChain
from insightlm.governance.ports.uow import GovernanceUnitOfWork
from insightlm.identity.adapters.security import Argon2PasswordHasher, JwtTokenIssuer
from insightlm.identity.adapters.uow import SqlAlchemyIdentityUnitOfWork
from insightlm.identity.application.authenticate import Authenticate
from insightlm.identity.application.invitations import AcceptInvitation, InviteMember
from insightlm.identity.application.members import (
    ChangeMemberRole,
    ListMembers,
    RevokeMembership,
)
from insightlm.identity.application.register import RegisterOwner
from insightlm.identity.ports.uow import IdentityUnitOfWork
from insightlm.retrieval.adapters.anthropic_generator import AnthropicGroundedGenerator
from insightlm.retrieval.adapters.chunker import StructureAwareChunker
from insightlm.retrieval.adapters.embedding import (
    GeminiEmbedder,
    HashingEmbedder,
    VoyageEmbedder,
)
from insightlm.retrieval.adapters.extractive_generator import ExtractiveGenerator
from insightlm.retrieval.ports.generation import GroundedGenerator
from insightlm.retrieval.ports.indexing import Embedder
from insightlm.settings import Settings
from insightlm.shared.adapters.clock import SystemClock
from insightlm.shared.adapters.db.base import create_engine, create_session_factory
from insightlm.shared.domain.errors import InvariantViolationError
from insightlm.shared.domain.ids import TenantId, UserId
from insightlm.shared.observability.logging import get_logger
from insightlm.shared.ports.clock import Clock
from insightlm.shared.ports.password import PasswordHasher
from insightlm.shared.ports.tokens import TokenIssuer
from insightlm.worker.ingestion import IngestionPipeline

_logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class Container:
    """Everything the delivery layers need, already wired."""

    settings: Settings
    engine: AsyncEngine
    session_factory: async_sessionmaker[AsyncSession]
    clock: Clock
    password_hasher: PasswordHasher
    token_issuer: TokenIssuer
    object_store: ObjectStore
    embedder: Embedder
    generator: GroundedGenerator

    register_owner: RegisterOwner
    authenticate: Authenticate
    invite_member: InviteMember
    accept_invitation: AcceptInvitation
    list_members: ListMembers
    change_member_role: ChangeMemberRole
    revoke_membership: RevokeMembership
    list_audit_entries: ListAuditEntries
    verify_audit_chain: VerifyAuditChain
    upload_document: UploadDocument
    ask: AskService
    ingestion: IngestionPipeline
    documents_uow: object

    async def dispose(self) -> None:
        await self.engine.dispose()


def build_container(settings: Settings) -> Container:
    engine = create_engine(settings)
    session_factory = create_session_factory(engine)

    clock: Clock = SystemClock()
    hasher: PasswordHasher = Argon2PasswordHasher()
    tokens: TokenIssuer = JwtTokenIssuer(
        settings.jwt_secret.get_secret_value(),
        algorithm=settings.jwt_algorithm,
        ttl_seconds=settings.access_token_ttl_seconds,
    )

    def uow_factory(
        *, tenant_id: TenantId | None = None, user_id: UserId | None = None
    ) -> IdentityUnitOfWork:
        return SqlAlchemyIdentityUnitOfWork(
            session_factory, clock, tenant_id=tenant_id, user_id=user_id
        )

    # The same adapter satisfies the narrower governance port -- structural typing
    # means no wrapper is needed, and governance still cannot see identity's repos.
    def governance_uow_factory(
        *, tenant_id: TenantId | None = None, user_id: UserId | None = None
    ) -> GovernanceUnitOfWork:
        return SqlAlchemyIdentityUnitOfWork(
            session_factory, clock, tenant_id=tenant_id, user_id=user_id
        )

    store = S3ObjectStore(settings)
    embedder = _build_embedder(settings)
    if settings.embedding_cache_path:
        from pathlib import Path

        from insightlm.retrieval.adapters.embedding_cache import (
            CachingEmbedder,
            EmbeddingStore,
        )

        embedder = CachingEmbedder(embedder, EmbeddingStore(Path(settings.embedding_cache_path)))
    generator = _build_generator(settings)

    def documents_uow(
        *, tenant_id: TenantId | None = None, user_id: UserId | None = None
    ) -> DocumentsUnitOfWork:
        return SqlAlchemyDocumentsUnitOfWork(
            session_factory, clock, tenant_id=tenant_id, user_id=user_id
        )

    return Container(
        settings=settings,
        engine=engine,
        session_factory=session_factory,
        clock=clock,
        password_hasher=hasher,
        token_issuer=tokens,
        register_owner=RegisterOwner(uow_factory, hasher, clock),
        authenticate=Authenticate(
            uow_factory,
            hasher,
            tokens,
            clock,
            access_token_ttl_seconds=settings.access_token_ttl_seconds,
        ),
        invite_member=InviteMember(
            uow_factory, clock, invitation_ttl_seconds=settings.invitation_ttl_seconds
        ),
        accept_invitation=AcceptInvitation(uow_factory, hasher, clock),
        list_members=ListMembers(uow_factory),
        change_member_role=ChangeMemberRole(uow_factory, clock),
        revoke_membership=RevokeMembership(uow_factory, clock),
        list_audit_entries=ListAuditEntries(governance_uow_factory),
        verify_audit_chain=VerifyAuditChain(governance_uow_factory),
        object_store=store,
        embedder=embedder,
        generator=generator,
        upload_document=UploadDocument(documents_uow, store, clock),
        ask=AskService(
            session_factory,
            embedder=embedder,
            generator=generator,
            rrf_k=settings.rrf_k,
        ),
        ingestion=IngestionPipeline(
            session_factory,
            store=store,
            parser=PyMuPdfParser(),
            chunker=StructureAwareChunker(enrich_with_title=settings.chunk_enrich_with_title),
            embedder=embedder,
            clock=clock,
        ),
        documents_uow=documents_uow,
    )


def _build_embedder(settings: Settings) -> Embedder:
    """Real embeddings when a key is configured, deterministic offline ones otherwise.

    Voyage first when both are present, only because it is the purpose-built option;
    either is a real semantic embedder and the eval harness is what should decide.

    The fallback is not a stub. It makes CI, a clean checkout and a live demo all work
    with no account and no network -- and because it is deterministic, retrieval tests
    assert exact rankings. Its quality ceiling is real and documented; Phase 2's eval
    harness measures the gap rather than assuming it.
    """
    if settings.voyage_api_key is not None:
        _logger.info("using_embedder", provider="voyage")
        return VoyageEmbedder(settings.voyage_api_key.get_secret_value())

    if settings.gemini_api_key is not None:
        _logger.info("using_embedder", provider="gemini", model=settings.gemini_embedding_model)
        return GeminiEmbedder(
            settings.gemini_api_key.get_secret_value(),
            model=settings.gemini_embedding_model,
        )

    _logger.info("using_offline_embedder", reason="no VOYAGE_API_KEY or GEMINI_API_KEY configured")
    return HashingEmbedder()


def _build_generator(settings: Settings) -> GroundedGenerator:
    """Pick a generator from configuration.

    All three satisfy one port, so the choice changes nothing downstream: retrieval,
    the citation contract and validation are identical whichever answers. That is the
    point of the port -- and it is what lets the evaluation re-run the same ablation
    on a second provider, turning "our pipeline scores X" into "the effect holds
    across models".

    `auto` picks the first provider with a key. Naming one explicitly makes a missing
    key an error rather than a silent downgrade, which matters in CI and during an
    evaluation run: answering with the wrong engine invalidates the results without
    anything appearing to go wrong.
    """
    provider = settings.generation_provider

    if provider == "offline":
        _logger.info("using_offline_generator", reason="explicitly selected")
        return ExtractiveGenerator()

    if provider in ("auto", "anthropic") and settings.anthropic_api_key is not None:
        from anthropic import AsyncAnthropic

        _logger.info("using_generator", provider="anthropic", model=settings.generation_model)
        return AnthropicGroundedGenerator(
            AsyncAnthropic(api_key=settings.anthropic_api_key.get_secret_value()),
            model=settings.generation_model,
            effort=settings.generation_effort,
        )

    if provider in ("auto", "gemini") and settings.gemini_api_key is not None:
        from insightlm.retrieval.adapters.gemini_generator import GeminiGroundedGenerator

        _logger.info("using_generator", provider="gemini", model=settings.gemini_model)
        return GeminiGroundedGenerator(
            settings.gemini_api_key.get_secret_value(), model=settings.gemini_model
        )

    if provider != "auto":
        raise InvariantViolationError(
            f"generation_provider is '{provider}' but its API key is not configured",
            detail={"provider": provider, "expected_env": f"{provider.upper()}_API_KEY"},
        )

    # The extractive fallback cannot hallucinate -- every claim is copied verbatim from
    # a retrieved span -- so the citation contract holds either way. What it cannot do
    # is synthesise across sources or paraphrase, which is exactly the difference the
    # Phase 2 evaluation measures, and which the UI now states plainly.
    _logger.info(
        "using_offline_generator", reason="no ANTHROPIC_API_KEY or GEMINI_API_KEY configured"
    )
    return ExtractiveGenerator()
