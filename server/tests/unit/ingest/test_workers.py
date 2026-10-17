from types import SimpleNamespace

from niadra.adapters.inbound.workers.ingest import handlers as ingest_handlers
from niadra.adapters.inbound.workers.sessions import handlers as session_handlers
from niadra.domain.tasks import Task, TaskName
from niadra.testing.cell import SPACE_ID, Cell
from tests.unit.ingest.fakes import build


async def test_worker_handlers_cover_the_modules_tasks(cell: Cell) -> None:
    ingest, sessions, _ = build(cell)
    container = SimpleNamespace(ingest=ingest, sessions=sessions)

    handlers = {**ingest_handlers(container), **session_handlers(container)}

    assert set(handlers) == {
        TaskName.HOT_REPAIR,
        TaskName.APPLY_INTAKE,
        TaskName.HOT_REBUILD,
        TaskName.DEDUPE_ROTATE,
        TaskName.IMPORT_FILE,
        TaskName.MEDIA_TEXT,
        TaskName.MASK_EVENTS,
        TaskName.CLOSE_SESSION,
    }
    await handlers[TaskName.DEDUPE_ROTATE](Task(TaskName.DEDUPE_ROTATE, SPACE_ID))
    assert cell.uow.store.data["ingest"]["generation"]["value"] == 1
