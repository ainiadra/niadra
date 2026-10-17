"""Refresh requests on PostgreSQL (24.3.5): kept once per object and reason, leased, settled by id when the
resolver worker answers or releases one, and given up with their rows after their leases."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from niadra.domain.common.ids import uuid7
from niadra.domain.state.shared import SharedKey
from niadra.ports.repos.typed import RefreshRow
from tests.integration.conftest import CloningUnitOfWork

NOW = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)


def row(reason: str, external_id: str) -> RefreshRow:
    key = SharedKey("item_variant", "loja", external_id)
    return RefreshRow(
        uuid7(), uuid4(), key, reason, "high", 1, NOW, {"admitted": True, "operation": "x", "units": 1}
    )


async def test_requests_are_settled_by_id_and_given_up_with_their_rows(pg_uow: CloningUnitOfWork) -> None:
    space = uuid4()
    answered, released, forgotten = (
        row("watch_revalidation", "v-1"),
        row("exposed_now", "v-2"),
        row("floor", "v-3"),
    )
    async with pg_uow(space) as uow:
        await uow.typed.request_refreshes([answered, released, forgotten])
    async with pg_uow(space) as uow:
        assert len(await uow.typed.lease_refreshes("worker", NOW, NOW + timedelta(seconds=60), limit=10)) == 3
    async with pg_uow(space) as uow:
        settled = await uow.typed.settle_refreshes([answered.request_id, released.request_id, uuid7()])
    assert {(r.request_id, r.reason, r.key.external_id) for r in settled} == {
        (answered.request_id, "watch_revalidation", "v-1"),
        (released.request_id, "exposed_now", "v-2"),
    }
    assert all(r.reservation == {"admitted": True, "operation": "x", "units": 1} for r in settled)
    async with pg_uow(space) as uow:
        assert await uow.typed.settle_refreshes([answered.request_id]) == []
        # The one left, leased once, is given up only after its third lease ran out.
        assert await uow.typed.give_up_refreshes(NOW + timedelta(seconds=61), 3, limit=10) == []
    for turn in (2, 3):
        at = NOW + timedelta(seconds=61 * (turn - 1))
        async with pg_uow(space) as uow:
            assert (
                len(await uow.typed.lease_refreshes("worker", at, at + timedelta(seconds=60), limit=10)) == 1
            )
    async with pg_uow(space) as uow:
        [given] = await uow.typed.give_up_refreshes(NOW + timedelta(seconds=300), 3, limit=10)
    assert (given.request_id, given.object_id, given.reason) == (
        forgotten.request_id,
        forgotten.object_id,
        "floor",
    )
