"""What the rest of the cell sees of a space database that is not serving (4, 9)."""

import json
from types import SimpleNamespace
from uuid import uuid4

import pytest
from starlette.requests import Request

from niadra.adapters.inbound.http.errors import handle_niadra_error
from niadra.adapters.inbound.workers.runtime import WorkerRuntime
from niadra.adapters.memory.fakes import RecordedDelivery
from niadra.adapters.memory.uow import InMemoryUnitOfWorkFactory
from niadra.app.pipeline import PipelineService
from niadra.domain.common.errors import Unavailable
from niadra.domain.spaces.directory import DbStatus
from niadra.domain.tasks import QueueClass, Task, TaskName
from niadra.testing.cell import SPACE_ID, Cell
from tests.unit.edge.test_service import issue, make_edge


def _request() -> Request:
    app = SimpleNamespace(state=SimpleNamespace(container=None))
    return Request(
        {"type": "http", "method": "POST", "path": "/v1/batch", "headers": [], "state": {}, "app": app}
    )


async def test_a_space_being_created_answers_503_with_retry_after_five() -> None:
    response = await handle_niadra_error(
        _request(), Unavailable("the space's database is being created", retry_after_s=5)
    )
    assert response.status_code == 503
    assert response.headers["Retry-After"] == "5"
    assert json.loads(bytes(response.body))["code"] == "unavailable"


async def test_other_unavailable_errors_keep_the_short_retry() -> None:
    response = await handle_niadra_error(_request(), Unavailable("the broker is down"))
    assert (response.status_code, response.headers["Retry-After"]) == (503, "2")


async def test_a_key_of_a_space_whose_database_is_not_ready_is_asked_to_retry(cell: Cell) -> None:
    edge = make_edge(cell)
    token, _ = await issue(edge)
    cell.databases.statuses[SPACE_ID] = DbStatus.CREATING
    with pytest.raises(Unavailable) as raised:
        await edge.authenticate_key(token)
    assert raised.value.retry_after_s == 5
    cell.databases.statuses[SPACE_ID] = DbStatus.READY
    assert (await edge.authenticate_key(token)).space.space_id == SPACE_ID


@pytest.mark.parametrize("status", [DbStatus.DELETED, DbStatus.DELETING, None])
async def test_a_task_of_a_deleted_or_unknown_space_is_acked_and_dropped(
    cell: Cell, status: DbStatus | None
) -> None:
    space = uuid4()
    ran: list[Task] = []

    async def handler(task: Task) -> None:
        ran.append(task)

    class Directory:
        async def status(self, space_id: object) -> DbStatus | None:
            return status

    worker = WorkerRuntime(
        cell.bus,
        PipelineService(cell.uow, cell.bus, cell.clock),
        {TaskName.EXTRACT: handler},
        queues=[q.value for q in QueueClass],
        databases=Directory(),  # type: ignore[arg-type]
    )
    delivery = RecordedDelivery(Task(TaskName.EXTRACT, space))
    await worker.handle(delivery)
    assert delivery.acked
    assert ran == []


async def test_a_task_waits_when_the_directory_cannot_be_read(cell: Cell) -> None:
    class Down:
        async def status(self, space_id: object) -> DbStatus | None:
            raise ConnectionError("cell database down")

    worker = WorkerRuntime(
        cell.bus,
        PipelineService(cell.uow, cell.bus, cell.clock),
        {},
        queues=[q.value for q in QueueClass],
        databases=Down(),  # type: ignore[arg-type]
    )
    delivery = RecordedDelivery(Task(TaskName.EXTRACT, SPACE_ID))
    await worker.handle(delivery)
    assert not delivery.acked
    assert delivery.retried == ["space directory unavailable"]


async def test_a_repository_of_the_other_database_raises() -> None:
    uow = InMemoryUnitOfWorkFactory()
    async with uow(SPACE_ID) as space:
        with pytest.raises(RuntimeError, match="keys lives in the cell database"):
            space.keys  # noqa: B018
        with pytest.raises(RuntimeError, match="lives in the cell database"):
            space.config  # noqa: B018
    async with uow.cell() as cell:
        with pytest.raises(RuntimeError, match="ingest lives in the space database"):
            cell.ingest  # noqa: B018
        with pytest.raises(RuntimeError, match="a cell transaction has no space"):
            cell.space_id  # noqa: B018
