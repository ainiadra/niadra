"""The persistence of the measure module (6.7, 24.7): outcome links and their daily totals, interleaving,
counterfactual runs and reports. Nothing here is sealed: a link holds ids, states, methods and money, never
content; a run or a report, counts."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Protocol
from uuid import UUID

from niadra.domain.measure.attribution import Link
from niadra.domain.measure.counterfactual import Summary
from niadra.domain.measure.stats import Moments
from niadra.domain.privacy.erasure import ErasedRow, ErasureScope
from niadra.domain.signals.daily import Interleaved


@dataclass(frozen=True, slots=True)
class StoredLink:
    link: Link
    known_at: datetime


@dataclass(frozen=True, slots=True)
class ArmRow:
    """One arm of an experiment: its people and, per metric, the moments of what each did after being
    assigned (`y`) and over the covariate's days before (`x`)."""

    arm: str
    people: int
    converted: int
    outcomes: int
    value_minor: int
    conversion: Moments
    value: Moments


@dataclass(frozen=True, slots=True)
class StoredRun:
    run_id: UUID
    tool: str
    element: str
    label: str | None
    created_at: datetime
    summary: Summary


@dataclass(frozen=True, slots=True)
class StoredReport:
    period_start: date
    period_end: date
    body: dict[str, Any]


class OutcomeRepository(Protocol):
    async def write(self, object_ids: Sequence[UUID], links: Sequence[Link], at: datetime) -> list[date]:
        """One statement: the links of `object_ids` become `links`, a link that stays keeping the moment it
        first counted. Returns the days whose totals moved."""
        ...

    async def roll_up(self, days: Sequence[date]) -> None:
        """`attribution_daily` of these days, from the links, in one statement."""
        ...

    async def finalize(self, now: datetime, *, limit: int) -> list[date]:
        """Provisional links past their `final_at` become final; the days whose totals moved."""
        ...

    async def next_due(self) -> datetime | None:
        """The earliest end of an exchange window still ahead, for the loop's pace."""
        ...

    async def links(self, *, before: tuple[datetime, UUID] | None, limit: int) -> list[StoredLink]:
        """The newest links first, by the moment they were last known."""
        ...

    async def daily(self, since: date, until: date) -> list[dict[str, Any]]: ...

    async def line_values(self, since: date, until: date) -> list[tuple[str, str, int]]:
        """What each line of a deterministic outcome with a value is worth, as (the order it counts in, line,
        value), each object's line once, for the reconciliation with the company's BI."""
        ...

    async def agreement(self, since: date, until: date) -> tuple[int, int]:
        """Of the lines a token and the identity method both attribute, how many name the same exposure."""
        ...

    async def expire(self, before: datetime, *, limit: int) -> list[date]:
        """Links older than their retention (13 months, 24.5) leave; the days whose totals moved."""
        ...

    async def put_interleaved(self, day: date, counts: Mapping[tuple[str, str], Interleaved]) -> None:
        """The day's interleaved impressions by (experiment, tool), in one statement; a rerun replaces
        them."""
        ...

    async def interleaved(self, since: date, until: date) -> list[dict[str, Any]]: ...

    async def add_report(
        self, kind: str, since: date, until: date, body: dict[str, Any], at: datetime
    ) -> UUID:
        """A report kept with its evidence (`measurement_reports`): a reconciliation, an interleaving."""
        ...

    async def reports(self, kind: str, since: date) -> list[StoredReport]:
        """The reports of a kind whose period ends on or after `since`, oldest first."""
        ...

    async def last_report(self, kind: str) -> date | None:
        """The end of the latest report of a kind."""
        ...

    async def arms(
        self,
        experiment: str,
        kind: str,
        until: datetime,
        covariate_days: int,
        success: Sequence[tuple[str, str]],
    ) -> list[ArmRow]:
        """Each arm's people assigned before `until` and the moments of their outcomes in a `success` state
        (outcome, state), from their assignment to `until` and over the `covariate_days` before it, each line
        once: one statement. A read model over the assignments and the profiles' handles, which the serve and
        identity modules own, as the insights read models are."""
        ...

    async def inflow(self, since: datetime) -> list[tuple[str, str, int]]:
        """`(experiment, kind, people)` first assigned since then, per experiment."""
        ...

    async def add_run(self, run: StoredRun) -> None: ...

    async def runs(self, *, before: tuple[datetime, UUID] | None, limit: int) -> list[StoredRun]:
        """Counterfactual runs, newest first."""
        ...

    async def run(self, run_id: UUID) -> StoredRun | None: ...

    async def written_since(self, since: datetime, *, limit: int) -> list[str]:
        """The objects (`type:namespace:id`) whose links were written at or after `since`."""
        ...

    async def links_of(self, handle_ids: Sequence[UUID]) -> list[StoredLink]:
        """The subject's package."""
        ...

    async def erase_for(self, scope: ErasureScope, *, limit: int) -> list[ErasedRow]:
        """The links and waiting objects of the target's handles; a conversation's erasure leaves them."""
        ...
