"""Compiled packs, conversation pins, agent watermarks and receipts."""

from datetime import date, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import BigInteger, CheckConstraint, Index, Integer, SmallInteger, text
from sqlalchemy.orm import Mapped, mapped_column

from niadra.adapters.outbound.postgres.schema.base import (
    CellBase,
    SpaceBase,
    change_seq,
    dek_version,
    now_default,
)


class PackBundle(SpaceBase):
    """Every stored variant of a profile's current generation, as one sealed value (5.10)."""

    __tablename__ = "pack_bundles"
    space_id: Mapped[UUID] = mapped_column(primary_key=True)
    target: Mapped[str] = mapped_column(primary_key=True)
    generation: Mapped[int] = mapped_column(BigInteger)
    body_ct: Mapped[bytes]
    dek_version: Mapped[int] = dek_version()
    last_read: Mapped[dict[str, Any]] = mapped_column(server_default=text("'{}'::jsonb"))
    """Milliseconds since the epoch of each variant's last read, by variant key."""


class PackManifest(SpaceBase):
    __tablename__ = "pack_manifests"
    space_id: Mapped[UUID] = mapped_column(primary_key=True)
    manifest_hash: Mapped[str] = mapped_column(primary_key=True)
    manifest: Mapped[dict[str, Any]]
    profile_id: Mapped[UUID | None]
    __table_args__ = (Index(None, "space_id", "profile_id"),)


class ConversationPin(SpaceBase):
    __tablename__ = "conversation_pins"
    space_id: Mapped[UUID] = mapped_column(primary_key=True)
    conversation_id: Mapped[str] = mapped_column(primary_key=True)
    view: Mapped[str] = mapped_column(primary_key=True)
    pack_key: Mapped[str]
    pack_hash: Mapped[str]
    generation: Mapped[int] = mapped_column(BigInteger)
    pinned_at: Mapped[datetime]
    expires_at: Mapped[datetime]


class AgentWatermark(SpaceBase):
    __tablename__ = "agent_watermarks"
    space_id: Mapped[UUID] = mapped_column(primary_key=True)
    source_id: Mapped[UUID] = mapped_column(primary_key=True)
    profile_id: Mapped[UUID] = mapped_column(primary_key=True)
    thread: Mapped[str] = mapped_column(primary_key=True, server_default="")
    manifest_hash: Mapped[str]
    seen_at: Mapped[datetime]
    generation: Mapped[int] = mapped_column(BigInteger, server_default=text("0"))


class ReadInvalidation(SpaceBase):
    """What changed under the read processes' state (`app.read_state`): written by triggers on handles,
    profiles, memberships and sessions, never by the application, and polled with the privacy feed."""

    __tablename__ = "read_invalidations"
    space_id: Mapped[UUID] = mapped_column(primary_key=True)
    id: Mapped[UUID] = mapped_column(primary_key=True, server_default=text("gen_random_uuid()"))
    handle_ids: Mapped[list[UUID]] = mapped_column(server_default=text("'{}'"))
    profile_ids: Mapped[list[UUID]] = mapped_column(server_default=text("'{}'"))
    threads: Mapped[list[str]] = mapped_column(server_default=text("'{}'"))
    created_at: Mapped[datetime] = now_default()
    __table_args__ = (Index(None, "created_at"),)


class ExperimentAssignment(SpaceBase):
    """The group a profile first got in an experiment, projected from its receipts: a later
    union of profiles keeps the survivor's group instead of drawing again by another handle."""

    __tablename__ = "experiment_assignments"
    space_id: Mapped[UUID] = mapped_column(primary_key=True)
    experiment: Mapped[str] = mapped_column(primary_key=True)
    profile_id: Mapped[UUID] = mapped_column(primary_key=True)
    grp: Mapped[str]
    assigned_at: Mapped[datetime]
    # Experiments on memory, an element, an agent or a ranking (24.7); `grp` is the arm drawn.
    kind: Mapped[str]
    __table_args__ = (CheckConstraint("kind IN ('memory', 'element', 'agent', 'interleave')", name="kind"),)


class Receipt(SpaceBase):
    """Append-only, hash-chained per space, slice and day (5.11, 5.13). Partitioned by `receipt_id`."""

    __tablename__ = "receipts"
    space_id: Mapped[UUID] = mapped_column(primary_key=True)
    receipt_id: Mapped[UUID] = mapped_column(primary_key=True)
    slice: Mapped[int] = mapped_column(SmallInteger)
    day: Mapped[date]
    seq: Mapped[int] = mapped_column(BigInteger)
    producer_id: Mapped[str]
    producer_seq: Mapped[int] = mapped_column(BigInteger)
    kind: Mapped[str]
    profile_id: Mapped[UUID | None]
    source_id: Mapped[UUID | None]
    person_id: Mapped[UUID | None]
    conversation_id: Mapped[str | None]
    task_id: Mapped[str | None]
    object_id: Mapped[UUID | None]
    about_handle_id: Mapped[UUID | None]
    purpose: Mapped[str | None]
    audience: Mapped[str]
    surface: Mapped[str]
    item_ids: Mapped[list[str]] = mapped_column(server_default=text("'{}'"))
    pack_hash: Mapped[str | None]
    manifest_hash: Mapped[str | None]
    verification: Mapped[str | None]
    policy_version: Mapped[str | None]
    experiment_group: Mapped[str | None]
    query_hash: Mapped[bytes | None]
    withheld: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    detail: Mapped[dict[str, Any] | None]
    read_at: Mapped[datetime]
    prev_hash: Mapped[bytes]
    hash: Mapped[bytes]
    change_seq: Mapped[int] = change_seq()
    __table_args__ = (
        Index("ix_receipts_chain", "space_id", "slice", "day", "seq"),
        Index(None, "space_id", "conversation_id", "read_at"),
        Index(None, "space_id", "profile_id", "read_at"),
        {"postgresql_partition_by": "RANGE (receipt_id)"},
    )


class ReceiptHead(SpaceBase):
    __tablename__ = "receipt_heads"
    space_id: Mapped[UUID] = mapped_column(primary_key=True)
    slice: Mapped[int] = mapped_column(SmallInteger, primary_key=True)
    day: Mapped[date] = mapped_column(primary_key=True)
    seq: Mapped[int] = mapped_column(BigInteger)
    hash: Mapped[bytes]


class ReceiptWatermark(CellBase):
    """Per read process: the highest contiguous producer sequence stored (5.11)."""

    __tablename__ = "receipt_watermarks"
    producer_id: Mapped[str] = mapped_column(primary_key=True)
    contiguous_seq: Mapped[int] = mapped_column(BigInteger)
    watermark_at: Mapped[datetime]
    state: Mapped[str]
    last_heard_at: Mapped[datetime]
    pending_seqs: Mapped[list[int]] = mapped_column(server_default=text("'{}'"))
    __table_args__ = (CheckConstraint("state IN ('active', 'closed', 'expired')", name="state"),)


class ReceiptPerson(SpaceBase):
    """The e-mail a person of the Console last acted under, kept beside the receipt chain, which holds only
    their id: the receipts of what they did read by name, whoever reads them."""

    __tablename__ = "receipt_people"
    space_id: Mapped[UUID] = mapped_column(primary_key=True)
    person_id: Mapped[UUID] = mapped_column(primary_key=True)
    label: Mapped[str]
    named_at: Mapped[datetime]


class AuditRoot(SpaceBase):
    __tablename__ = "audit_roots"
    space_id: Mapped[UUID] = mapped_column(primary_key=True)
    day: Mapped[date] = mapped_column(primary_key=True)
    root: Mapped[bytes]
    location: Mapped[str]
    anchored_at: Mapped[datetime]
