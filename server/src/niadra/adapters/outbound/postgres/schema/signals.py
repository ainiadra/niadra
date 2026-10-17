"""The tables of subject signals, of outcomes and experiments, and of review requests and legal holds (6.7).

Three owner modules, one file:
- `signals`: the compact state per subject (`subject_signals`), beneficiaries, the nightly priors, the
  tombstones of inferences a subject deleted, and the index of the raw interaction segments kept in object
  storage (`measure/raw/`, `measure/daily/`). The per-subject tables are hash-partitioned by the origin
  handle, like the memory tables.
- `measure`: outcome links (who and what an outcome is attributed to, by method and band), the daily
  aggregates without a person, the counterfactual runs and the reports.
- `privacy`: requests to review an automated decision (LGPD, art. 20) and legal holds (art. 16).
"""

from datetime import date, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import BigInteger, CheckConstraint, Index, Integer, SmallInteger, text
from sqlalchemy.orm import Mapped, mapped_column

from niadra.adapters.outbound.postgres.schema.base import SpaceBase, change_seq, dek_version, now_default
from niadra.domain.signals.body import MAX_STORED_BYTES

HASHED_BY_HANDLE = {"postgresql_partition_by": "HASH (origin_handle_id)"}
SELF = UUID(int=0)
"""`beneficiary_id` of what is the subject's own: signals for no beneficiary."""


class SubjectSignalsRow(SpaceBase):
    """What one subject (or one of their beneficiaries) wants, refuses and is, sealed in one body (its derived
    accumulators within 16 KB, what the person said whole): affinity by attribute and value, interests,
    attributes, persistent stated constraints and the
    effective evidence (24.3.2). Rewritten by `SignalsService.derive_due`, two statements per subject per
    window."""

    __tablename__ = "subject_signals"
    space_id: Mapped[UUID] = mapped_column(primary_key=True)
    origin_handle_id: Mapped[UUID] = mapped_column(primary_key=True)
    beneficiary_id: Mapped[UUID] = mapped_column(primary_key=True)
    body_ct: Mapped[bytes]
    body_version: Mapped[int] = mapped_column(SmallInteger)
    """The layout of the sealed body (`domain.signals.body.BODY_VERSION`)."""
    size_bytes: Mapped[int] = mapped_column(Integer)
    derived_at: Mapped[datetime]
    last_activity_at: Mapped[datetime]
    """13 months past it, the derived accumulators decay; what the person said stays (24.5)."""
    decayed_at: Mapped[datetime | None]
    """When the retention pass last took the derived accumulators out of the body
    (`domain.signals.body.told`): a row decays again only once new activity moves `last_activity_at` past
    it."""
    dek_version: Mapped[int] = dek_version()
    change_seq: Mapped[int] = change_seq()
    opted_out_at: Mapped[datetime | None]
    """On the subject's own row, the objection to personalization: nothing is derived for them (24.5.3)."""
    __table_args__ = (
        Index(None, "space_id", "last_activity_at"),
        Index(None, "space_id", "change_seq"),
        CheckConstraint(f"size_bytes <= {MAX_STORED_BYTES}", name="size_bytes"),
        HASHED_BY_HANDLE,
    )


class SignalIntakeRow(SpaceBase):
    """An interaction waiting for `SignalsService.derive_due`, sealed: from a turn record (`t:<turn>:<index>`)
    or a standalone batch item (`b:<key>`). Minutes long; `origin_handle_id` is null while its conversation
    has no known subject, for as long as that takes."""

    __tablename__ = "signal_intake"
    space_id: Mapped[UUID] = mapped_column(primary_key=True)
    entry_key: Mapped[str] = mapped_column(primary_key=True)
    origin_handle_id: Mapped[UUID | None]
    session: Mapped[str | None]
    """The keyed digest of the conversation."""
    received_at: Mapped[datetime]
    body_ct: Mapped[bytes]
    dek_version: Mapped[int] = dek_version()
    checked_at: Mapped[datetime | None]
    """When the loop last looked for the subject of a row without one and found none (`recheck_due`)."""
    __table_args__ = (
        Index(None, "space_id", "origin_handle_id", "received_at"),
        Index(None, "space_id", "received_at"),
    )


class BeneficiaryRow(SpaceBase):
    """Whom a purchase or a plan is for, under the subject's profile and without a handle of their own, with
    signals of their own. A gift goes to a disposable one; promotion to a profile goes through `profile_links`
    (role `dependent`)."""

    __tablename__ = "beneficiaries"
    space_id: Mapped[UUID] = mapped_column(primary_key=True)
    origin_handle_id: Mapped[UUID] = mapped_column(primary_key=True)
    beneficiary_id: Mapped[UUID] = mapped_column(primary_key=True)
    relation: Mapped[str]
    label_ct: Mapped[bytes | None]
    dek_version: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(server_default="active")
    created_at: Mapped[datetime]
    updated_at: Mapped[datetime]
    change_seq: Mapped[int] = change_seq()
    __table_args__ = (
        Index(None, "space_id", "change_seq"),
        CheckConstraint(
            "relation IN ('dependent', 'spouse', 'child', 'parent', 'relative', 'friend', 'gift', 'other')",
            name="relation",
        ),
        CheckConstraint("status IN ('active', 'discarded')", name="status"),
        CheckConstraint("(label_ct IS NULL) = (dek_version IS NULL)", name="label_sealed"),
        HASHED_BY_HANDLE,
    )


class SignalPriorRow(SpaceBase):
    """The segment's prior of an attribute's value (`lift = (S + α·μ) / (E + α)`, α = 5), computed nightly
    from aggregates: no person."""

    __tablename__ = "signal_priors"
    space_id: Mapped[UUID] = mapped_column(primary_key=True)
    segment: Mapped[str] = mapped_column(primary_key=True)
    attr: Mapped[str] = mapped_column(primary_key=True)
    value: Mapped[str] = mapped_column(primary_key=True)
    prior: Mapped[float]
    exposures: Mapped[int] = mapped_column(BigInteger)
    signals: Mapped[float]
    computed_at: Mapped[datetime]


class InferenceSuppressionRow(SpaceBase):
    """An inference the subject deleted (LGPD, art. 20): the same evidence never infers it again."""

    __tablename__ = "inference_suppressions"
    space_id: Mapped[UUID] = mapped_column(primary_key=True)
    origin_handle_id: Mapped[UUID] = mapped_column(primary_key=True)
    beneficiary_id: Mapped[UUID] = mapped_column(primary_key=True)
    inference_key_hmac: Mapped[bytes] = mapped_column(primary_key=True)
    kind: Mapped[str]
    evidence_until: Mapped[datetime]
    """Evidence at or before it no longer counts for this inference."""
    created_at: Mapped[datetime] = now_default()
    __table_args__ = (
        CheckConstraint("kind IN ('affinity', 'soft_constraint', 'attribute', 'interest')", name="kind"),
    )


class MeasureSegmentRow(SpaceBase):
    """One object of raw interactions (hourly, 45 days), of their daily aggregate or of the day's unmet
    demand (13 months) in object storage; an erasure rewrites an aggregate without the erased subjects."""

    __tablename__ = "measure_segments"
    space_id: Mapped[UUID] = mapped_column(primary_key=True)
    segment_id: Mapped[UUID] = mapped_column(primary_key=True)
    kind: Mapped[str]
    starts_at: Mapped[datetime]
    object_key: Mapped[str]
    rows: Mapped[int] = mapped_column(Integer)
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    sha256: Mapped[bytes]
    created_at: Mapped[datetime]
    rewritten_at: Mapped[datetime | None]
    expires_at: Mapped[datetime]
    __table_args__ = (
        Index(None, "space_id", "kind", "starts_at", unique=True),
        Index(None, "space_id", "expires_at"),
        CheckConstraint("kind IN ('raw_hourly', 'daily', 'demand_daily')", name="kind"),
    )


class OutcomeLinkRow(SpaceBase):
    """An outcome (an object reaching a state) attributed to what the agent did: a turn, an action, an
    exposure or a handoff, by one method. Deterministic methods (line, order) and probable ones (identity,
    assisted handoff) stay apart, so no report sums a weight as money (24.7). `link_id` is derived from the
    object, the line, the method and the origin, so attributing the same object again rewrites its links."""

    __tablename__ = "outcome_links"
    space_id: Mapped[UUID] = mapped_column(primary_key=True)
    link_id: Mapped[UUID] = mapped_column(primary_key=True)
    outcome: Mapped[str]
    """The name of the outcome definition in the `measurement` document."""
    origin_handle_id: Mapped[UUID]
    outcome_object_id: Mapped[UUID]
    object_ref: Mapped[str]
    """`type:namespace:id`, what the company's BI knows the object by."""
    outcome_state: Mapped[str]
    line_ref: Mapped[str | None]
    turn_id: Mapped[str | None]
    action_id: Mapped[UUID | None]
    exposure_id: Mapped[str | None]
    position: Mapped[int | None] = mapped_column(SmallInteger)
    """The card's position in the exposure, from the exposure token."""
    handoff_id: Mapped[UUID | None]
    agent: Mapped[str | None]
    method: Mapped[str]
    band: Mapped[str]
    confidence: Mapped[float | None]
    finality: Mapped[str]
    value_minor: Mapped[int | None] = mapped_column(BigInteger)
    currency: Mapped[str | None]
    valid_at: Mapped[datetime]
    known_at: Mapped[datetime]
    final_at: Mapped[datetime | None]
    """When a provisional link becomes final without the object saying so: the end of the exchange window."""
    order_ref: Mapped[str | None]
    """The order it counts in when that is not the object: the purchase a split order's parts share, under a
    definition that counts whole orders."""
    __table_args__ = (
        Index(None, "space_id", "origin_handle_id"),
        Index(None, "space_id", "outcome_object_id"),
        Index(None, "space_id", "known_at"),
        Index(None, "space_id", "final_at", postgresql_where=text("finality = 'provisional'")),
        CheckConstraint("method IN ('line', 'order', 'identity', 'assisted_handoff')", name="method"),
        CheckConstraint("band IN ('deterministic', 'probable')", name="band"),
        CheckConstraint("(method IN ('line', 'order')) = (band = 'deterministic')", name="band_of_method"),
        CheckConstraint("finality IN ('provisional', 'final', 'expired_without_outcome')", name="finality"),
        CheckConstraint("(value_minor IS NULL) = (currency IS NULL)", name="value_has_currency"),
        CheckConstraint(
            "num_nonnulls(turn_id, action_id, exposure_id, handoff_id) > 0",
            name="has_origin",
        ),
    )


class AttributionDailyRow(SpaceBase):
    """Outcomes and value by day, outcome, agent, method, band and finality: no person, 13 months."""

    __tablename__ = "attribution_daily"
    space_id: Mapped[UUID] = mapped_column(primary_key=True)
    day: Mapped[date] = mapped_column(primary_key=True)
    outcome: Mapped[str] = mapped_column(primary_key=True)
    agent: Mapped[str] = mapped_column(primary_key=True)
    method: Mapped[str] = mapped_column(primary_key=True)
    band: Mapped[str] = mapped_column(primary_key=True)
    finality: Mapped[str] = mapped_column(primary_key=True)
    currency: Mapped[str] = mapped_column(primary_key=True)
    """Empty for outcomes without money."""
    outcomes: Mapped[int] = mapped_column(Integer)
    lines: Mapped[int] = mapped_column(Integer)
    value_minor: Mapped[int] = mapped_column(BigInteger)


class InterleaveDailyRow(SpaceBase):
    """Interleaved impressions of a tool's two rankings, and which one the person's engagement credited."""

    __tablename__ = "interleave_daily"
    space_id: Mapped[UUID] = mapped_column(primary_key=True)
    day: Mapped[date] = mapped_column(primary_key=True)
    experiment: Mapped[str] = mapped_column(primary_key=True)
    tool: Mapped[str] = mapped_column(primary_key=True)
    impressions: Mapped[int] = mapped_column(Integer)
    wins_a: Mapped[int] = mapped_column(Integer)
    wins_b: Mapped[int] = mapped_column(Integer)
    ties: Mapped[int] = mapped_column(Integer)


class CounterfactualRunRow(SpaceBase):
    """A tool-level counterfactual the company's CI ran: both variants live, the base twice for the noise
    floor. It answers whether an element changes what the tool returns, never whether it is better."""

    __tablename__ = "counterfactual_runs"
    space_id: Mapped[UUID] = mapped_column(primary_key=True)
    run_id: Mapped[UUID] = mapped_column(primary_key=True)
    tool: Mapped[str]
    element: Mapped[str]
    cases: Mapped[int] = mapped_column(Integer)
    overlap_at_k: Mapped[float | None]
    noise_floor: Mapped[float | None]
    limits: Mapped[list[str]] = mapped_column(server_default=text("'{}'"))
    detail: Mapped[dict[str, Any]] = mapped_column(server_default=text("'{}'"))
    created_at: Mapped[datetime]
    __table_args__ = (Index(None, "space_id", "tool", "created_at"),)


class MeasurementReportRow(SpaceBase):
    __tablename__ = "measurement_reports"
    space_id: Mapped[UUID] = mapped_column(primary_key=True)
    report_id: Mapped[UUID] = mapped_column(primary_key=True)
    kind: Mapped[str]
    period_start: Mapped[date]
    period_end: Mapped[date]
    body: Mapped[dict[str, Any]]
    created_at: Mapped[datetime]
    __table_args__ = (
        Index(None, "space_id", "kind", "created_at"),
        CheckConstraint(
            "kind IN ('attribution', 'reconcile', 'interleaving', 'counterfactual', 'power', 'experiment', "
            "'cost')",
            name="kind",
        ),
        CheckConstraint("period_start <= period_end", name="period"),
    )


class ReviewRequestRow(SpaceBase):
    """A subject's request to review an automated decision (LGPD, art. 20), with the explanation built from
    receipts. The controller reviews; Niadra records the resolution. Kept at least 395 days (24.5)."""

    __tablename__ = "review_requests"
    space_id: Mapped[UUID] = mapped_column(primary_key=True)
    request_id: Mapped[UUID] = mapped_column(primary_key=True)
    profile_id: Mapped[UUID]
    decision_receipt_id: Mapped[UUID | None]
    reason_ct: Mapped[bytes]
    explanation: Mapped[dict[str, Any]] = mapped_column(server_default=text("'{}'"))
    """Receipt ids, rules, versions and inputs by reference: never personal content."""
    status: Mapped[str] = mapped_column(server_default="open")
    outcome: Mapped[str | None]
    resolution_ct: Mapped[bytes | None]
    created_at: Mapped[datetime]
    resolved_at: Mapped[datetime | None]
    resolved_by: Mapped[str | None]
    dek_version: Mapped[int] = dek_version()
    __table_args__ = (
        Index(None, "space_id", "status", "created_at"),
        Index(None, "space_id", "profile_id"),
        CheckConstraint("status IN ('open', 'resolved')", name="status"),
        CheckConstraint("outcome IN ('upheld', 'reversed', 'corrected')", name="outcome"),
        CheckConstraint(
            "(status = 'resolved') = (outcome IS NOT NULL AND resolved_at IS NOT NULL)", name="resolved"
        ),
    )


class LegalHoldRow(SpaceBase):
    """A hold on purging a handle's, an object's or a conversation's data for a legal obligation (LGPD,
    art. 16): the retention sweep skips it, and an erasure takes it out of every read at once and waits for
    the release to remove it. A conversation is named by the keyed hash turn records use, never by its id."""

    __tablename__ = "legal_holds"
    space_id: Mapped[UUID] = mapped_column(primary_key=True)
    hold_id: Mapped[UUID] = mapped_column(primary_key=True)
    scope: Mapped[str]
    handle_id: Mapped[UUID | None]
    object_id: Mapped[UUID | None]
    conversation_hmac: Mapped[bytes | None]
    reason_ct: Mapped[bytes]
    status: Mapped[str] = mapped_column(server_default="active")
    created_at: Mapped[datetime]
    created_by: Mapped[str]
    released_at: Mapped[datetime | None]
    released_by: Mapped[str | None]
    release_reason_ct: Mapped[bytes | None]
    dek_version: Mapped[int] = dek_version()
    __table_args__ = (
        Index(None, "space_id", "handle_id", postgresql_where=text("status = 'active'")),
        Index(None, "space_id", "object_id", postgresql_where=text("status = 'active'")),
        Index(None, "space_id", "conversation_hmac", postgresql_where=text("status = 'active'")),
        CheckConstraint("scope IN ('handle', 'object', 'conversation')", name="scope"),
        CheckConstraint("status IN ('active', 'released')", name="status"),
        CheckConstraint(
            "(scope = 'handle') = (handle_id IS NOT NULL) AND (scope = 'object') = (object_id IS NOT NULL) "
            "AND (scope = 'conversation') = (conversation_hmac IS NOT NULL)",
            name="one_target",
        ),
        CheckConstraint("(status = 'released') = (released_at IS NOT NULL)", name="released"),
    )
