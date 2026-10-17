"""Usage and billing aggregates (5.15)."""

from collections.abc import Sequence
from datetime import UTC, date, datetime, time, timedelta
from uuid import UUID

from niadra.app.extract import DEFAULT_DAILY_CAPS_MICROS, daily_ceiling, spend_history
from niadra.config.models import SpaceConfig
from niadra.contracts.metering import (
    BudgetLine,
    BudgetReport,
    ModelPriceOut,
    PromptCacheBucket,
    PromptCacheReport,
)
from niadra.domain.common.errors import InvalidInput, NotFound
from niadra.domain.common.space import Principal
from niadra.domain.measure.access import can_see, visible_sources
from niadra.domain.metering import (
    EVENT_METRICS,
    HOUR,
    Granularity,
    Metric,
    UsageRow,
    across_sources,
    by_day,
    hour_floor,
    hours_to_roll,
    merge_counts,
    usage_event_id,
)
from niadra.domain.metering.prompt_cache import (
    PRICES_AS_OF,
    PROMPT_CACHE_DIMENSIONS,
    ModelPrice,
    group_usage,
    model_id,
    price_for,
)
from niadra.domain.tasks import Task
from niadra.ports.infra import Clock, ConfigProvider
from niadra.ports.uow import UnitOfWork, UnitOfWorkFactory

MAX_RANGE = {Granularity.HOUR: timedelta(days=31), Granularity.DAY: timedelta(days=400)}
PROMPT_CACHE_PERIOD = timedelta(days=29)


class MeteringService:
    def __init__(self, uow: UnitOfWorkFactory, clock: Clock, config: ConfigProvider | None = None) -> None:
        self._uow = uow
        self._clock = clock
        self._config = config

    async def budget(self, space_id: UUID) -> BudgetReport:
        """Today's AI spend against the daily ceiling of each purpose: past it, extraction waits
        for the next UTC day and memory arrives late."""
        config = self._config.space(space_id) if self._config is not None else None
        now = self._clock.now()
        today = now.astimezone(UTC).date()
        tomorrow = datetime.combine(today + timedelta(days=1), time(), tzinfo=UTC)
        lines = []
        async with self._uow(space_id) as uow:
            for purpose in sorted(DEFAULT_DAILY_CAPS_MICROS):
                history = await spend_history(uow, purpose, today)
                cap, from_contract = daily_ceiling(config, purpose, history=history)
                spent = await uow.extract.spent(today, purpose)
                reached = cap is not None and spent >= cap
                lines.append(
                    BudgetLine(
                        purpose=purpose,
                        day=today,
                        cap_micros=cap,
                        from_contract=from_contract,
                        spent_micros=spent,
                        reached=reached,
                        resumes_at=tomorrow if reached else None,
                    )
                )
        return BudgetReport(items=lines)

    async def usage_rollup(self, task: Task) -> None:
        """Hourly task per space: recounts the current and previous hour into `usage_hourly`."""
        if hour := task.payload.get("hour"):
            hours = [hour_floor(datetime.fromisoformat(hour))]
        else:
            at = task.payload.get("scheduled_for")
            hours = hours_to_roll(datetime.fromisoformat(at) if at else self._clock.now())
        for start in hours:
            await self.rollup_hour(task.space_id, start)

    async def rollup_hour(self, space_id: UUID, hour: datetime) -> None:
        start = hour_floor(hour)
        async with self._uow(space_id) as uow:
            counted = await uow.metering.count_sources(space_id, start, start + HOUR)
            recorded = await uow.metering.sum_events(space_id, start, start + HOUR)
            await uow.metering.replace_hour(space_id, start, merge_counts(counted, recorded))

    async def usage(
        self,
        space_id: UUID,
        since: datetime,
        until: datetime,
        granularity: Granularity = Granularity.HOUR,
        metrics: Sequence[str] | None = None,
        *,
        source_id: UUID | None = None,
        per_source: bool = False,
    ) -> list[UsageRow]:
        """The space's usage; `source_id` keeps one agent's rows, `per_source` keeps a row per agent."""
        if since.tzinfo is None or until.tzinfo is None:
            raise InvalidInput("since and until need a timezone")
        if until <= since:
            raise InvalidInput("until must be after since")
        if until - since > MAX_RANGE[granularity]:
            raise InvalidInput(f"the range is too long for {granularity} granularity")
        async with self._uow(space_id) as uow:
            rows = await uow.metering.hourly(space_id, hour_floor(since), until, metrics, source_id=source_id)
        if not per_source:
            rows = across_sources(rows)
        return by_day(rows) if granularity is Granularity.DAY else rows

    async def prompt_cache(
        self,
        principal: Principal,
        *,
        since: date | None = None,
        until: date | None = None,
        group_by: Sequence[str] = ("source_id",),
        source_id: UUID | None = None,
    ) -> PromptCacheReport:
        """The agents' prompt cache per day, source, vendor, provider or model, from the usage each model
        call reported, with the savings as an estimate (15.3, rule 10). A vendor sees its sources only."""
        assert self._config is not None, "the prompt-cache report needs the configuration"
        until = until or self._clock.now().date()
        since = since or until - PROMPT_CACHE_PERIOD
        if since > until or (until - since).days > 400:
            raise InvalidInput("the period must be ordered and at most 400 days")
        if set(group_by) - set(PROMPT_CACHE_DIMENSIONS):
            raise InvalidInput("group_by accepts " + ", ".join(PROMPT_CACHE_DIMENSIONS))
        allowed = visible_sources(principal)
        if source_id is not None:
            if not can_see(principal, source_id):
                raise NotFound("source not found")
            allowed = frozenset({source_id})
        config = self._config.space(principal.space.space_id)
        async with self._uow(principal.space.space_id) as uow:
            rows = await uow.metering.model_usage(since, until, allowed)
        prices = space_prices(config)
        vendors = {s.source_id: s.vendor for s in config.sources}
        grouped = group_usage(rows, group_by, vendors=vendors, space_prices=prices)
        buckets = [
            PromptCacheBucket(
                **dict(zip(group_by, key, strict=True)),
                calls=t.counters.calls,
                prompt_tokens=t.counters.prompt_tokens,
                cached_tokens=t.counters.cached_tokens,
                cache_write_tokens=t.counters.cache_write_tokens,
                hit_rate=t.hit_rate,
                estimated_savings_micros=t.savings_micros if t.priced_calls else None,
                unpriced_calls=t.unpriced_calls,
            )
            for key, t in sorted(grouped.items(), key=lambda kv: tuple(str(p) for p in kv[0]))
        ]
        used: dict[tuple[str, str], ModelPriceOut] = {}
        for row in rows:
            found = price_for(row.key.provider, row.key.model, prices)
            name = (row.key.provider, model_id(row.key.model))
            if found is not None and name not in used:
                price, table = found
                used[name] = ModelPriceOut(
                    provider=name[0],
                    model=name[1],
                    input=price.input,
                    cached_input=price.cached_input,
                    cache_write=price.cache_write,
                    table="space" if table == "space" else "product",
                )
        return PromptCacheReport(
            since=since,
            until=until,
            group_by=list(group_by),
            buckets=buckets,
            prices=[used[k] for k in sorted(used)],
            prices_as_of=PRICES_AS_OF,
        )


def space_prices(config: SpaceConfig) -> dict[str, ModelPrice]:
    return {
        key: ModelPrice(p.input, p.cached_input, p.input if p.cache_write is None else p.cache_write)
        for key, p in config.settings.model_prices.items()
    }


async def record_usage_in(
    uow: UnitOfWork,
    space_id: UUID,
    metric: Metric,
    quantity: int,
    natural_key: Sequence[object],
    cost_micros: int,
    occurred_at: datetime,
) -> bool:
    """A usage event of a metric without a source table, inside the caller's transaction, for the modules
    whose work is the usage (exported bytes, assistant tokens): the usage row commits with the work, and
    `natural_key` makes a retried call count once (5.15)."""
    if metric not in EVENT_METRICS:
        raise InvalidInput(f"{metric} is counted from its source table")
    if quantity < 0 or cost_micros < 0:
        raise InvalidInput("usage cannot be negative")
    usage_id = usage_event_id(metric, *natural_key)
    return await uow.metering.record_event(
        space_id, usage_id, metric.value, quantity, cost_micros, occurred_at
    )
