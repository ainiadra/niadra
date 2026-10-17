"""The per-space loop steps run as the application role (06, 6.3). A space database has two server
connections behind PgBouncer; a system-role pass waited for one that the application's work never left idle,
and PgBouncer ended it after 10 s (`query_wait_timeout`, bench cell 28/09/2026). Only the work that changes a
stored event, truncates or creates partitions keeps the system role."""

import pytest

from niadra.adapters.memory.uow import InMemoryUnitOfWorkFactory
from niadra.app.purposes import PurposeService
from niadra.testing.cell import Cell
from tests.unit.flow.harness import Flow


def refuse_system(monkeypatch: pytest.MonkeyPatch) -> None:
    def system(self: InMemoryUnitOfWorkFactory, space_id: object) -> None:
        raise AssertionError("a loop step opened a system-role transaction")

    monkeypatch.setattr(InMemoryUnitOfWorkFactory, "system", system)


async def test_every_space_loop_step_runs_as_the_application_role(
    cell: Cell, monkeypatch: pytest.MonkeyPatch
) -> None:
    flow = Flow.build(cell)
    space = cell.config.spaces()[0].space_id
    # After 03:00 in the space's time zone, so the nightly steps read their day.
    cell.clock.advance(hours=30)
    refuse_system(monkeypatch)
    purposes = PurposeService(cell.uow, cell.clock, cell.config)
    for step in (
        flow.sessions.sweep_idle,
        flow.traits.watch_rules,
        flow.extract.watch_extractor,
        flow.extract.watch_withholding,
        flow.ingest.watch_masking,
        flow.memory.nightly,
        purposes.step,
    ):
        await step(space)
