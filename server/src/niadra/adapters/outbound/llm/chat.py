"""`LlmClient` over an OpenAI-compatible Chat Completions endpoint (5.7).

- One model generates everything: the extraction model, named by the deployment (`ChatSettings.model`). The
  caller sets the reasoning effort per task; the model bills its thinking as output tokens.
- Structured output with `response_format: json_schema`, strict whenever the schema can be (every object
  closed and every property required); a free-form part, like the assistant's proposed document, is sent
  without `strict`, which the provider would reject.
- When the deployment asks for it, the request carries routing preferences that deny data collection and
  require zero data retention (5.14.0), and may pin the providers tried first (`provider.order`).
- When the deployment asks for it (`include_usage`), the endpoint returns the real cost, cache reads
  included, which the budget guard adds to `llm_spend` (5.9).
- 5xx, timeouts and errors inside a 200 body are retried with backoff within the call's deadline;
  402 and 429 are never retried here, they raise `Unavailable` with a retry hint (an hour for 402) so the
  task waits instead of burning credits or the rate limit.
- A purpose with a hedge (extraction) sends a second request when the first has not answered in time, and
  takes whichever answers first: the provider's slow tail, not the median, is what kept memory past its
  minute. The abandoned request may still be billed, so the budget counts it at the cost of the answer used.
- No attribution headers, and prompts and completions are never logged.
"""

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol
from uuid import UUID

import httpx

from niadra.domain.common.errors import Unavailable
from niadra.domain.extract.budget import SpendHold
from niadra.ports.infra import LlmResult, LlmUsage, Reasoning

log = logging.getLogger(__name__)

RETRY_AFTER_ERROR_S = 30
MIN_ATTEMPT_S = 5.0
RETRY_AFTER_CREDITS_S = 3600
_pause = asyncio.sleep


@dataclass(frozen=True, slots=True)
class ChatSettings:
    base_url: str
    model: str
    timeout_s: float = 30.0
    retries: int = 4
    """Attempts after the first, while the deadline holds one more: a rate limit upstream, reported inside
    a 200 body, clears within seconds, and a timeout leaves room for none (30 s of 45)."""
    backoff_s: float = 1.0
    """The wait before the first retry, doubled before each next one."""
    deadline_s: float = 45.0
    """The whole call, retries included: memory is promised within a minute of the session's end, and the
    queue and the reconciliation need the rest of it. The extraction model at `low` answered a session in
    3.7 s at the median and 10.6 s at p95 on 26/09."""
    hedge_after_s: Mapping[str, float] = field(default_factory=lambda: {"extraction": 12.0})
    """By purpose: how long the first request may go unanswered before a second one races it. The
    extraction model at `low` answered 26 of 31 extractions within 10.5 s on 30/09, and two of them not
    within 40 s; a purpose without an entry never hedges (the assistant's long documents would always pay
    twice)."""
    provider_order: tuple[str, ...] = ()
    """Providers a routing endpoint tries first (`provider.order`), to keep the route on endpoints whose
    retention was checked; empty leaves the choice to the router."""
    zero_retention: bool = False
    include_usage: bool = False


def provider_preferences(zero_retention: bool, order: tuple[str, ...] = ()) -> dict[str, Any]:
    provider: dict[str, Any] = {"data_collection": "deny", "zdr": True} if zero_retention else {}
    if order:
        provider["order"] = list(order)
    return provider


class SpendGuard(Protocol):
    """The daily budget per space and purpose (`LlmBudget` in the app layer): reserve before the call,
    settle to the real cost after it, release when the call fails."""

    async def reserve(
        self, space_id: UUID, purpose: str, estimate_micros: int | None = None
    ) -> SpendHold: ...

    async def settle(self, hold: SpendHold, cost_micros: int) -> None: ...

    async def release(self, hold: SpendHold) -> None: ...


async def call_within_budget(
    budget: SpendGuard,
    space_id: UUID,
    purpose: str,
    call: Callable[[], Awaitable[tuple[LlmResult, int]]],
) -> LlmResult:
    """Runs `call` under a reservation of the space's budget, settled to what `call` says the provider
    bills (its answer, plus any request it abandoned); a failed call gives the reservation back."""
    hold = await budget.reserve(space_id, purpose)
    try:
        result, billed_micros = await call()
    except BaseException:
        try:
            await budget.release(hold)
        except Exception:
            log.exception("budget reservation not released; it stays counted for the day")
        raise
    await budget.settle(hold, billed_micros)
    return result


class _Retryable(Exception):
    """An attempt that failed in a way another attempt may not: carries the error the call raises when no
    attempt is left."""

    def __init__(self, error: Unavailable) -> None:
        super().__init__(str(error))
        self.error = error


class ChatCompletionsLlm:
    def __init__(
        self,
        api_key: str,
        budget: SpendGuard,
        settings: ChatSettings,
        http: httpx.AsyncClient | None = None,
    ) -> None:
        self._settings = settings
        self._budget = budget
        self._http = http or httpx.AsyncClient(timeout=self._settings.timeout_s)
        self._headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

    async def aclose(self) -> None:
        await self._http.aclose()

    async def complete_json(
        self,
        *,
        system: str,
        user: str,
        json_schema: Mapping[str, Any],
        reasoning: Reasoning,
        max_output_tokens: int,
        space_id: UUID,
        purpose: str,
    ) -> LlmResult:
        body = _body(system, user, json_schema, purpose, reasoning, max_output_tokens, self._settings)
        hedge_after_s = self._settings.hedge_after_s.get(purpose)

        async def answer() -> tuple[LlmResult, int]:
            data, abandoned = await self._post(body, hedge_after_s)
            result = _result(data, self._settings.model)
            return result, result.usage.cost_micros * (1 + abandoned)

        return await call_within_budget(self._budget, space_id, purpose, answer)

    async def _post(self, body: dict[str, Any], hedge_after_s: float | None) -> tuple[dict[str, Any], int]:
        """The first answer of up to `retries + 1` attempts inside the deadline, and how many attempts still
        running were abandoned for it. A failed attempt is followed by the next one after its backoff; with
        `hedge_after_s`, a slow one is raced by the next one without waiting for it to fail."""
        settings = self._settings
        url = f"{settings.base_url}/chat/completions"
        deadline = time.monotonic() + settings.deadline_s
        attempts = settings.retries + 1
        running: set[asyncio.Task[dict[str, Any]]] = set()
        # The time each attempt had when it went out: a retry is planned as if the failed attempt had used
        # its whole timeout, so a provider that answers nothing gets no attempt past the deadline.
        had: dict[asyncio.Task[dict[str, Any]], float] = {}
        started = failures = 0

        def launch() -> None:
            nonlocal started
            left = deadline - time.monotonic()
            if left < MIN_ATTEMPT_S:
                raise Unavailable("llm call out of time", retry_after_s=RETRY_AFTER_ERROR_S)
            started += 1
            attempt = asyncio.ensure_future(self._attempt(url, body, min(settings.timeout_s, left)))
            running.add(attempt)
            had[attempt] = left

        try:
            launch()
            while True:
                hedge = (
                    hedge_after_s is not None
                    and started < attempts
                    and deadline - time.monotonic() - hedge_after_s >= MIN_ATTEMPT_S
                )
                done, running = await asyncio.wait(
                    running, timeout=hedge_after_s if hedge else None, return_when=asyncio.FIRST_COMPLETED
                )
                if not done:
                    launch()
                    continue
                for attempt in done:
                    error = attempt.exception()
                    if error is None:
                        return attempt.result(), len(running)
                    if not isinstance(error, _Retryable):
                        raise error
                    failures += 1
                    failed, left = error, had[attempt]
                if running:
                    continue
                wait = settings.backoff_s * 2 ** (failures - 1)
                # A retry only runs when the time left still holds the wait and a useful attempt.
                if started >= attempts or left - settings.timeout_s - wait < MIN_ATTEMPT_S:
                    raise failed.error from failed.__cause__
                await _pause(wait)
                launch()
        finally:
            for attempt in running:
                attempt.cancel()
            await asyncio.gather(*running, return_exceptions=True)

    async def _attempt(self, url: str, body: dict[str, Any], timeout_s: float) -> dict[str, Any]:
        """One request; `_Retryable` when another attempt may do better."""
        try:
            response = await self._http.post(url, json=body, headers=self._headers, timeout=timeout_s)
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            raise _Retryable(
                Unavailable("llm provider unreachable", retry_after_s=RETRY_AFTER_ERROR_S)
            ) from exc
        status = response.status_code
        if status == 402:
            raise Unavailable("llm credits exhausted", retry_after_s=RETRY_AFTER_CREDITS_S)
        if status == 429:
            raise Unavailable("llm rate limited", retry_after_s=_retry_after(response))
        if status >= 500 or status == 408:
            raise _Retryable(
                Unavailable(f"llm provider failed with status {status}", retry_after_s=RETRY_AFTER_ERROR_S)
            )
        if status >= 400:
            raise Unavailable(f"llm request rejected with status {status}", retry_after_s=RETRY_AFTER_ERROR_S)
        data: dict[str, Any] = response.json()
        if "error" in data and not data.get("choices"):
            # Some gateways report upstream failures inside a 200 body.
            raise _Retryable(Unavailable("llm provider returned an error", retry_after_s=RETRY_AFTER_ERROR_S))
        return data


def _body(
    system: str,
    user: str,
    schema: Mapping[str, Any],
    name: str,
    reasoning: Reasoning,
    max_output_tokens: int,
    settings: ChatSettings,
) -> dict[str, Any]:
    # The system prompt comes first and does not change between sessions of a space, so the provider's
    # automatic prompt cache (from 1,024 tokens) bills it at the cache-read price after the first call.
    body: dict[str, Any] = {
        "model": settings.model,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        "max_tokens": max_output_tokens,
        "temperature": 0,
        "reasoning": {"effort": reasoning},
        "response_format": {
            "type": "json_schema",
            "json_schema": {"name": name, "strict": strict(schema), "schema": dict(schema)},
        },
    }
    if settings.include_usage:
        body["usage"] = {"include": True}
    if provider := provider_preferences(settings.zero_retention, settings.provider_order):
        body["provider"] = provider
    return body


def strict(schema: Mapping[str, Any]) -> bool:
    """Whether OpenAI's strict structured output accepts the schema: every object closed and every property
    required, down to the leaves."""
    if schema.get("type") == "object" or "properties" in schema:
        props = schema.get("properties")
        if not isinstance(props, Mapping) or schema.get("additionalProperties") is not False:
            return False
        if set(schema.get("required") or ()) != set(props):
            return False
        if not all(strict(p) for p in props.values()):
            return False
    items = schema.get("items")
    if isinstance(items, Mapping) and not strict(items):
        return False
    for key in ("anyOf", "oneOf", "allOf"):
        if not all(strict(s) for s in schema.get(key) or ()):
            return False
    return all(strict(d) for d in (schema.get("$defs") or {}).values())


def _result(data: dict[str, Any], model: str) -> LlmResult:
    try:
        content = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        content = None
    if not isinstance(content, str):
        raise Unavailable("llm answer without content", retry_after_s=RETRY_AFTER_ERROR_S)
    usage = data.get("usage") or {}
    cached = (usage.get("prompt_tokens_details") or {}).get("cached_tokens") or 0
    return LlmResult(
        content=content,
        model=str(data.get("model") or model),
        provider=str(data.get("provider") or ""),
        usage=LlmUsage(
            input_tokens=int(usage.get("prompt_tokens") or 0),
            output_tokens=int(usage.get("completion_tokens") or 0),
            cached_tokens=int(cached),
            cost_micros=round(float(usage.get("cost") or 0) * 1_000_000),
        ),
    )


def _retry_after(response: httpx.Response) -> int:
    raw = response.headers.get("retry-after", "")
    return int(raw) if raw.isdigit() else 60
