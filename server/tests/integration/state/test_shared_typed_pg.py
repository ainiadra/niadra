"""The typed pass of the shared objects on PostgreSQL and the cache store (8.6): a wave of
pushes over typed shared objects costs no statement to decide, one to consolidate and at most four to plan,
whatever its size; every customer's quote derived from a price that changed expires in one statement, and so
does a derived shared object, inside the pass's write; the sweep fires the shared objects' timers a page in
one statement, apart from the customers' ones; and a timer that fires while a pass plans its object stays
fired in the row, the stamps and the hot copy."""

import asyncio
import os
from collections.abc import AsyncIterator, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest
from redis.asyncio import Redis
from sqlalchemy import event, text
from sqlalchemy.engine import Engine

from niadra.adapters.memory.fakes import FixedClock, InMemoryCache, InMemoryObjectStore, StaticConfig
from niadra.adapters.outbound.keys.local import LocalKeyVault
from niadra.adapters.outbound.keys.postgres import PgWrappedKeyStore
from niadra.adapters.outbound.postgres.engines import make_engine
from niadra.adapters.outbound.postgres.registry import EngineRegistry
from niadra.adapters.outbound.postgres.typed import PgTypedRepository
from niadra.adapters.outbound.postgres.uow import PgUnitOfWorkFactory
from niadra.adapters.outbound.redis import connect
from niadra.adapters.outbound.redis.shared import RedisSharedObjects
from niadra.app.data_issues import DataIssuesService
from niadra.app.objects import ObjectsService
from niadra.app.observations import ObservationsConsumer
from niadra.app.shared_objects import SharedCopy, SharedObjectsService
from niadra.app.type_drift import TypeDriftService
from niadra.config.models import FeatureSettings, SourceConfig
from niadra.config.object_types import ObjectTypeRegistry
from niadra.contracts.state import ObjectPushRequest
from niadra.domain.common.ids import uuid7
from niadra.domain.common.space import SourcePrincipal
from niadra.domain.ingest.events import NewEvent
from niadra.domain.records import BusinessObject, EventRecord, ObjectKey
from niadra.domain.state.shared import SharedKey, shared_object_id
from niadra.domain.tasks import Task, TaskName
from niadra.domain.turns.issues import IssueQuery
from niadra.domain.vocabulary import Audience, EventKind, Feature, Scope, Speaker
from niadra.ports.repos.typed import SharedStamps, SharedWrite
from niadra.testing.cell import BILLING_SOURCE, principal, sample_config
from tests.integration.conftest import PgServer, clone_space
from tests.unit.audit.fakes import RecordingAdminAudit

REDIS = os.environ.get("NIADRA_TEST_REDIS", "")
NOW = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
QUOTES = uuid7()
PLANS = 500
TYPES: list[dict[str, Any]] = [
    {
        "type": "plan",
        "ownership": "shared",
        "mirror_of": {"system": "operator"},
        "working_set": {"enter_on": ["presented", "related"], "leave_after": "30d"},
        "fields": {"price_table": {"type": "money"}},
        "values": {"promo_ends": {"rule": "promo@v1"}},
        "sources": {"operator": {"kind": "push", "precedence": 1}},
        "timers": {"promo_review": {"due": "promo_ends - 1h", "on_fire": ["notify"]}},
    },
    {
        "type": "quote",
        "ownership": "subject",
        "nature": "derived",
        "mirror_of": {"system": "quotes"},
        "inputs": ["plan.price_table"],
        "fields": {"price": {"type": "money"}},
        "sources": {"quotes": {"kind": "push", "precedence": 1}},
        "timers": {"follow_up": {"due": "known_at + 2d", "on_fire": ["notify"]}},
        "time": {"known_at": {"origin": "platform"}},
    },
    {
        "type": "best_plan",
        "ownership": "shared",
        "nature": "derived",
        "mirror_of": {"system": "operator"},
        "working_set": {"enter_on": ["presented"], "leave_after": "30d"},
        "inputs": ["plan.price_table"],
        "fields": {"price": {"type": "money"}},
        "sources": {"operator": {"kind": "push", "precedence": 1}},
    },
]

pytestmark = pytest.mark.skipif(not REDIS, reason="needs NIADRA_TEST_REDIS")


@dataclass
class Stack:
    shared: SharedObjectsService
    objects: ObjectsService
    uow: PgUnitOfWorkFactory
    clock: FixedClock
    space_id: UUID
    pusher: SourcePrincipal
    statements: list[str]
    hot: RedisSharedObjects
    config: StaticConfig

    async def push(self, items: list[dict[str, Any]]) -> None:
        for start in range(0, len(items), 1000):
            page = ObjectPushRequest.model_validate({"objects": items[start : start + 1000]})
            await self.shared.push(self.pusher, page)

    async def quote(self, key: ObjectKey, plan: str) -> list[BusinessObject]:
        at = self.clock.now()
        record = EventRecord(
            event_id=uuid7(),
            kind=EventKind.SYSTEM_EVENT,
            source_id=QUOTES,
            channel="erp",
            speaker=Speaker.SYSTEM,
            occurred_at=at,
            received_at=at,
            handle_ids=(),
            canonical_type="quote.updated",
            fields={"price": 700.0, "input_refs": {"plan": plan}},
            object_refs=(key,),
        )
        async with self.uow(self.space_id) as uow:
            await uow.ingest.insert_events([NewEvent(self.space_id, record, f"key-{record.event_id}")])
        task = Task(TaskName.APPLY_OBJECT_EVENT, self.space_id, {"event_id": str(record.event_id)})
        written = await self.objects.apply_object_event(task)
        await self.shared.enter_related(self.space_id, written)
        return written

    def since(self, mark: int) -> list[str]:
        """The statements after `mark` that do work: not the transaction's own."""
        return [s for s in self.statements[mark:] if not _bookkeeping(s)]


def _bookkeeping(statement: str) -> bool:
    lowered = statement.strip().lower()
    return lowered.startswith(
        ("begin", "commit", "rollback", "select set_config", "set ", "select pg_advisory")
    )


_BOUND = "SELECT input_object_id, input_field FROM object_dependencies WHERE dependent_object_id = :i"


def object_of(space_id: UUID, kind: str, external: str) -> UUID:
    return shared_object_id(space_id, SharedKey(kind, "operator", external))


async def held(stack: Stack, kind: str, external: str) -> SharedCopy:
    [copy], _ = await stack.shared.copies(stack.space_id, [SharedKey(kind, "operator", external)])
    assert copy is not None
    return copy


def plan(n: int, version: int, fields: dict[str, Any], *, at: datetime = NOW) -> dict[str, Any]:
    return {
        "ref": {"type": "plan", "namespace": "operator", "id": f"p-{n}"},
        "version": version,
        "fields": fields,
        "provenance": {"source": "live", "source_observed_at": at.isoformat()},
    }


def best(version: int, price: float, *, at: datetime = NOW, **extra: Any) -> dict[str, Any]:
    return {
        "ref": {"type": "best_plan", "namespace": "operator", "id": "b-0"},
        "version": version,
        "fields": {"price": price},
        "provenance": {"source": "live", "source_observed_at": at.isoformat()},
        **extra,
    }


@contextmanager
def counting(statements: list[str]) -> Iterator[None]:
    def count(conn: Any, cursor: Any, statement: str, *args: Any) -> None:
        statements.append(statement)

    event.listen(Engine, "before_cursor_execute", count)
    try:
        yield
    finally:
        event.remove(Engine, "before_cursor_execute", count)


@pytest.fixture
async def stack(server: PgServer) -> AsyncIterator[Stack]:
    space_id = uuid4()
    await asyncio.to_thread(clone_space, space_id)
    base = sample_config(space_id)
    quotes = SourceConfig(source_id=QUOTES, name="quotes", audience=Audience.INTERNAL_AGENT)
    config = StaticConfig(
        base.model_copy(
            update={
                "sources": [*base.sources, quotes],
                "features": FeatureSettings(enabled=frozenset({Feature.STATE})),
                "object_types": ObjectTypeRegistry.model_validate({"types": TYPES}),
            }
        )
    )
    cell_engine = make_engine(server.servers.cell_url("system"), pool_size=1, max_overflow=2, pool_timeout=5)
    registry = EngineRegistry(server.servers, cell_engine)
    await registry.refresh()
    keys = LocalKeyVault(b"k" * 32, PgWrappedKeyStore(cell_engine, "shared-typed"))
    uow = PgUnitOfWorkFactory(keys, registry, cell_engine)
    clock = FixedClock(NOW)
    redis: Redis = connect(REDIS)
    hot = RedisSharedObjects(redis)
    shared = SharedObjectsService(
        uow,
        hot,
        keys,
        clock,
        config,
        ObservationsConsumer(uow, keys, config),
        InMemoryObjectStore(),
    )
    pusher = principal(BILLING_SOURCE, config.space(space_id), frozenset({Scope.STATE_PUSH}))
    statements: list[str] = []
    with counting(statements):
        try:
            yield Stack(
                shared,
                ObjectsService(uow, clock, config),
                uow,
                clock,
                space_id,
                pusher,
                statements,
                hot,
                config,
            )
        finally:
            await redis.aclose()
            await registry.close()
            await cell_engine.dispose()


async def test_a_wave_of_pushes_over_typed_objects_costs_one_statement_to_write_and_four_to_plan(
    stack: Stack,
) -> None:
    space = stack.space_id
    refs = [f"plan:operator:p-{n}" for n in range(PLANS)]
    await stack.shared.enter_working_set(space, refs, "presented")
    await stack.shared.consolidate(space)  # the first step loads the working set into the hot copy
    ends = NOW + timedelta(minutes=90)
    await stack.push(
        [plan(n, 1, {"price_table": 100.0, "promo_ends": ends.isoformat()}) for n in range(PLANS)]
    )
    await stack.shared.consolidate(space)
    assert await stack.shared.plan_typed(space) == []  # the first prices, before any quote
    quotes = [ObjectKey("quote", "operator", f"q-{n}") for n in range(50)]
    for n, key in enumerate(quotes):
        await stack.quote(key, refs[n])

    # 20 versions of every plan: 10,000 pushes. Each plan's price changes; half move the promotion's end.
    mark = len(stack.statements)
    for version in range(2, 22):
        later = ends + timedelta(minutes=version)
        await stack.push(
            [
                plan(
                    n,
                    version,
                    {"price_table": 100.0 + version} | ({"promo_ends": later.isoformat()} if n % 2 else {}),
                )
                for n in range(PLANS)
            ]
        )
    assert stack.since(mark) == [], "a push is decided on the hot copy alone"
    mark = len(stack.statements)
    assert await stack.shared.consolidate(space) == PLANS
    consolidated = stack.since(mark)
    assert len(consolidated) == 1, consolidated
    mark = len(stack.statements)
    expired = await stack.shared.plan_typed(space)
    planned = stack.since(mark)
    # The rows; their stamps, values and timers; the quotes; the firing task.
    assert len(planned) == 4, planned
    assert len([s for s in planned if "UPDATE business_objects" in s]) == 1
    assert {o.key for o in expired} == set(quotes)
    assert all(o.typed.expired_by == ("plan.price_table",) for o in expired)
    per_push = (len(consolidated) + len(planned)) / (20 * PLANS)
    assert per_push <= 0.001
    async with stack.uow(space) as uow:
        versions = (
            await uow.session.execute(text("SELECT count(*) FROM object_values WHERE version = 2"))
        ).scalar_one()
        armed = (
            await uow.session.execute(text("SELECT count(*) FROM object_timers WHERE state = 'armed'"))
        ).scalar_one()
    assert versions == PLANS // 2  # only the plans whose promotion moved got a new version
    assert armed == PLANS + len(quotes)
    # Nothing typed moved since: the pass costs no statement.
    mark = len(stack.statements)
    assert await stack.shared.plan_typed(space) == []
    assert stack.since(mark) == []


async def test_a_wave_of_pushes_that_drift_opens_its_issue_in_the_consolidators_transaction(
    stack: Stack,
) -> None:
    # Object-type spec 3.2.2 on a shared type: 10,000 pushes that carry a field the plan type does not declare
    # are still decided with no statement; the consolidation that writes them opens the one `drift` issue.
    cache = InMemoryCache()
    issues = DataIssuesService(stack.uow, stack.clock, stack.config, RecordingAdminAudit(), cache)  # type: ignore[arg-type]
    stack.shared.drift = TypeDriftService(stack.uow, stack.clock, stack.config, issues, cache)
    space = stack.space_id
    refs = [f"plan:operator:p-{n}" for n in range(PLANS)]
    await stack.shared.enter_working_set(space, refs, "presented")
    await stack.shared.consolidate(space)
    await stack.push([plan(n, 1, {"price_table": 100.0}) for n in range(PLANS)])  # the space's key, once
    await stack.shared.consolidate(space)
    mark = len(stack.statements)
    for version in range(2, 22):
        await stack.push(
            [plan(n, version, {"price_table": 100.0 + version, "tier": "gold"}) for n in range(PLANS)]
        )
    assert stack.since(mark) == [], "a push is decided on the hot copy alone, drift or not"
    mark = len(stack.statements)
    assert await stack.shared.consolidate(space) == PLANS
    consolidated = stack.since(mark)
    # The objects' write and the issue's.
    assert len(consolidated) == 2, consolidated
    mark = len(stack.statements)
    await stack.shared.plan_typed(space)
    planned = stack.since(mark)
    assert len(planned) <= 4, planned
    assert (len(consolidated) + len(planned)) / (20 * PLANS) <= 0.0006
    async with stack.uow(space) as uow:
        [issue] = await uow.turns.issues(IssueQuery(open=None))
    assert (issue.kind, issue.object_type, issue.field, issue.occurrences) == ("drift", "plan", "tier", 1)
    assert len(issue.refs) == 1  # throttled at the push: the first plan that showed it
    mark = len(stack.statements)
    assert await stack.shared.consolidate(space) == 0
    assert stack.since(mark) == []


async def test_the_sweep_fires_shared_timers_apart_from_the_customers_ones(stack: Stack) -> None:
    space = stack.space_id
    refs = [f"plan:operator:p-{n}" for n in range(3)]
    await stack.shared.enter_working_set(space, refs, "presented")
    await stack.shared.consolidate(space)
    ends = NOW + timedelta(minutes=30)
    await stack.push([plan(n, 1, {"promo_ends": ends.isoformat()}) for n in range(3)])
    await stack.shared.consolidate(space)
    await stack.shared.plan_typed(space)
    await stack.quote(ObjectKey("quote", "operator", "q-1"), refs[0])
    stack.clock.advance(minutes=1)
    # The customers' sweep leaves the shared objects' timers alone, and the shared one fires them in one.
    assert await stack.objects.fire_timers(space) == []
    mark = len(stack.statements)
    assert await stack.shared.fire_timers(space) == 3
    fired = stack.since(mark)
    assert len([s for s in fired if "UPDATE object_timers" in s]) == 1
    assert len([s for s in fired if "UPDATE shared_objects" in s]) == 1
    assert await stack.shared.fire_timers(space) == 0
    async with stack.uow(space) as uow:
        rows = (await uow.session.execute(text("SELECT object_id, state FROM object_timers"))).all()
        [row] = await uow.typed.shared_by_id([shared_object_id(space, SharedKey("plan", "operator", "p-0"))])
    shared_ids = {shared_object_id(space, SharedKey("plan", "operator", f"p-{n}")) for n in range(3)}
    assert {r.state for r in rows if r.object_id in shared_ids} == {"fired"}
    assert {r.state for r in rows if r.object_id not in shared_ids} == {"armed"}
    assert row.meta["$"]["t"]["promo_review"]["st"] == "fired"
    # A plan that leaves the working set takes its armed timers in the same statement.
    await stack.push([plan(0, 2, {"promo_ends": (ends + timedelta(days=1)).isoformat()})])
    await stack.shared.consolidate(space)
    await stack.shared.plan_typed(space)
    stack.clock.advance(days=31)
    await stack.shared.leave(space)
    async with stack.uow(space) as uow:
        left = (
            await uow.session.execute(
                text("SELECT count(*) FROM object_timers WHERE state = 'armed' AND object_id = ANY(:ids)"),
                {"ids": list(shared_ids)},
            )
        ).scalar_one()
    assert left == 0


async def test_a_derived_shared_object_expires_inside_the_passs_write_and_is_current_once_computed_again(
    stack: Stack,
) -> None:
    space = stack.space_id
    best_id, plan_id = object_of(space, "best_plan", "b-0"), object_of(space, "plan", "p-0")
    await stack.shared.enter_working_set(space, ["best_plan:operator:b-0"], "presented")
    await stack.shared.consolidate(space)
    await stack.push([best(1, 90.0, inputs={"plan": "plan:operator:p-0"})])
    await stack.shared.consolidate(space)
    await stack.shared.plan_typed(space)
    async with stack.uow(space) as uow:
        bound = (
            await uow.session.execute(
                text(_BOUND),
                {"i": best_id},
            )
        ).all()
        [row] = await uow.typed.shared_by_id([best_id])
        assert [r.key.external_id for r in await uow.typed.shared_by_id([plan_id])] == ["p-0"]
    assert [tuple(b) for b in bound] == [(plan_id, "plan.price_table")]
    assert row.derived_status == "current"

    stack.clock.advance(minutes=2)
    await stack.push([plan(0, 1, {"price_table": 100.0}, at=stack.clock.now())])
    await stack.shared.consolidate(space)
    mark = len(stack.statements)
    await stack.shared.plan_typed(space)
    planned = stack.since(mark)
    # The rows; their stamps, with the derived shared object's expiry; the customers' dependents.
    assert len(planned) == 3, planned
    async with stack.uow(space) as uow:
        [row] = await uow.typed.shared_by_id([best_id])
    assert row.derived_status == "expired_by_input"
    assert row.meta["$"]["by"] == ["plan.price_table"]
    copy = await held(stack, "best_plan", "b-0")
    assert (copy.derived_status, copy.typed.expired_by) == ("expired_by_input", ("plan.price_table",))

    # Computed before the change: still expired. After it: current, in the row and the hot copy.
    await stack.push([best(2, 91.0, at=NOW + timedelta(minutes=1))])
    await stack.shared.consolidate(space)
    await stack.shared.plan_typed(space)
    assert (await held(stack, "best_plan", "b-0")).derived_status == "expired_by_input"
    await stack.push([best(3, 95.0, at=stack.clock.now() + timedelta(seconds=1))])
    await stack.shared.consolidate(space)
    await stack.shared.plan_typed(space)
    async with stack.uow(space) as uow:
        [row] = await uow.typed.shared_by_id([best_id])
    assert row.derived_status == "current"
    assert "by" not in row.meta["$"]
    assert (await held(stack, "best_plan", "b-0")).derived_status == "current"


async def _due_review(stack: Stack) -> UUID:
    """A plan whose promotion review is armed and already due, and a new price waiting for the typed pass."""
    space = stack.space_id
    await stack.shared.enter_working_set(space, ["plan:operator:p-0"], "presented")
    await stack.shared.consolidate(space)
    ends = NOW + timedelta(minutes=30)
    await stack.push([plan(0, 1, {"price_table": 100.0, "promo_ends": ends.isoformat()})])
    await stack.shared.consolidate(space)
    await stack.shared.plan_typed(space)
    await stack.push([plan(0, 2, {"price_table": 101.0})])
    await stack.shared.consolidate(space)
    return object_of(space, "plan", "p-0")


async def test_a_timer_that_fires_while_a_pass_plans_its_object_stays_fired(
    stack: Stack, monkeypatch: pytest.MonkeyPatch
) -> None:
    space = stack.space_id
    plan_id = await _due_review(stack)
    read_rows = PgTypedRepository.shared_by_id
    fired: list[int] = []

    async def racing(self: PgTypedRepository, object_ids: Any) -> Any:
        rows = await read_rows(self, object_ids)
        if not fired:
            fired.append(-1)
            fired[0] = await stack.shared.fire_timers(space)
        return rows

    monkeypatch.setattr(PgTypedRepository, "shared_by_id", racing)
    await stack.shared.plan_typed(space)
    monkeypatch.undo()
    assert fired == [1]
    async with stack.uow(space) as uow:
        [state] = (
            await uow.session.execute(
                text("SELECT state FROM object_timers WHERE object_id = :o"), {"o": plan_id}
            )
        ).scalars()
        [row] = await uow.typed.shared_by_id([plan_id])
    stamps = row.meta["$"]
    assert state == "fired"
    assert stamps["t"]["promo_review"]["st"] == "fired"
    assert set(stamps["i"]) == {"price_table"}  # the pass's own stamps are kept
    assert (await held(stack, "plan", "p-0")).typed.timers["promo_review"].state == "fired"
    assert await stack.shared.fire_timers(space) == 0


async def test_a_pass_that_waits_on_the_sweep_merges_with_its_firing_and_the_sweep_never_waits(
    stack: Stack,
) -> None:
    space = stack.space_id
    plan_id = await _due_review(stack)
    async with stack.uow(space) as uow:
        [row] = await uow.typed.shared_by_id([plan_id])
    read = dict(row.meta["$"])
    stale = SharedWrite({**read, "i": {"price_table": "0" * 32}})

    async def write() -> list[SharedStamps]:
        async with stack.uow(space) as uow:
            return await uow.typed.write_shared({plan_id: stale}, {}, {}, {}, NOW)

    async with stack.uow(space) as sweep:
        assert len(await sweep.typed.fire_shared(NOW, limit=10)) == 1
        waiting = asyncio.create_task(write())
        await asyncio.sleep(0.5)
        assert not waiting.done(), "the pass waits on the row the sweep holds"
    [stored] = await waiting
    assert stored.stamps["t"]["promo_review"]["st"] == "fired"
    assert stored.stamps["i"] == {"price_table": "0" * 32}
    assert stored.stamps["n"] == read["n"] + 2

    # A pass holding the object: the sweep skips it at once, and takes it once the pass is done.
    await stack.push([plan(0, 3, {"promo_ends": (NOW + timedelta(minutes=40)).isoformat()})])
    await stack.shared.consolidate(space)
    await stack.shared.plan_typed(space)  # the review fires again, naming the first firing
    async with stack.uow(space) as uow:
        [row] = await uow.typed.shared_by_id([plan_id])
    again = SharedWrite(dict(row.meta["$"]))
    async with stack.uow(space) as holding:
        await holding.typed.write_shared({plan_id: again}, {}, {}, {}, NOW)
        async with stack.uow(space) as sweep:
            assert await asyncio.wait_for(sweep.typed.fire_shared(NOW, limit=10), 2) == []
    async with stack.uow(space) as sweep:
        assert len(await sweep.typed.fire_shared(NOW, limit=10)) == 1


async def test_the_hot_copy_keeps_the_newest_stamps_whatever_order_they_are_published_in(
    stack: Stack,
) -> None:
    space = stack.space_id
    await stack.shared.enter_working_set(space, ["plan:operator:p-0"], "presented")
    [digest] = [d for d in [await stack.shared._digest(space, SharedKey("plan", "operator", "p-0"))]]
    await stack.hot.set_typed(space, [(digest, b'{"n": 3}', 3)])
    await stack.hot.set_typed(space, [(digest, b'{"n": 2}', 2)])
    [found] = await stack.hot.load(space, [digest])
    assert found is not None
    assert found[2] == b'{"n": 3}'


async def test_a_derived_shared_object_bound_in_the_pass_its_input_changes_expires_at_once(
    stack: Stack,
) -> None:
    space = stack.space_id
    await stack.shared.enter_working_set(space, ["best_plan:operator:b-0", "plan:operator:p-0"], "presented")
    await stack.shared.consolidate(space)
    await stack.push([plan(0, 1, {"price_table": 100.0})])
    await stack.shared.consolidate(space)
    await stack.shared.plan_typed(space)
    await stack.push(
        [best(1, 90.0, inputs={"plan": "plan:operator:p-0"}), plan(0, 2, {"price_table": 120.0})]
    )
    await stack.shared.consolidate(space)
    mark = len(stack.statements)
    await stack.shared.plan_typed(space)
    assert len([s for s in stack.since(mark) if "UPDATE shared_objects" in s]) == 1
    copy = await held(stack, "best_plan", "b-0")
    assert (copy.derived_status, copy.typed.expired_by) == ("expired_by_input", ("plan.price_table",))
