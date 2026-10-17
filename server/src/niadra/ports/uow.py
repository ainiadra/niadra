"""The unit of work: one database transaction, in a space's database or in the cell database.

Three kinds, one factory:

    async with uow(space_id) as tx:          # the space's database, as the application role;
        ...                                  # row-level security on, the space pinned
    async with uow.system(space_id) as tx:   # the same database as the system role: TRUNCATE,
        ...                                  # partition upkeep, sweeps
    async with uow.cell() as tx:             # the cell database: directory, source keys, health,
        ...                                  # watermarks, dead tasks, the configuration copy

Use cases reach every module's repository through the transaction, so writes that must be atomic
(the raw event and its outbox row, for instance) share one transaction. Leaving the block commits;
an exception rolls back. A transaction never spans two databases: code that needs both opens two,
one after the other, and says which one wins if the second fails.
"""

from contextlib import AbstractAsyncContextManager
from typing import Protocol
from uuid import UUID

from niadra.ports.repos.agent_memory import AgentMemoryRepository
from niadra.ports.repos.agent_state import AgentStateRepository
from niadra.ports.repos.audit import ReceiptRepository, WatermarkRepository
from niadra.ports.repos.compile import PackRepository
from niadra.ports.repos.config import ConfigRepository
from niadra.ports.repos.configure import AssistRepository
from niadra.ports.repos.coordinate import CoordinateRepository
from niadra.ports.repos.decide import DecisionRepository
from niadra.ports.repos.directory import SpaceDirectoryRepository
from niadra.ports.repos.edge import IdempotencyRepository, SourceKeyRepository
from niadra.ports.repos.extract import ExtractionRunRepository
from niadra.ports.repos.feed import FeedRepository
from niadra.ports.repos.graph import GraphRepository
from niadra.ports.repos.holds import LegalHoldRepository
from niadra.ports.repos.identity import IdentityRepository
from niadra.ports.repos.ingest import EventRepository
from niadra.ports.repos.insights import InsightsRepository
from niadra.ports.repos.measure import MeasureRepository
from niadra.ports.repos.memory import MemoryRepository
from niadra.ports.repos.mentions import MentionRepository
from niadra.ports.repos.metering import UsageRepository
from niadra.ports.repos.notify import WebhookRepository
from niadra.ports.repos.objects import ObjectRepository
from niadra.ports.repos.outcomes import OutcomeRepository
from niadra.ports.repos.pipeline import DeadTaskRepository, TaskStore
from niadra.ports.repos.privacy import PrivacyRepository
from niadra.ports.repos.review import ReviewRepository
from niadra.ports.repos.review_requests import ReviewRequestRepository
from niadra.ports.repos.serve import ServeRepository
from niadra.ports.repos.sessions import HealthRepository, SessionRepository
from niadra.ports.repos.signals import SignalRepository
from niadra.ports.repos.traits import TraitRepository
from niadra.ports.repos.triggers import TriggerRepository
from niadra.ports.repos.turns import TurnRepository
from niadra.ports.repos.typed import TypedRepository


class SpaceUnitOfWork(Protocol):
    # Read-only members: an adapter may expose a more specific repository type (protocols are
    # covariant only for properties, not for plain attributes).
    @property
    def space_id(self) -> UUID: ...

    @property
    def edge(self) -> IdempotencyRepository: ...

    @property
    def ingest(self) -> EventRepository: ...

    @property
    def identity(self) -> IdentityRepository: ...

    @property
    def sessions(self) -> SessionRepository: ...

    @property
    def pipeline(self) -> TaskStore: ...

    @property
    def decide(self) -> DecisionRepository: ...

    @property
    def extract(self) -> ExtractionRunRepository: ...

    @property
    def memory(self) -> MemoryRepository: ...

    @property
    def mentions(self) -> MentionRepository: ...

    @property
    def objects(self) -> ObjectRepository: ...

    @property
    def traits(self) -> TraitRepository: ...

    @property
    def compile(self) -> PackRepository: ...

    @property
    def serve(self) -> ServeRepository: ...

    @property
    def audit(self) -> ReceiptRepository: ...

    @property
    def measure(self) -> MeasureRepository: ...

    @property
    def privacy(self) -> PrivacyRepository: ...

    @property
    def triggers(self) -> TriggerRepository: ...

    @property
    def notify(self) -> WebhookRepository: ...

    @property
    def review(self) -> ReviewRepository: ...

    @property
    def configure(self) -> AssistRepository: ...

    @property
    def metering(self) -> UsageRepository: ...

    @property
    def insights(self) -> InsightsRepository: ...

    @property
    def graph(self) -> GraphRepository: ...

    @property
    def agent_memory(self) -> AgentMemoryRepository: ...

    # The agent features' modules (4.4.1).
    @property
    def turns(self) -> TurnRepository: ...

    @property
    def feed(self) -> FeedRepository: ...

    @property
    def agent_state(self) -> AgentStateRepository: ...

    @property
    def typed(self) -> TypedRepository: ...

    @property
    def coordinate(self) -> CoordinateRepository: ...

    @property
    def signals(self) -> SignalRepository: ...

    @property
    def outcomes(self) -> OutcomeRepository: ...

    @property
    def legal_holds(self) -> LegalHoldRepository: ...

    @property
    def review_requests(self) -> ReviewRequestRepository: ...


# Most use cases only ever open space transactions.
UnitOfWork = SpaceUnitOfWork


class CellUnitOfWork(Protocol):
    @property
    def config(self) -> ConfigRepository: ...

    @property
    def keys(self) -> SourceKeyRepository: ...

    @property
    def health(self) -> HealthRepository: ...

    @property
    def watermarks(self) -> WatermarkRepository: ...

    @property
    def dead(self) -> DeadTaskRepository: ...

    @property
    def directory(self) -> SpaceDirectoryRepository: ...


class UnitOfWorkFactory(Protocol):
    def __call__(self, space_id: UUID) -> AbstractAsyncContextManager[SpaceUnitOfWork]:
        """The space's database as the application role, under row-level security. Raises
        `Unavailable` (retry in a few seconds) while the database is being created or restored."""
        ...

    def system(self, space_id: UUID) -> AbstractAsyncContextManager[SpaceUnitOfWork]:
        """The space's database as the system role, for maintenance (TRUNCATE, partitions, sweeps)."""
        ...

    def cell(self) -> AbstractAsyncContextManager[CellUnitOfWork]:
        """The cell database, as the system role."""
        ...
