"""Turn records as producers send them, for the turns module's tests: synthetic data only."""

from typing import Any

from niadra.domain.turns.digest import digest

QUOTE_ARGS = {"plan": "family", "ages": [34, 36, 5], "network": "regional"}
QUOTE_RESULT = {"quote_id": "q-77", "price_full": 511.06, "valid_until": "2026-10-05"}


def blob(value: Any, mode: str, pointer: str = "s3://company-turns/b.json") -> dict[str, Any]:
    sha256, size = digest(value)
    if mode == "stored":
        return {"sha256": sha256, "size": size, "content": value}
    if mode == "pointer":
        return {"sha256": sha256, "size": size, "pointer": pointer}
    return {"sha256": sha256, "size": size}


def turn(
    turn_id: str = "0192f5a0-7c3e-7a10-9d2b-5e61a4c0d001",
    *,
    mode: str = "stored",
    conversation_id: str | None = "c-8812",
    args: Any = QUOTE_ARGS,
    result: Any = QUOTE_RESULT,
    **changes: Any,
) -> dict[str, Any]:
    """A gold turn of a health plan sale: one quote tool call and one model call, the build pinned."""
    record: dict[str, Any] = {
        "spec": "turn-record.v0",
        "turn_id": turn_id,
        "conversation_id": conversation_id,
        "agent": {"name": "closing", "role": "specialist"},
        "kind": "message",
        "started_at": "2026-09-29T14:02:11Z",
        "ended_at": "2026-09-29T14:02:19Z",
        "content_mode": mode,
        "build": {
            "pins": {
                "prompts": {"core": "v16"},
                "model": "model-2026-08",
                "tool_schemas": {"quote": "sha256:" + "1" * 64},
                "niadra": {"compiler": "compiler-1.15", "pack_hash": "ph-1"},
            },
            "sdk": "niadra-python/0.7.0",
        },
        "calls": [
            {
                "call_id": "k1",
                "kind": "tool",
                "name": "quote",
                "status": "ok",
                "latency_ms": 740,
                "args_hash": digest(args)[0],
                "args": "b:1",
                "result_model": "b:2",
                "observations": [
                    {
                        "ref": "health_quote:operator:q-77",
                        "fields": {"price_full": 511.06},
                        "provenance": {"source": "live", "source_observed_at": "2026-09-29T14:02:12Z"},
                    }
                ],
            },
            {"call_id": "m1", "kind": "model", "name": "model-2026-08", "tokens": {"in": 2953, "out": 138}},
        ],
        "claims": [
            {
                "category": "price",
                "class": "money",
                "role": "price_full",
                "span": [41, 50],
                "value": {"amount": "511.06", "unit": "BRL"},
                "evidence": {"call_id": "k1", "field": "price_full"},
                "verdict": "matched",
                "action": "none",
            }
        ],
        "output": {"event_keys": ["wa-msg-9"]},
        "cost": {"usd": 0.036},
        "latency_ms": 8200,
        "blobs": {"b:1": blob(args, mode), "b:2": blob(result, mode, "s3://company-turns/r.json")},
    }
    return {**record, **changes}
