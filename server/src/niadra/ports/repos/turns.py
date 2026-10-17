"""The `turns` module's persistence (6.7): the intake of `POST /v1/turns`, the index of the kept tier and the
day's totals. The records themselves live in object storage."""

from collections.abc import Collection, Mapping, Sequence
from datetime import date, datetime
from typing import Any, Protocol
from uuid import UUID

from niadra.domain.privacy.erasure import ErasedRow, ErasureScope
from niadra.domain.turns.issues import DataIssueRow, IssueQuery, NewDataIssue
from niadra.domain.turns.records import (
    AppliedTurns,
    BuildSeen,
    DailyTotals,
    KeptTurn,
    NewTurnIntake,
    ScenarioChange,
    ScenarioRow,
    ScenarioRunRow,
    TakenIntake,
    TurnIntakeRow,
    TurnQuery,
)
from niadra.domain.turns.replay import ConfigSeen


class TurnIntakeLog(Protocol):
    """`accept` as a transaction of its own, in a single statement: the only database write a
    `POST /v1/turns` waits for (24.6). Idempotent by intake id."""

    async def accept(self, intake: NewTurnIntake) -> None: ...


class TurnRepository(Protocol):
    async def take_intake(self, limit: int) -> TakenIntake | None:
        """The oldest intakes, up to `limit`, under the space's intake lock for the transaction, with the
        conversations an erasure fenced off; None, having taken nothing, when another transaction holds it.
        One statement."""
        ...

    async def tombstone(self, marks: Sequence[str], until: datetime) -> None:
        """Fences off what an erasure takes (`conversation:<hex>`, `needle:<id>`) until `until`, when the
        retention sweep lets it go; one statement."""
        ...

    async def purge_tombstones(self, before: datetime) -> int: ...

    async def wait_for_apply(self) -> None:
        """Waits, in the caller's transaction, until no worker is adding the space's intakes up."""
        ...

    async def apply(self, applied: AppliedTurns) -> None:
        """The group's totals, builds, tool schemas and kept rows, and the intakes' removal with their outbox
        copies, in one statement."""
        ...

    async def fail_intake(self, intake_id: UUID) -> int:
        """One more failed attempt on an intake; returns the attempts so far."""
        ...

    async def drop_intake(self, intake_ids: Sequence[UUID]) -> None: ...

    async def set_aside_intake(self, intake_id: UUID, at: datetime) -> None:
        """Keeps an intake that failed every attempt out of the space's order (`failed_at`), sealed as it
        came, and deletes its outbox copy. Never deletes the intake."""
        ...

    async def failed_intake(
        self, limit: int, *, before: datetime | None = None, after: UUID | None = None
    ) -> list[TurnIntakeRow]:
        """Intakes set aside, oldest first; with `before`, only those received before it; with `after`, only
        those that came after that intake (a cursor)."""
        ...

    async def count_failed_intake(self) -> int:
        """How many intakes are set aside, every one, whatever a listing showed."""
        ...

    async def retry_intake(self, intake_ids: Sequence[UUID] | None) -> list[UUID]:
        """Puts intakes set aside back in the space's order with no attempts counted, all of them when
        `intake_ids` is None; returns the ids put back."""
        ...

    async def rewrite_intake(self, intake_id: UUID, body_ct: bytes, dek_version: int, turns: int) -> None:
        """Replaces an intake's sealed body and its count of turns: an erasure took some of them."""
        ...

    async def daily(self, since: date, until: date, source_id: UUID | None) -> list[DailyTotals]:
        """The day's totals from `since` to `until`, both included, for one source or all; one statement.
        The measurement reads these, never the turns."""
        ...

    async def keep(self, turns: Sequence[KeptTurn]) -> set[str]:
        """Adds kept rows, one statement; returns the turn ids that were not kept before."""
        ...

    async def kept(self, turn_ids: Sequence[str]) -> list[KeptTurn]: ...

    async def kept_between(
        self, since: datetime, until: datetime, *, after: str | None, limit: int
    ) -> list[KeptTurn]:
        """The rows kept in [`since`, `until`), in turn id order after `after`; one statement."""
        ...

    async def search(self, query: TurnQuery) -> list[KeptTurn]:
        """The kept tier only, newest first, keyset paged; one statement."""
        ...

    async def expire_kept(
        self,
        before: datetime,
        limit: int,
        *,
        keep: Collection[bytes] = (),
        kept_before: datetime | None = None,
        scenario_before: datetime | None = None,
    ) -> list[KeptTurn]:
        """Deletes up to `limit` kept rows whose `kept_until` passed and returns them. A row kept with no
        `kept_until` goes only once a retention is set: a non-scenario row kept before `kept_before`, a
        scenario's before `scenario_before`. A conversation whose keyed hash is in `keep` is under a legal
        hold, and its rows stay."""
        ...

    async def erase_for(self, scope: ErasureScope, *, limit: int) -> list[ErasedRow]:
        """Kept rows of the conversations in `scope.conversation_hashes`, with their objects' prefixes."""
        ...

    async def keep_for_scenario(self, turns: Sequence[KeptTurn]) -> None:
        """Kept rows for a scenario's turns, one statement: a turn kept already becomes the scenario's, and
        keeps the later of the two `kept_until`."""
        ...

    async def renew_scenario_turns(self, turn_ids: Sequence[str], until: datetime | None) -> None:
        """Moves the scenario turns' `kept_until` to at least `until` (None: kept until a retention is set);
        one statement."""
        ...

    async def add_scenario(self, scenario: ScenarioRow) -> ScenarioRow:
        """Adds a scenario; the one already stored under its id, for a retried request."""
        ...

    async def scenarios(
        self,
        *,
        ids: Sequence[UUID] | None = None,
        status: str | None = None,
        before: tuple[datetime, UUID] | None = None,
        limit: int = 50,
    ) -> list[ScenarioRow]:
        """By id, or a status's, newest first and keyset paged by (created_at, scenario_id); one statement."""
        ...

    async def change_scenario(
        self, scenario_id: UUID, change: ScenarioChange, at: datetime
    ) -> ScenarioRow | None:
        """Applies `change` and adds one to the version; None when the scenario does not exist."""
        ...

    async def add_run(self, run: ScenarioRunRow) -> ScenarioRunRow:
        """Adds a run; the one already stored under its id, for a retried request."""
        ...

    async def run(self, run_id: UUID) -> ScenarioRunRow | None: ...

    async def runs_of(
        self, scenario_ids: Sequence[UUID], before: datetime, limit: int
    ) -> list[ScenarioRunRow]:
        """The runs that included any of the scenarios, created before `before`, newest first; one
        statement."""
        ...

    async def build_pins(self, build_hashes: Sequence[bytes]) -> dict[bytes, dict[str, Any]]:
        """The pins of each build by its hash; one statement."""
        ...

    async def builds(self, limit: int) -> list[BuildSeen]:
        """The pinned builds, newest first by when they were first seen (`at`); one statement."""
        ...

    async def record_config(self, sections: Mapping[str, bytes], version: int, at: datetime) -> None:
        """A row for each section whose digest differs from the last one recorded for it; one statement."""
        ...

    async def config_history(self, limit: int) -> list[ConfigSeen]:
        """The newest rows, by snapshot version; one statement."""
        ...

    async def open_issue(self, issue: NewDataIssue, *, tell: bool) -> UUID:
        """Counts the occurrence in the open issue of its fingerprint, taking the new references up to 50, or
        opens one, in one statement; with `tell`, the same statement adds the task that announces a new
        issue. Returns the open issue's id."""
        ...

    async def issues(self, query: IssueQuery) -> list[DataIssueRow]:
        """One statement."""
        ...

    async def issue(self, issue_id: UUID) -> DataIssueRow | None: ...

    async def acknowledge_issue(self, issue_id: UUID, by: str, at: datetime) -> DataIssueRow | None:
        """Closes an open issue; an acknowledged one stays as it was. None when there is no such issue."""
        ...
