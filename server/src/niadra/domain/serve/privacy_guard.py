"""The privacy feed, held in each read process (06, 3).

Erasure, unmerge, retraction and revocation raise the profile's privacy version and write a
feed row in the same transaction. Every read process polls the feed on the writer every 2 s
over the last 120 s (row ids dedupe the overlap), because no write transaction outlives 60 s
and a row committed late still falls inside the window. Profiles and handles stay guarded
for 72 h, long after every cache that could hold stale state has expired.
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

REFRESH_EVERY = timedelta(seconds=2)
WINDOW = timedelta(seconds=120)
GUARD_FOR = timedelta(hours=72)
STALE_AFTER = timedelta(seconds=30)


@dataclass(frozen=True, slots=True)
class HandleMark:
    kind: str
    at: datetime


class PrivacyGuard:
    def __init__(self) -> None:
        self._seen: dict[UUID, datetime] = {}
        self._profiles: dict[UUID, tuple[int, datetime]] = {}
        self._handles: dict[UUID, HandleMark] = {}
        self.refreshed_at: datetime | None = None

    def due(self, now: datetime) -> bool:
        return self.refreshed_at is None or now - self.refreshed_at >= REFRESH_EVERY

    def stale(self, now: datetime) -> bool:
        """Privacy fails closed (L10): without a recent refresh nothing guarded can be proven safe."""
        return self.refreshed_at is None or now - self.refreshed_at > STALE_AFTER

    @staticmethod
    def window_start(now: datetime) -> datetime:
        return now - WINDOW

    def apply(self, rows: Iterable[Mapping[str, Any]], now: datetime, *, complete: bool = True) -> None:
        """`complete` is False when some space could not be read: its rows are applied, but the guard's
        clock stays where it was, so a feed that keeps failing makes the guard stale."""
        for row in rows:
            row_id = UUID(str(row["id"]))
            if row_id in self._seen:
                continue
            at = row["created_at"]
            self._seen[row_id] = at
            version = int(row["privacy_version"])
            for profile_id in row.get("profile_ids") or ():
                pid = UUID(str(profile_id))
                current = self._profiles.get(pid)
                if current is None or current[0] < version:
                    self._profiles[pid] = (version, at)
            for handle_id in row.get("handle_ids") or ():
                self._handles[UUID(str(handle_id))] = HandleMark(str(row["kind"]), at)
        self._prune(now)
        if complete:
            self.refreshed_at = now

    def required_version(self, profile_id: UUID) -> int | None:
        entry = self._profiles.get(profile_id)
        return entry[0] if entry else None

    def handle_mark(self, handle_id: UUID) -> HandleMark | None:
        return self._handles.get(handle_id)

    def _prune(self, now: datetime) -> None:
        horizon = now - GUARD_FOR
        self._seen = {k: v for k, v in self._seen.items() if v >= now - WINDOW - REFRESH_EVERY}
        self._profiles = {k: v for k, v in self._profiles.items() if v[1] >= horizon}
        self._handles = {k: v for k, v in self._handles.items() if v.at >= horizon}
