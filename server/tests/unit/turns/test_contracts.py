"""The turn record's rules that JSON Schema alone does not carry (`turn-record.v0`, 7.7): the content mode
decides where a blob's value is, every blob key a record uses exists, and a turn belongs somewhere."""

from typing import Any

import pytest
from pydantic import ValidationError

from niadra.contracts.turns import PromoteRequest, TurnRecord

DIGEST = "sha256:" + "ab" * 32


def record(mode: str, blob: dict[str, Any], **changes: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "turn_id": "0192f5a0-0000-7000-8000-00000000d001",
        "conversation_id": "c-8812",
        "agent": {"name": "closing", "role": "specialist"},
        "started_at": "2026-09-29T12:00:00Z",
        "ended_at": "2026-09-29T12:00:08Z",
        "content_mode": mode,
        "calls": [
            {"call_id": "k1", "kind": "tool", "name": "quote", "args": "b:1", "status": "ok"},
            {"call_id": "m1", "kind": "model", "tokens": {"in": 2953, "cached": 118000, "out": 138}},
        ],
        "blobs": {"b:1": blob},
    }
    return {**base, **changes}


@pytest.mark.parametrize(
    ("mode", "blob"),
    [
        ("stored", {"sha256": DIGEST, "content": {"plan": "family", "ages": [34, 36, 5]}}),
        ("pointer", {"sha256": DIGEST, "pointer": "s3://company-turns/turns/c-8812/b1.json"}),
        ("hash_only", {"sha256": DIGEST}),
    ],
)
def test_each_content_mode_takes_its_own_blob_shape(mode: str, blob: dict[str, Any]) -> None:
    turn = TurnRecord.model_validate(record(mode, blob))
    assert turn.calls[1].tokens is not None
    assert turn.calls[1].tokens.input == 2953
    assert turn.model_dump(by_alias=True)["calls"][1]["tokens"] == {"in": 2953, "cached": 118000, "out": 138}


@pytest.mark.parametrize(
    ("mode", "blob"),
    [
        # The canary of the pointer mode: no value of the customer ever travels to Niadra.
        ("pointer", {"sha256": DIGEST, "pointer": "s3://company-turns/b1.json", "content": {"x": 1}}),
        ("pointer", {"sha256": DIGEST}),
        ("hash_only", {"sha256": DIGEST, "content": {"x": 1}}),
        ("hash_only", {"sha256": DIGEST, "pointer": "s3://company-turns/b1.json"}),
        ("stored", {"sha256": DIGEST, "pointer": "s3://company-turns/b1.json"}),
    ],
)
def test_a_blob_in_the_wrong_place_for_its_mode_is_refused(mode: str, blob: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        TurnRecord.model_validate(record(mode, blob))


def test_every_blob_key_the_record_uses_must_exist() -> None:
    body = record("hash_only", {"sha256": DIGEST}, output={"ui": "b:9"})
    with pytest.raises(ValidationError, match="b:9"):
        TurnRecord.model_validate(body)


def test_a_turn_belongs_to_a_conversation_or_a_task() -> None:
    TurnRecord.model_validate(record("hash_only", {"sha256": DIGEST}, conversation_id=None, task_id="t-1"))
    with pytest.raises(ValidationError, match="conversation or a task"):
        TurnRecord.model_validate(record("hash_only", {"sha256": DIGEST}, conversation_id=None))


def test_a_turn_cannot_end_before_it_starts_nor_repeat_a_call() -> None:
    with pytest.raises(ValidationError, match="before"):
        TurnRecord.model_validate(record("hash_only", {"sha256": DIGEST}, ended_at="2026-09-29T11:59:00Z"))
    twice = record("hash_only", {"sha256": DIGEST})
    twice["calls"] = [twice["calls"][0], {**twice["calls"][0], "args": None}]
    with pytest.raises(ValidationError, match="call_id"):
        TurnRecord.model_validate(twice)


def test_tokens_belong_to_a_model_call() -> None:
    body = record("hash_only", {"sha256": DIGEST})
    body["calls"][0] = {**body["calls"][0], "tokens": {"in": 1, "out": 1}}
    with pytest.raises(ValidationError, match="model call"):
        TurnRecord.model_validate(body)


def test_interactions_are_the_interaction_specs() -> None:
    body = record("hash_only", {"sha256": DIGEST})
    presented = {
        "kind": "presented",
        "exposure_id": "0192f5a0-0000-7000-8000-0000000000e1",
        "list_id": "plans",
        "items": [{"pos": 1, "ref": "health_quote:operator:q-77", "shown": {"price_full": 511.06}}],
        "delivered_at": "2026-09-29T12:00:07Z",
    }
    body["interactions"] = [presented]
    turn = TurnRecord.model_validate(body)
    assert turn.model_dump()["interactions"][0]["list_id"] == "plans"
    for wrong in ({"kind": "clicked"}, {**presented, "items": []}, {**presented, "exposure_id": "ex_1"}):
        body["interactions"] = [wrong]
        with pytest.raises(ValidationError):
            TurnRecord.model_validate(body)


def test_a_turn_id_is_safe_in_a_path_and_an_object_key() -> None:
    for bad in ("a/b", "../x", "", "x" * 65):
        with pytest.raises(ValidationError):
            TurnRecord.model_validate(record("hash_only", {"sha256": DIGEST}, turn_id=bad))


def test_promotion_is_by_conversation_or_by_ids() -> None:
    PromoteRequest.model_validate({"conversation_id": "c-8812", "reason": "complaint"})
    PromoteRequest.model_validate({"turn_ids": ["t1", "t2"], "reason": "bug_report"})
    for body in ({"reason": "complaint"}, {"conversation_id": "c", "turn_ids": ["t1"], "reason": "review"}):
        with pytest.raises(ValidationError):
            PromoteRequest.model_validate(body)


def test_a_claim_is_the_claim_contracts_record() -> None:
    body = record("hash_only", {"sha256": DIGEST})
    claim = {
        "category": "price",
        "class": "money",
        "role": "price_full",
        "span": [41, 50],
        "value": {"amount": "511.06", "unit": "BRL"},
        "evidence": {"call_id": "k1", "field": "price_full"},
        "verdict": "matched",
        "action": "none",
    }
    body["claims"] = [claim]
    turn = TurnRecord.model_validate(body)
    assert turn.model_dump(by_alias=True)["claims"][0]["class"] == "money"
    for wrong in ({**claim, "class": "currency"}, {**claim, "verdict": "fine"}, {**claim, "span": [50, 41]}):
        body["claims"] = [wrong]
        with pytest.raises(ValidationError):
            TurnRecord.model_validate(body)
    body["claims"] = [{**claim, "evidence": {"anchor": "a:1"}}]
    with pytest.raises(ValidationError, match="a:1"):
        TurnRecord.model_validate(body)
