"""The observed types follow a space whose database was emptied (reset-spaces.sh) or restored from a dump: its
change sequence starts again lower, while the cell keeps the observed document with the old position. The
inference read only objects past that position, so after the reset of 07/10/2026 nothing new was observed
until the new database's sequence caught up (juridico-zero: `pay_court_fees` never observed again)."""

from datetime import timedelta

from niadra.domain.tasks import Task, TaskName
from niadra.testing.cell import BILLING_SOURCE, SPACE_ID, Cell
from tests.unit.flow.test_zero_touch import connected, event


async def test_a_type_sent_after_the_spaces_database_was_emptied_is_still_observed(cell: Cell) -> None:
    flow = await connected(cell)
    await flow.observed_config.observe(Task(TaskName.OBSERVE_CONFIG, SPACE_ID))
    assert cell.config.space(SPACE_ID).object_types.get("legal_case") is not None
    # The space's database goes, and a new one starts its sequence again; the cell keeps the document.
    objects = cell.uow.store.module("objects")
    for table in ("objects", "by_key", "events", "participants"):
        objects[table].clear()
    cell.uow.store.module("cell")["change_seq"]["value"] = 0
    later = (cell.clock.now() + timedelta(minutes=1)).isoformat()
    ref = {"type": "hearing", "namespace": "court", "id": "hearing-1"}
    sent = event("hearing-1", "hearing.scheduled", ref, {"case_id": "case-0", "state": "agendada"}, later)
    assert (await flow.send(BILLING_SOURCE, sent)).errors == []
    await flow.drain()
    await flow.observed_config.observe(Task(TaskName.OBSERVE_CONFIG, SPACE_ID))
    assert cell.config.space(SPACE_ID).object_types.get("hearing") is not None
