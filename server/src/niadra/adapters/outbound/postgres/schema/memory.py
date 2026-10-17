"""Memory, object and trait tables.

Memory tables are hash-partitioned by `origin_handle_id` and carry it in every key: every
read of memory starts from the handles of a profile (L6), so a query only touches their
partitions, and an unmerge moves nothing because facts follow their origin handle.
"""

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import BigInteger, CheckConstraint, Index, Integer, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column

from niadra.adapters.outbound.postgres.schema.base import SpaceBase, change_seq, dek_version
from niadra.domain.identity.display import NAME_PREDICATES

HASHED_BY_HANDLE = {"postgresql_partition_by": "HASH (origin_handle_id)"}


class VectorColumns:
    """The sealed vector a navigable item carries for history search (16.2). It is sealed on its own,
    under `embedding_dek_version`, so attaching it never rewrites the row's content columns."""

    embedding_ct: Mapped[bytes | None]
    embedding_model: Mapped[str | None]
    embedding_dim: Mapped[int | None] = mapped_column(Integer)
    embedding_dek_version: Mapped[int | None] = mapped_column(Integer)


class FactRow(VectorColumns, SpaceBase):
    __tablename__ = "facts"
    space_id: Mapped[UUID] = mapped_column(primary_key=True)
    origin_handle_id: Mapped[UUID] = mapped_column(primary_key=True)
    fact_id: Mapped[UUID] = mapped_column(primary_key=True)
    origin_conversation_id: Mapped[str | None]
    predicate: Mapped[str]
    namespace: Mapped[str] = mapped_column(server_default="tenant")
    value_ct: Mapped[bytes]
    value_hmac: Mapped[bytes]
    category: Mapped[str]
    sensitivity: Mapped[str]
    speaker: Mapped[str]
    confidence: Mapped[float]
    valid_at: Mapped[datetime]
    invalid_at: Mapped[datetime | None]
    created_at: Mapped[datetime]
    expired_at: Mapped[datetime | None]
    status: Mapped[str]
    times_seen: Mapped[int] = mapped_column(Integer, server_default=text("1"))
    last_confirmed_at: Mapped[datetime]
    about_handle_id: Mapped[UUID | None]
    object_ref: Mapped[dict[str, Any] | None]
    mention_handle_ids: Mapped[list[UUID]] = mapped_column(server_default=text("'{}'"))
    dek_version: Mapped[int] = dek_version()
    change_seq: Mapped[int] = change_seq()
    valid_until: Mapped[datetime | None]
    # Distinct sessions of the evidence.
    sessions_seen: Mapped[int] = mapped_column(Integer)
    # The purposes it was collected for, `{purpose: legal basis}` as they were then (13, 6.6); NULL until
    # tagged, `{}` when no rule of the space names one.
    purposes: Mapped[dict[str, Any] | None]
    # Who besides the person may read a fact about an object; NULL for every other fact.
    audience: Mapped[str | None]
    __table_args__ = (
        UniqueConstraint("space_id", "origin_handle_id", "predicate", "value_hmac"),
        Index(None, "space_id", "change_seq"),
        # The facts about an object, across the handles of its participants.
        Index(
            "ix_facts_object_ref",
            "space_id",
            text("(object_ref ->> 'type')"),
            text("(object_ref ->> 'namespace')"),
            text("(object_ref ->> 'external_id')"),
            postgresql_where=text("object_ref IS NOT NULL"),
        ),
        # A name typed in the Console's customer search, by its keyed digest, across the space's handles.
        Index(
            "ix_facts_name_value",
            "space_id",
            "value_hmac",
            postgresql_where=text(f"predicate IN ({', '.join(repr(p) for p in NAME_PREDICATES)})"),
        ),
        CheckConstraint("status IN ('active', 'expired', 'quarantined', 'retracted')", name="status"),
        CheckConstraint("audience IN ('shared', 'internal')", name="audience"),
        HASHED_BY_HANDLE,
    )


class FactMention(SpaceBase):
    __tablename__ = "fact_mentions"
    space_id: Mapped[UUID] = mapped_column(primary_key=True)
    origin_handle_id: Mapped[UUID] = mapped_column(primary_key=True)
    fact_id: Mapped[UUID] = mapped_column(primary_key=True)
    event_id: Mapped[UUID] = mapped_column(primary_key=True)
    change_seq: Mapped[int] = change_seq()
    # The event is the company's feedback on the row (retract, correct, restore): lineage, never evidence.
    feedback: Mapped[bool] = mapped_column(server_default=text("false"))
    __table_args__ = (
        Index(None, "space_id", "event_id"),
        Index(None, "space_id", "change_seq"),
        HASHED_BY_HANDLE,
    )


class FactRelation(SpaceBase):
    __tablename__ = "fact_relations"
    space_id: Mapped[UUID] = mapped_column(primary_key=True)
    a_handle_id: Mapped[UUID] = mapped_column(primary_key=True)
    a_fact_id: Mapped[UUID] = mapped_column(primary_key=True)
    b_handle_id: Mapped[UUID] = mapped_column(primary_key=True)
    b_fact_id: Mapped[UUID] = mapped_column(primary_key=True)
    relation: Mapped[str] = mapped_column(primary_key=True)
    # `model`, `rule` or `band`; the band's edges only warn (`domain.memory.contradictions`).
    origin: Mapped[str]
    __table_args__ = (
        CheckConstraint("relation IN ('supersedes', 'contradicts')", name="relation"),
        {"postgresql_partition_by": "HASH (a_handle_id)"},
    )


class EpisodeRow(VectorColumns, SpaceBase):
    __tablename__ = "episodes"
    space_id: Mapped[UUID] = mapped_column(primary_key=True)
    origin_handle_id: Mapped[UUID] = mapped_column(primary_key=True)
    episode_id: Mapped[UUID] = mapped_column(primary_key=True)
    version: Mapped[int] = mapped_column(Integer, primary_key=True)
    session_id: Mapped[UUID]
    conversation_id: Mapped[str | None]
    channel: Mapped[str]
    source_id: Mapped[UUID]
    about_handle_id: Mapped[UUID | None]
    summary_ct: Mapped[bytes]
    intent: Mapped[str]
    category: Mapped[str]
    normalized_category: Mapped[str]
    outcome: Mapped[str]
    resolution_ct: Mapped[bytes | None]
    sentiment: Mapped[float | None]
    initiated_by: Mapped[str | None]
    started_at: Mapped[datetime]
    ended_at: Mapped[datetime]
    is_current: Mapped[bool] = mapped_column(server_default=text("true"))
    dek_version: Mapped[int] = dek_version()
    change_seq: Mapped[int] = change_seq()
    purposes: Mapped[dict[str, Any] | None]
    spoken_by: Mapped[str | None]
    matter: Mapped[str | None]
    matter_label: Mapped[str | None]
    # The objects the conversation was about, `[{type, namespace, external_id}]`.
    object_refs: Mapped[list[Any] | None]
    __table_args__ = (
        Index(None, "space_id", "origin_handle_id", "normalized_category", "ended_at"),
        Index(None, "space_id", "change_seq"),
        HASHED_BY_HANDLE,
    )


class OpenItemRow(VectorColumns, SpaceBase):
    __tablename__ = "open_items"
    space_id: Mapped[UUID] = mapped_column(primary_key=True)
    origin_handle_id: Mapped[UUID] = mapped_column(primary_key=True)
    item_id: Mapped[UUID] = mapped_column(primary_key=True)
    about_handle_id: Mapped[UUID | None]
    description_ct: Mapped[bytes]
    owner: Mapped[str]
    due_at: Mapped[datetime | None]
    status: Mapped[str]
    origin: Mapped[dict[str, Any]]
    object_type: Mapped[str | None]
    object_namespace: Mapped[str | None]
    object_external_id: Mapped[str | None]
    expected_operation: Mapped[str | None]
    closed_by_action_id: Mapped[UUID | None]
    closed_at: Mapped[datetime | None]
    created_at: Mapped[datetime]
    dek_version: Mapped[int] = dek_version()
    change_seq: Mapped[int] = change_seq()
    # The company's feedback event that resolved the item: an erasure of the item rewrites it (8.4).
    closed_by_event_id: Mapped[UUID | None]
    purposes: Mapped[dict[str, Any] | None]
    # A promise, a dispute or a request.
    kind: Mapped[str]
    audience: Mapped[str | None]
    __table_args__ = (
        CheckConstraint("owner IN ('company', 'customer')", name="owner"),
        CheckConstraint("status IN ('open', 'resolved', 'overdue')", name="status"),
        CheckConstraint("kind IN ('promise', 'dispute', 'request')", name="kind"),
        CheckConstraint("audience IN ('shared', 'internal')", name="audience"),
        # The items about an object, across the handles of its participants.
        Index(
            "ix_open_items_object",
            "space_id",
            "object_type",
            "object_namespace",
            "object_external_id",
            postgresql_where=text("object_type IS NOT NULL"),
        ),
        Index("ix_open_items_due", "due_at", postgresql_where=text("status = 'open'")),
        Index(None, "space_id", "change_seq"),
        HASHED_BY_HANDLE,
    )


class OpenItemFeedbackEventRow(SpaceBase):
    """A `feedback.resolve_open_item` event that names an item without being the one that closed it (the
    item was already closed, or is another customer's): the event's sealed fields name the item, so this
    row lets an erasure of the item find the event and strip its reason (8.4). Ids only; not partitioned
    by handle, since the event's handle need not be the item's."""

    __tablename__ = "open_item_feedback_events"
    space_id: Mapped[UUID] = mapped_column(primary_key=True)
    item_id: Mapped[UUID] = mapped_column(primary_key=True)
    event_id: Mapped[UUID] = mapped_column(primary_key=True)


class ProfileAttrRow(SpaceBase):
    __tablename__ = "profile_attrs"
    space_id: Mapped[UUID] = mapped_column(primary_key=True)
    origin_handle_id: Mapped[UUID] = mapped_column(primary_key=True)
    slot: Mapped[str] = mapped_column(primary_key=True)
    source_id: Mapped[UUID] = mapped_column(primary_key=True)
    value_ct: Mapped[bytes]
    observed_at: Mapped[datetime]
    times_seen: Mapped[int] = mapped_column(Integer, server_default=text("1"))
    dek_version: Mapped[int] = dek_version()
    change_seq: Mapped[int] = change_seq()
    __table_args__ = (HASHED_BY_HANDLE,)


class TailSummary(SpaceBase):
    __tablename__ = "tail_summaries"
    space_id: Mapped[UUID] = mapped_column(primary_key=True)
    profile_id: Mapped[UUID] = mapped_column(primary_key=True)
    level: Mapped[str] = mapped_column(primary_key=True)
    membership_version: Mapped[int] = mapped_column(BigInteger)
    version: Mapped[int] = mapped_column(Integer)
    text_ct: Mapped[bytes]
    covers_until: Mapped[datetime]
    dek_version: Mapped[int] = dek_version()
    __table_args__ = (CheckConstraint("level IN ('short', 'long')", name="level"),)


class SettledValueRow(SpaceBase):
    """What a session settled without a predicate (11, 3.2): the raw value sealed, its key as an
    HMAC for the exact lookup, and the type, time, speaker and channel that answer "the protocol you sent
    me by e-mail" by rule."""

    __tablename__ = "settled_values"
    space_id: Mapped[UUID] = mapped_column(primary_key=True)
    origin_handle_id: Mapped[UUID] = mapped_column(primary_key=True)
    value_id: Mapped[UUID] = mapped_column(primary_key=True)
    type: Mapped[str]
    raw_ct: Mapped[bytes]
    value_hmac: Mapped[bytes]
    said_at: Mapped[datetime]
    speaker: Mapped[str]
    session_id: Mapped[UUID]
    conversation_id: Mapped[str | None]
    channel: Mapped[str]
    source_id: Mapped[UUID]
    episode_id: Mapped[UUID | None]
    object_ref: Mapped[dict[str, Any] | None]
    evidence_event_ids: Mapped[list[UUID]] = mapped_column(server_default=text("'{}'"))
    origin: Mapped[str]
    created_at: Mapped[datetime]
    dek_version: Mapped[int] = dek_version()
    purposes: Mapped[dict[str, Any] | None]
    __table_args__ = (
        Index(None, "space_id", "origin_handle_id", "type", "said_at"),
        Index(None, "space_id", "origin_handle_id", "value_hmac"),
        Index(None, "evidence_event_ids", postgresql_using="gin"),
        CheckConstraint(
            "type IN ('protocol', 'ticket', 'order', 'record', 'receipt', 'postal_code', 'amount', 'date', "
            "'code')",
            name="type",
        ),
        CheckConstraint("origin IN ('extraction', 'rule')", name="origin"),
        HASHED_BY_HANDLE,
    )


class BusinessObjectRow(VectorColumns, SpaceBase):
    __tablename__ = "business_objects"
    space_id: Mapped[UUID] = mapped_column(primary_key=True)
    object_id: Mapped[UUID] = mapped_column(primary_key=True)
    type: Mapped[str]
    namespace: Mapped[str]
    external_id: Mapped[str]
    origin_handle_id: Mapped[UUID | None]
    state_version: Mapped[str | None]
    as_of: Mapped[datetime]
    source_id: Mapped[UUID]
    record_ref: Mapped[str | None]
    change_seq: Mapped[int] = change_seq()
    # Typed state (24.3.1): the state sealed and merged field by field, with each field's stamps in
    # `field_meta`.
    state_ct: Mapped[bytes | None]
    dek_version: Mapped[int | None] = mapped_column(Integer)
    field_meta: Mapped[dict[str, Any] | None]
    inputs_hash: Mapped[bytes | None]
    derived_status: Mapped[str | None]
    match_keys: Mapped[list[str] | None] = mapped_column(ARRAY(Text))
    """The space's keyed digests of the fields a reverse relation finds the object by."""
    __table_args__ = (
        UniqueConstraint("space_id", "namespace", "type", "external_id"),
        Index(None, "space_id", "change_seq"),
        Index(None, "match_keys", postgresql_using="gin"),
        # A customer's objects by their owner: the customer page, the compiler and the relationship graph.
        Index(
            "ix_business_objects_origin_handle",
            "space_id",
            "origin_handle_id",
            postgresql_where=text("origin_handle_id IS NOT NULL"),
        ),
    )


class ObjectEvent(SpaceBase):
    __tablename__ = "object_events"
    space_id: Mapped[UUID] = mapped_column(primary_key=True)
    object_id: Mapped[UUID] = mapped_column(primary_key=True)
    event_id: Mapped[UUID] = mapped_column(primary_key=True)
    occurred_at: Mapped[datetime]
    change_seq: Mapped[int] = change_seq()
    __table_args__ = (Index(None, "space_id", "event_id"), Index(None, "space_id", "change_seq"))


class ObjectParticipant(SpaceBase):
    __tablename__ = "object_participants"
    space_id: Mapped[UUID] = mapped_column(primary_key=True)
    object_id: Mapped[UUID] = mapped_column(primary_key=True)
    handle_id: Mapped[UUID] = mapped_column(primary_key=True)
    role: Mapped[str] = mapped_column(primary_key=True)
    __table_args__ = (Index(None, "space_id", "handle_id"),)


class AgentActionRow(VectorColumns, SpaceBase):
    __tablename__ = "agent_actions"
    space_id: Mapped[UUID] = mapped_column(primary_key=True)
    action_id: Mapped[UUID] = mapped_column(primary_key=True)
    event_id: Mapped[UUID]
    source_id: Mapped[UUID]
    purpose: Mapped[str | None]
    object_id: Mapped[UUID | None]
    operation: Mapped[str]
    result_ct: Mapped[bytes | None]
    closes_handle_id: Mapped[UUID | None]
    closes_item_id: Mapped[UUID | None]
    status: Mapped[str]
    confirmed_by_event_id: Mapped[UUID | None]
    corrects_action_id: Mapped[UUID | None]
    occurred_at: Mapped[datetime]
    dek_version: Mapped[int] = dek_version()
    change_seq: Mapped[int] = change_seq()
    __table_args__ = (
        CheckConstraint("status IN ('declared', 'confirmed', 'divergent', 'quarantined')", name="status"),
        Index(None, "space_id", "object_id", "operation", "occurred_at"),
        Index(None, "space_id", "event_id"),
        Index(
            None,
            "space_id",
            "confirmed_by_event_id",
            postgresql_where=text("confirmed_by_event_id IS NOT NULL"),
        ),
        Index(
            "ix_agent_actions_declared",
            "space_id",
            "occurred_at",
            postgresql_where=text("status = 'declared'"),
        ),
        # Actions on no object that close a customer's promise, by the customer's handle.
        Index(
            "ix_agent_actions_closes_handle",
            "space_id",
            "closes_handle_id",
            postgresql_where=text("closes_handle_id IS NOT NULL"),
        ),
        Index(None, "space_id", "change_seq"),
    )


class TraitRow(VectorColumns, SpaceBase):
    __tablename__ = "traits"
    space_id: Mapped[UUID] = mapped_column(primary_key=True)
    trait_id: Mapped[UUID] = mapped_column(primary_key=True)
    profile_id: Mapped[UUID]
    membership_version: Mapped[int] = mapped_column(BigInteger)
    name: Mapped[str]
    value_ct: Mapped[bytes]
    layer: Mapped[str]
    rule_version: Mapped[str]
    first_seen: Mapped[datetime]
    last_evidence_at: Mapped[datetime]
    expires_at: Mapped[datetime]
    confidence: Mapped[float]
    retracted_at: Mapped[datetime | None]
    dek_version: Mapped[int] = dek_version()
    change_seq: Mapped[int] = change_seq()
    __table_args__ = (
        UniqueConstraint("space_id", "profile_id", "name"),
        CheckConstraint("layer IN ('stable', 'volatile')", name="layer"),
        Index("ix_traits_expiring", "expires_at", postgresql_where=text("retracted_at IS NULL")),
        Index(None, "space_id", "change_seq"),
    )


class TraitOptOut(SpaceBase):
    """Kept per handle so an opt-out survives merges and unmerges (20.5)."""

    __tablename__ = "trait_opt_outs"
    space_id: Mapped[UUID] = mapped_column(primary_key=True)
    handle_id: Mapped[UUID] = mapped_column(primary_key=True)
    opted_out_at: Mapped[datetime]


class PurposeObjection(SpaceBase):
    """A data subject's objection to one purpose (13, 6.6), kept per handle like a pattern opt-out so it
    survives merges and unmerges; it goes with the rest of the customer on erasure."""

    __tablename__ = "purpose_objections"
    space_id: Mapped[UUID] = mapped_column(primary_key=True)
    handle_id: Mapped[UUID] = mapped_column(primary_key=True)
    purpose: Mapped[str] = mapped_column(primary_key=True)
    objected_at: Mapped[datetime]


class TraitEvidence(SpaceBase):
    __tablename__ = "trait_evidence"
    space_id: Mapped[UUID] = mapped_column(primary_key=True)
    trait_id: Mapped[UUID] = mapped_column(primary_key=True)
    origin_handle_id: Mapped[UUID] = mapped_column(primary_key=True)
    evidence_kind: Mapped[str] = mapped_column(primary_key=True)
    evidence_id: Mapped[UUID] = mapped_column(primary_key=True)
    change_seq: Mapped[int] = change_seq()
    __table_args__ = (
        CheckConstraint("evidence_kind IN ('episode', 'event', 'action', 'open_item')", name="evidence_kind"),
        Index(None, "space_id", "evidence_id"),
        Index(None, "space_id", "change_seq"),
    )
