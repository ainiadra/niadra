"""Applying the signed snapshot and serving it from memory (4.7)."""

import dataclasses
import json
from uuid import UUID, uuid4

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from niadra.app.config import CachedConfigProvider, ConfigService
from niadra.config.models import ExportSchedule, SpaceConfig
from niadra.domain.common.errors import Conflict, InvalidInput, NotFound
from niadra.domain.config.signing import sign
from niadra.domain.spaces.directory import DbStatus, new_entry
from niadra.domain.tasks import TaskName
from niadra.testing.cell import SPACE_ID, Cell, sample_config
from tests.unit.config.conftest import CELL_ID, SnapshotFactory


@pytest.fixture
def service(cell: Cell, control_key: Ed25519PrivateKey) -> ConfigService:
    return ConfigService(cell.uow, [control_key.public_key()], CELL_ID, cell_version="1.4.0")


async def test_a_valid_snapshot_is_applied_and_served(
    cell: Cell, service: ConfigService, make_snapshot: SnapshotFactory
) -> None:
    other = sample_config(uuid4())
    applied = await service.apply(make_snapshot(3, sample_config(), other))
    assert (applied.version, applied.spaces) == (3, 2)
    provider = CachedConfigProvider(cell.uow, CELL_ID)
    await provider.refresh()
    assert provider.version == 3
    assert provider.space(SPACE_ID) == sample_config()
    assert {c.space_id for c in provider.spaces()} == {SPACE_ID, other.space_id}
    with pytest.raises(NotFound):
        provider.space(uuid4())


async def test_a_bad_signature_changes_nothing(
    cell: Cell, service: ConfigService, make_snapshot: SnapshotFactory
) -> None:
    await service.apply(make_snapshot(1, sample_config()))
    forged = make_snapshot(2, sample_config(uuid4()), key=Ed25519PrivateKey.generate())
    with pytest.raises(InvalidInput, match="bad signature"):
        await service.apply(forged)
    assert await service.active_version() == 1


async def test_one_invalid_space_rejects_the_whole_snapshot(
    service: ConfigService, make_snapshot: SnapshotFactory
) -> None:
    await service.apply(make_snapshot(1, sample_config()))
    config = sample_config()
    broken = config.model_copy(update={"sources": [*config.sources, config.sources[0]]})
    with pytest.raises(InvalidInput, match="duplicate source"):
        await service.apply(make_snapshot(2, sample_config(uuid4()), broken))
    assert await service.active_version() == 1


async def test_documents_must_match_their_hash(
    service: ConfigService, make_snapshot: SnapshotFactory
) -> None:
    snapshot = make_snapshot(1, sample_config())
    [digest] = snapshot.documents
    tampered = json.loads(snapshot.documents[digest])
    tampered["region"] = "us-west-1"
    with pytest.raises(InvalidInput, match="does not match"):
        await service.apply(dataclasses.replace(snapshot, documents={digest: json.dumps(tampered).encode()}))
    with pytest.raises(InvalidInput, match="missing"):
        await service.apply(dataclasses.replace(snapshot, documents={}))


async def test_older_versions_other_cells_and_newer_code_requirements_are_refused(
    service: ConfigService, make_snapshot: SnapshotFactory
) -> None:
    await service.apply(make_snapshot(5, sample_config()))
    with pytest.raises(Conflict):
        await service.apply(make_snapshot(5, sample_config()))
    with pytest.raises(InvalidInput, match="another cell"):
        await service.apply(make_snapshot(6, sample_config(), cell_id="other-cell"))
    with pytest.raises(InvalidInput, match="newer cell"):
        await service.apply(make_snapshot(6, sample_config(), min_cell_version="1.10.0"))
    assert (await service.apply(make_snapshot(6, sample_config(), min_cell_version="1.4.0"))).version == 6


async def test_a_space_that_left_the_cell_disappears_on_the_next_snapshot(
    cell: Cell, service: ConfigService, make_snapshot: SnapshotFactory
) -> None:
    leaving = sample_config(uuid4())
    await service.apply(make_snapshot(1, sample_config(), leaving))
    provider = CachedConfigProvider(cell.uow, CELL_ID)
    await provider.refresh()
    await service.apply(make_snapshot(2, sample_config()))
    await provider.refresh()
    assert [c.space_id for c in provider.spaces()] == [SPACE_ID]


def with_exports(config: SpaceConfig, *schedule_ids: str) -> SpaceConfig:
    exports = [
        ExportSchedule(
            schedule_id=schedule_id,
            destination_kind="s3",
            bucket="lake",
            region=config.region,
            tables=["facts"],
            interval_hours=6,
            credential_ref=f"spaces/{config.space_id}/export/{schedule_id}",
        )
        for schedule_id in schedule_ids
    ]
    return config.model_copy(update={"export_schedules": exports})


async def schedule_rows(cell: Cell) -> set[tuple[UUID, TaskName, str]]:
    # One in-memory store stands for every database, so this lists every space's schedules.
    async with cell.uow(SPACE_ID) as uow:
        return {(r.space_id, r.task, r.key) for r in await uow.pipeline.list_schedules()}


async def serving(cell: Cell, *configs: SpaceConfig) -> None:
    """Directory rows of databases that are ready, as the provisioner leaves them."""
    async with cell.uow.cell() as uow:
        for config in configs:
            entry = new_entry(config.space_id, config.tenant_id, config.environment, cell.clock.now())
            await uow.directory.insert(
                entry.moved(DbStatus.READY, schema_version="head", ready_at=cell.clock.now()),
                cell.clock.now(),
            )


async def test_applying_registers_each_serving_space_schedules_and_drops_removed_exports(
    cell: Cell, service: ConfigService, make_snapshot: SnapshotFactory
) -> None:
    other = sample_config(uuid4())
    await serving(cell, sample_config(), other)
    await service.apply(make_snapshot(1, with_exports(sample_config(), "lake", "siem"), other))
    rows = await schedule_rows(cell)
    assert (SPACE_ID, TaskName.EXPORT_RUN, "siem") in rows
    assert (other.space_id, TaskName.RETENTION_SWEEP, "") in rows
    assert (other.space_id, TaskName.PARTITION_MAINTENANCE, "") in rows
    # Per space: the minute, hourly and daily jobs, and the upkeep of its own database.
    assert len(rows) == 2 * 9 + 2

    await service.apply(make_snapshot(2, with_exports(sample_config(), "lake"), other))
    rows = await schedule_rows(cell)
    assert {key for space, task, key in rows if space == SPACE_ID and task is TaskName.EXPORT_RUN} == {"lake"}
    assert {task for space, task, _ in rows if space == SPACE_ID} >= {
        TaskName.USAGE_ROLLUP,
        TaskName.ROLLUP,
        TaskName.AUDIT_ANCHOR,
        TaskName.RETENTION_SWEEP,
        TaskName.IDENTITY_SUGGEST,
        TaskName.ARCHIVE_EVENTS,
        TaskName.PARTITION_MAINTENANCE,
        TaskName.DEDUPE_ROTATE,
    }


async def test_a_space_whose_database_is_not_ready_gets_its_schedules_from_the_provisioner(
    cell: Cell, service: ConfigService, make_snapshot: SnapshotFactory
) -> None:
    applied = await service.apply(make_snapshot(1, with_exports(sample_config(), "lake")))
    assert applied.spaces == 1
    assert await schedule_rows(cell) == set()


async def test_a_rejected_snapshot_leaves_the_schedules_alone(
    cell: Cell, service: ConfigService, make_snapshot: SnapshotFactory
) -> None:
    await serving(cell, sample_config())
    await service.apply(make_snapshot(3, with_exports(sample_config(), "lake")))
    before = await schedule_rows(cell)
    with pytest.raises(Conflict):
        await service.apply(make_snapshot(2, sample_config()))
    assert await schedule_rows(cell) == before


async def test_the_provider_keeps_the_last_good_copy_of_a_document_that_stops_parsing(
    cell: Cell, service: ConfigService, make_snapshot: SnapshotFactory
) -> None:
    await service.apply(make_snapshot(1, sample_config()))
    provider = CachedConfigProvider(cell.uow, CELL_ID)
    await provider.refresh()
    # A document stored by an older cell version that the current models no longer accept.
    async with cell.uow.cell() as uow:
        [doc] = await uow.config.documents("space_config")
        await uow.config.save_snapshot(CELL_ID, 2, {}, b"")
        await uow.config.replace_documents(
            "space_config", [dataclasses.replace(doc, version=2, document={**doc.document, "region": None})]
        )
    await provider.refresh()
    assert provider.version == 2
    assert provider.space(SPACE_ID) == sample_config()


async def test_cache_epoch_is_read_and_bumped(cell: Cell, service: ConfigService) -> None:
    provider = CachedConfigProvider(cell.uow, CELL_ID)
    assert await service.cache_epoch() == 1
    assert await service.bump_cache_epoch() == 2
    await provider.refresh()
    assert provider.cache_epoch == 2


def test_the_revocation_feed_is_verified_like_the_snapshot(
    service: ConfigService, control_key: Ed25519PrivateKey
) -> None:
    feed = {"cell_id": CELL_ID, "seq": 9, "keys": [{"space_id": str(SPACE_ID), "key_id": "k_whatsapp-bot"}]}
    parsed = service.verify_revocations(json.dumps(feed).encode(), sign(feed, control_key))
    assert (parsed.seq, parsed.keys[0].key_id) == (9, "k_whatsapp-bot")
    other = {**feed, "cell_id": "elsewhere"}
    with pytest.raises(InvalidInput, match="another cell"):
        service.verify_revocations(json.dumps(other).encode(), sign(other, control_key))
    with pytest.raises(InvalidInput, match="bad signature"):
        service.verify_revocations(json.dumps(feed).encode(), sign(other, control_key))


def test_space_config_documents_round_trip_through_json() -> None:
    assert (
        SpaceConfig.model_validate(json.loads(json.dumps(sample_config().model_dump(mode="json"))))
        == sample_config()
    )


async def test_applying_names_the_spaces_whose_configuration_changed(
    service: ConfigService, make_snapshot: SnapshotFactory
) -> None:
    # The receipt chain of each space records a configuration the control plane applied.
    other = sample_config(uuid4())
    first = await service.apply(make_snapshot(1, sample_config(), other))
    assert first.changed == ()  # the starting point of a cell is not a change anyone made
    changed = sample_config().model_copy(update={"region": "sa-east-1"})
    second = await service.apply(make_snapshot(2, changed, other))
    assert [space for space, _ in second.changed] == [SPACE_ID]


async def test_a_serving_space_whose_configuration_changed_goes_to_the_history_and_a_failure_is_not_fatal(
    cell: Cell, service: ConfigService, make_snapshot: SnapshotFactory
) -> None:
    recorded: list[tuple[UUID, int]] = []

    class History:
        async def record_config(self, config: SpaceConfig, version: int, at: object) -> None:
            if config.space_id != SPACE_ID:
                raise RuntimeError("the space database refused the write")
            recorded.append((config.space_id, version))

    service.history = History()
    other = sample_config(uuid4())
    await serving(cell, sample_config(), other)
    await service.apply(make_snapshot(1, sample_config(), other))
    await service.apply(make_snapshot(2, sample_config(), other))
    moved = sample_config().model_copy(update={"region": "sa-east-1"})
    await service.apply(make_snapshot(3, moved, other.model_copy(update={"region": "sa-east-1"})))
    assert recorded == [(SPACE_ID, 3)], "only a change, not the cell's starting point nor a repeat"
    assert await service.active_version() == 3


async def test_the_provider_knows_the_spaces_that_moved_away(
    cell: Cell, service: ConfigService, make_snapshot: SnapshotFactory
) -> None:
    # The edge answers 421 for them.
    gone = uuid4()
    await service.apply(make_snapshot(1, sample_config(), moved={str(gone): "cell-b"}))
    provider = CachedConfigProvider(cell.uow, CELL_ID)
    await provider.refresh()
    assert provider.moved() == {gone: "cell-b"}
