"""Replay's rows on PostgreSQL (24.3.5, 24.6.5): a scenario's turns become its kept rows and stay at least as
long as before, scenarios are paged and changed with their version, runs are stored once and found by the
scenarios they include, and the builds give the change log its pins."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from niadra.domain.common.ids import uuid7
from niadra.domain.turns.records import (
    AppliedTurns,
    BuildSeen,
    ScenarioChange,
    ScenarioRow,
    ScenarioRunRow,
)
from niadra.domain.turns.storage import KeptBy
from niadra.testing.cell import WHATSAPP_SOURCE
from tests.integration.conftest import CloningUnitOfWork
from tests.integration.turns.test_pg_turns import _kept

NOW = datetime(2026, 9, 29, 15, 0, tzinfo=UTC)


def _scenario(name: str, at: datetime) -> ScenarioRow:
    return ScenarioRow(
        scenario_id=uuid7(),
        name=name,
        origin="manual",
        turn_ids=("t-1", "t-2"),
        assertions=({"id": "a", "kind": "tool_called", "args": {"tool": "quote"}},),
        status="active",
        version=1,
        created_at=at,
        created_by="user:x",
        updated_at=at,
    )


async def test_a_scenarios_turns_keep_the_later_kept_until_and_renew_only_their_own(
    pg_uow: CloningUnitOfWork,
) -> None:
    space = uuid4()
    conversation = b"\xaa" * 32
    async with pg_uow(space) as uow:
        await uow.turns.keep([_kept("t-1", conversation, NOW, kept_until=NOW + timedelta(days=400))])
        await uow.turns.keep([_kept("t-3", conversation, NOW)])
    scenario_until = NOW + timedelta(days=180)
    async with pg_uow(space) as uow:
        await uow.turns.keep_for_scenario(
            [
                _kept("t-1", conversation, NOW, kept_by=KeptBy.SCENARIO, kept_until=scenario_until),
                _kept("t-2", conversation, NOW, kept_by=KeptBy.SCENARIO, kept_until=scenario_until),
            ]
        )
        await uow.turns.renew_scenario_turns(["t-2", "t-3"], NOW + timedelta(days=200))
    async with pg_uow(space) as uow:
        rows = {k.turn_id: k for k in await uow.turns.kept(["t-1", "t-2", "t-3"])}
    assert (rows["t-1"].kept_by, rows["t-1"].kept_until) == (KeptBy.SCENARIO, NOW + timedelta(days=400))
    assert rows["t-2"].kept_until == NOW + timedelta(days=200)
    assert (rows["t-3"].kept_by, rows["t-3"].kept_until) == (KeptBy.REQUEST, NOW + timedelta(days=30))


async def test_scenarios_are_added_once_paged_and_changed(pg_uow: CloningUnitOfWork) -> None:
    space = uuid4()
    rows = [_scenario(f"s{i}", NOW + timedelta(minutes=i)) for i in range(3)]
    async with pg_uow(space) as uow:
        for row in rows:
            assert await uow.turns.add_scenario(row) == row
        assert await uow.turns.add_scenario(rows[0]) == rows[0], "a retried request finds what it made"
    async with pg_uow(space) as uow:
        first = await uow.turns.scenarios(status="active", limit=2)
        rest = await uow.turns.scenarios(
            status="active", before=(first[-1].created_at, first[-1].scenario_id), limit=2
        )
        named = await uow.turns.scenarios(ids=[rows[1].scenario_id])
        changed = await uow.turns.change_scenario(
            rows[0].scenario_id, ScenarioChange(status="retired", assertions=()), NOW + timedelta(hours=1)
        )
        missing = await uow.turns.change_scenario(uuid4(), ScenarioChange(name="x"), NOW)
    assert [s.name for s in first + rest] == ["s2", "s1", "s0"]
    assert named == [rows[1]]
    assert changed is not None
    assert (changed.status, changed.assertions, changed.version, changed.name) == ("retired", (), 2, "s0")
    assert missing is None
    async with pg_uow(space) as uow:
        assert [s.name for s in await uow.turns.scenarios(status="retired")] == ["s0"]


async def test_runs_are_stored_once_and_found_by_any_scenario_they_include(pg_uow: CloningUnitOfWork) -> None:
    space = uuid4()
    a, b, c = uuid4(), uuid4(), uuid4()

    def run(at: datetime, *scenarios: object) -> ScenarioRunRow:
        return ScenarioRunRow(
            run_id=uuid7(),
            scenario_ids=tuple(scenarios),  # type: ignore[arg-type]
            build={"pins": {"model": "m"}},
            mode="hermetic_turn",
            vary=("prompts",),
            status="done",
            verdict="pass",
            summary={"scenarios": []},
            created_at=at,
            decided_at=at,
        )

    older, newer = run(NOW, a, b), run(NOW + timedelta(minutes=1), b, c)
    async with pg_uow(space) as uow:
        assert await uow.turns.add_run(older) == older
        assert await uow.turns.add_run(newer) == newer
        assert await uow.turns.add_run(older) == older
    async with pg_uow(space) as uow:
        assert await uow.turns.run(older.run_id) == older
        assert await uow.turns.run(uuid4()) is None
        found = await uow.turns.runs_of([a, c], NOW + timedelta(hours=1), 10)
        before = await uow.turns.runs_of([b], NOW + timedelta(seconds=30), 10)
    assert [r.run_id for r in found] == [newer.run_id, older.run_id]
    assert [r.run_id for r in before] == [older.run_id]


async def test_the_builds_seen_give_their_pins_by_hash_newest_first(pg_uow: CloningUnitOfWork) -> None:
    space = uuid4()
    builds = [
        BuildSeen(
            bytes([i]) * 32,
            WHATSAPP_SOURCE,
            "closing",
            {"model": f"m-{i}"},
            None,
            None,
            NOW + timedelta(hours=i),
        )
        for i in range(3)
    ]
    async with pg_uow(space) as uow:
        await uow.turns.apply(AppliedTurns(intake_ids=(), builds=tuple(builds)))
    async with pg_uow(space) as uow:
        listed = await uow.turns.builds(2)
        pins = await uow.turns.build_pins([bytes([0]) * 32, b"\xff" * 32])
    assert [b.pins["model"] for b in listed] == ["m-2", "m-1"]
    assert listed[0].at == NOW + timedelta(hours=2)
    assert pins == {bytes([0]) * 32: {"model": "m-0"}}
