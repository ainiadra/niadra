"""In-memory units of work with rollback by snapshot; the same contract as PostgreSQL.

One store stands for every database: unit tests exercise one space at a time, and the physical
separation of the databases is covered by the integration tests. The kinds are still enforced, so a
cell repository used inside a space transaction fails here as it does against PostgreSQL.
"""

import copy
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from typing import Any, Literal, TypeVar
from uuid import UUID

from niadra.adapters.memory.agent_memory import InMemoryAgentMemoryRepository
from niadra.adapters.memory.agent_state import InMemoryAgentStateRepository
from niadra.adapters.memory.audit import InMemoryReceiptRepository, InMemoryWatermarkRepository
from niadra.adapters.memory.compile import InMemoryPackRepository
from niadra.adapters.memory.config import InMemoryConfigRepository
from niadra.adapters.memory.configure import InMemoryAssistRepository
from niadra.adapters.memory.coordinate import InMemoryCoordinateRepository
from niadra.adapters.memory.decide import InMemoryDecisionRepository
from niadra.adapters.memory.directory import InMemorySpaceDirectory
from niadra.adapters.memory.edge import InMemoryIdempotencyRepository, InMemorySourceKeyRepository
from niadra.adapters.memory.extract import InMemoryExtractionRunRepository
from niadra.adapters.memory.feed import InMemoryFeedRepository
from niadra.adapters.memory.graph import InMemoryGraphRepository
from niadra.adapters.memory.holds import InMemoryLegalHoldRepository
from niadra.adapters.memory.identity import InMemoryIdentityRepository
from niadra.adapters.memory.ingest import InMemoryEventRepository
from niadra.adapters.memory.insights import InMemoryInsightsRepository
from niadra.adapters.memory.measure import InMemoryMeasureRepository
from niadra.adapters.memory.memory import InMemoryMemoryRepository
from niadra.adapters.memory.mentions import InMemoryMentionRepository
from niadra.adapters.memory.metering import InMemoryUsageRepository
from niadra.adapters.memory.notify import InMemoryWebhookRepository
from niadra.adapters.memory.objects import InMemoryObjectRepository
from niadra.adapters.memory.outcomes import InMemoryOutcomeRepository
from niadra.adapters.memory.pipeline import InMemoryDeadTaskRepository, InMemoryTaskStore
from niadra.adapters.memory.privacy import InMemoryPrivacyRepository
from niadra.adapters.memory.review import InMemoryReviewRepository
from niadra.adapters.memory.review_requests import InMemoryReviewRequestRepository
from niadra.adapters.memory.serve import InMemoryServeRepository
from niadra.adapters.memory.sessions import InMemoryHealthRepository, InMemorySessionRepository
from niadra.adapters.memory.signals import InMemorySignalRepository
from niadra.adapters.memory.store import MemoryStore
from niadra.adapters.memory.traits import InMemoryTraitRepository
from niadra.adapters.memory.triggers import InMemoryTriggerRepository
from niadra.adapters.memory.turns import InMemoryTurnRepository
from niadra.adapters.memory.typed import InMemoryTypedRepository
from niadra.app.read_state import Change, thread_key

Kind = Literal["space", "cell"]
R = TypeVar("R")
SPACE_REPOS = (
    "edge",
    "ingest",
    "identity",
    "sessions",
    "pipeline",
    "decide",
    "extract",
    "memory",
    "objects",
    "traits",
    "compile",
    "serve",
    "audit",
    "measure",
    "privacy",
    "triggers",
    "notify",
    "review",
    "configure",
    "metering",
    "insights",
    "graph",
    "agent_memory",
    "turns",
    "feed",
    "agent_state",
    "typed",
    "coordinate",
    "signals",
    "outcomes",
    "legal_holds",
    "review_requests",
)
CELL_REPOS = ("config", "keys", "health", "watermarks", "dead", "directory")


class InMemoryUnitOfWork:
    def __init__(self, store: MemoryStore, space_id: UUID | None = None, kind: Kind = "space") -> None:
        self.store = store
        self.kind = kind
        self._space_id = space_id
        self._repos: dict[str, Any] = {}
        # Built up front, as the tables of a database exist before the first query: tests read the
        # store's namespaces directly.
        for name in SPACE_REPOS if kind == "space" else CELL_REPOS:
            getattr(self, name)

    @property
    def space_id(self) -> UUID:
        if self._space_id is None:
            raise RuntimeError("a cell transaction has no space")
        return self._space_id

    def _repo(self, name: str, kind: Kind, make: Callable[[MemoryStore], R]) -> R:
        if kind != self.kind:
            raise RuntimeError(f"{name} lives in the {kind} database")
        if name not in self._repos:
            self._repos[name] = make(self.store)
        repo: R = self._repos[name]
        return repo

    @property
    def edge(self) -> InMemoryIdempotencyRepository:
        return self._repo("edge", "space", InMemoryIdempotencyRepository)

    @property
    def ingest(self) -> InMemoryEventRepository:
        return self._repo("ingest", "space", lambda store: InMemoryEventRepository(store, self._space_id))

    @property
    def identity(self) -> InMemoryIdentityRepository:
        return self._repo("identity", "space", InMemoryIdentityRepository)

    @property
    def sessions(self) -> InMemorySessionRepository:
        return self._repo("sessions", "space", InMemorySessionRepository)

    @property
    def pipeline(self) -> InMemoryTaskStore:
        return self._repo("pipeline", "space", InMemoryTaskStore)

    @property
    def decide(self) -> InMemoryDecisionRepository:
        return self._repo("decide", "space", InMemoryDecisionRepository)

    @property
    def extract(self) -> InMemoryExtractionRunRepository:
        return self._repo("extract", "space", InMemoryExtractionRunRepository)

    @property
    def memory(self) -> InMemoryMemoryRepository:
        return self._repo("memory", "space", InMemoryMemoryRepository)

    @property
    def mentions(self) -> InMemoryMentionRepository:
        return self._repo("mentions", "space", InMemoryMentionRepository)

    @property
    def objects(self) -> InMemoryObjectRepository:
        return self._repo("objects", "space", InMemoryObjectRepository)

    @property
    def traits(self) -> InMemoryTraitRepository:
        return self._repo("traits", "space", InMemoryTraitRepository)

    @property
    def compile(self) -> InMemoryPackRepository:
        return self._repo("compile", "space", InMemoryPackRepository)

    @property
    def serve(self) -> InMemoryServeRepository:
        return self._repo("serve", "space", InMemoryServeRepository)

    @property
    def audit(self) -> InMemoryReceiptRepository:
        return self._repo("audit", "space", InMemoryReceiptRepository)

    @property
    def measure(self) -> InMemoryMeasureRepository:
        return self._repo("measure", "space", InMemoryMeasureRepository)

    @property
    def privacy(self) -> InMemoryPrivacyRepository:
        return self._repo("privacy", "space", InMemoryPrivacyRepository)

    @property
    def triggers(self) -> InMemoryTriggerRepository:
        return self._repo("triggers", "space", InMemoryTriggerRepository)

    @property
    def notify(self) -> InMemoryWebhookRepository:
        return self._repo("notify", "space", InMemoryWebhookRepository)

    @property
    def review(self) -> InMemoryReviewRepository:
        return self._repo("review", "space", InMemoryReviewRepository)

    @property
    def configure(self) -> InMemoryAssistRepository:
        return self._repo("configure", "space", InMemoryAssistRepository)

    @property
    def metering(self) -> InMemoryUsageRepository:
        return self._repo("metering", "space", InMemoryUsageRepository)

    @property
    def insights(self) -> InMemoryInsightsRepository:
        return self._repo("insights", "space", InMemoryInsightsRepository)

    @property
    def graph(self) -> InMemoryGraphRepository:
        return self._repo("graph", "space", InMemoryGraphRepository)

    @property
    def agent_memory(self) -> InMemoryAgentMemoryRepository:
        return self._repo("agent_memory", "space", InMemoryAgentMemoryRepository)

    @property
    def turns(self) -> InMemoryTurnRepository:
        return self._repo("turns", "space", lambda store: InMemoryTurnRepository(store, self._space_id))

    @property
    def feed(self) -> InMemoryFeedRepository:
        return self._repo("feed", "space", lambda store: InMemoryFeedRepository(store, self._space_id))

    @property
    def agent_state(self) -> InMemoryAgentStateRepository:
        return self._repo(
            "agent_state", "space", lambda store: InMemoryAgentStateRepository(store, self._space_id)
        )

    @property
    def typed(self) -> InMemoryTypedRepository:
        return self._repo("typed", "space", InMemoryTypedRepository)

    @property
    def coordinate(self) -> InMemoryCoordinateRepository:
        return self._repo("coordinate", "space", InMemoryCoordinateRepository)

    @property
    def signals(self) -> InMemorySignalRepository:
        return self._repo("signals", "space", InMemorySignalRepository)

    @property
    def outcomes(self) -> InMemoryOutcomeRepository:
        return self._repo("outcomes", "space", InMemoryOutcomeRepository)

    @property
    def legal_holds(self) -> InMemoryLegalHoldRepository:
        return self._repo("legal_holds", "space", InMemoryLegalHoldRepository)

    @property
    def review_requests(self) -> InMemoryReviewRequestRepository:
        return self._repo("review_requests", "space", InMemoryReviewRequestRepository)

    @property
    def config(self) -> InMemoryConfigRepository:
        return self._repo("config", "cell", InMemoryConfigRepository)

    @property
    def keys(self) -> InMemorySourceKeyRepository:
        return self._repo("keys", "cell", InMemorySourceKeyRepository)

    @property
    def health(self) -> InMemoryHealthRepository:
        return self._repo("health", "cell", InMemoryHealthRepository)

    @property
    def watermarks(self) -> InMemoryWatermarkRepository:
        return self._repo("watermarks", "cell", InMemoryWatermarkRepository)

    @property
    def dead(self) -> InMemoryDeadTaskRepository:
        return self._repo("dead", "cell", InMemoryDeadTaskRepository)

    @property
    def directory(self) -> InMemorySpaceDirectory:
        return self._repo("directory", "cell", InMemorySpaceDirectory)


ChangeListener = Callable[[UUID, Change], None]


class InMemoryUnitOfWorkFactory:
    def __init__(self, store: MemoryStore | None = None) -> None:
        self.store = store or MemoryStore()
        self.listeners: list[ChangeListener] = []
        """Told at each commit what changed under the read path's state, as PostgreSQL's triggers write
        `read_invalidations` (the same columns); a read process's state listens here in unit tests."""

    def __call__(self, space_id: UUID) -> AbstractAsyncContextManager[InMemoryUnitOfWork]:
        return self._open(space_id, "space")

    def system(self, space_id: UUID) -> AbstractAsyncContextManager[InMemoryUnitOfWork]:
        return self._open(space_id, "space")

    def cell(self) -> AbstractAsyncContextManager[InMemoryUnitOfWork]:
        return self._open(None, "cell")

    @asynccontextmanager
    async def _open(self, space_id: UUID | None, kind: Kind) -> AsyncIterator[InMemoryUnitOfWork]:
        snapshot = copy.deepcopy(self.store.data)
        try:
            yield InMemoryUnitOfWork(self.store, space_id, kind)
        except BaseException:
            self.store.data = snapshot
            raise
        if self.listeners and space_id is not None:
            change = read_changes(snapshot, self.store.data)
            if change is not None:
                for listener in self.listeners:
                    listener(space_id, change)


def read_changes(before: dict[str, dict[str, Any]], after: dict[str, dict[str, Any]]) -> Change | None:
    """What the `read_invalidations` triggers would have written for this transaction."""
    handles: set[UUID] = set()
    profiles: set[UUID] = set()
    threads: set[str] = set()
    old, new = before.get("identity", {}), after.get("identity", {})
    for handle_id in _changed(old.get("handles", {}), new.get("handles", {}), _HANDLE_FIELDS):
        handles.add(handle_id)
    old_keys, new_keys = old.get("keys", {}), new.get("keys", {})
    for key in old_keys.keys() | new_keys.keys():
        if old_keys.get(key) != new_keys.get(key):
            handles.update(h for h in (old_keys.get(key), new_keys.get(key)) if h is not None)
    old_members, new_members = old.get("membership", {}), new.get("membership", {})
    for handle_id in old_members.keys() | new_members.keys():
        was, now = old_members.get(handle_id), new_members.get(handle_id)
        if (was and was[0]) != (now and now[0]):
            handles.add(handle_id)
            profiles.update(m[0] for m in (was, now) if m is not None)
    # A link made, ended or changed: both of its handles, as the `profile_links` trigger writes them.
    old_links, new_links = old.get("links", {}), new.get("links", {})
    for link_id in old_links.keys() | new_links.keys():
        was, now = old_links.get(link_id), new_links.get(link_id)
        if was != now:
            for link in (was, now):
                if link is not None:
                    handles.update((link.person_handle_id, link.org_handle_id))
    old_profiles, new_profiles = old.get("profiles", {}), new.get("profiles", {})
    for profile_id in old_profiles.keys() | new_profiles.keys():
        if _profile_state(old_profiles.get(profile_id)) != _profile_state(new_profiles.get(profile_id)):
            profiles.add(profile_id)
    old_sessions = before.get("sessions", {}).get("sessions", {})
    new_sessions = after.get("sessions", {}).get("sessions", {})
    for session_id, (_, session) in new_sessions.items():
        prior = old_sessions.get(session_id)
        if prior is not None and _proof_state(prior[1]) != _proof_state(session):
            thread = thread_key(session.conversation_id, session.task_id)
            if thread is not None:
                threads.add(thread)
    if not (handles or profiles or threads):
        return None
    return Change(frozenset(handles), frozenset(profiles), frozenset(threads))


_HANDLE_FIELDS = ("status", "provisional", "subject_kind", "value")


def _changed(before: dict[UUID, Any], after: dict[UUID, Any], fields: tuple[str, ...]) -> list[UUID]:
    # A new handle fires nothing, as in PostgreSQL: nothing kept could name it yet.
    return [
        key
        for key in before
        if key not in after or any(getattr(before[key], f) != getattr(after[key], f) for f in fields)
    ]


def _profile_state(profile: Any) -> tuple[Any, ...] | None:
    if profile is None:
        return None
    record = profile.record
    return (
        profile.retired_at,
        profile.erased_at,
        record.privacy_version,
        record.membership_version,
        record.kind,
        record.org_type,
    )


def _proof_state(session: Any) -> tuple[Any, ...]:
    return (session.proven_level, session.proven_until, session.closed_at)
