"""The PostgreSQL units of work on SQLAlchemy's async session.

A space transaction runs in the space's own database, reached through the `EngineRegistry`: as the
application role, subject to row-level security, with the space pinned by `set_config(..., true)`,
which lives only inside the transaction and is safe behind PgBouncer in transaction mode; or as the
system role (BYPASSRLS) for maintenance of that one database. A cell transaction runs in the cell
database as the system role; it has no space and no sealer.
"""

from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from typing import Any, Literal, TypeVar
from uuid import UUID

from sqlalchemy import exc, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from niadra.adapters.outbound.postgres.agent_memory import PgAgentMemoryRepository
from niadra.adapters.outbound.postgres.agent_state import PgAgentStateRepository
from niadra.adapters.outbound.postgres.audit import PgReceiptRepository, PgWatermarkRepository
from niadra.adapters.outbound.postgres.compile import PgPackRepository
from niadra.adapters.outbound.postgres.config import PgConfigRepository
from niadra.adapters.outbound.postgres.configure import PgAssistRepository
from niadra.adapters.outbound.postgres.coordinate import PgCoordinateRepository
from niadra.adapters.outbound.postgres.decide import PgDecisionRepository
from niadra.adapters.outbound.postgres.directory import PgSpaceDirectoryRepository
from niadra.adapters.outbound.postgres.edge import PgIdempotencyRepository, PgSourceKeyRepository
from niadra.adapters.outbound.postgres.engines import make_engine, raise_unreachable
from niadra.adapters.outbound.postgres.extract import PgExtractionRunRepository
from niadra.adapters.outbound.postgres.feed import PgFeedRepository
from niadra.adapters.outbound.postgres.graph import PgGraphRepository
from niadra.adapters.outbound.postgres.holds import PgLegalHoldRepository
from niadra.adapters.outbound.postgres.identity import PgIdentityRepository
from niadra.adapters.outbound.postgres.ingest import PgEventRepository
from niadra.adapters.outbound.postgres.insights import PgInsightsRepository
from niadra.adapters.outbound.postgres.measure import PgMeasureRepository
from niadra.adapters.outbound.postgres.memory import PgMemoryRepository
from niadra.adapters.outbound.postgres.mentions import PgMentionRepository
from niadra.adapters.outbound.postgres.metering import PgUsageRepository
from niadra.adapters.outbound.postgres.notify import PgWebhookRepository
from niadra.adapters.outbound.postgres.objects import PgObjectRepository
from niadra.adapters.outbound.postgres.outcomes import PgOutcomeRepository
from niadra.adapters.outbound.postgres.pipeline import PgDeadTaskRepository, PgTaskStore
from niadra.adapters.outbound.postgres.privacy import PgPrivacyRepository
from niadra.adapters.outbound.postgres.registry import EngineRegistry
from niadra.adapters.outbound.postgres.review import PgReviewRepository
from niadra.adapters.outbound.postgres.review_requests import PgReviewRequestRepository
from niadra.adapters.outbound.postgres.sealer import Sealer
from niadra.adapters.outbound.postgres.serve import PgServeRepository
from niadra.adapters.outbound.postgres.servers import Role
from niadra.adapters.outbound.postgres.sessions import PgHealthRepository, PgSessionRepository
from niadra.adapters.outbound.postgres.signals import PgSignalRepository
from niadra.adapters.outbound.postgres.traits import PgTraitRepository
from niadra.adapters.outbound.postgres.triggers import PgTriggerRepository
from niadra.adapters.outbound.postgres.turns import PgTurnRepository
from niadra.adapters.outbound.postgres.typed import PgTypedRepository
from niadra.domain.common.errors import Unavailable
from niadra.ports.infra import KeyVault

Kind = Literal["space", "cell"]
R = TypeVar("R")

__all__ = ["PgUnitOfWork", "PgUnitOfWorkFactory", "make_engine"]


class PgUnitOfWork:
    """Every repository of both kinds of database, built on first use. A repository of the other kind
    raises: a cell repository inside a space transaction would read the wrong database."""

    def __init__(
        self, session: AsyncSession, sealer: Sealer | None, space_id: UUID | None, kind: Kind
    ) -> None:
        self.session = session
        self.kind = kind
        self._space_id = space_id
        self._sealer = sealer
        self._repos: dict[str, Any] = {}

    @property
    def space_id(self) -> UUID:
        if self._space_id is None:
            raise RuntimeError("a cell transaction has no space")
        return self._space_id

    def _repo(self, name: str, kind: Kind, make: Callable[[AsyncSession, Sealer | None], R]) -> R:
        if kind != self.kind:
            raise RuntimeError(f"{name} lives in the {kind} database")
        if name not in self._repos:
            self._repos[name] = make(self.session, self._sealer)
        repo: R = self._repos[name]
        return repo

    # Space database.

    @property
    def edge(self) -> PgIdempotencyRepository:
        return self._repo("edge", "space", PgIdempotencyRepository)

    @property
    def ingest(self) -> PgEventRepository:
        return self._repo("ingest", "space", PgEventRepository)

    @property
    def mentions(self) -> PgMentionRepository:
        return self._repo("mentions", "space", PgMentionRepository)

    async def flush_mentions(self, keys: KeyVault) -> None:  # noqa: ARG002 (the sealer holds the space's keys)
        """What the rows sealed in this transaction name a person by and no statement of theirs carried goes
        to `text_mentions` before the commit (`domain.privacy.mentions`): a row and its index entry are
        written together or not at all."""
        if self._sealer is None or not self._sealer.written or self._space_id is None:
            return
        if entries := await self._sealer.take_mentions():
            await self.mentions.index(entries)

    @property
    def identity(self) -> PgIdentityRepository:
        return self._repo("identity", "space", PgIdentityRepository)

    @property
    def sessions(self) -> PgSessionRepository:
        return self._repo("sessions", "space", PgSessionRepository)

    @property
    def pipeline(self) -> PgTaskStore:
        return self._repo("pipeline", "space", PgTaskStore)

    @property
    def decide(self) -> PgDecisionRepository:
        return self._repo("decide", "space", PgDecisionRepository)

    @property
    def extract(self) -> PgExtractionRunRepository:
        return self._repo("extract", "space", PgExtractionRunRepository)

    @property
    def memory(self) -> PgMemoryRepository:
        return self._repo("memory", "space", PgMemoryRepository)

    @property
    def objects(self) -> PgObjectRepository:
        return self._repo("objects", "space", PgObjectRepository)

    @property
    def traits(self) -> PgTraitRepository:
        return self._repo("traits", "space", PgTraitRepository)

    @property
    def compile(self) -> PgPackRepository:
        return self._repo("compile", "space", PgPackRepository)

    @property
    def serve(self) -> PgServeRepository:
        return self._repo("serve", "space", PgServeRepository)

    @property
    def audit(self) -> PgReceiptRepository:
        return self._repo("audit", "space", PgReceiptRepository)

    @property
    def measure(self) -> PgMeasureRepository:
        return self._repo("measure", "space", PgMeasureRepository)

    @property
    def privacy(self) -> PgPrivacyRepository:
        return self._repo("privacy", "space", PgPrivacyRepository)

    @property
    def triggers(self) -> PgTriggerRepository:
        return self._repo("triggers", "space", PgTriggerRepository)

    @property
    def notify(self) -> PgWebhookRepository:
        return self._repo("notify", "space", PgWebhookRepository)

    @property
    def review(self) -> PgReviewRepository:
        return self._repo("review", "space", PgReviewRepository)

    @property
    def configure(self) -> PgAssistRepository:
        return self._repo("configure", "space", PgAssistRepository)

    @property
    def metering(self) -> PgUsageRepository:
        return self._repo("metering", "space", PgUsageRepository)

    @property
    def insights(self) -> PgInsightsRepository:
        return self._repo("insights", "space", PgInsightsRepository)

    @property
    def graph(self) -> PgGraphRepository:
        return self._repo("graph", "space", PgGraphRepository)

    @property
    def agent_memory(self) -> PgAgentMemoryRepository:
        return self._repo("agent_memory", "space", PgAgentMemoryRepository)

    @property
    def turns(self) -> PgTurnRepository:
        return self._repo("turns", "space", PgTurnRepository)

    @property
    def feed(self) -> PgFeedRepository:
        return self._repo("feed", "space", PgFeedRepository)

    @property
    def agent_state(self) -> PgAgentStateRepository:
        return self._repo("agent_state", "space", PgAgentStateRepository)

    @property
    def typed(self) -> PgTypedRepository:
        return self._repo("typed", "space", PgTypedRepository)

    @property
    def coordinate(self) -> PgCoordinateRepository:
        return self._repo("coordinate", "space", PgCoordinateRepository)

    @property
    def signals(self) -> PgSignalRepository:
        return self._repo("signals", "space", PgSignalRepository)

    @property
    def outcomes(self) -> PgOutcomeRepository:
        return self._repo("outcomes", "space", PgOutcomeRepository)

    @property
    def legal_holds(self) -> PgLegalHoldRepository:
        return self._repo("legal_holds", "space", PgLegalHoldRepository)

    @property
    def review_requests(self) -> PgReviewRequestRepository:
        return self._repo("review_requests", "space", PgReviewRequestRepository)

    # Cell database.

    @property
    def config(self) -> PgConfigRepository:
        return self._repo("config", "cell", PgConfigRepository)

    @property
    def keys(self) -> PgSourceKeyRepository:
        return self._repo("keys", "cell", PgSourceKeyRepository)

    @property
    def health(self) -> PgHealthRepository:
        return self._repo("health", "cell", PgHealthRepository)

    @property
    def watermarks(self) -> PgWatermarkRepository:
        return self._repo("watermarks", "cell", PgWatermarkRepository)

    @property
    def dead(self) -> PgDeadTaskRepository:
        return self._repo("dead", "cell", PgDeadTaskRepository)

    @property
    def directory(self) -> PgSpaceDirectoryRepository:
        return self._repo("directory", "cell", PgSpaceDirectoryRepository)


class PgUnitOfWorkFactory:
    def __init__(self, keys: KeyVault, registry: EngineRegistry, cell_engine: AsyncEngine) -> None:
        self._keys = keys
        self._registry = registry
        self._cell = cell_engine

    def __call__(self, space_id: UUID) -> AbstractAsyncContextManager[PgUnitOfWork]:
        return self._space(space_id, "app")

    def system(self, space_id: UUID) -> AbstractAsyncContextManager[PgUnitOfWork]:
        return self._space(space_id, "system")

    @asynccontextmanager
    async def cell(self) -> AsyncIterator[PgUnitOfWork]:
        async with AsyncSession(self._cell, expire_on_commit=False) as session, session.begin():
            await _connect(session)
            yield PgUnitOfWork(session, None, None, "cell")

    @asynccontextmanager
    async def _space(self, space_id: UUID, role: Role) -> AsyncIterator[PgUnitOfWork]:
        async with self._registry.lease(space_id, role) as engine:
            for attempt in range(2):
                try:
                    async with AsyncSession(engine, expire_on_commit=False) as session, session.begin():
                        await _connect(session)
                        await _pin(session, space_id, retry=attempt == 0)
                        uow = PgUnitOfWork(session, Sealer(self._keys, space_id), space_id, "space")
                        yield uow
                        await uow.flush_mentions(self._keys)
                    return
                except _DeadConnection:
                    continue


class _DeadConnection(Exception):
    """The pooled connection was gone before the transaction's first statement took effect."""


async def _pin(session: AsyncSession, space_id: UUID, *, retry: bool) -> None:
    """The transaction's first statement, which also finds a connection the pool kept but the pooler or the
    server closed (a restart, an idle timeout): the space engines skip the pool's ping, so this statement
    stands in for it. Nothing of the transaction took effect yet, so it starts again on a fresh connection,
    once."""
    try:
        await session.execute(
            text("SELECT set_config('niadra.space_id', :space, true)"), {"space": str(space_id)}
        )
    except exc.DBAPIError as error:
        if retry and error.connection_invalidated:
            raise _DeadConnection from error
        raise_unreachable(error)


async def _connect(session: AsyncSession) -> None:
    """Checks a connection out now, so a pool that stays full, or a database out of reach, turns into a 503,
    not a stuck request or a 500."""
    try:
        await session.connection()
    except exc.TimeoutError:
        raise Unavailable("the database connection pool is busy", retry_after_s=1) from None
    except exc.DBAPIError as error:
        raise_unreachable(error)
