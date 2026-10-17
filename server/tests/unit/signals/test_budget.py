"""The context budget: `include: ["budget"]` shows what the pack costs per section, what this
agent's recorded turns already spent in the conversation and the case, and the cut the measurement found. The
counters come from the applied turns into the state store; a lost store makes the block say it did not count,
never zero; an erasure takes the counters of the target's conversations."""

from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

import pytest

from niadra.app.budget import added_up, counts_of
from niadra.config.models import FeatureSettings, SpaceConfig
from niadra.contracts.common import Handle
from niadra.contracts.context import ContextRequest, ContextResponse
from niadra.contracts.privacy import ForgetRequest
from niadra.contracts.turns import TurnRecord
from niadra.domain.common.errors import NotFound
from niadra.domain.measure.trim import ContextTrim, TrimUnit
from niadra.domain.tasks import TaskName
from niadra.domain.vocabulary import Feature, HandleType, ReceiptSurface, Verification
from niadra.testing.cell import SPACE_ID, VOICE_SOURCE, WHATSAPP_SOURCE, Cell, principal, sample_config
from tests.unit.flow.harness import Flow

PHONE = "+5511955551212"
SUBJECT = Handle(type=HandleType.PHONE_E164, value=PHONE)


@dataclass(frozen=True)
class Applied:
    source_id: UUID
    received_at: datetime
    record: TurnRecord


def configured(*features: Feature) -> SpaceConfig:
    return sample_config().model_copy(update={"features": FeatureSettings(enabled=frozenset(features))})


def turn(
    turn_id: str, source: UUID, at: datetime, *, conversation: str | None = "wa-9", task: str | None = None
) -> Applied:
    record = TurnRecord.model_validate(
        {
            "turn_id": turn_id,
            "conversation_id": conversation,
            "task_id": task,
            "agent": {"name": "closer"},
            "started_at": at.isoformat(),
            "content_mode": "hash_only",
            "calls": [
                {"call_id": "m1", "kind": "model", "tokens": {"in": 1000, "cached": 800, "out": 50}},
                {"call_id": "t1", "kind": "tool", "name": "search_items"},
                {"call_id": "m2", "kind": "model", "tokens": {"in": 1100, "cached": 1000, "out": 40}},
            ],
            "cost": {"usd": 0.0042},
        }
    )
    return Applied(source, at, record)


@pytest.fixture
async def flow(cell: Cell) -> Flow:
    cell.config.put(configured(Feature.TURNS, Feature.SIGNALS))
    flow = Flow.build(cell)
    item = {
        "type": "event",
        "idempotency_key": "wa-9-hello",
        "channel": "whatsapp",
        "conversation_id": "wa-9",
        "handles": [{"type": "phone_e164", "value": PHONE}],
        "speaker": {"role": "customer"},
        "direction": "inbound",
        "content": {"type": "text", "text": "Quero trocar meu plano"},
        "occurred_at": cell.clock.now().isoformat(),
    }
    assert (await flow.send(WHATSAPP_SOURCE, item)).errors == []
    await flow.drain()
    return flow


async def apply(flow: Flow, *turns: Applied) -> None:
    async with flow.cell.uow(SPACE_ID) as uow:
        await flow.budget.consume(uow, SPACE_ID, list(turns))


async def read(flow: Flow, source: UUID = WHATSAPP_SOURCE, **extra: Any) -> ContextResponse:
    request = ContextRequest(
        subject=SUBJECT,
        verification=Verification.V0,
        include=["budget"],
        **({"conversation_id": "wa-9"} | extra),
    )
    return await flow.serve.context(principal(source, flow.cell.config.space(SPACE_ID)), request)


def test_a_turn_counts_its_model_and_tool_calls_tokens_and_cost() -> None:
    at = datetime(2026, 9, 29)
    value = counts_of(turn("t1", WHATSAPP_SOURCE, at))  # type: ignore[arg-type]
    assert value == "2,1,2100,1800,90,4200"
    use, others = added_up(
        {f"{WHATSAPP_SOURCE}:t1": value, f"{WHATSAPP_SOURCE}:t2": value, f"{VOICE_SOURCE}:t3": value},
        WHATSAPP_SOURCE,
    )
    assert (use.turns, use.model_calls, use.tool_calls, use.tokens_in, use.cost_usd) == (
        2,
        4,
        2,
        4200,
        0.0084,
    )
    assert others == 1


async def test_the_block_shows_the_pack_this_agents_spend_and_the_other_agents_turns(flow: Flow) -> None:
    now = flow.cell.clock.now()
    await apply(flow, turn("t1", WHATSAPP_SOURCE, now), turn("t2", WHATSAPP_SOURCE, now))
    # A group applied again counts its turns once.
    await apply(flow, turn("t3", VOICE_SOURCE, now), turn("t1", WHATSAPP_SOURCE, now))
    response = await read(flow)
    budget = response.budget
    assert budget is not None
    assert budget.counted
    assert budget.pack is not None
    assert budget.pack.total > 0
    assert sum(budget.pack.sections.values()) <= budget.pack.total
    spent = budget.conversation
    assert spent is not None
    assert (spent.turns, spent.model_calls, spent.tool_calls, spent.tokens_cached) == (2, 4, 2, 3600)
    assert spent.cost_usd == pytest.approx(0.0084)
    assert budget.other_agents_turns == 1
    assert budget.case is None
    assert budget.cut is None
    receipts = [r for r in flow.receipts.sent if r.surface is ReceiptSurface.BUDGET]
    assert receipts[-1].detail == {"counted": "1", "pack_tokens": str(budget.pack.total), "turns": "2"}


async def test_a_case_counts_apart_from_its_conversation(flow: Flow) -> None:
    now = flow.cell.clock.now()
    await apply(
        flow,
        turn("t1", WHATSAPP_SOURCE, now, task="claim-77"),
        turn("t2", WHATSAPP_SOURCE, now, conversation=None, task="claim-77"),
    )
    # A read names a conversation or a case; each counts its own turns.
    by_case = (await read(flow, conversation_id=None, task_id="claim-77")).budget
    assert by_case is not None
    assert by_case.case is not None
    assert (by_case.conversation, by_case.case.turns) == (None, 2)
    by_conversation = (await read(flow)).budget
    assert by_conversation is not None
    assert by_conversation.conversation is not None
    assert (by_conversation.conversation.turns, by_conversation.case) == (1, None)


async def test_the_cut_the_measurement_found_is_shown_and_whether_the_pack_applies_it(flow: Flow) -> None:
    await read(flow)  # the first read loads the space's settings into the read process
    space = flow.serve._space[SPACE_ID]
    trim = ContextTrim(WHATSAPP_SOURCE, "chat", (TrimUnit("episode:billing", 120),), space.at, 28, 50)
    flow.serve._space[SPACE_ID] = type(space)(space.at, [trim], space.weights, space.views, space.guarded)
    budget = (await read(flow)).budget
    assert budget is not None
    assert budget.cut is not None
    assert (budget.cut.units, budget.cut.applied, budget.cut.window_days) == (["episode:billing"], True, 28)


async def test_a_lost_state_store_leaves_the_turns_applied_and_the_block_uncounted(flow: Flow) -> None:
    flow.turn_counters.down = True
    await apply(flow, turn("t1", WHATSAPP_SOURCE, flow.cell.clock.now()))
    budget = (await read(flow)).budget
    assert budget is not None
    assert (budget.counted, budget.conversation) == (False, None)
    assert budget.pack is not None
    assert not (await read(flow)).degraded  # the block was read: it says what it could not count


async def test_the_block_needs_turns_on(flow: Flow) -> None:
    flow.cell.config.put(configured(Feature.SIGNALS))
    with pytest.raises(NotFound):
        await read(flow)


async def test_an_erasure_takes_the_counters_of_the_targets_conversations(flow: Flow) -> None:
    caller = principal(WHATSAPP_SOURCE, flow.cell.config.space(SPACE_ID))
    record = turn("t1", WHATSAPP_SOURCE, flow.cell.clock.now()).record
    response = await flow.turns.record(
        caller, [record.model_dump(mode="json", by_alias=True, exclude_none=True)]
    )
    assert response.errors == []
    await flow.drain()
    assert flow.turn_counters.rows  # the applied turn was counted
    await flow.privacy.forget(
        SPACE_ID, ForgetRequest(target="handle", handle=SUBJECT), requested_by="dpo", idempotency_key="f-1"
    )
    ran = await flow.drain()
    assert TaskName.PURGE_SIGNALS in [t.name for t in ran]
    assert flow.turn_counters.rows == {}
