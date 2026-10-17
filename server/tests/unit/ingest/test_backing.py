"""The backing report on an agent's turn (13, 6.2): kept with the turn as kinds, counts and short ids, and
a guard the SDK found the turn going against sends `guard.violated` when the turn arrives (13, 6.1)."""

import json
from typing import Any

import pytest
from pydantic import ValidationError

from niadra.config.models import WebhookEndpoint
from niadra.contracts.events import EventItem
from niadra.testing.cell import SPACE_ID, WHATSAPP_SOURCE, Cell, principal
from tests.unit.ingest.fakes import build, message
from tests.unit.ingest.test_service import send, stored

REPORT = {"checked": 2, "unbacked_values": [{"kind": "amount"}], "guard_violations": ["000000d1"]}


def agent_turn(key: str, backing: dict[str, Any] | None = REPORT, **overrides: Any) -> dict[str, Any]:
    fields: dict[str, Any] = {"speaker": {"role": "ai_agent"}, **overrides}
    item = message(key, "A parcela é R$ 259,90 e a visita é dia 19/09.", **fields)
    if backing is not None:
        item["backing"] = backing
    return item


def subscribe(cell: Cell) -> None:
    config = cell.config.space(SPACE_ID)
    endpoint = WebhookEndpoint(
        endpoint_id="supervisor",
        url="https://supervisor.example/hooks",
        source_id=WHATSAPP_SOURCE,
        event_types=["guard.violated"],
        secret_refs=["spaces/agent/webhook/supervisor"],
    )
    cell.config.put(config.model_copy(update={"webhook_endpoints": [endpoint]}))


def delivered(cell: Cell) -> list[dict[str, Any]]:
    rows = cell.uow.store.module("notify").get("deliveries", {}).values()
    return [d.body for d in rows if d.event_type == "guard.violated"]


def test_backing_rides_only_on_an_agents_message_and_names_no_value() -> None:
    EventItem.model_validate(agent_turn("a1"))
    EventItem.model_validate(agent_turn("a2", speaker={"role": "human_agent"}))
    with pytest.raises(ValidationError, match="only valid on a message of an agent"):
        EventItem.model_validate(message("c1", backing=REPORT))
    with pytest.raises(ValidationError, match="cannot outnumber"):
        EventItem.model_validate(agent_turn("a3", {"checked": 0, "unbacked_values": [{"kind": "date"}]}))
    with pytest.raises(ValidationError):
        EventItem.model_validate(
            agent_turn("a4", {"checked": 1, "unbacked_values": [{"kind": "date", "value": "19/09"}]})
        )
    with pytest.raises(ValidationError):
        EventItem.model_validate(agent_turn("a5", {"checked": 1, "guard_violations": ["19/09/2026"]}))
    assert EventItem.model_validate(agent_turn("a6", None)).backing is None


async def test_the_report_is_kept_with_the_turn_and_a_violation_leaves_at_once(cell: Cell) -> None:
    subscribe(cell)
    ingest, _, _ = build(cell)
    response = await send(ingest, principal(), agent_turn("t1"))
    assert response.accepted == 1
    [event] = stored(cell)
    assert event.record.content_meta["backing"] == REPORT
    [body] = delivered(cell)
    data = body["data"]
    assert data["guards"] == ["000000d1"]
    assert (data["event_id"], data["detected_by"], data["speaker"]) == (
        str(event.record.event_id),
        "sdk",
        "ai_agent",
    )
    assert data["conversation_id"] == "conv-1"
    # The values of the report never leave; the ids and the send time vary, so they are left out of the look.
    shown = json.dumps({**body, "id": None, "created_at": None, "data": {**data, "event_id": None}})
    assert "259" not in shown
    assert "19/09" not in shown


async def test_a_report_without_violations_sends_nothing(cell: Cell) -> None:
    subscribe(cell)
    ingest, _, _ = build(cell)
    await send(ingest, principal(), agent_turn("t1", {"checked": 2, "unbacked_values": []}))
    await send(ingest, principal(), agent_turn("t2", None))
    assert delivered(cell) == []
    # A redelivered turn is a duplicate: it never sends again.
    await send(ingest, principal(), agent_turn("t3"))
    await send(ingest, principal(), agent_turn("t3"))
    assert len(delivered(cell)) == 1
