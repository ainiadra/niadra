"""`PgMeasureRepository` for guard lines (13, 6.1) and backed answers (13, 6.2): the counters per value
type and arm, the types the read path writes guards for, and the backing counters of a daily row."""

from datetime import date, timedelta

from niadra.adapters.outbound.postgres.uow import PgUnitOfWorkFactory
from niadra.domain.common.ids import uuid7
from niadra.domain.measure.model import DailyCounters, DailyKey
from niadra.domain.measure.slots import GuardCounters, GuardDailyKey
from tests.integration.measure.test_measure_pg import SOURCE

DAY = date(2026, 9, 22)


async def test_guard_counters_add_subtract_and_name_the_contradicted_types(pg_uow: PgUnitOfWorkFactory):
    space = uuid7()
    date_key = GuardDailyKey(DAY, SOURCE, "date", "guards")
    amount_key = GuardDailyKey(DAY, SOURCE, "amount", "value")
    old = GuardDailyKey(DAY - timedelta(days=40), SOURCE, "protocol", "value")
    async with pg_uow(space) as uow:
        await uow.measure.add_guard_daily(date_key, GuardCounters(slots=3, contradicted=1))
        await uow.measure.add_guard_daily(date_key, GuardCounters(slots=1, contradicted=0))
        await uow.measure.add_guard_daily(amount_key, GuardCounters(slots=2, contradicted=1))
        await uow.measure.add_guard_daily(amount_key, GuardCounters(slots=1, contradicted=1).negated())
        await uow.measure.add_guard_daily(old, GuardCounters(slots=5, contradicted=5))
    async with pg_uow(space) as uow:
        rows = await uow.measure.guard_daily(DAY - timedelta(days=1), DAY)
        types = await uow.measure.contradicted_value_types(DAY - timedelta(days=30))
        nobody = await uow.measure.guard_daily(DAY - timedelta(days=60), DAY, frozenset({uuid7()}))
    by_key = {r.key: (r.counters.slots, r.counters.contradicted) for r in rows}
    assert by_key == {date_key: (4, 1), amount_key: (1, 0)}
    assert types == {"date"}  # the amount's contradiction was taken back; the protocol's is too old
    assert nobody == []
    async with pg_uow(uuid7()) as uow:
        assert await uow.measure.contradicted_value_types(DAY - timedelta(days=30)) == frozenset()


async def test_the_daily_row_keeps_the_backing_counters(pg_uow: PgUnitOfWorkFactory):
    space = uuid7()
    key = DailyKey(DAY, SOURCE, "voice", "", "")
    async with pg_uow(space) as uow:
        await uow.measure.add_daily(key, DailyCounters(sessions=1, answers_checked=4, unbacked_values=3))
        await uow.measure.add_daily(key, DailyCounters(answers_checked=1, guard_violations=2))
    async with pg_uow(space) as uow:
        [found] = await uow.measure.daily(DAY, DAY)
    counters = found.counters.as_dict()
    assert (counters["answers_checked"], counters["unbacked_values"], counters["guard_violations"]) == (
        5,
        3,
        2,
    )
