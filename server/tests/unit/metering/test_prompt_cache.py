"""The agents' prompt cache, measured (15.3, rule 10): the usage an agent's turn carries, summed
once per model call, and the report with the estimated savings."""

from dataclasses import replace
from datetime import date
from typing import Any
from uuid import UUID

import pytest
from pydantic import ValidationError

from niadra.adapters.memory.metering import InMemoryUsageRepository
from niadra.app.metering import MeteringService
from niadra.config.models import ModelPriceConfig
from niadra.contracts.events import EventItem
from niadra.domain.common.errors import InvalidInput, NotFound
from niadra.domain.common.ids import uuid7
from niadra.domain.common.space import PersonPrincipal, SpaceRef
from niadra.domain.metering.prompt_cache import (
    PRICES_AS_OF,
    ModelPrice,
    ModelUsageCounters,
    ModelUsageKey,
    ModelUsageRow,
    group_usage,
    model_id,
    price_for,
    savings_micros,
)
from niadra.domain.vocabulary import Environment
from niadra.testing.cell import SPACE_ID, TENANT_ID, VOICE_SOURCE, WHATSAPP_SOURCE, Cell, principal
from tests.unit.ingest.fakes import build, message
from tests.unit.ingest.test_service import send

DAY = date(2026, 9, 22)
OPENAI = {"provider": "openai", "model": "gpt-4.1-2025-04-14", "prompt_tokens": 3000, "cached_tokens": 2048}


def agent_turn(key: str, usage: dict[str, Any] | None = OPENAI, **overrides: Any) -> dict[str, Any]:
    item = message(key, "Sua visita está marcada para amanhã.", speaker={"role": "ai_agent"}, **overrides)
    if usage is not None:
        item["usage"] = usage
    return item


def person(roles: frozenset[str] = frozenset({"analysis"}), **kw: Any) -> PersonPrincipal:
    space = SpaceRef(SPACE_ID, TENANT_ID, "us-east-2", Environment.SANDBOX)
    return replace(PersonPrincipal(space, uuid7(), roles), **kw)


def row(
    source: UUID, provider: str, model: str, calls: int, prompt: int, cached: int, write: int = 0
) -> ModelUsageRow:
    return ModelUsageRow(
        ModelUsageKey(DAY, source, provider, model), ModelUsageCounters(calls, prompt, cached, write)
    )


def test_usage_rides_only_on_the_agents_message_and_its_parts_fit() -> None:
    EventItem.model_validate(agent_turn("a1"))
    with pytest.raises(ValidationError, match="only valid on a message of the `ai_agent`"):
        EventItem.model_validate(message("c1", usage=OPENAI))
    with pytest.raises(ValidationError, match="part of prompt_tokens"):
        EventItem.model_validate(agent_turn("a2", {**OPENAI, "cache_write_tokens": 1000}))
    with pytest.raises(ValidationError):
        EventItem.model_validate(agent_turn("a3", {**OPENAI, "provider": "Open AI"}))
    # Never required: the turn without it is the same turn as before.
    assert EventItem.model_validate(agent_turn("a4", None)).usage is None


async def test_each_model_call_counts_once_per_day_source_and_model(cell: Cell) -> None:
    ingest, _, _ = build(cell)
    anthropic = {
        "provider": "anthropic",
        "model": "claude-sonnet-4-5-20250929",
        "prompt_tokens": 5000,
        "cached_tokens": 4000,
        "cache_write_tokens": 500,
    }
    first = await send(ingest, principal(), agent_turn("t1"), agent_turn("t2"), agent_turn("t3", anthropic))
    # The SDK retries a batch it could not confirm: the same turns are duplicates, never new usage.
    again = await send(ingest, principal(), agent_turn("t1"), agent_turn("t2"))
    assert (first.accepted, again.duplicates) == (3, 2)
    rows = await InMemoryUsageRepository(cell.uow.store).model_usage(DAY, DAY)
    by_model = {r.key.model: r for r in rows}
    assert by_model["gpt-4.1-2025-04-14"].counters == ModelUsageCounters(2, 6000, 4096, 0)
    assert by_model["claude-sonnet-4-5-20250929"].counters == ModelUsageCounters(1, 5000, 4000, 500)
    assert {r.key.source_id for r in rows} == {WHATSAPP_SOURCE}
    event = next(e for e in cell.uow.store.data["ingest"]["events"].values() if e.idempotency_key == "t1")
    assert event.record.content_meta["model_usage"] == {**OPENAI, "cache_write_tokens": 0}


def test_models_are_priced_by_their_name_however_it_was_decorated() -> None:
    assert model_id("gpt-4.1-2025-04-14") == "gpt-4.1"
    assert model_id("gpt-4o-mini-2024-07-18") == "gpt-4o-mini"
    assert model_id("claude-sonnet-4-5-20250929") == "claude-sonnet-4-5"
    assert model_id("anthropic/claude-sonnet-4.5") == "claude-sonnet-4-5"
    assert model_id("us.anthropic.claude-haiku-4-5-20251001-v1:0") == "claude-haiku-4-5"
    assert model_id("claude-opus-4-5@20251101") == "claude-opus-4-5"
    assert price_for("openai", "gpt-4.1-2025-04-14") == (ModelPrice(2.0, 0.5, 2.0), "product")
    assert price_for("anthropic", "anthropic/claude-sonnet-4.5") == (ModelPrice(3.0, 0.3, 3.75), "product")
    assert price_for("openai", "openai/gpt-5-mini") == (ModelPrice(0.25, 0.025, 0.25), "product")
    # A model the tables do not name has no price: a newer one is never priced as an older one.
    assert price_for("openai", "gpt-5.1") is None
    assert price_for("mistral", "mistral-large") is None
    mine = {"openai/gpt-5.1": ModelPrice(1.0, 0.1, 1.0)}
    assert price_for("openai", "gpt-5.1", mine) == (ModelPrice(1.0, 0.1, 1.0), "space")


def test_the_saving_is_the_cached_discount_less_the_write_premium() -> None:
    # OpenAI: 2,048 cached tokens of gpt-4.1 at 2.00 instead of 0.50 per million.
    assert savings_micros(ModelPrice(2.0, 0.5, 2.0), ModelUsageCounters(1, 3000, 2048)) == 3072
    # Anthropic: 4,000 read at 0.30 instead of 3.00, 500 written at 3.75 instead of 3.00.
    assert savings_micros(ModelPrice(3.0, 0.3, 3.75), ModelUsageCounters(1, 5000, 4000, 500)) == 10425
    # Writing without ever reading back costs more than it saves.
    assert savings_micros(ModelPrice(3.0, 0.3, 3.75), ModelUsageCounters(1, 5000, 0, 5000)) == -3750


def test_a_group_sums_the_savings_of_each_model_and_counts_what_has_no_price() -> None:
    rows = [
        row(WHATSAPP_SOURCE, "openai", "gpt-4.1-2025-04-14", 2, 6000, 4096),
        row(WHATSAPP_SOURCE, "anthropic", "claude-sonnet-4-5", 1, 5000, 4000, 500),
        row(WHATSAPP_SOURCE, "openai", "gpt-9", 3, 900, 300),
        row(VOICE_SOURCE, "openai", "gpt-4o", 1, 2000, 0),
    ]
    grouped = group_usage(rows, ["source_id"], vendors={})
    whatsapp = grouped[(WHATSAPP_SOURCE,)]
    assert whatsapp.counters == ModelUsageCounters(6, 11900, 8396, 500)
    assert whatsapp.savings_micros == 6144 + 10425
    assert (whatsapp.priced_calls, whatsapp.unpriced_calls) == (3, 3)
    assert whatsapp.hit_rate == pytest.approx(8396 / 11900)
    assert grouped[(VOICE_SOURCE,)].hit_rate == 0.0
    by_vendor = group_usage(rows, ["vendor"], vendors={WHATSAPP_SOURCE: "A", VOICE_SOURCE: None})
    assert set(by_vendor) == {("A",), ("",)}


@pytest.fixture
async def seeded(cell: Cell) -> MeteringService:
    repo = InMemoryUsageRepository(cell.uow.store)
    await repo.add_model_usage(
        [
            row(WHATSAPP_SOURCE, "openai", "gpt-4.1-2025-04-14", 2, 6000, 4096),
            row(WHATSAPP_SOURCE, "openai", "gpt-9", 1, 1000, 0),
            row(VOICE_SOURCE, "anthropic", "claude-sonnet-4-5-20250929", 1, 5000, 4000, 500),
        ]
    )
    return MeteringService(cell.uow, cell.clock, cell.config)


async def test_the_report_per_agent_with_rates_estimates_and_the_prices_it_used(
    seeded: MeteringService,
) -> None:
    report = await seeded.prompt_cache(person(), since=DAY, until=DAY)
    by_source = {b.source_id: b for b in report.buckets}
    whatsapp = by_source[WHATSAPP_SOURCE]
    assert (whatsapp.calls, whatsapp.prompt_tokens, whatsapp.cached_tokens) == (3, 7000, 4096)
    assert whatsapp.hit_rate == pytest.approx(4096 / 7000)
    assert (whatsapp.estimated_savings_micros, whatsapp.unpriced_calls) == (6144, 1)
    assert by_source[VOICE_SOURCE].estimated_savings_micros == 10425
    assert report.estimate is True
    assert report.prices_as_of == PRICES_AS_OF
    assert [(p.provider, p.model, p.table) for p in report.prices] == [
        ("anthropic", "claude-sonnet-4-5", "product"),
        ("openai", "gpt-4.1", "product"),
    ]
    by_model = await seeded.prompt_cache(person(), since=DAY, until=DAY, group_by=["vendor", "model"])
    unpriced = next(b for b in by_model.buckets if b.model == "gpt-9")
    assert (unpriced.vendor, unpriced.estimated_savings_micros, unpriced.unpriced_calls) == ("A", None, 1)


async def test_a_vendor_sees_its_own_agents_and_the_space_can_price_a_model(
    cell: Cell, seeded: MeteringService
) -> None:
    mine = await seeded.prompt_cache(principal(WHATSAPP_SOURCE), since=DAY, until=DAY)
    assert {b.source_id for b in mine.buckets} == {WHATSAPP_SOURCE}
    with pytest.raises(NotFound):
        await seeded.prompt_cache(principal(WHATSAPP_SOURCE), since=DAY, until=DAY, source_id=VOICE_SOURCE)
    with pytest.raises(InvalidInput):
        await seeded.prompt_cache(person(), since=DAY, until=DAY, group_by=["profile_id"])
    config = cell.config.space(SPACE_ID)
    prices = {"openai/gpt-9": ModelPriceConfig(input=1.0, cached_input=0.1)}
    cell.config.put(
        config.model_copy(update={"settings": config.settings.model_copy(update={"model_prices": prices})})
    )
    report = await seeded.prompt_cache(person(), since=DAY, until=DAY, group_by=["model"])
    priced = next(b for b in report.buckets if b.model == "gpt-9")
    assert (priced.estimated_savings_micros, priced.unpriced_calls) == (0, 0)
    assert ("openai", "gpt-9", "space") in {(p.provider, p.model, p.table) for p in report.prices}
