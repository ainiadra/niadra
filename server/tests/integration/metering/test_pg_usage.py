"""PgUsageRepository: the hourly read model over the source tables, and usage events (5.15)."""

from datetime import UTC, date, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import insert

from niadra.adapters.memory.fakes import FixedClock
from niadra.adapters.outbound.postgres.schema.operate import (
    ContextUse,
    ExportRun,
    ReviewSample,
    TriggerFiring,
    WebhookDelivery,
)
from niadra.adapters.outbound.postgres.schema.read_path import Receipt
from niadra.adapters.outbound.postgres.schema.write_path import Event, ExtractionRun, Session
from niadra.adapters.outbound.postgres.uow import PgUnitOfWorkFactory
from niadra.app.metering import MeteringService, record_usage_in
from niadra.domain.common.ids import uuid7
from niadra.domain.metering import Granularity, Metric, UsageCount, UsageRow
from niadra.domain.metering.prompt_cache import ModelUsageCounters, ModelUsageKey, ModelUsageRow

H = datetime(2026, 9, 22, 14, 0, tzinfo=UTC)
AGENT = UUID("0192f5a0-0000-7000-8000-00000000c001")


def at(minutes: float) -> datetime:
    return H + timedelta(minutes=minutes)


def mint(when: datetime) -> UUID:
    return uuid7(int(when.timestamp() * 1000))


def event(space: UUID, when: datetime) -> dict[str, Any]:
    return {
        "space_id": space,
        "event_id": mint(when),
        "idempotency_key": str(uuid7()),
        "kind": "message",
        "source_id": AGENT,
        "channel": "whatsapp",
        "speaker": "customer",
        "occurred_at": when,
        "received_at": when,
        "dek_version": 1,
    }


def receipt(
    space: UUID,
    when: datetime,
    kind: str = "read",
    conversation: str | None = "c1",
    source: UUID | None = AGENT,
    group: str | None = None,
) -> dict[str, Any]:
    return {
        "experiment_group": group,
        "space_id": space,
        "receipt_id": mint(when),
        "slice": 0,
        "day": date(2026, 9, 22),
        "seq": 1,
        "producer_id": "read-1",
        "producer_seq": 1,
        "kind": kind,
        "source_id": source,
        "conversation_id": conversation,
        "audience": "customer_agent",
        "surface": "pack",
        "read_at": when,
        "prev_hash": b"",
        "hash": b"",
    }


def session(space: UUID, opened: datetime, closed: datetime | None, conversation: str) -> dict[str, Any]:
    return {
        "space_id": space,
        "session_id": uuid7(),
        "kind": "conversation",
        "conversation_id": conversation,
        "source_id": AGENT,
        "channel": "whatsapp",
        "opened_at": opened,
        "last_event_at": opened,
        "closed_at": closed,
    }


async def seed(uow: PgUnitOfWorkFactory, space: UUID) -> None:
    async with uow(space) as tx:
        db = tx.session
        await db.execute(insert(Event), [event(space, at(1)), event(space, at(59)), event(space, at(61))])
        await db.execute(
            insert(Receipt),
            [
                receipt(space, at(10)),
                receipt(space, at(11), "history"),
                receipt(space, at(12), "holdout", "c2"),
                receipt(space, at(13), conversation="c3", source=None),
            ],
        )
        sessions = [
            session(space, at(5), at(30), "c1"),  # agent read inside: billable
            session(space, at(5), at(30), "c2"),  # control group only: not billable
            session(space, at(5), at(30), "c3"),  # only a person read it in the Console
            session(space, at(5), None, "c1"),  # still open
        ]
        await db.execute(insert(Session), sessions)
        await db.execute(
            insert(ExtractionRun),
            [
                {
                    "space_id": space,
                    "session_id": sessions[0]["session_id"],
                    "versions_key": "v1",
                    "model": "m",
                    "provider": "p",
                    "input_tokens": 1000,
                    "output_tokens": 200,
                    "cost_micros": 700,
                    "status": "ok",
                    "created_at": at(20),
                },
                {
                    "space_id": space,
                    "session_id": uuid7(),
                    "versions_key": "v1",
                    "model": "m",
                    "provider": "p",
                    "input_tokens": 500,
                    "output_tokens": 100,
                    "cost_micros": 300,
                    "status": "ok",
                    "created_at": at(40),
                },
            ],
        )
        await db.execute(
            insert(WebhookDelivery),
            [
                {
                    "space_id": space,
                    "delivery_id": uuid7(),
                    "endpoint_id": "crm",
                    "event_type": "trigger.fired",
                    "body": {},
                    "body_hash": b"h",
                    "status": status,
                    "created_at": at(1),
                    "delivered_at": delivered,
                }
                for status, delivered in (("delivered", at(2)), ("failing", None))
            ],
        )
        await db.execute(
            insert(ExportRun),
            [
                {
                    "space_id": space,
                    "run_id": uuid7(),
                    "schedule_id": "s",
                    "from_seq": 0,
                    "to_seq": 9,
                    "status": "done",
                    "started_at": at(1),
                    "finished_at": at(3),
                },
            ],
        )
        await db.execute(
            insert(TriggerFiring),
            [
                {
                    "space_id": space,
                    "firing_id": uuid7(),
                    "rule_id": "r",
                    "rule_version": "1",
                    "profile_id": uuid7(),
                    "window_key": "w",
                    "idempotency_key": b"k",
                    "evidence": [],
                    "membership_version": 1,
                    "fired_at": at(4),
                },
            ],
        )
        await db.execute(
            insert(ReviewSample),
            [
                {
                    "space_id": space,
                    "sample_id": uuid7(),
                    "session_id": sessions[1]["session_id"],
                    "condition": "base",
                    "created_at": at(6),
                },
            ],
        )
        await db.execute(
            insert(ContextUse),
            [
                {
                    "space_id": space,
                    "conversation_key": "c1",
                    "manifest_hash": "m",
                    "source_id": AGENT,
                    "measure_version": "1",
                    "profile_id": uuid7(),
                    "channel": "whatsapp",
                    "view": "default",
                    "items": [],
                    "repeated": 0,
                    "used": 1,
                    "contradicted": 0,
                    "measured_at": at(50),
                },
            ],
        )


async def test_source_counts_for_one_hour(pg_uow: PgUnitOfWorkFactory) -> None:
    space = uuid7()
    await seed(pg_uow, space)
    async with pg_uow(space) as tx:
        counts = await tx.metering.count_sources(space, H, H + timedelta(hours=1))
    # Per source where the table knows it, directly or through the session; an extraction run whose
    # session was erased and the space's own work (exports, triggers, webhooks) have none.
    assert sorted(counts, key=lambda c: (c.metric, str(c.source_id))) == [
        UsageCount(Metric.CONTEXT_READ.value, 1, 0, AGENT),
        UsageCount(Metric.CONTEXT_READ.value, 1),
        UsageCount(Metric.EVENT_INGESTED.value, 2, 0, AGENT),
        UsageCount(Metric.EXPORT_RUN.value, 1),
        UsageCount(Metric.EXTRACTION_RUN.value, 1, 700, AGENT),
        UsageCount(Metric.EXTRACTION_RUN.value, 1, 300),
        UsageCount(Metric.EXTRACTION_TOKENS.value, 1200, 0, AGENT),
        UsageCount(Metric.EXTRACTION_TOKENS.value, 600),
        UsageCount(Metric.HISTORY_CALL.value, 1, 0, AGENT),
        UsageCount(Metric.MEASURE_RUN.value, 1, 0, AGENT),
        UsageCount(Metric.REVIEW_SAMPLE.value, 1, 0, AGENT),
        UsageCount(Metric.SESSION_BILLABLE.value, 1, 0, AGENT),
        UsageCount(Metric.TRIGGER_FIRED.value, 1),
        UsageCount(Metric.WEBHOOK_DELIVERED.value, 1),
    ]


async def test_a_conversation_counts_once_whoever_reads_and_never_for_the_control_group(
    pg_uow: PgUnitOfWorkFactory,
) -> None:
    # Several agents in one conversation count once, even when only another agent read;
    # A navigation of the control group delivered nothing and is not billed.
    space, other = uuid7(), uuid7()
    async with pg_uow(space) as tx:
        await tx.session.execute(
            insert(Receipt),
            [
                receipt(space, at(10), conversation="both", source=other),
                receipt(space, at(11), conversation="both"),
                receipt(space, at(12), "history", conversation="both", source=other),
                receipt(space, at(13), conversation="another", source=other),
                receipt(space, at(14), "history", conversation="control", group="pilot:control"),
                receipt(space, at(15), "history", conversation="shadow", group="pilot:shadow"),
                receipt(space, at(16), "history", conversation="memory", group="pilot:memory"),
            ],
        )
        names = ("both", "another", "control", "shadow", "memory")
        await tx.session.execute(insert(Session), [session(space, at(5), at(30), c) for c in names])
    async with pg_uow(space) as tx:
        counts = await tx.metering.count_sources(space, H, H + timedelta(hours=1))
    billable = [c for c in counts if c.metric == Metric.SESSION_BILLABLE.value]
    # Billed to the source that opened each session: "both", "another" and "memory".
    assert billable == [UsageCount(Metric.SESSION_BILLABLE.value, 3, 0, AGENT)]


async def test_rollup_writes_usage_hourly_idempotently_with_usage_events(pg_uow: PgUnitOfWorkFactory) -> None:
    space = uuid7()
    await seed(pg_uow, space)
    service = MeteringService(pg_uow, FixedClock(H + timedelta(minutes=70)))
    for recorded in (True, False):
        async with pg_uow(space) as tx:
            assert (
                await record_usage_in(tx, space, Metric.EXPORT_BYTES, 4096, ["run", "facts"], 0, at(3))
                is recorded
            )
    await service.rollup_hour(space, H)
    await service.rollup_hour(space, H)
    rows = await service.usage(space, H, H + timedelta(hours=2))
    by_metric = {r.metric: r for r in rows if r.hour == H}
    assert by_metric[Metric.EXPORT_BYTES.value] == UsageRow(H, Metric.EXPORT_BYTES.value, 4096, 0)
    assert by_metric[Metric.EVENT_INGESTED.value].quantity == 2
    assert len(by_metric) == 12
    daily = await service.usage(space, H, H + timedelta(hours=2), Granularity.DAY)
    assert {r.metric for r in daily} == set(by_metric)
    only = await service.usage(space, H, H + timedelta(hours=2), metrics=[Metric.SESSION_BILLABLE.value])
    assert [r.quantity for r in only] == [1]
    extraction = [Metric.EXTRACTION_RUN.value]
    per_source = await service.usage(space, H, H + timedelta(hours=2), metrics=extraction, per_source=True)
    assert [(r.source_id, r.quantity, r.cost_micros) for r in per_source] == [(None, 1, 300), (AGENT, 1, 700)]
    agent = await service.usage(space, H, H + timedelta(hours=2), metrics=extraction, source_id=AGENT)
    assert [(r.source_id, r.quantity) for r in agent] == [(None, 1)]


async def test_model_usage_adds_per_day_source_provider_and_model(pg_uow: PgUnitOfWorkFactory) -> None:
    # What the agents' model calls reported, summed where it lands.
    space, day = uuid7(), H.date()
    key = ModelUsageKey(day, AGENT, "openai", "gpt-4.1-2025-04-14")
    other = ModelUsageKey(day - timedelta(days=1), AGENT, "anthropic", "claude-haiku-4-5")
    async with pg_uow(space) as uow:
        await uow.metering.add_model_usage(
            [
                ModelUsageRow(key, ModelUsageCounters(1, 3000, 2048)),
                ModelUsageRow(other, ModelUsageCounters(1, 900, 0, 900)),
            ]
        )
    async with pg_uow(space) as uow:
        await uow.metering.add_model_usage([ModelUsageRow(key, ModelUsageCounters(2, 6000, 4096))])
        both = await uow.metering.model_usage(day - timedelta(days=1), day)
        today = await uow.metering.model_usage(day, day, frozenset({AGENT}))
        nobody = await uow.metering.model_usage(day, day, frozenset({uuid7()}))
    assert [r.key for r in both] == [other, key]
    assert today == [ModelUsageRow(key, ModelUsageCounters(3, 9000, 6144, 0))]
    assert nobody == []
