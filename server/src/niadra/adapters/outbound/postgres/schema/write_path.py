"""Ingest, session and identity tables."""

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import BigInteger, CheckConstraint, Identity, Index, Integer, LargeBinary, Text, text
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column

from niadra.adapters.outbound.postgres.schema.base import SpaceBase, change_seq, dek_version, now_default


class Event(SpaceBase):
    """The immutable log (L1). Range-partitioned by `event_id`, a UUIDv7 minted on arrival."""

    __tablename__ = "events"
    space_id: Mapped[UUID] = mapped_column(primary_key=True)
    event_id: Mapped[UUID] = mapped_column(primary_key=True)
    idempotency_key: Mapped[str]
    kind: Mapped[str]
    canonical_type: Mapped[str | None]
    source_id: Mapped[UUID]
    channel: Mapped[str]
    conversation_id: Mapped[str | None]
    task_id: Mapped[str | None]
    handle_ids: Mapped[list[UUID]] = mapped_column(server_default=text("'{}'"))
    subjects: Mapped[list[Any] | None]
    object_refs: Mapped[list[Any] | None]
    closes_ref: Mapped[dict[str, Any] | None]
    action: Mapped[dict[str, Any] | None]
    corrects_event_id: Mapped[UUID | None]
    speaker: Mapped[str]
    speaker_id: Mapped[str | None]
    direction: Mapped[str | None]
    visibility: Mapped[str] = mapped_column(server_default="public")
    verification_hint: Mapped[str | None]
    content_ct: Mapped[bytes | None]
    content_meta: Mapped[dict[str, Any] | None]
    fields_ct: Mapped[bytes | None]
    voice: Mapped[dict[str, Any] | None]
    occurred_at: Mapped[datetime]
    received_at: Mapped[datetime]
    skew_corrected: Mapped[bool] = mapped_column(server_default=text("false"))
    raw_ref: Mapped[str | None]
    dek_version: Mapped[int] = dek_version()
    change_seq: Mapped[int] = change_seq()
    __table_args__ = (
        CheckConstraint("kind IN ('message', 'system_event', 'action')", name="kind"),
        Index(None, "space_id", "conversation_id", "occurred_at"),
        Index(None, "space_id", "task_id", "occurred_at", postgresql_where=text("task_id IS NOT NULL")),
        # Recent events of one source: the configuration assistant's samples (22.4).
        Index(None, "space_id", "source_id", "event_id"),
        {"postgresql_partition_by": "RANGE (event_id)"},
    )


class EventHandle(SpaceBase):
    """The log's index by handle, written in the same transaction as the event (06, 6.2)."""

    __tablename__ = "event_handles"
    space_id: Mapped[UUID] = mapped_column(primary_key=True)
    event_id: Mapped[UUID] = mapped_column(primary_key=True)
    handle_id: Mapped[UUID] = mapped_column(primary_key=True)
    received_at: Mapped[datetime]
    __table_args__ = (
        Index(None, "space_id", "handle_id", "received_at"),
        {"postgresql_partition_by": "RANGE (event_id)"},
    )


class EventMention(SpaceBase):
    """What an event's text, fields and result name a person by, keyed-hashed (`domain.privacy.raw_index`),
    written with the event: an erasure finds the events of others that name the target by it (5.14.0)."""

    __tablename__ = "event_mentions"
    space_id: Mapped[UUID] = mapped_column(primary_key=True)
    event_id: Mapped[UUID] = mapped_column(primary_key=True)
    tokens: Mapped[list[bytes]] = mapped_column(ARRAY(LargeBinary))
    __table_args__ = (Index(None, "tokens", postgresql_using="gin"),)


class TextMention(SpaceBase):
    """What a row of derived memory names a person by, keyed-hashed (`domain.privacy.raw_index`), written
    with the row by its sealer: an erasure finds the rows of others that name the target by it (5.14.0)."""

    __tablename__ = "text_mentions"
    space_id: Mapped[UUID] = mapped_column(primary_key=True)
    store: Mapped[str] = mapped_column(Text, primary_key=True)
    row_key: Mapped[str] = mapped_column(Text, primary_key=True)
    tokens: Mapped[list[bytes]] = mapped_column(ARRAY(LargeBinary))
    __table_args__ = (Index(None, "tokens", postgresql_using="gin"),)


class ObjectMention(SpaceBase):
    """What a turn recording in object storage names a person by, keyed-hashed, by its key (5.14.0)."""

    __tablename__ = "object_mentions"
    space_id: Mapped[UUID] = mapped_column(primary_key=True)
    key: Mapped[str] = mapped_column(Text, primary_key=True)
    tokens: Mapped[list[bytes]] = mapped_column(ARRAY(LargeBinary))
    __table_args__ = (Index(None, "tokens", postgresql_using="gin"),)


def _event_keys(generation: int) -> type[SpaceBase]:
    # Three rotating dedupe tables: ingest inserts into the current one and reads the previous
    # one; `dedupe_rotate` truncates only the retired one, so ingestion never waits on it (5.2).
    return type(
        f"EventKeys{generation}",
        (SpaceBase,),
        {
            "__tablename__": f"event_keys_{generation}",
            "__annotations__": {
                "space_id": Mapped[UUID],
                "idempotency_key": Mapped[str],
                "event_id": Mapped[UUID],
                "received_at": Mapped[datetime],
            },
            "space_id": mapped_column(primary_key=True),
            "idempotency_key": mapped_column(primary_key=True),
            "__table_args__": {"postgresql_partition_by": "HASH (space_id, idempotency_key)"},
        },
    )


EventKeys0 = _event_keys(0)
EventKeys1 = _event_keys(1)
EventKeys2 = _event_keys(2)


class IngestIntake(SpaceBase):
    """Batches acknowledged and not stored yet, alive for milliseconds (5.2): their prepared events, masked
    and sealed, applied in `seq` order and deleted once stored; one that failed every attempt is kept with
    `failed_at`. `skip` holds the keys an earlier batch claimed. The
    order comes from the database, not from the ids: two processes' clocks never agree to the millisecond,
    and a batch sent after another's answer always draws a later number."""

    __tablename__ = "ingest_intake"
    space_id: Mapped[UUID] = mapped_column(primary_key=True)
    intake_id: Mapped[UUID] = mapped_column(primary_key=True)
    seq: Mapped[int] = mapped_column(BigInteger, Identity(always=True))
    received_at: Mapped[datetime]
    source_id: Mapped[UUID]
    body_ct: Mapped[bytes]
    dek_version: Mapped[int] = dek_version()
    skip: Mapped[list[str]] = mapped_column(server_default=text("'{}'"))
    attempts: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    failed_at: Mapped[datetime | None]
    """Set when the batch failed every attempt: kept aside, out of the order, until an admin retries it."""


class Conversation(SpaceBase):
    __tablename__ = "conversations"
    space_id: Mapped[UUID] = mapped_column(primary_key=True)
    conversation_id: Mapped[str] = mapped_column(primary_key=True)
    aliases: Mapped[list[str]] = mapped_column(server_default=text("'{}'"))
    channel: Mapped[str]
    primary_handle: Mapped[UUID | None]
    first_event_at: Mapped[datetime]
    last_event_at: Mapped[datetime]
    dirty: Mapped[bool] = mapped_column(server_default=text("true"))
    change_seq: Mapped[int] = change_seq()
    __table_args__ = (
        Index(None, "aliases", postgresql_using="gin"),
        Index(None, "space_id", "primary_handle", "last_event_at"),
    )


class IngestImport(SpaceBase):
    """A CSV or JSONL file ingested by the `import_file` task, part by part (5.2, 5.3)."""

    __tablename__ = "ingest_imports"
    space_id: Mapped[UUID] = mapped_column(primary_key=True)
    import_id: Mapped[UUID] = mapped_column(primary_key=True)
    source_id: Mapped[UUID]
    key_id: Mapped[str]
    scopes: Mapped[list[str]]
    format: Mapped[str]
    status: Mapped[str]
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    sha256: Mapped[str]
    header: Mapped[list[str] | None]
    part_rows: Mapped[list[int]]
    next_part: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    next_row: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    accepted: Mapped[int] = mapped_column(BigInteger, server_default=text("0"))
    duplicates: Mapped[int] = mapped_column(BigInteger, server_default=text("0"))
    rejected: Mapped[int] = mapped_column(BigInteger, server_default=text("0"))
    errors: Mapped[list[Any]] = mapped_column(server_default=text("'[]'"))
    created_at: Mapped[datetime]
    finished_at: Mapped[datetime | None]
    __table_args__ = (
        CheckConstraint("format IN ('csv', 'jsonl')", name="format"),
        CheckConstraint("status IN ('queued', 'running', 'completed', 'failed')", name="status"),
    )


class SourceHeartbeat(SpaceBase):
    __tablename__ = "source_heartbeats"
    space_id: Mapped[UUID] = mapped_column(primary_key=True)
    source_id: Mapped[UUID] = mapped_column(primary_key=True)
    window_start: Mapped[datetime] = mapped_column(primary_key=True)
    sent: Mapped[int] = mapped_column(BigInteger, server_default=text("0"))
    received: Mapped[int] = mapped_column(BigInteger, server_default=text("0"))
    rejected: Mapped[int] = mapped_column(BigInteger, server_default=text("0"))
    unmapped: Mapped[int] = mapped_column(BigInteger, server_default=text("0"))
    last_event_at: Mapped[datetime | None]


class Session(SpaceBase):
    __tablename__ = "sessions"
    space_id: Mapped[UUID] = mapped_column(primary_key=True)
    session_id: Mapped[UUID] = mapped_column(primary_key=True)
    kind: Mapped[str]
    conversation_id: Mapped[str | None]
    task_id: Mapped[str | None]
    source_id: Mapped[UUID]
    channel: Mapped[str]
    profile_id: Mapped[UUID | None]
    opened_at: Mapped[datetime]
    last_event_at: Mapped[datetime]
    closed_at: Mapped[datetime | None]
    close_reason: Mapped[str | None]
    proven_level: Mapped[str | None]
    proven_until: Mapped[datetime | None]
    initiated_by: Mapped[str | None]
    extracted_version: Mapped[str | None]
    change_seq: Mapped[int] = change_seq()
    reopened: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    ended_at: Mapped[datetime | None]
    __table_args__ = (
        CheckConstraint("kind IN ('conversation', 'task')", name="kind"),
        Index("ix_sessions_open", "last_event_at", postgresql_where=text("closed_at IS NULL")),
        Index(
            "uq_sessions_open_conversation",
            "space_id",
            "conversation_id",
            unique=True,
            postgresql_where=text("closed_at IS NULL AND conversation_id IS NOT NULL"),
        ),
        Index(
            "uq_sessions_open_task",
            "space_id",
            "task_id",
            unique=True,
            postgresql_where=text("closed_at IS NULL AND task_id IS NOT NULL"),
        ),
        Index(None, "space_id", "profile_id", "opened_at"),
        # Closed sessions of a conversation, for `forget` by conversation (8.4).
        Index(None, "space_id", "conversation_id"),
    )


class SessionEnd(SpaceBase):
    """An end (`conversation.ended`, `task.ended`) that arrived before any turn opened its session: the
    turns at or before its time close as soon as they arrive (5.4). The thread's id and times only, until
    `expires_at` (the idempotency window)."""

    __tablename__ = "session_ends"
    space_id: Mapped[UUID] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(primary_key=True)
    thread_id: Mapped[str] = mapped_column(primary_key=True)
    ended_at: Mapped[datetime]
    expires_at: Mapped[datetime]
    __table_args__ = (
        CheckConstraint("kind IN ('conversation', 'task')", name="kind"),
        Index(None, "expires_at"),
    )


class Profile(SpaceBase):
    __tablename__ = "profiles"
    space_id: Mapped[UUID] = mapped_column(primary_key=True)
    profile_id: Mapped[UUID] = mapped_column(primary_key=True)
    kind: Mapped[str]
    org_type: Mapped[str | None]
    pseudonym: Mapped[str]
    membership_version: Mapped[int] = mapped_column(BigInteger, server_default=text("1"))
    privacy_version: Mapped[int] = mapped_column(BigInteger, server_default=text("1"))
    created_at: Mapped[datetime] = now_default()
    retired_at: Mapped[datetime | None]
    erased_at: Mapped[datetime | None]
    """Set by `forget`: the row stays as a minimal tombstone so `privacy_version` keeps rising (5.11)."""
    change_seq: Mapped[int] = change_seq()
    __table_args__ = (
        CheckConstraint("kind IN ('person', 'account', 'partner', 'staff')", name="kind"),
        Index(None, "space_id", "pseudonym", unique=True),
    )


class HandleRow(SpaceBase):
    __tablename__ = "handles"
    space_id: Mapped[UUID] = mapped_column(primary_key=True)
    handle_id: Mapped[UUID] = mapped_column(primary_key=True)
    type: Mapped[str]
    scope: Mapped[str] = mapped_column(server_default="")
    subject_kind: Mapped[str]
    value_hmac: Mapped[bytes]
    value_ct: Mapped[bytes | None]
    status: Mapped[str] = mapped_column(server_default="active")
    level: Mapped[str] = mapped_column(server_default="V0")
    first_seen: Mapped[datetime]
    last_seen: Mapped[datetime]
    # A number that changed hands leaves its handle: `value_hmac` becomes a marker no lookup
    # matches, and the keyed hash moves here, so erasure and the Console still find the old owner.
    former_hmac: Mapped[bytes | None]
    # Joined by a claim (`declared`, `explicit_identify`) and not yet confirmed: reads through it stay at V1.
    provisional: Mapped[bool] = mapped_column(server_default=text("false"))
    dek_version: Mapped[int] = dek_version()
    change_seq: Mapped[int] = change_seq()
    __table_args__ = (
        Index(None, "space_id", "type", "scope", "value_hmac", unique=True),
        Index(
            "ix_handles_former_hmac",
            "space_id",
            "type",
            "scope",
            "former_hmac",
            postgresql_where=text("former_hmac IS NOT NULL"),
        ),
        # The customer list walks handles by activity, newest or oldest first; the kind is a key column so
        # a filter by kind is checked in the index, before any row is fetched.
        Index("ix_handles_last_seen", "space_id", "last_seen", "handle_id", "subject_kind"),
    )


class ProfileMembership(SpaceBase):
    __tablename__ = "profile_membership"
    space_id: Mapped[UUID] = mapped_column(primary_key=True)
    handle_id: Mapped[UUID] = mapped_column(primary_key=True)
    profile_id: Mapped[UUID] = mapped_column(index=True)
    version: Mapped[int] = mapped_column(BigInteger)
    change_seq: Mapped[int] = change_seq()


class Assertion(SpaceBase):
    """Immutable identity evidence; only `retracted_at` changes (5.3)."""

    __tablename__ = "assertions"
    space_id: Mapped[UUID] = mapped_column(primary_key=True)
    assertion_id: Mapped[UUID] = mapped_column(primary_key=True)
    handle_a: Mapped[UUID]
    handle_b: Mapped[UUID]
    method: Mapped[str]
    origin_event_id: Mapped[UUID | None]
    confidence: Mapped[float]
    level_at_capture: Mapped[str]
    state: Mapped[str] = mapped_column(server_default="applied")
    actor: Mapped[str | None]
    reason: Mapped[str | None]
    created_at: Mapped[datetime]
    retracted_at: Mapped[datetime | None]
    change_seq: Mapped[int] = change_seq()
    __table_args__ = (
        CheckConstraint("state IN ('applied', 'pending', 'rejected')", name="state"),
        Index(None, "space_id", "handle_a", postgresql_where=text("retracted_at IS NULL")),
        Index(None, "space_id", "handle_b", postgresql_where=text("retracted_at IS NULL")),
        # Retracted evidence is erased too (8.4), so `forget` needs every row of a handle.
        Index("ix_assertions_all_handle_a", "space_id", "handle_a"),
        Index("ix_assertions_all_handle_b", "space_id", "handle_b"),
    )


class MergeLog(SpaceBase):
    __tablename__ = "merge_log"
    space_id: Mapped[UUID] = mapped_column(primary_key=True)
    merge_id: Mapped[UUID] = mapped_column(primary_key=True)
    op: Mapped[str]
    from_profiles: Mapped[list[UUID]]
    to_profiles: Mapped[list[UUID]]
    assertion_id: Mapped[UUID | None]
    handles_moved: Mapped[list[UUID]]
    actor: Mapped[str]
    reason: Mapped[str | None]
    created_at: Mapped[datetime]
    reversed_at: Mapped[datetime | None]
    change_seq: Mapped[int] = change_seq()
    __table_args__ = (CheckConstraint("op IN ('merge', 'unmerge')", name="op"),)


class ProfileLink(SpaceBase):
    """Person <-> organization links, tied to handles so an unmerge knows where each goes (21.2)."""

    __tablename__ = "profile_links"
    space_id: Mapped[UUID] = mapped_column(primary_key=True)
    link_id: Mapped[UUID] = mapped_column(primary_key=True)
    person_handle_id: Mapped[UUID]
    org_handle_id: Mapped[UUID]
    role: Mapped[str]
    can_see_contacts: Mapped[bool] = mapped_column(server_default=text("false"))
    valid_from: Mapped[datetime]
    valid_to: Mapped[datetime | None]
    method: Mapped[str]
    origin_event_id: Mapped[UUID | None]
    confidence: Mapped[float]
    change_seq: Mapped[int] = change_seq()
    __table_args__ = (
        Index(None, "space_id", "person_handle_id", postgresql_where=text("valid_to IS NULL")),
        Index(None, "space_id", "org_handle_id", postgresql_where=text("valid_to IS NULL")),
    )


class BlockedValue(SpaceBase):
    __tablename__ = "blocked_values"
    space_id: Mapped[UUID] = mapped_column(primary_key=True)
    type: Mapped[str] = mapped_column(primary_key=True)
    value_hmac: Mapped[bytes] = mapped_column(primary_key=True)
    reason: Mapped[str | None]


class IdentitySuggestion(SpaceBase):
    __tablename__ = "identity_suggestions"
    space_id: Mapped[UUID] = mapped_column(primary_key=True)
    suggestion_id: Mapped[UUID] = mapped_column(primary_key=True)
    handle_a: Mapped[UUID]
    handle_b: Mapped[UUID]
    score: Mapped[float]
    features: Mapped[dict[str, Any]]
    status: Mapped[str] = mapped_column(server_default="open")
    created_at: Mapped[datetime]
    __table_args__ = (
        CheckConstraint("status IN ('open', 'accepted', 'rejected')", name="status"),
        # The open suggestions of a customer's handles, for the relationship graph; reviewed ones stay out.
        Index(
            "ix_identity_suggestions_open_a", "space_id", "handle_a", postgresql_where=text("status = 'open'")
        ),
        Index(
            "ix_identity_suggestions_open_b", "space_id", "handle_b", postgresql_where=text("status = 'open'")
        ),
    )


class Decision(SpaceBase):
    __tablename__ = "decisions"
    space_id: Mapped[UUID] = mapped_column(primary_key=True)
    decision_id: Mapped[UUID] = mapped_column(primary_key=True)
    task: Mapped[str]
    input_hash: Mapped[bytes]
    model: Mapped[str]
    model_version: Mapped[str]
    label: Mapped[str]
    probability: Mapped[float]
    threshold: Mapped[float]
    subject_ref: Mapped[str | None]
    created_at: Mapped[datetime]

    # The pairs of the contradiction band the decision model already judged are not asked again.
    __table_args__ = (
        Index(
            "ix_decisions_contradiction_subject",
            "space_id",
            "subject_ref",
            postgresql_where=text("task = 'contradiction'"),
        ),
    )


class ExtractionRun(SpaceBase):
    __tablename__ = "extraction_runs"
    space_id: Mapped[UUID] = mapped_column(primary_key=True)
    session_id: Mapped[UUID] = mapped_column(primary_key=True)
    versions_key: Mapped[str] = mapped_column(primary_key=True)
    model: Mapped[str]
    provider: Mapped[str]
    input_tokens: Mapped[int] = mapped_column(Integer)
    output_tokens: Mapped[int] = mapped_column(Integer)
    cached_tokens: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    cost_micros: Mapped[int] = mapped_column(BigInteger)
    status: Mapped[str]
    dropped_items: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    result_ct: Mapped[bytes | None]
    dek_version: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime]
    applied_profile_id: Mapped[UUID | None]
    """The profile the memory module applied the run to; None while it has not, or when it applied nothing."""
    applied_work: Mapped[int | None] = mapped_column(BigInteger)
    """The profile's `compile` request that covers the applied run (`profile_work.requested`); ingest status
    says `ready` once `done` reaches it. None until the run is applied; 0 when it asked for no compilation."""
    __table_args__ = (CheckConstraint("status IN ('ok', 'invalid', 'failed', 'minimal')", name="status"),)


class LlmSpend(SpaceBase):
    """Daily LLM spend per space, checked before every call so a budget cap is never exceeded."""

    __tablename__ = "llm_spend"
    space_id: Mapped[UUID] = mapped_column(primary_key=True)
    day: Mapped[datetime] = mapped_column(primary_key=True)
    purpose: Mapped[str] = mapped_column(primary_key=True)
    cost_micros: Mapped[int] = mapped_column(BigInteger, server_default=text("0"))
    calls: Mapped[int] = mapped_column(Integer, server_default=text("0"))
