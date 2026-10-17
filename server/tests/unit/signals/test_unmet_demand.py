"""Unmet demand: tool calls that came back empty or poor, by what their arguments asked on the
type registry's fields, kept raw, added up each night by pseudonym, reported by week and combination only
where at least ten distinct people asked it, and taken out of every day an erasure's subject is in."""

import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from niadra.config.measurement import MeasurementSettings
from niadra.config.models import FeatureSettings, SpaceConfig
from niadra.config.object_types import ObjectTypeRegistry
from niadra.contracts.common import Handle
from niadra.contracts.privacy import ForgetRequest
from niadra.contracts.turns import TurnRecord
from niadra.domain.ingest.hot import unpack_envelope
from niadra.domain.privacy.files import from_parquet
from niadra.domain.signals.demand import DemandRow, asked_of, demand_entry, normalized, weekly
from niadra.domain.signals.derive import Catalog
from niadra.domain.signals.storage import demand_key, object_aad
from niadra.domain.vocabulary import Feature, HandleType
from niadra.testing.cell import SPACE_ID, WHATSAPP_SOURCE, Cell, sample_config
from tests.unit.flow.harness import Flow
from tests.unit.signals.test_signals_flow import Applied, said

RETAIL = json.loads((Path(__file__).parents[3] / "src/niadra/config/templates/retail.json").read_text())
CATALOG = Catalog(hidden=frozenset({"item_variant.buyer_note"}), sensitive_types=frozenset({"prescription"}),
                  tracked={"item_variant": frozenset(), "prescription": frozenset()})  # fmt: skip


def wants(attr: str, op: str, *values: Any, source: str = "tool_args") -> dict[str, Any]:
    return {"kind": "preference", "attr": attr, "op": op, "values": list(values), "strength": "must",
            "scope": "turn", "source": source}  # fmt: skip


def test_values_are_normalized_so_the_same_ask_counts_together() -> None:
    assert [normalized(v) for v in (" Vermelho ", "MIDI  longo", 399, 412.5, 0.0349, True, 0)] == [
        "vermelho", "midi longo", 400, 410, 0.035, True, 0
    ]  # fmt: skip


def test_only_the_tools_arguments_on_declared_fields_count_never_a_hidden_field_or_a_sensitive_type() -> None:
    asked = asked_of(
        [
            wants("item_variant.color", "in", "Vermelho"),
            wants("item_variant.price_sale", "lte", 399),
            wants("item_variant.color", "in", "vermelho "),  # the same value twice is one
            wants("item_variant.buyer_note", "eq", "para a Ana"),  # hidden
            wants("prescription.drug", "eq", "x"),  # sensitive type
            wants("look.style", "eq", "praia"),  # a type the registry does not declare
            wants("item_variant.size_label", "eq", "38", source="stated"),  # said, not a tool's argument
            {"kind": "engaged", "ref": "item_variant:loja:1", "how": "click"},
        ],
        CATALOG,
    )
    assert asked == [
        {"attr": "item_variant.color", "op": "in", "values": ["vermelho"]},
        {"attr": "item_variant.price_sale", "op": "lte", "values": [400]},
    ]


def test_a_call_is_demand_when_it_returned_fewer_than_three_results() -> None:
    assert demand_entry("search_items", 3, []) is None
    assert demand_entry("search_items", 2, []) == {"kind": "demand", "tool": "search_items", "results": 2,
                                                   "asked": []}  # fmt: skip


def test_a_week_is_reported_only_where_ten_distinct_people_asked() -> None:
    monday = date(2026, 9, 28)
    red = '[{"attr":"item_variant.color","op":"in","values":["vermelho"]}]'
    rows = [DemandRow(f"p{i}", "search_items", red, 2, 1) for i in range(10)]
    rows += [DemandRow(f"p{i}", "search_items", "[]", 1, 1) for i in range(9)]
    days = [
        (monday, rows[:5]),
        (monday + timedelta(days=3), rows[5:]),
        (monday + timedelta(days=7), rows[:1]),
    ]
    [found] = weekly(days, 10)
    assert (found.week, found.tool, found.calls, found.empty) == (monday, "search_items", 20, 10)
    assert found.people == 10
    assert found.asked == [{"attr": "item_variant.color", "op": "in", "values": ["vermelho"]}]


# Across the modules.


def configured() -> SpaceConfig:
    return sample_config().model_copy(
        update={
            "features": FeatureSettings(enabled=frozenset({Feature.SIGNALS, Feature.MEASUREMENT})),
            "object_types": ObjectTypeRegistry.model_validate(RETAIL),
            "measurement": MeasurementSettings(),
        }
    )


def searched(turn_id: str, conversation: str, at: datetime, results: int, *asked: dict[str, Any]) -> Applied:
    record = TurnRecord.model_validate(
        {
            "turn_id": turn_id,
            "conversation_id": conversation,
            "agent": {"name": "stylist"},
            "started_at": at.isoformat(),
            "content_mode": "hash_only",
            "interactions": list(asked),
            "calls": [
                {
                    "call_id": "c1", "kind": "tool", "name": "search_items",
                    "applied": {"constraints": "cv_1", "results_checked": results, "violations": 0,
                                "unverifiable": 0},
                },
                {"call_id": "c2", "kind": "tool", "name": "get_look", "status": "error",
                 "applied": {"constraints": "cv_1", "results_checked": 0, "violations": 0,
                             "unverifiable": 0}},
            ],
        }
    )  # fmt: skip
    return Applied(WHATSAPP_SOURCE, at, record)


async def a_day_of_searches(flow: Flow, people: int, results: int = 0) -> None:
    now = flow.cell.clock.now()
    red = [wants("item_variant.color", "in", "vermelho"), wants("item_variant.price_sale", "lte", 400)]
    for i in range(people):
        conversation = f"wa-{i}"
        item = said(conversation, "Tem vestido vermelho?", now, key=f"k-{i}")
        item["handles"] = [{"type": "phone_e164", "value": f"+55119555500{i:02d}"}]
        assert (await flow.send(WHATSAPP_SOURCE, item)).errors == []
    await flow.drain()
    async with flow.cell.uow(SPACE_ID) as uow:
        turns = [searched(f"t{i}", f"wa-{i}", now, results, *red) for i in range(people)]
        await flow.signals.consume(uow, SPACE_ID, turns)  # type: ignore[arg-type]
    # The session overlay, which the next turn's constraints block reads, never holds a demand entry.
    overlay = [key for entries in flow.signal_overlay.entries.values() for key in entries]
    assert overlay
    assert not [key for key in overlay if ":d:" in key]
    flow.cell.clock.advance(minutes=6)
    await flow.signals.derive_due(SPACE_ID)
    flow.cell.clock.advance(days=1)
    await flow.signals.nightly(SPACE_ID)


@pytest.fixture
def flow(cell: Cell) -> Flow:
    cell.config.put(configured())
    return Flow.build(cell)


async def test_ten_people_whose_search_came_back_empty_make_a_row_and_nobody_is_named(flow: Flow) -> None:
    day = flow.cell.clock.now().astimezone(UTC).date()
    await a_day_of_searches(flow, 10)
    [found] = await flow.signals.unmet_demand(SPACE_ID, day, day)
    assert (found.tool, found.calls, found.empty, found.people) == ("search_items", 10, 10, 10)
    assert found.asked == [
        {"attr": "item_variant.color", "op": "in", "values": ["vermelho"]},
        {"attr": "item_variant.price_sale", "op": "lte", "values": [400]},
    ]
    stored = flow.cell.objects.objects[demand_key(SPACE_ID, day)]
    version, sealed = unpack_envelope(stored)
    plain = await flow.cell.keys.decrypt(SPACE_ID, sealed, object_aad(demand_key(SPACE_ID, day)), version)
    rows = from_parquet(plain)
    assert len(rows) == 10
    assert all(len(r["pseudonym"]) == 32 for r in rows)
    # The failed call is not demand; nothing of it reached the rows.
    assert {r["tool"] for r in rows} == {"search_items"}

    # One subject erased: their day leaves the demand, and the week falls below ten people.
    await flow.privacy.forget(
        SPACE_ID,
        ForgetRequest(target="handle", handle=Handle(type=HandleType.PHONE_E164, value="+5511955550003")),
        requested_by="dpo",
        idempotency_key="f-1",
    )
    await flow.drain()
    assert await flow.signals.unmet_demand(SPACE_ID, day, day) == []


async def test_nine_people_or_results_that_were_enough_make_no_row(flow: Flow) -> None:
    day = flow.cell.clock.now().astimezone(UTC).date()
    await a_day_of_searches(flow, 9)
    assert await flow.signals.unmet_demand(SPACE_ID, day, day) == []


async def test_enough_results_are_no_demand(flow: Flow) -> None:
    day = flow.cell.clock.now().astimezone(UTC).date()
    await a_day_of_searches(flow, 10, results=5)
    assert await flow.signals.unmet_demand(SPACE_ID, day, day) == []


async def test_the_analyst_reads_it_by_week_over_http(flow: Flow) -> None:
    from dataclasses import dataclass

    import httpx

    from niadra.adapters.inbound.http.app import create_app
    from niadra.adapters.inbound.http.signals import admin_router
    from niadra.adapters.memory.fakes import StaticConfig
    from niadra.app.signals import SignalsService
    from tests.unit.measure.test_experiment_reports import FakeEdge

    @dataclass
    class Container:
        edge: FakeEdge
        config: StaticConfig
        signals: SignalsService

    day = flow.cell.clock.now().astimezone(UTC).date()
    await a_day_of_searches(flow, 10)
    app = create_app("niadra-test", Container(FakeEdge(), flow.cell.config, flow.signals), (admin_router,))
    analyst = {"authorization": "Bearer analyst"}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://cell") as http:
        page = await http.get(f"/v1/measure/unmet-demand?since={day}&until={day}", headers=analyst)
        other = await http.get(
            f"/v1/measure/unmet-demand?since={day}&until={day}&tool=quote_plan", headers=analyst
        )
        backwards = await http.get(f"/v1/measure/unmet-demand?since={day}&until=2026-01-01", headers=analyst)
    assert page.status_code == 200, page.text
    body = page.json()
    assert (body["min_people"], body["next_cursor"]) == (10, None)
    [row] = body["items"]
    assert (row["tool"], row["people"], row["empty"]) == ("search_items", 10, 10)
    assert "pseudonym" not in page.text
    assert other.json()["items"] == []
    assert backwards.status_code == 422
