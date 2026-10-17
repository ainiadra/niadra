"""Outcomes and their attribution on PostgreSQL (6.7, 24.7): a batch of objects' links written in one
statement and their days added up in one more, a link that stays keeping the moment it first counted, the
window's end, the reconciliation's values, the identity calibration and erasure."""

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import event
from sqlalchemy.engine import Engine

from niadra.domain.common.ids import uuid7
from niadra.domain.measure.attribution import DETERMINISTIC, FINAL, PROBABLE, PROVISIONAL, Link, link_id
from niadra.domain.measure.counterfactual import Case, summarize
from niadra.domain.privacy.erasure import ErasureScope, TargetKind
from niadra.domain.signals.daily import Interleaved
from niadra.ports.repos.outcomes import StoredRun
from tests.integration.conftest import CloningUnitOfWork

NOW = datetime(2026, 9, 29, 12, 0, tzinfo=UTC)
HANDLE = UUID("0199a3b2-0000-7000-8000-00000000b001")
ORDER = UUID("0199a3b2-0000-7000-8000-00000000a001")
EXPOSURE = "0199a3b2-6d10-7c2a-9e11-4f0b8a7c3e55"


@contextmanager
def counting() -> Iterator[list[str]]:
    statements: list[str] = []

    def count(conn: Any, cursor: Any, statement: str, *args: Any) -> None:
        if "set_config('niadra.space_id'" not in statement:
            statements.append(" ".join(statement.split()[:3]))

    event.listen(Engine, "before_cursor_execute", count)
    try:
        yield statements
    finally:
        event.remove(Engine, "before_cursor_execute", count)


def link(line: str, method: str, **change: Any) -> Link:
    band = DETERMINISTIC if method in ("line", "order") else PROBABLE
    fields: dict[str, Any] = {
        "link_id": link_id(ORDER, line, method, EXPOSURE),
        "outcome": "purchase",
        "object_id": ORDER,
        "object_ref": "order:shop:8812",
        "handle_id": HANDLE,
        "state": "delivered",
        "line_ref": line,
        "method": method,
        "band": band,
        "finality": PROVISIONAL,
        "valid_at": NOW,
        "value_minor": 19990,
        "currency": "BRL",
        "exposure_id": EXPOSURE,
        "position": 1,
        "turn_id": "t-stylist-1",
        "agent": "stylist",
        "final_at": NOW + timedelta(days=30),
    }
    return Link(**(fields | change))


async def test_attributing_a_batch_is_one_statement_and_its_days_one_more(pg_uow: CloningUnitOfWork) -> None:
    space = uuid4()
    links = [link("1", "line"), link("1", "identity"), link("2", "identity", value_minor=14990)]
    async with pg_uow(space) as uow:
        await uow.outcomes.next_due()  # the space's database is found and stamped once, before
    with counting() as statements:
        async with pg_uow(space) as uow:
            days = await uow.outcomes.write([ORDER], links, NOW)
            await uow.outcomes.roll_up(days)
    assert days == [NOW.date()]
    assert len(statements) == 2, statements
    async with pg_uow(space) as uow:
        rows = await uow.outcomes.daily(NOW.date(), NOW.date())
        assert {(r["method"], r["band"], r["lines"], r["value_minor"]) for r in rows} == {
            ("line", DETERMINISTIC, 1, 19990), ("identity", PROBABLE, 2, 34980)
        }  # fmt: skip
        # Written again unchanged, nothing moves; the object reaches `kept` a month later: final, and the link
        # keeps the day it first counted.
        assert await uow.outcomes.write([ORDER], links, NOW) == []
        later = NOW + timedelta(days=31)
        kept = [
            link("1", "line", state="kept", finality=FINAL, valid_at=later, final_at=None),
            link("1", "identity", state="kept", finality=FINAL, valid_at=later, final_at=None),
        ]
        days = await uow.outcomes.write([ORDER], kept, later)
        await uow.outcomes.roll_up(days)
        found = {(s.link.line_ref, s.link.method): s for s in await uow.outcomes.links(before=None, limit=10)}
        assert set(found) == {
            ("1", "line"),
            ("1", "identity"),
        }  # the bag's link left with the object's rewrite
        assert found[("1", "line")].link.valid_at == NOW
        assert found[("1", "line")].link.finality == FINAL
        rows = await uow.outcomes.daily(NOW.date(), NOW.date())
        assert {(r["method"], r["finality"], r["value_minor"]) for r in rows} == {
            ("line", FINAL, 19990), ("identity", FINAL, 19990)
        }  # fmt: skip


async def test_a_split_purchases_parts_count_as_one_order_under_its_definition(
    pg_uow: CloningUnitOfWork,
) -> None:
    # A purchase split by seller into two orders, counted whole by the signed definition.
    space, part = uuid4(), UUID("0199a3b2-0000-7000-8000-00000000a002")
    purchase = "order:shop:P-77"
    links = [
        link("1", "line", order_ref=purchase),
        link("1", "line", object_id=part, object_ref="order:shop:8812-02", order_ref=purchase,
             link_id=link_id(part, "1", "line", EXPOSURE), value_minor=4990),
        link("1", "line", object_id=uuid4(), object_ref="order:shop:9000", link_id=uuid4(), value_minor=100),
    ]  # fmt: skip
    async with pg_uow(space) as uow:
        days = await uow.outcomes.write([ORDER, part], links, NOW)
        await uow.outcomes.roll_up(days)
        [row] = await uow.outcomes.daily(NOW.date(), NOW.date())
        assert (row["outcomes"], row["lines"], row["value_minor"]) == (2, 3, 19990 + 4990 + 100)
        assert sorted(await uow.outcomes.line_values(NOW.date(), NOW.date())) == [
            ("order:shop:9000", "1", 100), (purchase, "1", 4990), (purchase, "1", 19990)
        ]  # fmt: skip


async def test_the_window_ends_and_retention_takes_old_links(pg_uow: CloningUnitOfWork) -> None:
    space = uuid4()
    async with pg_uow(space) as uow:
        await uow.outcomes.write([ORDER], [link("1", "line")], NOW)
        assert await uow.outcomes.next_due() == NOW + timedelta(days=30)
        assert await uow.outcomes.finalize(NOW + timedelta(days=29), limit=10) == []
        assert await uow.outcomes.finalize(NOW + timedelta(days=30), limit=10) == [NOW.date()]
        [stored] = await uow.outcomes.links(before=None, limit=10)
        assert (stored.link.finality, stored.link.final_at) == (FINAL, None)
        assert await uow.outcomes.next_due() is None
        # The service passes `now - 13 months`: a link older than that leaves, with the day it moved.
        assert await uow.outcomes.expire(NOW, limit=10) == []
        assert await uow.outcomes.expire(NOW + timedelta(seconds=1), limit=10) == [NOW.date()]


async def test_reconciliation_values_calibration_paging_and_erasure(pg_uow: CloningUnitOfWork) -> None:
    space = uuid4()
    other = "0199a3b2-6d10-7c2a-9e11-4f0b8a7c3e56"
    links = [
        link("1", "line"),
        link("1", "identity"),
        link("2", "line", value_minor=14990, link_id=link_id(ORDER, "2", "line", EXPOSURE)),
        link("2", "identity", exposure_id=other, link_id=link_id(ORDER, "2", "identity", other)),
    ]
    async with pg_uow(space) as uow:
        await uow.outcomes.write([ORDER], links, NOW)
        assert sorted(await uow.outcomes.line_values(NOW.date(), NOW.date())) == [
            ("order:shop:8812", "1", 19990),
            ("order:shop:8812", "2", 14990),
        ]
        assert await uow.outcomes.agreement(NOW.date(), NOW.date()) == (1, 2)
        assert await uow.outcomes.written_since(NOW, limit=10) == ["order:shop:8812"]
        assert await uow.outcomes.written_since(NOW + timedelta(seconds=1), limit=10) == []
        first = await uow.outcomes.links(before=None, limit=3)
        last = first[-1]
        rest = await uow.outcomes.links(before=(last.known_at, last.link.link_id), limit=3)
        assert len(first) + len(rest) == 4
        report = await uow.outcomes.add_report("reconcile", NOW.date(), NOW.date(), {"deviation": 0.0}, NOW)
        assert isinstance(report, UUID)
        assert [s.link.line_ref for s in await uow.outcomes.links_of([HANDLE])] != []
        scope = ErasureScope(uuid4(), TargetKind.HANDLE, (HANDLE,), None, None, NOW)
        erased = await uow.outcomes.erase_for(scope, limit=100)
        assert [r.table for r in erased] == ["outcome_links"] * 4
        assert await uow.outcomes.links(before=None, limit=10) == []


async def test_the_days_interleaved_impressions_replace_a_rerun(pg_uow: CloningUnitOfWork) -> None:
    space = uuid4()
    async with pg_uow(space) as uow:
        await uow.outcomes.put_interleaved(NOW.date(), {("rank_v2", "search"): Interleaved(3, 1, 1, 1)})
        await uow.outcomes.put_interleaved(NOW.date(), {("rank_v2", "search"): Interleaved(4, 2, 1, 1)})
        [row] = await uow.outcomes.interleaved(NOW.date(), NOW.date())
        assert (row["impressions"], row["wins_a"], row["wins_b"], row["ties"]) == (4, 2, 1, 1)


def bought(handle: UUID, at: datetime, value: int, state: str = "delivered") -> tuple[UUID, list[Link]]:
    order = uuid4()
    base = link("1", "line", object_id=order, handle_id=handle, valid_at=at, value_minor=value, state=state)
    base = replace(base, link_id=link_id(order, "1", "line", EXPOSURE), final_at=None, finality=FINAL)
    return order, [base, replace(base, link_id=link_id(order, "1", "identity", EXPOSURE), method="identity",
                                 band=PROBABLE)]  # fmt: skip


async def test_an_experiments_arms_are_one_statement_over_the_links(pg_uow: CloningUnitOfWork) -> None:
    space = uuid4()
    assigned = NOW - timedelta(days=10)
    people = {arm: [(uuid4(), uuid4()) for _ in range(3)] for arm in ("control", "on")}
    async with pg_uow(space) as uow:
        for arm, members in people.items():
            for profile, handle in members:
                await uow.identity.set_membership([handle], profile, 1)
                await uow.serve.project_assignment(
                    experiment="soft_on", profile_id=profile, group=arm, assigned_at=assigned, kind="element"
                )
        late = uuid4()
        await uow.serve.project_assignment(
            experiment="soft_on",
            profile_id=late,
            group="on",
            assigned_at=NOW + timedelta(days=1),
            kind="element",
        )
        t0h, t1h = people["on"][0][1], people["on"][1][1]
        writes = [
            bought(people["control"][0][1], assigned - timedelta(days=30), 5_000),  # history only
            bought(t0h, assigned + timedelta(days=2), 8_000),
            bought(t0h, assigned + timedelta(days=3), 2_000),
            bought(t0h, assigned - timedelta(days=5), 1_000),  # history
            bought(t1h, assigned + timedelta(days=1), 9_000, state="cancelled"),  # not a success
        ]
        for order, links in writes:
            await uow.outcomes.write([order], links, NOW)
    with counting() as statements:
        async with pg_uow(space) as uow:
            rows = await uow.outcomes.arms("soft_on", "element", NOW, 90, [("purchase", "delivered")])
    assert len(statements) == 1, statements
    by_arm = {r.arm: r for r in rows}
    control, on = by_arm["control"], by_arm["on"]
    assert (control.people, control.converted, control.outcomes, control.value_minor) == (3, 0, 0, 0)
    assert (control.conversion.sx, control.value.sx, control.value.sxx) == (1, 5_000, 25_000_000)
    assert (on.people, on.converted, on.outcomes, on.value_minor) == (3, 1, 2, 10_000)
    assert (on.conversion.sy, on.conversion.sx, on.conversion.sxy) == (1, 1, 1)
    assert (on.value.syy, on.value.sxy) == (100_000_000, 10_000_000)
    async with pg_uow(space) as uow:
        short = await uow.outcomes.arms("soft_on", "element", NOW, 0, [("purchase", "delivered")])
        assert {r.arm: r.conversion.sx for r in short} == {"control": 0, "on": 0}
        assert await uow.outcomes.inflow(NOW - timedelta(days=28)) == [("soft_on", "element", 7)]
        assert await uow.outcomes.last_report("experiment") is None
        await uow.outcomes.add_report("experiment", NOW.date(), NOW.date(), {"experiment": "soft_on"}, NOW)
        assert await uow.outcomes.last_report("experiment") == NOW.date()
        [kept] = await uow.outcomes.reports("experiment", NOW.date())
        assert kept.body == {"experiment": "soft_on"}
        assert await uow.outcomes.reports("experiment", NOW.date() + timedelta(days=1)) == []


async def test_a_counterfactual_run_is_kept_as_its_report(pg_uow: CloningUnitOfWork) -> None:
    space = uuid4()
    summary = summarize([Case("completed", 10, 0.5, 0.9, engaged=((1, 3),)), Case("tool_error")], "hard")
    runs = [StoredRun(uuid7(), "search_items", "hard", label, NOW + timedelta(seconds=i), summary)
            for i, label in enumerate(("first", None))]  # fmt: skip
    async with pg_uow(space) as uow:
        for run in runs:
            await uow.outcomes.add_run(run)
    async with pg_uow(space) as uow:
        newest = await uow.outcomes.runs(before=None, limit=1)
        older = await uow.outcomes.runs(before=(newest[0].created_at, newest[0].run_id), limit=5)
        one = await uow.outcomes.run(runs[0].run_id)
    assert [r.run_id for r in newest + older] == [runs[1].run_id, runs[0].run_id]
    assert one == runs[0]
