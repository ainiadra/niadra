"""Experiment reports, their nightly looks, the power calculator and counterfactual runs:
each arm from what its people did after they were assigned, CUPED over the days before, the sequential
test's p-value kept as the minimum over the looks, and a run from the company's CI stored as its report."""

from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest

from niadra.adapters.inbound.http.app import create_app
from niadra.adapters.inbound.http.signals import admin_router
from niadra.adapters.memory.fakes import StaticConfig
from niadra.app.experiments import LOOK, ExperimentsService
from niadra.config.measurement import MeasurementSettings
from niadra.config.models import FeatureSettings, SpaceConfig
from niadra.contracts.signals import PowerRequest
from niadra.domain.common.errors import InvalidInput, Unauthenticated
from niadra.domain.common.ids import uuid7
from niadra.domain.common.space import PersonPrincipal, SourcePrincipal, SpaceRef
from niadra.domain.measure.attribution import DETERMINISTIC, FINAL, PROBABLE, Link, link_id
from niadra.domain.vocabulary import Environment, Feature, Scope
from niadra.testing.cell import SPACE_ID, TENANT_ID, WHATSAPP_SOURCE, Cell, principal, sample_config

START = datetime(2026, 9, 1, tzinfo=UTC)
ASSIGNED = START + timedelta(days=1)
SPACE = SpaceRef(SPACE_ID, TENANT_ID, "us-east-2", Environment.SANDBOX)
ANALYST = PersonPrincipal(SPACE, uuid7(), frozenset({"analysis"}))
PURCHASE = {"name": "purchase", "object_type": "order", "success_states": ["delivered", "kept"]}


def experiment(**change: Any) -> dict[str, Any]:
    fields: dict[str, Any] = {
        "name": "soft_on",
        "kind": "element",
        "element": "soft",
        "arms": [{"name": "control", "fraction": 0.5}, {"name": "on", "fraction": 0.5}],
        "salt": "salt-of-the-experiment",
        "starts_at": START,
    }
    return fields | change


def configured(*experiments: dict[str, Any]) -> SpaceConfig:
    measurement = {"revenue": {"currency": "BRL"}, "outcomes": [PURCHASE], "experiments": list(experiments)}
    return sample_config().model_copy(
        update={
            "features": FeatureSettings(enabled=frozenset({Feature.MEASUREMENT})),
            "measurement": MeasurementSettings.model_validate(measurement),
        }
    )


@dataclass(frozen=True)
class Person:
    profile_id: UUID
    handle_id: UUID


async def assign(
    cell: Cell, arm: str, n: int, *, name: str = "soft_on", kind: str = "element"
) -> list[Person]:
    people = [Person(uuid4(), uuid4()) for _ in range(n)]
    async with cell.uow(SPACE_ID) as uow:
        for person in people:
            await uow.identity.set_membership([person.handle_id], person.profile_id, 1)
            await uow.serve.project_assignment(
                experiment=name, profile_id=person.profile_id, group=arm, assigned_at=ASSIGNED, kind=kind
            )
    return people


async def bought(cell: Cell, person: Person, at: datetime, value: int = 10_000, state: str = "kept") -> None:
    """An order of one line, attributed by its token and, for the same line, by identity: it counts once."""
    order = uuid4()
    line = Link(
        link_id=link_id(order, "1", "line", "e1"), outcome="purchase", object_id=order,
        object_ref=f"order:shop:{order}", handle_id=person.handle_id, state=state, line_ref="1",
        method="line", band=DETERMINISTIC, finality=FINAL, valid_at=at, value_minor=value, currency="BRL",
        exposure_id="e1", position=1,
    )  # fmt: skip
    probable = replace(line, link_id=link_id(order, "1", "identity", "e1"), method="identity", band=PROBABLE)
    async with cell.uow(SPACE_ID) as uow:
        await uow.outcomes.write([order], [line, probable], at)


def service(cell: Cell) -> ExperimentsService:
    return ExperimentsService(cell.uow, cell.clock, cell.config)


async def a_running_experiment(cell: Cell) -> tuple[list[Person], list[Person]]:
    """200 people an arm; a third of the treated bought, a sixth of the control; a fifth of each bought before
    they were assigned, and those buy again more often."""
    cell.config.put(configured(experiment()))
    control, treated = await assign(cell, "control", 200), await assign(cell, "on", 200)
    after = ASSIGNED + timedelta(days=3)
    for i, person in enumerate(control):
        if i % 5 == 0:
            await bought(cell, person, START - timedelta(days=20))
        if i % 6 == 0:
            await bought(cell, person, after, value=8_000)
    for i, person in enumerate(treated):
        if i % 5 == 0:
            await bought(cell, person, START - timedelta(days=20))
        if i % 3 == 0:
            await bought(cell, person, after, value=8_000)
    cell.clock.at = ASSIGNED + timedelta(days=10)
    return control, treated


async def test_each_arm_counts_what_its_people_did_after_their_assignment(cell: Cell) -> None:
    await a_running_experiment(cell)
    report = await service(cell).report(ANALYST)
    [result] = report.experiments
    arms = {a.arm: a for a in result.arms}
    assert (arms["control"].people, arms["control"].converted, arms["control"].outcomes) == (200, 34, 34)
    assert (arms["on"].people, arms["on"].converted, arms["on"].value_minor) == (200, 67, 67 * 8_000)
    by_metric = {c.metric: c for c in result.comparisons}
    conversion = by_metric["conversion"]
    assert conversion.arm == "on"
    assert conversion.control_mean == pytest.approx(34 / 200)
    assert conversion.arm_mean == pytest.approx(67 / 200)
    assert conversion.ci_low < conversion.difference < conversion.ci_high
    assert conversion.looks == 0
    assert conversion.decided == (conversion.p_value <= result.alpha)
    assert by_metric["value"].relative == pytest.approx(conversion.relative, rel=0.2)


async def test_an_outcome_before_the_assignment_is_history_not_effect(cell: Cell) -> None:
    cell.config.put(configured(experiment()))
    [early] = await assign(cell, "control", 1)
    await bought(cell, early, START - timedelta(days=5))
    cell.clock.at = ASSIGNED + timedelta(days=1)
    async with cell.uow(SPACE_ID) as uow:
        [row] = await uow.outcomes.arms("soft_on", "element", cell.clock.now(), 90, [("purchase", "kept")])
    assert (row.converted, row.conversion.sy, row.conversion.sx) == (0, 0, 1)
    async with cell.uow(SPACE_ID) as uow:
        [row] = await uow.outcomes.arms("soft_on", "element", cell.clock.now(), 0, [("purchase", "kept")])
    assert row.conversion.sx == 0  # CUPED off: no history read


async def test_the_nightly_look_is_kept_and_later_reports_take_the_minimum(cell: Cell) -> None:
    await a_running_experiment(cell)
    experiments = service(cell)
    assert await experiments.look(SPACE_ID) == 1
    assert await experiments.look(SPACE_ID) == 0  # once a day
    first = {c.metric: c for c in (await experiments.report(ANALYST)).experiments[0].comparisons}
    assert first["conversion"].looks == 1
    async with cell.uow(SPACE_ID) as uow:
        [kept] = await uow.outcomes.reports(LOOK, START.date())
        # A look that saw more than today does: the report keeps the smallest p-value ever seen.
        await uow.outcomes.add_report(
            LOOK, START.date(), START.date(), {"experiment": "soft_on", "arms": {"on": {"value": 1e-4}}},
            cell.clock.now(),
        )  # fmt: skip
    assert kept.body["experiment"] == "soft_on"
    later = {c.metric: c for c in (await experiments.report(ANALYST)).experiments[0].comparisons}
    assert later["value"].p_value == pytest.approx(1e-4)
    assert later["value"].decided


async def test_nothing_is_compared_below_the_arms_minimum_and_nothing_runs_where_nothing_is_measured(
    cell: Cell,
) -> None:
    cell.config.put(configured(experiment()))
    await assign(cell, "control", 10)
    await assign(cell, "on", 10)
    [result] = (await service(cell).report(ANALYST)).experiments
    assert [a.people for a in result.arms] == [10, 10]
    assert result.comparisons == []
    cell.config.put(sample_config())
    assert await service(cell).look(SPACE_ID) == 0


async def test_power_from_the_space_traffic_and_from_given_numbers(cell: Cell) -> None:
    await a_running_experiment(cell)
    found = await service(cell).power(ANALYST, PowerRequest(effect=0.1))
    assert (found.traffic_from, found.baseline_from, found.experiment) == ("history", "history", "soft_on")
    # The 400 people came in over the 11 days the experiment has run.
    assert found.daily_people == pytest.approx(400 / 11, abs=1e-3)
    assert found.baseline_mean == pytest.approx(101 / 400)
    assert found.days is not None
    assert found.days_sequential is not None
    assert found.days_sequential >= found.days
    given = await service(cell).power(
        ANALYST, PowerRequest(effect=0.2, daily_people=500, baseline_mean=0.1, control_fraction=0.5)
    )
    assert (given.traffic_from, given.baseline_from, given.experiment) == ("given", "given", None)
    assert given.people == pytest.approx(7_070, rel=0.02)
    assert given.days == 15
    value = await service(cell).power(
        ANALYST,
        PowerRequest(metric="value", effect=0.2, daily_people=100, baseline_mean=5000, baseline_sd=9000),
    )
    assert value.baseline_sd == 9000


async def test_power_without_history_asks_for_the_numbers(cell: Cell) -> None:
    cell.config.put(configured())
    with pytest.raises(InvalidInput, match="daily_people"):
        await service(cell).power(ANALYST, PowerRequest(effect=0.1))
    with pytest.raises(InvalidInput, match="rate"):
        await service(cell).power(ANALYST, PowerRequest(effect=0.1, daily_people=10, baseline_mean=2))


# Over HTTP.

RUNNER = principal(WHATSAPP_SOURCE, scopes=frozenset({Scope.REPLAY}))
TRACKER = principal(WHATSAPP_SOURCE, scopes=frozenset({Scope.TRACK}))


class FakeEdge:
    async def authenticate_person(self, token: str) -> PersonPrincipal:
        if token != "analyst":
            raise Unauthenticated("unknown token")
        return ANALYST

    async def authenticate_key(self, token: str) -> SourcePrincipal:
        keys = {"nia_sk_runner": RUNNER, "nia_sk_tracker": TRACKER}
        if token not in keys:
            raise Unauthenticated("unknown key")
        return keys[token]

    async def check_rate(self, principal: Any, path: str) -> None:
        return None


@dataclass
class Container:
    edge: FakeEdge
    config: StaticConfig
    experiments: ExperimentsService


def client(cell: Cell) -> httpx.AsyncClient:
    app = create_app("niadra-test", Container(FakeEdge(), cell.config, service(cell)), (admin_router,))
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://cell")


def case(i: int, **change: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "turn_id": f"t-{i}", "call_id": "c1", "status": "completed", "k": 10, "overlap": 0.5, "noise": 0.95,
        "base_count": 10, "variant_count": 12, "engaged": [{"base": 2, "variant": 4}],
    }  # fmt: skip
    return body | change


async def test_the_ci_runner_posts_a_counterfactual_run_and_the_console_reads_it(cell: Cell) -> None:
    cell.config.put(configured())
    run = {
        "tool": "search_items",
        "element": "hard",
        "label": "ci-7f3c2a1",
        "cases": [case(i) for i in range(40)],
    }
    run["cases"].append({"turn_id": "t-x", "call_id": "c9", "status": "no_dry_run"})
    async with client(cell) as http:
        created = await http.post(
            "/v1/measure/counterfactual-runs", headers={"authorization": "Bearer nia_sk_runner"}, json=run
        )
        unscoped = await http.post(
            "/v1/measure/counterfactual-runs", headers={"authorization": "Bearer nia_sk_tracker"}, json=run
        )
        measures = await http.post(
            "/v1/measure/counterfactual-runs",
            headers={"authorization": "Bearer nia_sk_runner"},
            json={**run, "cases": [case(0, status="tool_error")]},
        )
        page = await http.get("/v1/measure/counterfactual-runs", headers={"authorization": "Bearer analyst"})
        one = await http.get(
            f"/v1/measure/counterfactual-runs/{created.json()['run_id']}",
            headers={"authorization": "Bearer analyst"},
        )
        missing = await http.get(
            f"/v1/measure/counterfactual-runs/{uuid4()}", headers={"authorization": "Bearer analyst"}
        )
    assert created.status_code == 201, created.text
    body = created.json()
    assert (body["cases"], body["completed"], body["skipped"]) == (41, 40, {"no_dry_run": 1})
    assert (body["overlap"], body["noise_floor"], body["effect"]) == (0.5, 0.95, 0.45)
    assert body["below_noise"] == 40
    assert body["engaged"] == {
        "items": 40,
        "shown": 40,
        "kept": 40,
        "lost": 0,
        "gained": 0,
        "mean_shift": 2.0,
    }
    assert body["limits"] == [
        "not_quality",
        "model_reaction_not_measured",
        "trivial_for_hard",
        "cases_skipped",
    ]
    assert unscoped.status_code == 403
    assert measures.status_code == 422  # a case that did not complete carries no measures
    assert [r["run_id"] for r in page.json()["items"]] == [body["run_id"]]
    assert one.json() == body
    assert missing.status_code == 404


async def test_the_analyst_reads_the_experiments_and_asks_the_power(cell: Cell) -> None:
    await a_running_experiment(cell)
    analyst = {"authorization": "Bearer analyst"}
    async with client(cell) as http:
        report = await http.get("/v1/measure/experiments", headers=analyst)
        power = await http.post("/v1/measure/power", headers=analyst, json={"effect": 0.1})
        refused = await http.post("/v1/measure/power", headers=analyst, json={"effect": 0})
    assert report.status_code == 200
    [result] = report.json()["experiments"]
    assert {a["arm"] for a in result["arms"]} == {"control", "on"}
    assert {c["metric"] for c in result["comparisons"]} == {"conversion", "value"}
    assert power.status_code == 200
    assert power.json()["experiment"] == "soft_on"
    assert refused.status_code == 422


async def test_the_routes_answer_as_if_absent_where_measurement_is_off(cell: Cell) -> None:
    cell.config.put(sample_config())
    async with client(cell) as http:
        report = await http.get("/v1/measure/experiments", headers={"authorization": "Bearer analyst"})
    assert report.status_code == 404
