"""An event as ingestion prepares it before any database work, and the intake log's body (5.2).

`PreparedEvent` is everything the write path computes from the item and the caller alone: the masked
text, fields and action, the content metadata, the hashed voice numbers, the capped hint and the
levels the channel proves. Storing it is what the rest of the write path does with the database
(conversation, identity, sessions), which the acknowledgement does not wait for: an `Intake` row
carries the prepared events of one batch, sealed, until they are applied in arrival order.
"""

import dataclasses
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any
from uuid import UUID

from niadra.contracts.common import Handle
from niadra.domain.records import ObjectKey
from niadra.domain.vocabulary import (
    Audience,
    EventKind,
    Scope,
    Speaker,
    SubjectKind,
    Verification,
    Visibility,
)

SubjectGroup = tuple[SubjectKind, str | None, tuple[Handle, ...]]


@dataclasses.dataclass(frozen=True, slots=True)
class PreparedEvent:
    event_id: UUID
    idempotency_key: str
    kind: EventKind
    channel: str
    speaker: Speaker
    occurred_at: datetime
    skew_corrected: bool
    groups: tuple[SubjectGroup, ...]
    speaker_id: str | None = None
    direction: str | None = None
    conversation_id: str | None = None
    """As sent: the stored conversation it names is found when the event is stored."""
    aliases: tuple[str, ...] = ()
    task_id: str | None = None
    text: str | None = None
    fields: dict[str, Any] = dataclasses.field(default_factory=dict)
    action: dict[str, Any] | None = None
    canonical_type: str | None = None
    object_refs: tuple[ObjectKey, ...] = ()
    visibility: Visibility = Visibility.PUBLIC
    stt_confidence: float | None = None
    corrects_event_id: UUID | None = None
    content_meta: dict[str, Any] | None = None
    voice: dict[str, Any] | None = None
    closes_ref: dict[str, Any] | None = None
    verification_hint: Verification | None = None
    network: Verification | None = None
    """What the call's network attestation proves."""
    channel_proof: Verification | None = None
    """What an inbound customer turn proves by the channel's own plausibility."""
    guards: tuple[str, ...] = ()

    def to_json(self) -> dict[str, Any]:
        return {
            "event_id": str(self.event_id),
            "idempotency_key": self.idempotency_key,
            "kind": self.kind.value,
            "channel": self.channel,
            "speaker": self.speaker.value,
            "occurred_at": self.occurred_at.isoformat(),
            "skew_corrected": self.skew_corrected,
            "groups": [
                [kind.value, role, [h.model_dump(mode="json", exclude_none=True) for h in handles]]
                for kind, role, handles in self.groups
            ],
            "speaker_id": self.speaker_id,
            "direction": self.direction,
            "conversation_id": self.conversation_id,
            "aliases": list(self.aliases),
            "task_id": self.task_id,
            "text": self.text,
            "fields": self.fields,
            "action": self.action,
            "canonical_type": self.canonical_type,
            "object_refs": [[o.type, o.namespace, o.external_id] for o in self.object_refs],
            "visibility": self.visibility.value,
            "stt_confidence": self.stt_confidence,
            "corrects_event_id": _str(self.corrects_event_id),
            "content_meta": self.content_meta,
            "voice": self.voice,
            "closes_ref": self.closes_ref,
            "verification_hint": _value(self.verification_hint),
            "network": _value(self.network),
            "channel_proof": _value(self.channel_proof),
            "guards": list(self.guards),
        }

    @classmethod
    def from_json(cls, doc: Mapping[str, Any]) -> "PreparedEvent":
        return cls(
            event_id=UUID(doc["event_id"]),
            idempotency_key=doc["idempotency_key"],
            kind=EventKind(doc["kind"]),
            channel=doc["channel"],
            speaker=Speaker(doc["speaker"]),
            occurred_at=datetime.fromisoformat(doc["occurred_at"]),
            skew_corrected=bool(doc["skew_corrected"]),
            groups=tuple(
                (SubjectKind(kind), role, tuple(Handle.model_validate(h) for h in handles))
                for kind, role, handles in doc["groups"]
            ),
            speaker_id=doc.get("speaker_id"),
            direction=doc.get("direction"),
            conversation_id=doc.get("conversation_id"),
            aliases=tuple(doc.get("aliases") or ()),
            task_id=doc.get("task_id"),
            text=doc.get("text"),
            fields=dict(doc.get("fields") or {}),
            action=doc.get("action"),
            canonical_type=doc.get("canonical_type"),
            object_refs=tuple(ObjectKey(*o) for o in doc.get("object_refs") or ()),
            visibility=Visibility(doc.get("visibility") or Visibility.PUBLIC.value),
            stt_confidence=doc.get("stt_confidence"),
            corrects_event_id=UUID(doc["corrects_event_id"]) if doc.get("corrects_event_id") else None,
            content_meta=doc.get("content_meta"),
            voice=doc.get("voice"),
            closes_ref=doc.get("closes_ref"),
            verification_hint=_level(doc.get("verification_hint")),
            network=_level(doc.get("network")),
            channel_proof=_level(doc.get("channel_proof")),
            guards=tuple(doc.get("guards") or ()),
        )


@dataclasses.dataclass(frozen=True, slots=True)
class Intake:
    """One acknowledged batch waiting to be stored: who sent it, when, and its prepared events. `hot` names,
    per event, the handles whose hot layer the acknowledgement already wrote."""

    intake_id: UUID
    received_at: datetime
    source_id: UUID
    key_id: str
    scopes: frozenset[Scope]
    audience: Audience
    ceiling: Verification
    events: tuple[PreparedEvent, ...]
    rejected: int = 0
    """Items the batch's validation refused, for the source's counters."""
    duplicates: int = 0
    """Items repeated inside the batch; repeats of earlier batches are the row's `skip` keys."""
    heartbeats: tuple[tuple[datetime, int], ...] = ()
    hot: Mapping[UUID, tuple[UUID, ...]] = dataclasses.field(default_factory=dict)

    def to_json(self) -> dict[str, Any]:
        return {
            "intake_id": str(self.intake_id),
            "received_at": self.received_at.isoformat(),
            "source_id": str(self.source_id),
            "key_id": self.key_id,
            "scopes": sorted(s.value for s in self.scopes),
            "audience": self.audience.value,
            "ceiling": self.ceiling.value,
            "events": [e.to_json() for e in self.events],
            "rejected": self.rejected,
            "duplicates": self.duplicates,
            "heartbeats": [[at.isoformat(), sent] for at, sent in self.heartbeats],
            "hot": {str(k): [str(h) for h in v] for k, v in self.hot.items()},
        }

    @classmethod
    def from_json(cls, doc: Mapping[str, Any]) -> "Intake":
        return cls(
            intake_id=UUID(doc["intake_id"]),
            received_at=datetime.fromisoformat(doc["received_at"]),
            source_id=UUID(doc["source_id"]),
            key_id=doc["key_id"],
            scopes=frozenset(Scope(s) for s in doc["scopes"]),
            audience=Audience(doc["audience"]),
            ceiling=Verification(doc["ceiling"]),
            events=tuple(PreparedEvent.from_json(e) for e in doc["events"]),
            rejected=int(doc.get("rejected", 0)),
            duplicates=int(doc.get("duplicates", 0)),
            heartbeats=tuple(
                (datetime.fromisoformat(at), int(sent)) for at, sent in doc.get("heartbeats") or ()
            ),
            hot={UUID(k): tuple(UUID(h) for h in v) for k, v in (doc.get("hot") or {}).items()},
        )


def groups_of(
    subjects: Sequence[tuple[SubjectKind, str | None, Sequence[Handle]]],
) -> tuple[SubjectGroup, ...]:
    return tuple((kind, role, tuple(handles)) for kind, role, handles in subjects)


def _str(value: UUID | None) -> str | None:
    return str(value) if value is not None else None


def _value(level: Verification | None) -> str | None:
    return level.value if level is not None else None


def _level(value: str | None) -> Verification | None:
    return Verification(value) if value else None
