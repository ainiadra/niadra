"""The directory's rules: names, which databases serve, waves, retries and reconciliation (2, 8)."""

from datetime import UTC, datetime, timedelta
from uuid import UUID

from niadra.domain.spaces.directory import (
    DbStatus,
    SpaceDatabase,
    Tier,
    WantedSpace,
    db_name_for,
    detail,
    new_entry,
    next_retry,
    reconcile,
    space_of,
    waves,
)

NOW = datetime(2026, 9, 24, 12, tzinfo=UTC)
TENANT = UUID(int=1)
A, B, C, D = (UUID(int=i) for i in (0xA, 0xB, 0xC, 0xD))


def row(space: UUID, status: DbStatus = DbStatus.READY, **changes: object) -> SpaceDatabase:
    entry = new_entry(space, TENANT, str(changes.pop("environment", "sandbox")), NOW)
    ready = {"schema_version": "head", "ready_at": NOW} if status is not DbStatus.CREATING else {}
    return entry.moved(status, **{**ready, **changes})


def test_a_database_is_named_after_its_space_and_named_back() -> None:
    name = db_name_for(A)
    assert name == "s_" + A.hex
    assert len(name) == 34
    assert space_of(name) == A
    assert space_of("s_" + A.hex + "_old_20260924120000") is None
    assert space_of("s_template") is None
    assert space_of("niadra_cell") is None


def test_a_database_serves_once_it_has_a_schema_and_while_it_is_not_being_replaced_or_deleted() -> None:
    assert row(A).serving
    assert row(A, DbStatus.MIGRATING).serving  # an online migration, one transactional revision at a time
    assert row(A, DbStatus.FAILED).serving  # a failed migration left it at a revision the code supports
    assert not row(A, DbStatus.CREATING).serving
    assert not row(A, DbStatus.MIGRATING, schema_version=None).serving  # its first migration
    for status in (DbStatus.MAINTENANCE, DbStatus.DELETING, DbStatus.DELETED):
        assert not row(A, status).serving


def test_new_spaces_are_t0_with_one_hash_partition() -> None:
    entry = new_entry(A, TENANT, "production", NOW)
    assert (entry.tier, entry.hash_modulus, entry.status) == (Tier.T0, 1, DbStatus.CREATING)
    assert new_entry(A, TENANT, "production", NOW, Tier.T1).hash_modulus == 32


def test_waves_go_sandboxes_then_small_production_then_large() -> None:
    rows = [
        row(D, environment="production", tier=Tier.T1),
        row(C, environment="production"),
        row(B, environment="sandbox"),
        row(A, environment="production", tier=Tier.T2),
    ]
    assert [[r.space_id for r in wave] for wave in waves(rows)] == [[B], [C], [A, D]]
    assert waves([row(C, environment="production")]) == [[row(C, environment="production")]]


def test_retries_back_off_to_fifteen_minutes() -> None:
    waits = [next_retry(n, NOW) - NOW for n in range(1, 6)]
    assert waits == [
        timedelta(seconds=30),
        timedelta(minutes=1),
        timedelta(minutes=5),
        timedelta(minutes=15),
        timedelta(minutes=15),
    ]


def test_failure_details_keep_the_type_and_stay_short() -> None:
    assert detail(RuntimeError("x" * 1000)).startswith("RuntimeError: xxx")
    assert len(detail(RuntimeError("x" * 1000))) == 500


def wanted(*spaces: UUID) -> list[WantedSpace]:
    return [WantedSpace(s, TENANT, "sandbox") for s in spaces]


def test_a_space_of_the_snapshot_without_a_row_is_created() -> None:
    plan = reconcile(wanted(A, B), {}, [row(A)], [db_name_for(A)], NOW)
    assert [w.space_id for w in plan.create] == [B]
    assert (plan.retry, plan.delete, plan.drift, plan.orphans) == ([], [], [], [])


def test_a_creation_that_never_finished_is_retried_when_its_backoff_passed() -> None:
    failed = row(A, DbStatus.FAILED, schema_version=None, ready_at=None, failures=1)
    waiting = row(B, DbStatus.FAILED, ready_at=None, retry_at=NOW + timedelta(seconds=30), failures=1)
    crashed = row(C, DbStatus.CREATING)
    migrated_then_failed = row(D, DbStatus.FAILED)  # a live database: the setup runner's, not ours
    plan = reconcile(wanted(A, B, C, D), {}, [failed, waiting, crashed, migrated_then_failed], [], NOW)
    assert {r.space_id for r in plan.retry} == {A, C}
    assert plan.create == []


def test_a_removed_space_is_deleted_until_its_row_says_deleted() -> None:
    removed = {A: NOW, B: NOW, C: NOW}
    rows = [row(A), row(B, DbStatus.DELETING), row(C, DbStatus.DELETED)]
    plan = reconcile(wanted(), removed, rows, [db_name_for(A), db_name_for(B)], NOW)
    assert {r.space_id for r in plan.delete} == {A, B}
    assert plan.drift == []


def test_a_truncated_snapshot_deletes_nothing_and_reports_drift() -> None:
    plan = reconcile(wanted(A), {}, [row(A), row(B), row(C, DbStatus.DELETED)], [], NOW)
    assert plan.delete == []
    assert plan.drift == [B]


def test_a_database_without_a_row_is_an_orphan_but_copies_and_scratch_databases_are_not() -> None:
    names = [
        db_name_for(A),
        db_name_for(B),
        db_name_for(C),
        db_name_for(A) + "_old_20260920101010",
        db_name_for(A) + "_r20260924101010",
        "s_template",
    ]
    plan = reconcile(wanted(A), {}, [row(A), row(C, DbStatus.DELETED)], names, NOW)
    # C's row says deleted but its database is still there: that is an orphan too.
    assert plan.orphans == sorted([db_name_for(B), db_name_for(C)])
