"""What a read leaves behind (5.11, 5.13): the draft a read process emits and the writer chains."""

import base64
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from uuid import UUID

from niadra.domain.common.space import PersonPrincipal, Principal
from niadra.domain.vocabulary import Audience, ReceiptKind, ReceiptSurface


@dataclass(frozen=True, slots=True)
class ReceiptDraft:
    receipt_id: UUID
    space_id: UUID
    producer_id: str
    producer_seq: int
    kind: ReceiptKind
    surface: ReceiptSurface
    audience: Audience
    read_at: datetime
    profile_id: UUID | None = None
    source_id: UUID | None = None
    person_id: UUID | None = None
    conversation_id: str | None = None
    task_id: str | None = None
    object_id: UUID | None = None
    about_handle_id: UUID | None = None
    purpose: str | None = None
    item_ids: tuple[str, ...] = ()
    pack_hash: str | None = None
    manifest_hash: str | None = None
    verification: str | None = None
    policy_version: str | None = None
    experiment_group: str | None = None
    query_hash: bytes | None = None
    withheld: int = 0
    extra: dict[str, str] = field(default_factory=dict)
    """Transport metadata for the projections (the pinned view); neither stored nor chained."""
    detail: dict[str, str] = field(default_factory=dict)
    """What the receipt must keep saying: why a read was refused, which action a person took; for a
    read's slots, the plan (`slots`), the turn's reading (`analysis`) and why each slot was chosen
    (`why`: positions per channel, weighted contributions, fused score, the rule of a derived line),
    all as ids and numbers, never a line, a value or the question. Stored and chained."""


PERSON_LABEL = "person_label"
"""The `extra` key with the name of the person who acted: the writer keeps it beside the chain, not in it."""


def person_extra(principal: Principal) -> dict[str, str]:
    """What a receipt of a person's action carries for the writer to name them by."""
    if isinstance(principal, PersonPrincipal) and principal.label:
        return {PERSON_LABEL: principal.label}
    return {}


@dataclass(frozen=True, slots=True)
class ProducerMark:
    """Sent by an idle or stopping read process so the cell watermark can advance (5.11)."""

    producer_id: str
    last_seq: int
    at: datetime
    final: bool = False


def draft_to_json(draft: ReceiptDraft) -> dict[str, Any]:
    """A draft as JSON: for the durable path through the outbox when the queue does not confirm."""
    return {
        "receipt_id": str(draft.receipt_id),
        "space_id": str(draft.space_id),
        "producer_id": draft.producer_id,
        "producer_seq": draft.producer_seq,
        "kind": draft.kind.value,
        "surface": draft.surface.value,
        "audience": draft.audience.value,
        "read_at": draft.read_at.isoformat(),
        "profile_id": _text(draft.profile_id),
        "source_id": _text(draft.source_id),
        "person_id": _text(draft.person_id),
        "conversation_id": draft.conversation_id,
        "task_id": draft.task_id,
        "object_id": _text(draft.object_id),
        "about_handle_id": _text(draft.about_handle_id),
        "purpose": draft.purpose,
        "item_ids": list(draft.item_ids),
        "pack_hash": draft.pack_hash,
        "manifest_hash": draft.manifest_hash,
        "verification": draft.verification,
        "policy_version": draft.policy_version,
        "experiment_group": draft.experiment_group,
        "query_hash": base64.b64encode(draft.query_hash).decode() if draft.query_hash else None,
        "withheld": draft.withheld,
        "extra": dict(draft.extra),
        "detail": dict(draft.detail),
    }


def draft_from_json(data: dict[str, Any]) -> ReceiptDraft:
    def uuid(name: str) -> UUID | None:
        value = data.get(name)
        return UUID(value) if value else None

    query_hash = data.get("query_hash")
    return ReceiptDraft(
        receipt_id=UUID(data["receipt_id"]),
        space_id=UUID(data["space_id"]),
        producer_id=data["producer_id"],
        producer_seq=int(data["producer_seq"]),
        kind=ReceiptKind(data["kind"]),
        surface=ReceiptSurface(data["surface"]),
        audience=Audience(data["audience"]),
        read_at=datetime.fromisoformat(data["read_at"]),
        profile_id=uuid("profile_id"),
        source_id=uuid("source_id"),
        person_id=uuid("person_id"),
        conversation_id=data.get("conversation_id"),
        task_id=data.get("task_id"),
        object_id=uuid("object_id"),
        about_handle_id=uuid("about_handle_id"),
        purpose=data.get("purpose"),
        item_ids=tuple(data.get("item_ids") or ()),
        pack_hash=data.get("pack_hash"),
        manifest_hash=data.get("manifest_hash"),
        verification=data.get("verification"),
        policy_version=data.get("policy_version"),
        experiment_group=data.get("experiment_group"),
        query_hash=base64.b64decode(query_hash) if query_hash else None,
        withheld=int(data.get("withheld") or 0),
        extra=dict(data.get("extra") or {}),
        detail=dict(data.get("detail") or {}),
    )


def _text(value: UUID | None) -> str | None:
    return str(value) if value else None
