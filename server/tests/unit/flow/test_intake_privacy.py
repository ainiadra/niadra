"""What was acknowledged and not stored yet is in every erasure and every package (5.2, 8.4, 5.14): both store
the space's pending batches first, so a turn sent before the request is erased or exported with the rest,
never stored after it."""

import io
import json
import zipfile
from typing import Any
from uuid import UUID

from niadra.adapters.memory.fakes import ScriptedLlm
from niadra.contracts.common import Handle
from niadra.contracts.privacy import ExportRequest, ForgetRequest
from niadra.domain.privacy.erasure import ErasureStatus
from niadra.domain.tasks import TaskName
from niadra.domain.vocabulary import HandleType
from niadra.testing.cell import SPACE_ID, WHATSAPP_SOURCE, Cell
from tests.unit.flow.harness import Flow
from tests.unit.flow.test_first_read import PHONE, remembered, said
from tests.unit.ingest.fakes import stop_storing

SUBJECT = Handle(type=HandleType.PHONE_E164, value=PHONE)


async def acknowledged_but_not_stored(cell: Cell) -> Flow:
    flow = Flow.build(cell, ScriptedLlm(remembered()))
    now = cell.clock.now().isoformat()
    await flow.send(
        WHATSAPP_SOURCE, said("wa-1", "Me chama de Rafa, preciso da segunda via", now, channel="whatsapp")
    )
    await flow.drain()
    flow.ingest._workers = cell.bus  # as production wires it: the answer hands the store to a worker
    later = said("wa-1", "o pedido novo e o 734211", cell.clock.now().isoformat(), channel="whatsapp")
    assert (await flow.send(WHATSAPP_SOURCE, later)).accepted == 1
    stop_storing(flow.ingest)  # the process that answered has not stored it yet
    assert cell.uow.store.data["ingest"]["intake"]
    return flow


def texts(cell: Cell) -> list[str | None]:
    return [e.record.text for e in cell.uow.store.data["ingest"]["events"].values()]


async def test_an_erasure_takes_the_turns_acknowledged_before_it(cell: Cell) -> None:
    flow = await acknowledged_but_not_stored(cell)

    accepted = await flow.privacy.forget(
        SPACE_ID,
        ForgetRequest(target="handle", handle=SUBJECT),
        idempotency_key="f1",
        requested_by="user:dpo",
    )
    # The relay has not brought the pending batch back yet: the erasure stores it itself.
    await flow.drain(skip={TaskName.APPLY_INTAKE})

    out = await flow.privacy.erasure_status(SPACE_ID, UUID(accepted.request_id))
    assert out.status == ErasureStatus.COMPLETED.value
    assert cell.uow.store.data["ingest"]["intake"] == {}
    assert texts(cell) == []
    await flow.drain()
    assert texts(cell) == []


async def test_a_package_holds_the_turns_acknowledged_before_it(cell: Cell) -> None:
    flow = await acknowledged_but_not_stored(cell)
    profile_id = next(iter(cell.uow.store.data["identity"]["profiles"]))

    made = await flow.privacy.export_package(SPACE_ID, ExportRequest(handle=SUBJECT), requested_by="user:dpo")

    data = await flow.privacy.download_package(SPACE_ID, UUID(made.run_id))
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        events: list[dict[str, Any]] = [
            json.loads(line) for line in archive.read("events.jsonl").splitlines()
        ]
    assert any("734211" in (e.get("text") or "") for e in events), profile_id


class KeptRow:
    """A module that keeps a row aside, as ingest and turns do after every attempt failed."""

    def __init__(self) -> None:
        self.scopes: list[Any] = []

    async def fence(self, space_id: UUID, scope: Any) -> None:
        pass

    async def purge(self, space_id: UUID, scope: Any) -> None:
        pass

    async def kept_for(self, space_id: UUID, scope: Any) -> list[dict[str, Any]]:
        self.scopes.append(scope)
        return [{"event_id": "e-kept", "text": "o pedido novo e o 734211", "kept_aside": True}]


async def test_a_package_holds_what_was_kept_aside_for_the_subject(cell: Cell) -> None:
    """A row set aside after failing every attempt is never deleted, and the subject's access package holds
    what it keeps about them: leaving it out cut the package."""
    flow = await acknowledged_but_not_stored(cell)
    await flow.drain()
    keeper = KeptRow()
    flow.privacy._fences = (*flow.privacy._fences, keeper)

    made = await flow.privacy.export_package(SPACE_ID, ExportRequest(handle=SUBJECT), requested_by="user:dpo")

    data = await flow.privacy.download_package(SPACE_ID, UUID(made.run_id))
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        kept = [json.loads(line) for line in archive.read("kept_aside.jsonl").splitlines()]
    assert kept == [{"event_id": "e-kept", "text": "o pedido novo e o 734211", "kept_aside": True}]
    [scope] = keeper.scopes
    profile_id = next(iter(cell.uow.store.data["identity"]["profiles"]))
    assert scope.profile_id == profile_id
    assert scope.conversation_hashes, "the subject's conversations, as an erasure finds their turns"


class Recorded:
    def __init__(self) -> None:
        self.scopes: list[Any] = []

    async def fence(self, space_id: UUID, scope: Any) -> None:
        pass

    async def purge(self, space_id: UUID, scope: Any) -> None:
        pass

    async def recorded_for(self, space_id: UUID, scope: Any) -> list[dict[str, Any]]:
        self.scopes.append(scope)
        return [{"turn_id": "t-1", "record": {"turn_id": "t-1"}}]


async def test_a_package_holds_the_recordings_of_the_subjects_conversations(cell: Cell) -> None:
    flow = await acknowledged_but_not_stored(cell)
    await flow.drain()
    recorder = Recorded()
    flow.privacy._fences = (*flow.privacy._fences, recorder)

    made = await flow.privacy.export_package(SPACE_ID, ExportRequest(handle=SUBJECT), requested_by="user:dpo")

    data = await flow.privacy.download_package(SPACE_ID, UUID(made.run_id))
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        recorded = [json.loads(line) for line in archive.read("turn_recordings.jsonl").splitlines()]
    assert recorded == [{"turn_id": "t-1", "record": {"turn_id": "t-1"}}]
    [scope] = recorder.scopes
    assert scope.conversation_hashes
