"""Receipts as security events for the tenant's SIEM (5.13): OCSF 1.3 API Activity, or the same event
wrapped for an HTTP Event Collector.

Each receipt becomes one event: who read (the source or the person), on which surface, for which
customer (the pseudonymous profile id) and object, with what purpose and level, whether the policy
refused it, and where it sits on the hash chain, so the SIEM can check the chain on its own. Like the
receipts, the events carry ids and hashes, never what was read.
"""

from collections.abc import Iterable, Mapping, Sequence
from typing import Any
from uuid import UUID

import orjson

from niadra.domain.audit.chain import StoredReceipt
from niadra.domain.vocabulary import ReceiptKind

OCSF_VERSION = "1.3.0"
API_ACTIVITY = 6003
APPLICATION_ACTIVITY = 6
READ, OTHER = 2, 99
SUCCESS, FAILURE = 1, 2
INFORMATIONAL, MEDIUM = 1, 3
HEC_SOURCETYPE = "niadra:ocsf:api_activity"
SIEM_FORMATS = frozenset({"ocsf", "hec"})
MAX_EVENTS_PER_DELIVERY = 200
_READS = frozenset(
    {ReceiptKind.READ, ReceiptKind.HISTORY, ReceiptKind.ANALYTICS, ReceiptKind.HOLDOUT, ReceiptKind.DENIED}
)


def receipt_event(receipt: StoredReceipt, source_names: Mapping[UUID, str]) -> dict[str, Any]:
    d = receipt.draft
    refused = d.kind is ReceiptKind.DENIED
    activity = READ if d.kind in _READS else OTHER
    event: dict[str, Any] = {
        "class_uid": API_ACTIVITY,
        "class_name": "API Activity",
        "category_uid": APPLICATION_ACTIVITY,
        "category_name": "Application Activity",
        "activity_id": activity,
        "activity_name": "Read" if activity == READ else "Other",
        "type_uid": API_ACTIVITY * 100 + activity,
        "severity_id": MEDIUM if refused else INFORMATIONAL,
        "status_id": FAILURE if refused else SUCCESS,
        "status": "Failure" if refused else "Success",
        "time": int(d.read_at.timestamp() * 1000),
        "metadata": {
            "version": OCSF_VERSION,
            "product": {"name": "Niadra", "vendor_name": "Niadra", "feature": {"name": "receipts"}},
            "uid": str(d.receipt_id),
            "log_name": "receipts",
            "sequence": receipt.seq,
        },
        "actor": _actor(d.source_id, d.person_id, d.audience.value, source_names),
        "api": {"operation": d.surface.value, "service": {"name": "niadra"}},
        "src_endpoint": {"svc_name": d.producer_id},
        "resources": _resources(d.profile_id, d.object_id),
        "unmapped": {
            "niadra": {
                "space_id": str(d.space_id),
                "kind": d.kind.value,
                "purpose": d.purpose,
                "verification": d.verification,
                "policy_version": d.policy_version,
                "items": len(d.item_ids),
                "withheld": d.withheld,
                "pack_hash": d.pack_hash,
                "manifest_hash": d.manifest_hash,
                "conversation_id": d.conversation_id,
                "task_id": d.task_id,
                "detail": dict(d.detail),
                "chain": {
                    "slice": receipt.slice,
                    "day": receipt.day.isoformat(),
                    "seq": receipt.seq,
                    "prev_hash": receipt.prev_hash.hex(),
                    "hash": receipt.hash.hex(),
                },
            }
        },
    }
    if refused:
        event["status_detail"] = d.detail.get("reason", "refused")
    if d.kind is ReceiptKind.ADMIN and (action := d.detail.get("action")):
        event["api"]["operation"] = action
    return event


def test_event(space_id: UUID, delivery_id: UUID, at_ms: int) -> dict[str, Any]:
    """What a test delivery sends to a SIEM stream: an API Activity event marked as a test, with no
    receipt behind it and outside the chain."""
    return {
        "class_uid": API_ACTIVITY,
        "class_name": "API Activity",
        "category_uid": APPLICATION_ACTIVITY,
        "category_name": "Application Activity",
        "activity_id": OTHER,
        "activity_name": "Other",
        "type_uid": API_ACTIVITY * 100 + OTHER,
        "severity_id": INFORMATIONAL,
        "status_id": SUCCESS,
        "status": "Success",
        "time": at_ms,
        "metadata": {
            "version": OCSF_VERSION,
            "product": {"name": "Niadra", "vendor_name": "Niadra", "feature": {"name": "receipts"}},
            "uid": str(delivery_id),
            "log_name": "receipts",
        },
        "actor": {"app_name": "niadra"},
        "api": {"operation": "test", "service": {"name": "niadra"}},
        "unmapped": {"niadra": {"space_id": str(space_id), "test": True}},
    }


def hec_event(event: Mapping[str, Any]) -> dict[str, Any]:
    """An HTTP Event Collector envelope: epoch seconds, a fixed source type, the OCSF event inside."""
    return {"time": event["time"] / 1000, "source": "niadra", "sourcetype": HEC_SOURCETYPE, "event": event}


def ndjson(events: Iterable[Mapping[str, Any]]) -> bytes:
    """One JSON object per line, keys sorted: the same batch always signs the same way."""
    return b"".join(orjson.dumps(e, option=orjson.OPT_SORT_KEYS) + b"\n" for e in events)


def siem_body(format_: str, events: Sequence[Mapping[str, Any]]) -> tuple[bytes, str]:
    if format_ == "hec":
        return ndjson(hec_event(e) for e in events), "application/json"
    return ndjson(events), "application/x-ndjson"


def chunks[T](items: Sequence[T], size: int = MAX_EVENTS_PER_DELIVERY) -> list[Sequence[T]]:
    return [items[i : i + size] for i in range(0, len(items), size)]


def _actor(
    source_id: UUID | None, person_id: UUID | None, audience: str, names: Mapping[UUID, str]
) -> dict[str, Any]:
    if person_id is not None:
        return {"user": {"uid": str(person_id), "type": "User"}, "app_name": "niadra-console"}
    if source_id is None:
        return {"app_name": audience}
    return {"app_uid": str(source_id), "app_name": names.get(source_id, audience)}


def _resources(profile_id: UUID | None, object_id: UUID | None) -> list[dict[str, str]]:
    found = []
    if profile_id is not None:
        found.append({"type": "customer_profile", "uid": str(profile_id)})
    if object_id is not None:
        found.append({"type": "business_object", "uid": str(object_id)})
    return found
