"""The agents' prompt cache, measured (15.3, rule 10): what each model call reported and what the cached
part of its prompt saved, as an estimate.

The SDK reads the usage the provider returns with every model call it wraps (or the one the caller
passes with the agent's turn) and sends it with that turn: every input token of the call, the ones read
from the provider's prompt cache and the ones written to it. The cell sums them per day, source, provider
and model. The saving is an estimate from a price table: a cached token costs the cached price instead
of the input price, and a token written to the cache costs the write price (Anthropic charges a premium
for it; OpenAI does not). Prices change and differ by contract, so every figure is labeled an estimate,
the table says when it was taken, and a space can set its own prices.
"""

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, date
from uuid import UUID

from niadra.domain.records import EventRecord

PRICES_AS_OF = "2026-09-24"
"""When the product's table was taken from the providers' public price lists (standard tier, USD)."""


@dataclass(frozen=True, slots=True)
class ModelPrice:
    """USD per million tokens. One token at 1 USD per million is exactly one micro-dollar."""

    input: float
    cached_input: float
    cache_write: float


def _anthropic(input_: float, cached: float) -> ModelPrice:
    # A 5-minute cache write costs 1.25 times the input price.
    return ModelPrice(input_, cached, input_ * 1.25)


def _openai(input_: float, cached: float) -> ModelPrice:
    # OpenAI caches on its own and charges nothing extra to write.
    return ModelPrice(input_, cached, input_)


PRODUCT_PRICES: Mapping[str, Mapping[str, ModelPrice]] = {
    "anthropic": {
        "claude-fable-5-1": _anthropic(10.0, 0.25),
        "claude-fable-5": _anthropic(10.0, 1.0),
        "claude-opus-5-5": _anthropic(4.0, 0.20),
        "claude-opus-5": _anthropic(5.0, 0.50),
        "claude-opus-4-8": _anthropic(5.0, 0.50),
        "claude-opus-4-7": _anthropic(5.0, 0.50),
        "claude-opus-4-6": _anthropic(5.0, 0.50),
        "claude-opus-4-5": _anthropic(5.0, 0.50),
        "claude-opus-4-1": _anthropic(15.0, 1.50),
        "claude-opus-4-0": _anthropic(15.0, 1.50),
        "claude-opus-4": _anthropic(15.0, 1.50),
        "claude-sonnet-5": _anthropic(2.0, 0.20),
        "claude-sonnet-4-6": _anthropic(3.0, 0.30),
        "claude-sonnet-4-5": _anthropic(3.0, 0.30),
        "claude-sonnet-4-0": _anthropic(3.0, 0.30),
        "claude-sonnet-4": _anthropic(3.0, 0.30),
        "claude-haiku-4-5": _anthropic(1.0, 0.10),
    },
    "openai": {
        "gpt-5": _openai(1.25, 0.125),
        "gpt-5-mini": _openai(0.25, 0.025),
        "gpt-5-nano": _openai(0.05, 0.005),
        "gpt-4.1": _openai(2.0, 0.50),
        "gpt-4.1-mini": _openai(0.40, 0.10),
        "gpt-4.1-nano": _openai(0.10, 0.025),
        "gpt-4o": _openai(2.50, 1.25),
        "gpt-4o-mini": _openai(0.15, 0.075),
        "o3": _openai(2.0, 0.50),
        "o3-mini": _openai(1.10, 0.55),
        "o4-mini": _openai(1.10, 0.275),
        "o1": _openai(15.0, 7.50),
    },
}

# `anthropic/claude-...` (routers), `anthropic.claude-...` or `us.anthropic.claude-...` (Bedrock),
# `...@20251101` (Vertex) and dated or versioned snapshots all name the same model.
_ROUTER_PREFIX = re.compile(r"^[a-z0-9_-]+/")
_BEDROCK_PREFIX = re.compile(r"^(?:[a-z]{2,4}\.)?anthropic\.")
_SNAPSHOT = re.compile(r"(?:@.*|-\d{4}-\d{2}-\d{2}|-\d{8}(?:-v\d+:\d+)?|-v\d+:\d+)$")


def model_id(model: str) -> str:
    """The model's name without router, cloud or snapshot decorations, as the price table keys it."""
    name = _SNAPSHOT.sub("", _BEDROCK_PREFIX.sub("", _ROUTER_PREFIX.sub("", model.strip().lower())))
    # Routers write Claude versions with a dot (`claude-sonnet-4.5`); Anthropic, with a hyphen.
    return name.replace(".", "-") if name.startswith("claude-") else name


def price_for(
    provider: str, model: str, space_prices: Mapping[str, ModelPrice] | None = None
) -> tuple[ModelPrice, str] | None:
    """The space's price for `provider/model`, then the product's, with where it came from. A model the
    tables do not name exactly, or by a dated name of it, has no price: guessing would misstate it."""
    name = model_id(model)
    for key in (f"{provider}/{model}", f"{provider}/{name}"):
        if space_prices and key in space_prices:
            return space_prices[key], "space"
    table = PRODUCT_PRICES.get(provider.strip().lower(), {})
    if name in table:
        return table[name], "product"
    return None


@dataclass(slots=True)
class ModelUsageCounters:
    calls: int = 0
    prompt_tokens: int = 0
    cached_tokens: int = 0
    cache_write_tokens: int = 0

    def add(self, other: "ModelUsageCounters") -> None:
        self.calls += other.calls
        self.prompt_tokens += other.prompt_tokens
        self.cached_tokens += other.cached_tokens
        self.cache_write_tokens += other.cache_write_tokens


@dataclass(frozen=True, slots=True)
class ModelUsageKey:
    day: date
    source_id: UUID
    provider: str
    model: str


@dataclass(frozen=True, slots=True)
class ModelUsageRow:
    key: ModelUsageKey
    counters: ModelUsageCounters


def savings_micros(price: ModelPrice, counters: ModelUsageCounters) -> int:
    """What the cache saved against paying every input token at the input price; negative when the
    writes cost more than the reads saved."""
    saved = counters.cached_tokens * (price.input - price.cached_input)
    premium = counters.cache_write_tokens * (price.cache_write - price.input)
    return round(saved - premium)


@dataclass(slots=True)
class CacheTotals:
    counters: ModelUsageCounters
    savings_micros: int = 0
    priced_calls: int = 0
    unpriced_calls: int = 0

    @property
    def hit_rate(self) -> float | None:
        prompt = self.counters.prompt_tokens
        return self.counters.cached_tokens / prompt if prompt > 0 else None


PROMPT_CACHE_DIMENSIONS = ("day", "source_id", "vendor", "provider", "model")


def group_usage(
    rows: Iterable[ModelUsageRow],
    by: Iterable[str],
    *,
    vendors: Mapping[UUID, str | None],
    space_prices: Mapping[str, ModelPrice] | None = None,
) -> dict[tuple[object, ...], CacheTotals]:
    """Counters and estimated savings summed per group; each row is priced by its own model first, so
    a group that mixes models sums their savings, never averages their prices."""
    dims = tuple(by)
    if unknown := set(dims) - set(PROMPT_CACHE_DIMENSIONS):
        raise ValueError(f"unknown dimension: {sorted(unknown)}")
    grouped: dict[tuple[object, ...], CacheTotals] = {}
    for row in rows:
        key = tuple(
            vendors.get(row.key.source_id) or "" if name == "vendor" else getattr(row.key, name)
            for name in dims
        )
        totals = grouped.setdefault(key, CacheTotals(ModelUsageCounters()))
        totals.counters.add(row.counters)
        found = price_for(row.key.provider, row.key.model, space_prices)
        if found is None:
            totals.unpriced_calls += row.counters.calls
            continue
        totals.priced_calls += row.counters.calls
        totals.savings_micros += savings_micros(found[0], row.counters)
    return grouped


def model_usage_rows(records: Iterable[EventRecord]) -> list[ModelUsageRow]:
    """What the ingestion adds for the new events of a batch: the usage the agent's turns carry, summed per
    day (UTC, of when the turn happened), source, provider and model."""
    found: dict[ModelUsageKey, ModelUsageCounters] = {}
    for record in records:
        usage = (record.content_meta or {}).get("model_usage")
        if not isinstance(usage, Mapping):
            continue
        day = record.occurred_at.astimezone(UTC).date()
        key = ModelUsageKey(day, record.source_id, str(usage["provider"]), str(usage["model"]))
        found.setdefault(key, ModelUsageCounters()).add(
            ModelUsageCounters(
                calls=1,
                prompt_tokens=int(usage["prompt_tokens"]),
                cached_tokens=int(usage.get("cached_tokens") or 0),
                cache_write_tokens=int(usage.get("cache_write_tokens") or 0),
            )
        )
    return [ModelUsageRow(key, counters) for key, counters in found.items()]
