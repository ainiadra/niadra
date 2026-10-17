"""In-memory `InsightsRepository` for unit tests.

In PostgreSQL these reads are named read models over other modules' tables (4.3). Here they are an
analytical copy that tests fill with `add_profile`, `add_row` and `add_episode`, the
way the per-space analytical tables would be filled (06, 4.6).
"""

import random
from collections.abc import Collection, Iterator, Sequence
from datetime import date, datetime
from typing import Any
from uuid import UUID

from niadra.adapters.memory.store import MemoryStore
from niadra.domain.insights.gaps import GapDay, Sketch
from niadra.domain.insights.model import (
    AggregateQuery,
    AggregateRow,
    Entity,
    EpisodeText,
    Metric,
    ProfileMatch,
    ProfileRef,
    RollupRow,
    Selection,
    age_band,
    sentiment_band,
)


class InMemoryInsightsRepository:
    def __init__(self, store: MemoryStore) -> None:
        self._s = store.module("insights")
        self._s.setdefault("profiles", {})
        self._s.setdefault("rows", [])
        self._s.setdefault("rollups", {})
        self._s.setdefault("gaps", {})
        self._s.setdefault("reviews", {})

    def add_profile(self, ref: ProfileRef, created_at: datetime) -> None:
        self._s["profiles"][ref.pseudonym] = ref
        self.add_row(Entity.PROFILES, ref.profile_id, created_at, kind=ref.kind)

    def add_row(self, entity: Entity, profile_id: UUID | None, at: datetime, **dimensions: Any) -> None:
        self._s["rows"].append({"entity": entity, "profile_id": profile_id, "t": at, **dimensions})

    def add_episode(self, episode: EpisodeText, profile_id: UUID, **dimensions: Any) -> None:
        self.add_row(
            Entity.EPISODES,
            profile_id,
            episode.ended_at,
            episode=episode,
            intent=episode.intent,
            category=episode.category,
            outcome=episode.outcome,
            channel=episode.channel,
            sentiment=episode.sentiment,
            **dimensions,
        )

    async def aggregate(
        self, query: AggregateQuery, exclude_categories: Collection[str]
    ) -> list[AggregateRow]:
        groups: dict[tuple[str | None, ...], list[dict[str, Any]]] = {}
        for row in self._select(query.selection, exclude_categories):
            groups.setdefault(tuple(_text(row.get(d)) for d in query.group_by), []).append(row)
        result = []
        for key, rows in groups.items():
            sentiments = [r["sentiment"] for r in rows if r.get("sentiment") is not None]
            result.append(
                AggregateRow(
                    group=key,
                    count=len(rows),
                    profiles=len({r["profile_id"] for r in rows if r["profile_id"]})
                    if Metric.PROFILES in query.metrics
                    else None,
                    avg_sentiment=sum(sentiments) / len(sentiments)
                    if Metric.AVG_SENTIMENT in query.metrics and sentiments
                    else None,
                )
            )
        return result

    async def find_profiles(
        self,
        selection: Selection,
        exclude_categories: Collection[str],
        min_matches: int,
        after: str | None,
        limit: int,
    ) -> list[ProfileMatch]:
        counts: dict[UUID, int] = {}
        for row in self._select(selection, exclude_categories):
            if row["profile_id"] is not None:
                counts[row["profile_id"]] = counts.get(row["profile_id"], 0) + 1
        pseudonyms = {ref.profile_id: ref.pseudonym for ref in self._s["profiles"].values()}
        found = [
            ProfileMatch(pseudonyms[p], n)
            for p, n in counts.items()
            if n >= min_matches and p in pseudonyms and (after is None or pseudonyms[p] > after)
        ]
        return sorted(found, key=lambda m: m.pseudonym)[:limit]

    async def episode_texts(
        self, selection: Selection, exclude_categories: Collection[str], limit: int, *, newest_first: bool
    ) -> list[EpisodeText]:
        episodes = [r["episode"] for r in self._select(selection, exclude_categories)]
        if newest_first:
            episodes.sort(key=lambda e: e.ended_at, reverse=True)
        else:
            random.shuffle(episodes)
        return episodes[:limit]

    async def profile_by_pseudonym(self, pseudonym: str) -> ProfileRef | None:
        ref: ProfileRef | None = self._s["profiles"].get(pseudonym)
        return ref

    async def write_rollups(self, day: date, rows: Sequence[RollupRow]) -> None:
        self._s["rollups"][day] = list(rows)

    async def latest_rollups(self) -> tuple[date | None, list[RollupRow]]:
        if not self._s["rollups"]:
            return None, []
        day = max(self._s["rollups"])
        return day, list(self._s["rollups"][day])

    async def write_gap_days(self, day: date, rows: Sequence[GapDay]) -> None:
        self._s["gaps"][day] = [GapDay(r.day, r.key, r.turns, Sketch(r.customers.to_bytes())) for r in rows]

    async def purge_gap_days(self, before: date) -> int:
        old = [day for day in self._s["gaps"] if day < before]
        for day in old:
            del self._s["gaps"][day]
        return len(old)

    async def gap_days(self, since: date, until: date) -> list[GapDay]:
        return [r for day, rows in sorted(self._s["gaps"].items()) if since <= day < until for r in rows]

    async def gaps_day(self) -> int | None:
        value: int | None = self._s.get("gaps_day")
        return value

    async def set_gaps_day(self, day: int) -> None:
        self._s["gaps_day"] = day

    async def reviews(self) -> dict[str, tuple[str, str, datetime, str | None]]:
        return dict(self._s["reviews"])

    async def put_review(
        self, key: str, decision: str, decided_by: str, at: datetime, note: str | None
    ) -> None:
        self._s["reviews"][key] = (decision, decided_by, at, note)

    def _select(self, selection: Selection, exclude_categories: Collection[str]) -> Iterator[dict[str, Any]]:
        for row in self._s["rows"]:
            if row["entity"] != selection.entity or not selection.since <= row["t"] < selection.until:
                continue
            if selection.entity is Entity.EPISODES and (
                row.get("category") in exclude_categories
                or row.get("normalized_category") in exclude_categories
            ):
                continue
            view = {**row, **_computed(selection, row)}
            if all(_text(view.get(k)) in values for k, values in selection.filters.items()):
                yield view


def _computed(selection: Selection, row: dict[str, Any]) -> dict[str, str]:
    if selection.entity is Entity.EPISODES:
        return {"sentiment_band": sentiment_band(row.get("sentiment"))}
    if selection.entity is Entity.OPEN_ITEMS:
        return {"age_band": age_band(row["t"], selection.until)}
    return {}


def _text(value: Any) -> str | None:
    return None if value is None else str(value)
