"""The turn record's pure rules (24.3.5): the digest every producer computes the same way, where a record
lives, which turns the kept tier takes, and what the day's totals add up."""

from datetime import date
from typing import Any
from uuid import UUID

import pytest

from niadra.domain.turns.daily import LATENCY_BOUNDS_MS, build_hash, builds_seen, daily_totals, latency_bucket
from niadra.domain.turns.digest import canonical_json, digest
from niadra.domain.turns.frames import derived_flags, frame_of
from niadra.domain.turns.replay import replayable
from niadra.domain.turns.storage import (
    KEEP_FLAGS,
    Tier,
    by_id_key,
    conversation_prefixes,
    keep_flags,
    sampled,
    short_turn_ids,
    turn_key,
)
from tests.spec import cases

SPACE = UUID("0192f5a0-0000-7000-8000-00000000a001")
SOURCE = UUID("0192f5a0-0000-7000-8000-00000000c001")
CONVERSATION = bytes.fromhex("ab" * 32)


@pytest.mark.parametrize("case", cases("turn-record-digest", {"note", "value"}), ids=lambda c: c["id"])
def test_a_value_has_one_canonical_form_and_one_digest(case: dict[str, Any]) -> None:
    assert canonical_json(case["value"]).decode() == case["expect"]["canonical"]
    assert digest(case["value"]) == (case["expect"]["sha256"], case["expect"]["size"])


def test_a_value_json_cannot_hold_has_no_digest() -> None:
    for bad in (float("nan"), {1: "a"}, b"bytes"):
        with pytest.raises(ValueError):  # noqa: PT011
            digest(bad)


def test_the_tier_comes_first_and_the_turn_id_ends_the_key() -> None:
    short = turn_key(Tier.SHORT, SPACE, CONVERSATION, "t1")
    assert short == f"turns/short/{SPACE}/{'ab' * 32}/t1.bin"
    assert turn_key(Tier.KEPT, SPACE, CONVERSATION, "t1").startswith(f"turns/kept/{SPACE}/")
    assert all(
        k.startswith(p) for k, p in zip((short,), conversation_prefixes(SPACE, CONVERSATION), strict=False)
    )
    assert by_id_key(SPACE, "t1") == f"turns/short/{SPACE}/by-id/t1"
    listed = [
        short,
        turn_key(Tier.SHORT, SPACE, CONVERSATION, "t1.bin"),
        f"turns/short/{SPACE}/{'ab' * 32}/x",
    ]
    assert short_turn_ids(SPACE, CONVERSATION, listed) == ["t1", "t1.bin"]


def test_the_sample_keeps_whole_conversations_and_answers_the_same_every_time() -> None:
    conversations = [bytes([i]) * 32 for i in range(200)]
    kept = [c for c in conversations if sampled(SPACE, c, 0.05)]
    assert 2 <= len(kept) <= 25
    assert kept == [c for c in conversations if sampled(SPACE, c, 0.05)]
    assert not any(sampled(SPACE, c, 0) for c in conversations)
    assert all(sampled(SPACE, c, 1) for c in conversations)


def test_flags_that_keep_a_turn_and_the_ones_the_record_implies() -> None:
    assert keep_flags(["truncated", "handoff", "error"]) == ["error", "handoff"]
    assert "truncated" not in KEEP_FLAGS
    frame = {"calls": [{"call_id": "k1", "kind": "tool", "status": "timeout"}], "completeness": "incomplete"}
    assert derived_flags(frame) == {"error", "incomplete"}
    assert derived_flags({"flags": ["synthetic"], "calls": [{"kind": "tool", "status": "cancelled"}]}) == {
        "synthetic"
    }


def test_a_frame_keeps_digests_and_pointers_and_never_a_value() -> None:
    record = {"turn_id": "t1", "blobs": {"b:1": {"sha256": "sha256:" + "0" * 64, "size": 3, "content": [1]}}}
    assert frame_of(record)["blobs"] == {"b:1": {"sha256": "sha256:" + "0" * 64, "size": 3}}
    assert record["blobs"]["b:1"]["content"] == [1]


def test_only_a_gold_complete_record_with_its_values_and_pins_replays() -> None:
    pins = {"prompts": {"core": "v16"}, "model": "m-1"}
    gold = {"content_mode": "stored", "build": {"pins": pins}}
    assert replayable(gold, {"prompts", "model"})
    assert not replayable({**gold, "fidelity": "bronze"}, {"model"})
    assert not replayable({**gold, "completeness": "partial"}, {"model"})
    assert not replayable({**gold, "content_mode": "hash_only"}, {"model"})
    assert not replayable({**gold, "build": {"pins": {"model": "m-1", "prompts": {}}}}, {"prompts"})
    assert replayable({**gold, "content_mode": "pointer"}, set())


def _frame(**changes: Any) -> dict[str, Any]:
    frame: dict[str, Any] = {
        "turn_id": "t1",
        "agent": {"name": "closing"},
        "started_at": "2026-09-29T23:59:00-03:00",
        "content_mode": "hash_only",
        "build": {"pins": {"model": "m-1", "niadra": {"compiler": "c-1", "pack_hash": "p-1"}}},
        "latency_ms": 700,
        "calls": [
            {
                "call_id": "k1",
                "kind": "tool",
                "status": "error",
                "synthetic": True,
                "applied": {
                    "constraints": "cv_1",
                    "hard_sent": ["size", "color"],
                    "results_checked": 5,
                    "violations": 2,
                    "unverifiable": 1,
                },
            },
            {
                "call_id": "k2",
                "kind": "tool",
                "cache_hit": True,
                "applied": {
                    "constraints": "cv_1",
                    "hard_sent": ["size"],
                    "results_checked": 3,
                    "violations": 3,
                    "unverifiable": 0,
                    "relaxed": "declared",
                },
            },
            {
                "call_id": "k3",
                "kind": "tool",
                "applied": {
                    "constraints": "cv_1",
                    "results_checked": 1,
                    "violations": 1,
                    "unverifiable": 0,
                    "relaxed": "suspected",
                },
            },
            {"call_id": "m1", "kind": "model", "tokens": {"in": 100, "cached": 1000, "out": 10}},
        ],
        "interactions": [
            {"kind": "presented", "items": [{"pos": 1}, {"pos": 2}, {"pos": 3}]},
            {"kind": "seen", "max_index_seen": 2},
        ],
        "claims": [
            {"category": "price", "verdict": "matched"},
            {"category": "price", "verdict": "matched"},
            {"category": "price", "verdict": "no_evidence"},
        ],
        "effects": [{"key": "farewell:c1", "state": "done"}],
        "cost": {"usd": 0.036},
    }
    return {**frame, **changes}


def test_the_days_totals_count_calls_tokens_claims_constraints_and_what_was_shown() -> None:
    [row] = daily_totals([(SOURCE, _frame())])
    assert row.day == date(2026, 9, 30), "the day is UTC's"
    assert (row.turns, row.tool_calls, row.model_calls, row.errors) == (1, 3, 1, 1)
    assert (row.synthetic, row.cache_hits) == (1, 1)
    assert (row.tokens_in, row.tokens_cached, row.tokens_out, row.cost_micros) == (100, 1000, 10, 36_000)
    assert row.claims == {"price|matched": 2, "price|no_evidence": 1}
    assert (row.constraints_sent, row.results_checked, row.unverifiable) == (3, 9, 1)
    # A relaxation the tool declared is reported apart; a suspected one still counts as violated.
    assert (row.violations, row.relaxed_declared, row.relaxed_suspected) == (3, 1, 1)
    assert (row.effects, row.items_presented) == (1, 3)
    assert row.latency_buckets[latency_bucket(700)] == 1
    assert len(row.latency_buckets) == len(LATENCY_BOUNDS_MS) + 1


def test_one_row_per_day_source_agent_and_build() -> None:
    other_build = _frame(build={"pins": {"model": "m-2"}})
    rows = daily_totals(
        [(SOURCE, _frame()), (SOURCE, _frame()), (SOURCE, other_build), (SOURCE, _frame(agent={"name": "x"}))]
    )
    assert sorted(r.turns for r in rows) == [1, 1, 2]
    assert len({r.key for r in rows}) == 3


def test_a_build_is_its_pins_without_the_packs_hash() -> None:
    pins = {"model": "m-1", "niadra": {"compiler": "c-1", "pack_hash": "p-1"}}
    assert build_hash(pins) == build_hash({**pins, "niadra": {"compiler": "c-1", "pack_hash": "p-2"}})
    assert build_hash(pins) != build_hash({**pins, "niadra": {"compiler": "c-2"}})
    assert build_hash({"model": "m-1"}) == build_hash(
        {"model": "m-1", "prompts": {}, "niadra": {"pack_hash": "p"}}
    )
    [build] = builds_seen([(SOURCE, _frame()), (SOURCE, _frame(started_at="2026-09-28T10:00:00Z"))])
    assert build.at.isoformat() == "2026-09-28T10:00:00+00:00"
