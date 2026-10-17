"""Legal holds on turn records (24.5.1): a held conversation's objects get a copy no bucket rule expires,
new turns of it are copied while the hold lasts, reads fall back to the copy, an erasure leaves it, and the
release removes it."""

from dataclasses import dataclass, field
from uuid import UUID

from niadra.adapters.memory.turns import TransactionTurnIntakeLog
from niadra.app.turns import TurnService
from niadra.config.models import FeatureSettings
from niadra.contracts.turns import PromoteRequest
from niadra.domain.spaces.deletion import space_object_prefixes
from niadra.domain.turns.storage import (
    HMAC_PURPOSE,
    Tier,
    conversation_prefix,
    conversation_prefixes,
    turn_key,
)
from niadra.domain.vocabulary import Feature
from niadra.entrypoints.setup import OBJECT_EXPIRY_DAYS
from niadra.testing.cell import SPACE_ID
from tests.unit.turns.conftest import World
from tests.unit.turns.records import turn

T1, T2, T3 = (f"0192f5a0-7c3e-7a10-9d2b-5e61a4c0d00{i}" for i in (1, 2, 3))


@dataclass
class Holds:
    held: set[bytes] = field(default_factory=set)

    async def held_conversations(self, space_id: UUID) -> frozenset[bytes]:
        assert space_id == SPACE_ID
        return frozenset(self.held)


def _service(world: World, holds: Holds) -> TurnService:
    config = world.cell.config.space(SPACE_ID)
    features = FeatureSettings(enabled=frozenset({Feature.TURNS, Feature.LEGAL_HOLDS}))
    world.cell.config.put(config.model_copy(update={"features": features}))
    return TurnService(
        world.cell.uow,
        TransactionTurnIntakeLog(world.cell.uow),
        world.cell.keys,
        world.cell.objects,
        world.cell.clock,
        world.cell.config,
        world.audit,  # type: ignore[arg-type]
        holds=holds,
    )


def _expire_by_bucket_rules(world: World) -> None:
    """What the bucket's rules and a retention the customer set would delete once every one came due: the
    short and kept tiers, which no bucket rule expires on its own."""
    expiring = (*OBJECT_EXPIRY_DAYS, "turns/short/", "turns/kept/")
    for key in list(world.cell.objects.objects):
        if any(key.startswith(prefix) for prefix in expiring):
            del world.cell.objects.objects[key]


async def test_a_held_conversations_objects_outlive_the_lifecycle_rule_and_go_on_release(
    world: World,
) -> None:
    holds = Holds()
    service = _service(world, holds)
    ana = await world.cell.keys.hmac(SPACE_ID, HMAC_PURPOSE, b"c-8812")
    await service.record(world.caller, [turn(T1), turn(T2, conversation_id="c-other")])
    await service.promote(world.caller, PromoteRequest(turn_ids=[T1], reason="complaint"))
    assert await service.keep_held(SPACE_ID) == 0, "nothing is held"

    holds.held.add(ana)
    copied = await service.keep_held(SPACE_ID)
    await service.record(world.caller, [turn(T3)])
    later = await service.keep_held(SPACE_ID)

    held = conversation_prefix(Tier.HELD, SPACE_ID, ana)
    assert (copied, later) == (1, 1), "one copy per turn, whichever tiers hold it; new turns follow"
    assert sorted(k for k in world.turn_objects() if k.startswith(held)) == [
        held + f"{T1}.bin",
        held + f"{T3}.bin",
    ]
    assert await service.keep_held(SPACE_ID) == 0, "a pass copies nothing twice"

    _expire_by_bucket_rules(world)
    assert sorted(k for k in world.turn_objects() if k.startswith("turns/")) == [
        held + f"{T1}.bin",
        held + f"{T3}.bin",
    ]
    view = await service.read(world.caller, T1)
    assert view.record.turn_id == T1, "the kept row finds the held copy"

    holds.held.clear()
    assert await service.keep_held(SPACE_ID) == 2
    assert world.turn_objects() == []


def test_no_bucket_rule_and_no_erasure_prefix_reaches_the_held_copies_and_space_deletion_does() -> None:
    held = turn_key(Tier.HELD, SPACE_ID, b"\xaa" * 32, "t1")
    scenario = turn_key(Tier.SCENARIO, SPACE_ID, b"\xaa" * 32, "t1")
    for key in (held, scenario):
        assert not any(key.startswith(prefix) for prefix in OBJECT_EXPIRY_DAYS), key
        assert any(key.startswith(prefix) for prefix in space_object_prefixes(SPACE_ID)), key
    erased = conversation_prefixes(SPACE_ID, b"\xaa" * 32)
    assert any(scenario.startswith(prefix) for prefix in erased)
    assert not any(held.startswith(prefix) for prefix in erased), "the hold's copy waits for the release"


async def test_a_space_without_legal_holds_keeps_no_copy(world: World) -> None:
    holds = Holds({await world.cell.keys.hmac(SPACE_ID, HMAC_PURPOSE, b"c-8812")})
    service = TurnService(
        world.cell.uow,
        TransactionTurnIntakeLog(world.cell.uow),
        world.cell.keys,
        world.cell.objects,
        world.cell.clock,
        world.cell.config,
        world.audit,  # type: ignore[arg-type]
        holds=holds,
    )
    await service.record(world.caller, [turn(T1)])
    assert await service.keep_held(SPACE_ID) == 0
    assert not any("/held/" in k for k in world.turn_objects())
