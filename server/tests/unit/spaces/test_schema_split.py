"""The split of the tables between the cell database and the space databases (spec 1), asserted
literally: a new table has to be placed in one of them on purpose."""

import importlib.util
import re
from pathlib import Path

from niadra.adapters.outbound.postgres.schema import (
    CELL_TABLES,
    SPACE_TABLES,
    UNPINNED_SPACE_TABLES,
    CellBase,
    SpaceBase,
)

ROOT = Path(__file__).resolve().parents[3]
MIGRATIONS = ROOT / "migrations"

CELL = {
    "cell_state",
    "cell_health",
    "config_snapshot",
    "config_documents",
    "secrets",
    "data_keys",
    "source_keys",
    "dead_tasks",
    "receipt_watermarks",
    "space_databases",
    "deletion_receipts_outbox",
}
SPACE = {
    *("events", "event_handles", "event_keys_0", "event_keys_1", "event_keys_2", "conversations"),
    *("ingest_imports", "source_heartbeats", "sessions", "profiles", "handles", "profile_membership"),
    *("assertions", "merge_log", "profile_links", "blocked_values", "identity_suggestions"),
    "decisions",
    *("extraction_runs", "llm_spend", "facts", "fact_mentions", "fact_relations", "episodes"),
    *("open_items", "profile_attrs", "tail_summaries", "business_objects", "object_events"),
    *("object_participants", "agent_actions", "traits", "trait_opt_outs", "trait_evidence"),
    "pack_bundles",
    *("pack_manifests", "conversation_pins", "agent_watermarks", "receipts", "receipt_heads"),
    "receipt_people",
    *("audit_roots", "tombstones", "context_use", "context_use_daily", "measured_sessions"),
    *("erasure_requests", "privacy_feed", "export_runs", "trigger_firings", "webhook_deliveries"),
    *("review_samples", "assist_runs", "usage_hourly", "usage_events", "tenant_rollups"),
    *("request_idempotency", "outbox", "scheduled_tasks", "profile_work", "queue_leases", "schedules"),
    *("space_identity", "space_state"),
    # Added later, each with its own revision: what each source leaves unused (15.8), and what the
    # agents' model calls reported (15.3).
    *("context_use_units", "context_trims", "model_usage_daily"),
    # The agents' own working notes and their proposals (10, C).
    *("agent_notes", "agent_note_proposals"),
    # The first group of each profile in an experiment.
    "experiment_assignments",
    # What a session settled without a predicate: protocols, postal codes, amounts (11, 3.2).
    "settled_values",
    # Slots by retrieval channel and the space's learned fusion weights (11, 3.4).
    *("context_use_slot_daily", "retrieval_weights"),
    # Guard lines per settled value type and arm (memo 13, P2).
    "context_use_guard_daily",
    # A data subject's objection to a purpose, and the knowledge gaps with the team's reviews (13, 6.5, 6.6).
    *("purpose_objections", "knowledge_gaps", "insight_reviews"),
    # The view each agent and model learned to use (memo 13, P4).
    *("context_use_view_daily", "view_budgets"),
    # The resolutions of an open item by feedback that did not close it, for its erasure (8.4).
    "open_item_feedback_events",
    # An end that arrived before the turns of its session (5.4).
    "session_ends",
    # Batches acknowledged before they are stored, for milliseconds (5.2).
    "ingest_intake",
    # What changed under the read processes' state, written by triggers (5.11).
    "read_invalidations",
    # Dropped later by a contract revision: `eval_runs`, which no process wrote, and `org_parents`, the
    # organization parents the founder removed on 10/10/2026.
    # The agent features' tables (6.7), as in `SPACE_TABLES`.
    # The turns module: what an agent read, called, showed, claimed and decided (6.7).
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
    *("shared_objects", "object_values", "object_dependencies", "object_timers"),
    *("object_watches", "refresh_requests", "content_scans", "agent_state"),
    *("ownership_claims", "effects", "contact_log", "suppressions", "commitments", "handoffs"),
    "coord_reports",
    "budget_spends",
    "subject_signals",
    "beneficiaries",
    "signal_intake",
    "signal_priors",
    "inference_suppressions",
    "measure_segments",
    "outcome_links",
    "attribution_daily",
    "interleave_daily",
    "counterfactual_runs",
    "measurement_reports",
    "review_requests",
    "legal_holds",
    # Raw content by key, when it was written and what each payload names (5.14.0).
    "raw_objects",
    # What each event's text names a person by, for an erasure of someone named only there (5.14.0).
    "event_mentions",
    # What derived rows and turn recordings name a person by, for the same erasure (5.14.0).
    "text_mentions",
    "object_mentions",
}


def test_the_split_is_the_one_of_the_spec() -> None:
    assert set(CELL_TABLES) == CELL
    assert set(SPACE_TABLES) == SPACE
    assert len(CELL_TABLES) == len(CELL) == 11
    # No table listed twice.
    assert len(SPACE_TABLES) == len(SPACE)


def test_every_declared_table_is_in_exactly_one_database() -> None:
    cell, space = set(CellBase.metadata.tables), set(SpaceBase.metadata.tables)
    assert cell == set(CELL_TABLES)
    assert space == set(SPACE_TABLES)
    assert cell.isdisjoint(space)


def test_every_space_table_with_a_space_column_gets_row_level_security() -> None:
    versions = MIGRATIONS / "space" / "versions"
    baseline = next(versions.glob("*_space_baseline.py"))
    migration = _module(baseline)
    # The baseline's list is frozen; a table added later enables its own row-level security, and a table
    # dropped later leaves with its revision's upgrade.
    upgrades = [_upgrade_body(path) for path in versions.glob("*.py") if path != baseline]
    later = {
        match.group(1)
        for body in upgrades
        for match in re.finditer(r"enable_space_rls\('([a-z_0-9]+)'\)", body)
    }
    dropped = {
        match.group(1)
        for body in upgrades
        for match in re.finditer(r'op\.drop_table\("([a-z_0-9]+)"\)', body)
    }
    pinned = (set(migration.PINNED_TABLES) | later) - dropped
    with_space = {name for name, table in SpaceBase.metadata.tables.items() if "space_id" in table.c}
    assert pinned == with_space - set(UNPINNED_SPACE_TABLES)
    assert {"outbox", "scheduled_tasks"} <= set(migration.PINNED_TABLES)


def test_no_migration_names_a_hash_partition() -> None:
    """The number of hash partitions is per database (8 or 32): a migration that adds an index or a
    constraint to a partitioned parent iterates `pg_inherits` instead of naming `facts_p0`...`_p31`."""
    literal = re.compile(r"\b[a-z_]+_p([0-9]|[12][0-9]|3[01])\b")
    offenders = [
        f"{path.relative_to(ROOT)}: {match.group(0)}"
        for path in sorted(MIGRATIONS.rglob("*.py"))
        for match in literal.finditer(path.read_text())
    ]
    assert offenders == []


def _upgrade_body(path: Path) -> str:
    text = path.read_text()
    return text[text.index("def upgrade()") : text.index("def downgrade()")]


def _module(path: Path) -> object:
    spec = importlib.util.spec_from_file_location(path.stem, path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
