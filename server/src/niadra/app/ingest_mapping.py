"""Webhook payload to batch items through the source's versioned mapping (5.2, 18.2).

Pure and without LLM: each rule whose `match` is truthy yields one item, validated afterwards
like any batch item. The raw payload is always kept elsewhere, so a new mapping version can
remap it later.
"""

from datetime import UTC, datetime
from typing import Any

import jmespath

from niadra.config.models import Mapping, MappingRule
from niadra.domain.vocabulary import EventKind, Speaker

_EPOCH_MS_THRESHOLD = 10**12


def map_payload(mapping: Mapping, payload: Any) -> list[dict[str, Any]]:
    return [_item(mapping, rule, payload) for rule in mapping.rules if jmespath.search(rule.match, payload)]


def _item(mapping: Mapping, rule: MappingRule, payload: Any) -> dict[str, Any]:
    def get(expr: str | None) -> Any:
        return jmespath.search(expr, payload) if expr else None

    key = get(rule.idempotency_key)
    item: dict[str, Any] = {
        "type": "event",
        "kind": rule.kind.value,
        # Two sources may number their events alike; the key is namespaced by source.
        "idempotency_key": f"wh:{mapping.source_id}:{key}" if key not in (None, "") else None,
        "occurred_at": _time(get(rule.occurred_at)),
        "channel": rule.channel,
        "conversation_id": _text(get(rule.conversation_id)) or _conversation_of(mapping, rule, get, key),
        # Free text from a system (a ticket, an e-mail body) was written by the customer (18.2).
        "speaker": {"role": (Speaker.CUSTOMER if rule.kind is EventKind.MESSAGE else Speaker.SYSTEM).value},
        "canonical_type": rule.canonical_type or _text(get(rule.canonical_type_expr)),
        "handles": [
            _drop_none(
                {
                    "type": h.type.value,
                    "value": str(value),
                    "scope": h.scope,
                    "subject_kind": h.subject_kind.value if h.subject_kind else None,
                }
            )
            for h in rule.handles
            if (value := get(h.value)) not in (None, "")
        ],
        "object_refs": [
            {"type": o.type, "namespace": o.namespace, "id": str(value)}
            for o in rule.object_refs
            if (value := get(o.id)) not in (None, "")
        ],
        "fields": _drop_none({name: get(expr) for name, expr in rule.fields.items()}),
    }
    if text := _text(get(rule.text)):
        item["content"] = {"type": "text", "text": text}
    if rule.kind is EventKind.ACTION:
        item["action"] = {"operation": rule.operation}
    return _drop_none(item)


def _conversation_of(mapping: Mapping, rule: MappingRule, get: Any, key: Any) -> str | None:
    """Free text from a system is read like a conversation (18.2), and only a conversation opens a session
    that gets extracted. Without an expression for it, the texts about the same object (a ticket, an
    order) form one conversation; without an object, each text is its own."""
    if rule.kind is not EventKind.MESSAGE:
        return None
    for ref in rule.object_refs:
        if (value := get(ref.id)) not in (None, ""):
            return f"obj:{ref.type}:{ref.namespace}:{value}"
    return f"wh:{mapping.source_id}:{key}" if key not in (None, "") else None


def _time(value: Any) -> Any:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return value
    seconds = value / 1000 if value > _EPOCH_MS_THRESHOLD else value
    return datetime.fromtimestamp(seconds, UTC).isoformat()


def _text(value: Any) -> str | None:
    return None if value in (None, "") else str(value)


def _drop_none(doc: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in doc.items() if v is not None}
