"""In-memory `OutcomeRepository` for unit tests."""

from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from typing import Any
from uuid import UUID

from niadra.adapters.memory.store import MemoryStore
from niadra.domain.common.ids import uuid7
from niadra.domain.measure.attribution import EXPIRED, FINAL, PROVISIONAL, Link
from niadra.domain.measure.stats import of_pairs
from niadra.domain.privacy.erasure import ErasedRow, ErasureScope, TargetKind
from niadra.domain.signals.daily import Interleaved
from niadra.ports.repos.outcomes import ArmRow, StoredLink, StoredReport, StoredRun

_CHANGES = ("state", "finality", "value_minor", "currency", "turn_id", "agent", "confidence", "order_ref")


def _day(at: datetime) -> date:
    return at.astimezone(UTC).date()


class InMemoryOutcomeRepository:
    def __init__(self, store: MemoryStore) -> None:
        self._s = store.module("outcomes")
        self.rows: dict[UUID, StoredLink] = self._s.setdefault("links", {})
        self.daily_rows: dict[tuple[Any, ...], list[int]] = self._s.setdefault("daily", {})
        self.report_rows: dict[UUID, tuple[Any, ...]] = self._s.setdefault("reports", {})
        self.interleaved_rows: dict[tuple[date, str, str], Interleaved] = self._s.setdefault(
            "interleaved", {}
        )
        self.runs_kept: dict[UUID, StoredRun] = self._s.setdefault("runs", {})
        self.statements: list[str] = self._s.setdefault("statements", [])
        # The read model of `arms` crosses into the serve and identity modules' rows, as its SQL does.
        self._store = store

    async def write(self, object_ids: Sequence[UUID], links: Sequence[Link], at: datetime) -> list[date]:
        if not object_ids:
            return []
        self.statements.append("write")
        fresh = {x.link_id: x for x in links}
        days: set[date] = set()
        for link_id, stored in list(self.rows.items()):
            if stored.link.object_id in object_ids and link_id not in fresh:
                days.add(_day(stored.link.valid_at))
                del self.rows[link_id]
        for link in links:
            before = self.rows.get(link.link_id)
            if before is not None:
                if all(getattr(before.link, k) == getattr(link, k) for k in _CHANGES):
                    continue
                valid = min(before.link.valid_at, link.valid_at)
                final_at = None if link.final_at is None else valid + (link.final_at - link.valid_at)
                link = replace(link, valid_at=valid, final_at=final_at)
            self.rows[link.link_id] = StoredLink(link, at)
            days.add(_day(link.valid_at))
        return sorted(days)

    async def roll_up(self, days: Sequence[date]) -> None:
        if not days:
            return
        self.statements.append("roll_up")
        wanted = set(days)
        for key in [k for k in self.daily_rows if k[0] in wanted]:
            del self.daily_rows[key]
        objects: dict[tuple[Any, ...], set[str]] = defaultdict(set)
        for stored in self.rows.values():
            x = stored.link
            if _day(x.valid_at) not in wanted:
                continue
            key = (_day(x.valid_at), x.outcome, x.agent or "", x.method, x.band, x.finality, x.currency or "")
            acc = self.daily_rows.setdefault(key, [0, 0, 0])
            objects[key].add(x.order_ref or str(x.object_id))
            acc[0] = len(objects[key])
            acc[1] += 1
            acc[2] += x.value_minor or 0

    async def finalize(self, now: datetime, *, limit: int) -> list[date]:
        self.statements.append("finalize")
        due = sorted(
            (s for s in self.rows.values() if s.link.finality == PROVISIONAL and s.link.final_at is not None),
            key=lambda s: s.link.final_at or now,
        )
        days = set()
        for stored in [s for s in due if s.link.final_at and s.link.final_at <= now][:limit]:
            self.rows[stored.link.link_id] = StoredLink(
                replace(stored.link, finality=FINAL, final_at=None), now
            )
            days.add(_day(stored.link.valid_at))
        return sorted(days)

    async def next_due(self) -> datetime | None:
        ends = [
            s.link.final_at for s in self.rows.values() if s.link.finality == PROVISIONAL and s.link.final_at
        ]
        return min(ends, default=None)

    async def links(self, *, before: tuple[datetime, UUID] | None, limit: int) -> list[StoredLink]:
        found = sorted(self.rows.values(), key=lambda s: (s.known_at, s.link.link_id), reverse=True)
        if before is not None:
            found = [s for s in found if (s.known_at, s.link.link_id) < before]
        return found[:limit]

    async def daily(self, since: date, until: date) -> list[dict[str, Any]]:
        names = ("day", "outcome", "agent", "method", "band", "finality", "currency")
        return [
            dict(zip(names, key, strict=True)) | {"outcomes": acc[0], "lines": acc[1], "value_minor": acc[2]}
            for key, acc in sorted(self.daily_rows.items())
            if since <= key[0] <= until
        ]

    async def line_values(self, since: date, until: date) -> list[tuple[str, str, int]]:
        found: dict[tuple[str, str], tuple[datetime, int, str]] = {}
        for stored in self.rows.values():
            x = stored.link
            if (
                x.band != "deterministic"
                or x.finality == EXPIRED
                or x.value_minor is None
                or x.currency is None
            ):
                continue
            if not since <= _day(x.valid_at) <= until:
                continue
            key = (x.object_ref, x.line_ref or "")
            if key not in found or found[key][0] < stored.known_at:
                found[key] = (stored.known_at, x.value_minor, x.order_ref or x.object_ref)
        return [(order, line, value) for (_, line), (_, value, order) in sorted(found.items())]

    async def agreement(self, since: date, until: date) -> tuple[int, int]:
        by_line: dict[tuple[UUID, str | None], dict[str, str | None]] = defaultdict(dict)
        for stored in self.rows.values():
            x = stored.link
            if x.method in ("line", "identity") and since <= _day(x.valid_at) <= until:
                by_line[(x.object_id, x.line_ref)][x.method] = x.exposure_id
        both = [m for m in by_line.values() if "line" in m and "identity" in m]
        return sum(1 for m in both if m["line"] == m["identity"]), len(both)

    async def expire(self, before: datetime, *, limit: int) -> list[date]:
        old = sorted(
            (s for s in self.rows.values() if s.link.valid_at < before), key=lambda s: s.link.valid_at
        )
        days = set()
        for stored in old[:limit]:
            del self.rows[stored.link.link_id]
            days.add(_day(stored.link.valid_at))
        return sorted(days)

    async def put_interleaved(self, day: date, counts: Mapping[tuple[str, str], Interleaved]) -> None:
        for (experiment, tool), c in counts.items():
            self.interleaved_rows[(day, experiment, tool)] = c

    async def interleaved(self, since: date, until: date) -> list[dict[str, Any]]:
        return [
            {"day": day, "experiment": experiment, "tool": tool, "impressions": c.impressions,
             "wins_a": c.wins_a, "wins_b": c.wins_b, "ties": c.ties}
            for (day, experiment, tool), c in sorted(self.interleaved_rows.items())
            if since <= day <= until
        ]  # fmt: skip

    async def add_report(
        self, kind: str, since: date, until: date, body: dict[str, Any], at: datetime
    ) -> UUID:
        report_id = uuid7()
        self.report_rows[report_id] = (kind, since, until, body, at)
        return report_id

    async def reports(self, kind: str, since: date) -> list[StoredReport]:
        found = sorted(
            (
                (r[2], rid, StoredReport(r[1], r[2], r[3]))
                for rid, r in self.report_rows.items()
                if r[0] == kind
            ),
            key=lambda x: (x[0], x[1]),
        )
        return [x[2] for x in found if x[2].period_end >= since]

    async def last_report(self, kind: str) -> date | None:
        return max((r[2] for r in self.report_rows.values() if r[0] == kind), default=None)

    async def arms(
        self,
        experiment: str,
        kind: str,
        until: datetime,
        covariate_days: int,
        success: Sequence[tuple[str, str]],
    ) -> list[ArmRow]:
        self.statements.append("arms")
        assigned = {
            profile_id: (group, at)
            for (name, profile_id), (group, at, of_kind) in self._store.module("serve")
            .get("assignments", {})
            .items()
            if name == experiment and of_kind == kind and at < until
        }
        membership = self._store.module("identity").get("membership", {})
        wanted = set(success)
        lines: dict[UUID, dict[tuple[str, str, str], tuple[int, datetime]]] = defaultdict(dict)
        for stored in self.rows.values():
            x = stored.link
            member = membership.get(x.handle_id)
            if member is None or member[0] not in assigned or x.finality == EXPIRED:
                continue
            if (x.outcome, x.state) not in wanted:
                continue
            at = assigned[member[0]][1]
            if not at - timedelta(days=covariate_days) <= x.valid_at < until:
                continue
            key = (x.order_ref or str(x.object_id), str(x.object_id), x.line_ref or "")
            value, first = lines[member[0]].get(key, (0, x.valid_at))
            lines[member[0]][key] = (max(value, x.value_minor or 0), min(first, x.valid_at))
        by_arm: dict[str, list[tuple[int, int, int, int, int]]] = defaultdict(list)
        for profile_id, (arm, at) in assigned.items():
            found = lines.get(profile_id, {})
            after = {k: v for k, v in found.items() if v[1] >= at}
            before = {k: v for k, v in found.items() if v[1] < at}
            yv = sum(v[0] for v in after.values())
            xv = sum(v[0] for v in before.values())
            by_arm[arm].append((len({k[0] for k in after}), int(bool(after)), int(bool(before)), yv, xv))
        return [
            ArmRow(
                arm=arm, people=len(people), converted=sum(p[1] for p in people),
                outcomes=sum(p[0] for p in people), value_minor=sum(p[3] for p in people),
                conversion=of_pairs((p[1], p[2]) for p in people),
                value=of_pairs((p[3], p[4]) for p in people),
            )
            for arm, people in sorted(by_arm.items())
        ]  # fmt: skip

    async def inflow(self, since: datetime) -> list[tuple[str, str, int]]:
        counts: dict[tuple[str, str], int] = defaultdict(int)
        for (experiment, _), (_, at, kind) in self._store.module("serve").get("assignments", {}).items():
            if at >= since:
                counts[(experiment, kind)] += 1
        return [(e, k, n) for (e, k), n in sorted(counts.items())]

    async def add_run(self, run: StoredRun) -> None:
        self.runs_kept[run.run_id] = run

    async def runs(self, *, before: tuple[datetime, UUID] | None, limit: int) -> list[StoredRun]:
        found = sorted(self.runs_kept.values(), key=lambda r: (r.created_at, r.run_id), reverse=True)
        if before is not None:
            found = [r for r in found if (r.created_at, r.run_id) < before]
        return found[:limit]

    async def run(self, run_id: UUID) -> StoredRun | None:
        return self.runs_kept.get(run_id)

    async def written_since(self, since: datetime, *, limit: int) -> list[str]:
        return sorted({s.link.object_ref for s in self.rows.values() if s.known_at >= since})[:limit]

    async def links_of(self, handle_ids: Sequence[UUID]) -> list[StoredLink]:
        return sorted(
            (s for s in self.rows.values() if s.link.handle_id in handle_ids),
            key=lambda s: (s.link.valid_at, s.link.link_id),
        )

    async def erase_for(self, scope: ErasureScope, *, limit: int) -> list[ErasedRow]:
        if scope.event_ids or scope.kind is TargetKind.CONVERSATION or not scope.handle_ids:
            return []
        erased = []
        for link_id in [k for k, s in self.rows.items() if s.link.handle_id in scope.handle_ids][:limit]:
            del self.rows[link_id]
            erased.append(ErasedRow("outcome_links", {"link_id": str(link_id)}))
        return erased
