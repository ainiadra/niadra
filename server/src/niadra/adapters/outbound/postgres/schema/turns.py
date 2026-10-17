"""The `turns` module (6.7): what an agent read, called, showed, claimed and decided in a turn.

The records themselves live in object storage, one object per turn under
`turns/<tier>/<space>/<hmac(conversation)>/<turn_id>.bin`; the database holds only what must be found or
added up. No column names a conversation in clear: `conversation_hmac` is the space's keyed hash of the
conversation id (or of the task id for a turn without a conversation), so a conversation is found, and its
turns erased, by the hash the prefix is made of.
"""

from datetime import date, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import BigInteger, CheckConstraint, Identity, Index, Integer, text
from sqlalchemy.orm import Mapped, mapped_column

from niadra.adapters.outbound.postgres.schema.base import SpaceBase, dek_version


def _counter() -> Mapped[int]:
    return mapped_column(BigInteger, server_default=text("0"))


class TurnIntake(SpaceBase):
    """One `POST /v1/turns` acknowledged and not yet applied, alive for seconds: the turn frames the edge
    stored, sealed (they carry what the tools showed). `apply_turns` drains them in `seq` order, up to 50 at
    a time, and deletes them (one that failed every attempt is kept with `failed_at`). Only turns the edge
    stored for the first time are here: a duplicate never reaches the aggregates."""

    __tablename__ = "turn_intake"
    space_id: Mapped[UUID] = mapped_column(primary_key=True)
    intake_id: Mapped[UUID] = mapped_column(primary_key=True)
    seq: Mapped[int] = mapped_column(BigInteger, Identity(always=True))
    received_at: Mapped[datetime]
    source_id: Mapped[UUID]
    turns: Mapped[int] = mapped_column(Integer)
    body_ct: Mapped[bytes]
    dek_version: Mapped[int] = dek_version()
    attempts: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    failed_at: Mapped[datetime | None]
    """Set when the intake failed every attempt: kept aside, out of the order, until an admin retries it."""


class TurnKept(SpaceBase):
    """The index of the kept tier, about 150 bytes a turn: promoted by a flag at capture, by request
    (`POST /v1/turns/promote`) or by deterministic sample. The short tier has no row."""

    __tablename__ = "turns_kept"
    space_id: Mapped[UUID] = mapped_column(primary_key=True)
    turn_id: Mapped[str] = mapped_column(primary_key=True)
    conversation_hmac: Mapped[bytes]
    source_id: Mapped[UUID]
    agent: Mapped[str]
    kind: Mapped[str]
    started_at: Mapped[datetime]
    fidelity: Mapped[str]
    completeness: Mapped[str]
    content_mode: Mapped[str]
    replayable: Mapped[bool]
    build_hash: Mapped[bytes | None]
    flags: Mapped[list[str]] = mapped_column(server_default=text("'{}'"))
    kept_by: Mapped[str]
    kept_at: Mapped[datetime]
    kept_until: Mapped[datetime | None]
    __table_args__ = (
        CheckConstraint("kind IN ('message', 'action', 'event', 'timer')", name="kind"),
        CheckConstraint("fidelity IN ('bronze', 'silver', 'gold')", name="fidelity"),
        CheckConstraint(
            "completeness IN ('complete', 'partial', 'incomplete', 'unknown')", name="completeness"
        ),
        CheckConstraint("content_mode IN ('stored', 'pointer', 'hash_only')", name="content_mode"),
        CheckConstraint("kept_by IN ('flag', 'request', 'sample', 'scenario')", name="kept_by"),
        Index(None, "space_id", "conversation_hmac", "started_at"),
        Index(None, "space_id", "source_id", "started_at"),
        Index(None, "space_id", "started_at"),
        Index(None, "space_id", "kept_until"),
        Index(None, "space_id", "kept_at"),
    )


class TurnDaily(SpaceBase):
    """What the turns of a day added up to, per source, agent and build, with no person (6.7): the cost and
    regression reports read only this. `claims` counts verdicts as `<category>|<verdict>`; the latency
    buckets have the bounds of `domain.turns.daily`. `violations` leaves out the results of a relaxation the
    tool declared, which `relaxed_declared` counts."""

    __tablename__ = "turn_daily"
    space_id: Mapped[UUID] = mapped_column(primary_key=True)
    day: Mapped[date] = mapped_column(primary_key=True)
    source_id: Mapped[UUID] = mapped_column(primary_key=True)
    agent: Mapped[str] = mapped_column(primary_key=True)
    build_hash: Mapped[bytes] = mapped_column(primary_key=True)
    turns: Mapped[int] = _counter()
    incomplete: Mapped[int] = _counter()
    tool_calls: Mapped[int] = _counter()
    model_calls: Mapped[int] = _counter()
    errors: Mapped[int] = _counter()
    synthetic: Mapped[int] = _counter()
    cache_hits: Mapped[int] = _counter()
    tokens_in: Mapped[int] = _counter()
    tokens_cached: Mapped[int] = _counter()
    tokens_out: Mapped[int] = _counter()
    cost_micros: Mapped[int] = _counter()
    latency_buckets: Mapped[list[int]] = mapped_column(server_default=text("'{}'"))
    claims: Mapped[dict[str, Any]] = mapped_column(server_default=text("'{}'::jsonb"))
    constraints_sent: Mapped[int] = _counter()
    results_checked: Mapped[int] = _counter()
    violations: Mapped[int] = _counter()
    unverifiable: Mapped[int] = _counter()
    relaxed_declared: Mapped[int] = _counter()
    relaxed_suspected: Mapped[int] = _counter()
    effects: Mapped[int] = _counter()
    items_presented: Mapped[int] = _counter()


class AgentBuild(SpaceBase):
    """A pinned build, by the hash of its pins: prompts and versions, corpus digest, model, assembler, the
    tool schemas' hashes and the compiler's version. The pack's hash is per turn and stays out of it."""

    __tablename__ = "agent_builds"
    space_id: Mapped[UUID] = mapped_column(primary_key=True)
    build_hash: Mapped[bytes] = mapped_column(primary_key=True)
    source_id: Mapped[UUID]
    agent: Mapped[str]
    pins: Mapped[dict[str, Any]]
    sdk: Mapped[str | None]
    adapter: Mapped[str | None]
    first_seen_at: Mapped[datetime]
    last_seen_at: Mapped[datetime]
    __table_args__ = (Index(None, "space_id", "source_id", "agent", "first_seen_at"),)


class Scenario(SpaceBase):
    """A replay scenario: kept turns and the structural assertions they must keep passing."""

    __tablename__ = "scenarios"
    space_id: Mapped[UUID] = mapped_column(primary_key=True)
    scenario_id: Mapped[UUID] = mapped_column(primary_key=True)
    name: Mapped[str]
    origin: Mapped[str]
    turn_ids: Mapped[list[str]]
    assertions: Mapped[list[Any]]
    status: Mapped[str]
    version: Mapped[int] = mapped_column(Integer, server_default=text("1"))
    created_at: Mapped[datetime]
    created_by: Mapped[str]
    updated_at: Mapped[datetime]
    __table_args__ = (
        CheckConstraint("origin IN ('manual', 'report')", name="origin"),
        CheckConstraint("status IN ('active', 'retired')", name="status"),
        Index(None, "space_id", "status", "created_at"),
    )


class ScenarioRun(SpaceBase):
    """What the customer's CI reported for N executions, as the statistical verdict: `summary` holds each
    scenario's verdict and its counts per assertion, which the next run of a scenario takes as its baseline.
    The executions themselves are not kept."""

    __tablename__ = "scenario_runs"
    space_id: Mapped[UUID] = mapped_column(primary_key=True)
    run_id: Mapped[UUID] = mapped_column(primary_key=True)
    scenario_ids: Mapped[list[UUID]]
    build: Mapped[dict[str, Any]]
    status: Mapped[str]
    verdict: Mapped[str | None]
    summary: Mapped[dict[str, Any]] = mapped_column(server_default=text("'{}'::jsonb"))
    mode: Mapped[str]
    vary: Mapped[list[str]] = mapped_column(server_default=text("'{}'"))
    created_at: Mapped[datetime]
    decided_at: Mapped[datetime | None]
    __table_args__ = (
        CheckConstraint("status IN ('pending', 'done')", name="status"),
        CheckConstraint(
            "verdict IS NULL OR verdict IN "
            "('pass', 'flaky', 'infrastructure_error', 'pin_mismatch', 'regression')",
            name="verdict",
        ),
        CheckConstraint("mode IN ('hermetic_turn', 'hermetic_conversation', 'era_memory')", name="mode"),
        Index(None, "space_id", "created_at"),
    )


class DataIssue(SpaceBase):
    """A data problem returned to its owner, with no personal data: up to 50 object references (system
    ids), the type, field and source, and one open row per `fingerprint` (class, type, field, source)."""

    __tablename__ = "data_issues"
    space_id: Mapped[UUID] = mapped_column(primary_key=True)
    issue_id: Mapped[UUID] = mapped_column(primary_key=True)
    kind: Mapped[str]
    fingerprint: Mapped[bytes]
    object_type: Mapped[str | None]
    field: Mapped[str | None]
    source: Mapped[str | None]
    refs: Mapped[list[Any]] = mapped_column(server_default=text("'[]'::jsonb"))
    occurrences: Mapped[int] = mapped_column(BigInteger, server_default=text("1"))
    opened_at: Mapped[datetime]
    last_seen_at: Mapped[datetime]
    acknowledged_at: Mapped[datetime | None]
    acknowledged_by: Mapped[str | None]
    __table_args__ = (
        CheckConstraint(
            "kind IN ('null_field', 'out_of_vocabulary', 'stale_source', 'coverage_drop', 'invalid_value', "
            "'divergence', 'drift', 'rule_conflict', 'type_undeclared', 'field_held', 'budget_reached', "
            "'row_failed', 'inference_limit')",
            name="kind",
        ),
        Index(None, "space_id", "fingerprint", unique=True, postgresql_where=text("acknowledged_at IS NULL")),
        Index(None, "space_id", "opened_at"),
    )


class ConfigHistory(SpaceBase):
    """Each section of the space's configuration, by the digest it had in each applied snapshot where it
    changed: the change log's `config` kind. Section names and digests only, never a value (the control plane
    keeps the documents and who approved them)."""

    __tablename__ = "config_history"
    space_id: Mapped[UUID] = mapped_column(primary_key=True)
    section: Mapped[str] = mapped_column(primary_key=True)
    version: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    applied_at: Mapped[datetime]
    digest: Mapped[bytes]
    __table_args__ = (Index(None, "space_id", "applied_at"),)


class NotificationFeed(SpaceBase):
    """The webhooks' bodies for the company that pulls them (`GET /v1/notifications`), owned by the feed
    (`app/notifications.py`): appended after the transaction that announced the event committed, numbered
    under the space's feed lock so the numbers are the commit order. Ids and pointers only (7.5)."""

    __tablename__ = "notification_feed"
    space_id: Mapped[UUID] = mapped_column(primary_key=True)
    seq: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    event_id: Mapped[UUID]
    type: Mapped[str]
    created_at: Mapped[datetime]
    body: Mapped[dict[str, Any]]
    __table_args__ = (
        Index(None, "space_id", "event_id", unique=True),
        Index(None, "space_id", "created_at"),
    )


class TurnTombstone(SpaceBase):
    """What an erasure fenced off (24.5.5), for the days a late copy of it could still arrive: a conversation
    by its keyed hash (`conversation:<hex>`), whose turns a worker drops whatever intake brings them, or an id
    webhook bodies name the target by (`needle:<id>`), which keeps the event out of the notification feed."""

    __tablename__ = "turn_tombstones"
    space_id: Mapped[UUID] = mapped_column(primary_key=True)
    mark: Mapped[str] = mapped_column(primary_key=True)
    until: Mapped[datetime]
