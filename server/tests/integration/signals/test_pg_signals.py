"""Subject signals on PostgreSQL (24.3.2, 24.6): the pending interactions, the derivation's one read and one
write per pass, the objection marker that stops it, erasure, and the statement budgets of `apply_turns`'
consumer and of the derivation."""

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import event
from sqlalchemy.engine import Engine

from niadra.adapters.memory.fakes import (
    DictSecrets,
    FixedClock,
    InMemoryCache,
    InMemoryHotLayer,
    InMemoryObjectStore,
    StaticConfig,
)
from niadra.adapters.memory.signals import InMemorySignalOverlay
from niadra.app.identity import IdentityService
from niadra.app.ingest import IngestService
from niadra.app.sessions import SessionService
from niadra.app.signals import SignalsService
from niadra.config.models import FeatureSettings
from niadra.contracts.ingest import validate_items
from niadra.contracts.turns import TurnRecord
from niadra.domain.common.ids import uuid7
from niadra.domain.ingest.events import ConversationTouch
from niadra.domain.privacy.erasure import ErasureScope, TargetKind
from niadra.domain.signals.daily import Prior
from niadra.domain.signals.records import SELF, PendingRow, Segment, SignalRow, SignalWrite
from niadra.domain.vocabulary import Feature
from niadra.testing.cell import WHATSAPP_SOURCE, principal, sample_config
from tests.integration.conftest import CloningUnitOfWork

NOW = datetime(2026, 9, 29, 12, 0, tzinfo=UTC)
NO_BLACK = {
    "kind": "preference",
    "attr": "item_variant.color",
    "op": "not_in",
    "values": ["preto"],
    "strength": "must",
    "scope": "persistent",
}


@dataclass(frozen=True)
class Applied:
    source_id: UUID
    received_at: datetime
    record: TurnRecord


@contextmanager
def counting() -> Iterator[list[str]]:
    """Every statement any engine of the process runs, the space pins apart."""
    statements: list[str] = []

    def count(conn: Any, cursor: Any, statement: str, *args: Any) -> None:
        if "set_config('niadra.space_id'" not in statement:
            statements.append(" ".join(statement.split()[:3]))

    event.listen(Engine, "before_cursor_execute", count)
    try:
        yield statements
    finally:
        event.remove(Engine, "before_cursor_execute", count)


def pending(key: str, handle: UUID | None, at: datetime = NOW, session: str | None = "s1") -> PendingRow:
    return PendingRow(key, handle, at, b"sealed", 1, session)


def service(pg_uow: CloningUnitOfWork, space: UUID, clock: FixedClock) -> SignalsService:
    config = sample_config(space).model_copy(
        update={"features": FeatureSettings(enabled=frozenset({Feature.SIGNALS}))}
    )
    return SignalsService(
        pg_uow,  # type: ignore[arg-type]
        pg_uow._keys,
        InMemoryCache(),
        InMemorySignalOverlay(),
        InMemoryObjectStore(),
        clock,
        StaticConfig(config),
    )


async def conversation(pg_uow: CloningUnitOfWork, space: UUID, name: str) -> UUID:
    handle = uuid7()
    async with pg_uow(space) as uow:
        await uow.ingest.upsert_conversation(
            space, ConversationTouch(name, "whatsapp", NOW, NOW, primary_handle=handle)
        )
    return handle


def turn(turn_id: str, conversation_id: str, *interactions: dict[str, Any]) -> Applied:
    record = TurnRecord.model_validate(
        {
            "turn_id": turn_id,
            "conversation_id": conversation_id,
            "agent": {"name": "stylist"},
            "started_at": NOW.isoformat(),
            "content_mode": "hash_only",
            "interactions": list(interactions),
        }
    )
    return Applied(uuid4(), NOW, record)


async def test_pending_rows_are_added_once_and_come_due_by_window_or_quiet(pg_uow: CloningUnitOfWork) -> None:
    space, a, b = uuid4(), uuid4(), uuid4()
    async with pg_uow(space) as uow:
        await uow.signals.add_pending(
            [pending("t:1:0", a, NOW), pending("t:2:0", b, NOW + timedelta(minutes=4, seconds=30))]
        )
        await uow.signals.add_pending([pending("t:1:0", a, NOW)])
        await uow.signals.add_pending([pending("t:3:0", None, NOW)])
    async with pg_uow(space) as uow:
        early = await uow.signals.due_snapshots(
            NOW + timedelta(seconds=30), window=timedelta(minutes=5), quiet=timedelta(minutes=1), limit=10
        )
        late = await uow.signals.due_snapshots(
            NOW + timedelta(minutes=5), window=timedelta(minutes=5), quiet=timedelta(minutes=1), limit=10
        )
    assert (early.snapshots, early.unresolved) == ({}, False)
    assert set(late.snapshots) == {a}
    assert late.unresolved
    assert [p.entry_key for p in late.snapshots[a].pending] == ["t:1:0"]


async def test_an_interaction_without_a_subject_is_kept_and_looked_at_again_later(
    pg_uow: CloningUnitOfWork,
) -> None:
    space = uuid4()
    window, quiet = timedelta(minutes=5), timedelta(minutes=1)
    async with pg_uow(space) as uow:
        await uow.signals.add_pending([pending("t:1:0", None, NOW, "s1"), pending("t:2:0", None, NOW, "s2")])
    first = NOW + timedelta(hours=2)
    async with pg_uow(space) as uow:
        assert [p.entry_key for p in await uow.signals.unresolved(first, 10)] == ["t:1:0", "t:2:0"]
        await uow.signals.checked(["t:1:0", "t:2:0"], first)
    # Checked after waiting two hours, it waits two more before the next look; it never goes on its own.
    soon, later = first + timedelta(hours=1), first + timedelta(hours=2)
    async with pg_uow(space) as uow:
        assert await uow.signals.unresolved(soon, 10) == []
        assert not (await uow.signals.due_snapshots(soon, window=window, quiet=quiet, limit=10)).unresolved
        again = await uow.signals.unresolved(later, 10)
        assert (await uow.signals.due_snapshots(later, window=window, quiet=quiet, limit=10)).unresolved
    assert [(p.entry_key, p.checked_at) for p in again] == [("t:1:0", first), ("t:2:0", first)]
    # An erasure reaches them by their conversation's digest.
    async with pg_uow(space) as uow:
        erased = await uow.signals.drop_unattributed(["s1"])
        left = await uow.signals.unresolved(later, 10)
    assert [r.key for r in erased] == [{"entry_key": "t:1:0"}]
    assert [p.entry_key for p in left] == ["t:2:0"]


async def test_one_write_stores_every_subject_but_one_who_objected(pg_uow: CloningUnitOfWork) -> None:
    space, a, b = uuid4(), uuid4(), uuid4()
    marker = SignalRow(b, SELF, b"marker", 1, 1, 6, NOW, NOW, NOW)
    async with pg_uow(space) as uow:
        await uow.signals.add_pending([pending("t:1:0", a), pending("t:2:0", b)])
        await uow.signals.withdraw([b], [marker])
        await uow.signals.add_pending([pending("t:2:1", b)])
    body = SignalRow(a, SELF, b"body-a", 1, 1, 6, NOW, NOW)
    other = SignalRow(b, SELF, b"body-b", 1, 1, 6, NOW, NOW)
    with counting() as statements:
        async with pg_uow(space) as uow:
            stored = await uow.signals.store_many(
                [
                    SignalWrite(a, NOW, (body,), (), ("t:1:0",)),
                    SignalWrite(b, NOW, (other,), (), ("t:2:1",)),
                ]
            )
    assert stored == {a}
    assert len(statements) == 1, statements
    async with pg_uow(space) as uow:
        rows = await uow.signals.rows_for([a, b])
        left = await uow.signals.due_snapshots(
            NOW + timedelta(hours=1), window=timedelta(minutes=5), quiet=timedelta(minutes=1), limit=10
        )
    assert {(r.handle_id, r.body_ct, r.opted_out_at is not None) for r in rows} == {
        (a, b"body-a", False),
        (b, b"marker", True),
    }
    assert left.snapshots == {}


async def test_erasure_takes_a_handles_rows_and_a_conversations_pending_ones(
    pg_uow: CloningUnitOfWork,
) -> None:
    space, a = uuid4(), uuid4()
    async with pg_uow(space) as uow:
        await uow.signals.add_pending([pending("t:1:0", a)])
        await uow.signals.store(SignalWrite(a, NOW, (SignalRow(a, SELF, b"x", 1, 1, 1, NOW, NOW),), (), ()))
    async with pg_uow(space) as uow:
        erased = await uow.signals.erase_for(
            ErasureScope(uuid4(), TargetKind.HANDLE, (a,), None, None, NOW), limit=100
        )
    assert sorted(r.table for r in erased) == ["signal_intake", "subject_signals"]
    async with pg_uow(space) as uow:
        assert await uow.signals.rows_for([a]) == []


async def test_thirteen_months_inactive_rows_decay_once_and_a_row_derived_again_meanwhile_stays(
    pg_uow: CloningUnitOfWork,
) -> None:
    space, a, b = uuid4(), uuid4(), uuid4()
    old = NOW - timedelta(days=400)
    before = NOW - timedelta(days=395)
    async with pg_uow(space) as uow:
        await uow.signals.store(SignalWrite(a, old, (SignalRow(a, SELF, b"x", 1, 1, 1, old, old),), (), ()))
        await uow.signals.withdraw([b], [SignalRow(b, SELF, b"m", 1, 1, 1, old, old, old)])
    async with pg_uow(space) as uow:
        [found] = await uow.signals.inactive(before, limit=100)
    assert found.handle_id == a  # never the opted-out marker
    async with pg_uow(space) as uow:
        assert await uow.signals.decay([replace(found, body_ct=b"told")], NOW) == 1
    async with pg_uow(space) as uow:
        assert await uow.signals.inactive(before, limit=100) == []  # decayed since its last activity
        [kept] = await uow.signals.rows_for([a])
    assert kept.body_ct == b"told"
    # Derived again after the read: the stale rewrite and the stale drop leave it as it is.
    later = NOW - timedelta(days=399)
    async with pg_uow(space) as uow:
        await uow.signals.store(
            SignalWrite(a, later, (SignalRow(a, SELF, b"new", 1, 1, 3, later, old),), (), ())
        )
    async with pg_uow(space) as uow:
        assert await uow.signals.decay([replace(found, body_ct=b"stale")], NOW) == 0
        assert await uow.signals.drop_untold([found]) == []
        [row] = await uow.signals.rows_for([a])
    assert row.body_ct == b"new"
    async with pg_uow(space) as uow:
        gone = await uow.signals.drop_untold([row])
        assert await uow.signals.rows_for([a]) == []
    assert [r.key["origin_handle_id"] for r in gone] == [str(a)]


async def test_consuming_a_group_of_turns_runs_two_statements(pg_uow: CloningUnitOfWork) -> None:
    space = uuid4()
    await conversation(pg_uow, space, "wa-1")
    await conversation(pg_uow, space, "wa-2")
    signals = service(pg_uow, space, FixedClock(NOW))
    turns = [turn(f"t{i}", f"wa-{1 + i % 2}", NO_BLACK) for i in range(50)]
    await pg_uow._keys.encrypt(space, b"warm", b"warm")  # the space's data key, loaded once per process
    with counting() as statements:
        async with pg_uow(space) as uow:
            await signals.consume(uow, space, turns)
    # The conversations' subjects, and the pending interactions.
    assert len(statements) == 2, statements


async def test_derive_signals_reads_once_and_writes_once_however_many_subjects(
    pg_uow: CloningUnitOfWork,
) -> None:
    space = uuid4()
    for name in ("wa-1", "wa-2", "wa-3"):
        await conversation(pg_uow, space, name)
    clock = FixedClock(NOW)
    signals = service(pg_uow, space, clock)
    async with pg_uow(space) as uow:
        await signals.consume(uow, space, [turn(f"t{n}", f"wa-{n}", NO_BLACK) for n in (1, 2, 3)])
    clock.advance(minutes=6)
    await pg_uow._keys.encrypt(space, b"warm", b"warm")
    with counting() as statements:
        assert await signals.derive_due(space) == 3
    # One read and one write for the three subjects: under two statements per subject per window (24.6).
    assert len(statements) == 2, statements
    with counting() as statements:
        assert await signals.derive_due(space) == 0
    assert len(statements) == 1, statements


async def test_a_standalone_interaction_batch_is_one_statement(pg_uow: CloningUnitOfWork) -> None:
    space = uuid4()
    clock = FixedClock(NOW)
    config = StaticConfig(
        sample_config(space).model_copy(
            update={"features": FeatureSettings(enabled=frozenset({Feature.SIGNALS}))}
        )
    )
    identity = IdentityService(pg_uow, clock, config)  # type: ignore[arg-type]
    objects = InMemoryObjectStore()
    ingest = IngestService(
        pg_uow,  # type: ignore[arg-type]
        identity,
        SessionService(pg_uow, clock, config),  # type: ignore[arg-type]
        pg_uow._keys,
        InMemoryHotLayer(),
        clock,
        config,
        objects,
        DictSecrets(),
    )
    items = [
        {
            "type": "interaction",
            "idempotency_key": f"i-{n}",
            "handle": {"type": "phone_e164", "value": "+5511900009876"},
            "occurred_at": NOW.isoformat(),
            "interaction": NO_BLACK,
        }
        for n in range(20)
    ]
    valid, errors = validate_items(items)
    # A process's first use of a space finds its database and checks its stamp, once.
    async with pg_uow(space):
        pass
    await pg_uow._keys.encrypt(space, b"warm", b"warm")
    with counting() as statements:
        response = await ingest.ingest_items(principal(WHATSAPP_SOURCE, config.space(space)), valid, errors)
    assert (response.accepted, response.errors) == (20, [])
    # The outbox row that hands the batch to its worker; the payload itself is in object storage.
    assert statements == ["INSERT INTO outbox"], statements
    assert len(objects.objects) == 1


async def test_segments_priors_and_the_rebuilds_pages(pg_uow: CloningUnitOfWork) -> None:
    space = uuid4()
    day = datetime(2026, 9, 28, tzinfo=UTC)
    segment = Segment(uuid7(), "daily", day, "measure/daily/x/2026-09-28.parquet", 3, 100, b"\x01" * 32,
                      day + timedelta(days=395))  # fmt: skip
    async with pg_uow(space) as uow:
        await uow.signals.put_segment(segment, NOW)
        # A rerun of the same day replaces its row.
        await uow.signals.put_segment(replace(segment, segment_id=uuid7(), rows=4), NOW)
        [found] = await uow.signals.segments("daily", day)
        assert (found.segment_id, found.rows) == (segment.segment_id, 4)
        await uow.signals.rewrite_segment(replace(found, rows=0, size_bytes=10), NOW)
        assert (await uow.signals.segments("daily", day))[0].rows == 0
        assert await uow.signals.expire_segments(day + timedelta(days=394)) == []
        assert await uow.signals.expire_segments(day + timedelta(days=395)) == [segment.object_key]

        first = [
            Prior("item_variant.color", "bege", 0.25, 20, 5.0, 10),
            Prior("item_variant.color", "azul", 1.0, 9, 9.0, 10),
        ]
        await uow.signals.replace_priors(first, NOW)
        await uow.signals.replace_priors(first[:1], NOW)
        assert [(p.value, p.prior) for p in await uow.signals.priors()] == [("bege", 0.25)]
        # A source's own segment beside the space's; a segment no longer computed leaves.
        store = Prior("item_variant.color", "bege", 0.5, 12, 6.0, 10, segment=str(uuid4()))
        await uow.signals.replace_priors([first[0], store], NOW)
        assert {(p.segment, p.prior) for p in await uow.signals.priors()} == {
            ("all", 0.25),
            (store.segment, 0.5),
        }
        await uow.signals.replace_priors(first[:1], NOW)
        assert [p.segment for p in await uow.signals.priors()] == ["all"]

        handle = uuid4()
        await uow.signals.add_pending(
            [pending("t:1:0", handle), pending("t:1:1", None), pending("t:2:0", handle)]
        )
        page = await uow.signals.pending_page(None, 1)
        assert [r.entry_key for r in page] == ["t:1:0"]
        assert [r.entry_key for r in await uow.signals.pending_page("t:1:0", 10)] == ["t:2:0"]
