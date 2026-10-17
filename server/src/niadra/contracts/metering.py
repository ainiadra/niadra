"""`GET /v1/usage`: hourly or daily usage aggregates of the space (5.15)."""

from datetime import date, datetime
from typing import Literal
from uuid import UUID

from pydantic import Field

from niadra.contracts.common import Model
from niadra.domain.metering import Granularity


class UsageItem(Model):
    start: datetime = Field(description="Start of the hour or UTC day.")
    metric: str = Field(description="`session_billable`, `context_read`, `event_ingested`, ...")
    quantity: int
    cost_micros: int = Field(description="Our cost in millionths of a US dollar, when the metric has one.")
    source_id: str | None = Field(
        default=None,
        description="With `group_by=source`: the agent or system; null for the space's own work (exports, "
        "triggers, webhooks) and for hours counted before usage was kept per source.",
    )


class UsageResponse(Model):
    since: datetime
    until: datetime
    granularity: Granularity
    source_id: str | None = None
    group_by: Literal["source"] | None = None
    items: list[UsageItem]


class PromptCacheBucket(Model):
    day: date | None = None
    source_id: UUID | None = None
    vendor: str | None = Field(default=None, description='From the source configuration; "" when none.')
    provider: str | None = None
    model: str | None = None
    calls: int = Field(description="Agent turns whose model call reported its usage.")
    prompt_tokens: int = Field(description="Every input token of those calls, cached ones included.")
    cached_tokens: int = Field(description="Input tokens the provider read from its prompt cache.")
    cache_write_tokens: int = Field(description="Input tokens written to the cache.")
    hit_rate: float | None = Field(
        default=None, description="`cached_tokens` over `prompt_tokens`; null without tokens."
    )
    estimated_savings_micros: int | None = Field(
        default=None,
        description="An estimate, in millionths of a US dollar: cached tokens at the input price minus "
        "the cached price, less the write premium, by the price of each call's model. Null when no call "
        "of the bucket has a price.",
    )
    unpriced_calls: int = Field(
        default=0, description="Calls of models without a price in either table: left out of the estimate."
    )


class ModelPriceOut(Model):
    provider: str
    model: str
    input: float = Field(description="USD per million input tokens.")
    cached_input: float
    cache_write: float
    table: Literal["product", "space"] = Field(
        description="`product`: the product's table of public prices; `space`: the space's settings."
    )


class PromptCacheReport(Model):
    """`GET /v1/usage/prompt-cache`: the agents' prompt cache, from what the providers reported (15.3)."""

    since: date
    until: date
    group_by: list[str]
    buckets: list[PromptCacheBucket]
    prices: list[ModelPriceOut] = Field(description="The price of every model the estimate used.")
    prices_as_of: str = Field(description="When the product's table was taken from the public price lists.")
    estimate: bool = Field(
        default=True, description="Always true: the savings are an estimate, never what a provider billed."
    )


class BudgetLine(Model):
    purpose: str = Field(description="`extraction` or `assistant`.")
    day: date = Field(description="The UTC day the spend counts in.")
    cap_micros: int | None = Field(description="The daily ceiling, in millionths of a US dollar.")
    from_contract: bool = Field(description="True when the contract sets it; false for the cell's default.")
    spent_micros: int
    reached: bool = Field(description="Work of this purpose waits for the next UTC day.")
    resumes_at: datetime | None = None


class BudgetReport(Model):
    items: list[BudgetLine]


class ContextLatencyWindow(Model):
    window: Literal["24h", "7d"]
    reads: int = Field(description="Context reads of the space in the window.")
    p50_ms: float | None = Field(default=None, description="Median server time; null without reads.")
    p95_ms: float | None = None
    under_target: float | None = Field(
        default=None, description="Share of the reads the server answered in `target_ms` or less."
    )
    within_slo: bool | None = Field(
        default=None, description="Whether that share reached the objective, `slo` of the reads."
    )
    error_budget_used: float | None = Field(
        default=None,
        description="Share of the objective's error budget (the reads over `target_ms` it allows) spent; "
        "above 1 the objective was missed.",
    )
    degraded: float | None = Field(
        default=None,
        description="Share of the reads served degraded: memory late, missing or a block left out.",
    )


class ContextLatencyReport(Model):
    """The server time of the space's context reads (`/v1/context`), without the network, from the cell's
    own metrics, refreshed at most once a minute."""

    available: bool = Field(
        description="False when the cell's metrics could not be read; the windows are empty."
    )
    target_ms: int = Field(description="The published server time of a context read.")
    slo: float = Field(description="The share of reads the objective expects under `target_ms`.")
    windows: list[ContextLatencyWindow]
    as_of: datetime
