from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import text

from niadra.adapters.outbound.postgres.uow import PgUnitOfWorkFactory
from niadra.domain.common.ids import uuid7
from niadra.domain.privacy.erasure import ErasedRow, ErasureRequest, ErasureStatus, TargetKind
from niadra.domain.privacy.export import ON_DEMAND, ExportRun, ExportStatus

AT = datetime(2026, 9, 22, 12, 0, tzinfo=UTC)


def request(**overrides: object) -> ErasureRequest:
    values: dict[str, object] = {
        "request_id": uuid4(),
        "target_kind": TargetKind.HANDLE,
        "target_id": str(uuid4()),
        "status": ErasureStatus.PENDING,
        "requested_by": "user:u",
        "requested_at": AT,
        "counts": {"scope": {"kind": "handle", "handle_ids": []}},
    }
    values.update(overrides)
    return ErasureRequest(**values)  # type: ignore[arg-type]


async def test_erasure_requests_are_created_once_and_saved(pg_uow: PgUnitOfWorkFactory) -> None:
    space = uuid4()
    wanted = request()
    async with pg_uow(space) as uow:
        stored, created = await uow.privacy.create_erasure(space, wanted)
        again, created_again = await uow.privacy.create_erasure(space, request(request_id=wanted.request_id))
    assert (stored, created) == (wanted, True)
    assert (again, created_again) == (wanted, False)
    done = ErasureRequest(
        **{
            **_fields(wanted),
            "status": ErasureStatus.COMPLETED,
            "completed_at": AT,
            "counts": {"tables": {"events": 3}},
            "receipt_hash": b"\x00" * 32,
        }
    )
    async with pg_uow(space) as uow:
        await uow.privacy.save_erasure(space, done)
    async with pg_uow(space) as uow:
        assert await uow.privacy.get_erasure(space, wanted.request_id) == done
        assert await uow.privacy.get_erasure(uuid4(), wanted.request_id) is None


def _fields(r: ErasureRequest) -> dict[str, object]:
    return {name: getattr(r, name) for name in r.__dataclass_fields__}


async def test_the_feed_takes_the_space_from_the_transaction(pg_uow: PgUnitOfWorkFactory) -> None:
    space, profile, handle = uuid4(), uuid4(), uuid4()
    async with pg_uow(space) as uow:
        before = (await uow.session.execute(text("SELECT now()"))).scalar_one()
    async with pg_uow(space) as uow:
        await uow.privacy.record_feed(
            kind="erasure", profile_ids=[profile], handle_ids=[handle], privacy_version=7
        )
    async with pg_uow(space) as uow:
        rows = [
            r for r in await uow.privacy.feed_since(before - timedelta(seconds=1)) if r["space_id"] == space
        ]
        assert [(r["kind"], r["profile_ids"], r["handle_ids"], r["privacy_version"]) for r in rows] == [
            ("erasure", [profile], [handle], 7)
        ]
    async with pg_uow(space) as uow:
        assert await uow.privacy.purge_feed(space, datetime.now(UTC) + timedelta(minutes=1)) == 1


async def test_tombstones_follow_the_cell_sequence(pg_uow: PgUnitOfWorkFactory) -> None:
    space = uuid4()
    async with pg_uow(space) as uow:
        start = await uow.privacy.current_change_seq()
        await uow.privacy.add_tombstones(
            space, [ErasedRow("events", {"event_id": "e1"}), ErasedRow("facts", {"fact_id": "f1"})], AT
        )
        observed = await uow.privacy.current_change_seq()
        await uow.privacy.add_tombstones(
            space, [ErasedRow("events", {"event_id": "e2"})], AT + timedelta(days=500)
        )
    async with pg_uow(space) as uow:
        first = await uow.privacy.tombstones_between(space, start, observed)
        everything = await uow.privacy.tombstones_between(space, start, None)
        assert [(t.table, t.key) for t in first] == [
            ("events", {"event_id": "e1"}),
            ("facts", {"fact_id": "f1"}),
        ]
        assert len(everything) == 3
        assert everything[0].change_seq < everything[1].change_seq < everything[2].change_seq
        assert await uow.privacy.purge_tombstones(space, AT + timedelta(days=1)) == 2


async def test_export_runs_list_newest_first_and_find_the_runs_that_carried_a_row(
    pg_uow: PgUnitOfWorkFactory,
) -> None:
    space = uuid4()
    old = ExportRun(uuid4(), "lake", 0, 10, ExportStatus.COMPLETED, AT, {"files": []}, AT)
    new = ExportRun(uuid4(), "lake", 10, 20, ExportStatus.COMPLETED, AT + timedelta(hours=1))
    package = ExportRun(uuid4(), ON_DEMAND, 0, 99, ExportStatus.COMPLETED, AT)
    old, new, package = (
        ExportRun(**{**_run_fields(r), "run_id": rid})
        for r, rid in zip((old, new, package), sorted(uuid4() for _ in range(3)), strict=True)
    )
    async with pg_uow(space) as uow:
        for run in (old, new, package):
            await uow.privacy.create_export_run(space, run)
        await uow.privacy.save_export_run(space, ExportRun(**{**_run_fields(new), "error": "late"}))
    async with pg_uow(space) as uow:
        runs = await uow.privacy.export_runs(space, schedule_id="lake", before_id=None, limit=10)
        assert [r.run_id for r in runs] == [new.run_id, old.run_id]
        assert runs[0].error == "late"
        assert runs[1].manifest == {"files": []}
        paged = await uow.privacy.export_runs(space, schedule_id=None, before_id=new.run_id, limit=10)
        assert [r.run_id for r in paged] == [old.run_id]
        assert await uow.privacy.runs_covering(space, [5, 15, 50]) == [old.run_id, new.run_id]
        assert await uow.privacy.runs_covering(space, [10]) == [old.run_id]
        assert await uow.privacy.runs_covering(space, []) == []
        assert await uow.privacy.purge_export_runs(space, AT + timedelta(minutes=30)) == 2


def _run_fields(r: ExportRun) -> dict[str, object]:
    return {name: getattr(r, name) for name in r.__dataclass_fields__}


async def test_stored_packages_are_the_on_demand_runs_not_yet_deleted(pg_uow: PgUnitOfWorkFactory) -> None:
    space, ana, bia = uuid4(), uuid4(), uuid4()

    def package(profile: UUID, at: datetime, **extra: str) -> ExportRun:
        manifest = {"profile_id": str(profile), "location": f"exports/{uuid4()}.zip", **extra}
        return ExportRun(uuid7(), ON_DEMAND, 0, 0, ExportStatus.COMPLETED, at, manifest, finished_at=at)

    old_ana = package(ana, AT - timedelta(days=2))
    new_ana = package(ana, AT)
    gone = package(bia, AT - timedelta(days=3), deleted_at=AT.isoformat())
    lake = ExportRun(uuid7(), "lake", 0, 5, ExportStatus.COMPLETED, AT - timedelta(days=9), {"files": []})
    async with pg_uow(space) as uow:
        for run in (old_ana, new_ana, gone, lake):
            await uow.privacy.create_export_run(space, run)
    async with pg_uow(space) as uow:
        of_ana = await uow.privacy.stored_packages(space, profile_id=ana, created_before=None, limit=10)
        expired = await uow.privacy.stored_packages(
            space, profile_id=None, created_before=AT - timedelta(days=1), limit=10
        )
    assert {r.run_id for r in of_ana} == {old_ana.run_id, new_ana.run_id}
    assert [r.run_id for r in expired] == [old_ana.run_id]


async def test_row_level_security_hides_other_spaces(rls_uow: PgUnitOfWorkFactory) -> None:
    mine, theirs = uuid4(), uuid4()
    shared = request()
    for space in (mine, theirs):
        async with rls_uow(space) as uow:
            await uow.privacy.create_erasure(space, shared)
            await uow.privacy.add_tombstones(space, [ErasedRow("events", {"event_id": "x"})], AT)
            await uow.privacy.record_feed(kind="erasure", profile_ids=[], handle_ids=[], privacy_version=1)
    async with rls_uow(mine) as uow:
        assert await uow.privacy.get_erasure(theirs, shared.request_id) is None
        assert await uow.privacy.tombstones_between(theirs, 0, None) == []
        for table in ("erasure_requests", "tombstones", "privacy_feed"):
            spaces = await uow.session.execute(text(f"SELECT DISTINCT space_id FROM {table}"))
            assert set(spaces.scalars()) == {mine}


async def test_erasure_requests_are_listed_newest_first_by_status(pg_uow: PgUnitOfWorkFactory) -> None:
    space = uuid4()
    older = request(requested_at=AT - timedelta(hours=1), status=ErasureStatus.COMPLETED)
    newer = [request(requested_at=AT) for _ in range(2)]
    async with pg_uow(space) as uow:
        for r in (older, *newer):
            await uow.privacy.create_erasure(space, r)
    async with pg_uow(space) as uow:
        first = await uow.privacy.list_erasures(space, status=None, before=None, limit=2)
        rest = await uow.privacy.list_erasures(
            space, status=None, before=(first[-1].requested_at, first[-1].request_id), limit=10
        )
        done = await uow.privacy.list_erasures(space, status=ErasureStatus.COMPLETED, before=None, limit=10)
        assert await uow.privacy.list_erasures(uuid4(), status=None, before=None, limit=10) == []
    assert [r.request_id for r in first] == sorted((r.request_id for r in newer), reverse=True)
    assert [r.request_id for r in rest] == [older.request_id]
    assert [r.request_id for r in done] == [older.request_id]
