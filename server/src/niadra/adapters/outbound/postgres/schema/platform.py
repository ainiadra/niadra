"""Platform tables: the cell's own state (cell database) and the pipeline and edge tables of a space.

Cell database: configuration copy, cell counters and health, secrets, wrapped data keys, source keys,
dead tasks, the space directory and the deletion receipts on their way to the control plane.
Space database: the stamp, space counters, request idempotency, outbox, scheduled tasks, coalescing
counters, leases and schedules.
"""

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import BigInteger, Boolean, CheckConstraint, Index, Integer, text
from sqlalchemy.orm import Mapped, mapped_column

from niadra.adapters.outbound.postgres.schema.base import CellBase, SpaceBase, now_default
from niadra.domain.pipeline.delay import reason_check


class ConfigSnapshot(CellBase):
    __tablename__ = "config_snapshot"
    cell_id: Mapped[str] = mapped_column(primary_key=True)
    slot: Mapped[str] = mapped_column(primary_key=True)
    version: Mapped[int] = mapped_column(BigInteger)
    manifest: Mapped[dict[str, Any]]
    signature: Mapped[bytes]
    applied_at: Mapped[datetime] = now_default()
    __table_args__ = (CheckConstraint("slot IN ('active', 'previous')", name="slot"),)


class ConfigDocument(CellBase):
    __tablename__ = "config_documents"
    space_id: Mapped[UUID] = mapped_column(primary_key=True)
    doc_type: Mapped[str] = mapped_column(primary_key=True)
    content_hash: Mapped[str]
    version: Mapped[int] = mapped_column(BigInteger)
    document: Mapped[dict[str, Any]]


class CellState(CellBase):
    """Cell-wide counters: `cache_epoch` enters every derived-state key in the cache (06, 3)."""

    __tablename__ = "cell_state"
    key: Mapped[str] = mapped_column(primary_key=True)
    value: Mapped[int] = mapped_column(BigInteger)
    updated_at: Mapped[datetime] = now_default()


class CellHealth(CellBase):
    __tablename__ = "cell_health"
    component: Mapped[str] = mapped_column(primary_key=True)
    state: Mapped[str]
    since: Mapped[datetime] = now_default()
    detail: Mapped[str | None]
    __table_args__ = (CheckConstraint("state IN ('healthy', 'degraded')", name="state"),)


class Secret(CellBase):
    """Tenant secrets (webhook signing keys, export credentials), one row per version.

    In the cell database: the reference already names the space (`spaces/<space>/<kind>/<id>`), and
    the secret store sits below the unit of work, like the key vault.
    """

    __tablename__ = "secrets"
    ref: Mapped[str] = mapped_column(primary_key=True)
    version: Mapped[int] = mapped_column(Integer, primary_key=True)
    value_ct: Mapped[bytes]
    kek_id: Mapped[str]
    created_at: Mapped[datetime] = now_default()


class DataKey(CellBase):
    """Wrapped data keys, one or more versions per space (5.14.1). Kept out of the space database on
    purpose: a dump of a space holds only ciphertext, and deleting these rows shreds every copy."""

    __tablename__ = "data_keys"
    space_id: Mapped[UUID] = mapped_column(primary_key=True)
    version: Mapped[int] = mapped_column(Integer, primary_key=True)
    wrapped: Mapped[bytes]
    kek_id: Mapped[str]
    created_at: Mapped[datetime] = now_default()


class SourceKey(CellBase):
    __tablename__ = "source_keys"
    space_id: Mapped[UUID] = mapped_column(primary_key=True)
    key_id: Mapped[str] = mapped_column(primary_key=True)
    source_id: Mapped[UUID]
    fast_hash: Mapped[bytes] = mapped_column(unique=True)
    slow_hash: Mapped[str]
    scopes: Mapped[list[str]]
    status: Mapped[str] = mapped_column(server_default="active")
    version: Mapped[int] = mapped_column(BigInteger)
    __table_args__ = (CheckConstraint("status IN ('active', 'revoked')", name="status"),)


class RequestIdempotency(SpaceBase):
    """Idempotency-Key of governance POSTs: kept 24 h; same key with another body is a 409 (7.0)."""

    __tablename__ = "request_idempotency"
    space_id: Mapped[UUID] = mapped_column(primary_key=True)
    idem_key: Mapped[str] = mapped_column(primary_key=True)
    body_hash: Mapped[bytes]
    status: Mapped[int] = mapped_column(Integer)
    response: Mapped[dict[str, Any]]
    created_at: Mapped[datetime] = now_default()


class Outbox(SpaceBase):
    """One row per task, alive for milliseconds: the writer publishes after commit and deletes it (5.5)."""

    __tablename__ = "outbox"
    id: Mapped[UUID] = mapped_column(primary_key=True)
    space_id: Mapped[UUID]
    task: Mapped[str]
    queue: Mapped[str]
    profile_id: Mapped[UUID | None]
    payload: Mapped[dict[str, Any]]
    created_at: Mapped[datetime] = now_default()
    # Aggressive autovacuum for this table is set by the migration.
    __table_args__ = (Index(None, "created_at"),)


class ScheduledTask(SpaceBase):
    """Delayed work: retries with backoff, busy leases, schedules and the hot-layer safety net."""

    __tablename__ = "scheduled_tasks"
    id: Mapped[UUID] = mapped_column(primary_key=True)
    space_id: Mapped[UUID]
    task: Mapped[str]
    queue: Mapped[str]
    profile_id: Mapped[UUID | None]
    payload: Mapped[dict[str, Any]]
    due_at: Mapped[datetime] = mapped_column(index=True)
    reason: Mapped[str]
    # Exactly the reasons the code writes; a new one needs a space migration too (`delay.py`).
    __table_args__ = (CheckConstraint(reason_check(), name="reason"),)


class ProfileWork(SpaceBase):
    """Coalescing counters: writers bump `requested`, the task records `done` (5.5)."""

    __tablename__ = "profile_work"
    space_id: Mapped[UUID] = mapped_column(primary_key=True)
    profile_id: Mapped[UUID] = mapped_column(primary_key=True)
    task: Mapped[str] = mapped_column(primary_key=True)
    requested: Mapped[int] = mapped_column(BigInteger, server_default=text("0"))
    done: Mapped[int] = mapped_column(BigInteger, server_default=text("0"))


class QueueLease(SpaceBase):
    __tablename__ = "queue_leases"
    space_id: Mapped[UUID] = mapped_column(primary_key=True)
    unit_key: Mapped[str] = mapped_column(primary_key=True)
    owner: Mapped[str]
    epoch: Mapped[int] = mapped_column(BigInteger)
    expires_at: Mapped[datetime]


class DeadTask(CellBase):
    __tablename__ = "dead_tasks"
    space_id: Mapped[UUID] = mapped_column(primary_key=True)
    id: Mapped[UUID] = mapped_column(primary_key=True)
    task: Mapped[str]
    queue: Mapped[str]
    payload: Mapped[dict[str, Any]]
    attempts: Mapped[int] = mapped_column(Integer)
    last_error: Mapped[str | None]
    dead_at: Mapped[datetime] = now_default()


class Schedule(SpaceBase):
    __tablename__ = "schedules"
    space_id: Mapped[UUID] = mapped_column(primary_key=True)
    task: Mapped[str] = mapped_column(primary_key=True)
    # Several schedules of one task in a space, like one export per destination; '' for the rest.
    key: Mapped[str] = mapped_column(primary_key=True, server_default="")
    next_run: Mapped[datetime]
    timezone: Mapped[str] = mapped_column(server_default="UTC")
    every_seconds: Mapped[int] = mapped_column(Integer)
    payload: Mapped[dict[str, Any]] = mapped_column(server_default=text("'{}'"))


class SpaceDatabase(CellBase):
    """The directory: where each space's database lives and what state it is in (one database per space).

    The control plane owns the list of spaces; this table mirrors it for the cell's operations, and
    every space database carries a stamp (`space_identity`) that is checked against it on first use.
    """

    __tablename__ = "space_databases"
    space_id: Mapped[UUID] = mapped_column(primary_key=True)
    tenant_id: Mapped[UUID]
    environment: Mapped[str]
    db_name: Mapped[str] = mapped_column(unique=True)
    # A key of the configured instances; `primary` is the cell's shared PostgreSQL.
    instance: Mapped[str] = mapped_column(server_default="primary")
    tier: Mapped[str] = mapped_column(server_default="t0")
    hash_modulus: Mapped[int] = mapped_column(Integer)
    status: Mapped[str]
    # The Alembic head applied; NULL until the first migration succeeded.
    schema_version: Mapped[str | None]
    status_detail: Mapped[str | None]
    # Consecutive provisioning failures and when the next attempt may run (backoff).
    failures: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    retry_at: Mapped[datetime | None]
    created_at: Mapped[datetime] = now_default()
    ready_at: Mapped[datetime | None]
    deleted_at: Mapped[datetime | None]
    updated_at: Mapped[datetime] = now_default()
    __table_args__ = (
        CheckConstraint("environment IN ('sandbox', 'production')", name="environment"),
        CheckConstraint("tier IN ('t0', 't1', 't2')", name="tier"),
        CheckConstraint(
            "status IN ('creating', 'migrating', 'ready', 'maintenance', 'failed', 'deleting', 'deleted')",
            name="status",
        ),
    )


class DeletionReceiptDraft(CellBase):
    """A deletion receipt until the control plane acknowledges it; retried until then."""

    __tablename__ = "deletion_receipts_outbox"
    space_id: Mapped[UUID] = mapped_column(primary_key=True)
    receipt: Mapped[dict[str, Any]]
    created_at: Mapped[datetime] = now_default()
    sent_at: Mapped[datetime | None]


class SpaceIdentity(SpaceBase):
    """The stamp: one row naming the space the database belongs to, written by the provisioner and
    checked when a process first opens the database, so a mis-wired directory fails loudly."""

    __tablename__ = "space_identity"
    one: Mapped[bool] = mapped_column(Boolean, primary_key=True, server_default=text("true"))
    space_id: Mapped[UUID]
    tenant_id: Mapped[UUID]
    region: Mapped[str]
    environment: Mapped[str]
    cell_id: Mapped[str]
    hash_modulus: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = now_default()
    __table_args__ = (CheckConstraint("one", name="one"),)


class SpaceState(SpaceBase):
    """Space counters: the dedupe generation that selects the current `event_keys_*` table (5.2)."""

    __tablename__ = "space_state"
    key: Mapped[str] = mapped_column(primary_key=True)
    value: Mapped[int] = mapped_column(BigInteger)
    updated_at: Mapped[datetime] = now_default()
