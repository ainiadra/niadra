"""In-memory `TurnRepository` and `TurnIntakeLog` for unit tests: the same rows and rules as PostgreSQL."""

import dataclasses
from collections.abc import Collection, Mapping, Sequence
from datetime import date, datetime
from typing import Any, cast
from uuid import UUID

from niadra.adapters.memory.mentions import index_in, mentions_of
from niadra.adapters.memory.store import MemoryStore
from niadra.domain.privacy.erasure import ErasedRow, ErasureScope
from niadra.domain.tasks import Task, TaskName
from niadra.domain.turns.issues import DataIssueRow, IssueQuery, NewDataIssue, merged_refs
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
from niadra.domain.turns.storage import KeptBy
from niadra.ports.uow import UnitOfWorkFactory


class InMemoryTurnRepository:
    def __init__(self, store: MemoryStore, space_id: UUID | None) -> None:
        self._space_id = space_id
        self._outbox: list[Task] = store.module("pipeline").setdefault("outbox", [])
        s = store.module("turns")
        self._mentions = mentions_of(store)
        self._intake: dict[tuple[UUID | None, UUID], TurnIntakeRow] = s.setdefault("intake", {})
        self._kept: dict[tuple[UUID | None, str], KeptTurn] = s.setdefault("kept", {})
        self.daily_rows: dict[tuple[UUID | None, date, UUID, str, bytes], DailyTotals] = s.setdefault(
            "daily", {}
        )
        self._builds: dict[tuple[UUID | None, bytes], BuildSeen] = s.setdefault("builds", {})
        self._scenarios: dict[tuple[UUID | None, UUID], ScenarioRow] = s.setdefault("scenarios", {})
        self._runs: dict[tuple[UUID | None, UUID], ScenarioRunRow] = s.setdefault("runs", {})
        self._issues: dict[tuple[UUID | None, UUID], tuple[bytes, DataIssueRow]] = s.setdefault("issues", {})
        self._config: dict[tuple[UUID | None, str, int], ConfigSeen] = s.setdefault("config_history", {})
        self._tombstones: dict[tuple[UUID | None, str], datetime] = store.module("tombstones").setdefault(
            "marks", {}
        )

    async def accept_intake(self, intake: NewTurnIntake) -> None:
        key = (intake.space_id, intake.intake_id)
        if key in self._intake:
            return
        self._intake[key] = TurnIntakeRow(
            intake.intake_id, intake.received_at, intake.source_id, intake.body_ct, intake.dek_version
        )
        payload = {"intake_id": str(intake.intake_id)}
        self._outbox.append(Task(TaskName.APPLY_TURNS, intake.space_id, payload, id=intake.intake_id))

    async def take_intake(self, limit: int) -> TakenIntake | None:
        # Insertion order stands in for the database's sequence; one transaction at a time holds the lock.
        rows = [
            r for (space, _), r in self._intake.items() if space == self._space_id and r.failed_at is None
        ][:limit]
        return TakenIntake(rows, frozenset(bytes.fromhex(m[13:]) for m in self._marks("conversation:")))

    def _marks(self, prefix: str) -> list[str]:
        return [m for (space, m) in self._tombstones if space == self._space_id and m.startswith(prefix)]

    async def tombstone(self, marks: Sequence[str], until: datetime) -> None:
        for mark in marks:
            key = (self._space_id, mark)
            self._tombstones[key] = max(until, self._tombstones.get(key, until))

    async def wait_for_apply(self) -> None:
        return None

    async def purge_tombstones(self, before: datetime) -> int:
        doomed = [k for k, until in self._tombstones.items() if k[0] == self._space_id and until < before]
        for key in doomed:
            del self._tombstones[key]
        return len(doomed)

    async def apply(self, applied: AppliedTurns) -> None:
        for row in applied.daily:
            key = (self._space_id, *row.key)
            current = self.daily_rows.get(key)
            self.daily_rows[key] = row if current is None else _plus(current, row)
        for build in applied.builds:
            first = self._builds.get((self._space_id, build.build_hash))
            if first is None or build.at < first.at:
                self._builds[(self._space_id, build.build_hash)] = build
        await self.keep(applied.kept)
        index_in(self._mentions["objects"], applied.mentions, replace=False)
        await self.drop_intake(applied.intake_ids)
        if applied.flagged:
            assert self._space_id is not None
            payload = {"turns": list(applied.flagged)}
            self._outbox.append(Task(TaskName.NOTIFY_FLAGGED_TURNS, self._space_id, payload))
        for scan in applied.scans:
            assert self._space_id is not None
            self._outbox.append(Task(TaskName.SCAN_TURN_ARGUMENTS, self._space_id, scan))

    async def fail_intake(self, intake_id: UUID) -> int:
        row = self._intake.get((self._space_id, intake_id))
        if row is None:
            return 0
        self._intake[(self._space_id, intake_id)] = dataclasses.replace(row, attempts=row.attempts + 1)
        return row.attempts + 1

    async def drop_intake(self, intake_ids: Sequence[UUID]) -> None:
        for intake_id in intake_ids:
            self._intake.pop((self._space_id, intake_id), None)
        self._outbox[:] = [t for t in self._outbox if t.id not in set(intake_ids)]

    async def set_aside_intake(self, intake_id: UUID, at: datetime) -> None:
        row = self._intake.get((self._space_id, intake_id))
        if row is not None:
            self._intake[(self._space_id, intake_id)] = dataclasses.replace(row, failed_at=at)
        self._outbox[:] = [t for t in self._outbox if t.id != intake_id]

    async def failed_intake(
        self, limit: int, *, before: datetime | None = None, after: UUID | None = None
    ) -> list[TurnIntakeRow]:
        kept = [
            r for (space, _), r in self._intake.items() if space == self._space_id and r.failed_at is not None
        ]
        if after is not None:
            # Insertion order stands in for the database's sequence.
            ids = [r.intake_id for r in kept]
            kept = kept[ids.index(after) + 1 :] if after in ids else []
        return [r for r in kept if before is None or r.received_at < before][:limit]

    async def count_failed_intake(self) -> int:
        mine = (r for (space, _), r in self._intake.items() if space == self._space_id)
        return sum(1 for r in mine if r.failed_at is not None)

    async def retry_intake(self, intake_ids: Sequence[UUID] | None) -> list[UUID]:
        put_back = []
        for (space, intake_id), row in list(self._intake.items()):
            if space != self._space_id or row.failed_at is None:
                continue
            if intake_ids is not None and intake_id not in intake_ids:
                continue
            self._intake[(space, intake_id)] = dataclasses.replace(row, failed_at=None, attempts=0)
            put_back.append(intake_id)
        return put_back

    async def rewrite_intake(self, intake_id: UUID, body_ct: bytes, dek_version: int, turns: int) -> None:  # noqa: ARG002
        row = self._intake.get((self._space_id, intake_id))
        if row is not None:
            self._intake[(self._space_id, intake_id)] = dataclasses.replace(
                row, body_ct=body_ct, dek_version=dek_version
            )

    async def daily(self, since: date, until: date, source_id: UUID | None) -> list[DailyTotals]:
        rows = [
            row
            for (space, *_), row in self.daily_rows.items()
            if space == self._space_id
            and since <= row.day <= until
            and (source_id is None or row.source_id == source_id)
        ]
        return sorted(rows, key=lambda r: (r.day, str(r.source_id), r.agent))

    async def keep(self, turns: Sequence[KeptTurn]) -> set[str]:
        added = set()
        for turn in turns:
            if (self._space_id, turn.turn_id) not in self._kept:
                self._kept[(self._space_id, turn.turn_id)] = turn
                added.add(turn.turn_id)
        return added

    async def kept(self, turn_ids: Sequence[str]) -> list[KeptTurn]:
        return [t for i in turn_ids if (t := self._kept.get((self._space_id, i))) is not None]

    async def kept_between(
        self, since: datetime, until: datetime, *, after: str | None, limit: int
    ) -> list[KeptTurn]:
        found = [
            t
            for (space, _), t in self._kept.items()
            if space == self._space_id and since <= t.kept_at < until and (after is None or t.turn_id > after)
        ]
        return sorted(found, key=lambda t: t.turn_id)[:limit]

    async def search(self, query: TurnQuery) -> list[KeptTurn]:
        found = [
            t
            for (space, _), t in self._kept.items()
            if space == self._space_id
            and (query.conversation is None or t.conversation == query.conversation)
            and (query.agent is None or t.agent == query.agent)
            and (query.since is None or t.started_at >= query.since)
            and (query.until is None or t.started_at < query.until)
            and (not query.flags or set(t.flags) & set(query.flags))
            and (query.sources is None or t.source_id in query.sources)
            and (query.before is None or (t.started_at, t.turn_id) < query.before)
        ]
        return sorted(found, key=lambda t: (t.started_at, t.turn_id), reverse=True)[: query.limit]

    async def expire_kept(
        self,
        before: datetime,
        limit: int,
        *,
        keep: Collection[bytes] = (),
        kept_before: datetime | None = None,
        scenario_before: datetime | None = None,
    ) -> list[KeptTurn]:
        def due(t: KeptTurn) -> bool:
            if t.kept_until is not None:
                return t.kept_until < before
            cut = scenario_before if t.kept_by == KeptBy.SCENARIO else kept_before
            return cut is not None and t.kept_at < cut

        doomed = sorted(
            (
                t
                for (space, _), t in self._kept.items()
                if space == self._space_id and due(t) and t.conversation not in keep
            ),
            key=lambda t: t.kept_at,
        )[:limit]
        for turn in doomed:
            del self._kept[(self._space_id, turn.turn_id)]
        return doomed

    async def erase_for(self, scope: ErasureScope, *, limit: int) -> list[ErasedRow]:
        hashes = set(scope.conversation_hashes)
        doomed = [
            t for (space, _), t in self._kept.items() if space == self._space_id and t.conversation in hashes
        ][:limit]
        for turn in doomed:
            del self._kept[(self._space_id, turn.turn_id)]
        return [ErasedRow("turns_kept", {"turn_id": t.turn_id}) for t in doomed]

    async def keep_for_scenario(self, turns: Sequence[KeptTurn]) -> None:
        for turn in turns:
            current = self._kept.get((self._space_id, turn.turn_id))
            if current is None:
                until = turn.kept_until
            elif current.kept_until is None or turn.kept_until is None:
                until = None
            else:
                until = max(turn.kept_until, current.kept_until)
            self._kept[(self._space_id, turn.turn_id)] = dataclasses.replace(
                current or turn, kept_by=KeptBy.SCENARIO, kept_until=until
            )

    async def renew_scenario_turns(self, turn_ids: Sequence[str], until: datetime | None) -> None:
        for turn_id in turn_ids:
            row = self._kept.get((self._space_id, turn_id))
            if row is None or row.kept_by != KeptBy.SCENARIO or row.kept_until is None:
                continue
            if until is None or row.kept_until < until:
                self._kept[(self._space_id, turn_id)] = dataclasses.replace(row, kept_until=until)

    async def add_scenario(self, scenario: ScenarioRow) -> ScenarioRow:
        return self._scenarios.setdefault((self._space_id, scenario.scenario_id), scenario)

    async def scenarios(
        self,
        *,
        ids: Sequence[UUID] | None = None,
        status: str | None = None,
        before: tuple[datetime, UUID] | None = None,
        limit: int = 50,
    ) -> list[ScenarioRow]:
        found = [
            s
            for (space, _), s in self._scenarios.items()
            if space == self._space_id
            and (ids is None or s.scenario_id in ids)
            and (status is None or s.status == status)
            and (before is None or (s.created_at, s.scenario_id) < before)
        ]
        return sorted(found, key=lambda s: (s.created_at, s.scenario_id), reverse=True)[:limit]

    async def change_scenario(
        self, scenario_id: UUID, change: ScenarioChange, at: datetime
    ) -> ScenarioRow | None:
        current = self._scenarios.get((self._space_id, scenario_id))
        if current is None:
            return None
        changed = dataclasses.replace(
            current,
            name=change.name if change.name is not None else current.name,
            assertions=change.assertions if change.assertions is not None else current.assertions,
            status=change.status if change.status is not None else current.status,
            version=current.version + 1,
            updated_at=at,
        )
        self._scenarios[(self._space_id, scenario_id)] = changed
        return changed

    async def add_run(self, run: ScenarioRunRow) -> ScenarioRunRow:
        return self._runs.setdefault((self._space_id, run.run_id), run)

    async def run(self, run_id: UUID) -> ScenarioRunRow | None:
        return self._runs.get((self._space_id, run_id))

    async def runs_of(
        self, scenario_ids: Sequence[UUID], before: datetime, limit: int
    ) -> list[ScenarioRunRow]:
        found = [
            r
            for (space, _), r in self._runs.items()
            if space == self._space_id and set(scenario_ids) & set(r.scenario_ids) and r.created_at < before
        ]
        return sorted(found, key=lambda r: (r.created_at, r.run_id), reverse=True)[:limit]

    async def build_pins(self, build_hashes: Sequence[bytes]) -> dict[bytes, dict[str, Any]]:
        return {
            h: dict(b.pins) for h in build_hashes if (b := self._builds.get((self._space_id, h))) is not None
        }

    async def builds(self, limit: int) -> list[BuildSeen]:
        found = [b for (space, _), b in self._builds.items() if space == self._space_id]
        return sorted(found, key=lambda b: (-b.at.timestamp(), b.build_hash))[:limit]

    async def record_config(self, sections: Mapping[str, bytes], version: int, at: datetime) -> None:
        for name, digest in sorted(sections.items()):
            recorded = [
                s
                for (space, section, _), s in self._config.items()
                if (space, section) == (self._space_id, name)
            ]
            last = max(recorded, key=lambda s: s.version, default=None)
            if last is None or last.digest != digest:
                self._config.setdefault(
                    (self._space_id, name, version), ConfigSeen(name, version, at, digest)
                )

    async def config_history(self, limit: int) -> list[ConfigSeen]:
        found = [s for (space, _, _), s in self._config.items() if space == self._space_id]
        return sorted(found, key=lambda s: (-s.version, s.section))[:limit]

    async def open_issue(self, issue: NewDataIssue, *, tell: bool) -> UUID:
        for key, (fingerprint, row) in self._issues.items():
            if key[0] == self._space_id and fingerprint == issue.fingerprint and row.acknowledged_at is None:
                self._issues[key] = (
                    fingerprint,
                    dataclasses.replace(
                        row,
                        occurrences=row.occurrences + 1,
                        last_seen_at=max(row.last_seen_at, issue.at),
                        refs=merged_refs(row.refs, issue.refs),
                    ),
                )
                return row.issue_id
        self._issues[(self._space_id, issue.issue_id)] = (
            issue.fingerprint,
            DataIssueRow(
                issue_id=issue.issue_id,
                kind=issue.kind,
                object_type=issue.object_type,
                field=issue.field,
                source=issue.source,
                refs=issue.refs,
                occurrences=1,
                opened_at=issue.at,
                last_seen_at=issue.at,
            ),
        )
        if tell:
            assert self._space_id is not None
            payload = {"issue_id": str(issue.issue_id)}
            self._outbox.append(Task(TaskName.NOTIFY_DATA_ISSUE, self._space_id, payload))
        return issue.issue_id

    async def issues(self, query: IssueQuery) -> list[DataIssueRow]:
        found = [
            row
            for (space, _), (_, row) in self._issues.items()
            if space == self._space_id
            and (query.open is None or (row.acknowledged_at is None) == query.open)
            and (query.kind is None or row.kind == query.kind)
            and (query.before is None or (row.opened_at, row.issue_id) < query.before)
        ]
        return sorted(found, key=lambda r: (r.opened_at, r.issue_id), reverse=True)[: query.limit]

    async def issue(self, issue_id: UUID) -> DataIssueRow | None:
        found = self._issues.get((self._space_id, issue_id))
        return found[1] if found is not None else None

    async def acknowledge_issue(self, issue_id: UUID, by: str, at: datetime) -> DataIssueRow | None:
        found = self._issues.get((self._space_id, issue_id))
        if found is None:
            return None
        fingerprint, row = found
        if row.acknowledged_at is None:
            row = dataclasses.replace(row, acknowledged_at=at, acknowledged_by=by)
            self._issues[(self._space_id, issue_id)] = (fingerprint, row)
        return row


class TransactionTurnIntakeLog:
    """`TurnIntakeLog` as an ordinary transaction of the unit of work: the same row as PostgreSQL's
    single statement."""

    def __init__(self, uow: UnitOfWorkFactory) -> None:
        self._uow = uow

    async def accept(self, intake: NewTurnIntake) -> None:
        async with self._uow(intake.space_id) as uow:
            await cast(InMemoryTurnRepository, uow.turns).accept_intake(intake)


def _plus(a: DailyTotals, b: DailyTotals) -> DailyTotals:
    counters = {
        f.name: getattr(a, f.name) + getattr(b, f.name)
        for f in dataclasses.fields(DailyTotals)
        if f.type == "int" or f.type is int
    }
    width = max(len(a.latency_buckets), len(b.latency_buckets))
    buckets = [
        (a.latency_buckets[i] if i < len(a.latency_buckets) else 0)
        + (b.latency_buckets[i] if i < len(b.latency_buckets) else 0)
        for i in range(width)
    ]
    claims = {k: a.claims.get(k, 0) + b.claims.get(k, 0) for k in a.claims.keys() | b.claims.keys()}
    return dataclasses.replace(a, **counters, latency_buckets=buckets, claims=claims)
