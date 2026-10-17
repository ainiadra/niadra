"""What ingestion writes for one event, beyond the shared `EventRecord` (5.2)."""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from niadra.domain.records import EventRecord, ObjectKey
from niadra.domain.vocabulary import EventKind, Speaker, Verification, Visibility

HANDOFF = "handoff"
FEEDBACK_PREFIX = "feedback."


@dataclass(frozen=True, slots=True)
class NewEvent:
    space_id: UUID
    record: EventRecord
    idempotency_key: str
    direction: str | None = None
    verification_hint: Verification | None = None
    voice: dict[str, Any] | None = None
    closes_ref: dict[str, Any] | None = None
    raw_ref: str | None = None
    skew_corrected: bool = False
    mentions: frozenset[bytes] = frozenset()
    """The keyed hashes of what its text, fields and result name a person by (`domain.privacy.raw_index`),
    so an erasure finds an event of someone else that names the target."""


@dataclass(frozen=True, slots=True)
class ConversationTouch:
    """One upsert per conversation per batch: aliases merged, times widened, `dirty` or-ed."""

    conversation_id: str
    channel: str
    first_event_at: datetime
    last_event_at: datetime
    aliases: frozenset[str] = frozenset()
    primary_handle: UUID | None = None
    dirty: bool = False

    def widen(self, other: "ConversationTouch") -> "ConversationTouch":
        return ConversationTouch(
            conversation_id=self.conversation_id,
            channel=self.channel,
            first_event_at=min(self.first_event_at, other.first_event_at),
            last_event_at=max(self.last_event_at, other.last_event_at),
            aliases=self.aliases | other.aliases,
            primary_handle=self.primary_handle or other.primary_handle,
            dirty=self.dirty or other.dirty,
        )


def starts_object_work(record: EventRecord) -> bool:
    """System events and actions go to `apply_object_event` without waiting for a session (5.2).

    Handoffs are read by the measurement and feedback by the memory module, so neither is a
    business-object event.
    """
    if record.kind is EventKind.MESSAGE:
        return False
    canonical = record.canonical_type or ""
    return canonical != HANDOFF and not canonical.startswith(FEEDBACK_PREFIX)


def marks_dirty(record: EventRecord) -> bool:
    """Only messages make a conversation dirty; the system-event path never waits for a session (5.2)."""
    return record.kind is EventKind.MESSAGE


def touches_session(record: EventRecord) -> bool:
    """Messages and actions are activity; system events, handoffs and feedback never open a session."""
    return record.kind is not EventKind.SYSTEM_EVENT and bool(record.conversation_id or record.task_id)


def export_row(record: EventRecord) -> dict[str, Any]:
    """An event as the continuous export writes it (22.3): content decrypted, storage keys left out."""
    return {
        "event_id": record.event_id,
        "kind": record.kind.value,
        "canonical_type": record.canonical_type,
        "source_id": record.source_id,
        "channel": record.channel,
        "conversation_id": record.conversation_id,
        "task_id": record.task_id,
        "handle_ids": list(record.handle_ids),
        "subjects": list(record.subjects),
        "object_refs": [
            {"type": o.type, "namespace": o.namespace, "id": o.external_id} for o in record.object_refs
        ],
        "action": record.action,
        "corrects_event_id": record.corrects_event_id,
        "speaker": record.speaker.value,
        "speaker_id": record.speaker_id,
        "visibility": record.visibility.value,
        "text": record.text,
        "fields": record.fields,
        "content_meta": record.content_meta,
        "occurred_at": record.occurred_at,
        "received_at": record.received_at,
    }


def record_from_json(data: Mapping[str, Any]) -> EventRecord:
    """An event back from its JSON form: the cold archive keeps each one as the record's JSON line (5.2)."""

    def uuid_or_none(value: Any) -> UUID | None:
        return UUID(value) if value else None

    return EventRecord(
        event_id=UUID(data["event_id"]),
        kind=EventKind(data["kind"]),
        source_id=UUID(data["source_id"]),
        channel=data["channel"],
        speaker=Speaker(data["speaker"]),
        occurred_at=datetime.fromisoformat(data["occurred_at"]),
        received_at=datetime.fromisoformat(data["received_at"]),
        handle_ids=tuple(UUID(h) for h in data["handle_ids"]),
        text=data["text"],
        conversation_id=data["conversation_id"],
        task_id=data["task_id"],
        canonical_type=data["canonical_type"],
        fields=dict(data["fields"]),
        object_refs=tuple(ObjectKey(**ref) for ref in data["object_refs"]),
        subjects=tuple(data["subjects"]),
        action=data["action"],
        visibility=Visibility(data["visibility"]),
        stt_confidence=data["stt_confidence"],
        speaker_id=data["speaker_id"],
        corrects_event_id=uuid_or_none(data["corrects_event_id"]),
        content_meta=data["content_meta"],
    )


def is_feedback(record: EventRecord) -> bool:
    return (record.canonical_type or "").startswith(FEEDBACK_PREFIX)
