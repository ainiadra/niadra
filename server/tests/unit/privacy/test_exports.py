import io
import json
import zipfile
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import timedelta
from uuid import UUID, uuid4

import pyarrow.parquet as pq
import pytest

from niadra.app.privacy import secret_ref
from niadra.contracts.privacy import ExportRequest, ForgetRequest
from niadra.domain.common.errors import InvalidInput, NotFound
from niadra.domain.notify.webhooks import Delivery
from niadra.domain.privacy.erasure import ErasedRow
from niadra.domain.privacy.export import SETTLE, ExportStatus
from niadra.domain.privacy.files import from_parquet, sha256_hex
from niadra.domain.tasks import Task, TaskName
from niadra.testing.cell import SPACE_ID

from .conftest import LAKE_CREDENTIAL, config_with_schedule
from .world import T0, World


def plan() -> Task:
    return Task(TaskName.EXPORT_RUN, SPACE_ID, {"schedule_id": "lake"})


def set_change_seq(cell, value: int) -> None:
    cell.uow.store.module("privacy").setdefault("change_seq", {"value": 0})["value"] = value


def scheduled(cell) -> list[Task]:
    return [task for task, _, _ in cell.uow.store.module("pipeline")["scheduled"]]


async def test_a_run_observes_the_sequence_then_exports_after_it_settles(
    cell, world: World, make_privacy, destinations
) -> None:
    marina, _ = world.person("+5511999990001")
    kept = world.event(marina, "c-1", "hello")
    gone = world.event(marina, "c-1", "bye")
    world.events[gone]["text"] = None
    privacy = make_privacy()
    cell.secrets.values[LAKE_CREDENTIAL] = "write-only-key"
    set_change_seq(cell, world.change_seq)
    async with cell.uow(SPACE_ID) as uow:
        await uow.privacy.add_tombstones(SPACE_ID, [ErasedRow("events", {"event_id": str(gone)})], T0)

    await privacy.export_run(plan())
    (follow,) = scheduled(cell)
    await privacy.export_run(plan())
    assert len(scheduled(cell)) == 1, "one run per schedule at a time"

    await privacy.export_run(follow)
    assert len(scheduled(cell)) == 2, "not settled yet: try again later"
    assert destinations.store.objects == {}

    cell.clock.advance(seconds=SETTLE.total_seconds())
    world.change_seq = 100
    world.event(marina, "c-2", "written after the observation")
    await privacy.export_run(follow)

    runs = (await privacy.export_runs(SPACE_ID, schedule_id="lake", cursor=None, limit=10)).items
    (run,) = runs
    assert run.status == "completed"
    assert (run.from_seq, run.to_seq) == (0, 3)
    # The bytes the bucket received are usage, recorded once for the run.
    sent = sum(len(v) for k, v in destinations.store.objects.items() if k.endswith(".parquet"))
    events = cell.uow.store.module("metering")["events"].values()
    assert [(m, q) for m, q, _, _ in events if m == "export_bytes"] == [("export_bytes", sent)]
    assert destinations.opened == [("lake", "write-only-key")]
    assert destinations.closed == 1
    [announced] = await deliveries(cell)
    assert announced.event_type == "export.completed"
    assert announced.body["data"] == {
        "schedule_id": "lake",
        "run_id": run.run_id,
        "from_seq": 0,
        "to_seq": 3,
        "files": len(run.manifest["files"]),
    }
    manifest = json.loads(destinations.store.objects[f"niadra/manifests/{run.run_id}.json"])
    assert manifest["erased"] == [{"table": "events", "event_id": str(gone), "change_seq": 2}]
    assert manifest["skipped_tables"] == ["unknown_table"]
    events_file = next(f for f in manifest["files"] if f["table"] == "events")
    data = destinations.store.objects[events_file["path"]]
    assert sha256_hex(data) == events_file["sha256"]
    rows = from_parquet(data)
    assert [(r["event_id"], r["op"]) for r in rows] == [(str(kept), "upsert")]
    assert pq.read_schema(io.BytesIO(data)).metadata[b"niadra.schema_version"] == b"1"
    tombstones = next(f for f in manifest["files"] if f["table"] == "tombstones")
    assert from_parquet(destinations.store.objects[tombstones["path"]])[0]["op"] == "delete"


async def test_the_next_run_starts_where_the_last_completed_one_ended(
    cell, world: World, make_privacy
) -> None:
    marina, _ = world.person("+5511999990001")
    world.event(marina, "c-1", "one")
    privacy = make_privacy()
    cell.secrets.values[LAKE_CREDENTIAL] = "k"
    set_change_seq(cell, 5)
    await privacy.export_run(plan())
    cell.clock.advance(minutes=3)
    await privacy.export_run(scheduled(cell)[0])
    set_change_seq(cell, 9)
    await privacy.export_run(plan())
    runs = (await privacy.export_runs(SPACE_ID, schedule_id="lake", cursor=None, limit=10)).items
    assert [(r.from_seq, r.to_seq, r.status) for r in runs] == [(5, 9, "waiting"), (0, 5, "completed")]


async def test_a_destination_outside_the_region_fails_the_run(cell, make_privacy, destinations) -> None:
    privacy = make_privacy()
    cell.config.put(config_with_schedule(region="eu-west-1"))
    await privacy.export_run(plan())
    cell.clock.advance(minutes=3)
    await privacy.export_run(scheduled(cell)[0])
    (run,) = (await privacy.export_runs(SPACE_ID, schedule_id=None, cursor=None, limit=10)).items
    assert (run.status, run.error) == ("failed", "destination_outside_region")
    assert destinations.opened == []


async def test_a_failing_destination_marks_the_run_failed_without_content(cell, make_privacy) -> None:
    privacy = make_privacy()
    await privacy.export_run(plan())
    cell.clock.advance(minutes=3)
    await privacy.export_run(scheduled(cell)[0])  # no credential in the vault
    (run,) = (await privacy.export_runs(SPACE_ID, schedule_id=None, cursor=None, limit=10)).items
    assert (run.status, run.error) == ("failed", "unknown secret")
    [announced] = await deliveries(cell)
    assert (announced.event_type, announced.body["data"]["error"]) == ("export.failed", "unknown secret")


async def test_a_destination_error_is_named_by_type_only(cell, make_privacy, destinations) -> None:
    class Refusing:
        @asynccontextmanager
        async def open(self, schedule: object, credential: str) -> AsyncIterator[object]:
            raise PermissionError(f"AccessDenied for key {credential}")
            yield

    cell.secrets.values[LAKE_CREDENTIAL] = "secret-key"
    privacy = make_privacy()
    privacy._destinations = Refusing()
    await privacy.export_run(plan())
    cell.clock.advance(minutes=3)
    await privacy.export_run(scheduled(cell)[0])
    (run,) = (await privacy.export_runs(SPACE_ID, schedule_id=None, cursor=None, limit=10)).items
    assert (run.status, run.error) == ("failed", "PermissionError")


async def deliveries(cell) -> list[Delivery]:
    async with cell.uow(SPACE_ID) as uow:
        return await uow.notify.list_deliveries()


async def test_unknown_schedule(make_privacy) -> None:
    with pytest.raises(NotFound):
        await make_privacy().export_run(Task(TaskName.EXPORT_RUN, SPACE_ID, {"schedule_id": "nope"}))


async def test_the_package_holds_the_subjects_records_as_json_lines(cell, world: World, make_privacy) -> None:
    marina, profile = world.person("+5511999990001")
    world.event(marina, "c-1", "hello")
    world.fact(marina, "c-1")
    privacy = make_privacy()
    package = await privacy.export_package(SPACE_ID, ExportRequest(profile_id=profile), requested_by="user:u")
    assert (
        package.download_url
        == f"https://objects.test/exports/{SPACE_ID}/{package.run_id}.zip?download=1&expires=900"
    )
    assert package.download_expires_at == cell.clock.now() + timedelta(minutes=15)
    data = await privacy.download_package(SPACE_ID, UUID(package.run_id))
    assert sha256_hex(data) == package.sha256
    usage = [(m, q) for m, q, _, _ in cell.uow.store.module("metering")["events"].values()]
    assert usage == [("export_bytes", len(data))]
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        events = [json.loads(line) for line in archive.read("events.jsonl").splitlines()]
        assert events[0]["text"] == "hello"
        assert len(archive.read("facts.jsonl").splitlines()) == 1
        assert set(package.files) == set(archive.namelist())
    runs = (await privacy.export_runs(SPACE_ID, schedule_id="on_demand", cursor=None, limit=10)).items
    assert runs[0].status == ExportStatus.COMPLETED.value
    with pytest.raises(NotFound):
        await privacy.download_package(SPACE_ID, uuid4())
    with pytest.raises(NotFound):
        await privacy.export_package(SPACE_ID, ExportRequest(profile_id=uuid4()), requested_by="user:u")


async def test_a_package_leaves_storage_after_a_day(cell, world: World, make_privacy) -> None:
    # The package is the subject's data decrypted; it never stays in the bucket for good.
    marina, profile = world.person("+5511999990001")
    world.event(marina, "c-1", "hello")
    privacy = make_privacy()
    package = await privacy.export_package(SPACE_ID, ExportRequest(profile_id=profile), requested_by="user:u")
    location = f"exports/{SPACE_ID}/{package.run_id}.zip"
    cell.clock.advance(hours=23)
    await privacy.retention_sweep(Task(TaskName.RETENTION_SWEEP, SPACE_ID))
    assert location in cell.objects.objects  # still within its day
    assert await privacy.download_package(SPACE_ID, UUID(package.run_id))
    cell.clock.advance(hours=2)
    with pytest.raises(NotFound):
        await privacy.download_package(SPACE_ID, UUID(package.run_id))
    await privacy.retention_sweep(Task(TaskName.RETENTION_SWEEP, SPACE_ID))
    assert location not in cell.objects.objects
    [run] = (await privacy.export_runs(SPACE_ID, schedule_id="on_demand", cursor=None, limit=10)).items
    assert run.manifest is not None
    assert run.manifest["deleted_at"] == cell.clock.now().isoformat()


async def test_forgetting_the_subject_deletes_its_package_at_once(cell, world: World, make_privacy) -> None:
    # A package made before the erasure holds what the erasure removes, decrypted.
    marina, profile = world.person("+5511999990001")
    joao, joao_profile = world.person("+5511999990002")
    world.event(marina, "c-1", "hello")
    world.event(joao, "c-2", "oi")
    privacy = make_privacy()
    mine = await privacy.export_package(SPACE_ID, ExportRequest(profile_id=profile), requested_by="user:u")
    other = await privacy.export_package(SPACE_ID, ExportRequest(profile_id=joao_profile), requested_by="u")
    request = ForgetRequest(target="profile", profile_id=profile)
    accepted = await privacy.forget(SPACE_ID, request, idempotency_key="k", requested_by="user:u")
    await privacy.erase(Task(TaskName.ERASE, SPACE_ID, {"request_id": accepted.request_id}))
    assert f"exports/{SPACE_ID}/{mine.run_id}.zip" not in cell.objects.objects
    assert f"exports/{SPACE_ID}/{other.run_id}.zip" in cell.objects.objects
    with pytest.raises(NotFound):
        await privacy.download_package(SPACE_ID, UUID(mine.run_id))
    status = await privacy.erasure_status(SPACE_ID, UUID(accepted.request_id))
    erasures = cell.uow.store.module("privacy")["erasures"]
    assert next(iter(erasures.values())).counts["export_packages"] == 1
    assert status.status == "completed"


async def test_secrets_are_written_to_the_vault_only(cell, make_privacy) -> None:
    privacy = make_privacy()
    assert await privacy.put_secret(SPACE_ID, "webhook", "wh-1", "s3cr3t") == 1
    assert cell.secrets.values[secret_ref(SPACE_ID, "webhook", "wh-1")] == "s3cr3t"
    await privacy.put_destination_credential(SPACE_ID, "lake", "role-arn")
    assert cell.secrets.values[LAKE_CREDENTIAL] == "role-arn"
    with pytest.raises(NotFound):
        await privacy.put_destination_credential(SPACE_ID, "nope", "x")


async def test_a_credential_that_can_read_the_bucket_is_never_kept(cell, make_privacy, destinations) -> None:
    # The destination credential must be write-only; one that lists the bucket is refused.
    privacy = make_privacy()
    destinations.readable.add("reads-too")
    with pytest.raises(InvalidInput, match="only permission to write"):
        await privacy.put_destination_credential(SPACE_ID, "lake", "reads-too")
    assert LAKE_CREDENTIAL not in cell.secrets.values


async def test_a_new_schedule_first_exports_the_months_in_the_cold_archive(
    cell, world: World, make_privacy, destinations
) -> None:
    # A schedule created after a month left the database still sends that month, once.
    marina, _ = world.person("+5511999990001")
    old = world.event(marina, "c-1", "an old turn", at=T0)
    privacy = make_privacy()
    await privacy.archive_events(Task(TaskName.ARCHIVE_EVENTS, SPACE_ID, {"month": "2026-06"}))
    assert old not in world.events
    recent = world.event(marina, "c-9", "july", at=T0.replace(month=7))
    cell.secrets.values[LAKE_CREDENTIAL] = "k"
    set_change_seq(cell, world.change_seq)
    await privacy.export_run(plan())
    cell.clock.advance(minutes=3)
    await privacy.export_run(scheduled(cell)[0])

    (run,) = (await privacy.export_runs(SPACE_ID, schedule_id="lake", cursor=None, limit=10)).items
    manifest = json.loads(destinations.store.objects[f"niadra/manifests/{run.run_id}.json"])
    assert manifest["archived_months"] == ["2026-06"]
    events = [
        row
        for f in manifest["files"]
        if f["table"] == "events"
        for row in from_parquet(destinations.store.objects[f["path"]])
    ]
    by_id = {r["event_id"]: r for r in events}
    assert set(by_id) == {str(old), str(recent)}
    assert (by_id[str(old)]["text"], by_id[str(old)]["change_seq"]) == ("an old turn", 0)
    assert by_id[str(old)]["kind"] == "message"

    # The next run starts where this one ended: the archive is never sent again.
    world.event(marina, "c-10", "august", at=T0.replace(month=8))
    set_change_seq(cell, world.change_seq)
    await privacy.export_run(plan())
    cell.clock.advance(minutes=3)
    await privacy.export_run(scheduled(cell)[-1])
    latest = (await privacy.export_runs(SPACE_ID, schedule_id="lake", cursor=None, limit=10)).items[0]
    assert latest.status == "completed"
    manifest = json.loads(destinations.store.objects[f"niadra/manifests/{latest.run_id}.json"])
    assert manifest["archived_months"] == []


async def test_a_schedule_never_writes_or_reads_a_secret_of_another_space(
    cell, make_privacy, destinations
) -> None:
    # The vault is the cell's: a schedule naming another space's secret would overwrite it with a value
    # this tenant chose, or open this tenant's destination with it.
    other = f"spaces/{uuid4()}/webhook/crm"
    cell.secrets.values[other] = "the-other-tenants-secret"
    privacy = make_privacy()
    cell.config.put(config_with_schedule(credential_ref=other))
    with pytest.raises(InvalidInput, match="a secret of this space"):
        await privacy.put_destination_credential(SPACE_ID, "lake", "attacker-known")
    assert cell.secrets.values[other] == "the-other-tenants-secret"

    await privacy.export_run(plan())
    cell.clock.advance(minutes=3)
    await privacy.export_run(scheduled(cell)[0])
    (run,) = (await privacy.export_runs(SPACE_ID, schedule_id="lake", cursor=None, limit=10)).items
    assert run.status == "failed"
    assert destinations.opened == []
