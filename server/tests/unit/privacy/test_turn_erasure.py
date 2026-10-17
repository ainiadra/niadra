"""Erasure reaches turn records (24.5): a person's conversations are found by the keyed hash the records are
kept under, before the conversations' rows go, and both tiers go by prefix with the kept tier's rows."""

from collections.abc import Callable
from datetime import date
from typing import Any
from uuid import UUID

from niadra.adapters.memory.turns import TransactionTurnIntakeLog
from niadra.app.privacy import PrivacyService
from niadra.app.replay import ReplayService
from niadra.app.turns import TurnService
from niadra.config.recording import RecordingSettings
from niadra.contracts.common import Handle
from niadra.contracts.privacy import ForgetRequest
from niadra.contracts.turns import ScenarioCreate
from niadra.domain.privacy.erasure import ErasureScope
from niadra.domain.tasks import Task, TaskName
from niadra.domain.turns.storage import HMAC_PURPOSE
from niadra.domain.vocabulary import HandleType
from niadra.testing.cell import SPACE_ID, WHATSAPP_SOURCE, Cell, principal
from tests.unit.audit.fakes import RecordingAdminAudit
from tests.unit.turns.records import turn

from .world import FakeIngest, World


class Conversations(FakeIngest):
    """Ana's conversation, as the conversations' rows name it until the events step erases them."""

    async def conversation_names(self, scope: ErasureScope) -> list[str]:
        return ["c-ana", "c-ana-alias"] if scope.handle_ids else []


async def _record(cell: Cell) -> None:
    turns = TurnService(
        cell.uow,
        TransactionTurnIntakeLog(cell.uow),
        cell.keys,
        cell.objects,
        cell.clock,
        cell.config,
        RecordingAdminAudit(),  # type: ignore[arg-type]
    )
    caller = principal(WHATSAPP_SOURCE, cell.config.space(SPACE_ID))
    await turns.record(
        caller,
        [
            turn("ana-1", conversation_id="c-ana"),
            turn("ana-2", conversation_id="c-ana", flags=["handoff"]),
            turn("ana-3", conversation_id="c-ana-alias", mode="hash_only"),
            turn("bruno-1", conversation_id="c-bruno", flags=["handoff"]),
        ],
    )


async def _erase(privacy: PrivacyService, cell: Cell, request: ForgetRequest) -> dict[str, Any]:
    accepted = await privacy.forget(SPACE_ID, request, idempotency_key="k-turns", requested_by="user:u")
    [task] = [t for t in cell.uow.store.module("pipeline")["outbox"] if t.name.value == "erase"]
    await privacy.erase(task)
    async with cell.uow(SPACE_ID) as uow:
        done = await uow.privacy.get_erasure(SPACE_ID, UUID(accepted.request_id))
    assert done is not None
    return done.counts


async def _hash(cell: Cell, conversation: str) -> str:
    return (await cell.keys.hmac(SPACE_ID, HMAC_PURPOSE, conversation.encode())).hex()


async def test_erasing_a_conversation_removes_its_turn_records_by_prefix(
    cell: Cell, world: World, make_privacy: Callable[..., PrivacyService]
) -> None:
    privacy = make_privacy()
    handle, _ = world.person("+5511999990001")
    world.event(handle, "c-ana", "sobre o plano familiar")
    await _record(cell)
    ana, bruno = await _hash(cell, "c-ana"), await _hash(cell, "c-bruno")
    assert any(ana in key for key in cell.objects.objects)

    counts = await _erase(privacy, cell, ForgetRequest(target="conversation", conversation_id="c-ana"))

    keys = sorted(cell.objects.objects)
    assert not any(ana in key for key in keys)
    assert not any(key.endswith(("/ana-1", "/ana-2")) for key in keys), "the by-id entries go too"
    assert any(bruno in key for key in keys)
    assert any(key.endswith("/ana-3") for key in keys), "another conversation of the same person stays"
    async with cell.uow(SPACE_ID) as uow:
        assert [k.turn_id for k in await uow.turns.kept(["ana-2", "bruno-1"])] == ["bruno-1"]
    assert counts["tables"]["turns_kept"] == 1
    assert counts["turn_objects"] == 3
    assert counts["scope"]["conversation_hashes"] == [ana]


async def test_erasing_a_person_reaches_every_conversation_the_conversations_rows_name(
    cell: Cell, world: World, make_privacy: Callable[..., PrivacyService]
) -> None:
    handle, _ = world.person("+5511999990001")
    world.event(handle, "c-ana", "sobre o plano familiar")
    privacy = make_privacy(ingest=Conversations(world))
    await _record(cell)
    counts = await _erase(
        privacy,
        cell,
        ForgetRequest(target="handle", handle=Handle(type=HandleType.PHONE_E164, value="+5511999990001")),
    )
    remaining = sorted(k for k in cell.objects.objects if k.startswith("turns/"))
    assert all("ana-" not in key for key in remaining)
    assert any(key.endswith("bruno-1.bin") for key in remaining)
    assert counts["turn_objects"] == 4, "three records in the short tier, one of them kept too"
    assert len(counts["scope"]["conversation_hashes"]) == 2


async def test_with_no_retention_set_every_recording_is_kept_a_year_later(
    cell: Cell, world: World, make_privacy: Callable[..., PrivacyService]
) -> None:
    privacy = make_privacy()
    await _record(cell)
    recorded = sorted(k for k in cell.objects.objects if k.startswith("turns/"))
    assert any(k.startswith(f"turns/kept/{SPACE_ID}/") for k in recorded)

    cell.clock.advance(days=400)
    await privacy.retention_sweep(Task(TaskName.RETENTION_SWEEP, SPACE_ID))

    assert sorted(k for k in cell.objects.objects if k.startswith("turns/")) == recorded
    async with cell.uow(SPACE_ID) as uow:
        [row] = await uow.turns.kept(["bruno-1"])
    assert row.kept_until is None


async def test_the_retention_sweep_lets_a_kept_turn_go_after_its_days_and_the_totals_stay(
    cell: Cell, world: World, make_privacy: Callable[..., PrivacyService]
) -> None:
    privacy = make_privacy()
    config = cell.config.space(SPACE_ID)
    cell.config.put(config.model_copy(update={"recording": RecordingSettings(kept_days=30)}))
    await _record(cell)
    short = sorted(k for k in cell.objects.objects if k.startswith(f"turns/short/{SPACE_ID}/"))
    assert short
    bruno = await _hash(cell, "c-bruno")
    kept = f"turns/kept/{SPACE_ID}/{bruno}/bruno-1.bin"
    assert kept in cell.objects.objects

    cell.clock.advance(days=29)
    await privacy.retention_sweep(Task(TaskName.RETENTION_SWEEP, SPACE_ID))
    assert kept in cell.objects.objects
    cell.clock.advance(days=2)
    await privacy.retention_sweep(Task(TaskName.RETENTION_SWEEP, SPACE_ID))

    assert kept not in cell.objects.objects
    short_left = [
        k for k in cell.objects.objects if k.startswith(f"turns/short/{SPACE_ID}/") and k.endswith(".bin")
    ]
    assert short_left == []
    async with cell.uow(SPACE_ID) as uow:
        assert await uow.turns.kept(["bruno-1", "ana-2"]) == []
        totals = await uow.turns.daily(date(2026, 6, 1), date(2026, 12, 31), None)
    assert sum(row.turns for row in totals) == 4


async def test_a_scenarios_turns_go_with_their_row_and_by_prefix_on_erasure(
    cell: Cell, world: World, make_privacy: Callable[..., PrivacyService]
) -> None:
    """A scenario's copy goes with its row once the space sets `scenario_days`, and by the conversation's
    prefix on erasure."""
    privacy = make_privacy()
    config = cell.config.space(SPACE_ID)
    cell.config.put(config.model_copy(update={"recording": RecordingSettings(scenario_days=180)}))
    handle, _ = world.person("+5511999990001")
    world.event(handle, "c-ana", "sobre o plano familiar")
    await _record(cell)
    turns = TurnService(
        cell.uow,
        TransactionTurnIntakeLog(cell.uow),
        cell.keys,
        cell.objects,
        cell.clock,
        cell.config,
        RecordingAdminAudit(),  # type: ignore[arg-type]
    )
    replay = ReplayService(cell.uow, turns, cell.objects, cell.clock, cell.config, RecordingAdminAudit())  # type: ignore[arg-type]
    caller = principal(WHATSAPP_SOURCE, cell.config.space(SPACE_ID))
    await replay.create(caller, ScenarioCreate(name="bruno", turn_ids=["bruno-1"]), "k-bruno")
    await replay.create(caller, ScenarioCreate(name="ana", turn_ids=["ana-1"]), "k-ana")
    bruno, ana = await _hash(cell, "c-bruno"), await _hash(cell, "c-ana")
    copy = f"turns/scenario/{SPACE_ID}/{bruno}/bruno-1.bin"
    assert copy in cell.objects.objects

    await _erase(privacy, cell, ForgetRequest(target="conversation", conversation_id="c-ana"))
    assert not any(key.startswith(f"turns/scenario/{SPACE_ID}/{ana}/") for key in cell.objects.objects)

    cell.clock.advance(days=181)
    await privacy.retention_sweep(Task(TaskName.RETENTION_SWEEP, SPACE_ID))
    assert copy not in cell.objects.objects
    async with cell.uow(SPACE_ID) as uow:
        assert await uow.turns.kept(["bruno-1"]) == []
