"""Ownership and effects (24.3.4, 6.7): the durable record of the coordination the state store decides.

The check never reads these tables: the state store answers it, the decision goes to the confirmed queue,
and the batch writer stores it here with one statement per batch. A key the state store lost is rebuilt
from them on its first miss. Everything about a person hangs off the origin handle (L6), so erasure finds
it; suppressions outlive an erasure, as the proof that the person asked not to be contacted, and keep the
handle's keyed hash and sealed canonical form for that.
"""

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import BigInteger, CheckConstraint, Index, Integer, text
from sqlalchemy.orm import Mapped, mapped_column

from niadra.adapters.outbound.postgres.schema.base import SpaceBase, change_seq, dek_version, now_default


class OwnershipClaimRow(SpaceBase):
    """Who holds a subject or an object, by declaration or by observation, with the source's validity;
    `epoch` fences a release (24.3.4). A declared subject is named by its handle's keyed hash, as the state
    store keys it, so no identity is resolved on the way."""

    __tablename__ = "ownership_claims"
    space_id: Mapped[UUID] = mapped_column(primary_key=True)
    claim_id: Mapped[UUID] = mapped_column(primary_key=True)
    target: Mapped[str]
    origin_handle_id: Mapped[UUID | None]
    subject_hmac: Mapped[bytes | None]
    object_type: Mapped[str | None]
    object_namespace: Mapped[str | None]
    object_external_id: Mapped[str | None]
    kind: Mapped[str]
    task: Mapped[str | None]
    holder_kind: Mapped[str]
    holder: Mapped[str]
    level: Mapped[str]
    via: Mapped[str]
    source_id: Mapped[UUID]
    observer: Mapped[str | None]
    basis_event_id: Mapped[UUID | None]
    claimed_at: Mapped[datetime]
    valid_until: Mapped[datetime]
    intents: Mapped[list[str]] = mapped_column(server_default=text("'{}'"))
    epoch: Mapped[int] = mapped_column(BigInteger)
    released_at: Mapped[datetime | None]
    __table_args__ = (
        CheckConstraint("target IN ('subject', 'object')", name="target"),
        CheckConstraint("kind IN ('owner', 'case', 'task_lock')", name="kind"),
        CheckConstraint("holder_kind IN ('agent', 'human', 'queue', 'system')", name="holder_kind"),
        CheckConstraint("via IN ('declaration', 'mapping', 'worker')", name="via"),
        CheckConstraint(
            "(target = 'subject' AND (origin_handle_id IS NOT NULL OR subject_hmac IS NOT NULL))"
            " OR (target = 'object' AND object_type IS NOT NULL AND object_external_id IS NOT NULL)",
            name="target_named",
        ),
        Index(None, "space_id", "origin_handle_id"),
        Index(None, "space_id", "subject_hmac"),
        Index(
            "ix_ownership_claims_object", "space_id", "object_type", "object_namespace", "object_external_id"
        ),
        Index(None, "space_id", "valid_until"),
    )


class EffectRow(SpaceBase):
    """An external effect of one business fact, exactly once: the key is kept only as its keyed hash
    (24.3.4). `key_valid_until` null holds the key as long as the fact exists."""

    __tablename__ = "effects"
    space_id: Mapped[UUID] = mapped_column(primary_key=True)
    effect_key_hash: Mapped[bytes] = mapped_column(primary_key=True)
    kind: Mapped[str]
    state: Mapped[str]
    fact_type: Mapped[str | None]
    fact_namespace: Mapped[str | None]
    fact_external_id: Mapped[str | None]
    origin_handle_id: Mapped[UUID | None]
    subject_hmac: Mapped[bytes | None]
    source_id: Mapped[UUID]
    agent: Mapped[str | None]
    attempt: Mapped[int] = mapped_column(Integer)
    cost_units: Mapped[int] = mapped_column(BigInteger, server_default=text("0"))
    reserved_at: Mapped[datetime]
    lease_until: Mapped[datetime | None]
    done_at: Mapped[datetime | None]
    settled_at: Mapped[datetime | None]
    settled_by: Mapped[str | None]
    key_valid_until: Mapped[datetime | None]
    __table_args__ = (
        CheckConstraint(
            "kind IN ('delivery', 'document', 'notice', 'filing', 'paid_call', 'external_action')",
            name="kind",
        ),
        CheckConstraint("state IN ('reserved', 'done', 'failed', 'unknown_outcome')", name="state"),
        CheckConstraint(
            "settled_by IS NULL OR settled_by IN ('reserver', 'observation', 'person')", name="settled_by"
        ),
        Index(None, "space_id", "origin_handle_id"),
        Index(None, "space_id", "subject_hmac"),
        # What waits on a person or on the system: the ambiguous attempts (`effect.unknown_outcome`).
        Index(
            "ix_effects_unknown",
            "space_id",
            "reserved_at",
            postgresql_where=text("state = 'unknown_outcome'"),
        ),
    )


class ContactLogRow(SpaceBase):
    """Every contact the coordination saw, weekly partitioned by `contact_id`, a UUIDv7: the decision that
    reserved it, which its contact token carries as `jti`, or minted when a `contact.made` came without one.
    Two `contact.made` citing the same token make `uses` 2, which is how a token used twice shows; `use_ids`
    are the declarations counted, so a batch written again counts none twice (24.3.4). The subject is its
    handle's keyed hash, as the state store keys it."""

    __tablename__ = "contact_log"
    space_id: Mapped[UUID] = mapped_column(primary_key=True)
    contact_id: Mapped[UUID] = mapped_column(primary_key=True)
    origin_handle_id: Mapped[UUID | None]
    subject_hmac: Mapped[bytes | None]
    purpose: Mapped[str]
    channel: Mapped[str]
    direction: Mapped[str]
    source_id: Mapped[UUID]
    agent: Mapped[str | None]
    gateway: Mapped[str | None]
    decision_id: Mapped[UUID | None]
    state: Mapped[str]
    unchecked: Mapped[bool] = mapped_column(server_default=text("false"))
    uses: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    use_ids: Mapped[list[UUID]] = mapped_column(server_default=text("'{}'"))
    reserved_at: Mapped[datetime | None]
    used_at: Mapped[datetime | None]
    __table_args__ = (
        CheckConstraint("direction IN ('inbound', 'outbound')", name="direction"),
        CheckConstraint("state IN ('reserved', 'made', 'expired')", name="state"),
        Index(None, "space_id", "origin_handle_id"),
        Index(None, "space_id", "subject_hmac"),
        {"postgresql_partition_by": "RANGE (contact_id)"},
    )


class BudgetSpendRow(SpaceBase):
    """Units a contact or paid budget spent on a subject, an object or the whole space, kept until the window
    they count in ends: what reloads the budget after the state store came back empty (24.3.4). `budget` is
    `c:<purpose>` or `p:<operation>`, `slice` the funnel stage's window; a subject is its handle's keyed
    hash, an object `<type>\\t<namespace>\\t<id>`."""

    __tablename__ = "budget_spends"
    space_id: Mapped[UUID] = mapped_column(primary_key=True)
    spend_id: Mapped[UUID] = mapped_column(primary_key=True)
    budget: Mapped[str]
    slice: Mapped[str | None]
    target_kind: Mapped[str]
    target: Mapped[bytes]
    units: Mapped[int] = mapped_column(Integer)
    settled: Mapped[bool] = mapped_column(server_default=text("false"))
    spent_at: Mapped[datetime]
    until: Mapped[datetime]
    __table_args__ = (
        CheckConstraint("target_kind IN ('subject', 'object', 'space')", name="target_kind"),
        Index(None, "space_id", "target_kind", "target", "until"),
        Index(None, "space_id", "until"),
    )


class SuppressionRow(SpaceBase):
    """A subject not to be contacted for a purpose, on a channel or all of them (null). The feed of changes
    is ordered by `change_seq` and keyed per source (the suppression list spec); `resume_after_s` records
    the reason's interval that set `until`."""

    __tablename__ = "suppressions"
    space_id: Mapped[UUID] = mapped_column(primary_key=True)
    suppression_id: Mapped[UUID] = mapped_column(primary_key=True)
    origin_handle_id: Mapped[UUID | None]
    handle_type: Mapped[str]
    handle_hmac: Mapped[bytes]
    handle_ct: Mapped[bytes]
    dek_version: Mapped[int] = dek_version()
    purpose: Mapped[str]
    channel: Mapped[str | None]
    reason: Mapped[str]
    source_id: Mapped[UUID | None]
    since: Mapped[datetime]
    until: Mapped[datetime | None]
    resume_after_s: Mapped[int | None] = mapped_column(Integer)
    lifted_at: Mapped[datetime | None]
    change_seq: Mapped[int] = change_seq()
    contact_window: Mapped[str | None]
    """The local hours it holds in, `<from>;<to>;<days>;<zone>` (`Window.encode`); null for every hour."""
    __table_args__ = (
        Index(None, "space_id", "handle_type", "handle_hmac", "purpose"),
        Index(None, "space_id", "origin_handle_id"),
        Index(None, "space_id", "change_seq"),
    )


class CommitmentRow(SpaceBase):
    """An offer, a discount or a settlement proposal a vendor's agent made, with sealed terms; it also
    becomes an action in `agent_actions`, so another agent sees it as done by someone else (24.3.4)."""

    __tablename__ = "commitments"
    space_id: Mapped[UUID] = mapped_column(primary_key=True)
    commitment_id: Mapped[UUID] = mapped_column(primary_key=True)
    external_id: Mapped[str]
    origin_handle_id: Mapped[UUID | None]
    profile_id: Mapped[UUID | None]
    subject_hmac: Mapped[bytes | None]
    object_type: Mapped[str | None]
    object_namespace: Mapped[str | None]
    object_external_id: Mapped[str | None]
    type: Mapped[str]
    compatibility: Mapped[str]
    terms_ct: Mapped[bytes]
    dek_version: Mapped[int] = dek_version()
    status: Mapped[str]
    score: Mapped[float | None]
    source_id: Mapped[UUID]
    agent: Mapped[str | None]
    action_id: Mapped[UUID | None]
    made_at: Mapped[datetime]
    valid_until: Mapped[datetime | None]
    decided_at: Mapped[datetime | None]
    __table_args__ = (
        CheckConstraint("compatibility IN ('first_holds', 'best_wins', 'exclusive')", name="compatibility"),
        CheckConstraint(
            "status IN ('active', 'superseded', 'accepted', 'declined', 'withdrawn', 'expired')",
            name="status",
        ),
        Index(None, "space_id", "origin_handle_id", "status"),
        Index(None, "space_id", "subject_hmac", "status"),
    )


class HandoffRow(SpaceBase):
    """A handoff with its sealed package, the receiving side's policy and level, and the outcome that comes
    back; kept 180 days (24.3.4)."""

    __tablename__ = "handoffs"
    space_id: Mapped[UUID] = mapped_column(primary_key=True)
    handoff_id: Mapped[UUID] = mapped_column(primary_key=True)
    origin_handle_id: Mapped[UUID | None]
    profile_id: Mapped[UUID | None]
    subject_hmac: Mapped[bytes | None]
    subject_ct: Mapped[bytes | None]
    conversation_id: Mapped[str | None]
    source_id: Mapped[UUID]
    agent: Mapped[str | None]
    target: Mapped[str]
    level: Mapped[str | None]
    policy_version: Mapped[str | None]
    package_ct: Mapped[bytes]
    package_sha256: Mapped[bytes]
    dek_version: Mapped[int] = dek_version()
    status: Mapped[str]
    outcome: Mapped[str | None]
    outcome_ct: Mapped[bytes | None]
    outcome_dek_version: Mapped[int | None] = mapped_column(Integer)
    """The key version the outcome is sealed under: the reported outcome may come after a rotation."""
    created_at: Mapped[datetime]
    expected_by: Mapped[datetime | None]
    accepted_at: Mapped[datetime | None]
    closed_at: Mapped[datetime | None]
    __table_args__ = (
        CheckConstraint("status IN ('created', 'accepted', 'closed', 'expired')", name="status"),
        Index(None, "space_id", "origin_handle_id"),
        Index(None, "space_id", "subject_hmac"),
        Index(None, "space_id", "status", "created_at"),
    )


class CoordReportRow(SpaceBase):
    """A shadow run's report: conflicts per 1,000 customers a month, without anyone in them, and the sealed
    sample a person reviews (24.3.4)."""

    __tablename__ = "coord_reports"
    space_id: Mapped[UUID] = mapped_column(primary_key=True)
    report_id: Mapped[UUID] = mapped_column(primary_key=True)
    status: Mapped[str]
    days: Mapped[int] = mapped_column(Integer)
    requested_at: Mapped[datetime] = now_default()
    finished_at: Mapped[datetime | None]
    summary: Mapped[dict[str, Any]] = mapped_column(server_default=text("'{}'::jsonb"))
    sample_ct: Mapped[bytes | None]
    dek_version: Mapped[int | None] = mapped_column(Integer)
    __table_args__ = (
        CheckConstraint("status IN ('pending', 'done', 'failed')", name="status"),
        Index(None, "space_id", "requested_at"),
    )
