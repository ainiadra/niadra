"""Measurement, privacy, triggers, webhooks, review, assistant, evaluation, usage and insights."""

from datetime import date, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import BigInteger, CheckConstraint, Index, Integer, LargeBinary, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column

from niadra.adapters.outbound.postgres.schema.base import CHANGE_SEQ, SpaceBase, change_seq, now_default


class Tombstone(SpaceBase):
    """Deletes the continuous export replays as tombstones (22.3)."""

    __tablename__ = "tombstones"
    space_id: Mapped[UUID] = mapped_column(primary_key=True)
    change_seq: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, server_default=CHANGE_SEQ.next_value()
    )
    table_name: Mapped[str]
    row_key: Mapped[dict[str, Any]]
    deleted_at: Mapped[datetime] = now_default()


class ContextUse(SpaceBase):
    __tablename__ = "context_use"
    space_id: Mapped[UUID] = mapped_column(primary_key=True)
    conversation_key: Mapped[str] = mapped_column(primary_key=True)
    manifest_hash: Mapped[str] = mapped_column(primary_key=True)
    source_id: Mapped[UUID] = mapped_column(primary_key=True)
    measure_version: Mapped[str] = mapped_column(primary_key=True)
    session_id: Mapped[UUID | None]
    profile_id: Mapped[UUID]
    channel: Mapped[str]
    view: Mapped[str]
    experiment_group: Mapped[str | None]
    items: Mapped[list[Any]]
    questions: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    repeated: Mapped[int] = mapped_column(Integer)
    used: Mapped[int] = mapped_column(Integer)
    contradicted: Mapped[int] = mapped_column(Integer)
    late: Mapped[bool] = mapped_column(server_default=text("false"))
    transfer_unread: Mapped[bool | None]
    not_measured_reason: Mapped[str | None]
    measured_at: Mapped[datetime]
    change_seq: Mapped[int] = change_seq()
    tokens_saved: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    slots: Mapped[list[Any] | None]
    """The per-turn slots of the delivery and the agent's verdicts on them (11, 3.4); null before them."""
    # Rows about an object its lines carried, those of objects used, and messages against an instruction
    # about one.
    object_rows: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    object_rows_used: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    instruction_violations: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    __table_args__ = (
        Index("ix_context_use_space_id_session_id", "space_id", "session_id"),
        Index("ix_context_use_space_id_profile_id", "space_id", "profile_id"),
        Index(None, "space_id", "change_seq"),
    )


class ContextUseDaily(SpaceBase):
    __tablename__ = "context_use_daily"
    space_id: Mapped[UUID] = mapped_column(primary_key=True)
    day: Mapped[date] = mapped_column(primary_key=True)
    source_id: Mapped[UUID] = mapped_column(primary_key=True)
    channel: Mapped[str] = mapped_column(primary_key=True)
    view: Mapped[str] = mapped_column(primary_key=True)
    experiment_group: Mapped[str] = mapped_column(primary_key=True, server_default="")
    deliveries: Mapped[int] = mapped_column(BigInteger)
    deliveries_used: Mapped[int] = mapped_column(BigInteger)
    questions: Mapped[int] = mapped_column(BigInteger)
    repeated: Mapped[int] = mapped_column(BigInteger)
    contradicted: Mapped[int] = mapped_column(BigInteger)
    transfers: Mapped[int] = mapped_column(BigInteger)
    transfers_unread: Mapped[int] = mapped_column(BigInteger)
    not_measured: Mapped[int] = mapped_column(BigInteger)
    recontacts: Mapped[int] = mapped_column(BigInteger)
    sessions: Mapped[int] = mapped_column(BigInteger)
    late_deliveries: Mapped[int] = mapped_column(BigInteger, server_default=text("0"))
    no_context: Mapped[int] = mapped_column(BigInteger, server_default=text("0"))
    selection_misses: Mapped[int] = mapped_column(BigInteger, server_default=text("0"))
    trimmed_deliveries: Mapped[int] = mapped_column(BigInteger, server_default=text("0"))
    tokens_saved: Mapped[int] = mapped_column(BigInteger, server_default=text("0"))
    answers_checked: Mapped[int] = mapped_column(BigInteger, server_default=text("0"))
    unbacked_values: Mapped[int] = mapped_column(BigInteger, server_default=text("0"))
    guard_violations: Mapped[int] = mapped_column(BigInteger, server_default=text("0"))
    object_rows: Mapped[int] = mapped_column(BigInteger, server_default=text("0"))
    object_rows_used: Mapped[int] = mapped_column(BigInteger, server_default=text("0"))
    instruction_violations: Mapped[int] = mapped_column(BigInteger, server_default=text("0"))


class ContextUseUnits(SpaceBase):
    """Per day, source, view and item unit: measured deliveries that carried it, how many the agent used
    and how many trimmed items it needed. Counters only, no customer data (15.8, 19.2)."""

    __tablename__ = "context_use_units"
    space_id: Mapped[UUID] = mapped_column(primary_key=True)
    day: Mapped[date] = mapped_column(primary_key=True)
    source_id: Mapped[UUID] = mapped_column(primary_key=True)
    view: Mapped[str] = mapped_column(primary_key=True)
    unit: Mapped[str] = mapped_column(primary_key=True)
    deliveries: Mapped[int] = mapped_column(BigInteger, server_default=text("0"))
    used: Mapped[int] = mapped_column(BigInteger, server_default=text("0"))
    missed: Mapped[int] = mapped_column(BigInteger, server_default=text("0"))


class ContextUseSlotDaily(SpaceBase):
    """Per day, source, conversation channel, view, experiment group and retrieval channel: the reads
    the channel ran in or missed its deadline in, the slots it brought and what agents did with them.
    Counters only, no customer data (11, 3.4)."""

    __tablename__ = "context_use_slot_daily"
    space_id: Mapped[UUID] = mapped_column(primary_key=True)
    day: Mapped[date] = mapped_column(primary_key=True)
    source_id: Mapped[UUID] = mapped_column(primary_key=True)
    channel: Mapped[str] = mapped_column(primary_key=True)
    view: Mapped[str] = mapped_column(primary_key=True)
    experiment_group: Mapped[str] = mapped_column(primary_key=True, server_default="")
    slot_channel: Mapped[str] = mapped_column(primary_key=True)
    turns: Mapped[int] = mapped_column(BigInteger, server_default=text("0"))
    skipped: Mapped[int] = mapped_column(BigInteger, server_default=text("0"))
    slots: Mapped[int] = mapped_column(BigInteger, server_default=text("0"))
    used: Mapped[int] = mapped_column(BigInteger, server_default=text("0"))
    repeated: Mapped[int] = mapped_column(BigInteger, server_default=text("0"))
    contradicted: Mapped[int] = mapped_column(BigInteger, server_default=text("0"))


class ContextUseGuardDaily(SpaceBase):
    """Per day, source, settled value type and arm (`value`, `guards`, `control`): the values slotted or
    guarded and how many the agent contradicted (13, 6.1). Counters only, no customer data."""

    __tablename__ = "context_use_guard_daily"
    space_id: Mapped[UUID] = mapped_column(primary_key=True)
    day: Mapped[date] = mapped_column(primary_key=True)
    source_id: Mapped[UUID] = mapped_column(primary_key=True)
    value_type: Mapped[str] = mapped_column(primary_key=True)
    arm: Mapped[str] = mapped_column(primary_key=True)
    slots: Mapped[int] = mapped_column(BigInteger, server_default=text("0"))
    contradicted: Mapped[int] = mapped_column(BigInteger, server_default=text("0"))


class ContextUseViewDaily(SpaceBase):
    """Per day, reading source, target model, view, arm and learned section: the deliveries that carried
    the section, used it and missed it, and per delivery (section '') the agent's questions and repeats
    (13, 6.3). Counters only, no customer data."""

    __tablename__ = "context_use_view_daily"
    space_id: Mapped[UUID] = mapped_column(primary_key=True)
    day: Mapped[date] = mapped_column(primary_key=True)
    source_id: Mapped[UUID] = mapped_column(primary_key=True)
    target: Mapped[str] = mapped_column(primary_key=True)
    view: Mapped[str] = mapped_column(primary_key=True)
    arm: Mapped[str] = mapped_column(primary_key=True)
    section: Mapped[str] = mapped_column(primary_key=True)
    deliveries: Mapped[int] = mapped_column(BigInteger, server_default=text("0"))
    used: Mapped[int] = mapped_column(BigInteger, server_default=text("0"))
    missed: Mapped[int] = mapped_column(BigInteger, server_default=text("0"))
    questions: Mapped[int] = mapped_column(BigInteger, server_default=text("0"))
    repeated: Mapped[int] = mapped_column(BigInteger, server_default=text("0"))


class ViewBudgetRun(SpaceBase):
    """One nightly run of the learned views, append-only: every reader's factors after it, the evidence
    and the control groups' say (13, 6.3). The last row is what the read path compiles with."""

    __tablename__ = "view_budgets"
    space_id: Mapped[UUID] = mapped_column(primary_key=True)
    run_id: Mapped[UUID] = mapped_column(primary_key=True)
    day: Mapped[date]
    computed_at: Mapped[datetime]
    version: Mapped[int] = mapped_column(Integer)
    outcome: Mapped[str]
    factors: Mapped[dict[str, Any]]
    evidence: Mapped[dict[str, Any]]
    rules: Mapped[str]
    __table_args__ = (
        CheckConstraint("outcome IN ('applied', 'unchanged', 'no_verdicts', 'vetoed')", name="outcome"),
    )


class RetrievalWeightRun(SpaceBase):
    """One nightly run of the space's retrieval weights, append-only: the weights in force after it, the
    evidence and the control group's say (11, 3.4). The last row is what the read path fuses with."""

    __tablename__ = "retrieval_weights"
    space_id: Mapped[UUID] = mapped_column(primary_key=True)
    run_id: Mapped[UUID] = mapped_column(primary_key=True)
    day: Mapped[date]
    computed_at: Mapped[datetime]
    version: Mapped[int] = mapped_column(Integer)
    outcome: Mapped[str]
    weights: Mapped[dict[str, Any]]
    evidence: Mapped[dict[str, Any]]
    rules: Mapped[str]
    __table_args__ = (
        CheckConstraint("outcome IN ('applied', 'unchanged', 'no_verdicts', 'vetoed')", name="outcome"),
    )


class ContextTrim(SpaceBase):
    """The item units each source leaves unused in a view, as the last measurement derived them (15.8)."""

    __tablename__ = "context_trims"
    space_id: Mapped[UUID] = mapped_column(primary_key=True)
    source_id: Mapped[UUID] = mapped_column(primary_key=True)
    view: Mapped[str] = mapped_column(primary_key=True)
    units: Mapped[list[Any]]
    derived_at: Mapped[datetime]
    window_days: Mapped[int] = mapped_column(Integer)
    min_deliveries: Mapped[int] = mapped_column(Integer)


class MeasuredSession(SpaceBase):
    """One row per measured session: the input of the 72 h recontact and the transfer rate (19.2)."""

    __tablename__ = "measured_sessions"
    space_id: Mapped[UUID] = mapped_column(primary_key=True)
    session_id: Mapped[UUID] = mapped_column(primary_key=True)
    measure_version: Mapped[str]
    conversation_key: Mapped[str]
    source_id: Mapped[UUID]
    channel: Mapped[str]
    profile_id: Mapped[UUID | None]
    experiment_group: Mapped[str] = mapped_column(server_default="")
    day: Mapped[date]
    ended_at: Mapped[datetime]
    measured_at: Mapped[datetime]
    no_context: Mapped[bool]
    normalized_category: Mapped[str | None]
    initiated_by: Mapped[str | None]
    transfer_state: Mapped[str | None]
    recontact_scope: Mapped[str | None]
    __table_args__ = (
        CheckConstraint(
            "transfer_state IN ('none', 'read', 'unread', 'not_integrated')", name="transfer_state"
        ),
        CheckConstraint("recontact_scope IN ('category', 'general')", name="recontact_scope"),
        Index("ix_measured_sessions_space_id_profile_id_ended_at", "space_id", "profile_id", "ended_at"),
    )


class ErasureRequest(SpaceBase):
    __tablename__ = "erasure_requests"
    space_id: Mapped[UUID] = mapped_column(primary_key=True)
    request_id: Mapped[UUID] = mapped_column(primary_key=True)
    target_kind: Mapped[str]
    target_id: Mapped[str]
    status: Mapped[str]
    requested_by: Mapped[str]
    requested_at: Mapped[datetime]
    completed_at: Mapped[datetime | None]
    counts: Mapped[dict[str, Any]] = mapped_column(server_default=text("'{}'"))
    receipt_hash: Mapped[bytes | None]
    __table_args__ = (
        CheckConstraint("target_kind IN ('profile', 'handle', 'conversation')", name="target_kind"),
        CheckConstraint("status IN ('pending', 'running', 'completed', 'failed')", name="status"),
    )


class PrivacyFeed(SpaceBase):
    """Read from the writer every 2 s by every read process, by time window (5.11)."""

    __tablename__ = "privacy_feed"
    space_id: Mapped[UUID] = mapped_column(primary_key=True)
    id: Mapped[UUID] = mapped_column(primary_key=True)
    kind: Mapped[str]
    profile_ids: Mapped[list[UUID]] = mapped_column(server_default=text("'{}'"))
    handle_ids: Mapped[list[UUID]] = mapped_column(server_default=text("'{}'"))
    privacy_version: Mapped[int] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = now_default()
    __table_args__ = (
        CheckConstraint("kind IN ('erasure', 'unmerge', 'merge', 'retraction', 'revocation')", name="kind"),
        Index(None, "created_at"),
    )


class RawObject(SpaceBase):
    """Raw content in object storage, indexed as it is written (`domain.privacy.raw_index`): the retention
    reads it by `written_at`, an erasure a payload under no handle by its `tokens`."""

    __tablename__ = "raw_objects"
    space_id: Mapped[UUID] = mapped_column(primary_key=True)
    object_key: Mapped[str] = mapped_column(primary_key=True)
    owner: Mapped[str]
    written_at: Mapped[datetime]
    tokens: Mapped[list[bytes]] = mapped_column(ARRAY(LargeBinary), server_default=text("'{}'"))
    __table_args__ = (
        Index(None, "space_id", "written_at"),
        Index(None, "space_id", "owner"),
        Index(None, "tokens", postgresql_using="gin"),
    )


class ExportRun(SpaceBase):
    __tablename__ = "export_runs"
    space_id: Mapped[UUID] = mapped_column(primary_key=True)
    run_id: Mapped[UUID] = mapped_column(primary_key=True)
    schedule_id: Mapped[str]
    from_seq: Mapped[int] = mapped_column(BigInteger)
    to_seq: Mapped[int] = mapped_column(BigInteger)
    status: Mapped[str]
    manifest: Mapped[dict[str, Any] | None]
    started_at: Mapped[datetime]
    finished_at: Mapped[datetime | None]
    error: Mapped[str | None]


class TriggerFiring(SpaceBase):
    __tablename__ = "trigger_firings"
    space_id: Mapped[UUID] = mapped_column(primary_key=True)
    firing_id: Mapped[UUID] = mapped_column(primary_key=True)
    rule_id: Mapped[str]
    rule_version: Mapped[str]
    profile_id: Mapped[UUID]
    window_key: Mapped[str]
    idempotency_key: Mapped[bytes]
    evidence: Mapped[list[Any]]
    membership_version: Mapped[int] = mapped_column(BigInteger)
    late: Mapped[bool] = mapped_column(server_default=text("false"))
    retracted_at: Mapped[datetime | None]
    fired_at: Mapped[datetime]
    change_seq: Mapped[int] = change_seq()
    __table_args__ = (
        UniqueConstraint("space_id", "rule_id", "profile_id", "window_key"),
        Index(None, "space_id", "profile_id", "fired_at"),
        Index(None, "space_id", "fired_at"),
    )


class WebhookDelivery(SpaceBase):
    __tablename__ = "webhook_deliveries"
    space_id: Mapped[UUID] = mapped_column(primary_key=True)
    delivery_id: Mapped[UUID] = mapped_column(primary_key=True)
    endpoint_id: Mapped[str]
    event_type: Mapped[str]
    body: Mapped[dict[str, Any]]
    body_hash: Mapped[bytes]
    attempts: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    next_attempt_at: Mapped[datetime | None]
    status: Mapped[str]
    last_status_code: Mapped[int | None] = mapped_column(Integer)
    last_error: Mapped[str | None]
    created_at: Mapped[datetime]
    delivered_at: Mapped[datetime | None]
    __table_args__ = (
        CheckConstraint("status IN ('pending', 'delivered', 'failing', 'dead')", name="status"),
        Index(None, "space_id", "endpoint_id", "created_at"),
        Index(None, "space_id", "created_at"),
    )


class ReviewSample(SpaceBase):
    __tablename__ = "review_samples"
    space_id: Mapped[UUID] = mapped_column(primary_key=True)
    sample_id: Mapped[UUID] = mapped_column(primary_key=True)
    session_id: Mapped[UUID]
    profile_id: Mapped[UUID | None]
    conversation_id: Mapped[str | None]
    condition: Mapped[str]
    purpose: Mapped[str | None]
    verdicts: Mapped[list[Any]] = mapped_column(server_default=text("'[]'"))
    reviewer_id: Mapped[UUID | None]
    created_at: Mapped[datetime]
    reviewed_at: Mapped[datetime | None]


class AssistRun(SpaceBase):
    __tablename__ = "assist_runs"
    space_id: Mapped[UUID] = mapped_column(primary_key=True)
    run_id: Mapped[UUID] = mapped_column(primary_key=True)
    task: Mapped[str]
    sample_ids: Mapped[list[str]]
    diff_hash: Mapped[str | None]
    proposal: Mapped[dict[str, Any] | None]
    status: Mapped[str]
    requested_by: Mapped[UUID]
    created_at: Mapped[datetime]


class UsageHourly(SpaceBase):
    __tablename__ = "usage_hourly"
    space_id: Mapped[UUID] = mapped_column(primary_key=True)
    hour: Mapped[datetime] = mapped_column(primary_key=True)
    metric: Mapped[str] = mapped_column(primary_key=True)
    # The agent or system; the nil id for the space's own work (exports, triggers, webhooks).
    source_id: Mapped[UUID] = mapped_column(
        primary_key=True, server_default=text("'00000000-0000-0000-0000-000000000000'")
    )
    quantity: Mapped[int] = mapped_column(BigInteger)
    cost_micros: Mapped[int] = mapped_column(BigInteger, server_default=text("0"))


class UsageEvent(SpaceBase):
    __tablename__ = "usage_events"
    space_id: Mapped[UUID] = mapped_column(primary_key=True)
    usage_id: Mapped[str] = mapped_column(primary_key=True)
    metric: Mapped[str]
    quantity: Mapped[int] = mapped_column(BigInteger)
    cost_micros: Mapped[int] = mapped_column(BigInteger, server_default=text("0"))
    occurred_at: Mapped[datetime]


class ModelUsageDaily(SpaceBase):
    """What the agents' model calls reported, summed per day, source, provider and model: tokens and the
    provider's prompt cache (15.3). Counters only, no customer data."""

    __tablename__ = "model_usage_daily"
    space_id: Mapped[UUID] = mapped_column(primary_key=True)
    day: Mapped[date] = mapped_column(primary_key=True)
    source_id: Mapped[UUID] = mapped_column(primary_key=True)
    provider: Mapped[str] = mapped_column(primary_key=True)
    model: Mapped[str] = mapped_column(primary_key=True)
    calls: Mapped[int] = mapped_column(BigInteger, server_default=text("0"))
    prompt_tokens: Mapped[int] = mapped_column(BigInteger, server_default=text("0"))
    cached_tokens: Mapped[int] = mapped_column(BigInteger, server_default=text("0"))
    cache_write_tokens: Mapped[int] = mapped_column(BigInteger, server_default=text("0"))


class KnowledgeGapDay(SpaceBase):
    """Per day, what customers asked that memory had no record of, or that an agent had to ask them for,
    by kind, subject (a value type or a predicate) and conversation category (13, 6.5). Counters and a
    distinct-count sketch of hashed profile ids, never an id or a value."""

    __tablename__ = "knowledge_gaps"
    space_id: Mapped[UUID] = mapped_column(primary_key=True)
    day: Mapped[date] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(primary_key=True)
    subject: Mapped[str] = mapped_column(primary_key=True)
    topic: Mapped[str] = mapped_column(primary_key=True, server_default="")
    turns: Mapped[int] = mapped_column(BigInteger)
    customers: Mapped[bytes]
    """A HyperLogLog sketch: mergeable across days, so a month counts each customer once."""
    __table_args__ = (
        CheckConstraint(
            "kind IN ('no_record', 'agent_question', 'answered', 'object_unresolved')", name="kind"
        ),
    )


class InsightReview(SpaceBase):
    """The team's word on one knowledge gap or standing-answer proposal (13, 6.5): accepted or dismissed,
    by whom and when. The key is the subject and category, never customer data."""

    __tablename__ = "insight_reviews"
    space_id: Mapped[UUID] = mapped_column(primary_key=True)
    key: Mapped[str] = mapped_column(primary_key=True)
    decision: Mapped[str]
    decided_by: Mapped[str]
    decided_at: Mapped[datetime]
    note: Mapped[str | None]
    __table_args__ = (CheckConstraint("decision IN ('accepted', 'dismissed')", name="decision"),)


class TenantRollup(SpaceBase):
    __tablename__ = "tenant_rollups"
    space_id: Mapped[UUID] = mapped_column(primary_key=True)
    day: Mapped[date] = mapped_column(primary_key=True)
    dimension: Mapped[str] = mapped_column(primary_key=True)
    bucket: Mapped[str] = mapped_column(primary_key=True)
    value: Mapped[int] = mapped_column(BigInteger)
