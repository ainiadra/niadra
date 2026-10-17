"""In-memory `ExtractionRunRepository` for unit tests."""

from collections.abc import Sequence
from dataclasses import replace
from datetime import date, datetime
from typing import Any
from uuid import UUID

from niadra.adapters.memory.store import MemoryStore
from niadra.domain.extract.result import ExtractionResult, ExtractionRun, RunStatus
from niadra.domain.privacy.erasure import ErasedRow, ErasureScope

NOTICE_SUFFIX = ":notice"


class InMemoryExtractionRunRepository:
    def __init__(self, store: MemoryStore) -> None:
        self._s = store.module("extract")
        self._s.setdefault("runs", {})
        self._s.setdefault("spend", {})
        # (normalized text, session) -> (profile, seen at); the in-memory store keeps the text itself.

    @property
    def _runs(self) -> dict[tuple[UUID, str], tuple[ExtractionRun, dict[str, Any] | None]]:
        return self._s["runs"]  # type: ignore[no-any-return]

    async def save_run(self, run: ExtractionRun) -> None:
        # Results are kept serialized, as in PostgreSQL, so a shape that cannot round-trip fails here too.
        key = (run.session_id, run.versions_key)
        stored = replace(run, result=None)
        if key in self._runs:
            before = self._runs[key][0]
            stored = replace(
                stored,
                input_tokens=before.input_tokens + run.input_tokens,
                output_tokens=before.output_tokens + run.output_tokens,
                cached_tokens=before.cached_tokens + run.cached_tokens,
                cost_micros=before.cost_micros + run.cost_micros,
            )
        self._runs[key] = (stored, run.result.to_dict() if run.result else None)

    async def get_run(self, session_id: UUID, versions_key: str) -> ExtractionRun | None:
        found = self._runs.get((session_id, versions_key))
        return _with_result(*found) if found else None

    async def mark_applied(
        self, session_id: UUID, versions_key: str, profile_id: UUID | None, work: int
    ) -> None:
        key = (session_id, versions_key)
        if key in self._runs:
            run, result = self._runs[key]
            self._runs[key] = (replace(run, applied_profile_id=profile_id, applied_work=work), result)

    async def latest_run(self, session_id: UUID) -> ExtractionRun | None:
        runs = [v for (s, _), v in self._runs.items() if s == session_id]
        if not runs:
            return None
        return _with_result(*max(runs, key=lambda v: (v[0].created_at, v[0].versions_key)))

    async def runs_page(self, after: tuple[UUID, str] | None, limit: int) -> list[ExtractionRun]:
        keys = sorted(
            (k for k, (_, result) in self._runs.items() if result is not None),
            key=lambda k: (str(k[0]), k[1]),
        )
        if after is not None:
            keys = [k for k in keys if (str(k[0]), k[1]) > (str(after[0]), after[1])]
        return [_with_result(*self._runs[k]) for k in keys[:limit]]

    async def rewrite_result(self, run: ExtractionRun) -> None:
        key = (run.session_id, run.versions_key)
        if key in self._runs and run.result is not None:
            stored = replace(self._runs[key][0], dropped_items=run.dropped_items)
            self._runs[key] = (stored, run.result.to_dict())

    async def run_keys(self, session_ids: Sequence[UUID]) -> dict[UUID, list[tuple[str, RunStatus]]]:
        wanted = set(session_ids)
        found: dict[UUID, list[tuple[str, RunStatus]]] = {}
        for (session_id, versions_key), (run, _) in self._runs.items():
            if session_id in wanted:
                found.setdefault(session_id, []).append((versions_key, run.status))
        return found

    async def extractor_state(self) -> tuple[int | None, datetime | None]:
        return self._s.get("extractor_fingerprint"), self._s.get("extractor_changed_at")

    async def set_extractor_state(self, fingerprint: int, changed_at: datetime | None) -> None:
        self._s["extractor_fingerprint"] = fingerprint
        if changed_at is not None:
            self._s["extractor_changed_at"] = changed_at

    async def spent(self, day: date, purpose: str) -> int:
        return int(self._s["spend"].get((day, purpose), (0, 0))[0])

    async def add_spend(self, day: date, purpose: str, cost_micros: int, *, calls: int = 1) -> None:
        cost, count = self._s["spend"].get((day, purpose), (0, 0))
        self._s["spend"][(day, purpose)] = (cost + cost_micros, count + calls)

    async def reserve_spend(self, day: date, purpose: str, cost_micros: int, cap_micros: int) -> bool:
        cost, count = self._s["spend"].get((day, purpose), (0, 0))
        if cost >= cap_micros:
            return False
        self._s["spend"][(day, purpose)] = (cost + cost_micros, count)
        return True

    async def mark_budget_notice(self, day: date, purpose: str) -> bool:
        key = (day, f"{purpose}{NOTICE_SUFFIX}")
        if key in self._s["spend"]:
            return False
        self._s["spend"][key] = (0, 0)
        return True

    async def erase_for(self, scope: ErasureScope, *, limit: int) -> list[ErasedRow]:
        sessions = set(scope.session_ids)
        doomed = sorted(k for k, (_, result) in self._runs.items() if k[0] in sessions and result)[:limit]
        for key in doomed:
            self._runs[key] = (self._runs[key][0], None)
        return [ErasedRow("extraction_runs", {"session_id": str(s), "versions_key": v}) for s, v in doomed]


def _with_result(run: ExtractionRun, result: dict[str, Any] | None) -> ExtractionRun:
    return replace(run, result=ExtractionResult.from_dict(result) if result else None)
