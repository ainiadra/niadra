"""The daily LLM budget under parallel calls, on a real space database (5.9).

On 25/09 a sandbox spent 1,126,925 micros against a cap of 500,000: each call checked the spend, and
only added its cost after the answer, so every call in flight passed the same check.
"""

import asyncio
import json
from typing import Any

import httpx
import pytest
from sqlalchemy import text

from niadra.adapters.memory.fakes import FixedClock
from niadra.adapters.outbound.llm.chat import ChatCompletionsLlm, ChatSettings
from niadra.app.extract import LlmBudget
from niadra.domain.common.ids import uuid7
from niadra.domain.extract.errors import BudgetExceeded
from tests.integration.conftest import CloningUnitOfWork

CAP = 10_000
COST = 1_000  # each answer's real cost, in micros
CALLS = 40


async def test_parallel_calls_cannot_spend_past_the_daily_cap(pg_uow: CloningUnitOfWork) -> None:
    space = uuid7()
    clock = FixedClock()
    budget = LlmBudget(pg_uow, clock, {"extraction": CAP})
    in_flight = 0
    answer = asyncio.Event()

    async def provider(request: httpx.Request) -> httpx.Response:
        nonlocal in_flight
        in_flight += 1
        await answer.wait()  # every admitted call is in flight before any is answered
        body = {
            "model": "m",
            "choices": [{"message": {"content": json.dumps({"ok": True})}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "cost": COST / 1_000_000},
        }
        return httpx.Response(200, json=body)

    http = httpx.AsyncClient(transport=httpx.MockTransport(provider))
    llm = ChatCompletionsLlm("key", budget, ChatSettings("https://llm.example.com/v1", "m"), http)

    async def call() -> Any:
        return await llm.complete_json(
            system="s",
            user="u",
            json_schema={"type": "object"},
            reasoning="low",
            max_output_tokens=100,
            space_id=space,
            purpose="extraction",
        )

    await pg_uow.ensure(space)
    calls = [asyncio.create_task(call()) for _ in range(CALLS)]
    refused = 0
    for _ in range(500):
        refused = sum(1 for c in calls if c.done() and isinstance(c.exception(), BudgetExceeded))
        if in_flight + refused == CALLS:
            break
        await asyncio.sleep(0.02)
    answer.set()
    outcomes = await asyncio.gather(*calls, return_exceptions=True)
    await http.aclose()

    answered = [o for o in outcomes if not isinstance(o, BaseException)]
    assert all(isinstance(o, BudgetExceeded) for o in outcomes if isinstance(o, BaseException))
    async with pg_uow.system(space) as uow:
        spent, counted = (
            await uow.session.execute(
                text("SELECT cost_micros, calls FROM llm_spend WHERE purpose = 'extraction'")
            )
        ).one()
    assert spent <= CAP, f"{len(answered)} calls spent {spent} micros against a cap of {CAP}"
    assert (spent, counted) == (COST * len(answered), len(answered)), "every hold settled to its real cost"
    assert refused
    assert answered


@pytest.mark.parametrize("fails", [True, False])
async def test_a_failed_call_gives_its_reservation_back(pg_uow: CloningUnitOfWork, fails: bool) -> None:
    space = uuid7()
    budget = LlmBudget(pg_uow, FixedClock(), {"extraction": CAP})
    hold = await budget.reserve(space, "extraction")
    if fails:
        await budget.release(hold)
    else:
        await budget.settle(hold, 250)
    async with pg_uow.system(space) as uow:
        row = (
            await uow.session.execute(
                text("SELECT cost_micros, calls FROM llm_spend WHERE purpose = 'extraction'")
            )
        ).one()
    assert tuple(row) == ((0, 0) if fails else (250, 1))
