"""`PgTriggerRepository` against a real PostgreSQL 17."""

from datetime import UTC, datetime, timedelta
from uuid import UUID

from niadra.adapters.outbound.postgres.uow import PgUnitOfWorkFactory
from niadra.domain.common.ids import uuid7
from niadra.domain.traits.evidence import EvidenceRef
from niadra.domain.triggers.firing import Firing

NOW = datetime(2026, 9, 22, 18, tzinfo=UTC)


def _firing(profile: UUID, window: str, minutes: float = 0, rule: str = "promise_overdue") -> Firing:
    evidence = (EvidenceRef("open_item", uuid7(), uuid7()),)
    return Firing(
        uuid7(), rule, "v1", profile, window, b"\x01" * 32, evidence, 3, NOW + timedelta(minutes=minutes)
    )


async def test_one_firing_per_rule_subject_and_window(pg_uow: PgUnitOfWorkFactory) -> None:
    space, profile = uuid7(), uuid7()
    first = _firing(profile, "24h:1")
    async with pg_uow(space) as uow:
        assert await uow.triggers.add_firing(first)
        assert not await uow.triggers.add_firing(_firing(profile, "24h:1"))
        assert await uow.triggers.add_firing(_firing(profile, "24h:2", minutes=5))
        assert await uow.triggers.add_firing(_firing(profile, "24h:1", rule="other"))
    async with pg_uow(space) as uow:
        assert await uow.triggers.count_since(NOW) == 3
        assert await uow.triggers.count_since(NOW + timedelta(minutes=1)) == 1
        [stored, *_] = await uow.triggers.list_firings(rule_id="promise_overdue", profile_id=profile)
        assert stored.window_key == "24h:2"
        await uow.triggers.retract(first.firing_id, NOW)
    async with pg_uow(space) as uow:
        active = await uow.triggers.firings_for(profile, since=NOW - timedelta(days=1))
        assert first.firing_id not in {f.firing_id for f in active}
        assert len(active) == 2
        [retracted] = [f for f in await uow.triggers.list_firings() if f.firing_id == first.firing_id]
        assert retracted.retracted_at == NOW
        assert retracted.evidence == first.evidence
        assert retracted.idempotency_key == first.idempotency_key
        assert retracted.membership_version == 3
    async with pg_uow(uuid7()) as uow:
        assert await uow.triggers.count_since(NOW - timedelta(days=1)) == 0


async def test_keyset_pagination(pg_uow: PgUnitOfWorkFactory) -> None:
    space = uuid7()
    async with pg_uow(space) as uow:
        for i in range(5):
            await uow.triggers.add_firing(_firing(uuid7(), f"w{i}", minutes=i))
        page = await uow.triggers.list_firings(limit=2)
        rest = await uow.triggers.list_firings(before=(page[-1].fired_at, page[-1].firing_id), limit=10)
    assert [f.window_key for f in page] == ["w4", "w3"]
    assert [f.window_key for f in rest] == ["w2", "w1", "w0"]
