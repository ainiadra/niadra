"""Experiments and the tool counterfactual.

An experiment on an element or an agent holds a share of people out of it, drawn once per person (24.7). Its
report compares each arm with `control` on what people did after they were assigned, from the outcome links:
the share that converted and the money of their lines. CUPED adjusts each person by the same measure over the
days before their assignment, and the mixture sequential test decides: once a night the report's look is kept,
and the p-value a report gives is the minimum over the kept looks and its own, valid however often anyone
looks. The power calculator says, before an experiment starts, how many days of the space's traffic a
difference needs.

The counterfactual runs come from the company's CI: what an element changes in a tool's answer, against the
tool's own noise (the tool counterfactual spec). The report is kept; the cases are not.
"""

import dataclasses
import math
from collections import defaultdict
from collections.abc import Sequence
from datetime import datetime, timedelta
from uuid import UUID

from niadra.app.outcomes import outcome_definitions
from niadra.config.measurement import ElementExperiment, SequentialTest
from niadra.config.models import SpaceConfig
from niadra.contracts.signals import (
    ArmComparison,
    ArmOutcomes,
    CounterfactualRun,
    CounterfactualRunCreate,
    CounterfactualRunPage,
    ExperimentReport,
    ExperimentResult,
    PowerRequest,
    PowerResult,
)
from niadra.domain.common.errors import InvalidInput, NotFound
from niadra.domain.common.ids import uuid7
from niadra.domain.common.space import Principal
from niadra.domain.measure.counterfactual import Case, summarize
from niadra.domain.measure.experiments import CONTROL
from niadra.domain.measure.stats import (
    Moments,
    compare,
    mixing_tau2,
    msprt,
    needed,
    per_person_variance,
    theta,
)
from niadra.domain.notify.cursor import decode_cursor, encode_cursor
from niadra.domain.vocabulary import Feature
from niadra.ports.infra import Clock, ConfigProvider
from niadra.ports.repos.outcomes import ArmRow, StoredReport, StoredRun
from niadra.ports.uow import UnitOfWorkFactory

LOOK = "experiment"
"""The kind of the reports that keep each night's look."""
LOOK_HOUR = 5
"""The nightly look runs after the signals' nightly pass, in the space's time zone."""
TRAFFIC_DAYS = 28
METRICS = ("conversion", "value")
REPORTED_KINDS = frozenset({"element", "agent"})


class ExperimentsService:
    def __init__(self, uow: UnitOfWorkFactory, clock: Clock, config: ConfigProvider) -> None:
        self._uow = uow
        self._clock = clock
        self._config = config

    # The report and its nightly look.

    async def report(self, principal: Principal) -> ExperimentReport:
        """Every element and agent experiment, arm by arm: one statement per experiment and one for the
        looks."""
        space_id = principal.space.space_id
        now = self._clock.now()
        found = await self._compute(space_id, now)
        return ExperimentReport(as_of=now, experiments=[result for result, _ in found])

    async def look(self, space_id: UUID) -> int:
        """The nightly look: each experiment's p-values now, kept so every later report takes the minimum.
        Nothing, and no statement, in a space without experiments to report."""
        config = self._measuring(space_id)
        if config is None or not _reported(config):
            return 0
        now = self._clock.now()
        today = now.date()
        async with self._uow(space_id) as uow:
            last = await uow.outcomes.last_report(LOOK)
        if last is not None and last >= today:
            return 0
        found = await self._compute(space_id, now)
        async with self._uow(space_id) as uow:
            for result, _ in found:
                if not result.comparisons:
                    continue
                arms: dict[str, dict[str, float]] = defaultdict(dict)
                for c in result.comparisons:
                    arms[c.arm][c.metric] = c.p_value
                await uow.outcomes.add_report(
                    LOOK, today, today, {"experiment": result.experiment, "arms": arms}, now
                )
        return len(found)

    async def _compute(self, space_id: UUID, now: datetime) -> list[tuple[ExperimentResult, list[ArmRow]]]:
        config = self._measuring(space_id)
        if config is None:
            return []
        experiments = [e for e in _reported(config) if e.starts_at <= now]
        if not experiments:
            return []
        success = [(d.name, state) for d in outcome_definitions(config) for state in d.success_states]
        async with self._uow(space_id) as uow:
            rows = {
                e.name: await uow.outcomes.arms(
                    e.name, e.kind, min(now, e.ends_at or now), e.cuped_days, success
                )
                for e in experiments
            }
            looks = await uow.outcomes.reports(LOOK, min(e.starts_at for e in experiments).date())
        return [(_result(e, rows[e.name], looks), rows[e.name]) for e in experiments]

    # Power.

    async def power(self, principal: Principal, request: PowerRequest) -> PowerResult:
        """How many days of traffic a relative difference of `effect` needs, fixed and sequential."""
        space_id = principal.space.space_id
        config = self._config.space(space_id)
        now = self._clock.now()
        name: str | None = None
        daily = float(request.daily_people) if request.daily_people is not None else None
        mean, sd = request.baseline_mean, request.baseline_sd
        if request.metric == "conversion" and mean is not None:
            if mean > 1:
                raise InvalidInput("a conversion baseline is a rate, at most 1")
            sd = math.sqrt(mean * (1 - mean))
        if daily is None or mean is None or sd is None:
            name, kind, people = await self._history(space_id, now, request.experiment)
            if daily is None:
                daily = people / _traffic_days(config, name, now)
            if mean is None or sd is None:
                success = [(d.name, s) for d in outcome_definitions(config) for s in d.success_states]
                async with self._uow(space_id) as uow:
                    rows = await uow.outcomes.arms(name, kind, now, 0, success)
                pooled = sum((_moments(r, request.metric) for r in rows), Moments())
                mean = pooled.mean_y if mean is None else mean
                sd = math.sqrt(pooled.var_y) if request.metric == "value" and sd is None else sd
                if request.metric == "conversion":
                    sd = math.sqrt(mean * (1 - mean))
        assert sd is not None
        sequential = _sequential(config, name)
        need = needed(
            request.effect * mean,
            sd * sd,
            alpha=request.alpha,
            power=request.power,
            control_fraction=request.control_fraction,
            mixing_variance=sequential.mixing_variance,
        )
        return PowerResult(
            metric=request.metric,
            effect=request.effect,
            baseline_mean=round(mean, 6),
            baseline_sd=round(sd, 6),
            daily_people=round(daily, 3),
            people=need.people if need else None,
            days=_days(need.people, daily) if need else None,
            people_sequential=need.people_sequential if need else None,
            days_sequential=_days(need.people_sequential, daily) if need else None,
            traffic_from="given" if request.daily_people is not None else "history",
            baseline_from="given" if request.baseline_mean is not None else "history",
            experiment=name,
        )

    async def _history(self, space_id: UUID, now: datetime, wanted: str | None) -> tuple[str, str, int]:
        """The experiment whose last 28 days stand for the traffic: the named one, or the busiest."""
        async with self._uow(space_id) as uow:
            inflow = await uow.outcomes.inflow(now - timedelta(days=TRAFFIC_DAYS))
        found = [x for x in inflow if wanted is None or x[0] == wanted]
        if not found:
            raise InvalidInput(
                "no experiment took people in the last 28 days: pass `daily_people` and `baseline_mean`"
                if wanted is None
                else "that experiment took no people in the last 28 days"
            )
        return max(found, key=lambda x: (x[2], x[0]))

    # Counterfactual runs.

    async def record_run(self, principal: Principal, request: CounterfactualRunCreate) -> CounterfactualRun:
        cases = [
            Case(c.status, c.k, c.overlap, c.noise, c.dry_run, tuple((e.base, e.variant) for e in c.engaged))
            for c in request.cases
        ]
        run = StoredRun(
            uuid7(), request.tool, request.element, request.label, self._clock.now(),
            summarize(cases, request.element),
        )  # fmt: skip
        async with self._uow(principal.space.space_id) as uow:
            await uow.outcomes.add_run(run)
        return run_out(run)

    async def runs(self, principal: Principal, cursor: str | None, limit: int) -> CounterfactualRunPage:
        async with self._uow(principal.space.space_id) as uow:
            found = await uow.outcomes.runs(before=decode_cursor(cursor), limit=limit + 1)
        page = found[:limit]
        more = len(found) > limit
        return CounterfactualRunPage(
            items=[run_out(r) for r in page],
            next_cursor=encode_cursor(page[-1].created_at, page[-1].run_id) if more else None,
        )

    async def run(self, principal: Principal, run_id: UUID) -> CounterfactualRun:
        async with self._uow(principal.space.space_id) as uow:
            found = await uow.outcomes.run(run_id)
        if found is None:
            raise NotFound("unknown counterfactual run")
        return run_out(found)

    def _measuring(self, space_id: UUID) -> SpaceConfig | None:
        try:
            config = self._config.space(space_id)
        except NotFound:
            return None
        return config if Feature.MEASUREMENT in config.features.enabled else None


def _reported(config: SpaceConfig) -> list[ElementExperiment]:
    return [e for e in config.measurement.experiments if e.kind in REPORTED_KINDS]


def _result(e: ElementExperiment, rows: Sequence[ArmRow], looks: Sequence[StoredReport]) -> ExperimentResult:
    kept: dict[tuple[str, str], list[float]] = defaultdict(list)
    for look in looks:
        if look.body.get("experiment") != e.name:
            continue
        for arm, metrics in (look.body.get("arms") or {}).items():
            for metric, p in metrics.items():
                kept[(arm, metric)].append(float(p))
    control = next((r for r in rows if r.arm == CONTROL), None)
    comparisons: list[ArmComparison] = []
    for metric in METRICS:
        coefficient = theta(_moments(r, metric) for r in rows) if e.cuped_days else None
        for row in rows:
            if control is None or row.arm == CONTROL:
                continue
            c, a = _moments(control, metric), _moments(row, metric)
            estimate = compare(c, a, coefficient)
            if estimate is None:
                continue
            tau2 = mixing_tau2(e.sequential.mixing_variance, per_person_variance(c, a, coefficient))
            test = msprt(estimate.difference, estimate.variance, tau2, e.sequential.alpha)
            if test is None:
                continue
            earlier = kept.get((row.arm, metric), [])
            p_value = min([test.p_value, *earlier])
            comparisons.append(
                ArmComparison(
                    arm=row.arm, metric=metric, control_mean=_r(estimate.control_mean),
                    arm_mean=_r(estimate.arm_mean), difference=_r(estimate.difference),
                    relative=_r(estimate.relative) if estimate.relative is not None else None,
                    ci_low=_r(test.ci_low), ci_high=_r(test.ci_high),
                    variance_reduction=_r(estimate.reduction) if estimate.reduction is not None else None,
                    p_value=_r(p_value), decided=p_value <= e.sequential.alpha, looks=len(earlier),
                )
            )  # fmt: skip
    return ExperimentResult(
        experiment=e.name,
        kind="agent" if e.kind == "agent" else "element",
        element=e.element,
        agent=e.agent,
        starts_at=e.starts_at,
        ends_at=e.ends_at,
        alpha=e.sequential.alpha,
        cuped_days=e.cuped_days,
        arms=[
            ArmOutcomes(
                arm=r.arm,
                people=r.people,
                converted=r.converted,
                outcomes=r.outcomes,
                value_minor=r.value_minor,
            )
            for r in rows
        ],
        comparisons=comparisons,
    )


def _moments(row: ArmRow, metric: str) -> Moments:
    return row.conversion if metric == "conversion" else row.value


def _sequential(config: SpaceConfig, name: str | None) -> SequentialTest:
    found = next((e for e in config.measurement.experiments if e.name == name), None)
    return found.sequential if found is not None else SequentialTest()


def _traffic_days(config: SpaceConfig, name: str, now: datetime) -> int:
    """The days the inflow is spread over: 28, or fewer for an experiment younger than that."""
    starts = [e.starts_at for e in config.measurement.experiments if e.name == name]
    starts += [e.starts_at for e in config.experiments if e.name == name]
    if not starts:
        return TRAFFIC_DAYS
    return max(1, min(TRAFFIC_DAYS, math.ceil((now - min(starts)).total_seconds() / 86400)))


def _days(people: int, daily: float) -> int:
    return math.ceil(people / daily)


def _r(value: float) -> float:
    return round(value, 6)


def run_out(run: StoredRun) -> CounterfactualRun:
    x = run.summary
    return CounterfactualRun.model_validate(
        {
            "run_id": str(run.run_id), "tool": run.tool, "element": run.element, "label": run.label,
            "created_at": run.created_at, "cases": x.cases, "completed": x.completed, "skipped": x.skipped,
            "overlap": x.overlap, "noise_floor": x.noise_floor, "effect": x.effect,
            "below_noise": x.below_noise, "above_noise": x.above_noise, "ties": x.ties, "p_value": x.p_value,
            "engaged": dataclasses.asdict(x.engaged), "limits": list(x.limits),
        }
    )  # fmt: skip
