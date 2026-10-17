"""Typed state (24.3.1, 6.7): shared objects, computed values, dependencies, relations, timers, watches,
refresh requests, content scans and the agents' working state. The subject's objects stay in
`business_objects` (`schema/memory.py`), which gains the typed columns in the same revision.

Freshness is never stored: `field_meta` keeps stamps (`valid_at`, `source_observed_at`, `known_at`, the
source, its version and the observer), and a read computes the status against the rule in force (L19). No
entry of `field_meta` holds a value; a previous value kept for `track_changes` is sealed with the state.
"""

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import BigInteger, CheckConstraint, Index, Integer, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from niadra.adapters.outbound.postgres.schema.base import SpaceBase, change_seq, dek_version


class SharedObject(SpaceBase):
    """An object many subjects meet (an item variant, a network hospital, a court), kept while in the working
    set. It has no event log: pushes and observations cache a system of record that is not Niadra.

    `state_ct` keeps, per field and source, the latest observation sealed on its own (`{field: {source:
    [dek_version, base64 ciphertext]}}`, AAD `shared_objects:<object_id>:<field>/<source>`), and `field_meta`
    its stamps and the source's version, so the hot copy and the consolidator merge field by field without
    opening any value."""

    __tablename__ = "shared_objects"
    space_id: Mapped[UUID] = mapped_column(primary_key=True)
    object_id: Mapped[UUID] = mapped_column(primary_key=True)
    type: Mapped[str]
    namespace: Mapped[str]
    external_id: Mapped[str]
    variant: Mapped[str] = mapped_column(server_default="")
    state_ct: Mapped[dict[str, Any]] = mapped_column(server_default=text("'{}'"))
    field_meta: Mapped[dict[str, Any]] = mapped_column(server_default=text("'{}'"))
    version: Mapped[int] = mapped_column(BigInteger, server_default=text("0"))
    inputs_hash: Mapped[bytes | None]
    derived_status: Mapped[str | None]
    last_referenced_at: Mapped[datetime]
    hot_until: Mapped[datetime | None]
    watch_count: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    change_seq: Mapped[int] = change_seq()
    __table_args__ = (
        UniqueConstraint("space_id", "type", "namespace", "external_id", "variant"),
        CheckConstraint("derived_status IN ('current', 'expired_by_input')", name="derived_status"),
        Index(None, "space_id", "last_referenced_at"),
        Index(None, "space_id", "change_seq"),
    )


class ObjectValue(SpaceBase):
    """A value the company computed (a deadline, a price, a waiting period), one row per version (L26): a new
    rule or a changed input brings a new version that names the one it supersedes."""

    __tablename__ = "object_values"
    space_id: Mapped[UUID] = mapped_column(primary_key=True)
    object_id: Mapped[UUID] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(primary_key=True)
    version: Mapped[int] = mapped_column(Integer, primary_key=True)
    value_ct: Mapped[bytes | None]
    dek_version: Mapped[int | None] = mapped_column(Integer)
    absent_as: Mapped[str | None]
    rule: Mapped[str]
    rule_version: Mapped[str]
    inputs_hash: Mapped[bytes]
    input_refs: Mapped[dict[str, Any]] = mapped_column(server_default=text("'{}'"))
    computed_at: Mapped[datetime]
    valid_until: Mapped[datetime | None]
    declared_gaps: Mapped[list[str]] = mapped_column(server_default=text("'{}'"))
    gap_effect: Mapped[str] = mapped_column(server_default="unknown")
    supersedes_version: Mapped[int | None] = mapped_column(Integer)
    event_id: Mapped[UUID | None]
    recorded_at: Mapped[datetime]
    change_seq: Mapped[int] = change_seq()
    __table_args__ = (
        CheckConstraint("gap_effect IN ('errs_early', 'errs_late', 'unknown')", name="gap_effect"),
        CheckConstraint("(value_ct IS NULL) = (dek_version IS NULL)", name="sealed"),
        Index(None, "space_id", "recorded_at"),
        Index(None, "space_id", "change_seq"),
    )


class ObjectDependency(SpaceBase):
    """A derived object and one input it is a function of: when the input changes, every dependent expires in
    one statement, by this key."""

    __tablename__ = "object_dependencies"
    space_id: Mapped[UUID] = mapped_column(primary_key=True)
    input_object_id: Mapped[UUID] = mapped_column(primary_key=True)
    input_field: Mapped[str] = mapped_column(primary_key=True)
    dependent_object_id: Mapped[UUID] = mapped_column(primary_key=True)
    created_at: Mapped[datetime]
    __table_args__ = (Index(None, "space_id", "dependent_object_id"),)


class ObjectTimer(SpaceBase):
    """A timer of an object. `due_at` only indexes the five-minute sweep: a read recomputes when it is due
    from the rule. A timer that fires again after its value was revised names the firing it supersedes."""

    __tablename__ = "object_timers"
    space_id: Mapped[UUID] = mapped_column(primary_key=True)
    timer_id: Mapped[UUID] = mapped_column(primary_key=True)
    object_id: Mapped[UUID]
    name: Mapped[str]
    once_per_key: Mapped[str | None]
    due_at: Mapped[datetime | None]
    state: Mapped[str]
    value_version: Mapped[int | None] = mapped_column(Integer)
    fired_at: Mapped[datetime | None]
    supersedes_firing_id: Mapped[UUID | None]
    created_at: Mapped[datetime]
    __table_args__ = (
        CheckConstraint("state IN ('armed', 'fired', 'cancelled')", name="state"),
        Index("ix_object_timers_armed", "space_id", "due_at", postgresql_where=text("state = 'armed'")),
        Index(None, "space_id", "object_id", "name"),
    )


class ObjectWatch(SpaceBase):
    """A subject's explicit request to be told when a shared object meets a condition (a niadra-expr),
    revalidated by the company's resolver worker before it fires."""

    __tablename__ = "object_watches"
    space_id: Mapped[UUID] = mapped_column(primary_key=True)
    watch_id: Mapped[UUID] = mapped_column(primary_key=True)
    object_id: Mapped[UUID]
    origin_handle_id: Mapped[UUID]
    condition: Mapped[str]
    consent_event_key: Mapped[str]
    expires_at: Mapped[datetime]
    last_fired_at: Mapped[datetime | None]
    created_at: Mapped[datetime]
    __table_args__ = (
        Index(None, "space_id", "object_id"),
        Index(None, "space_id", "origin_handle_id"),
        Index(None, "space_id", "expires_at"),
    )


class RefreshRequest(SpaceBase):
    """Work for the company's resolver worker: an object to fetch again and the named reason, written only
    when the budget admits it, leased for 60 seconds at a time."""

    __tablename__ = "refresh_requests"
    space_id: Mapped[UUID] = mapped_column(primary_key=True)
    request_id: Mapped[UUID] = mapped_column(primary_key=True)
    object_id: Mapped[UUID]
    type: Mapped[str]
    namespace: Mapped[str]
    external_id: Mapped[str]
    variant: Mapped[str] = mapped_column(server_default="")
    reason: Mapped[str]
    priority: Mapped[str]
    budget_units: Mapped[int] = mapped_column(Integer)
    requested_at: Mapped[datetime]
    lease_until: Mapped[datetime | None]
    lease_owner: Mapped[str | None]
    attempts: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    reservation: Mapped[dict[str, Any] | None]
    """What the budget reserved for the fetch (the coordination's admission), settled once the object comes
    back fresh or the request is given up."""
    __table_args__ = (
        UniqueConstraint("space_id", "object_id", "reason"),
        CheckConstraint("priority IN ('high', 'normal', 'low')", name="priority"),
        Index(None, "space_id", "requested_at"),
    )


class ContentScan(SpaceBase):
    """The scan state of a third party's content (a message, a document, an attachment, a text field of a
    system) (L27): `pending` blocks a model's read as `flagged` does when the type asks for a scan, and only a
    person releases what was flagged, with a reason and a receipt."""

    __tablename__ = "content_scans"
    space_id: Mapped[UUID] = mapped_column(primary_key=True)
    content_ref: Mapped[str] = mapped_column(primary_key=True)
    origin_handle_id: Mapped[UUID | None]
    state: Mapped[str]
    reason: Mapped[str | None]
    decided_by: Mapped[str | None]
    decided_at: Mapped[datetime | None]
    created_at: Mapped[datetime]
    __table_args__ = (
        CheckConstraint("state IN ('pending', 'clean', 'flagged')", name="state"),
        Index(None, "space_id", "origin_handle_id"),
    )


class AgentStateRow(SpaceBase):
    """An agent's working state, by scope: written by code and never by a model, read from the cache, and
    written to this table in batches. The scope key is an HMAC of the scope, since a conversation id may look
    like a phone number; the Console shows size, version and dates, never the body."""

    __tablename__ = "agent_state"
    space_id: Mapped[UUID] = mapped_column(primary_key=True)
    scope_key: Mapped[bytes] = mapped_column(primary_key=True)
    source_id: Mapped[UUID] = mapped_column(primary_key=True)
    agent: Mapped[str] = mapped_column(primary_key=True)
    scope_kind: Mapped[str]
    body_ct: Mapped[bytes]
    dek_version: Mapped[int] = dek_version()
    body_bytes: Mapped[int] = mapped_column(Integer)
    schema_version: Mapped[str | None]
    version: Mapped[int] = mapped_column(BigInteger)
    updated_at: Mapped[datetime]
    valid_until: Mapped[datetime | None]
    """Null for a type that names no retention: the state is kept until it names one."""
    origin_handle_id: Mapped[UUID | None]
    subject_key: Mapped[bytes | None]
    """The customer the state is about, as the space's keyed digest of their handle (the one `handles` keeps):
    the writer resolves `origin_handle_id` from it, and erasure and the customer's package find the state."""
    __table_args__ = (
        CheckConstraint("scope_kind IN ('conversation', 'task', 'object', 'subject')", name="scope_kind"),
        Index(None, "space_id", "valid_until"),
        Index(None, "space_id", "origin_handle_id"),
        Index(None, "space_id", "subject_key"),
    )
