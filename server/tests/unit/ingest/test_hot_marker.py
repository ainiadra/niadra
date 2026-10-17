"""The state store keeps nothing across a restart: the marker notices, and the loops rebuild the hot layer."""

from contextlib import AbstractAsyncContextManager
from typing import Any
from uuid import UUID

import pytest

from niadra.app.ingest import IngestService
from niadra.app.sessions import SessionService
from niadra.domain.common.errors import Unavailable
from niadra.domain.sessions.rules import HOT_LAYER
from niadra.ports.infra import HotLayerState, HotTurn
from niadra.testing.cell import SPACE_ID, Cell, principal, sample_config
from tests.unit.ingest.fakes import FakeIdentity, build, handle_id, message
from tests.unit.ingest.test_service import PHONE, send

OTHER_SPACE = UUID("0192f5a0-0000-7000-8000-00000000a0ff")


async def turns(cell: Cell) -> list[HotTurn]:
    return (await cell.hot.read(SPACE_ID, [handle_id(PHONE)], 0)).turns


async def test_a_sealed_store_is_left_alone(cell: Cell) -> None:
    ingest, _, _ = build(cell)
    assert not await ingest.watch_hot_layer()
    assert await cell.hot.state() is HotLayerState.WHOLE


async def test_a_wiped_store_is_rebuilt_from_the_event_log_and_sealed(cell: Cell) -> None:
    ingest, sessions, _ = build(cell)
    await send(ingest, principal(), message("m1"))
    await send(ingest, principal(), message("m2"))
    cell.hot.wipe()
    assert not (await cell.hot.read(SPACE_ID, [handle_id(PHONE)], 0)).whole

    assert await ingest.watch_hot_layer()

    read = await cell.hot.read(SPACE_ID, [handle_id(PHONE)], 0)
    assert (len(read.turns), read.whole) == (2, True)
    assert not await sessions.is_degraded(HOT_LAYER)
    assert not await ingest.watch_hot_layer()  # sealed: nothing left to do


async def test_a_store_that_restarts_again_mid_rebuild_is_not_sealed(
    cell: Cell, monkeypatch: pytest.MonkeyPatch
) -> None:
    ingest, sessions, _ = build(cell)
    for n in range(3):
        await send(ingest, principal(), message(f"m{n}"))
    cell.hot.wipe()
    monkeypatch.setattr("niadra.app.ingest.REBUILD_PAGE", 1)
    add = cell.hot.add
    pages = 0

    async def restart_after_the_first_page(*args: Any, **kwargs: Any) -> None:
        nonlocal pages
        pages += 1
        if pages == 2:
            cell.hot.wipe()
        await add(*args, **kwargs)

    monkeypatch.setattr(cell.hot, "add", restart_after_the_first_page)

    assert not await ingest.watch_hot_layer()
    assert await cell.hot.state() is HotLayerState.LOST
    assert not (await cell.hot.read(SPACE_ID, [handle_id(PHONE)], 0)).whole
    assert await sessions.is_degraded(HOT_LAYER)

    monkeypatch.setattr(cell.hot, "add", add)
    assert await ingest.watch_hot_layer()  # the next check starts over and finishes
    assert len(await turns(cell)) == 3


async def test_a_rebuild_already_running_elsewhere_is_not_started_twice(cell: Cell) -> None:
    ingest, _, _ = build(cell)
    cell.hot.wipe()
    assert await cell.hot.claim_rebuild("another", 60_000)
    assert not await ingest.watch_hot_layer()
    assert await cell.hot.state() is HotLayerState.REBUILDING


async def test_with_the_store_down_the_check_waits_quietly(cell: Cell) -> None:
    ingest, _, _ = build(cell)
    cell.hot.fail = True
    assert not await ingest.watch_hot_layer()


class OneSpaceRestoring:
    """The unit of work of a cell where one space's database is not serving."""

    def __init__(self, base: Any, restoring: UUID) -> None:
        self._base = base
        self._restoring = restoring

    def __call__(self, space_id: UUID) -> AbstractAsyncContextManager[Any]:
        if space_id == self._restoring:
            raise Unavailable("the space's database is being restored", retry_after_s=5)
        return self._base(space_id)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._base, name)


async def test_a_space_that_is_not_serving_does_not_hold_the_seal(cell: Cell) -> None:
    # Being created (no turns yet), restored (the restore rebuilds it) or deleted.
    cell.config.put(sample_config(OTHER_SPACE))
    uow = OneSpaceRestoring(cell.uow, OTHER_SPACE)
    sessions = SessionService(uow, cell.clock, cell.config)  # type: ignore[arg-type]
    ingest = IngestService(
        uow,  # type: ignore[arg-type]
        FakeIdentity(),
        sessions,
        cell.keys,
        cell.hot,
        cell.clock,
        cell.config,
        cell.objects,
        cell.secrets,
    )
    await send(ingest, principal(), message("m1"))
    cell.hot.wipe()
    assert await ingest.watch_hot_layer()
    assert len(await turns(cell)) == 1
