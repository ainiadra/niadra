"""The relationship graph on PostgreSQL: the same graph as in memory, and what one costs.

The micro database serves every space, so a graph runs a fixed number of statements whatever the size of the
space and of the graph: the space pin, the center, then per level of depth the handles, the ties, the
objects, the activity and the customers the ties reach (`app/graph.py`).
"""

from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy import event
from sqlalchemy.engine import Engine

from niadra.adapters.memory.fakes import FixedClock, StaticConfig
from niadra.app.graph import GraphService
from niadra.app.identity import IdentityService
from niadra.config.object_types import ObjectTypeRegistry
from niadra.contracts.common import Handle
from niadra.contracts.graph import Graph
from niadra.domain.common.ids import uuid7
from niadra.domain.common.space import PersonPrincipal, SpaceRef
from niadra.domain.coordinate.destination import SUBJECT_HMAC, subject_canonical
from niadra.domain.graph import OBJECTS_PER_CUSTOMER
from niadra.domain.identity.model import SuggestionRecord
from niadra.domain.ingest.events import NewEvent
from niadra.domain.objects.state import SYSTEM_PREFIX
from niadra.domain.records import AgentAction, BusinessObject, EventRecord, ObjectKey, SessionRecord
from niadra.domain.state.observed import Observation
from niadra.domain.vocabulary import (
    ActionStatus,
    AssertionMethod,
    Environment,
    EventKind,
    HandleType,
    Speaker,
    SubjectKind,
    Verification,
)
from niadra.ports.repos.coordinate import HandoffRow
from niadra.testing.cell import (
    BILLING_SOURCE,
    TENANT_ID,
    VOICE_SOURCE,
    WHATSAPP_SOURCE,
    PatchedUnitOfWorkFactory,
    sample_config,
)
from tests.integration.conftest import CellStack, CloningUnitOfWork
from tests.integration.identity.test_pg_identity import NOW, Recorder

T = HandleType
AROUND_STATEMENTS = {1: 12, 2: 17}
"""The space pin and the center, then six per level: handles, ties, objects, what each person said about
them (the instructions are read only where signals are on), activity, the customers
reached; and four for the names of every customer in it: their handles, name facts and slots, own
records."""
OVERVIEW_STATEMENTS = 11
"""The space pin, a walk of the latest active handles per kind (account, partner, person), their customers,
the ties and the activity, and the four of the names."""
ORDER_TYPE = {
    "type": "order",
    "ownership": "subject",
    "mirror_of": {"system": "billing-agent"},
    "sources": {"billing_agent": {"kind": "push", "precedence": 1}},
    "states": ["placed", "shipped", "delivered"],
}


def person(n: int) -> Handle:
    return Handle(type=T.PHONE_E164, value=f"+5511900{n:06d}")


def company(n: int) -> Handle:
    return Handle(type=T.SYSTEM_ID, value=f"EMP-{n:04d}", scope="erp", subject_kind=SubjectKind.ACCOUNT)


@dataclass
class World:
    space_id: UUID
    uow: PatchedUnitOfWorkFactory
    raw: CloningUnitOfWork
    identity: IdentityService
    graphs: GraphService
    keys: Any

    @property
    def reader(self) -> PersonPrincipal:
        space = SpaceRef(self.space_id, TENANT_ID, "us-east-2", Environment.SANDBOX)
        return PersonPrincipal(space, UUID(int=7), frozenset({"security"}))

    async def attach(
        self, *handles: Handle, kind: SubjectKind = SubjectKind.PERSON, minutes: int = 0
    ) -> UUID:
        async with self.uow(self.space_id) as uow:
            result = await self.identity.attach(
                uow,
                list(handles),
                subject_kind=kind,
                method=AssertionMethod.SAME_EVENT,
                origin_event_id=uuid4(),
                level=Verification.V0,
                at=NOW + timedelta(minutes=minutes),
            )
        assert result.profile_id is not None
        return result.profile_id

    async def handle_id(self, h: Handle) -> UUID:
        async with self.raw(self.space_id) as uow:
            record = await uow.identity.find_handle(h.type, h.scope or "", h.value)
            assert record is not None
            return record.handle_id

    async def customer(self, n: int, company_n: int | None = None) -> UUID:
        """A customer with an order, an agent's action on it, the system's event about it and two
        conversations; a member of company `company_n` when given."""
        profile_id = await self.attach(person(n), minutes=n)
        if company_n is not None:
            await self.identity.create_link(self.space_id, person(n), company(company_n), role="compras")
        handle_id = await self.handle_id(person(n))
        at = NOW + timedelta(minutes=n)
        order = BusinessObject(
            uuid7(),
            ObjectKey("order", "erp", f"PED-{n:05d}"),
            {"state": "shipped"},
            at,
            BILLING_SOURCE,
            origin_handle_id=handle_id,
            fields={"state": {f"{SYSTEM_PREFIX}{BILLING_SOURCE}": Observation("shipped", at, at, at)}},
        )
        system_event = EventRecord(
            uuid7(), EventKind.SYSTEM_EVENT, BILLING_SOURCE, "erp", Speaker.SYSTEM, at, at, ()
        )
        async with self.raw(self.space_id) as uow:
            await uow.objects.add_object(order)
            await uow.ingest.insert_events([NewEvent(self.space_id, system_event, f"ev-{n}")])
            await uow.objects.add_object_event(order.object_id, system_event.event_id, at)
            await uow.objects.add_action(
                AgentAction(
                    uuid7(),
                    uuid7(),
                    VOICE_SOURCE,
                    "update_address",
                    ActionStatus.DECLARED,
                    at,
                    order.object_id,
                )
            )
            for k in range(2):
                await uow.sessions.create_session(
                    self.space_id,
                    SessionRecord(
                        uuid7(),
                        "conversation",
                        WHATSAPP_SOURCE,
                        "whatsapp",
                        at,
                        at,
                        f"c{n}-{k}",
                        profile_id=profile_id,
                    ),
                )
        return profile_id


@pytest.fixture
async def world(pg_uow: CloningUnitOfWork, pg_stack: CellStack) -> World:
    space_id = uuid4()
    recorder = Recorder()
    uow = PatchedUnitOfWorkFactory(pg_uow, privacy=recorder, pipeline=recorder)  # type: ignore[arg-type]
    config = sample_config(space_id).model_copy(
        update={"object_types": ObjectTypeRegistry.model_validate({"types": [ORDER_TYPE]})}
    )
    configs = StaticConfig(config)
    return World(
        space_id,
        uow,
        pg_uow,
        IdentityService(uow, FixedClock(NOW), configs),
        GraphService(pg_uow, pg_stack.vault, FixedClock(NOW), configs),  # type: ignore[arg-type]
        pg_stack.vault,
    )


@contextmanager
def statements() -> Iterator[list[str]]:
    seen: list[str] = []

    def note(conn: Any, cursor: Any, statement: str, *args: Any) -> None:
        seen.append(" ".join(statement.split())[:80])

    event.listen(Engine, "before_cursor_execute", note)
    try:
        yield seen
    finally:
        event.remove(Engine, "before_cursor_execute", note)


def edges(graph: Graph, kind: str) -> list[tuple[str, str, dict[str, Any]]]:
    refs = {n.id: n.ref or n.label for n in graph.nodes}
    return [(refs[e.from_], refs[e.to], e.meta) for e in graph.edges if e.kind == kind]


async def scenario(world: World) -> tuple[UUID, UUID, UUID, UUID]:
    """A customer of a company, a colleague, a suggested twin and a handoff."""
    await world.attach(company(1), kind=SubjectKind.ACCOUNT)
    marina = await world.customer(1, company_n=1)
    colleague = await world.customer(2, company_n=1)
    twin = await world.attach(person(3))
    marina_phone, twin_phone = await world.handle_id(person(1)), await world.handle_id(person(3))
    canonical, _ = subject_canonical(T.PHONE_E164, person(1).value)
    digest = await world.keys.hmac(world.space_id, SUBJECT_HMAC, canonical.encode())
    async with world.raw(world.space_id) as uow:
        await uow.identity.add_suggestion(SuggestionRecord(uuid7(), marina_phone, twin_phone, 0.8, NOW))
        handoff_id = uuid7()
        await uow.coordinate.add_handoff(
            HandoffRow(
                handoff_id, digest, None, WHATSAPP_SOURCE, "whatsapp-bot", "queue:fechamento", b"p", b"h", 1,
                "created", None, NOW, None,
            )
        )  # fmt: skip
        await uow.coordinate.move_handoff(
            handoff_id, status="closed", from_statuses=["created"], at=NOW, outcome="venda_fechada"
        )
    return marina, colleague, twin, await world.handle_id(company(1))


async def test_the_graph_reads_the_same_relations_as_in_memory(world: World) -> None:
    marina, colleague, twin, _ = await scenario(world)

    near = await world.graphs.around(world.reader, marina)
    far = await world.graphs.around(world.reader, marina, depth=2)
    overview = await world.graphs.overview(world.reader)

    company_id = next(n.ref for n in near.nodes if n.kind == "account")
    assert [(a, b, m["role"]) for a, b, m in edges(near, "member_of")] == [
        (str(marina), company_id, "compras")
    ]
    [(agent, order, acted)] = edges(near, "acted_on")
    assert (agent, acted["count"], acted["operations"]) == (str(VOICE_SOURCE), 1, ["update_address"])
    assert [(s, m["count"]) for s, _, m in edges(near, "reported")] == [(str(BILLING_SOURCE), 1)]
    assert [(s, m["count"], m["channels"]) for s, _, m in edges(near, "talked_with")] == [
        (str(WHATSAPP_SOURCE), 2, ["whatsapp"])
    ]
    assert [(s, q, m["outcome"]) for s, q, m in edges(near, "handed_off")] == [
        (str(WHATSAPP_SOURCE), "fechamento", "venda_fechada")
    ]
    assert [{a, b} for a, b, _ in edges(near, "suggested_same")] == [{str(marina), str(twin)}]
    [order_node] = [n for n in near.nodes if n.kind == "object"]
    assert (order_node.ref, order_node.meta["state"]) == (order, "shipped")
    assert str(colleague) in {n.ref for n in far.nodes}
    assert {(a, b) for a, b, _ in edges(overview, "member_of")} >= {(str(marina), company_id)}
    for graph in (near, far, overview):
        body = graph.model_dump_json()
        assert "900000001" not in body
        assert "EMP-0001" not in body


async def test_a_graph_costs_the_same_statements_whatever_the_size_of_the_space(world: World) -> None:
    marina, *_ = await scenario(world)
    await world.graphs.around(world.reader, marina)  # warms the key vault: the space's keys are cached after

    small = await costs(world, marina)
    for n in range(10, 70):
        await world.customer(n, company_n=1 + n % 2)
    large = await costs(world, marina)

    assert (
        small
        == large
        == {"1": AROUND_STATEMENTS[1], "2": AROUND_STATEMENTS[2], "overview": OVERVIEW_STATEMENTS}
    )


async def costs(world: World, center: UUID) -> dict[str, int]:
    found: dict[str, int] = {}
    for depth in (1, 2):
        with statements() as seen:
            await world.graphs.around(world.reader, center, depth=depth)
        found[str(depth)] = len(_space(seen))
    with statements() as seen:
        await world.graphs.overview(world.reader)
    found["overview"] = len(_space(seen))
    return found


def _space(seen: Sequence[str]) -> list[str]:
    """The statements on the space's database: transaction control and the cell's key reads are not."""
    return [s for s in seen if not s.startswith(("BEGIN", "COMMIT", "ROLLBACK")) and "data_keys" not in s]


@pytest.mark.parametrize("depth", [1, 2])
async def test_a_customer_with_more_objects_than_the_limit_keeps_who_they_are(
    world: World, depth: int
) -> None:
    marina, colleague, twin, _ = await scenario(world)
    handle_id = await world.handle_id(person(1))
    async with world.raw(world.space_id) as uow:
        for n in range(300):
            at = NOW + timedelta(days=1, minutes=n)
            busy = await uow.objects.add_object(
                BusinessObject(uuid7(), ObjectKey("order", "erp", f"BUSY-{n:05d}"), {}, at, BILLING_SOURCE,
                               origin_handle_id=handle_id)
            )  # fmt: skip
            # A system event's handles take part in its object: the owner is a participant too.
            await uow.objects.add_participants(busy.object_id, [(handle_id, "customer")])

    with statements() as seen:
        graph = await world.graphs.around(world.reader, marina, depth=depth)

    refs = {n.ref: n for n in graph.nodes}
    assert refs[str(marina)].meta["objects_total"] == 301
    assert graph.truncated
    assert len([n for n in graph.nodes if n.kind == "handle" and n.meta["hop"] == 1]) == 1
    assert len([e for e in graph.edges if e.kind == "identifies"]) >= 1
    assert str(twin) in refs
    assert len([o for a, o, _ in edges(graph, "owns") if a == str(marina)]) == OBJECTS_PER_CUSTOMER
    assert "order BUSY-00299" in {n.label for n in graph.nodes}
    if depth == 2:
        assert str(colleague) in refs
    assert len(_space(seen)) == AROUND_STATEMENTS[depth]
