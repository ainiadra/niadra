import asyncio
import json
import time
from datetime import date
from typing import Any
from uuid import UUID

import httpx
import pytest
import respx

from niadra.adapters.outbound.llm.chat import ChatCompletionsLlm, ChatSettings, strict
from niadra.app.agent_memory import ANSWER_SCHEMA as NOTE_SCHEMA
from niadra.app.configure import _ANSWER_SCHEMA as ASSIST_SCHEMA
from niadra.app.extract import LlmBudget
from niadra.domain.common.errors import Unavailable
from niadra.domain.extract.budget import SpendHold
from niadra.domain.extract.errors import BudgetExceeded
from niadra.ports.infra import Reasoning
from niadra.testing.cell import SPACE_ID, Cell

BASE_URL = "https://llm.example.com/v1"
URL = f"{BASE_URL}/chat/completions"
MODEL = "vendor/extraction-model"
SCHEMA = {
    "type": "object",
    "properties": {"ok": {"type": "boolean"}},
    "required": ["ok"],
    "additionalProperties": False,
}
NO_WAIT = ChatSettings(BASE_URL, MODEL, backoff_s=0.0)


def completion(content: str = '{"ok": true}', cost: float = 0.00042) -> dict[str, Any]:
    return {
        "id": "gen-1",
        "model": "vendor/extraction-model",
        "provider": "Upstream",
        "choices": [{"message": {"role": "assistant", "content": content}, "finish_reason": "stop"}],
        "usage": {
            "prompt_tokens": 900,
            "completion_tokens": 120,
            "cost": cost,
            "prompt_tokens_details": {"cached_tokens": 800},
        },
    }


def client(
    cell: Cell, caps: dict[str, int] | None = None, settings: ChatSettings = NO_WAIT
) -> ChatCompletionsLlm:
    budget = LlmBudget(cell.uow, cell.clock, caps)
    return ChatCompletionsLlm("sk-test", budget, settings, httpx.AsyncClient())


async def call(
    llm: ChatCompletionsLlm, reasoning: Reasoning = "low", schema: dict[str, Any] | None = None
) -> Any:
    return await llm.complete_json(
        system="sys",
        user="usr",
        json_schema=schema or SCHEMA,
        reasoning=reasoning,
        max_output_tokens=500,
        space_id=SPACE_ID,
        purpose="extraction",
    )


@respx.mock
async def test_the_model_is_asked_with_the_reasoning_effort_usage_and_spend(cell: Cell) -> None:
    route = respx.post(URL).mock(return_value=httpx.Response(200, json=completion()))
    settings = ChatSettings(BASE_URL, MODEL, backoff_s=0.0, zero_retention=True, include_usage=True)
    result = await call(client(cell, settings=settings))
    sent = json.loads(route.calls.last.request.content)
    headers = route.calls.last.request.headers
    assert sent["model"] == MODEL
    assert "models" not in sent  # no other model to fall back to
    assert sent["reasoning"] == {"effort": "low"}
    assert sent["response_format"]["type"] == "json_schema"
    assert sent["response_format"]["json_schema"]["strict"] is True
    assert (sent["temperature"], sent["max_tokens"], sent["usage"]) == (0, 500, {"include": True})
    assert sent["provider"] == {"data_collection": "deny", "zdr": True}
    assert headers["authorization"] == "Bearer sk-test"
    assert "http-referer" not in headers
    assert "x-title" not in headers
    assert (result.content, result.provider, result.model) == ('{"ok": true}', "Upstream", MODEL)
    # The endpoint's cost already bills the cached part of the prompt at the cache-read price.
    assert (result.usage.input_tokens, result.usage.cached_tokens, result.usage.cost_micros) == (
        900,
        800,
        420,
    )
    async with cell.uow(SPACE_ID) as uow:
        assert await uow.extract.spent(cell.clock.now().date(), "extraction") == 420


@respx.mock
@pytest.mark.parametrize("effort", ["none", "low", "medium"])
async def test_the_caller_s_effort_is_the_one_sent(cell: Cell, effort: Reasoning) -> None:
    route = respx.post(URL).mock(return_value=httpx.Response(200, json=completion()))
    await call(client(cell), reasoning=effort)
    assert json.loads(route.calls.last.request.content)["reasoning"] == {"effort": effort}


@respx.mock
async def test_the_deployment_pins_the_providers_tried_first(cell: Cell) -> None:
    route = respx.post(URL).mock(return_value=httpx.Response(200, json=completion()))
    pinned = ChatSettings(BASE_URL, MODEL, provider_order=("azure", "openai"), zero_retention=True)
    await call(client(cell, settings=pinned))
    sent = json.loads(route.calls.last.request.content)
    assert sent["provider"] == {"data_collection": "deny", "zdr": True, "order": ["azure", "openai"]}


@respx.mock
async def test_routing_preferences_and_usage_are_only_sent_when_the_deployment_asks(cell: Cell) -> None:
    route = respx.post(URL).mock(return_value=httpx.Response(200, json=completion()))
    await call(client(cell))
    sent = json.loads(route.calls.last.request.content)
    assert "provider" not in sent
    assert "usage" not in sent
    assert sent["model"] == MODEL


def test_strict_only_for_schemas_the_provider_accepts_as_strict() -> None:
    assert strict(SCHEMA)
    # The assistant's proposed document is free-form, and a note's fields are optional.
    assert not strict(ASSIST_SCHEMA)
    assert not strict(NOTE_SCHEMA)
    nested = {
        "type": "object",
        "properties": {"items": {"type": "array", "items": {"type": "object", "properties": {"a": {}}}}},
        "required": ["items"],
        "additionalProperties": False,
    }
    assert not strict(nested)


@respx.mock
async def test_a_free_form_schema_is_sent_without_strict(cell: Cell) -> None:
    route = respx.post(URL).mock(return_value=httpx.Response(200, json=completion('{"document": {}}')))
    await call(client(cell), schema=dict(ASSIST_SCHEMA))
    assert json.loads(route.calls.last.request.content)["response_format"]["json_schema"]["strict"] is False


@respx.mock
async def test_server_errors_and_timeouts_retry_with_backoff(
    cell: Cell, monkeypatch: pytest.MonkeyPatch
) -> None:
    waits: list[float] = []

    async def sleep(seconds: float) -> None:
        waits.append(seconds)

    monkeypatch.setattr("niadra.adapters.outbound.llm.chat._pause", sleep)
    route = respx.post(URL).mock(
        side_effect=[httpx.Response(502), httpx.ReadTimeout("slow"), httpx.Response(200, json=completion())]
    )
    llm = client(cell, settings=ChatSettings(BASE_URL, MODEL, backoff_s=1.0))
    assert (await call(llm)).content == '{"ok": true}'
    assert route.call_count == 3
    assert waits == [1.0, 2.0]
    assert {json.loads(c.request.content)["model"] for c in route.calls} == {MODEL}
    route.mock(side_effect=[httpx.ReadTimeout("slow")] * 5)
    with pytest.raises(Unavailable):
        await call(llm)
    assert route.call_count == 8  # five more attempts, then the task's own backoff takes over


@respx.mock
@pytest.mark.parametrize(
    ("status", "headers", "retry_after"), [(402, {}, 3600), (429, {"retry-after": "17"}, 17)]
)
async def test_402_and_429_are_never_retried(
    cell: Cell, status: int, headers: dict[str, str], retry_after: int
) -> None:
    route = respx.post(URL).mock(
        return_value=httpx.Response(status, headers=headers, json={"error": {"code": status}})
    )
    with pytest.raises(Unavailable) as exc:
        await call(client(cell))
    assert route.call_count == 1
    assert exc.value.extra["retry_after_s"] == retry_after


@respx.mock
async def test_a_rejected_request_is_not_retried(cell: Cell) -> None:
    route = respx.post(URL).mock(return_value=httpx.Response(400, json={"error": {"code": 400}}))
    with pytest.raises(Unavailable):
        await call(client(cell))
    assert route.call_count == 1


@respx.mock
async def test_budget_guard_stops_calls_over_the_daily_cap(cell: Cell) -> None:
    route = respx.post(URL).mock(return_value=httpx.Response(200, json=completion(cost=0.0006)))
    llm = client(cell, {"extraction": 1000})
    await call(llm)
    await call(llm)
    with pytest.raises(BudgetExceeded):
        await call(llm)
    assert route.call_count == 2


@respx.mock
async def test_error_inside_a_200_body_is_retried_then_unavailable(cell: Cell) -> None:
    route = respx.post(URL).mock(
        return_value=httpx.Response(200, json={"error": {"code": 502, "message": "upstream"}})
    )
    with pytest.raises(Unavailable):
        await call(client(cell))
    assert route.call_count == 5


@respx.mock
async def test_the_whole_call_fits_the_minute_memory_is_promised_in(cell: Cell) -> None:
    # With 10 s for the whole call and 8 s per attempt, a provider that times out gets one
    # attempt, never a second that would run past the deadline.
    timeouts: list[float] = []

    def slow(request: httpx.Request) -> httpx.Response:
        timeouts.append(request.extensions["timeout"]["read"])
        raise httpx.ReadTimeout("slow", request=request)

    respx.post(URL).mock(side_effect=slow)
    with pytest.raises(Unavailable):
        await call(client(cell, settings=ChatSettings(BASE_URL, MODEL, timeout_s=8.0, deadline_s=10.0)))
    assert len(timeouts) == 1
    assert timeouts[0] <= 8.0
    assert ChatSettings(BASE_URL, MODEL).deadline_s + 15 <= 60  # the call leaves the rest of the minute


class _Ledger:
    """The budget as the adapter sees it: what each call settled."""

    def __init__(self) -> None:
        self.settled: list[int] = []

    async def reserve(self, space_id: UUID, purpose: str, estimate_micros: int | None = None) -> SpendHold:
        return SpendHold(space_id, purpose, date(2026, 9, 30), 0)

    async def settle(self, hold: SpendHold, cost_micros: int) -> None:
        self.settled.append(cost_micros)

    async def release(self, hold: SpendHold) -> None:
        pass


@respx.mock
async def test_a_slow_extraction_is_raced_by_a_second_request_and_both_are_paid_for() -> None:
    # The provider's slow tail kept memory past its minute; the answer that comes first is taken,
    # and the budget counts the abandoned request at the cost of the one used.
    sent: list[float] = []

    async def answer(request: httpx.Request) -> httpx.Response:
        sent.append(time.monotonic())
        if len(sent) == 1:
            await asyncio.sleep(5)
        return httpx.Response(200, json=completion(cost=0.0003))

    respx.post(URL).mock(side_effect=answer)
    ledger = _Ledger()
    settings = ChatSettings(BASE_URL, MODEL, hedge_after_s={"extraction": 0.05})
    llm = ChatCompletionsLlm("sk-test", ledger, settings, httpx.AsyncClient())
    started = time.monotonic()
    assert (await call(llm)).content == '{"ok": true}'
    assert time.monotonic() - started < 1
    assert len(sent) == 2
    assert ledger.settled == [600]


@respx.mock
async def test_a_purpose_without_a_hedge_waits_for_its_one_request() -> None:
    async def answer(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(0.2)
        return httpx.Response(200, json=completion(cost=0.0003))

    route = respx.post(URL).mock(side_effect=answer)
    ledger = _Ledger()
    settings = ChatSettings(BASE_URL, MODEL, hedge_after_s={"extraction": 0.05})
    llm = ChatCompletionsLlm("sk-test", ledger, settings, httpx.AsyncClient())
    await llm.complete_json(
        system="sys",
        user="usr",
        json_schema=dict(ASSIST_SCHEMA),
        reasoning="low",
        max_output_tokens=500,
        space_id=SPACE_ID,
        purpose="assistant",
    )
    assert route.call_count == 1
    assert ledger.settled == [300]
