"""Legal retention of the typed state on PostgreSQL (24.5.1): customers' objects past their type's retention
leave with their values, timers and links; superseded values past 90 days go and the latest stays; what a
legal hold holds, by the object or by its owner, stays whole."""

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import text

from niadra.adapters.memory.fakes import FixedClock, StaticConfig
from niadra.adapters.outbound.postgres.uow import PgUnitOfWorkFactory
from niadra.app.objects import ObjectsService
from niadra.config.models import FeatureSettings
from niadra.config.object_types import ObjectTypeRegistry
from niadra.domain.common.ids import uuid7
from niadra.domain.privacy.holds import Held
from niadra.domain.records import BusinessObject, ObjectKey
from niadra.domain.state.shared import SharedKey
from niadra.domain.state.typed import NewValue
from niadra.domain.vocabulary import Feature
from niadra.ports.repos.typed import WatchRow
from niadra.testing.cell import sample_config

NOW = datetime(2026, 9, 29, 15, 0, tzinfo=UTC)
CART = {
    "type": "cart_quote",
    "ownership": "subject",
    "mirror_of": {"system": "store"},
    "fields": {"total": {"type": "money"}},
    "values": {"shipping": {"rule": "frete@v1"}},
    "retention": {"projection": "7d"},
}


def config(space: UUID) -> StaticConfig:
    return StaticConfig(
        sample_config(space).model_copy(
            update={
                "features": FeatureSettings(enabled=frozenset({Feature.STATE})),
                "object_types": ObjectTypeRegistry.model_validate({"types": [CART]}),
            }
        )
    )


def version(n: int, days_ago: float) -> NewValue:
    at = NOW - timedelta(days=days_ago)
    return NewValue(
        name="shipping",
        version=n,
        value=10.0 + n,
        absent_as=None,
        rule="frete@v1",
        inputs_hash=bytes([n]) * 32,
        inputs=(),
        computed_at=at,
        valid_until=None,
        declared_gaps=(),
        gap_effect="unknown",
        supersedes=n - 1 if n > 1 else None,
        recorded_at=at,
    )


async def remaining(pg_uow: Any, space: UUID) -> dict[str, Any]:
    async with pg_uow(space) as uow:
        objects = {
            r.external_id for r in await uow.session.execute(text("SELECT external_id FROM business_objects"))
        }
        values = sorted(
            (str(r.object_id), r.version)
            for r in await uow.session.execute(text("SELECT object_id, version FROM object_values"))
        )
    return {"objects": objects, "values": values}


async def test_the_retention_step_spares_what_a_legal_hold_holds(pg_uow: PgUnitOfWorkFactory) -> None:
    space, held_owner = uuid7(), uuid7()
    ids: dict[str, UUID] = {}
    async with pg_uow(space) as uow:
        for name, days, owner in (
            ("old", 10, uuid7()),
            ("held", 10, uuid7()),
            ("owned", 10, held_owner),
            ("fresh", 1, uuid7()),
        ):
            key = ObjectKey("cart_quote", "store", name)
            obj = BusinessObject(
                uuid7(), key, {}, NOW - timedelta(days=days), uuid7(), origin_handle_id=owner
            )
            ids[name] = (await uow.objects.add_object(obj)).object_id
        await uow.objects.add_object_event(ids["old"], uuid7(), NOW - timedelta(days=10))
        await uow.typed.add_values({ids["old"]: [version(1, 10)]}, None)
        await uow.typed.add_values({ids["fresh"]: [version(1, 100), version(2, 95), version(3, 1)]}, None)
        await uow.typed.add_values({ids["held"]: [version(1, 100), version(2, 1)]}, None)
    objects = ObjectsService(pg_uow, FixedClock(NOW), config(space))
    held = Held(objects={ids["held"]: (uuid7(),)}, handles={held_owner: (uuid7(),)})
    gone = await objects.expire_retained(space, held)
    found = await remaining(pg_uow, space)
    assert found["objects"] == {"held", "owned", "fresh"}
    assert found["values"] == sorted([(str(ids["fresh"]), 3), (str(ids["held"]), 1), (str(ids["held"]), 2)])
    # One object and its orphaned version, and the fresh object's two superseded versions.
    assert gone == 1 + 1 + 2
    assert await objects.expire_retained(space, held) == 0


async def test_a_held_shared_object_stays_in_the_working_set_and_a_watch_is_in_the_package(
    pg_uow: PgUnitOfWorkFactory,
) -> None:
    space, customer = uuid7(), uuid7()
    long_ago = NOW - timedelta(days=60)
    held_id, free_id = uuid7(), uuid7()
    async with pg_uow(space) as uow:
        await uow.typed.enter_working_set(
            [
                (held_id, SharedKey("item", "store", "held"), long_ago, long_ago),
                (free_id, SharedKey("item", "store", "free"), long_ago, long_ago),
            ]
        )
        left = await uow.typed.leave_working_set("item", NOW, limit=10, keep={held_id})
        assert left == [free_id]
        watch = WatchRow(uuid7(), held_id, customer, "price < 100", "consent-1", NOW + timedelta(days=30))
        await uow.typed.watch([watch], NOW)
    async with pg_uow(space) as uow:
        assert await uow.typed.watches_for([customer]) == [watch]
        assert await uow.typed.watches_for([uuid7()]) == []
