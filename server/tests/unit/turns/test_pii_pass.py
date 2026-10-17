"""The PII model's pass over stored tool arguments (24.3.5): only where the space asks for it, only `stored`
records and only their arguments, one task per group of up to 64 KB, and a record written again only when
the model found something, with its digests following what it keeps."""

from collections.abc import Sequence
from typing import Any

from niadra.adapters.memory.turns import TransactionTurnIntakeLog
from niadra.app.turns import TurnService
from niadra.config.recording import RecordingSettings
from niadra.domain.tasks import Task, TaskName
from niadra.domain.turns.digest import digest
from niadra.domain.turns.pii import SCAN_BYTES, Scan, batches
from niadra.ports.infra import PiiSpan
from niadra.ports.turns import TurnConsumers
from tests.unit.turns.conftest import World
from tests.unit.turns.records import QUOTE_RESULT, turn

T1, T2 = "0192f5a0-7c3e-7a10-9d2b-5e61a4c0d001", "0192f5a0-7c3e-7a10-9d2b-5e61a4c0d002"
ARGS = {"plan": "family", "note": "a proposta da Maria Souza", "ages": [34, 36], "network": "regional"}


class NameModels:
    """Finds the one person name the fixtures use; counts what it read."""

    def __init__(self) -> None:
        self.read: list[str] = []

    async def pii(self, texts: Sequence[str]) -> list[list[PiiSpan]]:
        self.read += texts
        return [
            [PiiSpan(t.index("Maria"), t.index("Maria") + 11, "person")] if "Maria" in t else []
            for t in texts
        ]


def _service(world: World, models: NameModels, **recording: Any) -> TurnService:
    world.configure(sample_rate=0, **recording)
    return TurnService(
        world.cell.uow,
        TransactionTurnIntakeLog(world.cell.uow),
        world.cell.keys,
        world.cell.objects,
        world.cell.clock,
        world.cell.config,
        world.audit,  # type: ignore[arg-type]
        consumers=TurnConsumers(),
        models=models,  # type: ignore[arg-type]
    )


def _scans(world: World) -> list[Task]:
    outbox = world.cell.uow.store.module("pipeline").get("outbox", [])
    return [t for t in outbox if t.name == TaskName.SCAN_TURN_ARGUMENTS]


def test_a_groups_records_go_in_tasks_of_at_most_64_kb_of_arguments() -> None:
    scans = [Scan(f"t{i}", "aa", size) for i, size in enumerate((30_000, 30_000, 10_000, 100_000, 1))]
    assert [[s.turn_id for s in b] for b in batches(scans)] == [["t0", "t1"], ["t2"], ["t3"], ["t4"]]
    assert SCAN_BYTES == 64 * 1024


async def test_what_the_model_finds_in_stored_arguments_is_replaced_and_the_rest_stays(world: World) -> None:
    models = NameModels()
    service = _service(world, models, pii_model=True)
    await service.record(world.caller, [turn(T1, args=ARGS), turn(T2, mode="pointer", args=ARGS)])
    [task] = _scans(world)
    assert [t["turn_id"] for t in task.payload["turns"]] == [T1], "a pointer record holds no value here"

    await service.scan_arguments(task)
    view = await service.read(world.caller, T1)
    args, result = view.record.blobs["b:1"], view.record.blobs["b:2"]
    assert args.content == {**ARGS, "note": "a proposta da [person_1]"}
    assert args.sha256 == digest(args.content)[0], "the digest follows what the record keeps"
    assert result.content == QUOTE_RESULT, "results are not read"
    assert "a proposta da Maria Souza" in models.read
    assert "family" in models.read
    assert all(len(t) >= 3 for t in models.read), "a leaf too short to hold a name is not read"

    stored = dict(world.cell.objects.objects)
    await service.scan_arguments(task)
    assert world.cell.objects.objects == stored, "a second pass finds placeholders only and writes nothing"


async def test_a_space_that_did_not_ask_gets_no_pass(world: World) -> None:
    service = _service(world, NameModels())
    await service.record(world.caller, [turn(T1, args=ARGS)])
    assert _scans(world) == []
    assert RecordingSettings().pii_model is False
