"""`PgMeasureRepository` for the learned views (13, 6.3): the counters per reader, arm and section, and the
append-only runs of the learned factors."""

from datetime import date, timedelta

from niadra.adapters.outbound.postgres.uow import PgUnitOfWorkFactory
from niadra.domain.common.ids import uuid7
from niadra.domain.measure.views import DELIVERY, ViewBudgets, ViewUseCounters, ViewUseKey, reader_key
from niadra.domain.measure.weights import WeightOutcome
from tests.integration.measure.test_measure_pg import SOURCE, T0

DAY = date(2026, 9, 22)


async def test_view_counters_add_and_subtract_per_key(pg_uow: PgUnitOfWorkFactory):
    space = uuid7()
    key = ViewUseKey(DAY, SOURCE, "openai/gpt-4o", "voice", "learned", "episode")
    delivery = ViewUseKey(DAY, SOURCE, "openai/gpt-4o", "voice", "learned", DELIVERY)
    old = ViewUseKey(DAY - timedelta(days=9), SOURCE, "neutral", "chat", "default", "fact")
    async with pg_uow(space) as uow:
        await uow.measure.add_view_use(key, ViewUseCounters(deliveries=2, used=1))
        await uow.measure.add_view_use(key, ViewUseCounters(deliveries=1, missed=1))
        await uow.measure.add_view_use(
            delivery, ViewUseCounters(deliveries=3, used=2, questions=4, repeated=1)
        )
        await uow.measure.add_view_use(old, ViewUseCounters(deliveries=1))
        await uow.measure.add_view_use(key, ViewUseCounters(deliveries=1, missed=1).negated())
    async with pg_uow(space) as uow:
        rows = await uow.measure.view_use_daily(DAY - timedelta(days=1), DAY)
        everything = await uow.measure.view_use_daily(DAY - timedelta(days=30), DAY, frozenset({SOURCE}))
        nobody = await uow.measure.view_use_daily(DAY - timedelta(days=30), DAY, frozenset({uuid7()}))
    found = {r.key: r.counters for r in rows}
    assert found[key] == ViewUseCounters(deliveries=2, used=1)
    assert found[delivery] == ViewUseCounters(deliveries=3, used=2, questions=4, repeated=1)
    assert len(everything) == 3
    assert nobody == []


async def test_view_runs_are_appended_and_the_newest_is_current(pg_uow: PgUnitOfWorkFactory):
    space = uuid7()
    reader = reader_key(SOURCE, "neutral", "voice")
    first = ViewBudgets(
        uuid7(),
        DAY,
        T0,
        1,
        WeightOutcome.APPLIED,
        {reader: {"episode": 0.985062, "slots": 1.013709}},
        {"guard": {"state": "no_experiment"}, "baseline_lift": None, "readers": {}},
    )
    second = ViewBudgets(
        uuid7(), DAY + timedelta(days=1), T0 + timedelta(days=1), 1, WeightOutcome.VETOED, first.factors, {}
    )
    async with pg_uow(space) as uow:
        assert await uow.measure.view_budgets() is None
        await uow.measure.put_view_budgets(first)
        await uow.measure.put_view_budgets(second)
    async with pg_uow(space) as uow:
        current = await uow.measure.view_budgets()
        runs = await uow.measure.view_budget_runs(10)
    assert current == second
    assert runs == [second, first]
    assert current is not None
    assert current.factors_for(SOURCE, "neutral", "voice")["episode"] == 0.985062
