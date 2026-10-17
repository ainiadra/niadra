"""The erasure cascade (8.4, 21.6) as data: what a `forget` targets and what each step reports back."""

import hashlib
import json
from dataclasses import dataclass, field, replace
from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

# Order of 8.4. Every step lives in the module that owns the rows; privacy only drives the cascade.
EVENTS_STEP = "ingest"
SESSION_ARTIFACTS_STEP = "extract"
EVIDENCE_STEPS = ("memory", "traits", "objects", "triggers")
"""Right after each batch of erased events, in the same transaction: mentions, trait evidence, the actions
and object events those events recorded, and trigger firings that pointed at them go, and so do facts and
traits left without evidence (L17)."""
ORIGIN_STEPS = (
    "memory",
    "traits",
    "objects",
    "identity",
    "compile",
    "measure",
    "extract",
    "sessions",
    "triggers",
    "review",
    "notify",
    # The agent features' steps (24.5): turns, signals, objects, coordinate, measure.
    "turns",
    "feed",
    "agent_state",
    "typed",
    "coordinate",
    "signals",
    "outcomes",
)
"""Then everything originated by the target: facts (including those about an account), episodes, open
items, attributes, traits, owned objects and the personal content of actions, links, packs, measurement,
the extraction results of the target's sessions (before the sessions themselves go), sessions, and the
target's trigger firings, review samples and webhook deliveries."""


class TargetKind(StrEnum):
    PROFILE = "profile"
    HANDLE = "handle"
    CONVERSATION = "conversation"


class ErasureStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class ErasureScope:
    """Resolved once, when the request is accepted, so the cascade survives identity erasing the handles.

    For a profile or a handle, `handle_ids` are what goes. For a conversation they only locate the data:
    participants erase what the conversation produced and keep the rest of the person's memory.
    `event_ids` is set only on the evidence step: the events erased in the current batch.
    `pseudonym` is the profile's, kept because webhook bodies name their subject only by it (7.5).
    `session_ids` is set only on the `extract` step, read from `sessions` before they are erased.
    `archived_event_ids` are the target's events already in the cold archive when the cascade ran: what
    they produced goes as if they were live. Kept with the request, so the ledger re-applies it to a
    restored dump even after the archive's files lost those rows.
    `conversation_hashes` are the space's keyed hashes of the target's conversations, found before their
    rows go: turn records are kept and erased by them (24.5).
    `subject_hashes` are the keyed hashes coordination names the target's handles by, found before the
    handles go: its claims, contacts, budgets, effects, commitments and handoffs are erased by them (24.5.5).
    """

    request_id: UUID
    kind: TargetKind
    handle_ids: tuple[UUID, ...]
    profile_id: UUID | None
    conversation_id: str | None
    requested_at: datetime
    event_ids: tuple[UUID, ...] = ()
    pseudonym: str | None = None
    session_ids: tuple[UUID, ...] = ()
    archived_event_ids: tuple[UUID, ...] = ()
    conversation_hashes: tuple[bytes, ...] = ()
    subject_hashes: tuple[bytes, ...] = ()

    def with_events(self, event_ids: tuple[UUID, ...]) -> "ErasureScope":
        return replace(self, event_ids=event_ids)

    def with_sessions(self, session_ids: tuple[UUID, ...]) -> "ErasureScope":
        return replace(self, session_ids=session_ids)

    def with_archive(self, event_ids: tuple[UUID, ...], handle_ids: tuple[UUID, ...]) -> "ErasureScope":
        """What the cold archive holds of the target; a conversation also learns the handles that spoke in
        it there, so its episodes and settled values are found."""
        events = tuple(sorted({*self.archived_event_ids, *event_ids}))
        handles = self.handle_ids
        if self.kind is TargetKind.CONVERSATION:
            handles = tuple(dict.fromkeys((*self.handle_ids, *handle_ids)))
        return replace(self, archived_event_ids=events, handle_ids=handles)

    def with_conversation_hashes(self, hashes: tuple[bytes, ...]) -> "ErasureScope":
        return replace(self, conversation_hashes=tuple(sorted({*self.conversation_hashes, *hashes})))

    def with_subject_hashes(self, hashes: tuple[bytes, ...]) -> "ErasureScope":
        return replace(self, subject_hashes=tuple(sorted({*self.subject_hashes, *hashes})))

    def to_json(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "request_id": str(self.request_id),
            "kind": self.kind.value,
            "handle_ids": [str(h) for h in self.handle_ids],
            "profile_id": str(self.profile_id) if self.profile_id else None,
            "conversation_id": self.conversation_id,
            "requested_at": self.requested_at.isoformat(),
            "pseudonym": self.pseudonym,
        }
        if self.archived_event_ids:
            data["archived_event_ids"] = [str(e) for e in self.archived_event_ids]
        if self.conversation_hashes:
            data["conversation_hashes"] = [h.hex() for h in self.conversation_hashes]
        if self.subject_hashes:
            data["subject_hashes"] = [h.hex() for h in self.subject_hashes]
        return data

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> "ErasureScope":
        return cls(
            request_id=UUID(data["request_id"]),
            kind=TargetKind(data["kind"]),
            handle_ids=tuple(UUID(h) for h in data["handle_ids"]),
            profile_id=UUID(data["profile_id"]) if data.get("profile_id") else None,
            conversation_id=data.get("conversation_id"),
            requested_at=datetime.fromisoformat(data["requested_at"]),
            pseudonym=data.get("pseudonym"),
            archived_event_ids=tuple(UUID(e) for e in data.get("archived_event_ids") or ()),
            conversation_hashes=tuple(bytes.fromhex(h) for h in data.get("conversation_hashes") or ()),
            subject_hashes=tuple(bytes.fromhex(h) for h in data.get("subject_hashes") or ()),
        )


@dataclass(frozen=True, slots=True)
class ErasedRow:
    """One row a participant erased: its key goes to the tombstones the continuous export replays (22.3).

    `blob_keys` are object-storage keys the row pointed at (raw payloads, media), deleted by privacy.
    `change_seq` is the row's last sequence before erasure; it finds the export runs that carried it.
    """

    table: str
    key: dict[str, str]
    blob_keys: tuple[str, ...] = ()
    change_seq: int | None = None
    object_ref: str | None = None
    """`type:namespace:id` of the object a row bound to one was about: the receipt counts
    them, and the webhook lists the objects, so the company records again in its own system what it needs."""


@dataclass(frozen=True, slots=True)
class ErasureRequest:
    request_id: UUID
    target_kind: TargetKind
    target_id: str
    status: ErasureStatus
    requested_by: str
    requested_at: datetime
    completed_at: datetime | None = None
    counts: dict[str, Any] = field(default_factory=dict)
    receipt_hash: bytes | None = None

    @property
    def scope(self) -> ErasureScope:
        return ErasureScope.from_json(self.counts["scope"])

    @property
    def erased(self) -> dict[str, int]:
        return dict(self.counts.get("tables", {}))

    @property
    def export_run_ids(self) -> list[str]:
        return list(self.counts.get("export_runs", []))


def request_id_for(space_id: UUID, idempotency_key: str) -> UUID:
    """Derived from the Idempotency-Key, so a retried `POST /v1/forget` lands on the same request (7.0)."""
    digest = bytearray(hashlib.sha256(f"forget:{space_id}:{idempotency_key}".encode()).digest()[:16])
    digest[6] = (digest[6] & 0x0F) | 0x80  # RFC 9562 version 8: custom, hash-derived
    digest[8] = (digest[8] & 0x3F) | 0x80
    return UUID(bytes=bytes(digest))


OBJECT_BOUND = "object_bound_rows_erased"
OBJECTS_TOUCHED = "objects"
MAX_OBJECTS_TOUCHED = 100
"""Objects a receipt lists at most; the count covers every row."""


def tally(counts: dict[str, Any], rows: list[ErasedRow], export_runs: list[UUID]) -> dict[str, Any]:
    """Counts per table and the export runs that already carried erased rows, merged into `counts`; the
    `COLD_FEEDBACK` ids go to their own list, for `erase_cold`."""
    tables = dict(counts.get("tables", {}))
    cold = set(counts.get(COLD_FEEDBACK, []))
    bound = int(counts.get(OBJECT_BOUND, 0))
    objects = set(counts.get(OBJECTS_TOUCHED, []))
    for row in rows:
        if row.table == COLD_FEEDBACK:
            cold.add(row.key["event_id"])
        else:
            tables[row.table] = tables.get(row.table, 0) + 1
        if row.object_ref is not None:
            bound += 1
            objects.add(row.object_ref)
    runs = sorted(set(counts.get("export_runs", [])) | {str(r) for r in export_runs})
    merged = {**counts, "tables": tables, "export_runs": runs}
    if bound:
        merged[OBJECT_BOUND] = bound
        merged[OBJECTS_TOUCHED] = sorted(objects)[:MAX_OBJECTS_TOUCHED]
    if cold:
        merged[COLD_FEEDBACK] = sorted(cold)
    return merged


def erasure_receipt_hash(request: ErasureRequest, completed_at: datetime) -> bytes:
    """The erasure receipt (8.4): a digest of what was asked, what went and when; ids and counts only."""
    body = {
        "request_id": str(request.request_id),
        "target_kind": request.target_kind.value,
        "target_id": request.target_id,
        "requested_at": request.requested_at.isoformat(),
        "completed_at": completed_at.isoformat(),
        "tables": request.erased,
        "export_runs": request.export_run_ids,
    }
    return hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":")).encode()).digest()


def blob_prefixes(space_id: UUID, handle_ids: tuple[UUID, ...]) -> list[str]:
    """Raw payloads and media live under space and handle, so a prefix delete reaches them (5.14.0)."""
    return [f"{kind}/{space_id}/{handle}/" for handle in handle_ids for kind in ("raw", "media")]


FEEDBACK_AUDIT_FIELDS = ("action", "fact_id", "open_item_id", "requested_by")
"""What a company feedback event keeps once a row it acted on is erased: ids and kinds only. The value
of a correction and the free-text reason go, since they may repeat what the customer said (8.4)."""
FEEDBACK_VALUES = "feedback_values"
"""The count, on the receipt, of feedback events that lost their values to the erasure."""
OPEN_ITEM_FEEDBACK = "open_item_feedback"
"""A row an erasure reports for an open item it took that the company's feedback resolved: its key names
the feedback event, which the cascade then rewrites like the feedback on an erased fact."""
FEEDBACK_LINKS = ("fact_mentions", OPEN_ITEM_FEEDBACK)
"""The erased rows whose `event_id` may be the company's feedback on what the erasure took."""
COLD_FEEDBACK = "cold_feedback"
"""A row naming a feedback event on what the erasure took that the cold archive may hold: past the hot
window the event lives only there, and a month being archived holds a copy made before the live row lost
its values. `tally` keeps these ids with the request (never on the receipt), and `erase_cold` rewrites
those archived rows with ids and kinds only, in the same pass that drops the target's own rows (5.14.0).
The archive keeps every event as it was sent until an erasure reaches it; values are not stripped before
archiving, so exports and timelines past the hot window still carry the company's reason."""
AGENT_NOTE_EVIDENCE = "agent_note_evidence"
"""A row per agent note or proposal that named an erased conversation as its evidence and lost that id:
the note holds no customer and stays, but a conversation id is often a phone number or an e-mail."""


def feedback_audit(fields: dict[str, Any]) -> dict[str, Any]:
    return {name: fields[name] for name in FEEDBACK_AUDIT_FIELDS if fields.get(name) is not None}


def origin_step(scope: ErasureScope) -> bool:
    """Participants in both lists are called twice: with the batch's events, then for the target itself."""
    return not scope.event_ids


# The erasure ledger: what each completed erasure targeted, kept in object storage because a
# space restored from a dump forgets the requests made after it. It outlives the dumps it guards.
LEDGER_DAYS = 40
"""Longer than the 35 days the space dumps are kept (the chart's backup.retentionDays in deploy/)."""


def ledger_prefix(space_id: UUID) -> str:
    return f"privacy/erasures/{space_id}/"


def ledger_key(space_id: UUID, request: ErasureRequest, written_at: datetime) -> str:
    """Grouped by day, so the retention sweep drops whole days without reading them."""
    return f"{ledger_prefix(space_id)}{written_at:%Y-%m-%d}/{request.request_id}.json"


def ledger_entry(request: ErasureRequest) -> dict[str, Any]:
    """Ids only: the scope, how the request was made and the digest of its body (never a value)."""
    return {
        "request_id": str(request.request_id),
        "target_kind": request.target_kind.value,
        "target_id": request.target_id,
        "requested_by": request.requested_by,
        "requested_at": request.requested_at.isoformat(),
        "scope": request.counts["scope"],
        "request_hash": request.counts.get("request_hash"),
    }


def request_from_ledger(entry: dict[str, Any], restore_key: str) -> ErasureRequest:
    """The request again, pending, for a space that came back from a dump taken before it."""
    counts: dict[str, Any] = {"scope": entry["scope"], "reapplied_after_restore": restore_key}
    if entry.get("request_hash"):
        counts["request_hash"] = entry["request_hash"]
    return ErasureRequest(
        request_id=UUID(entry["request_id"]),
        target_kind=TargetKind(entry["target_kind"]),
        target_id=entry["target_id"],
        status=ErasureStatus.PENDING,
        requested_by=entry["requested_by"],
        requested_at=datetime.fromisoformat(entry["requested_at"]),
        counts=counts,
    )


def ledger_aad(space_id: UUID, request_id: UUID) -> bytes:
    return f"erasure_ledger:{space_id}:{request_id}".encode()
