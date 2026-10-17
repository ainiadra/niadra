"""The day's totals of the turns (6.7): counts with no person, which the cost and regression reports read.

They add up the frames as the edge stored them (the record's JSON, without blob contents), so a worker
never needs the content to count.
"""

import hashlib
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from niadra.domain.turns.digest import canonical_json
from niadra.domain.turns.records import BuildSeen, DailyTotals

LATENCY_BOUNDS_MS = (250, 500, 1_000, 2_000, 4_000, 8_000, 16_000, 32_000)
"""Upper bounds of the latency buckets; the last bucket takes everything above."""
_ERRORS = frozenset({"error", "timeout"})


def latency_bucket(latency_ms: int) -> int:
    return next(
        (i for i, bound in enumerate(LATENCY_BOUNDS_MS) if latency_ms <= bound), len(LATENCY_BOUNDS_MS)
    )


def build_hash(pins: Mapping[str, Any]) -> bytes:
    """A build is its pins; the pack's hash changes every turn and stays out of it."""
    niadra = {k: v for k, v in (pins.get("niadra") or {}).items() if k != "pack_hash"}
    kept = {k: v for k, v in pins.items() if k != "niadra" and v not in (None, {}, [])}
    if any(v is not None for v in niadra.values()):
        kept["niadra"] = niadra
    return hashlib.sha256(canonical_json(kept)).digest()


def started(frame: Mapping[str, Any]) -> datetime:
    """When the turn started, in UTC: the day of the totals is UTC's."""
    at = datetime.fromisoformat(frame["started_at"])
    return at.astimezone(UTC) if at.tzinfo else at.replace(tzinfo=UTC)


def daily_totals(
    turns: Iterable[tuple[UUID, Mapping[str, Any]]], added: Iterable[tuple[UUID, Mapping[str, Any]]] = ()
) -> list[DailyTotals]:
    """One row per (day, source, agent, build) of the frames, each key once: an upsert takes each row once.
    `added` are what later parts of turns already counted brought: their calls and interactions, never
    another turn, cost or latency."""
    rows: dict[tuple[Any, ...], DailyTotals] = {}
    for whole, frames in ((True, turns), (False, added)):
        for source_id, frame in frames:
            pins = (frame.get("build") or {}).get("pins") or {}
            row = DailyTotals(started(frame).date(), source_id, frame["agent"]["name"], build_hash(pins))
            _add(rows.setdefault(row.key, row), frame, whole=whole)
    return sorted(rows.values(), key=lambda r: (r.day, str(r.source_id), r.agent, r.build_hash))


def _add(row: DailyTotals, frame: Mapping[str, Any], *, whole: bool) -> None:
    if whole:
        row.turns += 1
        row.incomplete += frame.get("completeness") in ("partial", "incomplete")
        _add_cost_and_latency(row, frame)
    for call in frame.get("calls") or ():
        if call["kind"] == "tool":
            row.tool_calls += 1
        else:
            row.model_calls += 1
        row.errors += call.get("status", "ok") in _ERRORS
        row.synthetic += bool(call.get("synthetic"))
        row.cache_hits += bool(call.get("cache_hit"))
        tokens = call.get("tokens") or {}
        row.tokens_in += int(tokens.get("in", 0))
        row.tokens_cached += int(tokens.get("cached", 0))
        row.tokens_out += int(tokens.get("out", 0))
        if applied := call.get("applied"):
            relaxed = applied.get("relaxed")
            row.constraints_sent += len(applied.get("hard_sent") or ())
            row.results_checked += int(applied["results_checked"])
            # A relaxation the tool declared is reported apart, never as a violation (6.9).
            row.violations += int(applied["violations"]) if relaxed != "declared" else 0
            row.unverifiable += int(applied["unverifiable"])
            row.relaxed_declared += relaxed == "declared"
            row.relaxed_suspected += relaxed == "suspected"
    for claim in frame.get("claims") or ():
        key = f"{claim['category']}|{claim['verdict']}"
        row.claims[key] = row.claims.get(key, 0) + 1
    row.effects += len(frame.get("effects") or ())
    row.items_presented += sum(
        len(i.get("items") or ()) for i in frame.get("interactions") or () if i.get("kind") == "presented"
    )


def _add_cost_and_latency(row: DailyTotals, frame: Mapping[str, Any]) -> None:
    if (cost := frame.get("cost")) is not None:
        row.cost_micros += round(float(cost["usd"]) * 1_000_000)
    if (latency := frame.get("latency_ms")) is not None:
        buckets = row.latency_buckets or [0] * (len(LATENCY_BOUNDS_MS) + 1)
        buckets[latency_bucket(int(latency))] += 1
        row.latency_buckets = buckets


def builds_seen(turns: Iterable[tuple[UUID, Mapping[str, Any]]]) -> list[BuildSeen]:
    """Each build of the frames once, with the first time it was seen among them."""
    seen: dict[bytes, BuildSeen] = {}
    for source_id, frame in sorted(turns, key=lambda t: started(t[1])):
        build = frame.get("build") or {}
        pins = build.get("pins") or {}
        key = build_hash(pins)
        seen.setdefault(
            key,
            BuildSeen(
                key,
                source_id,
                frame["agent"]["name"],
                pins,
                build.get("sdk"),
                build.get("adapter"),
                started(frame),
            ),
        )
    return sorted(seen.values(), key=lambda b: b.build_hash)
