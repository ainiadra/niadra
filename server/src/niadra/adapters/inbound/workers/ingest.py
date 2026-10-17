"""Task handlers of the `ingest` module: batches acknowledged by a process that died before storing them,
hot-layer repair and rebuild, dedupe rotation, file imports, media text and the masking pass over stored
events."""

from collections.abc import Awaitable, Callable
from typing import Any

from niadra.domain.tasks import Task, TaskName


def handlers(c: Any) -> dict[TaskName, Callable[[Task], Awaitable[None]]]:
    ingest = c.ingest

    async def dedupe_rotate(task: Task) -> None:
        await ingest.dedupe_rotate(task)

    return {
        TaskName.HOT_REPAIR: ingest.hot_repair,
        TaskName.APPLY_INTAKE: ingest.apply_intake,
        TaskName.HOT_REBUILD: ingest.hot_rebuild,
        TaskName.DEDUPE_ROTATE: dedupe_rotate,
        TaskName.IMPORT_FILE: ingest.import_file,
        TaskName.MEDIA_TEXT: ingest.media_text,
        TaskName.MASK_EVENTS: ingest.mask_events,
    }
