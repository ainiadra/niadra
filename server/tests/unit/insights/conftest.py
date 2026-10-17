"""An analytical space in memory: four profiles, their episodes, and fakes for the ports of other modules."""

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from uuid import UUID

import pytest

from niadra.adapters.memory.insights import InMemoryInsightsRepository
from niadra.app.insights import InsightsService
from niadra.domain.common.ids import uuid7
from niadra.domain.common.space import PersonPrincipal, SpaceRef
from niadra.domain.insights.model import Entity, EpisodeText, InsightsLimits, ProfileRef, Sealed
from niadra.domain.memory.vectors import ItemVector
from niadra.domain.receipts import ProducerMark, ReceiptDraft
from niadra.domain.records import Episode, Fact, HandleRecord, OpenItem, Trait
from niadra.domain.vocabulary import (
    Environment,
    FactStatus,
    HandleStatus,
    HandleType,
    OpenItemStatus,
    Sensitivity,
    Speaker,
    SubjectKind,
    Verification,
)
from niadra.testing.cell import SPACE_ID, TENANT_ID, WHATSAPP_SOURCE, Cell, PatchedUnitOfWorkFactory

LIMITS = InsightsLimits(min_group_size=2, max_calls_per_day=100, max_profiles_per_day=50)


class RecordingSink:
    def __init__(self, ok: bool = True) -> None:
        self.ok = ok
        self.drafts: list[ReceiptDraft] = []

    async def emit(self, drafts: Sequence[ReceiptDraft], timeout_s: float) -> bool:
        if self.ok:
            self.drafts.extend(drafts)
        return self.ok

    async def mark(self, mark: ProducerMark) -> None:
        return None


@dataclass
class FakeMemory:
    facts: list[Fact] = field(default_factory=list)
    episodes: list[Episode] = field(default_factory=list)
    items: list[OpenItem] = field(default_factory=list)
    vectors: list[ItemVector] = field(default_factory=list)

    async def episode_vectors(self, episode_ids: Sequence[UUID]) -> list[ItemVector]:
        return [v for v in self.vectors if v.item_id in episode_ids]

    async def facts_for(
        self, handle_ids: Sequence[UUID], statuses: Sequence[FactStatus] = (FactStatus.ACTIVE,)
    ) -> list[Fact]:
        return [f for f in self.facts if f.origin_handle_id in handle_ids and f.status in statuses]

    async def episodes_for(
        self, handle_ids: Sequence[UUID], *, since: datetime | None = None, limit: int = 200
    ) -> list[Episode]:
        return [e for e in self.episodes if e.origin_handle_id in handle_ids][:limit]

    async def get_episode(self, episode_id: UUID) -> Episode | None:
        return next((e for e in self.episodes if e.episode_id == episode_id), None)

    async def open_items_for(
        self, handle_ids: Sequence[UUID], statuses: Sequence[OpenItemStatus] = (OpenItemStatus.OPEN,)
    ) -> list[OpenItem]:
        return [i for i in self.items if i.origin_handle_id in handle_ids and i.status in statuses]


@dataclass
class FakeTraits:
    traits: list[Trait] = field(default_factory=list)
    supported_by: dict[UUID, set[UUID]] = field(default_factory=dict)
    """The handles each trait's evidence came through."""

    async def active_for(self, profile_id: UUID) -> list[Trait]:
        return [t for t in self.traits if t.profile_id == profile_id]

    async def traits_with_evidence_from(self, handle_ids: Sequence[UUID]) -> list[Trait]:
        return [t for t in self.traits if self.supported_by.get(t.trait_id, set()) & set(handle_ids)]


@dataclass
class FakeIdentity:
    handles: list[HandleRecord] = field(default_factory=list)

    async def get_handles(self, handle_ids: Sequence[UUID]) -> list[HandleRecord]:
        return [h for h in self.handles if h.handle_id in handle_ids]


@dataclass
class Space:
    cell: Cell
    service: InsightsService
    sink: RecordingSink
    repo: InMemoryInsightsRepository
    memory: FakeMemory
    traits: FakeTraits
    identity: FakeIdentity
    profiles: dict[str, ProfileRef]
    now: datetime

    def window(self) -> tuple[datetime, datetime]:
        return self.now - timedelta(days=30), self.now + timedelta(hours=1)


def person(*roles: str, sources: frozenset[UUID] | None = None) -> PersonPrincipal:
    return PersonPrincipal(
        space=SpaceRef(SPACE_ID, TENANT_ID, "us-east-2", Environment.SANDBOX),
        user_id=UUID("0192f5a0-0000-7000-8000-0000000000aa"),
        roles=frozenset(roles),
        allowed_sources=sources,
    )


async def _sealed(cell: Cell, episode_id: UUID, text: str) -> Sealed:
    ciphertext, dek = await cell.keys.encrypt(
        SPACE_ID, text.encode(), f"episodes:{episode_id}:summary_ct".encode()
    )
    return Sealed(ciphertext, dek, "episodes", str(episode_id), "summary_ct")


# (pseudonym, [(category, normalized, outcome, sentiment, summary)])
PEOPLE = {
    "p_ana": [
        ("invoice", "billing", "unresolved", -0.6, "Disputed a duplicated invoice, call +55 11 91234-5678")
    ],
    "p_bia": [
        ("invoice", "billing", "unresolved", -0.4, "Invoice charged twice and asked for a refund"),
        ("invoice", "billing", "resolved", 0.3, "Refund of the duplicated invoice confirmed"),
    ],
    "p_caio": [
        ("technical_visit", "technical_visit", "resolved", 0.5, "Technician visit scheduled for the router")
    ],
    "p_dora": [
        ("health", "health", "resolved", 0.0, "Talked about a medical condition, write to dora@mail.com")
    ],
}


@pytest.fixture
async def space(cell: Cell) -> Space:
    now = cell.clock.now()
    repo = InMemoryInsightsRepository(cell.uow.store)
    memory, traits, identity = FakeMemory(), FakeTraits(), FakeIdentity()
    profiles: dict[str, ProfileRef] = {}
    for index, (pseudonym, episodes) in enumerate(PEOPLE.items()):
        handle = uuid7()
        ref = ProfileRef(uuid7(), pseudonym, "person", (handle,))
        profiles[pseudonym] = ref
        repo.add_profile(ref, now - timedelta(days=index + 1))
        identity.handles.append(
            HandleRecord(
                handle,
                HandleType.PHONE_E164,
                "",
                SubjectKind.PERSON,
                f"+55119000000{index}",
                HandleStatus.ACTIVE,
                Verification.V1,
                now,
                now,
            )
        )
        for offset, (category, normalized, outcome, sentiment, summary) in enumerate(episodes):
            episode_id = uuid7()
            ended = now - timedelta(days=offset + 1, hours=index)
            text = EpisodeText(
                episode_id,
                pseudonym,
                "whatsapp",
                "complaint",
                category,
                outcome,
                ended,
                await _sealed(cell, episode_id, summary),
                sentiment,
            )
            repo.add_episode(
                text, ref.profile_id, normalized_category=normalized, source_id=str(WHATSAPP_SOURCE)
            )
            memory.episodes.append(
                Episode(
                    episode_id,
                    1,
                    handle,
                    uuid7(),
                    "whatsapp",
                    WHATSAPP_SOURCE,
                    summary,
                    "complaint",
                    category,
                    normalized,
                    outcome,
                    ended - timedelta(minutes=5),
                    ended,
                    sentiment=sentiment,
                )
            )
        repo.add_row(
            Entity.OPEN_ITEMS, ref.profile_id, now - timedelta(days=index * 5), owner="company", status="open"
        )
    ana = profiles["p_ana"]
    memory.facts += [
        Fact(
            uuid7(),
            ana.handle_ids[0],
            "preferred_name",
            "Ana, e-mail ana@mail.com",
            "contact",
            Sensitivity.NORMAL,
            Speaker.CUSTOMER,
            0.9,
            now,
            now,
            FactStatus.ACTIVE,
            now,
            evidence_event_ids=(uuid7(),),
        ),
        Fact(
            uuid7(),
            ana.handle_ids[0],
            "condition",
            "diabetes",
            "health",
            Sensitivity.SENSITIVE,
            Speaker.CUSTOMER,
            0.9,
            now,
            now,
            FactStatus.ACTIVE,
            now,
        ),
    ]
    memory.items.append(
        OpenItem(
            uuid7(),
            ana.handle_ids[0],
            "Refund the duplicated invoice",
            "company",
            OpenItemStatus.OPEN,
            now,
            {},
        )
    )
    traits.traits.append(
        Trait(
            uuid7(),
            ana.profile_id,
            1,
            "recurring_complaint",
            "billing",
            "volatile",
            "1",
            now,
            now,
            now + timedelta(days=30),
            0.8,
        )
    )
    sink = RecordingSink()
    uow = PatchedUnitOfWorkFactory(cell.uow, memory=memory, traits=traits, identity=identity)
    service = InsightsService(
        uow, cell.keys, cell.clock, cell.config, sink, cell.models, cell.cache, LIMITS, "test"
    )
    return Space(cell, service, sink, repo, memory, traits, identity, profiles, now)
