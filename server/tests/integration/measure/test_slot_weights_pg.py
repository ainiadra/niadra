"""`PgMeasureRepository` for the per-turn slots (11, 3.4): the slots of a row, the counters per retrieval
channel and the append-only runs of the retrieval weights."""

from datetime import date, timedelta

from niadra.adapters.outbound.postgres.uow import PgUnitOfWorkFactory
from niadra.domain.common.ids import uuid7
from niadra.domain.measure.slots import SlotCounters, SlotDailyKey
from niadra.domain.measure.weights import RetrievalWeights, WeightOutcome
from tests.integration.measure.test_measure_pg import SOURCE, T0, row

DAY = date(2026, 9, 22)
TURN = {
    "at": T0.isoformat(),
    "channels": {"exact": {"hits": 1}, "semantic": {"hits": 0, "skipped": True}},
    "items": [
        {
            "id": "v1",
            "kind": "value",
            "channels": ["exact", "values"],
            "position": 1,
            "used": True,
            "repeated": False,
            "contradicted": False,
            "evidence": [],
        }
    ],
}


async def test_a_row_keeps_its_slots(pg_uow: PgUnitOfWorkFactory):
    space, session, profile = uuid7(), uuid7(), uuid7()
    async with pg_uow(space) as uow:
        await uow.measure.insert_rows([row(session, profile, slots=(TURN,)), row(session, profile, "m2")])
    async with pg_uow(space) as uow:
        found = {r.manifest_hash: r for r in await uow.measure.rows_for_session(session)}
    assert found["m1"].slots == (TURN,)
    assert found["m2"].slots == ()


async def test_channel_counters_add_and_subtract_per_key(pg_uow: PgUnitOfWorkFactory):
    space = uuid7()
    key = SlotDailyKey(DAY, SOURCE, "voice", "voice", "exp:memory", "values")
    other = SlotDailyKey(DAY - timedelta(days=9), SOURCE, "voice", "voice", "", "exact")
    async with pg_uow(space) as uow:
        await uow.measure.add_slot_daily(key, SlotCounters(turns=2, slots=2, used=1))
        await uow.measure.add_slot_daily(key, SlotCounters(turns=1, slots=1, used=1, contradicted=1))
        await uow.measure.add_slot_daily(other, SlotCounters(turns=1, skipped=1))
        await uow.measure.add_slot_daily(key, SlotCounters(turns=1, slots=1, used=1).negated())
    async with pg_uow(space) as uow:
        rows = await uow.measure.slot_daily(DAY - timedelta(days=1), DAY)
        everything = await uow.measure.slot_daily(DAY - timedelta(days=30), DAY, frozenset({SOURCE}))
        nobody = await uow.measure.slot_daily(DAY - timedelta(days=30), DAY, frozenset({uuid7()}))
    [only] = rows
    assert only.key == key
    assert only.counters.as_dict() == {
        "turns": 2,
        "skipped": 0,
        "slots": 2,
        "used": 1,
        "repeated": 0,
        "contradicted": 1,
    }
    assert len(everything) == 2
    assert nobody == []


async def test_weight_runs_are_appended_and_the_newest_is_current(pg_uow: PgUnitOfWorkFactory):
    space = uuid7()
    first = RetrievalWeights(
        uuid7(),
        DAY,
        T0,
        1,
        WeightOutcome.APPLIED,
        {"chat": {"values": 2.027, "semantic": 0.98}},
        {"guard": {"state": "no_experiment"}, "baseline_lift": None},
    )
    second = RetrievalWeights(
        uuid7(), DAY + timedelta(days=1), T0 + timedelta(days=1), 1, WeightOutcome.VETOED, first.weights, {}
    )
    async with pg_uow(space) as uow:
        assert await uow.measure.retrieval_weights() is None
        await uow.measure.put_retrieval_weights(first)
        await uow.measure.put_retrieval_weights(second)
    async with pg_uow(space) as uow:
        current = await uow.measure.retrieval_weights()
        runs = await uow.measure.weight_runs(10)
    assert current == second
    assert runs == [second, first]
    assert current is not None
    assert current.for_view("chat")["values"] == 2.027
