"""Legal holds (24.5.1; LGPD, art. 16): the kept tier and the retention sweep skip what is held, an erasure
leaves a held conversation's turn records and says so, and the release finishes what waited."""

from collections.abc import Callable
from uuid import UUID

import pytest

from niadra.app.holds import LegalHoldsService
from niadra.app.privacy import PrivacyService, erasure_out
from niadra.config.recording import RecordingSettings
from niadra.contracts.privacy import ForgetRequest
from niadra.contracts.signals import LegalHoldCreate, LegalHoldRelease
from niadra.domain.common.errors import Conflict, NotFound
from niadra.domain.privacy.erasure import ErasureRequest
from niadra.domain.privacy.holds import LegalHoldRecord
from niadra.domain.tasks import Task, TaskName
from niadra.domain.turns.storage import HMAC_PURPOSE
from niadra.testing.cell import SPACE_ID, WHATSAPP_SOURCE, Cell, PatchedUnitOfWorkFactory, principal

from .test_turn_erasure import Conversations, _hash, _record
from .world import World

CALLER = principal(WHATSAPP_SOURCE)


def holds(cell: Cell) -> LegalHoldsService:
    return LegalHoldsService(cell.uow, cell.keys, cell.clock)


async def hold(cell: Cell, conversation: str, key: str = "h1") -> str:
    body = LegalHoldCreate(conversation_id=conversation, reason="processo trabalhista em curso")
    created = await holds(cell).create(CALLER, body, idempotency_key=key, by="user:dpo")
    assert (created.scope, created.status) == ("conversation", "active")
    return created.hold_id


async def erase(privacy: PrivacyService, cell: Cell, conversation: str) -> ErasureRequest:
    request = ForgetRequest(target="conversation", conversation_id=conversation)
    accepted = await privacy.forget(
        SPACE_ID, request, idempotency_key=f"k-{conversation}", requested_by="user:u"
    )
    [task] = [t for t in cell.uow.store.module("pipeline")["outbox"] if t.name is TaskName.ERASE]
    await privacy.erase(task)
    async with cell.uow(SPACE_ID) as uow:
        done = await uow.privacy.get_erasure(SPACE_ID, UUID(accepted.request_id))
    assert done is not None
    return done


def turn_keys(cell: Cell, conversation_hash: str) -> list[str]:
    return sorted(k for k in cell.objects.objects if conversation_hash in k)


async def test_a_hold_is_named_by_its_key_keeps_its_reason_sealed_and_is_released_once(cell: Cell) -> None:
    first = await hold(cell, "c-ana")
    assert await hold(cell, "c-ana") == first  # the same Idempotency-Key is the same hold
    async with cell.uow(SPACE_ID) as uow:
        [stored] = await uow.legal_holds.active()
    assert b"trabalhista" not in stored.reason_ct
    assert stored.conversation_hmac == await cell.keys.hmac(SPACE_ID, HMAC_PURPOSE, b"c-ana")
    released = await holds(cell).release(
        CALLER, first, LegalHoldRelease(reason="acordo homologado"), by="user:dpo"
    )
    assert released.status == "released"
    assert [t.name for t in cell.uow.store.module("pipeline")["outbox"]] == [TaskName.RELEASE_HELD]
    with pytest.raises(Conflict):
        await holds(cell).release(CALLER, first, LegalHoldRelease(reason="de novo"), by="user:dpo")
    with pytest.raises(NotFound):
        await holds(cell).release(CALLER, "not-a-hold", LegalHoldRelease(reason="x"), by="user:dpo")


async def test_an_erasure_leaves_a_held_conversations_turn_records_and_the_release_finishes_them(
    cell: Cell, world: World, make_privacy: Callable[..., PrivacyService]
) -> None:
    privacy = make_privacy()
    handle, _ = world.person("+5511999990001")
    world.event(handle, "c-ana", "sobre o plano familiar")
    await _record(cell)
    ana = await _hash(cell, "c-ana")
    kept = turn_keys(cell, ana)
    hold_id = await hold(cell, "c-ana")

    done = await erase(privacy, cell, "c-ana")

    assert turn_keys(cell, ana) == kept, "the held records stay until the release"
    async with cell.uow(SPACE_ID) as uow:
        assert [k.turn_id for k in await uow.turns.kept(["ana-2"])] == ["ana-2"]
    assert erasure_out(done).retained == {"held_turn_conversations": 1, "legal_holds": 1}
    assert done.counts["held"] == {"conversations": [ana], "holds": [hold_id]}
    assert "turns_kept" not in done.erased

    await holds(cell).release(CALLER, hold_id, LegalHoldRelease(reason="acordo homologado"), by="user:dpo")
    await privacy.release_held(Task(TaskName.RELEASE_HELD, SPACE_ID, {"hold_id": hold_id}))

    assert turn_keys(cell, ana) == []
    async with cell.uow(SPACE_ID) as uow:
        assert await uow.turns.kept(["ana-2"]) == []
        after = await uow.privacy.get_erasure(SPACE_ID, done.request_id)
    assert after is not None
    assert after.erased["turns_kept"] == 1
    assert after.counts["turn_objects"] == 3
    assert "held" not in after.counts
    assert erasure_out(after).retained == {"legal_holds": 1}


async def test_the_retention_sweep_keeps_a_held_kept_turn_until_the_release(
    cell: Cell, world: World, make_privacy: Callable[..., PrivacyService]
) -> None:
    privacy = make_privacy()
    config = cell.config.space(SPACE_ID)
    cell.config.put(config.model_copy(update={"recording": RecordingSettings(kept_days=30)}))
    await _record(cell)
    bruno = await _hash(cell, "c-bruno")
    kept = f"turns/kept/{SPACE_ID}/{bruno}/bruno-1.bin"
    hold_id = await hold(cell, "c-bruno")
    cell.clock.advance(days=31)
    await privacy.retention_sweep(Task(TaskName.RETENTION_SWEEP, SPACE_ID))
    assert kept in cell.objects.objects
    async with cell.uow(SPACE_ID) as uow:
        assert [k.turn_id for k in await uow.turns.kept(["bruno-1"])] == ["bruno-1"]
    await holds(cell).release(CALLER, hold_id, LegalHoldRelease(reason="fim"), by="user:dpo")
    await privacy.retention_sweep(Task(TaskName.RETENTION_SWEEP, SPACE_ID))
    assert kept not in cell.objects.objects


async def test_a_held_handle_holds_the_conversations_it_started(cell: Cell, world: World) -> None:
    handle, _ = world.person("+5511999990001")
    async with cell.uow(SPACE_ID) as uow:
        await uow.legal_holds.add(
            LegalHoldRecord(
                UUID(int=7), "handle", cell.clock.now(), "user:dpo", b"sealed", 1, handle_id=handle
            )
        )
    uow = PatchedUnitOfWorkFactory(cell.uow, ingest=Conversations(world))
    found = await LegalHoldsService(uow, cell.keys, cell.clock).held(SPACE_ID)  # type: ignore[arg-type]
    assert found.handles == {handle: (UUID(int=7),)}
    assert sorted(found.conversations) == sorted(
        [await cell.keys.hmac(SPACE_ID, HMAC_PURPOSE, name) for name in (b"c-ana", b"c-ana-alias")]
    )
