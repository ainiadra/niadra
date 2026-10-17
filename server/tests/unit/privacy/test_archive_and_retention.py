import json
from datetime import timedelta
from typing import Any, cast
from uuid import UUID, uuid4

from niadra.contracts.privacy import ForgetRequest
from niadra.domain.common.ids import uuid7
from niadra.domain.ingest.intake import IntakeRow
from niadra.domain.notify.webhooks import Delivery, DeliveryStatus
from niadra.domain.privacy.erasure import ErasedRow
from niadra.domain.privacy.export import ExportRun, ExportStatus
from niadra.domain.privacy.files import from_parquet
from niadra.domain.review.sampling import ReviewSampleRecord
from niadra.domain.tasks import Task, TaskName
from niadra.domain.triggers.firing import Firing
from niadra.domain.turns.records import NewTurnIntake
from niadra.entrypoints.setup import OBJECT_EXPIRY_DAYS
from niadra.testing.cell import SPACE_ID

from .world import T0, World

RAW_KINDS = ("raw", "media", "measure")


def archive_task(month: str | None = "2026-06") -> Task:
    return Task(TaskName.ARCHIVE_EVENTS, SPACE_ID, {"month": month} if month else {})


async def test_archive_writes_monthly_parts_with_content_encrypted_per_row(
    cell, world: World, make_privacy
) -> None:
    marina, _ = world.person("+5511999990001")
    inside = [world.event(marina, "c-1", f"turn {i}", at=T0 + timedelta(hours=i)) for i in range(3)]
    world.event(marina, "c-9", "july", at=T0.replace(month=7))
    await make_privacy().archive_events(archive_task())

    index = json.loads(cell.objects.objects[f"archive/events/{SPACE_ID}/index.json"])
    parts = index["months"]["2026-06"]
    assert [p["rows"] for p in parts] == [2, 1]  # archive_part_rows=2 in the fixture
    rows = [r for p in parts for r in from_parquet(cell.objects.objects[p["key"]])]
    assert sorted(r["event_id"] for r in rows) == sorted(str(e) for e in inside)
    assert all(b"turn" not in r["content_ct"] for r in rows)
    row = rows[0]
    plain = await cell.keys.decrypt(
        SPACE_ID, row["content_ct"], f"archive_events:{row['event_id']}:content".encode(), row["dek_version"]
    )
    assert json.loads(plain)["text"].startswith("turn")


async def test_an_archived_month_leaves_the_database_and_is_never_rewritten(
    cell, world: World, make_privacy
) -> None:
    # Past the hot window the events live only in the cold archive.
    marina, _ = world.person("+5511999990001")
    for i in range(3):
        world.event(marina, "c-1", f"turn {i}", at=T0 + timedelta(hours=i))
    july = world.event(marina, "c-9", "july", at=T0.replace(month=7))
    privacy = make_privacy()
    await privacy.archive_events(archive_task())
    assert list(world.events) == [july]
    index = json.loads(cell.objects.objects[f"archive/events/{SPACE_ID}/index.json"])
    assert index["purged"]["2026-06"]["rows"] == 3
    before = {k: v for k, v in cell.objects.objects.items() if "/2026-06/" in k}

    # The daily rerun finds the month archived: its rows are only in the archive now, so it stays.
    await privacy.archive_events(archive_task())
    assert {k: v for k, v in cell.objects.objects.items() if "/2026-06/" in k} == before


async def test_a_run_that_stopped_before_the_purge_finishes_it(cell, world: World, make_privacy) -> None:
    marina, _ = world.person("+5511999990001")
    world.event(marina, "c-1", "turn", at=T0)
    part = f"archive/events/{SPACE_ID}/2026-06/part-00000.parquet"
    cell.objects.objects[part] = b"archived"
    index = {"months": {"2026-06": [{"key": part, "rows": 1, "sha256": "x"}]}}
    cell.objects.objects[f"archive/events/{SPACE_ID}/index.json"] = json.dumps(index).encode()
    await make_privacy().archive_events(archive_task())
    assert world.events == {}
    assert cell.objects.objects[part] == b"archived"  # never rewritten from a half-purged month


async def test_the_default_month_is_the_last_one_before_the_hot_window(
    cell, world: World, make_privacy
) -> None:
    marina, _ = world.person("+5511999990001")
    world.event(marina, "c-1", "old", at=T0)
    privacy = make_privacy()
    cell.clock.at = T0.replace(month=9, day=15)  # 90 hot days back is mid-June, so May is archived
    await privacy.archive_events(archive_task(None))
    index = json.loads(cell.objects.objects[f"archive/events/{SPACE_ID}/index.json"])
    assert list(index["months"]) == ["2026-05"]


async def test_every_month_behind_the_hot_window_leaves_the_database(
    cell, world: World, make_privacy
) -> None:
    # The daily run took only the newest month past the window; months behind it (a shorter window,
    # a missed run) stayed in the database for good. Each run now queues the oldest month left, one by one.
    marina, _ = world.person("+5511999990001")
    for month in (3, 4, 5, 6):
        world.event(marina, f"c-{month}", "old", at=T0.replace(month=month))
    privacy = make_privacy()
    cell.clock.at = T0.replace(month=9, day=15)  # the daily run's month is May
    outbox = cell.uow.store.module("pipeline").setdefault("outbox", [])
    await privacy.archive_events(archive_task(None))
    ran = ["2026-05"]
    while queued := [t for t in outbox if t.name is TaskName.ARCHIVE_EVENTS]:
        [task] = queued
        outbox.remove(task)
        ran.append(task.payload["month"])
        await privacy.archive_events(task)
    assert ran[0] == "2026-05"
    assert sorted(ran[1:]) == ["2026-03", "2026-04"]  # each once, one run per month
    assert [e["at"].month for e in world.events.values()] == [6]  # June is inside the window
    index = json.loads(cell.objects.objects[f"archive/events/{SPACE_ID}/index.json"])
    assert sorted(index["purged"]) == ["2026-03", "2026-04", "2026-05"]


async def test_erase_cold_rewrites_only_the_files_with_the_target(cell, world: World, make_privacy) -> None:
    marina, _ = world.person("+5511999990001")
    joao, _ = world.person("+5511999990002")
    world.event(marina, "c-1", "a", at=T0)
    world.event(joao, "c-2", "b", at=T0 + timedelta(hours=1))
    world.event(joao, "c-2", "c", at=T0 + timedelta(hours=2))
    privacy = make_privacy()
    await privacy.archive_events(archive_task())
    index = json.loads(cell.objects.objects[f"archive/events/{SPACE_ID}/index.json"])
    untouched_key = next(
        p["key"]
        for p in index["months"]["2026-06"]
        if all(str(marina) not in r["handle_ids"] for r in from_parquet(cell.objects.objects[p["key"]]))
    )
    untouched = cell.objects.objects[untouched_key]

    request = ForgetRequest.model_validate(
        {"target": "handle", "handle": {"type": "phone_e164", "value": "+5511999990001"}}
    )
    accepted = await privacy.forget(SPACE_ID, request, idempotency_key="k", requested_by="user:u")
    await privacy.erase_cold(Task(TaskName.ERASE_COLD, SPACE_ID, {"request_id": accepted.request_id}))

    index = json.loads(cell.objects.objects[f"archive/events/{SPACE_ID}/index.json"])
    rows = [r for p in index["months"]["2026-06"] for r in from_parquet(cell.objects.objects[p["key"]])]
    assert all(str(marina) not in r["handle_ids"] for r in rows)
    assert len(rows) == 2
    assert cell.objects.objects[untouched_key] == untouched
    erasures = cell.uow.store.module("privacy")["erasures"]
    assert next(iter(erasures.values())).counts["cold_rows"] == 1


async def test_retention_sweep_expires_privacy_data_by_class(cell, world: World, make_privacy) -> None:
    privacy = make_privacy()
    old, recent = T0 - timedelta(days=400), T0 - timedelta(days=10)
    async with cell.uow(SPACE_ID) as uow:
        await uow.privacy.add_tombstones(SPACE_ID, [ErasedRow("events", {"event_id": "1"})], old)
        await uow.privacy.add_tombstones(SPACE_ID, [ErasedRow("events", {"event_id": "2"})], recent)
        await uow.privacy.create_export_run(
            SPACE_ID, ExportRun(uuid4(), "lake", 0, 1, ExportStatus.COMPLETED, old)
        )
        await uow.privacy.create_export_run(
            SPACE_ID, ExportRun(uuid4(), "lake", 1, 2, ExportStatus.COMPLETED, recent)
        )
    cell.objects.objects[f"archive/events/{SPACE_ID}/2020-01/part-00000.parquet"] = b"x"
    cell.objects.objects[f"archive/events/{SPACE_ID}/2026-05/part-00000.parquet"] = b"y"
    index = {"months": {"2020-01": [{"key": "a"}], "2026-05": [{"key": "b"}]}}
    cell.objects.objects[f"archive/events/{SPACE_ID}/index.json"] = json.dumps(index).encode()

    await privacy.retention_sweep(Task(TaskName.RETENTION_SWEEP, SPACE_ID))

    async with cell.uow(SPACE_ID) as uow:
        assert [t.key for t in await uow.privacy.tombstones_between(SPACE_ID, 0, None)] == [{"event_id": "2"}]
        runs = await uow.privacy.export_runs(SPACE_ID, schedule_id=None, before_id=None, limit=10)
        assert [r.started_at for r in runs] == [recent]
    # The company's events are kept until it sets a window (memory never cuts): they went after
    # five years by default.
    assert any("2020-01" in k for k in cell.objects.objects)
    config = cell.config.space(SPACE_ID)
    settings = config.settings.model_copy(update={"retention_days": {"cold_events": 5 * 365}})
    cell.config.put(config.model_copy(update={"settings": settings}))
    await privacy.retention_sweep(Task(TaskName.RETENTION_SWEEP, SPACE_ID))
    assert not any("2020-01" in k for k in cell.objects.objects)
    assert json.loads(cell.objects.objects[f"archive/events/{SPACE_ID}/index.json"])["months"].keys() == {
        "2026-05"
    }


def test_no_bucket_rule_ages_raw_content_out() -> None:
    # Raw payloads, media and raw interactions stay until an erasure or the customer's `raw_content` window.
    raw = ("raw/", "media/", "measure/raw/")
    assert [p for p in OBJECT_EXPIRY_DAYS for k in raw if p.startswith(k) or k.startswith(p)] == []


async def test_raw_content_is_kept_however_old_until_the_customer_sets_a_retention(
    cell, make_privacy
) -> None:
    # Never cut: an unmapped payload, a person's raw payload and media and their raw interactions stay past
    # the 7, 45 and 90 days that used to remove them; once the customer sets `raw_content`, older ones go.
    privacy = make_privacy()

    def day(days: int) -> str:
        return f"{(T0 - timedelta(days=days)).date():%Y-%m-%d}"

    handle = uuid4()
    for days in (3, 8, 12, 400):
        cell.objects.objects[f"raw/{SPACE_ID}/_/{day(days)}/payload-{days}"] = b"{}"
    cell.objects.objects[f"raw/{SPACE_ID}/{handle}/{day(400)}/payload"] = b"{}"
    cell.objects.objects[f"measure/raw/{SPACE_ID}/{handle}/{day(60)}/interactions.bin"] = b"sealed"
    uploaded = uuid7(int((T0 - timedelta(days=100)).timestamp() * 1000))
    cell.objects.objects[f"media/{SPACE_ID}/{handle}/{uploaded}"] = b"ogg"

    def raw() -> list[str]:
        return sorted(k.rsplit("/", 1)[1] for k in cell.objects.objects if k.split("/")[0] in RAW_KINDS)

    kept = raw()
    await privacy.retention_sweep(Task(TaskName.RETENTION_SWEEP, SPACE_ID))
    assert raw() == kept

    config = cell.config.space(SPACE_ID)
    settings = config.settings.model_copy(update={"retention_days": {"raw_content": 30}})
    cell.config.put(config.model_copy(update={"settings": settings}))
    await privacy.retention_sweep(Task(TaskName.RETENTION_SWEEP, SPACE_ID))
    assert raw() == ["payload-12", "payload-3", "payload-8"]


async def test_retention_sweep_expires_the_governance_logs_with_the_tenant_windows(
    cell, make_privacy
) -> None:
    privacy = make_privacy()
    config = cell.config.space(SPACE_ID)
    settings = config.settings.model_copy(update={"retention_days": {"review_samples": 30}})
    cell.config.put(config.model_copy(update={"settings": settings}))
    ancient, month_old, recent = T0 - timedelta(days=400), T0 - timedelta(days=40), T0 - timedelta(days=1)
    samples = [ReviewSampleRecord(uuid4(), uuid4(), "base", None, at) for at in (month_old, recent)]
    firings = [
        Firing(uuid4(), "r", "v1", uuid4(), f"w{i}", b"k", (), 1, at)
        for i, at in enumerate((ancient, month_old))
    ]
    deliveries = [
        Delivery(uuid4(), "crm", "trait.added", {}, b"h", DeliveryStatus.DELIVERED, at)
        for at in (month_old, recent)
    ]
    async with cell.uow(SPACE_ID) as uow:
        for sample in samples:
            await uow.review.add_sample(sample)
        for firing in firings:
            await uow.triggers.add_firing(firing)
        for delivery in deliveries:
            await uow.notify.add_delivery(delivery)

    await privacy.retention_sweep(Task(TaskName.RETENTION_SWEEP, SPACE_ID))

    async with cell.uow(SPACE_ID) as uow:
        assert await uow.review.get_sample(samples[0].sample_id) is None, "30 days, as the tenant set"
        assert await uow.review.get_sample(samples[1].sample_id) is not None
        assert [f.firing_id for f in await uow.triggers.list_firings()] == [firings[1].firing_id]
        assert [d.delivery_id for d in await uow.notify.list_deliveries()] == [deliveries[1].delivery_id]


async def test_the_subjects_package_reads_the_cold_archive_too(cell, world: World, make_privacy) -> None:
    # Access and portability do not stop at the hot window.
    import io
    import zipfile

    from niadra.contracts.privacy import ExportRequest

    marina, profile = world.person("+5511999990001")
    other, _ = world.person("+5511999990002")
    old = world.event(marina, "c-1", "an old turn", at=T0)
    world.event(other, "c-2", "someone else", at=T0)
    recent = world.event(marina, "c-9", "july", at=T0.replace(month=7))
    privacy = make_privacy()
    await privacy.archive_events(archive_task())
    assert old not in world.events  # the June turn lives only in the archive now
    package = await privacy.export_package(SPACE_ID, ExportRequest(profile_id=profile), requested_by="user:u")
    data = await privacy.download_package(SPACE_ID, UUID(package.run_id))
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        events = [json.loads(line) for line in archive.read("events.jsonl").splitlines()]
    by_id = {e["event_id"]: e for e in events}
    assert set(by_id) == {str(old), str(recent)}
    assert by_id[str(old)]["archived"] is True
    assert by_id[str(old)]["text"] == "an old turn"


async def test_a_package_has_no_cut_and_holds_each_event_once(
    cell, world: World, make_privacy, monkeypatch
) -> None:
    # Every section is whole, read a page at a time; a month archived but not yet purged is
    # still partly in the database, and its events appear once.
    import io
    import zipfile

    from niadra.app import privacy as module
    from niadra.contracts.privacy import ExportRequest

    monkeypatch.setattr(module, "PACKAGE_PAGE", 2)
    monkeypatch.setattr(module, "PACKAGE_CHUNK", 64)
    marina, profile = world.person("+5511999990001")
    june = [world.event(marina, "c-1", f"june {i}", at=T0 + timedelta(hours=i)) for i in range(3)]
    july = [
        world.event(marina, "c-9", f"july {i}", at=T0.replace(month=7) + timedelta(hours=i)) for i in range(5)
    ]
    saved = {e: dict(world.events[e]) for e in june}
    privacy = make_privacy()
    await privacy.archive_events(archive_task())
    # A purge that stopped halfway: one June event is still in the database and the month is not noted.
    world.events[june[0]] = saved[june[0]]
    key = f"archive/events/{SPACE_ID}/index.json"
    index = json.loads(cell.objects.objects[key])
    del index["purged"]["2026-06"]
    cell.objects.objects[key] = json.dumps(index).encode()

    package = await privacy.export_package(SPACE_ID, ExportRequest(profile_id=profile), requested_by="user:u")
    data = await privacy.download_package(SPACE_ID, UUID(package.run_id))
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        ids = [json.loads(line)["event_id"] for line in archive.read("events.jsonl").splitlines()]
        assert set(package.files) == set(archive.namelist())
    assert sorted(ids) == sorted(str(e) for e in [*june, *july])


async def test_a_batch_kept_aside_lives_as_long_as_the_companys_events(
    cell, world: World, make_privacy
) -> None:
    # A batch that failed every attempt is kept, never deleted, until the window of the
    # events it would have become has passed; events are kept until the company sets one (memory never cuts).
    privacy = make_privacy()
    old, recent = T0 - timedelta(days=6 * 365), T0 - timedelta(days=10)
    turns: dict[str, UUID] = {}
    for name, at in (("old", old), ("recent", recent)):
        events = uuid4()
        world.kept_intake[events] = IntakeRow(events, at, b"x", 1, frozenset(), 5, uuid4(), at)
        turns[name] = uuid4()
        async with cell.uow(SPACE_ID) as uow:
            await cast(Any, uow.turns).accept_intake(
                NewTurnIntake(turns[name], SPACE_ID, at, uuid4(), 1, b"x", 1)
            )
            await uow.turns.set_aside_intake(turns[name], at)
    recent_events = [i for i, r in world.kept_intake.items() if r.received_at == recent]

    await privacy.retention_sweep(Task(TaskName.RETENTION_SWEEP, SPACE_ID))
    assert len(world.kept_intake) == 2, "no window set: nothing goes"

    config = cell.config.space(SPACE_ID)
    settings = config.settings.model_copy(update={"retention_days": {"cold_events": 5 * 365}})
    cell.config.put(config.model_copy(update={"settings": settings}))
    await privacy.retention_sweep(Task(TaskName.RETENTION_SWEEP, SPACE_ID))

    assert list(world.kept_intake) == recent_events
    async with cell.uow(SPACE_ID) as uow:
        assert [r.intake_id for r in await uow.turns.failed_intake(10)] == [turns["recent"]]
