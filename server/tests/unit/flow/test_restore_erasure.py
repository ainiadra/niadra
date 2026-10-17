"""A space restored from a dump taken before an erasure runs that erasure again: the dump
brings back what was erased and forgets the request, and the ledger in object storage does not."""

import copy
from typing import Any
from uuid import UUID

import pytest

from niadra.adapters.memory.fakes import ScriptedLlm
from niadra.app.backups import BackupService
from niadra.contracts.common import Handle
from niadra.contracts.privacy import ForgetRequest
from niadra.domain.privacy.erasure import ErasureStatus, ledger_prefix
from niadra.domain.tasks import Task, TaskName
from niadra.domain.vocabulary import HandleType
from niadra.testing.cell import WHATSAPP_SOURCE, Cell
from tests.unit.flow.harness import Flow
from tests.unit.flow.test_first_read import PHONE, remembered, said

SUBJECT = Handle(type=HandleType.PHONE_E164, value=PHONE)


@pytest.fixture
async def flow(cell: Cell) -> Flow:
    flow = Flow.build(cell, ScriptedLlm(remembered()))
    now = cell.clock.now().isoformat()
    ended = {"type": "conversation.ended", "idempotency_key": "wa-1-end", "conversation_id": "wa-1"}
    first = said("wa-1", "Me chama de Rafa, preciso da segunda via", now, channel="whatsapp")
    assert (await flow.send(WHATSAPP_SOURCE, first, {**ended, "occurred_at": now})).errors == []
    await flow.drain()
    return flow


async def facts(flow: Flow) -> list[Any]:
    space = flow.cell.config.spaces()[0].space_id
    async with flow.cell.uow(space) as uow:
        found = await uow.identity.find_handle(HandleType.PHONE_E164, "", PHONE)
        return await uow.memory.facts_for([found.handle_id]) if found else []


async def test_a_restore_runs_again_the_erasures_its_dump_came_before(flow: Flow) -> None:
    space = flow.cell.config.spaces()[0].space_id
    assert [f.value for f in await facts(flow)] == ["Rafa"]
    dump = copy.deepcopy(flow.cell.uow.store.data)  # the nightly dump, before the request

    request = ForgetRequest(target="handle", handle=SUBJECT)
    done = await flow.privacy.forget(space, request, idempotency_key="forget-rafa", requested_by="user:dpo")
    await flow.drain()
    assert await facts(flow) == []
    assert await flow.cell.objects.list_keys(ledger_prefix(space))

    flow.cell.uow.store.data = dump  # restore-space puts the dump in place
    assert [f.value for f in await facts(flow)] == ["Rafa"]
    admin: Any = None  # the database side of the restore is covered in tests/integration/spaces
    backups = BackupService(
        flow.cell.uow, admin, flow.cell.objects, flow.cell.clock, cell_database="cell", keys=flow.cell.keys
    )
    assert await backups.reapply_erasures(space, "backups/s_x/dump") == [UUID(done.request_id)]
    await flow.drain()

    assert await facts(flow) == []
    async with flow.cell.uow(space) as uow:
        again = await uow.privacy.get_erasure(space, UUID(done.request_id))
    assert again is not None
    assert again.status is ErasureStatus.COMPLETED
    assert again.counts["reapplied_after_restore"] == "backups/s_x/dump"
    # A second restore from a dump taken after it finds the request completed and leaves it.
    assert await backups.reapply_erasures(space, "backups/s_x/later") == []


async def test_the_ledger_goes_after_it_outlived_every_dump(flow: Flow) -> None:
    space = flow.cell.config.spaces()[0].space_id
    request = ForgetRequest(target="handle", handle=SUBJECT)
    await flow.privacy.forget(space, request, idempotency_key="forget-rafa", requested_by="user:dpo")
    await flow.drain()
    assert await flow.cell.objects.list_keys(ledger_prefix(space))
    flow.cell.clock.advance(days=45)
    await flow.privacy.retention_sweep(_task(space))
    assert await flow.cell.objects.list_keys(ledger_prefix(space)) == []


def _task(space: UUID) -> Task:
    return Task(TaskName.RETENTION_SWEEP, space)
