"""The Console's customer screen (5.22): memory and business objects under the policy of the reader's role,
every item with its provenance, and an `admin` receipt for each read."""

from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import timedelta
from types import SimpleNamespace
from typing import Any
from uuid import UUID

import httpx
import pytest

from niadra.adapters.inbound.http.app import create_app
from niadra.adapters.inbound.http.objects import profiles_router
from niadra.adapters.memory.memory import InMemoryMemoryRepository
from niadra.adapters.memory.objects import InMemoryObjectRepository
from niadra.adapters.memory.traits import InMemoryTraitRepository
from niadra.app.decide import DecideService
from niadra.app.memory import MemoryService
from niadra.app.objects import ObjectsService
from niadra.config.models import PolicyCondition, PolicyRule
from niadra.domain.common.errors import NotFound, Unauthenticated
from niadra.domain.common.ids import uuid7
from niadra.domain.common.space import PersonPrincipal, SourcePrincipal, SpaceRef
from niadra.domain.extract.result import MemoryRef
from niadra.domain.memory.facts import normalize_value
from niadra.domain.records import (
    AgentAction,
    BusinessObject,
    Episode,
    EventRecord,
    Fact,
    ObjectKey,
    OpenItem,
    Trait,
)
from niadra.domain.traits.evidence import EvidenceRef
from niadra.domain.vocabulary import (
    ActionStatus,
    Audience,
    Environment,
    EventKind,
    FactStatus,
    OpenItemStatus,
    Sensitivity,
    Speaker,
)
from niadra.testing.cell import (
    BILLING_SOURCE,
    SPACE_ID,
    TENANT_ID,
    WHATSAPP_SOURCE,
    Cell,
    PatchedUnitOfWorkFactory,
    principal,
    sample_config,
)
from tests.unit.audit.fakes import RecordingAdminAudit
from tests.unit.objects.fakes import T0, FakeIdentity, FakeIngest, handle

SPACE = SpaceRef(SPACE_ID, TENANT_ID, "us-east-2", Environment.SANDBOX)


def person(*roles: str) -> PersonPrincipal:
    return PersonPrincipal(SPACE, uuid7(), frozenset(roles))


def message(source: UUID, minutes: int, *, kind: EventKind = EventKind.MESSAGE) -> EventRecord:
    at = T0 + timedelta(minutes=minutes)
    return EventRecord(
        uuid7(), kind, source, "whatsapp", Speaker.CUSTOMER, at, at, (), text="conteúdo", conversation_id="c1"
    )


def fact(
    origin: UUID,
    predicate: str,
    value: str,
    category: str,
    sensitivity=Sensitivity.NORMAL,
    status=FactStatus.ACTIVE,
) -> Fact:
    return Fact(
        uuid7(), origin, predicate, value, category, sensitivity, Speaker.CUSTOMER, 0.9, T0, T0, status, T0
    )


@dataclass
class World:
    cell: Cell
    identity: FakeIdentity
    ingest: FakeIngest
    profile_id: UUID
    events: dict[str, EventRecord]
    ids: dict[str, UUID]

    @property
    def uow(self) -> PatchedUnitOfWorkFactory:
        return PatchedUnitOfWorkFactory(self.cell.uow, identity=self.identity, ingest=self.ingest)

    def memory(self) -> MemoryService:
        decide = DecideService(self.uow, self.cell.models, self.cell.clock, decider=self.cell.decider)
        return MemoryService(self.uow, self.cell.models, decide, self.cell.clock, self.cell.config)

    def objects(self) -> ObjectsService:
        return ObjectsService(self.uow, self.cell.clock, self.cell.config)


@pytest.fixture
async def world(cell: Cell) -> World:
    config = sample_config()
    masked = PolicyRule(effect="mask", when=PolicyCondition(audience=Audience.ANALYST, category="contact"))
    cell.config.put(
        config.model_copy(
            update={"policy": config.policy.model_copy(update={"rules": [*config.policy.rules, masked]})}
        )
    )
    identity, ingest = FakeIdentity(), FakeIngest()
    marina = handle()
    profile = identity.add_profile(marina)
    h = marina.handle_id
    said, agreed, did = (
        message(WHATSAPP_SOURCE, 1),
        message(WHATSAPP_SOURCE, 2),
        message(BILLING_SOURCE, 3, kind=EventKind.ACTION),
    )
    ingest.add(said, agreed, did)
    store = cell.uow.store
    memory = InMemoryMemoryRepository(store)
    name = fact(h, "preferred_name", "Marina", "contact")
    health = fact(h, "pregnancy", "gestante", "health", Sensitivity.SENSITIVE)
    retired = fact(h, "preferred_name", "Mari", "contact", status=FactStatus.RETRACTED)
    for f in (name, health, retired):
        await memory.insert_fact(f, normalize_value(f.value))
        await memory.add_mentions(MemoryRef(h, f.fact_id), [said.event_id])
    promise = OpenItem(
        uuid7(),
        h,
        "Enviar segunda via",
        "company",
        OpenItemStatus.OPEN,
        T0,
        {"source_id": str(WHATSAPP_SOURCE), "event_ids": [str(agreed.event_id)]},
    )
    await memory.insert_open_item(promise)
    visit = Episode(
        uuid7(),
        1,
        h,
        uuid7(),
        "whatsapp",
        WHATSAPP_SOURCE,
        "Pediu segunda via",
        "support",
        "invoice",
        "billing",
        "resolved",
        T0,
        T0 + timedelta(minutes=9),
        conversation_id="c1",
    )
    await memory.add_episode_version(visit)
    traits = InMemoryTraitRepository(store)
    prefers = Trait(
        uuid7(),
        profile.profile_id,
        1,
        "prefers_whatsapp",
        "true",
        "stable",
        "r1",
        T0,
        T0,
        T0 + timedelta(days=90),
        0.8,
    )
    await traits.save_trait(
        prefers, [EvidenceRef("event", said.event_id, h), EvidenceRef("episode", visit.episode_id, h)]
    )
    objects = InMemoryObjectRepository(store)
    invoice = await objects.add_object(
        BusinessObject(uuid7(), ObjectKey("invoice", "erp", "0823"), {"amount": "40"}, T0, BILLING_SOURCE, h)
    )
    credit = AgentAction(
        uuid7(),
        did.event_id,
        BILLING_SOURCE,
        "credit",
        ActionStatus.CONFIRMED,
        did.occurred_at,
        invoice.object_id,
        result="creditou R$ 40",
    )
    await objects.add_action(credit)
    events = {"said": said, "agreed": agreed, "did": did}
    ids = {
        "name": name.fact_id,
        "health": health.fact_id,
        "promise": promise.item_id,
        "visit": visit.episode_id,
        "prefers": prefers.trait_id,
        "invoice": invoice.object_id,
        "credit": credit.action_id,
    }
    return World(cell, identity, ingest, profile.profile_id, events, ids)


async def test_the_privacy_officer_sees_the_memory_with_its_origins(world: World) -> None:
    memory = await world.memory().profile_memory(person("security"), world.profile_id)
    assert memory.audience is Audience.HUMAN
    facts = {f.predicate: f for f in memory.facts if f.status is FactStatus.ACTIVE}
    name = facts["preferred_name"]
    assert (name.value, name.masked) == ("Marina", False)
    said = world.events["said"]
    assert name.origin is not None
    assert (
        name.origin.event_id,
        name.origin.source_id,
        name.origin.channel,
        name.origin.kind,
        name.origin.at,
    ) == (str(said.event_id), str(WHATSAPP_SOURCE), "whatsapp", EventKind.MESSAGE, said.occurred_at)
    assert "pregnancy" not in facts
    assert memory.withheld == {"policy": 1}
    assert [f.status for f in memory.facts].count(FactStatus.RETRACTED) == 1
    [promise] = memory.open_items
    assert (promise.promise, promise.description) == (True, "Enviar segunda via")
    assert promise.origin is not None
    assert promise.origin.event_id == str(world.events["agreed"].event_id)
    [episode] = memory.timeline
    assert (episode.summary, episode.origin.channel, episode.origin.conversation_id) == (
        "Pediu segunda via",
        "whatsapp",
        "c1",
    )
    [trait] = memory.traits
    assert trait.active
    assert trait.rule_version == "r1"
    kinds = {e.kind: e.origin for e in trait.evidence}
    assert kinds["event"] is not None
    assert kinds["event"].event_id == str(said.event_id)
    assert kinds["episode"] is not None
    assert kinds["episode"].channel == "whatsapp"


async def test_an_analyst_gets_what_the_policy_masks_for_analysts(world: World) -> None:
    memory = await world.memory().profile_memory(person("analysis"), world.profile_id)
    assert memory.audience is Audience.ANALYST
    names = [f for f in memory.facts if f.predicate == "preferred_name"]
    assert names
    assert all(f.masked and f.value is None for f in names)


async def test_business_objects_come_with_their_agent_actions(world: World) -> None:
    view = await world.objects().profile_objects(person("integration"), world.profile_id)
    [invoice] = view.objects
    assert (invoice.role, invoice.ref.id, invoice.state) == ("owner", "0823", {"amount": "40"})
    [credit] = invoice.actions
    assert (credit.operation, credit.status, credit.result) == (
        "credit",
        ActionStatus.CONFIRMED,
        "creditou R$ 40",
    )
    assert (credit.origin.kind, credit.origin.source_id) == (EventKind.ACTION, str(BILLING_SOURCE))
    assert view.actions == []


async def test_a_long_list_says_where_its_next_page_starts(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The Console's lists stopped at 50 objects and 200 actions and episodes in silence. Past
    # a page each list gives the cursor of the next one, and the next page holds the rest.
    import niadra.app.profile_view as view_module

    monkeypatch.setattr(view_module, "OBJECTS_LIMIT", 1)
    monkeypatch.setattr(view_module, "TIMELINE_LIMIT", 1)
    h = world.identity.profiles[world.profile_id].handle_ids[0]
    objects = InMemoryObjectRepository(world.cell.uow.store)
    await objects.add_object(
        BusinessObject(
            uuid7(),
            ObjectKey("invoice", "erp", "0900"),
            {"amount": "70"},
            T0 - timedelta(days=1),
            BILLING_SOURCE,
            h,
        )
    )
    memory = InMemoryMemoryRepository(world.cell.uow.store)
    older = Episode(
        uuid7(),
        1,
        h,
        uuid7(),
        "whatsapp",
        WHATSAPP_SOURCE,
        "Perguntou do boleto",
        "support",
        "invoice",
        "billing",
        "resolved",
        T0 - timedelta(days=2),
        T0 - timedelta(days=2, minutes=-5),
        conversation_id="c0",
    )
    await memory.add_episode_version(older)
    first = await world.objects().profile_objects(person("integration"), world.profile_id)
    assert [o.ref.id for o in first.objects] == ["0823"]
    assert first.objects_next is not None
    rest = await world.objects().profile_objects(
        person("integration"), world.profile_id, objects_cursor=first.objects_next
    )
    assert [o.ref.id for o in rest.objects] == ["0900"]
    assert rest.objects_next is None
    page = await world.memory().profile_memory(person("integration"), world.profile_id)
    assert len(page.timeline) == 1
    assert page.timeline_next is not None
    after = await world.memory().profile_memory(
        person("integration"), world.profile_id, timeline_cursor=page.timeline_next
    )
    assert [e.summary for e in after.timeline] == ["Perguntou do boleto"]
    assert after.timeline_next is None


class FakeEdge:
    def __init__(self, people: dict[str, PersonPrincipal], keys: dict[str, SourcePrincipal]) -> None:
        self.people, self.keys = people, keys

    async def authenticate_person(self, token: str) -> PersonPrincipal:
        if token not in self.people:
            raise Unauthenticated("unknown token")
        return self.people[token]

    async def authenticate_key(self, token: str) -> SourcePrincipal:
        return self.keys[token]


@pytest.fixture
async def client(world: World) -> AsyncIterator[tuple[httpx.AsyncClient, RecordingAdminAudit]]:
    audit = RecordingAdminAudit()
    people = {"security": person("security"), "vendor": person("vendor")}
    container = SimpleNamespace(
        edge=FakeEdge(people, {"nia_sk_bot": principal()}),
        memory=world.memory(),
        objects=world.objects(),
        admin_audit=audit,
    )
    app = create_app("admin", container, [profiles_router])
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://cell") as http:
        yield http, audit


def auth(token: str) -> dict[str, str]:
    return {"authorization": f"Bearer {token}"}


async def test_routes_read_by_profile_id_and_leave_admin_receipts(client: Any, world: World) -> None:
    http, audit = client
    memory = await http.get(f"/v1/profiles/{world.profile_id}/memory", headers=auth("security"))
    assert memory.status_code == 200
    assert memory.json()["facts"]
    objects = await http.get(f"/v1/profiles/{world.profile_id}/objects", headers=auth("security"))
    assert objects.json()["objects"][0]["actions"][0]["id"] == str(world.ids["credit"])
    assert audit.records == [
        {"action": "profile.memory.read", "profile_id": world.profile_id, "target": None},
        {"action": "profile.objects.read", "profile_id": world.profile_id, "target": None},
    ]


async def test_routes_refuse_other_roles_agents_and_unknown_profiles(client: Any, world: World) -> None:
    http, audit = client
    assert (
        await http.get(f"/v1/profiles/{world.profile_id}/memory", headers=auth("vendor"))
    ).status_code == 403
    # An agent key without the admin scope never reads the Console's view.
    assert (
        await http.get(f"/v1/profiles/{world.profile_id}/memory", headers=auth("nia_sk_bot"))
    ).status_code == 403
    assert (await http.get(f"/v1/profiles/{uuid7()}/objects", headers=auth("security"))).status_code == 404
    assert audit.records == []


async def test_a_facts_history_lists_every_value_of_its_slot_under_the_policy(world: World) -> None:
    memory = world.memory()
    history = await memory.fact_history(person("security"), world.profile_id, world.ids["name"])
    assert history.predicate == "preferred_name"
    assert {(v.value, v.status) for v in history.versions} == {
        ("Marina", FactStatus.ACTIVE),
        ("Mari", FactStatus.RETRACTED),
    }
    assert all(v.origin is not None for v in history.versions)
    # A fact the reader may not see has no history for it either: withheld, never shown.
    health = await memory.fact_history(person("security"), world.profile_id, world.ids["health"])
    assert health.versions == []
    assert health.withheld == {"policy": 1}
    with pytest.raises(NotFound):
        await memory.fact_history(person("security"), world.profile_id, uuid7())


async def test_the_fact_history_route_leaves_an_admin_receipt(world: World) -> None:
    actions = RecordingAdminAudit()
    container = SimpleNamespace(
        edge=SimpleNamespace(
            authenticate_person=lambda token: _person(token),
            authenticate_key=lambda token: _refuse(),
        ),
        memory=world.memory(),
        admin_audit=actions,
    )
    app = create_app("admin", container, [profiles_router])
    url = f"/v1/profiles/{world.profile_id}/facts/{world.ids['name']}/history"
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://cell") as http:
        ok = await http.get(url, headers={"Authorization": "Bearer security"})
        refused = await http.get(url, headers={"Authorization": "Bearer integration"})
    assert ok.status_code == 200
    assert len(ok.json()["versions"]) == 2
    assert refused.status_code == 403
    assert [(r["action"], r["target"]) for r in actions.records] == [
        ("profile.fact_history.read", f"fact:{world.ids['name']}")
    ]


async def _person(token: str) -> PersonPrincipal:
    return person(token)


async def _refuse() -> Any:
    raise Unauthenticated("people only")
