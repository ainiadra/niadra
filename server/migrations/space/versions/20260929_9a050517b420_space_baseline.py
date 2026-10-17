"""space: baseline

Everything a space database holds, from an empty database. Every later revision adds to it.

- `hash_modulus` (config attribute, 8 by default, 32 for a large space) sets the hash partitions of the
  memory tables; nothing else may assume the count (CODE_GUIDE.md, the modulus rule). The dedupe tables
  always get eight.
- Row-level security stays on every table with a space column (`PINNED_TABLES`), including `outbox` and
  `scheduled_tasks`: the database holds one space, and a stray row of another one stays invisible.
- `ensure_week_partition` and `create_hash_partitions` run with their owner's rights (SECURITY DEFINER):
  creating a partition requires owning the parent, and partition upkeep runs as `niadra_system`.

Revision ID: 9a050517b420
Revises:
Create Date: 2026-09-29 21:34:51.170813
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "9a050517b420"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

HASH_PARTITIONED = (
    "facts",
    "fact_mentions",
    "fact_relations",
    "episodes",
    "open_items",
    "profile_attrs",
    "settled_values",
    "subject_signals",
    "beneficiaries",
)
DEDUPE_TABLES = ("event_keys_0", "event_keys_1", "event_keys_2")
DEFAULT_PARTITIONED = ("events", "event_handles", "receipts")
# Rows the change feeds read again when they change (`change_seq`); append-only tables need no trigger.
CHANGE_SEQ_TABLES = (
    "events",
    "conversations",
    "sessions",
    "profiles",
    "handles",
    "profile_membership",
    "assertions",
    "merge_log",
    "profile_links",
    "facts",
    "episodes",
    "open_items",
    "profile_attrs",
    "business_objects",
    "agent_actions",
    "traits",
    "context_use",
    "trigger_firings",
    "agent_notes",
    "shared_objects",
    "object_values",
    "subject_signals",
    "beneficiaries",
    "suppressions",
    "org_parents",
)
PINNED_TABLES = (
    "agent_actions",
    "agent_watermarks",
    "assertions",
    "assist_runs",
    "audit_roots",
    "blocked_values",
    "business_objects",
    "context_use",
    "context_use_daily",
    "conversation_pins",
    "conversations",
    "decisions",
    "episodes",
    "erasure_requests",
    "event_handles",
    "event_keys_0",
    "event_keys_1",
    "event_keys_2",
    "events",
    "export_runs",
    "extraction_runs",
    "fact_mentions",
    "fact_relations",
    "facts",
    "handles",
    "identity_suggestions",
    "ingest_imports",
    "llm_spend",
    "measured_sessions",
    "merge_log",
    "object_events",
    "object_participants",
    "open_items",
    "outbox",
    "pack_manifests",
    "privacy_feed",
    "profile_attrs",
    "profile_links",
    "profile_membership",
    "profile_work",
    "profiles",
    "queue_leases",
    "receipt_heads",
    "receipts",
    "request_idempotency",
    "review_samples",
    "scheduled_tasks",
    "schedules",
    "sessions",
    "source_heartbeats",
    "tail_summaries",
    "tenant_rollups",
    "tombstones",
    "trait_evidence",
    "trait_opt_outs",
    "traits",
    "trigger_firings",
    "usage_events",
    "usage_hourly",
    "webhook_deliveries",
    "context_trims",
    "context_use_units",
    "model_usage_daily",
    "agent_notes",
    "agent_note_proposals",
    "experiment_assignments",
    "settled_values",
    "context_use_slot_daily",
    "retrieval_weights",
    "context_use_guard_daily",
    "purpose_objections",
    "knowledge_gaps",
    "insight_reviews",
    "context_use_view_daily",
    "view_budgets",
    "open_item_feedback_events",
    "session_ends",
    "ingest_intake",
    "read_invalidations",
    "pack_bundles",
    "turn_intake",
    "turns_kept",
    "turn_daily",
    "agent_builds",
    "scenarios",
    "scenario_runs",
    "data_issues",
    "config_history",
    "notification_feed",
    "turn_tombstones",
    "shared_objects",
    "object_values",
    "object_dependencies",
    "object_timers",
    "object_watches",
    "refresh_requests",
    "content_scans",
    "agent_state",
    "subject_signals",
    "beneficiaries",
    "signal_intake",
    "signal_priors",
    "inference_suppressions",
    "measure_segments",
    "ownership_claims",
    "effects",
    "contact_log",
    "budget_spends",
    "suppressions",
    "commitments",
    "handoffs",
    "coord_reports",
    "outcome_links",
    "attribution_daily",
    "interleave_daily",
    "counterfactual_runs",
    "measurement_reports",
    "review_requests",
    "legal_holds",
    "org_parents",
    "receipt_people",
)
# Hot rows updated in place: room on the page for HOT updates, and vacuum before the bloat builds.
CHURNING_TABLES = ("sessions", "queue_leases", "profile_work", "ingest_intake", "pack_bundles")
VACUUMED_TABLES = ("scheduled_tasks", "read_invalidations")


def upgrade() -> None:
    modulus = int(op.get_context().config.attributes.get("hash_modulus", 8))
    op.execute("CREATE SEQUENCE change_seq AS bigint CACHE 1")
    op.execute(FUNCTIONS)
    op.create_table(
        "agent_actions",
        sa.Column("space_id", sa.UUID(), nullable=False),
        sa.Column("action_id", sa.UUID(), nullable=False),
        sa.Column("event_id", sa.UUID(), nullable=False),
        sa.Column("source_id", sa.UUID(), nullable=False),
        sa.Column("purpose", sa.Text(), nullable=True),
        sa.Column("object_id", sa.UUID(), nullable=True),
        sa.Column("operation", sa.Text(), nullable=False),
        sa.Column("result_ct", sa.LargeBinary(), nullable=True),
        sa.Column("closes_handle_id", sa.UUID(), nullable=True),
        sa.Column("closes_item_id", sa.UUID(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("confirmed_by_event_id", sa.UUID(), nullable=True),
        sa.Column("corrects_action_id", sa.UUID(), nullable=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("dek_version", sa.Integer(), nullable=False),
        sa.Column(
            "change_seq", sa.BigInteger(), server_default=sa.text("nextval('change_seq')"), nullable=False
        ),
        sa.Column("embedding_ct", sa.LargeBinary(), nullable=True),
        sa.Column("embedding_model", sa.Text(), nullable=True),
        sa.Column("embedding_dim", sa.Integer(), nullable=True),
        sa.Column("embedding_dek_version", sa.Integer(), nullable=True),
        sa.CheckConstraint(
            "status IN ('declared', 'confirmed', 'divergent', 'quarantined')",
            name=op.f("ck_agent_actions_status"),
        ),
        sa.PrimaryKeyConstraint("space_id", "action_id", name=op.f("pk_agent_actions")),
    )
    op.create_index(
        "ix_agent_actions_declared",
        "agent_actions",
        ["space_id", "occurred_at"],
        unique=False,
        postgresql_where=sa.text("status = 'declared'"),
    )
    op.create_index(
        op.f("ix_agent_actions_space_id_change_seq"),
        "agent_actions",
        ["space_id", "change_seq"],
        unique=False,
    )
    op.create_index(
        op.f("ix_agent_actions_space_id_confirmed_by_event_id"),
        "agent_actions",
        ["space_id", "confirmed_by_event_id"],
        unique=False,
        postgresql_where=sa.text("confirmed_by_event_id IS NOT NULL"),
    )
    op.create_index(
        op.f("ix_agent_actions_space_id_event_id"), "agent_actions", ["space_id", "event_id"], unique=False
    )
    op.create_index(
        op.f("ix_agent_actions_space_id_object_id_operation_occurred_at"),
        "agent_actions",
        ["space_id", "object_id", "operation", "occurred_at"],
        unique=False,
    )
    op.create_index(
        "ix_agent_actions_closes_handle",
        "agent_actions",
        ["space_id", "closes_handle_id"],
        unique=False,
        postgresql_where=sa.text("closes_handle_id IS NOT NULL"),
    )
    op.create_table(
        "agent_builds",
        sa.Column("space_id", sa.UUID(), nullable=False),
        sa.Column("build_hash", sa.LargeBinary(), nullable=False),
        sa.Column("source_id", sa.UUID(), nullable=False),
        sa.Column("agent", sa.Text(), nullable=False),
        sa.Column("pins", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("sdk", sa.Text(), nullable=True),
        sa.Column("adapter", sa.Text(), nullable=True),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("space_id", "build_hash", name=op.f("pk_agent_builds")),
    )
    op.create_index(
        op.f("ix_agent_builds_space_id_source_id_agent_first_seen_at"),
        "agent_builds",
        ["space_id", "source_id", "agent", "first_seen_at"],
        unique=False,
    )
    op.create_table(
        "agent_note_proposals",
        sa.Column("space_id", sa.UUID(), nullable=False),
        sa.Column("proposal_id", sa.UUID(), nullable=False),
        sa.Column("source_id", sa.UUID(), nullable=False),
        sa.Column("origin", sa.Text(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("title_ct", sa.LargeBinary(), nullable=False),
        sa.Column("body_ct", sa.LargeBinary(), nullable=False),
        sa.Column("dek_version", sa.Integer(), nullable=False),
        sa.Column("tags", postgresql.ARRAY(sa.Text()), nullable=False),
        sa.Column("visibility", sa.Text(), nullable=False),
        sa.Column("valid_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("evidence_conversation_id", sa.Text(), nullable=True),
        sa.Column("evidence_task_id", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by", sa.Text(), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decided_by", sa.Text(), nullable=True),
        sa.Column("note_id", sa.UUID(), nullable=True),
        sa.Column("problem", sa.Text(), nullable=True),
        sa.CheckConstraint("origin IN ('agent', 'distilled')", name=op.f("ck_agent_note_proposals_origin")),
        sa.CheckConstraint(
            "status IN ('drafting', 'pending', 'approved', 'rejected', 'failed')",
            name=op.f("ck_agent_note_proposals_status"),
        ),
        sa.PrimaryKeyConstraint("space_id", "proposal_id", name=op.f("pk_agent_note_proposals")),
    )
    op.create_index(
        op.f("ix_agent_note_proposals_space_id_source_id_status"),
        "agent_note_proposals",
        ["space_id", "source_id", "status"],
        unique=False,
    )
    op.create_table(
        "agent_notes",
        sa.Column("space_id", sa.UUID(), nullable=False),
        sa.Column("note_id", sa.UUID(), nullable=False),
        sa.Column("lineage_id", sa.UUID(), nullable=False),
        sa.Column("source_id", sa.UUID(), nullable=False),
        sa.Column("visibility", sa.Text(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("title_ct", sa.LargeBinary(), nullable=False),
        sa.Column("body_ct", sa.LargeBinary(), nullable=False),
        sa.Column("dek_version", sa.Integer(), nullable=False),
        sa.Column("tags", postgresql.ARRAY(sa.Text()), nullable=False),
        sa.Column("origin", sa.Text(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("supersedes_note_id", sa.UUID(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("valid_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("evidence_conversation_id", sa.Text(), nullable=True),
        sa.Column("evidence_task_id", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by", sa.Text(), nullable=False),
        sa.Column("retired_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "change_seq", sa.BigInteger(), server_default=sa.text("nextval('change_seq')"), nullable=False
        ),
        sa.CheckConstraint(
            "kind IN ('procedure', 'tool_note', 'process_note', 'pitfall')", name=op.f("ck_agent_notes_kind")
        ),
        sa.CheckConstraint("origin IN ('agent', 'human', 'distilled')", name=op.f("ck_agent_notes_origin")),
        sa.CheckConstraint("status IN ('active', 'retired')", name=op.f("ck_agent_notes_status")),
        sa.CheckConstraint(
            "visibility IN ('source', 'vendor', 'space')", name=op.f("ck_agent_notes_visibility")
        ),
        sa.PrimaryKeyConstraint("space_id", "note_id", name=op.f("pk_agent_notes")),
    )
    op.create_index(
        op.f("ix_agent_notes_space_id_change_seq"), "agent_notes", ["space_id", "change_seq"], unique=False
    )
    op.create_index(
        op.f("ix_agent_notes_space_id_lineage_id"), "agent_notes", ["space_id", "lineage_id"], unique=False
    )
    op.create_index(
        op.f("ix_agent_notes_space_id_source_id_status"),
        "agent_notes",
        ["space_id", "source_id", "status"],
        unique=False,
    )
    op.create_table(
        "agent_state",
        sa.Column("space_id", sa.UUID(), nullable=False),
        sa.Column("scope_key", sa.LargeBinary(), nullable=False),
        sa.Column("source_id", sa.UUID(), nullable=False),
        sa.Column("agent", sa.Text(), nullable=False),
        sa.Column("scope_kind", sa.Text(), nullable=False),
        sa.Column("body_ct", sa.LargeBinary(), nullable=False),
        sa.Column("dek_version", sa.Integer(), nullable=False),
        sa.Column("body_bytes", sa.Integer(), nullable=False),
        sa.Column("schema_version", sa.Text(), nullable=True),
        sa.Column("version", sa.BigInteger(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("valid_until", sa.DateTime(timezone=True), nullable=False),
        sa.Column("origin_handle_id", sa.UUID(), nullable=True),
        sa.Column("subject_key", sa.LargeBinary(), nullable=True),
        sa.CheckConstraint(
            "scope_kind IN ('conversation', 'task', 'object', 'subject')",
            name=op.f("ck_agent_state_scope_kind"),
        ),
        sa.PrimaryKeyConstraint("space_id", "scope_key", "source_id", "agent", name=op.f("pk_agent_state")),
    )
    op.create_index(
        op.f("ix_agent_state_space_id_origin_handle_id"),
        "agent_state",
        ["space_id", "origin_handle_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_agent_state_space_id_subject_key"), "agent_state", ["space_id", "subject_key"], unique=False
    )
    op.create_index(
        op.f("ix_agent_state_space_id_valid_until"), "agent_state", ["space_id", "valid_until"], unique=False
    )
    op.create_table(
        "agent_watermarks",
        sa.Column("space_id", sa.UUID(), nullable=False),
        sa.Column("source_id", sa.UUID(), nullable=False),
        sa.Column("profile_id", sa.UUID(), nullable=False),
        sa.Column("thread", sa.Text(), server_default="", nullable=False),
        sa.Column("manifest_hash", sa.Text(), nullable=False),
        sa.Column("seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("generation", sa.BigInteger(), server_default=sa.text("0"), nullable=False),
        sa.PrimaryKeyConstraint(
            "space_id", "source_id", "profile_id", "thread", name=op.f("pk_agent_watermarks")
        ),
    )
    op.create_table(
        "assertions",
        sa.Column("space_id", sa.UUID(), nullable=False),
        sa.Column("assertion_id", sa.UUID(), nullable=False),
        sa.Column("handle_a", sa.UUID(), nullable=False),
        sa.Column("handle_b", sa.UUID(), nullable=False),
        sa.Column("method", sa.Text(), nullable=False),
        sa.Column("origin_event_id", sa.UUID(), nullable=True),
        sa.Column("confidence", sa.REAL(), nullable=False),
        sa.Column("level_at_capture", sa.Text(), nullable=False),
        sa.Column("state", sa.Text(), server_default="applied", nullable=False),
        sa.Column("actor", sa.Text(), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("retracted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "change_seq", sa.BigInteger(), server_default=sa.text("nextval('change_seq')"), nullable=False
        ),
        sa.CheckConstraint("state IN ('applied', 'pending', 'rejected')", name=op.f("ck_assertions_state")),
        sa.PrimaryKeyConstraint("space_id", "assertion_id", name=op.f("pk_assertions")),
    )
    op.create_index("ix_assertions_all_handle_a", "assertions", ["space_id", "handle_a"], unique=False)
    op.create_index("ix_assertions_all_handle_b", "assertions", ["space_id", "handle_b"], unique=False)
    op.create_index(
        op.f("ix_assertions_space_id_handle_a"),
        "assertions",
        ["space_id", "handle_a"],
        unique=False,
        postgresql_where=sa.text("retracted_at IS NULL"),
    )
    op.create_index(
        op.f("ix_assertions_space_id_handle_b"),
        "assertions",
        ["space_id", "handle_b"],
        unique=False,
        postgresql_where=sa.text("retracted_at IS NULL"),
    )
    op.create_table(
        "assist_runs",
        sa.Column("space_id", sa.UUID(), nullable=False),
        sa.Column("run_id", sa.UUID(), nullable=False),
        sa.Column("task", sa.Text(), nullable=False),
        sa.Column("sample_ids", postgresql.ARRAY(sa.Text()), nullable=False),
        sa.Column("diff_hash", sa.Text(), nullable=True),
        sa.Column("proposal", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("requested_by", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("space_id", "run_id", name=op.f("pk_assist_runs")),
    )
    op.create_table(
        "attribution_daily",
        sa.Column("space_id", sa.UUID(), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("outcome", sa.Text(), nullable=False),
        sa.Column("agent", sa.Text(), nullable=False),
        sa.Column("method", sa.Text(), nullable=False),
        sa.Column("band", sa.Text(), nullable=False),
        sa.Column("finality", sa.Text(), nullable=False),
        sa.Column("currency", sa.Text(), nullable=False),
        sa.Column("outcomes", sa.Integer(), nullable=False),
        sa.Column("lines", sa.Integer(), nullable=False),
        sa.Column("value_minor", sa.BigInteger(), nullable=False),
        sa.PrimaryKeyConstraint(
            "space_id",
            "day",
            "outcome",
            "agent",
            "method",
            "band",
            "finality",
            "currency",
            name=op.f("pk_attribution_daily"),
        ),
    )
    op.create_table(
        "audit_roots",
        sa.Column("space_id", sa.UUID(), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("root", sa.LargeBinary(), nullable=False),
        sa.Column("location", sa.Text(), nullable=False),
        sa.Column("anchored_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("space_id", "day", name=op.f("pk_audit_roots")),
    )
    op.create_table(
        "beneficiaries",
        sa.Column("space_id", sa.UUID(), nullable=False),
        sa.Column("origin_handle_id", sa.UUID(), nullable=False),
        sa.Column("beneficiary_id", sa.UUID(), nullable=False),
        sa.Column("relation", sa.Text(), nullable=False),
        sa.Column("label_ct", sa.LargeBinary(), nullable=True),
        sa.Column("dek_version", sa.Integer(), nullable=True),
        sa.Column("status", sa.Text(), server_default="active", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "change_seq", sa.BigInteger(), server_default=sa.text("nextval('change_seq')"), nullable=False
        ),
        sa.CheckConstraint(
            "relation IN ('dependent', 'spouse', 'child', 'parent', 'relative', 'friend', 'gift', 'other')",
            name=op.f("ck_beneficiaries_relation"),
        ),
        sa.CheckConstraint("status IN ('active', 'discarded')", name=op.f("ck_beneficiaries_status")),
        sa.CheckConstraint(
            "(label_ct IS NULL) = (dek_version IS NULL)", name=op.f("ck_beneficiaries_label_sealed")
        ),
        sa.PrimaryKeyConstraint(
            "space_id", "origin_handle_id", "beneficiary_id", name=op.f("pk_beneficiaries")
        ),
        postgresql_partition_by="HASH (origin_handle_id)",
    )
    op.create_index(
        op.f("ix_beneficiaries_space_id_change_seq"),
        "beneficiaries",
        ["space_id", "change_seq"],
        unique=False,
    )
    op.create_table(
        "blocked_values",
        sa.Column("space_id", sa.UUID(), nullable=False),
        sa.Column("type", sa.Text(), nullable=False),
        sa.Column("value_hmac", sa.LargeBinary(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("space_id", "type", "value_hmac", name=op.f("pk_blocked_values")),
    )
    op.create_table(
        "budget_spends",
        sa.Column("space_id", sa.UUID(), nullable=False),
        sa.Column("spend_id", sa.UUID(), nullable=False),
        sa.Column("budget", sa.Text(), nullable=False),
        sa.Column("slice", sa.Text(), nullable=True),
        sa.Column("target_kind", sa.Text(), nullable=False),
        sa.Column("target", sa.LargeBinary(), nullable=False),
        sa.Column("units", sa.Integer(), nullable=False),
        sa.Column("settled", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("spent_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("until", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "target_kind IN ('subject', 'object', 'space')", name=op.f("ck_budget_spends_target_kind")
        ),
        sa.PrimaryKeyConstraint("space_id", "spend_id", name=op.f("pk_budget_spends")),
    )
    op.create_index(
        op.f("ix_budget_spends_space_id_target_kind_target_until"),
        "budget_spends",
        ["space_id", "target_kind", "target", "until"],
        unique=False,
    )
    op.create_index(
        op.f("ix_budget_spends_space_id_until"), "budget_spends", ["space_id", "until"], unique=False
    )
    op.create_table(
        "business_objects",
        sa.Column("space_id", sa.UUID(), nullable=False),
        sa.Column("object_id", sa.UUID(), nullable=False),
        sa.Column("type", sa.Text(), nullable=False),
        sa.Column("namespace", sa.Text(), nullable=False),
        sa.Column("external_id", sa.Text(), nullable=False),
        sa.Column("origin_handle_id", sa.UUID(), nullable=True),
        sa.Column("state_version", sa.Text(), nullable=True),
        sa.Column("as_of", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source_id", sa.UUID(), nullable=False),
        sa.Column("record_ref", sa.Text(), nullable=True),
        sa.Column(
            "change_seq", sa.BigInteger(), server_default=sa.text("nextval('change_seq')"), nullable=False
        ),
        sa.Column("state_ct", sa.LargeBinary(), nullable=True),
        sa.Column("dek_version", sa.Integer(), nullable=True),
        sa.Column("field_meta", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("inputs_hash", sa.LargeBinary(), nullable=True),
        sa.Column("derived_status", sa.Text(), nullable=True),
        sa.Column("embedding_ct", sa.LargeBinary(), nullable=True),
        sa.Column("embedding_model", sa.Text(), nullable=True),
        sa.Column("embedding_dim", sa.Integer(), nullable=True),
        sa.Column("embedding_dek_version", sa.Integer(), nullable=True),
        sa.Column("match_keys", postgresql.ARRAY(sa.Text()), nullable=True),
        sa.PrimaryKeyConstraint("space_id", "object_id", name=op.f("pk_business_objects")),
        sa.UniqueConstraint(
            "space_id",
            "namespace",
            "type",
            "external_id",
            name=op.f("uq_business_objects_space_id_namespace_type_external_id"),
        ),
    )
    op.create_index(
        op.f("ix_business_objects_space_id_change_seq"),
        "business_objects",
        ["space_id", "change_seq"],
        unique=False,
    )
    op.create_index(
        op.f("ix_business_objects_match_keys"),
        "business_objects",
        ["match_keys"],
        unique=False,
        postgresql_using="gin",
    )
    op.create_index(
        "ix_business_objects_origin_handle",
        "business_objects",
        ["space_id", "origin_handle_id"],
        unique=False,
        postgresql_where=sa.text("origin_handle_id IS NOT NULL"),
    )
    op.create_table(
        "commitments",
        sa.Column("space_id", sa.UUID(), nullable=False),
        sa.Column("commitment_id", sa.UUID(), nullable=False),
        sa.Column("external_id", sa.Text(), nullable=False),
        sa.Column("origin_handle_id", sa.UUID(), nullable=True),
        sa.Column("profile_id", sa.UUID(), nullable=True),
        sa.Column("subject_hmac", sa.LargeBinary(), nullable=True),
        sa.Column("object_type", sa.Text(), nullable=True),
        sa.Column("object_namespace", sa.Text(), nullable=True),
        sa.Column("object_external_id", sa.Text(), nullable=True),
        sa.Column("type", sa.Text(), nullable=False),
        sa.Column("compatibility", sa.Text(), nullable=False),
        sa.Column("terms_ct", sa.LargeBinary(), nullable=False),
        sa.Column("dek_version", sa.Integer(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("score", sa.REAL(), nullable=True),
        sa.Column("source_id", sa.UUID(), nullable=False),
        sa.Column("agent", sa.Text(), nullable=True),
        sa.Column("action_id", sa.UUID(), nullable=True),
        sa.Column("made_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("valid_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "compatibility IN ('first_holds', 'best_wins', 'exclusive')",
            name=op.f("ck_commitments_compatibility"),
        ),
        sa.CheckConstraint(
            "status IN ('active', 'superseded', 'accepted', 'declined', 'withdrawn', 'expired')",
            name=op.f("ck_commitments_status"),
        ),
        sa.PrimaryKeyConstraint("space_id", "commitment_id", name=op.f("pk_commitments")),
    )
    op.create_index(
        op.f("ix_commitments_space_id_origin_handle_id_status"),
        "commitments",
        ["space_id", "origin_handle_id", "status"],
        unique=False,
    )
    op.create_index(
        op.f("ix_commitments_space_id_subject_hmac_status"),
        "commitments",
        ["space_id", "subject_hmac", "status"],
        unique=False,
    )
    op.create_table(
        "config_history",
        sa.Column("space_id", sa.UUID(), nullable=False),
        sa.Column("section", sa.Text(), nullable=False),
        sa.Column("version", sa.BigInteger(), nullable=False),
        sa.Column("applied_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("digest", sa.LargeBinary(), nullable=False),
        sa.PrimaryKeyConstraint("space_id", "section", "version", name=op.f("pk_config_history")),
    )
    op.create_index(
        op.f("ix_config_history_space_id_applied_at"),
        "config_history",
        ["space_id", "applied_at"],
        unique=False,
    )
    op.create_table(
        "contact_log",
        sa.Column("space_id", sa.UUID(), nullable=False),
        sa.Column("contact_id", sa.UUID(), nullable=False),
        sa.Column("origin_handle_id", sa.UUID(), nullable=True),
        sa.Column("subject_hmac", sa.LargeBinary(), nullable=True),
        sa.Column("purpose", sa.Text(), nullable=False),
        sa.Column("channel", sa.Text(), nullable=False),
        sa.Column("direction", sa.Text(), nullable=False),
        sa.Column("source_id", sa.UUID(), nullable=False),
        sa.Column("agent", sa.Text(), nullable=True),
        sa.Column("gateway", sa.Text(), nullable=True),
        sa.Column("decision_id", sa.UUID(), nullable=True),
        sa.Column("state", sa.Text(), nullable=False),
        sa.Column("unchecked", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("uses", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("use_ids", postgresql.ARRAY(sa.UUID()), server_default=sa.text("'{}'"), nullable=False),
        sa.Column("reserved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("direction IN ('inbound', 'outbound')", name=op.f("ck_contact_log_direction")),
        sa.CheckConstraint("state IN ('reserved', 'made', 'expired')", name=op.f("ck_contact_log_state")),
        sa.PrimaryKeyConstraint("space_id", "contact_id", name=op.f("pk_contact_log")),
        postgresql_partition_by="RANGE (contact_id)",
    )
    op.create_index(
        op.f("ix_contact_log_space_id_origin_handle_id"),
        "contact_log",
        ["space_id", "origin_handle_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_contact_log_space_id_subject_hmac"),
        "contact_log",
        ["space_id", "subject_hmac"],
        unique=False,
    )
    op.create_table(
        "content_scans",
        sa.Column("space_id", sa.UUID(), nullable=False),
        sa.Column("content_ref", sa.Text(), nullable=False),
        sa.Column("origin_handle_id", sa.UUID(), nullable=True),
        sa.Column("state", sa.Text(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("decided_by", sa.Text(), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("state IN ('pending', 'clean', 'flagged')", name=op.f("ck_content_scans_state")),
        sa.PrimaryKeyConstraint("space_id", "content_ref", name=op.f("pk_content_scans")),
    )
    op.create_index(
        op.f("ix_content_scans_space_id_origin_handle_id"),
        "content_scans",
        ["space_id", "origin_handle_id"],
        unique=False,
    )
    op.create_table(
        "context_trims",
        sa.Column("space_id", sa.UUID(), nullable=False),
        sa.Column("source_id", sa.UUID(), nullable=False),
        sa.Column("view", sa.Text(), nullable=False),
        sa.Column("units", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("derived_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("window_days", sa.Integer(), nullable=False),
        sa.Column("min_deliveries", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("space_id", "source_id", "view", name=op.f("pk_context_trims")),
    )
    op.create_table(
        "context_use",
        sa.Column("space_id", sa.UUID(), nullable=False),
        sa.Column("conversation_key", sa.Text(), nullable=False),
        sa.Column("manifest_hash", sa.Text(), nullable=False),
        sa.Column("source_id", sa.UUID(), nullable=False),
        sa.Column("measure_version", sa.Text(), nullable=False),
        sa.Column("session_id", sa.UUID(), nullable=True),
        sa.Column("profile_id", sa.UUID(), nullable=False),
        sa.Column("channel", sa.Text(), nullable=False),
        sa.Column("view", sa.Text(), nullable=False),
        sa.Column("experiment_group", sa.Text(), nullable=True),
        sa.Column("items", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("questions", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("repeated", sa.Integer(), nullable=False),
        sa.Column("used", sa.Integer(), nullable=False),
        sa.Column("contradicted", sa.Integer(), nullable=False),
        sa.Column("late", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("transfer_unread", sa.Boolean(), nullable=True),
        sa.Column("not_measured_reason", sa.Text(), nullable=True),
        sa.Column("measured_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "change_seq", sa.BigInteger(), server_default=sa.text("nextval('change_seq')"), nullable=False
        ),
        sa.Column("tokens_saved", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("slots", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("object_rows", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("object_rows_used", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("instruction_violations", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.PrimaryKeyConstraint(
            "space_id",
            "conversation_key",
            "manifest_hash",
            "source_id",
            "measure_version",
            name=op.f("pk_context_use"),
        ),
    )
    op.create_index(
        op.f("ix_context_use_space_id_change_seq"), "context_use", ["space_id", "change_seq"], unique=False
    )
    op.create_index(
        "ix_context_use_space_id_profile_id", "context_use", ["space_id", "profile_id"], unique=False
    )
    op.create_index(
        "ix_context_use_space_id_session_id", "context_use", ["space_id", "session_id"], unique=False
    )
    op.create_table(
        "context_use_daily",
        sa.Column("space_id", sa.UUID(), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("source_id", sa.UUID(), nullable=False),
        sa.Column("channel", sa.Text(), nullable=False),
        sa.Column("view", sa.Text(), nullable=False),
        sa.Column("experiment_group", sa.Text(), server_default="", nullable=False),
        sa.Column("deliveries", sa.BigInteger(), nullable=False),
        sa.Column("deliveries_used", sa.BigInteger(), nullable=False),
        sa.Column("questions", sa.BigInteger(), nullable=False),
        sa.Column("repeated", sa.BigInteger(), nullable=False),
        sa.Column("contradicted", sa.BigInteger(), nullable=False),
        sa.Column("transfers", sa.BigInteger(), nullable=False),
        sa.Column("transfers_unread", sa.BigInteger(), nullable=False),
        sa.Column("not_measured", sa.BigInteger(), nullable=False),
        sa.Column("recontacts", sa.BigInteger(), nullable=False),
        sa.Column("sessions", sa.BigInteger(), nullable=False),
        sa.Column("late_deliveries", sa.BigInteger(), server_default=sa.text("0"), nullable=False),
        sa.Column("no_context", sa.BigInteger(), server_default=sa.text("0"), nullable=False),
        sa.Column("selection_misses", sa.BigInteger(), server_default=sa.text("0"), nullable=False),
        sa.Column("trimmed_deliveries", sa.BigInteger(), server_default=sa.text("0"), nullable=False),
        sa.Column("tokens_saved", sa.BigInteger(), server_default=sa.text("0"), nullable=False),
        sa.Column("answers_checked", sa.BigInteger(), server_default=sa.text("0"), nullable=False),
        sa.Column("unbacked_values", sa.BigInteger(), server_default=sa.text("0"), nullable=False),
        sa.Column("guard_violations", sa.BigInteger(), server_default=sa.text("0"), nullable=False),
        sa.Column("object_rows", sa.BigInteger(), server_default=sa.text("0"), nullable=False),
        sa.Column("object_rows_used", sa.BigInteger(), server_default=sa.text("0"), nullable=False),
        sa.Column("instruction_violations", sa.BigInteger(), server_default=sa.text("0"), nullable=False),
        sa.PrimaryKeyConstraint(
            "space_id",
            "day",
            "source_id",
            "channel",
            "view",
            "experiment_group",
            name=op.f("pk_context_use_daily"),
        ),
    )
    op.create_table(
        "context_use_guard_daily",
        sa.Column("space_id", sa.UUID(), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("source_id", sa.UUID(), nullable=False),
        sa.Column("value_type", sa.Text(), nullable=False),
        sa.Column("arm", sa.Text(), nullable=False),
        sa.Column("slots", sa.BigInteger(), server_default=sa.text("0"), nullable=False),
        sa.Column("contradicted", sa.BigInteger(), server_default=sa.text("0"), nullable=False),
        sa.PrimaryKeyConstraint(
            "space_id", "day", "source_id", "value_type", "arm", name=op.f("pk_context_use_guard_daily")
        ),
    )
    op.create_table(
        "context_use_slot_daily",
        sa.Column("space_id", sa.UUID(), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("source_id", sa.UUID(), nullable=False),
        sa.Column("channel", sa.Text(), nullable=False),
        sa.Column("view", sa.Text(), nullable=False),
        sa.Column("experiment_group", sa.Text(), server_default="", nullable=False),
        sa.Column("slot_channel", sa.Text(), nullable=False),
        sa.Column("turns", sa.BigInteger(), server_default=sa.text("0"), nullable=False),
        sa.Column("skipped", sa.BigInteger(), server_default=sa.text("0"), nullable=False),
        sa.Column("slots", sa.BigInteger(), server_default=sa.text("0"), nullable=False),
        sa.Column("used", sa.BigInteger(), server_default=sa.text("0"), nullable=False),
        sa.Column("repeated", sa.BigInteger(), server_default=sa.text("0"), nullable=False),
        sa.Column("contradicted", sa.BigInteger(), server_default=sa.text("0"), nullable=False),
        sa.PrimaryKeyConstraint(
            "space_id",
            "day",
            "source_id",
            "channel",
            "view",
            "experiment_group",
            "slot_channel",
            name=op.f("pk_context_use_slot_daily"),
        ),
    )
    op.create_table(
        "context_use_units",
        sa.Column("space_id", sa.UUID(), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("source_id", sa.UUID(), nullable=False),
        sa.Column("view", sa.Text(), nullable=False),
        sa.Column("unit", sa.Text(), nullable=False),
        sa.Column("deliveries", sa.BigInteger(), server_default=sa.text("0"), nullable=False),
        sa.Column("used", sa.BigInteger(), server_default=sa.text("0"), nullable=False),
        sa.Column("missed", sa.BigInteger(), server_default=sa.text("0"), nullable=False),
        sa.PrimaryKeyConstraint(
            "space_id", "day", "source_id", "view", "unit", name=op.f("pk_context_use_units")
        ),
    )
    op.create_table(
        "context_use_view_daily",
        sa.Column("space_id", sa.UUID(), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("source_id", sa.UUID(), nullable=False),
        sa.Column("target", sa.Text(), nullable=False),
        sa.Column("view", sa.Text(), nullable=False),
        sa.Column("arm", sa.Text(), nullable=False),
        sa.Column("section", sa.Text(), nullable=False),
        sa.Column("deliveries", sa.BigInteger(), server_default=sa.text("0"), nullable=False),
        sa.Column("used", sa.BigInteger(), server_default=sa.text("0"), nullable=False),
        sa.Column("missed", sa.BigInteger(), server_default=sa.text("0"), nullable=False),
        sa.Column("questions", sa.BigInteger(), server_default=sa.text("0"), nullable=False),
        sa.Column("repeated", sa.BigInteger(), server_default=sa.text("0"), nullable=False),
        sa.PrimaryKeyConstraint(
            "space_id",
            "day",
            "source_id",
            "target",
            "view",
            "arm",
            "section",
            name=op.f("pk_context_use_view_daily"),
        ),
    )
    op.create_table(
        "conversation_pins",
        sa.Column("space_id", sa.UUID(), nullable=False),
        sa.Column("conversation_id", sa.Text(), nullable=False),
        sa.Column("view", sa.Text(), nullable=False),
        sa.Column("pack_key", sa.Text(), nullable=False),
        sa.Column("pack_hash", sa.Text(), nullable=False),
        sa.Column("generation", sa.BigInteger(), nullable=False),
        sa.Column("pinned_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("space_id", "conversation_id", "view", name=op.f("pk_conversation_pins")),
    )
    op.create_table(
        "conversations",
        sa.Column("space_id", sa.UUID(), nullable=False),
        sa.Column("conversation_id", sa.Text(), nullable=False),
        sa.Column("aliases", postgresql.ARRAY(sa.Text()), server_default=sa.text("'{}'"), nullable=False),
        sa.Column("channel", sa.Text(), nullable=False),
        sa.Column("primary_handle", sa.UUID(), nullable=True),
        sa.Column("first_event_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_event_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("dirty", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column(
            "change_seq", sa.BigInteger(), server_default=sa.text("nextval('change_seq')"), nullable=False
        ),
        sa.PrimaryKeyConstraint("space_id", "conversation_id", name=op.f("pk_conversations")),
    )
    op.create_index(
        op.f("ix_conversations_aliases"), "conversations", ["aliases"], unique=False, postgresql_using="gin"
    )
    op.create_index(
        op.f("ix_conversations_space_id_primary_handle_last_event_at"),
        "conversations",
        ["space_id", "primary_handle", "last_event_at"],
        unique=False,
    )
    op.create_table(
        "coord_reports",
        sa.Column("space_id", sa.UUID(), nullable=False),
        sa.Column("report_id", sa.UUID(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("days", sa.Integer(), nullable=False),
        sa.Column(
            "requested_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "summary",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("sample_ct", sa.LargeBinary(), nullable=True),
        sa.Column("dek_version", sa.Integer(), nullable=True),
        sa.CheckConstraint("status IN ('pending', 'done', 'failed')", name=op.f("ck_coord_reports_status")),
        sa.PrimaryKeyConstraint("space_id", "report_id", name=op.f("pk_coord_reports")),
    )
    op.create_index(
        op.f("ix_coord_reports_space_id_requested_at"),
        "coord_reports",
        ["space_id", "requested_at"],
        unique=False,
    )
    op.create_table(
        "counterfactual_runs",
        sa.Column("space_id", sa.UUID(), nullable=False),
        sa.Column("run_id", sa.UUID(), nullable=False),
        sa.Column("tool", sa.Text(), nullable=False),
        sa.Column("element", sa.Text(), nullable=False),
        sa.Column("cases", sa.Integer(), nullable=False),
        sa.Column("overlap_at_k", sa.REAL(), nullable=True),
        sa.Column("noise_floor", sa.REAL(), nullable=True),
        sa.Column("limits", postgresql.ARRAY(sa.Text()), server_default=sa.text("'{}'"), nullable=False),
        sa.Column(
            "detail", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'"), nullable=False
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("space_id", "run_id", name=op.f("pk_counterfactual_runs")),
    )
    op.create_index(
        op.f("ix_counterfactual_runs_space_id_tool_created_at"),
        "counterfactual_runs",
        ["space_id", "tool", "created_at"],
        unique=False,
    )
    op.create_table(
        "data_issues",
        sa.Column("space_id", sa.UUID(), nullable=False),
        sa.Column("issue_id", sa.UUID(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("fingerprint", sa.LargeBinary(), nullable=False),
        sa.Column("object_type", sa.Text(), nullable=True),
        sa.Column("field", sa.Text(), nullable=True),
        sa.Column("source", sa.Text(), nullable=True),
        sa.Column(
            "refs",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("occurrences", sa.BigInteger(), server_default=sa.text("1"), nullable=False),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("acknowledged_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("acknowledged_by", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "kind IN ('null_field', 'out_of_vocabulary', 'stale_source', 'coverage_drop', 'invalid_value', 'divergence', 'drift', 'rule_conflict', 'type_undeclared')",
            name=op.f("ck_data_issues_kind"),
        ),
        sa.PrimaryKeyConstraint("space_id", "issue_id", name=op.f("pk_data_issues")),
    )
    op.create_index(
        op.f("ix_data_issues_space_id_fingerprint"),
        "data_issues",
        ["space_id", "fingerprint"],
        unique=True,
        postgresql_where=sa.text("acknowledged_at IS NULL"),
    )
    op.create_index(
        op.f("ix_data_issues_space_id_opened_at"), "data_issues", ["space_id", "opened_at"], unique=False
    )
    op.create_table(
        "decisions",
        sa.Column("space_id", sa.UUID(), nullable=False),
        sa.Column("decision_id", sa.UUID(), nullable=False),
        sa.Column("task", sa.Text(), nullable=False),
        sa.Column("input_hash", sa.LargeBinary(), nullable=False),
        sa.Column("model", sa.Text(), nullable=False),
        sa.Column("model_version", sa.Text(), nullable=False),
        sa.Column("label", sa.Text(), nullable=False),
        sa.Column("probability", sa.REAL(), nullable=False),
        sa.Column("threshold", sa.REAL(), nullable=False),
        sa.Column("subject_ref", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("space_id", "decision_id", name=op.f("pk_decisions")),
    )
    op.create_index(
        "ix_decisions_contradiction_subject",
        "decisions",
        ["space_id", "subject_ref"],
        unique=False,
        postgresql_where=sa.text("task = 'contradiction'"),
    )
    op.create_table(
        "effects",
        sa.Column("space_id", sa.UUID(), nullable=False),
        sa.Column("effect_key_hash", sa.LargeBinary(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("state", sa.Text(), nullable=False),
        sa.Column("fact_type", sa.Text(), nullable=True),
        sa.Column("fact_namespace", sa.Text(), nullable=True),
        sa.Column("fact_external_id", sa.Text(), nullable=True),
        sa.Column("origin_handle_id", sa.UUID(), nullable=True),
        sa.Column("subject_hmac", sa.LargeBinary(), nullable=True),
        sa.Column("source_id", sa.UUID(), nullable=False),
        sa.Column("agent", sa.Text(), nullable=True),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("cost_units", sa.BigInteger(), server_default=sa.text("0"), nullable=False),
        sa.Column("reserved_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("done_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("settled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("settled_by", sa.Text(), nullable=True),
        sa.Column("key_valid_until", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "kind IN ('delivery', 'document', 'notice', 'filing', 'paid_call', 'external_action')",
            name=op.f("ck_effects_kind"),
        ),
        sa.CheckConstraint(
            "settled_by IS NULL OR settled_by IN ('reserver', 'observation', 'person')",
            name=op.f("ck_effects_settled_by"),
        ),
        sa.CheckConstraint(
            "state IN ('reserved', 'done', 'failed', 'unknown_outcome')", name=op.f("ck_effects_state")
        ),
        sa.PrimaryKeyConstraint("space_id", "effect_key_hash", name=op.f("pk_effects")),
    )
    op.create_index(
        op.f("ix_effects_space_id_origin_handle_id"),
        "effects",
        ["space_id", "origin_handle_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_effects_space_id_subject_hmac"), "effects", ["space_id", "subject_hmac"], unique=False
    )
    op.create_index(
        "ix_effects_unknown",
        "effects",
        ["space_id", "reserved_at"],
        unique=False,
        postgresql_where=sa.text("state = 'unknown_outcome'"),
    )
    op.create_table(
        "episodes",
        sa.Column("space_id", sa.UUID(), nullable=False),
        sa.Column("origin_handle_id", sa.UUID(), nullable=False),
        sa.Column("episode_id", sa.UUID(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("session_id", sa.UUID(), nullable=False),
        sa.Column("conversation_id", sa.Text(), nullable=True),
        sa.Column("channel", sa.Text(), nullable=False),
        sa.Column("source_id", sa.UUID(), nullable=False),
        sa.Column("about_handle_id", sa.UUID(), nullable=True),
        sa.Column("summary_ct", sa.LargeBinary(), nullable=False),
        sa.Column("intent", sa.Text(), nullable=False),
        sa.Column("category", sa.Text(), nullable=False),
        sa.Column("normalized_category", sa.Text(), nullable=False),
        sa.Column("outcome", sa.Text(), nullable=False),
        sa.Column("resolution_ct", sa.LargeBinary(), nullable=True),
        sa.Column("sentiment", sa.REAL(), nullable=True),
        sa.Column("initiated_by", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("is_current", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("dek_version", sa.Integer(), nullable=False),
        sa.Column(
            "change_seq", sa.BigInteger(), server_default=sa.text("nextval('change_seq')"), nullable=False
        ),
        sa.Column("purposes", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("spoken_by", sa.Text(), nullable=True),
        sa.Column("matter", sa.Text(), nullable=True),
        sa.Column("matter_label", sa.Text(), nullable=True),
        sa.Column("embedding_ct", sa.LargeBinary(), nullable=True),
        sa.Column("embedding_model", sa.Text(), nullable=True),
        sa.Column("embedding_dim", sa.Integer(), nullable=True),
        sa.Column("embedding_dek_version", sa.Integer(), nullable=True),
        sa.Column("object_refs", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.PrimaryKeyConstraint(
            "space_id", "origin_handle_id", "episode_id", "version", name=op.f("pk_episodes")
        ),
        postgresql_partition_by="HASH (origin_handle_id)",
    )
    op.create_index(
        op.f("ix_episodes_space_id_change_seq"), "episodes", ["space_id", "change_seq"], unique=False
    )
    op.create_index(
        op.f("ix_episodes_space_id_origin_handle_id_normalized_category_ended_at"),
        "episodes",
        ["space_id", "origin_handle_id", "normalized_category", "ended_at"],
        unique=False,
    )
    op.create_table(
        "erasure_requests",
        sa.Column("space_id", sa.UUID(), nullable=False),
        sa.Column("request_id", sa.UUID(), nullable=False),
        sa.Column("target_kind", sa.Text(), nullable=False),
        sa.Column("target_id", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("requested_by", sa.Text(), nullable=False),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "counts", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'"), nullable=False
        ),
        sa.Column("receipt_hash", sa.LargeBinary(), nullable=True),
        sa.CheckConstraint(
            "status IN ('pending', 'running', 'completed', 'failed')", name=op.f("ck_erasure_requests_status")
        ),
        sa.CheckConstraint(
            "target_kind IN ('profile', 'handle', 'conversation')",
            name=op.f("ck_erasure_requests_target_kind"),
        ),
        sa.PrimaryKeyConstraint("space_id", "request_id", name=op.f("pk_erasure_requests")),
    )
    op.create_table(
        "event_handles",
        sa.Column("space_id", sa.UUID(), nullable=False),
        sa.Column("event_id", sa.UUID(), nullable=False),
        sa.Column("handle_id", sa.UUID(), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("space_id", "event_id", "handle_id", name=op.f("pk_event_handles")),
        postgresql_partition_by="RANGE (event_id)",
    )
    op.create_index(
        op.f("ix_event_handles_space_id_handle_id_received_at"),
        "event_handles",
        ["space_id", "handle_id", "received_at"],
        unique=False,
    )
    op.create_table(
        "event_keys_0",
        sa.Column("space_id", sa.UUID(), nullable=False),
        sa.Column("idempotency_key", sa.Text(), nullable=False),
        sa.Column("event_id", sa.UUID(), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("space_id", "idempotency_key", name=op.f("pk_event_keys_0")),
        postgresql_partition_by="HASH (space_id, idempotency_key)",
    )
    op.create_table(
        "event_keys_1",
        sa.Column("space_id", sa.UUID(), nullable=False),
        sa.Column("idempotency_key", sa.Text(), nullable=False),
        sa.Column("event_id", sa.UUID(), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("space_id", "idempotency_key", name=op.f("pk_event_keys_1")),
        postgresql_partition_by="HASH (space_id, idempotency_key)",
    )
    op.create_table(
        "event_keys_2",
        sa.Column("space_id", sa.UUID(), nullable=False),
        sa.Column("idempotency_key", sa.Text(), nullable=False),
        sa.Column("event_id", sa.UUID(), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("space_id", "idempotency_key", name=op.f("pk_event_keys_2")),
        postgresql_partition_by="HASH (space_id, idempotency_key)",
    )
    op.create_table(
        "events",
        sa.Column("space_id", sa.UUID(), nullable=False),
        sa.Column("event_id", sa.UUID(), nullable=False),
        sa.Column("idempotency_key", sa.Text(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("canonical_type", sa.Text(), nullable=True),
        sa.Column("source_id", sa.UUID(), nullable=False),
        sa.Column("channel", sa.Text(), nullable=False),
        sa.Column("conversation_id", sa.Text(), nullable=True),
        sa.Column("task_id", sa.Text(), nullable=True),
        sa.Column("handle_ids", postgresql.ARRAY(sa.UUID()), server_default=sa.text("'{}'"), nullable=False),
        sa.Column("subjects", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("object_refs", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("closes_ref", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("action", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("corrects_event_id", sa.UUID(), nullable=True),
        sa.Column("speaker", sa.Text(), nullable=False),
        sa.Column("speaker_id", sa.Text(), nullable=True),
        sa.Column("direction", sa.Text(), nullable=True),
        sa.Column("visibility", sa.Text(), server_default="public", nullable=False),
        sa.Column("verification_hint", sa.Text(), nullable=True),
        sa.Column("content_ct", sa.LargeBinary(), nullable=True),
        sa.Column("content_meta", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("fields_ct", sa.LargeBinary(), nullable=True),
        sa.Column("voice", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("skew_corrected", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("raw_ref", sa.Text(), nullable=True),
        sa.Column("dek_version", sa.Integer(), nullable=False),
        sa.Column(
            "change_seq", sa.BigInteger(), server_default=sa.text("nextval('change_seq')"), nullable=False
        ),
        sa.CheckConstraint("kind IN ('message', 'system_event', 'action')", name=op.f("ck_events_kind")),
        sa.PrimaryKeyConstraint("space_id", "event_id", name=op.f("pk_events")),
        postgresql_partition_by="RANGE (event_id)",
    )
    op.create_index(
        op.f("ix_events_space_id_conversation_id_occurred_at"),
        "events",
        ["space_id", "conversation_id", "occurred_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_events_space_id_source_id_event_id"),
        "events",
        ["space_id", "source_id", "event_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_events_space_id_task_id_occurred_at"),
        "events",
        ["space_id", "task_id", "occurred_at"],
        unique=False,
        postgresql_where=sa.text("task_id IS NOT NULL"),
    )
    op.create_table(
        "experiment_assignments",
        sa.Column("space_id", sa.UUID(), nullable=False),
        sa.Column("experiment", sa.Text(), nullable=False),
        sa.Column("profile_id", sa.UUID(), nullable=False),
        sa.Column("grp", sa.Text(), nullable=False),
        sa.Column("assigned_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.CheckConstraint(
            "kind IN ('memory', 'element', 'agent', 'interleave')",
            name=op.f("ck_experiment_assignments_kind"),
        ),
        sa.PrimaryKeyConstraint(
            "space_id", "experiment", "profile_id", name=op.f("pk_experiment_assignments")
        ),
    )
    op.create_table(
        "export_runs",
        sa.Column("space_id", sa.UUID(), nullable=False),
        sa.Column("run_id", sa.UUID(), nullable=False),
        sa.Column("schedule_id", sa.Text(), nullable=False),
        sa.Column("from_seq", sa.BigInteger(), nullable=False),
        sa.Column("to_seq", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("manifest", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("space_id", "run_id", name=op.f("pk_export_runs")),
    )
    op.create_table(
        "extraction_runs",
        sa.Column("space_id", sa.UUID(), nullable=False),
        sa.Column("session_id", sa.UUID(), nullable=False),
        sa.Column("versions_key", sa.Text(), nullable=False),
        sa.Column("model", sa.Text(), nullable=False),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=False),
        sa.Column("output_tokens", sa.Integer(), nullable=False),
        sa.Column("cached_tokens", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("cost_micros", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("dropped_items", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("result_ct", sa.LargeBinary(), nullable=True),
        sa.Column("dek_version", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("applied_profile_id", sa.UUID(), nullable=True),
        sa.Column("applied_work", sa.BigInteger(), nullable=True),
        sa.CheckConstraint(
            "status IN ('ok', 'invalid', 'failed', 'minimal')", name=op.f("ck_extraction_runs_status")
        ),
        sa.PrimaryKeyConstraint("space_id", "session_id", "versions_key", name=op.f("pk_extraction_runs")),
    )
    op.create_table(
        "fact_mentions",
        sa.Column("space_id", sa.UUID(), nullable=False),
        sa.Column("origin_handle_id", sa.UUID(), nullable=False),
        sa.Column("fact_id", sa.UUID(), nullable=False),
        sa.Column("event_id", sa.UUID(), nullable=False),
        sa.Column(
            "change_seq", sa.BigInteger(), server_default=sa.text("nextval('change_seq')"), nullable=False
        ),
        sa.Column("feedback", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.PrimaryKeyConstraint(
            "space_id", "origin_handle_id", "fact_id", "event_id", name=op.f("pk_fact_mentions")
        ),
        postgresql_partition_by="HASH (origin_handle_id)",
    )
    op.create_index(
        op.f("ix_fact_mentions_space_id_change_seq"),
        "fact_mentions",
        ["space_id", "change_seq"],
        unique=False,
    )
    op.create_index(
        op.f("ix_fact_mentions_space_id_event_id"), "fact_mentions", ["space_id", "event_id"], unique=False
    )
    op.create_table(
        "fact_relations",
        sa.Column("space_id", sa.UUID(), nullable=False),
        sa.Column("a_handle_id", sa.UUID(), nullable=False),
        sa.Column("a_fact_id", sa.UUID(), nullable=False),
        sa.Column("b_handle_id", sa.UUID(), nullable=False),
        sa.Column("b_fact_id", sa.UUID(), nullable=False),
        sa.Column("relation", sa.Text(), nullable=False),
        sa.Column("origin", sa.Text(), nullable=False),
        sa.CheckConstraint(
            "relation IN ('supersedes', 'contradicts')",
            name=op.f("ck_fact_relations_relation"),
        ),
        sa.PrimaryKeyConstraint(
            "space_id",
            "a_handle_id",
            "a_fact_id",
            "b_handle_id",
            "b_fact_id",
            "relation",
            name=op.f("pk_fact_relations"),
        ),
        postgresql_partition_by="HASH (a_handle_id)",
    )
    op.create_table(
        "facts",
        sa.Column("space_id", sa.UUID(), nullable=False),
        sa.Column("origin_handle_id", sa.UUID(), nullable=False),
        sa.Column("fact_id", sa.UUID(), nullable=False),
        sa.Column("origin_conversation_id", sa.Text(), nullable=True),
        sa.Column("predicate", sa.Text(), nullable=False),
        sa.Column("namespace", sa.Text(), server_default="tenant", nullable=False),
        sa.Column("value_ct", sa.LargeBinary(), nullable=False),
        sa.Column("value_hmac", sa.LargeBinary(), nullable=False),
        sa.Column("category", sa.Text(), nullable=False),
        sa.Column("sensitivity", sa.Text(), nullable=False),
        sa.Column("speaker", sa.Text(), nullable=False),
        sa.Column("confidence", sa.REAL(), nullable=False),
        sa.Column("valid_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("invalid_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expired_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("times_seen", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("last_confirmed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("about_handle_id", sa.UUID(), nullable=True),
        sa.Column("object_ref", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "mention_handle_ids", postgresql.ARRAY(sa.UUID()), server_default=sa.text("'{}'"), nullable=False
        ),
        sa.Column("embedding_ct", sa.LargeBinary(), nullable=True),
        sa.Column("embedding_model", sa.Text(), nullable=True),
        sa.Column("embedding_dim", sa.Integer(), nullable=True),
        sa.Column("embedding_dek_version", sa.Integer(), nullable=True),
        sa.Column("dek_version", sa.Integer(), nullable=False),
        sa.Column(
            "change_seq", sa.BigInteger(), server_default=sa.text("nextval('change_seq')"), nullable=False
        ),
        sa.Column("valid_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sessions_seen", sa.Integer(), nullable=False),
        sa.Column("purposes", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("audience", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "status IN ('active', 'expired', 'quarantined', 'retracted')", name=op.f("ck_facts_status")
        ),
        sa.CheckConstraint("audience IN ('shared', 'internal')", name=op.f("ck_facts_audience")),
        sa.PrimaryKeyConstraint("space_id", "origin_handle_id", "fact_id", name=op.f("pk_facts")),
        sa.UniqueConstraint(
            "space_id",
            "origin_handle_id",
            "predicate",
            "value_hmac",
            name=op.f("uq_facts_space_id_origin_handle_id_predicate_value_hmac"),
        ),
        postgresql_partition_by="HASH (origin_handle_id)",
    )
    op.create_index(op.f("ix_facts_space_id_change_seq"), "facts", ["space_id", "change_seq"], unique=False)
    op.create_index(
        "ix_facts_object_ref",
        "facts",
        [
            "space_id",
            sa.text("(object_ref ->> 'type')"),
            sa.text("(object_ref ->> 'namespace')"),
            sa.text("(object_ref ->> 'external_id')"),
        ],
        unique=False,
        postgresql_where=sa.text("object_ref IS NOT NULL"),
    )
    op.create_table(
        "handles",
        sa.Column("space_id", sa.UUID(), nullable=False),
        sa.Column("handle_id", sa.UUID(), nullable=False),
        sa.Column("type", sa.Text(), nullable=False),
        sa.Column("scope", sa.Text(), server_default="", nullable=False),
        sa.Column("subject_kind", sa.Text(), nullable=False),
        sa.Column("value_hmac", sa.LargeBinary(), nullable=False),
        sa.Column("value_ct", sa.LargeBinary(), nullable=True),
        sa.Column("status", sa.Text(), server_default="active", nullable=False),
        sa.Column("level", sa.Text(), server_default="V0", nullable=False),
        sa.Column("first_seen", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen", sa.DateTime(timezone=True), nullable=False),
        sa.Column("former_hmac", sa.LargeBinary(), nullable=True),
        sa.Column("provisional", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("dek_version", sa.Integer(), nullable=False),
        sa.Column(
            "change_seq", sa.BigInteger(), server_default=sa.text("nextval('change_seq')"), nullable=False
        ),
        sa.PrimaryKeyConstraint("space_id", "handle_id", name=op.f("pk_handles")),
    )
    op.create_index(
        "ix_handles_former_hmac",
        "handles",
        ["space_id", "type", "scope", "former_hmac"],
        unique=False,
        postgresql_where=sa.text("former_hmac IS NOT NULL"),
    )
    op.create_index(
        op.f("ix_handles_space_id_type_scope_value_hmac"),
        "handles",
        ["space_id", "type", "scope", "value_hmac"],
        unique=True,
    )
    op.create_index(
        "ix_handles_last_seen",
        "handles",
        ["space_id", "last_seen", "handle_id", "subject_kind"],
        unique=False,
    )
    op.create_table(
        "handoffs",
        sa.Column("space_id", sa.UUID(), nullable=False),
        sa.Column("handoff_id", sa.UUID(), nullable=False),
        sa.Column("origin_handle_id", sa.UUID(), nullable=True),
        sa.Column("profile_id", sa.UUID(), nullable=True),
        sa.Column("subject_hmac", sa.LargeBinary(), nullable=True),
        sa.Column("subject_ct", sa.LargeBinary(), nullable=True),
        sa.Column("conversation_id", sa.Text(), nullable=True),
        sa.Column("source_id", sa.UUID(), nullable=False),
        sa.Column("agent", sa.Text(), nullable=True),
        sa.Column("target", sa.Text(), nullable=False),
        sa.Column("level", sa.Text(), nullable=True),
        sa.Column("policy_version", sa.Text(), nullable=True),
        sa.Column("package_ct", sa.LargeBinary(), nullable=False),
        sa.Column("package_sha256", sa.LargeBinary(), nullable=False),
        sa.Column("dek_version", sa.Integer(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("outcome", sa.Text(), nullable=True),
        sa.Column("outcome_ct", sa.LargeBinary(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expected_by", sa.DateTime(timezone=True), nullable=True),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('created', 'accepted', 'closed', 'expired')", name=op.f("ck_handoffs_status")
        ),
        sa.PrimaryKeyConstraint("space_id", "handoff_id", name=op.f("pk_handoffs")),
    )
    op.create_index(
        op.f("ix_handoffs_space_id_origin_handle_id"),
        "handoffs",
        ["space_id", "origin_handle_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_handoffs_space_id_status_created_at"),
        "handoffs",
        ["space_id", "status", "created_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_handoffs_space_id_subject_hmac"), "handoffs", ["space_id", "subject_hmac"], unique=False
    )
    op.create_table(
        "identity_suggestions",
        sa.Column("space_id", sa.UUID(), nullable=False),
        sa.Column("suggestion_id", sa.UUID(), nullable=False),
        sa.Column("handle_a", sa.UUID(), nullable=False),
        sa.Column("handle_b", sa.UUID(), nullable=False),
        sa.Column("score", sa.REAL(), nullable=False),
        sa.Column("features", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("status", sa.Text(), server_default="open", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('open', 'accepted', 'rejected')", name=op.f("ck_identity_suggestions_status")
        ),
        sa.PrimaryKeyConstraint("space_id", "suggestion_id", name=op.f("pk_identity_suggestions")),
    )
    op.create_index(
        "ix_identity_suggestions_open_a",
        "identity_suggestions",
        ["space_id", "handle_a"],
        unique=False,
        postgresql_where=sa.text("status = 'open'"),
    )
    op.create_index(
        "ix_identity_suggestions_open_b",
        "identity_suggestions",
        ["space_id", "handle_b"],
        unique=False,
        postgresql_where=sa.text("status = 'open'"),
    )
    op.create_table(
        "inference_suppressions",
        sa.Column("space_id", sa.UUID(), nullable=False),
        sa.Column("origin_handle_id", sa.UUID(), nullable=False),
        sa.Column("beneficiary_id", sa.UUID(), nullable=False),
        sa.Column("inference_key_hmac", sa.LargeBinary(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("evidence_until", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(
            "kind IN ('affinity', 'soft_constraint', 'attribute', 'interest')",
            name=op.f("ck_inference_suppressions_kind"),
        ),
        sa.PrimaryKeyConstraint(
            "space_id",
            "origin_handle_id",
            "beneficiary_id",
            "inference_key_hmac",
            name=op.f("pk_inference_suppressions"),
        ),
    )
    op.create_table(
        "ingest_imports",
        sa.Column("space_id", sa.UUID(), nullable=False),
        sa.Column("import_id", sa.UUID(), nullable=False),
        sa.Column("source_id", sa.UUID(), nullable=False),
        sa.Column("key_id", sa.Text(), nullable=False),
        sa.Column("scopes", postgresql.ARRAY(sa.Text()), nullable=False),
        sa.Column("format", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("sha256", sa.Text(), nullable=False),
        sa.Column("header", postgresql.ARRAY(sa.Text()), nullable=True),
        sa.Column("part_rows", postgresql.ARRAY(sa.BigInteger()), nullable=False),
        sa.Column("next_part", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("next_row", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("accepted", sa.BigInteger(), server_default=sa.text("0"), nullable=False),
        sa.Column("duplicates", sa.BigInteger(), server_default=sa.text("0"), nullable=False),
        sa.Column("rejected", sa.BigInteger(), server_default=sa.text("0"), nullable=False),
        sa.Column(
            "errors", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[]'"), nullable=False
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("format IN ('csv', 'jsonl')", name=op.f("ck_ingest_imports_format")),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'completed', 'failed')", name=op.f("ck_ingest_imports_status")
        ),
        sa.PrimaryKeyConstraint("space_id", "import_id", name=op.f("pk_ingest_imports")),
    )
    op.create_table(
        "ingest_intake",
        sa.Column("space_id", sa.UUID(), nullable=False),
        sa.Column("intake_id", sa.UUID(), nullable=False),
        sa.Column("seq", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source_id", sa.UUID(), nullable=False),
        sa.Column("body_ct", sa.LargeBinary(), nullable=False),
        sa.Column("dek_version", sa.Integer(), nullable=False),
        sa.Column("skip", postgresql.ARRAY(sa.Text()), server_default=sa.text("'{}'"), nullable=False),
        sa.Column("attempts", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.PrimaryKeyConstraint("space_id", "intake_id", name=op.f("pk_ingest_intake")),
    )
    op.create_table(
        "insight_reviews",
        sa.Column("space_id", sa.UUID(), nullable=False),
        sa.Column("key", sa.Text(), nullable=False),
        sa.Column("decision", sa.Text(), nullable=False),
        sa.Column("decided_by", sa.Text(), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.CheckConstraint("decision IN ('accepted', 'dismissed')", name=op.f("ck_insight_reviews_decision")),
        sa.PrimaryKeyConstraint("space_id", "key", name=op.f("pk_insight_reviews")),
    )
    op.create_table(
        "interleave_daily",
        sa.Column("space_id", sa.UUID(), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("experiment", sa.Text(), nullable=False),
        sa.Column("tool", sa.Text(), nullable=False),
        sa.Column("impressions", sa.Integer(), nullable=False),
        sa.Column("wins_a", sa.Integer(), nullable=False),
        sa.Column("wins_b", sa.Integer(), nullable=False),
        sa.Column("ties", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("space_id", "day", "experiment", "tool", name=op.f("pk_interleave_daily")),
    )
    op.create_table(
        "knowledge_gaps",
        sa.Column("space_id", sa.UUID(), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("subject", sa.Text(), nullable=False),
        sa.Column("topic", sa.Text(), server_default="", nullable=False),
        sa.Column("turns", sa.BigInteger(), nullable=False),
        sa.Column("customers", sa.LargeBinary(), nullable=False),
        sa.CheckConstraint(
            "kind IN ('no_record', 'agent_question', 'answered', 'object_unresolved')",
            name=op.f("ck_knowledge_gaps_kind"),
        ),
        sa.PrimaryKeyConstraint(
            "space_id", "day", "kind", "subject", "topic", name=op.f("pk_knowledge_gaps")
        ),
    )
    op.create_table(
        "legal_holds",
        sa.Column("space_id", sa.UUID(), nullable=False),
        sa.Column("hold_id", sa.UUID(), nullable=False),
        sa.Column("scope", sa.Text(), nullable=False),
        sa.Column("handle_id", sa.UUID(), nullable=True),
        sa.Column("object_id", sa.UUID(), nullable=True),
        sa.Column("conversation_hmac", sa.LargeBinary(), nullable=True),
        sa.Column("reason_ct", sa.LargeBinary(), nullable=False),
        sa.Column("status", sa.Text(), server_default="active", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by", sa.Text(), nullable=False),
        sa.Column("released_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("released_by", sa.Text(), nullable=True),
        sa.Column("release_reason_ct", sa.LargeBinary(), nullable=True),
        sa.Column("dek_version", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "(scope = 'handle') = (handle_id IS NOT NULL) AND (scope = 'object') = (object_id IS NOT NULL) AND (scope = 'conversation') = (conversation_hmac IS NOT NULL)",
            name=op.f("ck_legal_holds_one_target"),
        ),
        sa.CheckConstraint(
            "(status = 'released') = (released_at IS NOT NULL)", name=op.f("ck_legal_holds_released")
        ),
        sa.CheckConstraint(
            "scope IN ('handle', 'object', 'conversation')", name=op.f("ck_legal_holds_scope")
        ),
        sa.CheckConstraint("status IN ('active', 'released')", name=op.f("ck_legal_holds_status")),
        sa.PrimaryKeyConstraint("space_id", "hold_id", name=op.f("pk_legal_holds")),
    )
    op.create_index(
        op.f("ix_legal_holds_space_id_conversation_hmac"),
        "legal_holds",
        ["space_id", "conversation_hmac"],
        unique=False,
        postgresql_where=sa.text("status = 'active'"),
    )
    op.create_index(
        op.f("ix_legal_holds_space_id_handle_id"),
        "legal_holds",
        ["space_id", "handle_id"],
        unique=False,
        postgresql_where=sa.text("status = 'active'"),
    )
    op.create_index(
        op.f("ix_legal_holds_space_id_object_id"),
        "legal_holds",
        ["space_id", "object_id"],
        unique=False,
        postgresql_where=sa.text("status = 'active'"),
    )
    op.create_table(
        "llm_spend",
        sa.Column("space_id", sa.UUID(), nullable=False),
        sa.Column("day", sa.DateTime(timezone=True), nullable=False),
        sa.Column("purpose", sa.Text(), nullable=False),
        sa.Column("cost_micros", sa.BigInteger(), server_default=sa.text("0"), nullable=False),
        sa.Column("calls", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.PrimaryKeyConstraint("space_id", "day", "purpose", name=op.f("pk_llm_spend")),
    )
    op.create_table(
        "measure_segments",
        sa.Column("space_id", sa.UUID(), nullable=False),
        sa.Column("segment_id", sa.UUID(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("object_key", sa.Text(), nullable=False),
        sa.Column("rows", sa.Integer(), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("sha256", sa.LargeBinary(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("rewritten_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "kind IN ('raw_hourly', 'daily', 'demand_daily')", name=op.f("ck_measure_segments_kind")
        ),
        sa.PrimaryKeyConstraint("space_id", "segment_id", name=op.f("pk_measure_segments")),
    )
    op.create_index(
        op.f("ix_measure_segments_space_id_expires_at"),
        "measure_segments",
        ["space_id", "expires_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_measure_segments_space_id_kind_starts_at"),
        "measure_segments",
        ["space_id", "kind", "starts_at"],
        unique=True,
    )
    op.create_table(
        "measured_sessions",
        sa.Column("space_id", sa.UUID(), nullable=False),
        sa.Column("session_id", sa.UUID(), nullable=False),
        sa.Column("measure_version", sa.Text(), nullable=False),
        sa.Column("conversation_key", sa.Text(), nullable=False),
        sa.Column("source_id", sa.UUID(), nullable=False),
        sa.Column("channel", sa.Text(), nullable=False),
        sa.Column("profile_id", sa.UUID(), nullable=True),
        sa.Column("experiment_group", sa.Text(), server_default="", nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("measured_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("no_context", sa.Boolean(), nullable=False),
        sa.Column("normalized_category", sa.Text(), nullable=True),
        sa.Column("initiated_by", sa.Text(), nullable=True),
        sa.Column("transfer_state", sa.Text(), nullable=True),
        sa.Column("recontact_scope", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "recontact_scope IN ('category', 'general')", name=op.f("ck_measured_sessions_recontact_scope")
        ),
        sa.CheckConstraint(
            "transfer_state IN ('none', 'read', 'unread', 'not_integrated')",
            name=op.f("ck_measured_sessions_transfer_state"),
        ),
        sa.PrimaryKeyConstraint("space_id", "session_id", name=op.f("pk_measured_sessions")),
    )
    op.create_index(
        "ix_measured_sessions_space_id_profile_id_ended_at",
        "measured_sessions",
        ["space_id", "profile_id", "ended_at"],
        unique=False,
    )
    op.create_table(
        "measurement_reports",
        sa.Column("space_id", sa.UUID(), nullable=False),
        sa.Column("report_id", sa.UUID(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("period_start", sa.Date(), nullable=False),
        sa.Column("period_end", sa.Date(), nullable=False),
        sa.Column("body", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "kind IN ('attribution', 'reconcile', 'interleaving', 'counterfactual', 'power', 'experiment', 'cost')",
            name=op.f("ck_measurement_reports_kind"),
        ),
        sa.CheckConstraint("period_start <= period_end", name=op.f("ck_measurement_reports_period")),
        sa.PrimaryKeyConstraint("space_id", "report_id", name=op.f("pk_measurement_reports")),
    )
    op.create_index(
        op.f("ix_measurement_reports_space_id_kind_created_at"),
        "measurement_reports",
        ["space_id", "kind", "created_at"],
        unique=False,
    )
    op.create_table(
        "merge_log",
        sa.Column("space_id", sa.UUID(), nullable=False),
        sa.Column("merge_id", sa.UUID(), nullable=False),
        sa.Column("op", sa.Text(), nullable=False),
        sa.Column("from_profiles", postgresql.ARRAY(sa.UUID()), nullable=False),
        sa.Column("to_profiles", postgresql.ARRAY(sa.UUID()), nullable=False),
        sa.Column("assertion_id", sa.UUID(), nullable=True),
        sa.Column("handles_moved", postgresql.ARRAY(sa.UUID()), nullable=False),
        sa.Column("actor", sa.Text(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reversed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "change_seq", sa.BigInteger(), server_default=sa.text("nextval('change_seq')"), nullable=False
        ),
        sa.CheckConstraint("op IN ('merge', 'unmerge')", name=op.f("ck_merge_log_op")),
        sa.PrimaryKeyConstraint("space_id", "merge_id", name=op.f("pk_merge_log")),
    )
    op.create_table(
        "model_usage_daily",
        sa.Column("space_id", sa.UUID(), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("source_id", sa.UUID(), nullable=False),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("model", sa.Text(), nullable=False),
        sa.Column("calls", sa.BigInteger(), server_default=sa.text("0"), nullable=False),
        sa.Column("prompt_tokens", sa.BigInteger(), server_default=sa.text("0"), nullable=False),
        sa.Column("cached_tokens", sa.BigInteger(), server_default=sa.text("0"), nullable=False),
        sa.Column("cache_write_tokens", sa.BigInteger(), server_default=sa.text("0"), nullable=False),
        sa.PrimaryKeyConstraint(
            "space_id", "day", "source_id", "provider", "model", name=op.f("pk_model_usage_daily")
        ),
    )
    op.create_table(
        "notification_feed",
        sa.Column("space_id", sa.UUID(), nullable=False),
        sa.Column("seq", sa.BigInteger(), nullable=False),
        sa.Column("event_id", sa.UUID(), nullable=False),
        sa.Column("type", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("body", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.PrimaryKeyConstraint("space_id", "seq", name=op.f("pk_notification_feed")),
    )
    op.create_index(
        op.f("ix_notification_feed_space_id_created_at"),
        "notification_feed",
        ["space_id", "created_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_notification_feed_space_id_event_id"),
        "notification_feed",
        ["space_id", "event_id"],
        unique=True,
    )
    op.create_table(
        "object_dependencies",
        sa.Column("space_id", sa.UUID(), nullable=False),
        sa.Column("input_object_id", sa.UUID(), nullable=False),
        sa.Column("input_field", sa.Text(), nullable=False),
        sa.Column("dependent_object_id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint(
            "space_id",
            "input_object_id",
            "input_field",
            "dependent_object_id",
            name=op.f("pk_object_dependencies"),
        ),
    )
    op.create_index(
        op.f("ix_object_dependencies_space_id_dependent_object_id"),
        "object_dependencies",
        ["space_id", "dependent_object_id"],
        unique=False,
    )
    op.create_table(
        "object_events",
        sa.Column("space_id", sa.UUID(), nullable=False),
        sa.Column("object_id", sa.UUID(), nullable=False),
        sa.Column("event_id", sa.UUID(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "change_seq", sa.BigInteger(), server_default=sa.text("nextval('change_seq')"), nullable=False
        ),
        sa.PrimaryKeyConstraint("space_id", "object_id", "event_id", name=op.f("pk_object_events")),
    )
    op.create_index(
        op.f("ix_object_events_space_id_change_seq"),
        "object_events",
        ["space_id", "change_seq"],
        unique=False,
    )
    op.create_index(
        op.f("ix_object_events_space_id_event_id"), "object_events", ["space_id", "event_id"], unique=False
    )
    op.create_table(
        "object_participants",
        sa.Column("space_id", sa.UUID(), nullable=False),
        sa.Column("object_id", sa.UUID(), nullable=False),
        sa.Column("handle_id", sa.UUID(), nullable=False),
        sa.Column("role", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint(
            "space_id", "object_id", "handle_id", "role", name=op.f("pk_object_participants")
        ),
    )
    op.create_index(
        op.f("ix_object_participants_space_id_handle_id"),
        "object_participants",
        ["space_id", "handle_id"],
        unique=False,
    )
    op.create_table(
        "object_timers",
        sa.Column("space_id", sa.UUID(), nullable=False),
        sa.Column("timer_id", sa.UUID(), nullable=False),
        sa.Column("object_id", sa.UUID(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("once_per_key", sa.Text(), nullable=True),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("state", sa.Text(), nullable=False),
        sa.Column("value_version", sa.Integer(), nullable=True),
        sa.Column("fired_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("supersedes_firing_id", sa.UUID(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("state IN ('armed', 'fired', 'cancelled')", name=op.f("ck_object_timers_state")),
        sa.PrimaryKeyConstraint("space_id", "timer_id", name=op.f("pk_object_timers")),
    )
    op.create_index(
        "ix_object_timers_armed",
        "object_timers",
        ["space_id", "due_at"],
        unique=False,
        postgresql_where=sa.text("state = 'armed'"),
    )
    op.create_index(
        op.f("ix_object_timers_space_id_object_id_name"),
        "object_timers",
        ["space_id", "object_id", "name"],
        unique=False,
    )
    op.create_table(
        "object_values",
        sa.Column("space_id", sa.UUID(), nullable=False),
        sa.Column("object_id", sa.UUID(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("value_ct", sa.LargeBinary(), nullable=True),
        sa.Column("dek_version", sa.Integer(), nullable=True),
        sa.Column("absent_as", sa.Text(), nullable=True),
        sa.Column("rule", sa.Text(), nullable=False),
        sa.Column("rule_version", sa.Text(), nullable=False),
        sa.Column("inputs_hash", sa.LargeBinary(), nullable=False),
        sa.Column(
            "input_refs",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'"),
            nullable=False,
        ),
        sa.Column("computed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("valid_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "declared_gaps", postgresql.ARRAY(sa.Text()), server_default=sa.text("'{}'"), nullable=False
        ),
        sa.Column("gap_effect", sa.Text(), server_default="unknown", nullable=False),
        sa.Column("supersedes_version", sa.Integer(), nullable=True),
        sa.Column("event_id", sa.UUID(), nullable=True),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "change_seq", sa.BigInteger(), server_default=sa.text("nextval('change_seq')"), nullable=False
        ),
        sa.CheckConstraint(
            "gap_effect IN ('errs_early', 'errs_late', 'unknown')", name=op.f("ck_object_values_gap_effect")
        ),
        sa.CheckConstraint(
            "(value_ct IS NULL) = (dek_version IS NULL)", name=op.f("ck_object_values_sealed")
        ),
        sa.PrimaryKeyConstraint("space_id", "object_id", "name", "version", name=op.f("pk_object_values")),
    )
    op.create_index(
        op.f("ix_object_values_space_id_change_seq"),
        "object_values",
        ["space_id", "change_seq"],
        unique=False,
    )
    op.create_index(
        op.f("ix_object_values_space_id_recorded_at"),
        "object_values",
        ["space_id", "recorded_at"],
        unique=False,
    )
    op.create_table(
        "object_watches",
        sa.Column("space_id", sa.UUID(), nullable=False),
        sa.Column("watch_id", sa.UUID(), nullable=False),
        sa.Column("object_id", sa.UUID(), nullable=False),
        sa.Column("origin_handle_id", sa.UUID(), nullable=False),
        sa.Column("condition", sa.Text(), nullable=False),
        sa.Column("consent_event_key", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_fired_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("space_id", "watch_id", name=op.f("pk_object_watches")),
    )
    op.create_index(
        op.f("ix_object_watches_space_id_expires_at"),
        "object_watches",
        ["space_id", "expires_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_object_watches_space_id_object_id"),
        "object_watches",
        ["space_id", "object_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_object_watches_space_id_origin_handle_id"),
        "object_watches",
        ["space_id", "origin_handle_id"],
        unique=False,
    )
    op.create_table(
        "open_item_feedback_events",
        sa.Column("space_id", sa.UUID(), nullable=False),
        sa.Column("item_id", sa.UUID(), nullable=False),
        sa.Column("event_id", sa.UUID(), nullable=False),
        sa.PrimaryKeyConstraint("space_id", "item_id", "event_id", name=op.f("pk_open_item_feedback_events")),
    )
    op.create_table(
        "open_items",
        sa.Column("space_id", sa.UUID(), nullable=False),
        sa.Column("origin_handle_id", sa.UUID(), nullable=False),
        sa.Column("item_id", sa.UUID(), nullable=False),
        sa.Column("about_handle_id", sa.UUID(), nullable=True),
        sa.Column("description_ct", sa.LargeBinary(), nullable=False),
        sa.Column("owner", sa.Text(), nullable=False),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("origin", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("object_type", sa.Text(), nullable=True),
        sa.Column("object_namespace", sa.Text(), nullable=True),
        sa.Column("object_external_id", sa.Text(), nullable=True),
        sa.Column("expected_operation", sa.Text(), nullable=True),
        sa.Column("closed_by_action_id", sa.UUID(), nullable=True),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("dek_version", sa.Integer(), nullable=False),
        sa.Column(
            "change_seq", sa.BigInteger(), server_default=sa.text("nextval('change_seq')"), nullable=False
        ),
        sa.Column("closed_by_event_id", sa.UUID(), nullable=True),
        sa.Column("purposes", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("embedding_ct", sa.LargeBinary(), nullable=True),
        sa.Column("embedding_model", sa.Text(), nullable=True),
        sa.Column("embedding_dim", sa.Integer(), nullable=True),
        sa.Column("embedding_dek_version", sa.Integer(), nullable=True),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("audience", sa.Text(), nullable=True),
        sa.CheckConstraint("owner IN ('company', 'customer')", name=op.f("ck_open_items_owner")),
        sa.CheckConstraint("status IN ('open', 'resolved', 'overdue')", name=op.f("ck_open_items_status")),
        sa.CheckConstraint("kind IN ('promise', 'dispute', 'request')", name=op.f("ck_open_items_kind")),
        sa.CheckConstraint("audience IN ('shared', 'internal')", name=op.f("ck_open_items_audience")),
        sa.PrimaryKeyConstraint("space_id", "origin_handle_id", "item_id", name=op.f("pk_open_items")),
        postgresql_partition_by="HASH (origin_handle_id)",
    )
    op.create_index(
        "ix_open_items_due",
        "open_items",
        ["due_at"],
        unique=False,
        postgresql_where=sa.text("status = 'open'"),
    )
    op.create_index(
        op.f("ix_open_items_space_id_change_seq"), "open_items", ["space_id", "change_seq"], unique=False
    )
    op.create_index(
        "ix_open_items_object",
        "open_items",
        ["space_id", "object_type", "object_namespace", "object_external_id"],
        unique=False,
        postgresql_where=sa.text("object_type IS NOT NULL"),
    )
    op.create_table(
        "outbox",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("space_id", sa.UUID(), nullable=False),
        sa.Column("task", sa.Text(), nullable=False),
        sa.Column("queue", sa.Text(), nullable=False),
        sa.Column("profile_id", sa.UUID(), nullable=True),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_outbox")),
    )
    op.create_index(op.f("ix_outbox_created_at"), "outbox", ["created_at"], unique=False)
    op.create_table(
        "org_parents",
        sa.Column("space_id", sa.UUID(), nullable=False),
        sa.Column("parent_link_id", sa.UUID(), nullable=False),
        sa.Column("org_handle_id", sa.UUID(), nullable=False),
        sa.Column("parent_handle_id", sa.UUID(), nullable=False),
        sa.Column("valid_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("valid_to", sa.DateTime(timezone=True), nullable=True),
        sa.Column("method", sa.Text(), nullable=False),
        sa.Column("confidence", sa.REAL(), nullable=False),
        sa.Column(
            "change_seq", sa.BigInteger(), server_default=sa.text("nextval('change_seq')"), nullable=False
        ),
        sa.PrimaryKeyConstraint("space_id", "parent_link_id", name=op.f("pk_org_parents")),
    )
    op.create_index(
        op.f("ix_org_parents_space_id_org_handle_id"),
        "org_parents",
        ["space_id", "org_handle_id"],
        unique=False,
        postgresql_where=sa.text("valid_to IS NULL"),
    )
    op.create_index(
        op.f("ix_org_parents_space_id_parent_handle_id"),
        "org_parents",
        ["space_id", "parent_handle_id"],
        unique=False,
        postgresql_where=sa.text("valid_to IS NULL"),
    )
    op.create_table(
        "outcome_links",
        sa.Column("space_id", sa.UUID(), nullable=False),
        sa.Column("link_id", sa.UUID(), nullable=False),
        sa.Column("outcome", sa.Text(), nullable=False),
        sa.Column("origin_handle_id", sa.UUID(), nullable=False),
        sa.Column("outcome_object_id", sa.UUID(), nullable=False),
        sa.Column("object_ref", sa.Text(), nullable=False),
        sa.Column("outcome_state", sa.Text(), nullable=False),
        sa.Column("line_ref", sa.Text(), nullable=True),
        sa.Column("turn_id", sa.Text(), nullable=True),
        sa.Column("action_id", sa.UUID(), nullable=True),
        sa.Column("exposure_id", sa.Text(), nullable=True),
        sa.Column("position", sa.SmallInteger(), nullable=True),
        sa.Column("handoff_id", sa.UUID(), nullable=True),
        sa.Column("agent", sa.Text(), nullable=True),
        sa.Column("method", sa.Text(), nullable=False),
        sa.Column("band", sa.Text(), nullable=False),
        sa.Column("confidence", sa.REAL(), nullable=True),
        sa.Column("finality", sa.Text(), nullable=False),
        sa.Column("value_minor", sa.BigInteger(), nullable=True),
        sa.Column("currency", sa.Text(), nullable=True),
        sa.Column("valid_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("known_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("final_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("order_ref", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "(method IN ('line', 'order')) = (band = 'deterministic')",
            name=op.f("ck_outcome_links_band_of_method"),
        ),
        sa.CheckConstraint("band IN ('deterministic', 'probable')", name=op.f("ck_outcome_links_band")),
        sa.CheckConstraint(
            "finality IN ('provisional', 'final', 'expired_without_outcome')",
            name=op.f("ck_outcome_links_finality"),
        ),
        sa.CheckConstraint(
            "method IN ('line', 'order', 'identity', 'assisted_handoff')",
            name=op.f("ck_outcome_links_method"),
        ),
        sa.CheckConstraint(
            "(value_minor IS NULL) = (currency IS NULL)", name=op.f("ck_outcome_links_value_has_currency")
        ),
        sa.CheckConstraint(
            "num_nonnulls(turn_id, action_id, exposure_id, handoff_id) > 0",
            name=op.f("ck_outcome_links_has_origin"),
        ),
        sa.PrimaryKeyConstraint("space_id", "link_id", name=op.f("pk_outcome_links")),
    )
    op.create_index(
        op.f("ix_outcome_links_space_id_final_at"),
        "outcome_links",
        ["space_id", "final_at"],
        unique=False,
        postgresql_where=sa.text("finality = 'provisional'"),
    )
    op.create_index(
        op.f("ix_outcome_links_space_id_known_at"), "outcome_links", ["space_id", "known_at"], unique=False
    )
    op.create_index(
        op.f("ix_outcome_links_space_id_origin_handle_id"),
        "outcome_links",
        ["space_id", "origin_handle_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_outcome_links_space_id_outcome_object_id"),
        "outcome_links",
        ["space_id", "outcome_object_id"],
        unique=False,
    )
    op.create_table(
        "ownership_claims",
        sa.Column("space_id", sa.UUID(), nullable=False),
        sa.Column("claim_id", sa.UUID(), nullable=False),
        sa.Column("target", sa.Text(), nullable=False),
        sa.Column("origin_handle_id", sa.UUID(), nullable=True),
        sa.Column("subject_hmac", sa.LargeBinary(), nullable=True),
        sa.Column("object_type", sa.Text(), nullable=True),
        sa.Column("object_namespace", sa.Text(), nullable=True),
        sa.Column("object_external_id", sa.Text(), nullable=True),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("task", sa.Text(), nullable=True),
        sa.Column("holder_kind", sa.Text(), nullable=False),
        sa.Column("holder", sa.Text(), nullable=False),
        sa.Column("level", sa.Text(), nullable=False),
        sa.Column("via", sa.Text(), nullable=False),
        sa.Column("source_id", sa.UUID(), nullable=False),
        sa.Column("observer", sa.Text(), nullable=True),
        sa.Column("basis_event_id", sa.UUID(), nullable=True),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("valid_until", sa.DateTime(timezone=True), nullable=False),
        sa.Column("intents", postgresql.ARRAY(sa.Text()), server_default=sa.text("'{}'"), nullable=False),
        sa.Column("epoch", sa.BigInteger(), nullable=False),
        sa.Column("released_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "(target = 'subject' AND (origin_handle_id IS NOT NULL OR subject_hmac IS NOT NULL)) OR (target = 'object' AND object_type IS NOT NULL AND object_external_id IS NOT NULL)",
            name=op.f("ck_ownership_claims_target_named"),
        ),
        sa.CheckConstraint(
            "holder_kind IN ('agent', 'human', 'queue', 'system')",
            name=op.f("ck_ownership_claims_holder_kind"),
        ),
        sa.CheckConstraint("kind IN ('owner', 'case', 'task_lock')", name=op.f("ck_ownership_claims_kind")),
        sa.CheckConstraint("target IN ('subject', 'object')", name=op.f("ck_ownership_claims_target")),
        sa.CheckConstraint(
            "via IN ('declaration', 'mapping', 'worker')", name=op.f("ck_ownership_claims_via")
        ),
        sa.PrimaryKeyConstraint("space_id", "claim_id", name=op.f("pk_ownership_claims")),
    )
    op.create_index(
        "ix_ownership_claims_object",
        "ownership_claims",
        ["space_id", "object_type", "object_namespace", "object_external_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_ownership_claims_space_id_origin_handle_id"),
        "ownership_claims",
        ["space_id", "origin_handle_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_ownership_claims_space_id_subject_hmac"),
        "ownership_claims",
        ["space_id", "subject_hmac"],
        unique=False,
    )
    op.create_index(
        op.f("ix_ownership_claims_space_id_valid_until"),
        "ownership_claims",
        ["space_id", "valid_until"],
        unique=False,
    )
    op.create_table(
        "pack_bundles",
        sa.Column("space_id", sa.UUID(), nullable=False),
        sa.Column("target", sa.Text(), nullable=False),
        sa.Column("generation", sa.BigInteger(), nullable=False),
        sa.Column("body_ct", sa.LargeBinary(), nullable=False),
        sa.Column("dek_version", sa.Integer(), nullable=False),
        sa.Column(
            "last_read",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("space_id", "target", name=op.f("pk_pack_bundles")),
    )
    op.create_table(
        "pack_manifests",
        sa.Column("space_id", sa.UUID(), nullable=False),
        sa.Column("manifest_hash", sa.Text(), nullable=False),
        sa.Column("manifest", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("profile_id", sa.UUID(), nullable=True),
        sa.PrimaryKeyConstraint("space_id", "manifest_hash", name=op.f("pk_pack_manifests")),
    )
    op.create_index(
        op.f("ix_pack_manifests_space_id_profile_id"),
        "pack_manifests",
        ["space_id", "profile_id"],
        unique=False,
    )
    op.create_table(
        "privacy_feed",
        sa.Column("space_id", sa.UUID(), nullable=False),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("profile_ids", postgresql.ARRAY(sa.UUID()), server_default=sa.text("'{}'"), nullable=False),
        sa.Column("handle_ids", postgresql.ARRAY(sa.UUID()), server_default=sa.text("'{}'"), nullable=False),
        sa.Column("privacy_version", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(
            "kind IN ('erasure', 'unmerge', 'merge', 'retraction', 'revocation')",
            name=op.f("ck_privacy_feed_kind"),
        ),
        sa.PrimaryKeyConstraint("space_id", "id", name=op.f("pk_privacy_feed")),
    )
    op.create_index(op.f("ix_privacy_feed_created_at"), "privacy_feed", ["created_at"], unique=False)
    op.create_table(
        "profile_attrs",
        sa.Column("space_id", sa.UUID(), nullable=False),
        sa.Column("origin_handle_id", sa.UUID(), nullable=False),
        sa.Column("slot", sa.Text(), nullable=False),
        sa.Column("source_id", sa.UUID(), nullable=False),
        sa.Column("value_ct", sa.LargeBinary(), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("times_seen", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("dek_version", sa.Integer(), nullable=False),
        sa.Column(
            "change_seq", sa.BigInteger(), server_default=sa.text("nextval('change_seq')"), nullable=False
        ),
        sa.PrimaryKeyConstraint(
            "space_id", "origin_handle_id", "slot", "source_id", name=op.f("pk_profile_attrs")
        ),
        postgresql_partition_by="HASH (origin_handle_id)",
    )
    op.create_table(
        "profile_links",
        sa.Column("space_id", sa.UUID(), nullable=False),
        sa.Column("link_id", sa.UUID(), nullable=False),
        sa.Column("person_handle_id", sa.UUID(), nullable=False),
        sa.Column("org_handle_id", sa.UUID(), nullable=False),
        sa.Column("role", sa.Text(), nullable=False),
        sa.Column("can_see_contacts", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("valid_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("valid_to", sa.DateTime(timezone=True), nullable=True),
        sa.Column("method", sa.Text(), nullable=False),
        sa.Column("origin_event_id", sa.UUID(), nullable=True),
        sa.Column("confidence", sa.REAL(), nullable=False),
        sa.Column(
            "change_seq", sa.BigInteger(), server_default=sa.text("nextval('change_seq')"), nullable=False
        ),
        sa.PrimaryKeyConstraint("space_id", "link_id", name=op.f("pk_profile_links")),
    )
    op.create_index(
        op.f("ix_profile_links_space_id_org_handle_id"),
        "profile_links",
        ["space_id", "org_handle_id"],
        unique=False,
        postgresql_where=sa.text("valid_to IS NULL"),
    )
    op.create_index(
        op.f("ix_profile_links_space_id_person_handle_id"),
        "profile_links",
        ["space_id", "person_handle_id"],
        unique=False,
        postgresql_where=sa.text("valid_to IS NULL"),
    )
    op.create_table(
        "profile_membership",
        sa.Column("space_id", sa.UUID(), nullable=False),
        sa.Column("handle_id", sa.UUID(), nullable=False),
        sa.Column("profile_id", sa.UUID(), nullable=False),
        sa.Column("version", sa.BigInteger(), nullable=False),
        sa.Column(
            "change_seq", sa.BigInteger(), server_default=sa.text("nextval('change_seq')"), nullable=False
        ),
        sa.PrimaryKeyConstraint("space_id", "handle_id", name=op.f("pk_profile_membership")),
    )
    op.create_index(
        op.f("ix_profile_membership_profile_id"), "profile_membership", ["profile_id"], unique=False
    )
    op.create_table(
        "profile_work",
        sa.Column("space_id", sa.UUID(), nullable=False),
        sa.Column("profile_id", sa.UUID(), nullable=False),
        sa.Column("task", sa.Text(), nullable=False),
        sa.Column("requested", sa.BigInteger(), server_default=sa.text("0"), nullable=False),
        sa.Column("done", sa.BigInteger(), server_default=sa.text("0"), nullable=False),
        sa.PrimaryKeyConstraint("space_id", "profile_id", "task", name=op.f("pk_profile_work")),
    )
    op.create_table(
        "profiles",
        sa.Column("space_id", sa.UUID(), nullable=False),
        sa.Column("profile_id", sa.UUID(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("org_type", sa.Text(), nullable=True),
        sa.Column("pseudonym", sa.Text(), nullable=False),
        sa.Column("membership_version", sa.BigInteger(), server_default=sa.text("1"), nullable=False),
        sa.Column("privacy_version", sa.BigInteger(), server_default=sa.text("1"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("retired_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("erased_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "change_seq", sa.BigInteger(), server_default=sa.text("nextval('change_seq')"), nullable=False
        ),
        sa.CheckConstraint("kind IN ('person', 'account', 'partner')", name=op.f("ck_profiles_kind")),
        sa.PrimaryKeyConstraint("space_id", "profile_id", name=op.f("pk_profiles")),
    )
    op.create_index(
        op.f("ix_profiles_space_id_pseudonym"), "profiles", ["space_id", "pseudonym"], unique=True
    )
    op.create_table(
        "purpose_objections",
        sa.Column("space_id", sa.UUID(), nullable=False),
        sa.Column("handle_id", sa.UUID(), nullable=False),
        sa.Column("purpose", sa.Text(), nullable=False),
        sa.Column("objected_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("space_id", "handle_id", "purpose", name=op.f("pk_purpose_objections")),
    )
    op.create_table(
        "queue_leases",
        sa.Column("space_id", sa.UUID(), nullable=False),
        sa.Column("unit_key", sa.Text(), nullable=False),
        sa.Column("owner", sa.Text(), nullable=False),
        sa.Column("epoch", sa.BigInteger(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("space_id", "unit_key", name=op.f("pk_queue_leases")),
    )
    op.create_table(
        "read_invalidations",
        sa.Column("space_id", sa.UUID(), nullable=False),
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("handle_ids", postgresql.ARRAY(sa.UUID()), server_default=sa.text("'{}'"), nullable=False),
        sa.Column("profile_ids", postgresql.ARRAY(sa.UUID()), server_default=sa.text("'{}'"), nullable=False),
        sa.Column("threads", postgresql.ARRAY(sa.Text()), server_default=sa.text("'{}'"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("space_id", "id", name=op.f("pk_read_invalidations")),
    )
    op.create_index(
        op.f("ix_read_invalidations_created_at"), "read_invalidations", ["created_at"], unique=False
    )
    op.create_table(
        "receipt_people",
        sa.Column("space_id", sa.UUID(), nullable=False),
        sa.Column("person_id", sa.UUID(), nullable=False),
        sa.Column("label", sa.Text(), nullable=False),
        sa.Column("named_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("space_id", "person_id", name=op.f("pk_receipt_people")),
    )
    op.create_table(
        "receipt_heads",
        sa.Column("space_id", sa.UUID(), nullable=False),
        sa.Column("slice", sa.SmallInteger(), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("seq", sa.BigInteger(), nullable=False),
        sa.Column("hash", sa.LargeBinary(), nullable=False),
        sa.PrimaryKeyConstraint("space_id", "slice", "day", name=op.f("pk_receipt_heads")),
    )
    op.create_table(
        "receipts",
        sa.Column("space_id", sa.UUID(), nullable=False),
        sa.Column("receipt_id", sa.UUID(), nullable=False),
        sa.Column("slice", sa.SmallInteger(), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("seq", sa.BigInteger(), nullable=False),
        sa.Column("producer_id", sa.Text(), nullable=False),
        sa.Column("producer_seq", sa.BigInteger(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("profile_id", sa.UUID(), nullable=True),
        sa.Column("source_id", sa.UUID(), nullable=True),
        sa.Column("person_id", sa.UUID(), nullable=True),
        sa.Column("conversation_id", sa.Text(), nullable=True),
        sa.Column("task_id", sa.Text(), nullable=True),
        sa.Column("object_id", sa.UUID(), nullable=True),
        sa.Column("about_handle_id", sa.UUID(), nullable=True),
        sa.Column("purpose", sa.Text(), nullable=True),
        sa.Column("audience", sa.Text(), nullable=False),
        sa.Column("surface", sa.Text(), nullable=False),
        sa.Column("item_ids", postgresql.ARRAY(sa.Text()), server_default=sa.text("'{}'"), nullable=False),
        sa.Column("pack_hash", sa.Text(), nullable=True),
        sa.Column("manifest_hash", sa.Text(), nullable=True),
        sa.Column("verification", sa.Text(), nullable=True),
        sa.Column("policy_version", sa.Text(), nullable=True),
        sa.Column("experiment_group", sa.Text(), nullable=True),
        sa.Column("query_hash", sa.LargeBinary(), nullable=True),
        sa.Column("withheld", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("detail", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("prev_hash", sa.LargeBinary(), nullable=False),
        sa.Column("hash", sa.LargeBinary(), nullable=False),
        sa.Column(
            "change_seq", sa.BigInteger(), server_default=sa.text("nextval('change_seq')"), nullable=False
        ),
        sa.PrimaryKeyConstraint("space_id", "receipt_id", name=op.f("pk_receipts")),
        postgresql_partition_by="RANGE (receipt_id)",
    )
    op.create_index("ix_receipts_chain", "receipts", ["space_id", "slice", "day", "seq"], unique=False)
    op.create_index(
        op.f("ix_receipts_space_id_conversation_id_read_at"),
        "receipts",
        ["space_id", "conversation_id", "read_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_receipts_space_id_profile_id_read_at"),
        "receipts",
        ["space_id", "profile_id", "read_at"],
        unique=False,
    )
    op.create_table(
        "refresh_requests",
        sa.Column("space_id", sa.UUID(), nullable=False),
        sa.Column("request_id", sa.UUID(), nullable=False),
        sa.Column("object_id", sa.UUID(), nullable=False),
        sa.Column("type", sa.Text(), nullable=False),
        sa.Column("namespace", sa.Text(), nullable=False),
        sa.Column("external_id", sa.Text(), nullable=False),
        sa.Column("variant", sa.Text(), server_default="", nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("priority", sa.Text(), nullable=False),
        sa.Column("budget_units", sa.Integer(), nullable=False),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lease_owner", sa.Text(), nullable=True),
        sa.Column("attempts", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("reservation", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.CheckConstraint(
            "priority IN ('high', 'normal', 'low')", name=op.f("ck_refresh_requests_priority")
        ),
        sa.PrimaryKeyConstraint("space_id", "request_id", name=op.f("pk_refresh_requests")),
        sa.UniqueConstraint(
            "space_id", "object_id", "reason", name=op.f("uq_refresh_requests_space_id_object_id_reason")
        ),
    )
    op.create_index(
        op.f("ix_refresh_requests_space_id_requested_at"),
        "refresh_requests",
        ["space_id", "requested_at"],
        unique=False,
    )
    op.create_table(
        "request_idempotency",
        sa.Column("space_id", sa.UUID(), nullable=False),
        sa.Column("idem_key", sa.Text(), nullable=False),
        sa.Column("body_hash", sa.LargeBinary(), nullable=False),
        sa.Column("status", sa.Integer(), nullable=False),
        sa.Column("response", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("space_id", "idem_key", name=op.f("pk_request_idempotency")),
    )
    op.create_table(
        "retrieval_weights",
        sa.Column("space_id", sa.UUID(), nullable=False),
        sa.Column("run_id", sa.UUID(), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("computed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("outcome", sa.Text(), nullable=False),
        sa.Column("weights", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("evidence", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("rules", sa.Text(), nullable=False),
        sa.CheckConstraint(
            "outcome IN ('applied', 'unchanged', 'no_verdicts', 'vetoed')",
            name=op.f("ck_retrieval_weights_outcome"),
        ),
        sa.PrimaryKeyConstraint("space_id", "run_id", name=op.f("pk_retrieval_weights")),
    )
    op.create_table(
        "review_requests",
        sa.Column("space_id", sa.UUID(), nullable=False),
        sa.Column("request_id", sa.UUID(), nullable=False),
        sa.Column("profile_id", sa.UUID(), nullable=False),
        sa.Column("decision_receipt_id", sa.UUID(), nullable=True),
        sa.Column("reason_ct", sa.LargeBinary(), nullable=False),
        sa.Column(
            "explanation",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'"),
            nullable=False,
        ),
        sa.Column("status", sa.Text(), server_default="open", nullable=False),
        sa.Column("outcome", sa.Text(), nullable=True),
        sa.Column("resolution_ct", sa.LargeBinary(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_by", sa.Text(), nullable=True),
        sa.Column("dek_version", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "(status = 'resolved') = (outcome IS NOT NULL AND resolved_at IS NOT NULL)",
            name=op.f("ck_review_requests_resolved"),
        ),
        sa.CheckConstraint(
            "outcome IN ('upheld', 'reversed', 'corrected')", name=op.f("ck_review_requests_outcome")
        ),
        sa.CheckConstraint("status IN ('open', 'resolved')", name=op.f("ck_review_requests_status")),
        sa.PrimaryKeyConstraint("space_id", "request_id", name=op.f("pk_review_requests")),
    )
    op.create_index(
        op.f("ix_review_requests_space_id_profile_id"),
        "review_requests",
        ["space_id", "profile_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_review_requests_space_id_status_created_at"),
        "review_requests",
        ["space_id", "status", "created_at"],
        unique=False,
    )
    op.create_table(
        "review_samples",
        sa.Column("space_id", sa.UUID(), nullable=False),
        sa.Column("sample_id", sa.UUID(), nullable=False),
        sa.Column("session_id", sa.UUID(), nullable=False),
        sa.Column("profile_id", sa.UUID(), nullable=True),
        sa.Column("conversation_id", sa.Text(), nullable=True),
        sa.Column("condition", sa.Text(), nullable=False),
        sa.Column("purpose", sa.Text(), nullable=True),
        sa.Column(
            "verdicts",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'"),
            nullable=False,
        ),
        sa.Column("reviewer_id", sa.UUID(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("space_id", "sample_id", name=op.f("pk_review_samples")),
    )
    op.create_table(
        "scenario_runs",
        sa.Column("space_id", sa.UUID(), nullable=False),
        sa.Column("run_id", sa.UUID(), nullable=False),
        sa.Column("scenario_ids", postgresql.ARRAY(sa.UUID()), nullable=False),
        sa.Column("build", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("verdict", sa.Text(), nullable=True),
        sa.Column(
            "summary",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("mode", sa.Text(), nullable=False),
        sa.Column("vary", postgresql.ARRAY(sa.Text()), server_default=sa.text("'{}'"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "mode IN ('hermetic_turn', 'hermetic_conversation', 'era_memory')",
            name=op.f("ck_scenario_runs_mode"),
        ),
        sa.CheckConstraint("status IN ('pending', 'done')", name=op.f("ck_scenario_runs_status")),
        sa.CheckConstraint(
            "verdict IS NULL OR verdict IN ('pass', 'flaky', 'infrastructure_error', 'pin_mismatch', 'regression')",
            name=op.f("ck_scenario_runs_verdict"),
        ),
        sa.PrimaryKeyConstraint("space_id", "run_id", name=op.f("pk_scenario_runs")),
    )
    op.create_index(
        op.f("ix_scenario_runs_space_id_created_at"),
        "scenario_runs",
        ["space_id", "created_at"],
        unique=False,
    )
    op.create_table(
        "scenarios",
        sa.Column("space_id", sa.UUID(), nullable=False),
        sa.Column("scenario_id", sa.UUID(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("origin", sa.Text(), nullable=False),
        sa.Column("turn_ids", postgresql.ARRAY(sa.Text()), nullable=False),
        sa.Column("assertions", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("origin IN ('manual', 'report')", name=op.f("ck_scenarios_origin")),
        sa.CheckConstraint("status IN ('active', 'retired')", name=op.f("ck_scenarios_status")),
        sa.PrimaryKeyConstraint("space_id", "scenario_id", name=op.f("pk_scenarios")),
    )
    op.create_index(
        op.f("ix_scenarios_space_id_status_created_at"),
        "scenarios",
        ["space_id", "status", "created_at"],
        unique=False,
    )
    op.create_table(
        "scheduled_tasks",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("space_id", sa.UUID(), nullable=False),
        sa.Column("task", sa.Text(), nullable=False),
        sa.Column("queue", sa.Text(), nullable=False),
        sa.Column("profile_id", sa.UUID(), nullable=True),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.CheckConstraint(
            "reason IN ('retry', 'lease_busy', 'space_busy', 'safety_net', 'llm_retry', 'llm_budget', 'reextract', 'rate_limited', 'valid_until', 'receipt_watermark', 'transfer_window', 'export_settle', 'delay', 'batch_window')",
            name=op.f("ck_scheduled_tasks_reason"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_scheduled_tasks")),
    )
    op.create_index(op.f("ix_scheduled_tasks_due_at"), "scheduled_tasks", ["due_at"], unique=False)
    op.create_table(
        "schedules",
        sa.Column("space_id", sa.UUID(), nullable=False),
        sa.Column("task", sa.Text(), nullable=False),
        sa.Column("key", sa.Text(), server_default="", nullable=False),
        sa.Column("next_run", sa.DateTime(timezone=True), nullable=False),
        sa.Column("timezone", sa.Text(), server_default="UTC", nullable=False),
        sa.Column("every_seconds", sa.Integer(), nullable=False),
        sa.Column(
            "payload", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'"), nullable=False
        ),
        sa.PrimaryKeyConstraint("space_id", "task", "key", name=op.f("pk_schedules")),
    )
    op.create_table(
        "session_ends",
        sa.Column("space_id", sa.UUID(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("thread_id", sa.Text(), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("kind IN ('conversation', 'task')", name=op.f("ck_session_ends_kind")),
        sa.PrimaryKeyConstraint("space_id", "kind", "thread_id", name=op.f("pk_session_ends")),
    )
    op.create_index(op.f("ix_session_ends_expires_at"), "session_ends", ["expires_at"], unique=False)
    op.create_table(
        "sessions",
        sa.Column("space_id", sa.UUID(), nullable=False),
        sa.Column("session_id", sa.UUID(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("conversation_id", sa.Text(), nullable=True),
        sa.Column("task_id", sa.Text(), nullable=True),
        sa.Column("source_id", sa.UUID(), nullable=False),
        sa.Column("channel", sa.Text(), nullable=False),
        sa.Column("profile_id", sa.UUID(), nullable=True),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_event_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("close_reason", sa.Text(), nullable=True),
        sa.Column("proven_level", sa.Text(), nullable=True),
        sa.Column("proven_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("initiated_by", sa.Text(), nullable=True),
        sa.Column("extracted_version", sa.Text(), nullable=True),
        sa.Column(
            "change_seq", sa.BigInteger(), server_default=sa.text("nextval('change_seq')"), nullable=False
        ),
        sa.Column("reopened", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("kind IN ('conversation', 'task')", name=op.f("ck_sessions_kind")),
        sa.PrimaryKeyConstraint("space_id", "session_id", name=op.f("pk_sessions")),
    )
    op.create_index(
        "ix_sessions_open",
        "sessions",
        ["last_event_at"],
        unique=False,
        postgresql_where=sa.text("closed_at IS NULL"),
    )
    op.create_index(
        op.f("ix_sessions_space_id_conversation_id"),
        "sessions",
        ["space_id", "conversation_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_sessions_space_id_profile_id_opened_at"),
        "sessions",
        ["space_id", "profile_id", "opened_at"],
        unique=False,
    )
    op.create_index(
        "uq_sessions_open_conversation",
        "sessions",
        ["space_id", "conversation_id"],
        unique=True,
        postgresql_where=sa.text("closed_at IS NULL AND conversation_id IS NOT NULL"),
    )
    op.create_index(
        "uq_sessions_open_task",
        "sessions",
        ["space_id", "task_id"],
        unique=True,
        postgresql_where=sa.text("closed_at IS NULL AND task_id IS NOT NULL"),
    )
    op.create_table(
        "settled_values",
        sa.Column("space_id", sa.UUID(), nullable=False),
        sa.Column("origin_handle_id", sa.UUID(), nullable=False),
        sa.Column("value_id", sa.UUID(), nullable=False),
        sa.Column("type", sa.Text(), nullable=False),
        sa.Column("raw_ct", sa.LargeBinary(), nullable=False),
        sa.Column("value_hmac", sa.LargeBinary(), nullable=False),
        sa.Column("said_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("speaker", sa.Text(), nullable=False),
        sa.Column("session_id", sa.UUID(), nullable=False),
        sa.Column("conversation_id", sa.Text(), nullable=True),
        sa.Column("channel", sa.Text(), nullable=False),
        sa.Column("source_id", sa.UUID(), nullable=False),
        sa.Column("episode_id", sa.UUID(), nullable=True),
        sa.Column("object_ref", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "evidence_event_ids", postgresql.ARRAY(sa.UUID()), server_default=sa.text("'{}'"), nullable=False
        ),
        sa.Column("origin", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("dek_version", sa.Integer(), nullable=False),
        sa.Column("purposes", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.CheckConstraint("origin IN ('extraction', 'rule')", name=op.f("ck_settled_values_origin")),
        sa.CheckConstraint(
            "type IN ('protocol', 'ticket', 'order', 'record', 'receipt', 'postal_code', 'amount', 'date', 'code')",
            name=op.f("ck_settled_values_type"),
        ),
        sa.PrimaryKeyConstraint("space_id", "origin_handle_id", "value_id", name=op.f("pk_settled_values")),
        postgresql_partition_by="HASH (origin_handle_id)",
    )
    op.create_index(
        op.f("ix_settled_values_evidence_event_ids"),
        "settled_values",
        ["evidence_event_ids"],
        unique=False,
        postgresql_using="gin",
    )
    op.create_index(
        op.f("ix_settled_values_space_id_origin_handle_id_type_said_at"),
        "settled_values",
        ["space_id", "origin_handle_id", "type", "said_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_settled_values_space_id_origin_handle_id_value_hmac"),
        "settled_values",
        ["space_id", "origin_handle_id", "value_hmac"],
        unique=False,
    )
    op.create_table(
        "shared_objects",
        sa.Column("space_id", sa.UUID(), nullable=False),
        sa.Column("object_id", sa.UUID(), nullable=False),
        sa.Column("type", sa.Text(), nullable=False),
        sa.Column("namespace", sa.Text(), nullable=False),
        sa.Column("external_id", sa.Text(), nullable=False),
        sa.Column("variant", sa.Text(), server_default="", nullable=False),
        sa.Column(
            "state_ct",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'"),
            nullable=False,
        ),
        sa.Column(
            "field_meta",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'"),
            nullable=False,
        ),
        sa.Column("version", sa.BigInteger(), server_default=sa.text("0"), nullable=False),
        sa.Column("inputs_hash", sa.LargeBinary(), nullable=True),
        sa.Column("derived_status", sa.Text(), nullable=True),
        sa.Column("last_referenced_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("hot_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("watch_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column(
            "change_seq", sa.BigInteger(), server_default=sa.text("nextval('change_seq')"), nullable=False
        ),
        sa.CheckConstraint(
            "derived_status IN ('current', 'expired_by_input')", name=op.f("ck_shared_objects_derived_status")
        ),
        sa.PrimaryKeyConstraint("space_id", "object_id", name=op.f("pk_shared_objects")),
        sa.UniqueConstraint(
            "space_id",
            "type",
            "namespace",
            "external_id",
            "variant",
            name=op.f("uq_shared_objects_space_id_type_namespace_external_id_variant"),
        ),
    )
    op.create_index(
        op.f("ix_shared_objects_space_id_change_seq"),
        "shared_objects",
        ["space_id", "change_seq"],
        unique=False,
    )
    op.create_index(
        op.f("ix_shared_objects_space_id_last_referenced_at"),
        "shared_objects",
        ["space_id", "last_referenced_at"],
        unique=False,
    )
    op.create_table(
        "signal_intake",
        sa.Column("space_id", sa.UUID(), nullable=False),
        sa.Column("entry_key", sa.Text(), nullable=False),
        sa.Column("origin_handle_id", sa.UUID(), nullable=True),
        sa.Column("session", sa.Text(), nullable=True),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("body_ct", sa.LargeBinary(), nullable=False),
        sa.Column("dek_version", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("space_id", "entry_key", name=op.f("pk_signal_intake")),
    )
    op.create_index(
        op.f("ix_signal_intake_space_id_origin_handle_id_received_at"),
        "signal_intake",
        ["space_id", "origin_handle_id", "received_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_signal_intake_space_id_received_at"),
        "signal_intake",
        ["space_id", "received_at"],
        unique=False,
    )
    op.create_table(
        "signal_priors",
        sa.Column("space_id", sa.UUID(), nullable=False),
        sa.Column("segment", sa.Text(), nullable=False),
        sa.Column("attr", sa.Text(), nullable=False),
        sa.Column("value", sa.Text(), nullable=False),
        sa.Column("prior", sa.REAL(), nullable=False),
        sa.Column("exposures", sa.BigInteger(), nullable=False),
        sa.Column("signals", sa.REAL(), nullable=False),
        sa.Column("computed_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("space_id", "segment", "attr", "value", name=op.f("pk_signal_priors")),
    )
    op.create_table(
        "source_heartbeats",
        sa.Column("space_id", sa.UUID(), nullable=False),
        sa.Column("source_id", sa.UUID(), nullable=False),
        sa.Column("window_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("sent", sa.BigInteger(), server_default=sa.text("0"), nullable=False),
        sa.Column("received", sa.BigInteger(), server_default=sa.text("0"), nullable=False),
        sa.Column("rejected", sa.BigInteger(), server_default=sa.text("0"), nullable=False),
        sa.Column("unmapped", sa.BigInteger(), server_default=sa.text("0"), nullable=False),
        sa.Column("last_event_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("space_id", "source_id", "window_start", name=op.f("pk_source_heartbeats")),
    )
    op.create_table(
        "space_identity",
        sa.Column("one", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("space_id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("region", sa.Text(), nullable=False),
        sa.Column("environment", sa.Text(), nullable=False),
        sa.Column("cell_id", sa.Text(), nullable=False),
        sa.Column("hash_modulus", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("one", name=op.f("ck_space_identity_one")),
        sa.PrimaryKeyConstraint("one", name=op.f("pk_space_identity")),
    )
    op.create_table(
        "space_state",
        sa.Column("key", sa.Text(), nullable=False),
        sa.Column("value", sa.BigInteger(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("key", name=op.f("pk_space_state")),
    )
    op.create_table(
        "subject_signals",
        sa.Column("space_id", sa.UUID(), nullable=False),
        sa.Column("origin_handle_id", sa.UUID(), nullable=False),
        sa.Column("beneficiary_id", sa.UUID(), nullable=False),
        sa.Column("body_ct", sa.LargeBinary(), nullable=False),
        sa.Column("body_version", sa.SmallInteger(), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("derived_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_activity_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("dek_version", sa.Integer(), nullable=False),
        sa.Column(
            "change_seq", sa.BigInteger(), server_default=sa.text("nextval('change_seq')"), nullable=False
        ),
        sa.Column("opted_out_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("size_bytes <= 16384", name=op.f("ck_subject_signals_size_bytes")),
        sa.PrimaryKeyConstraint(
            "space_id", "origin_handle_id", "beneficiary_id", name=op.f("pk_subject_signals")
        ),
        postgresql_partition_by="HASH (origin_handle_id)",
    )
    op.create_index(
        op.f("ix_subject_signals_space_id_change_seq"),
        "subject_signals",
        ["space_id", "change_seq"],
        unique=False,
    )
    op.create_index(
        op.f("ix_subject_signals_space_id_last_activity_at"),
        "subject_signals",
        ["space_id", "last_activity_at"],
        unique=False,
    )
    op.create_table(
        "suppressions",
        sa.Column("space_id", sa.UUID(), nullable=False),
        sa.Column("suppression_id", sa.UUID(), nullable=False),
        sa.Column("origin_handle_id", sa.UUID(), nullable=True),
        sa.Column("handle_type", sa.Text(), nullable=False),
        sa.Column("handle_hmac", sa.LargeBinary(), nullable=False),
        sa.Column("handle_ct", sa.LargeBinary(), nullable=False),
        sa.Column("dek_version", sa.Integer(), nullable=False),
        sa.Column("purpose", sa.Text(), nullable=False),
        sa.Column("channel", sa.Text(), nullable=True),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("source_id", sa.UUID(), nullable=True),
        sa.Column("since", sa.DateTime(timezone=True), nullable=False),
        sa.Column("until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resume_after_s", sa.Integer(), nullable=True),
        sa.Column("lifted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "change_seq", sa.BigInteger(), server_default=sa.text("nextval('change_seq')"), nullable=False
        ),
        sa.PrimaryKeyConstraint("space_id", "suppression_id", name=op.f("pk_suppressions")),
    )
    op.create_index(
        op.f("ix_suppressions_space_id_change_seq"), "suppressions", ["space_id", "change_seq"], unique=False
    )
    op.create_index(
        op.f("ix_suppressions_space_id_handle_type_handle_hmac_purpose"),
        "suppressions",
        ["space_id", "handle_type", "handle_hmac", "purpose"],
        unique=False,
    )
    op.create_index(
        op.f("ix_suppressions_space_id_origin_handle_id"),
        "suppressions",
        ["space_id", "origin_handle_id"],
        unique=False,
    )
    op.create_table(
        "tail_summaries",
        sa.Column("space_id", sa.UUID(), nullable=False),
        sa.Column("profile_id", sa.UUID(), nullable=False),
        sa.Column("level", sa.Text(), nullable=False),
        sa.Column("membership_version", sa.BigInteger(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("text_ct", sa.LargeBinary(), nullable=False),
        sa.Column("covers_until", sa.DateTime(timezone=True), nullable=False),
        sa.Column("dek_version", sa.Integer(), nullable=False),
        sa.CheckConstraint("level IN ('short', 'long')", name=op.f("ck_tail_summaries_level")),
        sa.PrimaryKeyConstraint("space_id", "profile_id", "level", name=op.f("pk_tail_summaries")),
    )
    op.create_table(
        "tenant_rollups",
        sa.Column("space_id", sa.UUID(), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("dimension", sa.Text(), nullable=False),
        sa.Column("bucket", sa.Text(), nullable=False),
        sa.Column("value", sa.BigInteger(), nullable=False),
        sa.PrimaryKeyConstraint("space_id", "day", "dimension", "bucket", name=op.f("pk_tenant_rollups")),
    )
    op.create_table(
        "tombstones",
        sa.Column("space_id", sa.UUID(), nullable=False),
        sa.Column(
            "change_seq", sa.BigInteger(), server_default=sa.text("nextval('change_seq')"), nullable=False
        ),
        sa.Column("table_name", sa.Text(), nullable=False),
        sa.Column("row_key", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("space_id", "change_seq", name=op.f("pk_tombstones")),
    )
    op.create_table(
        "trait_evidence",
        sa.Column("space_id", sa.UUID(), nullable=False),
        sa.Column("trait_id", sa.UUID(), nullable=False),
        sa.Column("origin_handle_id", sa.UUID(), nullable=False),
        sa.Column("evidence_kind", sa.Text(), nullable=False),
        sa.Column("evidence_id", sa.UUID(), nullable=False),
        sa.Column(
            "change_seq", sa.BigInteger(), server_default=sa.text("nextval('change_seq')"), nullable=False
        ),
        sa.CheckConstraint(
            "evidence_kind IN ('episode', 'event', 'action', 'open_item')",
            name=op.f("ck_trait_evidence_evidence_kind"),
        ),
        sa.PrimaryKeyConstraint(
            "space_id",
            "trait_id",
            "origin_handle_id",
            "evidence_kind",
            "evidence_id",
            name=op.f("pk_trait_evidence"),
        ),
    )
    op.create_index(
        op.f("ix_trait_evidence_space_id_change_seq"),
        "trait_evidence",
        ["space_id", "change_seq"],
        unique=False,
    )
    op.create_index(
        op.f("ix_trait_evidence_space_id_evidence_id"),
        "trait_evidence",
        ["space_id", "evidence_id"],
        unique=False,
    )
    op.create_table(
        "trait_opt_outs",
        sa.Column("space_id", sa.UUID(), nullable=False),
        sa.Column("handle_id", sa.UUID(), nullable=False),
        sa.Column("opted_out_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("space_id", "handle_id", name=op.f("pk_trait_opt_outs")),
    )
    op.create_table(
        "traits",
        sa.Column("space_id", sa.UUID(), nullable=False),
        sa.Column("trait_id", sa.UUID(), nullable=False),
        sa.Column("profile_id", sa.UUID(), nullable=False),
        sa.Column("membership_version", sa.BigInteger(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("value_ct", sa.LargeBinary(), nullable=False),
        sa.Column("layer", sa.Text(), nullable=False),
        sa.Column("rule_version", sa.Text(), nullable=False),
        sa.Column("first_seen", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_evidence_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("confidence", sa.REAL(), nullable=False),
        sa.Column("retracted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("dek_version", sa.Integer(), nullable=False),
        sa.Column(
            "change_seq", sa.BigInteger(), server_default=sa.text("nextval('change_seq')"), nullable=False
        ),
        sa.Column("embedding_ct", sa.LargeBinary(), nullable=True),
        sa.Column("embedding_model", sa.Text(), nullable=True),
        sa.Column("embedding_dim", sa.Integer(), nullable=True),
        sa.Column("embedding_dek_version", sa.Integer(), nullable=True),
        sa.CheckConstraint("layer IN ('stable', 'volatile')", name=op.f("ck_traits_layer")),
        sa.PrimaryKeyConstraint("space_id", "trait_id", name=op.f("pk_traits")),
        sa.UniqueConstraint(
            "space_id", "profile_id", "name", name=op.f("uq_traits_space_id_profile_id_name")
        ),
    )
    op.create_index(
        "ix_traits_expiring",
        "traits",
        ["expires_at"],
        unique=False,
        postgresql_where=sa.text("retracted_at IS NULL"),
    )
    op.create_index(op.f("ix_traits_space_id_change_seq"), "traits", ["space_id", "change_seq"], unique=False)
    op.create_table(
        "trigger_firings",
        sa.Column("space_id", sa.UUID(), nullable=False),
        sa.Column("firing_id", sa.UUID(), nullable=False),
        sa.Column("rule_id", sa.Text(), nullable=False),
        sa.Column("rule_version", sa.Text(), nullable=False),
        sa.Column("profile_id", sa.UUID(), nullable=False),
        sa.Column("window_key", sa.Text(), nullable=False),
        sa.Column("idempotency_key", sa.LargeBinary(), nullable=False),
        sa.Column("evidence", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("membership_version", sa.BigInteger(), nullable=False),
        sa.Column("late", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("retracted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("fired_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "change_seq", sa.BigInteger(), server_default=sa.text("nextval('change_seq')"), nullable=False
        ),
        sa.PrimaryKeyConstraint("space_id", "firing_id", name=op.f("pk_trigger_firings")),
        sa.UniqueConstraint(
            "space_id",
            "rule_id",
            "profile_id",
            "window_key",
            name=op.f("uq_trigger_firings_space_id_rule_id_profile_id_window_key"),
        ),
    )
    op.create_index(
        op.f("ix_trigger_firings_space_id_fired_at"),
        "trigger_firings",
        ["space_id", "fired_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_trigger_firings_space_id_profile_id_fired_at"),
        "trigger_firings",
        ["space_id", "profile_id", "fired_at"],
        unique=False,
    )
    op.create_table(
        "turn_daily",
        sa.Column("space_id", sa.UUID(), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("source_id", sa.UUID(), nullable=False),
        sa.Column("agent", sa.Text(), nullable=False),
        sa.Column("build_hash", sa.LargeBinary(), nullable=False),
        sa.Column("turns", sa.BigInteger(), server_default=sa.text("0"), nullable=False),
        sa.Column("incomplete", sa.BigInteger(), server_default=sa.text("0"), nullable=False),
        sa.Column("tool_calls", sa.BigInteger(), server_default=sa.text("0"), nullable=False),
        sa.Column("model_calls", sa.BigInteger(), server_default=sa.text("0"), nullable=False),
        sa.Column("errors", sa.BigInteger(), server_default=sa.text("0"), nullable=False),
        sa.Column("synthetic", sa.BigInteger(), server_default=sa.text("0"), nullable=False),
        sa.Column("cache_hits", sa.BigInteger(), server_default=sa.text("0"), nullable=False),
        sa.Column("tokens_in", sa.BigInteger(), server_default=sa.text("0"), nullable=False),
        sa.Column("tokens_cached", sa.BigInteger(), server_default=sa.text("0"), nullable=False),
        sa.Column("tokens_out", sa.BigInteger(), server_default=sa.text("0"), nullable=False),
        sa.Column("cost_micros", sa.BigInteger(), server_default=sa.text("0"), nullable=False),
        sa.Column(
            "latency_buckets",
            postgresql.ARRAY(sa.BigInteger()),
            server_default=sa.text("'{}'"),
            nullable=False,
        ),
        sa.Column(
            "claims",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("constraints_sent", sa.BigInteger(), server_default=sa.text("0"), nullable=False),
        sa.Column("results_checked", sa.BigInteger(), server_default=sa.text("0"), nullable=False),
        sa.Column("violations", sa.BigInteger(), server_default=sa.text("0"), nullable=False),
        sa.Column("unverifiable", sa.BigInteger(), server_default=sa.text("0"), nullable=False),
        sa.Column("relaxed_declared", sa.BigInteger(), server_default=sa.text("0"), nullable=False),
        sa.Column("relaxed_suspected", sa.BigInteger(), server_default=sa.text("0"), nullable=False),
        sa.Column("effects", sa.BigInteger(), server_default=sa.text("0"), nullable=False),
        sa.Column("items_presented", sa.BigInteger(), server_default=sa.text("0"), nullable=False),
        sa.PrimaryKeyConstraint(
            "space_id", "day", "source_id", "agent", "build_hash", name=op.f("pk_turn_daily")
        ),
    )
    op.create_table(
        "turn_intake",
        sa.Column("space_id", sa.UUID(), nullable=False),
        sa.Column("intake_id", sa.UUID(), nullable=False),
        sa.Column("seq", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source_id", sa.UUID(), nullable=False),
        sa.Column("turns", sa.Integer(), nullable=False),
        sa.Column("body_ct", sa.LargeBinary(), nullable=False),
        sa.Column("dek_version", sa.Integer(), nullable=False),
        sa.Column("attempts", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.PrimaryKeyConstraint("space_id", "intake_id", name=op.f("pk_turn_intake")),
    )
    op.create_table(
        "turn_tombstones",
        sa.Column("space_id", sa.UUID(), nullable=False),
        sa.Column("mark", sa.Text(), nullable=False),
        sa.Column("until", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("space_id", "mark", name=op.f("pk_turn_tombstones")),
    )
    op.create_table(
        "turns_kept",
        sa.Column("space_id", sa.UUID(), nullable=False),
        sa.Column("turn_id", sa.Text(), nullable=False),
        sa.Column("conversation_hmac", sa.LargeBinary(), nullable=False),
        sa.Column("source_id", sa.UUID(), nullable=False),
        sa.Column("agent", sa.Text(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("fidelity", sa.Text(), nullable=False),
        sa.Column("completeness", sa.Text(), nullable=False),
        sa.Column("content_mode", sa.Text(), nullable=False),
        sa.Column("replayable", sa.Boolean(), nullable=False),
        sa.Column("build_hash", sa.LargeBinary(), nullable=True),
        sa.Column("flags", postgresql.ARRAY(sa.Text()), server_default=sa.text("'{}'"), nullable=False),
        sa.Column("kept_by", sa.Text(), nullable=False),
        sa.Column("kept_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("kept_until", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "completeness IN ('complete', 'partial', 'incomplete', 'unknown')",
            name=op.f("ck_turns_kept_completeness"),
        ),
        sa.CheckConstraint(
            "content_mode IN ('stored', 'pointer', 'hash_only')", name=op.f("ck_turns_kept_content_mode")
        ),
        sa.CheckConstraint("fidelity IN ('bronze', 'silver', 'gold')", name=op.f("ck_turns_kept_fidelity")),
        sa.CheckConstraint(
            "kept_by IN ('flag', 'request', 'sample', 'scenario')", name=op.f("ck_turns_kept_kept_by")
        ),
        sa.CheckConstraint(
            "kind IN ('message', 'action', 'event', 'timer')", name=op.f("ck_turns_kept_kind")
        ),
        sa.PrimaryKeyConstraint("space_id", "turn_id", name=op.f("pk_turns_kept")),
    )
    op.create_index(
        op.f("ix_turns_kept_space_id_conversation_hmac_started_at"),
        "turns_kept",
        ["space_id", "conversation_hmac", "started_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_turns_kept_space_id_kept_at"), "turns_kept", ["space_id", "kept_at"], unique=False
    )
    op.create_index(
        op.f("ix_turns_kept_space_id_kept_until"), "turns_kept", ["space_id", "kept_until"], unique=False
    )
    op.create_index(
        op.f("ix_turns_kept_space_id_source_id_started_at"),
        "turns_kept",
        ["space_id", "source_id", "started_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_turns_kept_space_id_started_at"), "turns_kept", ["space_id", "started_at"], unique=False
    )
    op.create_table(
        "usage_events",
        sa.Column("space_id", sa.UUID(), nullable=False),
        sa.Column("usage_id", sa.Text(), nullable=False),
        sa.Column("metric", sa.Text(), nullable=False),
        sa.Column("quantity", sa.BigInteger(), nullable=False),
        sa.Column("cost_micros", sa.BigInteger(), server_default=sa.text("0"), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("space_id", "usage_id", name=op.f("pk_usage_events")),
    )
    op.create_table(
        "usage_hourly",
        sa.Column("space_id", sa.UUID(), nullable=False),
        sa.Column("hour", sa.DateTime(timezone=True), nullable=False),
        sa.Column("metric", sa.Text(), nullable=False),
        sa.Column(
            "source_id",
            sa.UUID(),
            server_default=sa.text("'00000000-0000-0000-0000-000000000000'"),
            nullable=False,
        ),
        sa.Column("quantity", sa.BigInteger(), nullable=False),
        sa.Column("cost_micros", sa.BigInteger(), server_default=sa.text("0"), nullable=False),
        sa.PrimaryKeyConstraint("space_id", "hour", "metric", "source_id", name=op.f("pk_usage_hourly")),
    )
    op.create_table(
        "view_budgets",
        sa.Column("space_id", sa.UUID(), nullable=False),
        sa.Column("run_id", sa.UUID(), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("computed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("outcome", sa.Text(), nullable=False),
        sa.Column("factors", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("evidence", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("rules", sa.Text(), nullable=False),
        sa.CheckConstraint(
            "outcome IN ('applied', 'unchanged', 'no_verdicts', 'vetoed')",
            name=op.f("ck_view_budgets_outcome"),
        ),
        sa.PrimaryKeyConstraint("space_id", "run_id", name=op.f("pk_view_budgets")),
    )
    op.create_table(
        "webhook_deliveries",
        sa.Column("space_id", sa.UUID(), nullable=False),
        sa.Column("delivery_id", sa.UUID(), nullable=False),
        sa.Column("endpoint_id", sa.Text(), nullable=False),
        sa.Column("event_type", sa.Text(), nullable=False),
        sa.Column("body", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("body_hash", sa.LargeBinary(), nullable=False),
        sa.Column("attempts", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("last_status_code", sa.Integer(), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('pending', 'delivered', 'failing', 'dead')", name=op.f("ck_webhook_deliveries_status")
        ),
        sa.PrimaryKeyConstraint("space_id", "delivery_id", name=op.f("pk_webhook_deliveries")),
    )
    op.create_index(
        op.f("ix_webhook_deliveries_space_id_created_at"),
        "webhook_deliveries",
        ["space_id", "created_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_webhook_deliveries_space_id_endpoint_id_created_at"),
        "webhook_deliveries",
        ["space_id", "endpoint_id", "created_at"],
        unique=False,
    )
    hashed = ", ".join(f"'{t}'" for t in HASH_PARTITIONED)
    op.execute(f"SELECT create_hash_partitions(t, {modulus}) FROM unnest(ARRAY[{hashed}]) AS t")
    dedupe = ", ".join(f"'{t}'" for t in DEDUPE_TABLES)
    op.execute(f"SELECT create_hash_partitions(t, 8) FROM unnest(ARRAY[{dedupe}]) AS t")
    for table in DEFAULT_PARTITIONED:
        op.execute(f"CREATE TABLE {table}_default PARTITION OF {table} DEFAULT")
    # The contact log's DEFAULT partition and this week's; the partition upkeep adds the weeks ahead.
    op.execute(
        "SELECT ensure_week_partition('contact_log', "
        "(floor(extract(epoch FROM now()) * 1000 / 604800000) * 604800000)::bigint)"
    )
    for table in CHANGE_SEQ_TABLES:
        op.execute(
            f"CREATE TRIGGER {table}_change_seq BEFORE UPDATE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION bump_change_seq()"
        )
    op.execute(READ_INVALIDATION_TRIGGERS)
    for table in PINNED_TABLES:
        op.execute(f"SELECT enable_space_rls('{table}')")
    op.execute(
        "ALTER TABLE outbox SET (autovacuum_vacuum_scale_factor = 0.01, autovacuum_vacuum_cost_delay = 0)"
    )
    for table in CHURNING_TABLES:
        op.execute(
            f"ALTER TABLE {table} SET (fillfactor = 70, autovacuum_vacuum_scale_factor = 0.02, "
            "autovacuum_analyze_scale_factor = 0.05)"
        )
    for table in VACUUMED_TABLES:
        op.execute(
            f"ALTER TABLE {table} SET (autovacuum_vacuum_scale_factor = 0.02, autovacuum_analyze_scale_factor = 0.05)"
        )
    for table in DEDUPE_TABLES:
        op.execute(
            "DO $$ DECLARE part regclass; BEGIN FOR part IN SELECT inhrelid::regclass FROM pg_inherits "
            f"WHERE inhparent = '{table}'::regclass LOOP EXECUTE format('ALTER TABLE %s SET "
            "(autovacuum_vacuum_scale_factor = 0.02, autovacuum_analyze_scale_factor = 0.05)', part); "
            "END LOOP; END $$"
        )
    op.execute("INSERT INTO space_state (key, value) VALUES ('dedupe_generation', 0)")
    op.execute(DEFINER_GRANTS)
    op.execute(EVENTS_APPEND_ONLY)


def downgrade() -> None:
    raise NotImplementedError("the baseline is where the history starts")


FUNCTIONS = r"""
CREATE FUNCTION uuid7_floor(at_ms bigint) RETURNS uuid
LANGUAGE sql IMMUTABLE PARALLEL SAFE AS $$
  SELECT (lpad(to_hex(at_ms), 12, '0') || '7000' || '8000' || '000000000000')::uuid
$$;

CREATE FUNCTION bump_change_seq() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  NEW.change_seq := nextval('change_seq');
  RETURN NEW;
END $$;

CREATE FUNCTION ensure_week_partition(parent regclass, week_start_ms bigint) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
DECLARE
  week_ms constant bigint := 7 * 24 * 3600 * 1000;
  child text := format('%s_w%s', parent::text, week_start_ms / week_ms);
  low uuid := uuid7_floor(week_start_ms);
  high uuid := uuid7_floor(week_start_ms + week_ms);
  fallback regclass;
  keycol name;
  collist text;
  stray boolean := false;
BEGIN
  IF to_regclass(child) IS NOT NULL THEN
    RETURN;
  END IF;
  EXECUTE format('LOCK TABLE %s IN ACCESS EXCLUSIVE MODE', parent);
  IF to_regclass(child) IS NOT NULL THEN
    RETURN;
  END IF;
  SELECT NULLIF(p.partdefid, 0)::regclass, a.attname INTO fallback, keycol
    FROM pg_partitioned_table p
    JOIN pg_attribute a ON a.attrelid = p.partrelid AND a.attnum = p.partattrs[0]
   WHERE p.partrelid = parent;
  -- A parent without its DEFAULT partition gets it first, so a row of a week not created yet lands there
  -- instead of failing, and no revision has to name a partition.
  IF fallback IS NULL THEN
    EXECUTE format('CREATE TABLE %I PARTITION OF %s DEFAULT', parent::text || '_default', parent);
  ELSE
    EXECUTE format('SELECT EXISTS (SELECT FROM %s WHERE %I >= %L AND %I < %L)', fallback, keycol, low, keycol, high)
      INTO stray;
  END IF;
  IF stray THEN
    SELECT string_agg(quote_ident(attname), ', ' ORDER BY attnum) INTO collist
      FROM pg_attribute WHERE attrelid = parent AND attnum > 0 AND NOT attisdropped;
    EXECUTE format('ALTER TABLE %s DETACH PARTITION %s', parent, fallback);
  END IF;
  EXECUTE format('CREATE TABLE %I PARTITION OF %s FOR VALUES FROM (%L) TO (%L)', child, parent, low, high);
  IF stray THEN
    EXECUTE format(
      'WITH moved AS (DELETE FROM %s WHERE %I >= %L AND %I < %L RETURNING %s) INSERT INTO %I (%s) SELECT %s FROM moved',
      fallback, keycol, low, keycol, high, collist, child, collist, collist);
    EXECUTE format('ALTER TABLE %s ATTACH PARTITION %s DEFAULT', parent, fallback);
  END IF;
  IF parent = 'events'::regclass AND EXISTS (SELECT FROM pg_roles WHERE rolname = 'niadra_app') THEN
    EXECUTE format('REVOKE UPDATE, DELETE ON %I FROM niadra_app', child);
  END IF;
END $$;

CREATE FUNCTION create_hash_partitions(parent regclass, modulus int) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
BEGIN
  FOR i IN 0 .. modulus - 1 LOOP
    EXECUTE format('CREATE TABLE %I PARTITION OF %s FOR VALUES WITH (MODULUS %s, REMAINDER %s)',
                   parent::text || '_p' || i, parent, modulus, i);
  END LOOP;
END $$;

-- Row-level security by space. The application role must set niadra.space_id in every
-- transaction; a missing setting raises instead of returning another space's rows.
CREATE FUNCTION enable_space_rls(tbl regclass) RETURNS void
LANGUAGE plpgsql AS $$
BEGIN
  EXECUTE format('ALTER TABLE %s ENABLE ROW LEVEL SECURITY', tbl);
  EXECUTE format('CREATE POLICY space_isolation ON %s USING (space_id = current_setting(''niadra.space_id'')::uuid)', tbl);
END $$;

CREATE FUNCTION claim_event_keys(
  p_space uuid, p_lock bigint, p_keys text[], p_events uuid[], p_received timestamptz,
  OUT generation bigint, OUT known text[]
) LANGUAGE plpgsql AS $$
DECLARE
  seen text[];
  claimed text[];
BEGIN
  -- The previous table is read under the shared lock: the rotation waited for every writer of it.
  PERFORM pg_advisory_xact_lock_shared(p_lock);
  generation := coalesce((SELECT value FROM space_state WHERE key = 'dedupe_generation'), 0);
  IF coalesce(cardinality(p_keys), 0) = 0 THEN
    known := '{}';
    RETURN;
  END IF;
  EXECUTE format(
    'SELECT coalesce(array_agg(idempotency_key), ''{}'') FROM %I'
    ' WHERE space_id = $1 AND idempotency_key = ANY($2)',
    'event_keys_' || ((generation - 1) % 3 + 3) % 3)
    INTO seen USING p_space, p_keys;
  EXECUTE format(
    'WITH claimed AS (INSERT INTO %I (space_id, idempotency_key, event_id, received_at)'
    ' SELECT $1, t.k, t.e, $3 FROM unnest($2, $4) AS t(k, e) WHERE NOT t.k = ANY($5)'
    ' ON CONFLICT DO NOTHING RETURNING idempotency_key)'
    ' SELECT coalesce(array_agg(idempotency_key), ''{}'') FROM claimed',
    'event_keys_' || generation % 3)
    INTO claimed USING p_space, p_keys, p_received, p_events, seen;
  known := ARRAY(SELECT k FROM unnest(p_keys) AS k WHERE NOT k = ANY(claimed));
END $$;

CREATE FUNCTION ingest_accept(
  p_space uuid, p_lock bigint, p_intake uuid, p_received timestamptz, p_source uuid,
  p_keys text[], p_events uuid[], p_body bytea, p_dek int, p_queue text
) RETURNS text[] LANGUAGE plpgsql AS $$
DECLARE
  claim record;
  stored text[];
BEGIN
  PERFORM set_config('niadra.space_id', p_space::text, true);
  -- A repeat of a call whose answer was lost with its connection answers what the first one stored.
  SELECT skip INTO stored FROM ingest_intake WHERE space_id = p_space AND intake_id = p_intake;
  IF FOUND THEN
    RETURN stored;
  END IF;
  SELECT * INTO claim FROM claim_event_keys(p_space, p_lock, p_keys, p_events, p_received);
  INSERT INTO ingest_intake (space_id, intake_id, received_at, source_id, body_ct, dek_version, skip)
  VALUES (p_space, p_intake, p_received, p_source, p_body, p_dek, claim.known);
  INSERT INTO outbox (id, space_id, task, queue, payload)
  VALUES (p_intake, p_space, 'apply_intake', p_queue, jsonb_build_object('intake_id', p_intake::text));
  RETURN claim.known;
END $$;

CREATE FUNCTION read_invalidation_handle() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP = 'DELETE' THEN
    INSERT INTO read_invalidations (space_id, handle_ids) VALUES (OLD.space_id, ARRAY[OLD.handle_id]);
    RETURN OLD;
  END IF;
  INSERT INTO read_invalidations (space_id, handle_ids) VALUES (NEW.space_id, ARRAY[NEW.handle_id]);
  RETURN NEW;
END $$;

CREATE FUNCTION read_invalidation_membership() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP = 'DELETE' THEN
    INSERT INTO read_invalidations (space_id, handle_ids, profile_ids)
    VALUES (OLD.space_id, ARRAY[OLD.handle_id], ARRAY[OLD.profile_id]);
    RETURN OLD;
  END IF;
  INSERT INTO read_invalidations (space_id, handle_ids, profile_ids)
  VALUES (
    NEW.space_id,
    ARRAY[NEW.handle_id],
    CASE WHEN TG_OP = 'UPDATE' THEN ARRAY[OLD.profile_id, NEW.profile_id] ELSE ARRAY[NEW.profile_id] END
  );
  RETURN NEW;
END $$;

CREATE FUNCTION read_invalidation_profile() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP = 'DELETE' THEN
    INSERT INTO read_invalidations (space_id, profile_ids) VALUES (OLD.space_id, ARRAY[OLD.profile_id]);
    RETURN OLD;
  END IF;
  INSERT INTO read_invalidations (space_id, profile_ids) VALUES (NEW.space_id, ARRAY[NEW.profile_id]);
  RETURN NEW;
END $$;

CREATE FUNCTION read_invalidation_link() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP = 'DELETE' THEN
    INSERT INTO read_invalidations (space_id, handle_ids)
      VALUES (OLD.space_id, ARRAY[OLD.person_handle_id, OLD.org_handle_id]);
    RETURN OLD;
  END IF;
  INSERT INTO read_invalidations (space_id, handle_ids)
    VALUES (NEW.space_id, ARRAY[NEW.person_handle_id, NEW.org_handle_id]);
  IF TG_OP = 'UPDATE' AND (OLD.person_handle_id, OLD.org_handle_id) IS DISTINCT FROM
      (NEW.person_handle_id, NEW.org_handle_id) THEN
    INSERT INTO read_invalidations (space_id, handle_ids)
      VALUES (OLD.space_id, ARRAY[OLD.person_handle_id, OLD.org_handle_id]);
  END IF;
  RETURN NEW;
END $$;

CREATE FUNCTION read_invalidation_session() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  -- The key `app.read_state.thread_key` gives the session: the task wins over the conversation.
  IF NEW.task_id IS NOT NULL OR NEW.conversation_id IS NOT NULL THEN
    INSERT INTO read_invalidations (space_id, threads)
    VALUES (
      NEW.space_id,
      ARRAY[CASE WHEN NEW.task_id IS NOT NULL THEN 't:' || NEW.task_id ELSE 'c:' || NEW.conversation_id END]
    );
  END IF;
  RETURN NEW;
END $$;

CREATE FUNCTION lease_acquire(
  p_space uuid, p_keys text[], p_owner text, p_now timestamptz, p_expires timestamptz
) RETURNS bigint[] LANGUAGE plpgsql AS $$
DECLARE
  unit text;
  taken bigint;
  epochs bigint[] := '{}';
BEGIN
  PERFORM set_config('niadra.space_id', p_space::text, true);
  FOREACH unit IN ARRAY p_keys LOOP
    taken := NULL;
    INSERT INTO queue_leases AS q (space_id, unit_key, owner, epoch, expires_at)
    VALUES (p_space, unit, p_owner, 1, p_expires)
    ON CONFLICT (space_id, unit_key) DO UPDATE
      SET owner = p_owner, epoch = q.epoch + 1, expires_at = p_expires
      WHERE q.expires_at <= p_now OR q.owner = p_owner
    RETURNING q.epoch INTO taken;
    IF taken IS NULL THEN
      DELETE FROM queue_leases WHERE space_id = p_space AND owner = p_owner AND unit_key = ANY(p_keys);
      RETURN NULL;
    END IF;
    epochs := epochs || taken;
  END LOOP;
  RETURN epochs;
END $$;

CREATE FUNCTION pipeline_due(
  p_space uuid, OUT outbox_oldest timestamptz, OUT scheduled_due timestamptz, OUT schedule_due timestamptz,
  OUT intake_pending bigint, OUT intake_oldest timestamptz
) LANGUAGE plpgsql AS $$
BEGIN
  PERFORM set_config('niadra.space_id', p_space::text, true);
  SELECT min(created_at) INTO outbox_oldest FROM outbox;
  SELECT min(due_at) INTO scheduled_due FROM scheduled_tasks;
  SELECT min(next_run) INTO schedule_due FROM schedules;
  SELECT count(*), min(received_at) INTO intake_pending, intake_oldest FROM ingest_intake;
END $$;

CREATE FUNCTION lease_release(p_space uuid, p_keys text[], p_owner text, p_epochs bigint[])
RETURNS void LANGUAGE plpgsql AS $$
BEGIN
  PERFORM set_config('niadra.space_id', p_space::text, true);
  DELETE FROM queue_leases AS q USING unnest(p_keys, p_epochs) AS held(unit, epoch)
  WHERE q.space_id = p_space AND q.unit_key = held.unit AND q.owner = p_owner AND q.epoch = held.epoch;
END $$;

CREATE FUNCTION turn_accept(
  p_space uuid, p_intake uuid, p_received timestamptz, p_source uuid, p_turns int, p_body bytea, p_dek int,
  p_queue text
) RETURNS void LANGUAGE plpgsql AS $$
BEGIN
  PERFORM set_config('niadra.space_id', p_space::text, true);
  INSERT INTO turn_intake (space_id, intake_id, received_at, source_id, turns, body_ct, dek_version)
  VALUES (p_space, p_intake, p_received, p_source, p_turns, p_body, p_dek)
  ON CONFLICT DO NOTHING;
  IF FOUND THEN
    INSERT INTO outbox (id, space_id, task, queue, payload)
    VALUES (p_intake, p_space, 'apply_turns', p_queue, jsonb_build_object('intake_id', p_intake::text));
  END IF;
END $$;

CREATE FUNCTION drop_expired_contact_weeks(before_ms bigint, held bytea[]) RETURNS integer
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
DECLARE
  cutoff bigint := least(before_ms, (extract(epoch from now() - interval '90 days') * 1000)::bigint);
  child regclass;
  upper_uuid text;
  upper_ms bigint;
  busy boolean;
  dropped integer := 0;
BEGIN
  PERFORM set_config('lock_timeout', '2s', true);
  FOR child, upper_uuid IN
    SELECT c.oid::regclass, substring(pg_get_expr(c.relpartbound, c.oid) FROM 'TO \(''([0-9a-f-]+)''\)')
      FROM pg_inherits i JOIN pg_class c ON c.oid = i.inhrelid
     WHERE i.inhparent = 'contact_log'::regclass
  LOOP
    CONTINUE WHEN upper_uuid IS NULL;
    upper_ms := ('x' || replace(left(upper_uuid, 13), '-', ''))::bit(48)::bigint;
    CONTINUE WHEN upper_ms > cutoff;
    EXECUTE format('SELECT EXISTS (SELECT FROM %s WHERE subject_hmac = ANY($1))', child) USING held INTO busy;
    CONTINUE WHEN busy;
    BEGIN
      EXECUTE format('DROP TABLE %s', child);
      dropped := dropped + 1;
    EXCEPTION WHEN lock_not_available THEN
      RETURN dropped;
    END;
  END LOOP;
  RETURN dropped;
END $$;
"""

READ_INVALIDATION_TRIGGERS = """
CREATE TRIGGER handles_read_invalidation AFTER UPDATE OF status, provisional, value_hmac, subject_kind
  ON handles FOR EACH ROW
  WHEN (OLD.status IS DISTINCT FROM NEW.status OR OLD.provisional IS DISTINCT FROM NEW.provisional
        OR OLD.value_hmac IS DISTINCT FROM NEW.value_hmac OR OLD.subject_kind IS DISTINCT FROM NEW.subject_kind)
  EXECUTE FUNCTION read_invalidation_handle();
CREATE TRIGGER handles_read_invalidation_delete AFTER DELETE
  ON handles FOR EACH ROW EXECUTE FUNCTION read_invalidation_handle();

CREATE TRIGGER profile_membership_read_invalidation AFTER INSERT OR DELETE
  ON profile_membership FOR EACH ROW EXECUTE FUNCTION read_invalidation_membership();
CREATE TRIGGER profile_membership_read_invalidation_update AFTER UPDATE OF profile_id
  ON profile_membership FOR EACH ROW WHEN (OLD.profile_id IS DISTINCT FROM NEW.profile_id)
  EXECUTE FUNCTION read_invalidation_membership();

CREATE TRIGGER profiles_read_invalidation
  AFTER UPDATE OF retired_at, erased_at, privacy_version, membership_version, kind, org_type
  ON profiles FOR EACH ROW
  WHEN (OLD.retired_at IS DISTINCT FROM NEW.retired_at OR OLD.erased_at IS DISTINCT FROM NEW.erased_at
        OR OLD.privacy_version IS DISTINCT FROM NEW.privacy_version
        OR OLD.membership_version IS DISTINCT FROM NEW.membership_version
        OR OLD.kind IS DISTINCT FROM NEW.kind OR OLD.org_type IS DISTINCT FROM NEW.org_type)
  EXECUTE FUNCTION read_invalidation_profile();
CREATE TRIGGER profiles_read_invalidation_delete AFTER DELETE
  ON profiles FOR EACH ROW EXECUTE FUNCTION read_invalidation_profile();

CREATE TRIGGER profile_links_read_invalidation
  AFTER INSERT OR DELETE OR UPDATE OF person_handle_id, org_handle_id, role, can_see_contacts, valid_from, valid_to
  ON profile_links FOR EACH ROW EXECUTE FUNCTION read_invalidation_link();

CREATE TRIGGER sessions_read_invalidation AFTER UPDATE OF proven_level, proven_until, closed_at
  ON sessions FOR EACH ROW
  WHEN (OLD.proven_level IS DISTINCT FROM NEW.proven_level OR OLD.proven_until IS DISTINCT FROM NEW.proven_until
        OR OLD.closed_at IS DISTINCT FROM NEW.closed_at)
  EXECUTE FUNCTION read_invalidation_session();
"""

# Only the maintenance role runs the definer functions: not PUBLIC, and not the application role, which
# the database's default privileges gave EXECUTE on every function. The roles exist on every provisioned
# server; `alembic check` in CI may run against a bare one, hence the guards.
DEFINER_GRANTS = """
REVOKE EXECUTE ON FUNCTION ensure_week_partition(regclass, bigint), create_hash_partitions(regclass, int) FROM PUBLIC;
DO $$
BEGIN
  IF EXISTS (SELECT FROM pg_roles WHERE rolname = 'niadra_app') THEN
    REVOKE EXECUTE ON FUNCTION ensure_week_partition(regclass, bigint), create_hash_partitions(regclass, int) FROM niadra_app;
  END IF;
  IF EXISTS (SELECT FROM pg_roles WHERE rolname = 'niadra_system') THEN
    GRANT EXECUTE ON FUNCTION ensure_week_partition(regclass, bigint), create_hash_partitions(regclass, int) TO niadra_system;
  END IF;
END $$;
"""

# The event log is append-only for the application role: a week partition created later gets the
# same revoke from `ensure_week_partition`.
EVENTS_APPEND_ONLY = """
DO $$
DECLARE part regclass;
BEGIN
  IF EXISTS (SELECT FROM pg_roles WHERE rolname = 'niadra_app') THEN
    REVOKE UPDATE, DELETE ON events FROM niadra_app;
    FOR part IN SELECT inhrelid::regclass FROM pg_inherits WHERE inhparent = 'events'::regclass LOOP
      EXECUTE format('REVOKE UPDATE, DELETE ON %s FROM niadra_app', part);
    END LOOP;
  END IF;
END $$;
"""
