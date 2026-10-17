"""Coordination on the production adapters (24.3.4, 24.6): the check route over a real Redis-compatible
state store, RabbitMQ with publisher confirms and PostgreSQL behind PgBouncer's pool.

The budgets of the small database are asserted here: the check and an effect's reservation run no statement,
and the batch writer stores a batch of 500 records in one statement besides the transaction's pin. So is a
state store that came back empty: it reloads from the durable record and keeps the purposes that fail closed
waiting for 60 seconds.

The latency targets (a check at most 10 ms at p95 on the server, 30 ms with a contact token) go through
`tests.latency.within`: ten times the target in the suite, the target itself as a benchmark
(`NIADRA_BENCH=1`).
"""

import asyncio
import os
import statistics
import time
from collections.abc import AsyncIterator, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest
from redis.asyncio import Redis
from sqlalchemy import event, text
from sqlalchemy.engine import Engine

from niadra.adapters.inbound.http import coordinate as coordinate_routes
from niadra.adapters.inbound.http.app import create_app
from niadra.adapters.memory.fakes import FixedClock, StaticConfig
from niadra.adapters.outbound.keys.local import LocalKeyVault
from niadra.adapters.outbound.keys.postgres import PgWrappedKeyStore
from niadra.adapters.outbound.postgres.engines import make_engine
from niadra.adapters.outbound.postgres.registry import EngineRegistry
from niadra.adapters.outbound.postgres.uow import PgUnitOfWorkFactory
from niadra.adapters.outbound.rabbitmq.connection import COORDINATION_QUEUE, RabbitConnection
from niadra.adapters.outbound.rabbitmq.coordination import RabbitCoordinationFeed, RabbitCoordinationLog
from niadra.adapters.outbound.redis import connect
from niadra.adapters.outbound.redis.coordinate import RedisCoordination
from niadra.adapters.outbound.redis.erasure import RedisErasureMarks
from niadra.adapters.outbound.secrets import PgSecretStore
from niadra.app.coordinate import CoordinateService
from niadra.config.coordination import (
    CommitmentType,
    ContactBudget,
    CoordinationSettings,
    Gateway,
    HandoffSettings,
    OwnershipSettings,
    PaidBudget,
    PurposeRule,
)
from niadra.config.models import FeatureSettings
from niadra.contracts.common import Handle, ObjectRef
from niadra.contracts.context import ContextRequest, ContextResponse
from niadra.contracts.coordinate import CheckRequest, CheckResult, ClaimRequest
from niadra.contracts.turns import TurnAgent, TurnEffect, TurnRecord
from niadra.domain.common.errors import Unauthenticated
from niadra.domain.common.ids import uuid7
from niadra.domain.common.space import SourcePrincipal
from niadra.domain.coordinate.destination import b64url, unb64url
from niadra.domain.coordinate.model import (
    ClaimKind,
    Compatibility,
    EffectSnapshot,
    EffectState,
    FailDirection,
    Settler,
)
from niadra.domain.coordinate.records import (
    ContactRecord,
    EffectRecord,
    OwnershipRecord,
    SpendRecord,
    SuppressionRecord,
)
from niadra.domain.privacy.erasure import ErasureScope, TargetKind
from niadra.domain.vocabulary import Feature
from niadra.ports.turns import AppliedTurn
from niadra.testing.cell import BILLING_SOURCE, WHATSAPP_SOURCE, principal, sample_config
from tests.integration.conftest import PgServer, clone_space
from tests.latency import BENCH, within

REDIS = os.environ.get("NIADRA_TEST_REDIS", "")
RABBITMQ = os.environ.get("NIADRA_TEST_RABBITMQ", "")
STATE_DB = 15
"""A database of its own on the test server: these tests empty it to play a restart."""
KEY = "nia_sk_coordinate"
PHONE = {"type": "phone_e164", "value": "+5511987650000"}
GATEWAY_KEY = bytes(range(32))
QUOTE = ObjectRef(type="quote", namespace="operator", id="q-77")

NOW = datetime(2026, 9, 30, 15, 0, tzinfo=UTC)
"""A Wednesday at noon in Sao Paulo: the stack's clock never depends on when the suite runs."""
PUBLISH_TIMEOUT_S = 5.0
"""How long a check waits for the queue to confirm what it recorded. In production it waits 150 ms and falls
back past it (a check whose reservation the queue did not confirm is undone, which tests with a stub queue
cover); on a loaded CI runner a real broker's confirm sometimes takes longer, which turned an allow into a
`defer` with no token in about one run in three. These tests measure the check, not the runner's broker."""
CHECK_P95_S = 0.010
CONTACT_P95_S = 0.030
WARM_UP = 20
SAMPLES = 2000 if BENCH else 300

pytestmark = pytest.mark.skipif(
    not (REDIS and RABBITMQ), reason="needs NIADRA_TEST_REDIS and NIADRA_TEST_RABBITMQ"
)


class CompiledViews:
    """The context compiler's read, as far as a handoff's package needs it."""

    async def context(self, caller: SourcePrincipal, request: ContextRequest) -> ContextResponse:
        return ContextResponse.model_construct(text="[Cliente] Marina", etag="e1", version="p1")


class FakeEdge:
    def __init__(self, caller: SourcePrincipal) -> None:
        self._caller = caller

    async def authenticate_key(self, token: str) -> SourcePrincipal:
        if token != KEY:
            raise Unauthenticated("unknown key")
        return self._caller

    async def check_rate(self, principal: Any, path: str) -> None:
        return None


@dataclass
class Container:
    edge: FakeEdge
    config: StaticConfig
    coordinate: CoordinateService


@dataclass
class Stack:
    space_id: UUID
    caller: SourcePrincipal
    service: CoordinateService
    client: httpx.AsyncClient
    state: Redis
    clock: FixedClock
    statements: list[str]
    uow: PgUnitOfWorkFactory


@contextmanager
def counting(statements: list[str]) -> Iterator[None]:
    def count(conn: Any, cursor: Any, statement: str, *args: Any) -> None:
        statements.append(" ".join(statement.split()[:3]))

    event.listen(Engine, "before_cursor_execute", count)
    try:
        yield
    finally:
        event.remove(Engine, "before_cursor_execute", count)


@pytest.fixture
async def stack(server: PgServer) -> AsyncIterator[Stack]:
    space_id = uuid4()
    await asyncio.to_thread(clone_space, space_id)
    marketing = PurposeRule(
        name="marketing", fail=FailDirection.CLOSED, token=True, budget=ContactBudget(limit=2, per_hours=168)
    )
    gateway_ref = f"spaces/{space_id}/gateway/wa"
    settings = CoordinationSettings(
        purposes=[p if p.name != "marketing" else marketing for p in CoordinationSettings().purposes],
        gateways=[Gateway(gateway_id="wa_gateway", secret_ref=gateway_ref, channels=frozenset({"whatsapp"}))],
        paid_budgets=[
            PaidBudget(operation="price_lookup", units=10, per_hours=24),
            PaidBudget(operation="bureau_query", units=5, per_hours=24, scope="space"),
        ],
        ownership=OwnershipSettings.model_validate(
            {
                "sources": [
                    {
                        "name": "service_panel",
                        "via": "mapping",
                        "max_validity_s": 3600,
                        "levels": ["human_active"],
                        "source_id": str(BILLING_SOURCE),
                        "rules": [
                            {
                                "canonical_type": "panel.session_opened",
                                "level": "human_active",
                                "holder": "human:panel",
                            }
                        ],
                    },
                    {"name": "middleware", "via": "worker", "max_validity_s": 7200, "levels": ["soft_pause"]},
                ]
            }
        ),
        commitments=[CommitmentType(name="trial", compatibility=Compatibility.FIRST_HOLDS)],
        handoffs=HandoffSettings.model_validate({"outcomes": [{"name": "sold"}]}),
    )
    config = sample_config(space_id).model_copy(
        update={
            "features": FeatureSettings(enabled=frozenset({Feature.COORDINATION})),
            "coordination": settings,
        }
    )
    configs = StaticConfig(config)
    cell_engine = make_engine(server.servers.cell_url("system"), pool_size=1, max_overflow=2, pool_timeout=5)
    registry = EngineRegistry(server.servers, cell_engine)
    await registry.refresh()
    keys = LocalKeyVault(b"k" * 32, PgWrappedKeyStore(cell_engine, "coordination"))
    secrets = PgSecretStore.from_kek(cell_engine, b"k" * 32, "local")
    await secrets.put(gateway_ref, b64url(GATEWAY_KEY))
    uow = PgUnitOfWorkFactory(keys, registry, cell_engine)
    state = connect(f"{REDIS.rsplit('/', 1)[0]}/{STATE_DB}")
    await state.flushdb()
    rabbit = RabbitConnection(RABBITMQ)
    await rabbit.open()
    queue = await rabbit.publisher.get_queue(COORDINATION_QUEUE, ensure=False)
    await queue.purge()
    clock = FixedClock(NOW)
    service = CoordinateService(
        uow,
        RedisCoordination(state),
        RabbitCoordinationLog(rabbit),
        keys,
        clock,
        configs,
        publish_timeout_s=PUBLISH_TIMEOUT_S,
        marks=RedisErasureMarks(state),
        feed=RabbitCoordinationFeed(rabbit, linger_s=0.05),
        secrets=secrets,
        views=CompiledViews(),
    )
    await service.watch_state_store()
    caller = principal(WHATSAPP_SOURCE, config)
    app = create_app(
        "niadra-test", Container(FakeEdge(caller), configs, service), (coordinate_routes.router,)
    )
    client = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://cell",
        headers={"authorization": f"Bearer {KEY}"},
    )
    try:
        yield Stack(space_id, caller, service, client, state, clock, [], uow)
    finally:
        await client.aclose()
        await state.flushdb()
        await state.aclose()
        await rabbit.close()
        await registry.close()
        await cell_engine.dispose()


def body(**fields: Any) -> dict[str, Any]:
    return {
        "agent": "agent:closing",
        "intent": "farewell",
        "direction": "outbound",
        "purpose": "service",
        **fields,
    }


async def test_a_check_and_an_effect_reservation_run_no_statement(stack: Stack) -> None:
    await stack.client.post("/v1/coordination/check", json=body(subject=PHONE))  # warms the process
    with counting(stack.statements):
        check = await stack.client.post(
            "/v1/coordination/check", json=body(subject=PHONE, effect_key="farewell:c1")
        )
        reserve = await stack.client.post(
            "/v1/coordination/effects",
            json={"effect_key": "filing:n1", "kind": "filing"},
            headers={"idempotency-key": "r1"},
        )
        marketing = await stack.client.post(
            "/v1/coordination/check", json=body(subject=PHONE, purpose="marketing", intent="promo")
        )
    assert check.status_code == 200, check.text
    assert (check.json()["decision"], check.json()["effect"]["state"]) == ("allow", "none")
    assert (reserve.status_code, reserve.json()["state"]) == (201, "reserved")
    assert marketing.json()["contact_budget"]["marketing"]["remaining"] == 1
    assert stack.statements == []


async def test_a_hundred_concurrent_checks_through_the_route_allow_exactly_two(stack: Stack) -> None:
    responses = await asyncio.gather(
        *(
            stack.client.post(
                "/v1/coordination/check", json=body(subject=PHONE, purpose="marketing", intent="promo")
            )
            for _ in range(100)
        )
    )
    decisions = [r.json()["decision"] for r in responses]
    assert decisions.count("allow") == 2
    assert decisions.count("deny") == 98


async def _p95(stack: Stack, requests: list[CheckRequest]) -> tuple[float, list[CheckResult]]:
    """The server's p95 in seconds over `requests`, after `WARM_UP` of them warmed the connections."""
    for request in requests[:WARM_UP]:
        await stack.service.check(stack.caller, request)
    took: list[float] = []
    results: list[CheckResult] = []
    for request in requests[WARM_UP:]:
        started = time.perf_counter()
        results.append(await stack.service.check(stack.caller, request))
        took.append(time.perf_counter() - started)
    return statistics.quantiles(took, n=100)[94], results


def _contact(i: int) -> CheckRequest:
    contact = body(purpose="marketing", intent="promo", channel="whatsapp")
    return CheckRequest.model_validate(
        {**contact, "subject": {"type": "phone_e164", "value": f"+55119877{i:05d}"}}
    )


async def test_the_check_answers_within_its_latency_target(stack: Stack) -> None:
    p95, _ = await _p95(stack, [_check(i) for i in range(WARM_UP + SAMPLES)])
    within("coordination check p95", p95, CHECK_P95_S)


async def test_an_effect_with_an_ambiguous_result_is_never_sent_again_through_the_route(stack: Stack) -> None:
    first = (await stack.client.post("/v1/coordination/check", json=body(effect_key="farewell:c9"))).json()
    assert first["effect"] == {"state": "none", "attempt": 1}
    declared = await stack.client.post(
        "/v1/coordination/declare",
        json={
            "kind": "effect",
            "agent": "agent:closing",
            "detail": {"effect_key": "farewell:c9", "state": "unknown_outcome", "attempt": 1},
        },
        headers={"idempotency-key": "d1"},
    )
    assert declared.json() == {"accepted": True, "decision_id": None}
    retries = await asyncio.gather(
        *(stack.client.post("/v1/coordination/check", json=body(effect_key="farewell:c9")) for _ in range(20))
    )
    assert {(r.json()["decision"], tuple(r.json()["reasons"])) for r in retries} == {
        ("deny", ("effect_unknown_outcome",))
    }


async def test_the_writer_stores_a_batch_of_five_hundred_in_one_statement(stack: Stack) -> None:
    now_ms = int(stack.clock.now().timestamp() * 1000)
    records: list[Any] = [
        EffectRecord(stack.space_id, uuid4().bytes + uuid4().bytes, _snapshot(now_ms), WHATSAPP_SOURCE)
        for _ in range(400)
    ]
    records += [
        SuppressionRecord(
            stack.space_id,
            uuid7(),
            "phone",
            uuid4().bytes * 2,
            b"sealed",
            1,
            "marketing",
            None,
            "opt_out",
            WHATSAPP_SOURCE,
            now_ms,
            None,
            None,
        )
        for _ in range(100)
    ]
    with counting(stack.statements):
        await stack.service.write(records)
    assert len(stack.statements) == 2, stack.statements  # the transaction's pin, then the batch
    assert stack.statements[0].startswith("SELECT set_config")
    async with stack.uow(stack.space_id) as uow:
        changes = await uow.coordinate.suppression_changes(None, 500, stack.clock.now())
        assert len(changes) == 100


async def test_the_writer_takes_what_the_checks_published_from_the_queue(stack: Stack) -> None:
    await stack.client.post("/v1/coordination/check", json=body(effect_key="farewell:c5"))
    writer = asyncio.create_task(stack.service.run_writer())
    try:
        found = None
        for _ in range(100):
            async with stack.uow(stack.space_id) as uow:
                effect_id = await stack.service._effect_hash(stack.space_id, "farewell:c5")
                found = await uow.coordinate.effect(unb64url(effect_id))
            if found is not None:
                break
            await asyncio.sleep(0.05)
    finally:
        writer.cancel()
    assert found is not None
    assert (found.state, found.attempt) == (EffectState.RESERVED, 1)


async def test_a_restarted_state_store_rebuilds_and_stays_closed_for_sixty_seconds(stack: Stack) -> None:
    await stack.client.post("/v1/coordination/check", json=body(effect_key="farewell:c7"))
    await stack.client.post(
        "/v1/coordination/declare",
        json={
            "kind": "effect",
            "agent": "agent:closing",
            "detail": {"effect_key": "farewell:c7", "state": "done", "attempt": 1},
        },
        headers={"idempotency-key": "d7"},
    )
    await stack.client.post(
        "/v1/coordination/declare",
        json={
            "kind": "suppression.added",
            "agent": "a",
            "subject": PHONE,
            "detail": {"purpose": "service", "reason": "opt_out"},
        },
        headers={"idempotency-key": "s7"},
    )
    writer = asyncio.create_task(stack.service.run_writer())
    await asyncio.sleep(0.5)
    writer.cancel()
    await stack.state.flushdb()  # the state store restarts empty

    marketing = (await stack.client.post("/v1/coordination/check", json=body(purpose="marketing"))).json()
    assert (marketing["decision"], marketing["reasons"]) == ("defer", ["rebuilding"])
    done = (await stack.client.post("/v1/coordination/check", json=body(effect_key="farewell:c7"))).json()
    assert (done["decision"], done["reasons"]) == ("deny", ["effect_done"])
    suppressed = (await stack.client.post("/v1/coordination/check", json=body(subject=PHONE))).json()
    assert (suppressed["decision"], suppressed["reasons"]) == ("deny", ["suppressed"])

    stack.clock.advance(seconds=60)
    marketing = (await stack.client.post("/v1/coordination/check", json=body(purpose="marketing"))).json()
    assert marketing["decision"] == "allow"
    assert await stack.service.watch_state_store()
    assert await stack.state.get("coord:marker") == b"whole"
    with counting(stack.statements):
        whole = (await stack.client.post("/v1/coordination/check", json=body(subject=PHONE))).json()
    assert whole["reasons"] == ["suppressed"]
    assert stack.statements == []


def _check(i: int) -> CheckRequest:
    return CheckRequest.model_validate(body(subject={"type": "phone_e164", "value": f"+55119876{i:05d}"}))


def _snapshot(now_ms: int) -> EffectSnapshot:
    return EffectSnapshot(
        state=EffectState.DONE,
        attempt=1,
        kind="delivery",
        source=str(WHATSAPP_SOURCE),
        agent="agent:closing",
        reserved_at_ms=now_ms,
        lease_until_ms=now_ms + 60_000,
        settled_at_ms=now_ms + 1000,
        settled_by=Settler.RESERVER,
        done_at_ms=now_ms + 1000,
        cost_units=0,
        fact=None,
        ttl_ms=int(timedelta(days=30).total_seconds() * 1000),
    )


async def test_the_effects_of_a_group_of_fifty_turns_cost_one_statement(stack: Stack) -> None:
    turns = [
        AppliedTurn(
            WHATSAPP_SOURCE,
            stack.clock.now(),
            TurnRecord(
                turn_id=f"t{i}",
                conversation_id=f"c{i}",
                agent=TurnAgent(name="closing"),
                started_at=stack.clock.now(),
                content_mode="hash_only",
                effects=[TurnEffect(key=f"farewell:c{i}", state="done")],
            ),
        )
        for i in range(50)
    ]
    async with stack.uow(stack.space_id) as uow:
        with counting(stack.statements):
            await stack.service.consume(uow, stack.space_id, turns)
    assert len(stack.statements) == 1, stack.statements  # the group's pin is the turns module's
    done = (await stack.client.post("/v1/coordination/check", json=body(effect_key="farewell:c7"))).json()
    assert (done["decision"], done["reasons"]) == ("deny", ["effect_done"])


async def test_a_contact_with_its_token_runs_no_statement_and_answers_within_its_latency_target(
    stack: Stack,
) -> None:
    contact = body(subject=PHONE, purpose="marketing", intent="promo", channel="whatsapp")
    await stack.client.post("/v1/coordination/check", json=contact)  # warms the gateway's key
    with counting(stack.statements):
        answer = (await stack.client.post("/v1/coordination/check", json=contact)).json()
        keys = await stack.client.get(
            "/.well-known/niadra-contact-keys.json", params={"space": stack.space_id}
        )
    assert answer["decision"] == "allow"
    assert answer["contact_token"].startswith("nct1.")
    assert keys.json()["keys"][0]["status"] == "active"
    assert stack.statements == []
    p95, results = await _p95(stack, [_contact(i) for i in range(WARM_UP + SAMPLES)])
    assert all(result.contact_token is not None for result in results)
    within("coordination check with a contact token p95", p95, CONTACT_P95_S)


async def test_the_writer_stores_contacts_and_spends_in_one_statement_counting_each_use_once(
    stack: Stack,
) -> None:
    now_ms = int(stack.clock.now().timestamp() * 1000)
    contacts = [uuid7() for _ in range(150)]
    subject = uuid4().bytes * 2

    def contact(contact_id: UUID, state: str, use: UUID | None = None) -> ContactRecord:
        return ContactRecord(
            stack.space_id, contact_id, subject, "marketing", "whatsapp", WHATSAPP_SOURCE, "agent:sales",
            "wa_gateway", contact_id, state, reserved_at_ms=now_ms if state == "reserved" else None,
            used_at_ms=now_ms if state == "made" else None, use_ids=(use,) if use else (),
        )  # fmt: skip

    records: list[Any] = [contact(c, "reserved") for c in contacts]
    records += [contact(c, "made", uuid7()) for c in contacts[:100]]
    records += [
        SpendRecord(stack.space_id, c, "c:marketing", None, "subject", subject, 1, now_ms, now_ms + 3_600_000)
        for c in contacts[:100]
    ]
    twice = contact(contacts[0], "made", uuid7())
    with counting(stack.statements):
        await stack.service.write(records)
    assert len(stack.statements) == 2, stack.statements  # the transaction's pin, then the batch
    await stack.service.write([twice])
    await stack.service.write([twice])  # a batch the queue delivered again counts nothing twice
    async with stack.uow(stack.space_id) as uow:
        state = await uow.coordinate.target(subject, kind="subject", now=stack.clock.now())
    assert len(state.spends) == 100
    rows = await _contact_rows(stack)
    assert rows[contacts[0]] == ("made", 2)
    assert rows[contacts[1]] == ("made", 1)
    assert rows[contacts[120]] == ("reserved", 0)


async def test_budgets_reload_from_the_durable_record_after_the_state_store_restarts(stack: Stack) -> None:
    contact = body(subject=PHONE, purpose="marketing", intent="promo")
    for _ in range(2):
        assert (await stack.client.post("/v1/coordination/check", json=contact)).json()["decision"] == "allow"
    paid = await stack.service.reserve(stack.space_id, "price_lookup", object=QUOTE, units=6)
    shared = await stack.service.reserve(stack.space_id, "bureau_query", units=5)
    assert (paid.admitted, shared.admitted) == (True, True)
    writer = asyncio.create_task(stack.service.run_writer())
    await asyncio.sleep(0.5)
    writer.cancel()

    await stack.state.flushdb()  # the loops process reloads the whole space
    assert await stack.service.watch_state_store()
    refused = (await stack.client.post("/v1/coordination/check", json=contact)).json()
    assert (refused["decision"], refused["reasons"]) == ("deny", ["budget_exhausted"])
    over = await stack.service.reserve(stack.space_id, "price_lookup", object=QUOTE, units=5)
    assert (over.admitted, over.reason, over.remaining) == (False, "budget_exhausted", 4)
    assert not (await stack.service.reserve(stack.space_id, "bureau_query", units=1)).admitted

    await stack.state.flushdb()  # and before it does, each target is loaded when first touched
    waiting = (await stack.client.post("/v1/coordination/check", json=contact)).json()
    assert waiting["reasons"] == ["rebuilding"]
    stack.clock.advance(seconds=60)
    refused = (await stack.client.post("/v1/coordination/check", json=contact)).json()
    assert (refused["decision"], refused["reasons"]) == ("deny", ["budget_exhausted"])
    assert not (await stack.service.reserve(stack.space_id, "price_lookup", object=QUOTE, units=5)).admitted
    assert not (await stack.service.reserve(stack.space_id, "bureau_query", units=1)).admitted


async def test_a_paid_reservation_and_its_settlement_run_no_statement(stack: Stack) -> None:
    await stack.service.reserve(stack.space_id, "price_lookup", object=QUOTE, units=1)  # warms the process
    with counting(stack.statements):
        held = await stack.service.reserve(stack.space_id, "price_lookup", object=QUOTE)
        await stack.service.settle(stack.space_id, held, outcome="done", units=2)
    assert (held.admitted, held.units) == (True, 1)
    assert stack.statements == []


async def _contact_rows(stack: Stack) -> dict[UUID, tuple[str, int]]:
    async with stack.uow(stack.space_id) as uow:
        session = uow.coordinate._db  # type: ignore[attr-defined]
        rows = await session.execute(text("SELECT contact_id, state, uses FROM contact_log"))
        return {row.contact_id: (row.state, row.uses) for row in rows}


async def _write_published(stack: Stack) -> None:
    writer = asyncio.create_task(stack.service.run_writer())
    await asyncio.sleep(0.5)
    writer.cancel()


async def test_observed_claims_task_locks_and_commitments_reload_from_the_durable_record(
    stack: Stack,
) -> None:
    hearing = ObjectRef(type="hearing", namespace="court", id="4471")
    lock = ClaimRequest(
        object=hearing, kind=ClaimKind.TASK_LOCK, holder="agent:drafts", task="summary", lease_s=900
    )
    await stack.service.claim(stack.caller, lock, "k1")
    paused = ClaimRequest(
        subject=Handle.model_validate(PHONE), kind=ClaimKind.OWNER, holder="middleware:pause",
        level="soft_pause", lease_s=900, source="middleware",
    )  # fmt: skip
    await stack.service.claim(stack.caller, paused, "k2")
    commitment = {
        "kind": "commitment.made",
        "agent": "agent:sales",
        "subject": PHONE,
        "detail": {"commitment_id": "t1", "type": "trial", "terms": {"days": 7}},
    }
    once = {"idempotency-key": "t1"}
    declared = await stack.client.post("/v1/coordination/declare", json=commitment, headers=once)
    assert declared.json()["accepted"] is True
    await _write_published(stack)
    await stack.state.flushdb()  # the state store restarts empty and the loops process reloads it
    assert await stack.service.watch_state_store()
    body_lock = body(object=hearing.model_dump(), agent="human:lawyer", task="summary")
    locked = (await stack.client.post("/v1/coordination/check", json=body_lock)).json()
    assert (locked["decision"], locked["reasons"]) == ("defer", ["lock_held"])
    assert [(lk["task"], lk["holder"]) for lk in locked["locks"]] == [("summary", "agent:drafts")]
    held = (await stack.client.post("/v1/coordination/check", json=body(subject=PHONE))).json()
    assert (held["decision"], held["owner"]["holder"], held["owner"]["via"]) == (
        "defer",
        "middleware:pause",
        "worker",
    )
    assert [c["id"] for c in held["commitments_active"]] == ["t1"]


async def test_a_handoff_is_one_write_to_create_and_its_outcome_closes_it(stack: Stack) -> None:
    create = {"subject": PHONE, "agent": "agent:sales", "target": "queue:closing", "reason": "fechar hoje"}
    await stack.client.post("/v1/handoffs", json=create, headers={"idempotency-key": "h0"})  # warms the vault
    with counting(stack.statements):
        created = await stack.client.post("/v1/handoffs", json=create, headers={"idempotency-key": "h1"})
    assert created.status_code == 201, created.text
    handoff = created.json()
    # The package reads the subject's effects and open promises (a subject with no profile stops at its
    # handle), then one statement writes the handoff with what its package names a person by.
    assert [s for s in stack.statements if not s.startswith("SELECT set_config")] == [
        "SELECT effect_key_hash, state,",
        "SELECT handles.space_id, handles.handle_id,",
        "WITH named_mentions AS",
    ]
    assert handoff["package"]["context"]["text"].endswith("[Cliente] Marina")
    path = f"/v1/handoffs/{handoff['handoff_id']}"
    assert (await stack.client.get(path)).json()["package"] == handoff["package"]
    accepted = await stack.client.post(f"{path}/accept", headers={"idempotency-key": "a1"})
    assert accepted.json()["status"] == "accepted"
    closed = await stack.client.post(
        f"{path}/outcome", json={"outcome": "sold"}, headers={"idempotency-key": "o1"}
    )
    assert (closed.json()["status"], closed.json()["outcome"]) == ("closed", "sold")
    unknown = await stack.client.post(
        f"{path}/outcome", json={"outcome": "lost"}, headers={"idempotency-key": "o2"}
    )
    assert unknown.status_code == 422


async def test_erasure_takes_the_subjects_rows_and_retention_each_class_in_its_time(stack: Stack) -> None:
    now_ms = int(stack.clock.now().timestamp() * 1000)
    subject = uuid4().bytes * 2
    old = now_ms - 100 * 86_400_000

    held = uuid4().bytes * 2  # a subject whose handle is under a legal hold

    def claim(ended_ms: int, name: bytes = subject) -> OwnershipRecord:
        return OwnershipRecord(
            stack.space_id, uuid7(), name, None, "owner", "agent:sales", "agent_active", "declaration",
            WHATSAPP_SOURCE, ended_ms - 60_000, ended_ms, (), 1,
        )  # fmt: skip

    def ended(name: bytes) -> list[Any]:
        return [
            claim(old, name),
            ContactRecord(
                stack.space_id, uuid7(old), name, "marketing", "whatsapp", WHATSAPP_SOURCE, "agent:sales",
                None, None, "reserved", reserved_at_ms=old,
            ),
            SpendRecord(stack.space_id, uuid7(), "c:marketing", None, "subject", name, 1, old, old + 1000),
        ]  # fmt: skip

    await stack.service.write([claim(now_ms + 60_000), *ended(subject), *ended(held)])
    async with stack.uow(stack.space_id) as uow:
        counts = await uow.coordinate.expire(stack.clock.now(), limit=100, held_subjects={held})
    assert (counts["ownership_claims"], counts["contact_log"], counts["budget_spends"]) == (1, 1, 1)
    async with stack.uow(stack.space_id) as uow:
        released = await uow.coordinate.expire(stack.clock.now(), limit=100)
    assert (released["ownership_claims"], released["contact_log"], released["budget_spends"]) == (1, 1, 1)
    scope = ErasureScope(
        uuid4(), TargetKind.HANDLE, (uuid4(),), None, None, stack.clock.now(), subject_hashes=(subject,)
    )
    async with stack.uow(stack.space_id) as uow:
        erased = await uow.coordinate.erase_for(scope, limit=100)
    assert [row.table for row in erased] == ["ownership_claims"]


async def test_records_an_erasure_fenced_off_stay_out_of_the_durable_record_and_its_reload(
    stack: Stack,
) -> None:
    # A claim still in the writer's queue when the subject is erased never lands; one made afterwards does.
    now_ms = int(stack.clock.now().timestamp() * 1000)
    subject = uuid4().bytes * 2

    def claim(at_ms: int) -> OwnershipRecord:
        return OwnershipRecord(
            stack.space_id, uuid7(), subject, None, "owner", "agent:sales", "agent_active", "declaration",
            WHATSAPP_SOURCE, at_ms, at_ms + 600_000, (), 1,
        )  # fmt: skip

    queued = claim(now_ms - 1000)
    scope = ErasureScope(
        uuid4(), TargetKind.HANDLE, (uuid4(),), None, None, stack.clock.now(), subject_hashes=(subject,)
    )
    await stack.service.fence(stack.space_id, scope)
    stack.clock.advance(seconds=2)
    later = claim(now_ms + 2000)
    await stack.service.write([queued, later])
    async with stack.uow(stack.space_id) as uow:
        rows = (await uow.session.execute(text("SELECT claim_id FROM ownership_claims"))).scalars().all()
        state = await uow.coordinate.target(subject, kind="subject", now=stack.clock.now())
    assert rows == [later.claim_id]
    assert [c.claim_id for c in state.claims] == [later.claim_id]

    overview = await stack.service.overview(stack.caller, 7)
    assert [(o.level, o.via, o.claims) for o in overview.ownership] == [("agent_active", "declaration", 1)]
    assert overview.conflicts.ownership_overlaps == 0
    assert [b.purpose for b in overview.budgets] == ["marketing"]
