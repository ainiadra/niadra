"""Zero touch, outcomes: a type whose lifecycle names final states is measured with no
`measurement` declaration; a declaration for the type wins."""

from datetime import timedelta

from niadra.app.outcomes import outcome_definitions
from niadra.config.measurement import MeasurementSettings, OutcomeDefinition
from niadra.config.object_types import ObjectTypeRegistry
from niadra.domain.tasks import TaskName
from niadra.testing.cell import BILLING_SOURCE, WHATSAPP_SOURCE, Cell
from tests.unit.flow.harness import Flow
from tests.unit.measure.test_outcomes_flow import LINES, ORDER_TYPE, PHONE, PURCHASE, configured, links, order

NOTE_TYPE = {
    "type": "note",
    "ownership": "subject",
    "mirror_of": {"system": "crm"},
    "fields": {"text": {"type": "string"}},
    "sources": {"crm": {"kind": "push", "precedence": 1}},
}


def undeclared() -> object:
    return configured().model_copy(
        update={
            "object_types": ObjectTypeRegistry.model_validate({"types": [ORDER_TYPE, NOTE_TYPE]}),
            "measurement": MeasurementSettings(),
        }
    )


def test_a_type_with_final_states_is_an_outcome_without_a_declaration() -> None:
    [inferred] = outcome_definitions(undeclared())  # type: ignore[arg-type]
    assert inferred == OutcomeDefinition(
        name="order", object_type="order", success_states=["kept", "returned", "cancelled"]
    )
    # A type with no final state (the note) is not an outcome.
    declared = configured()
    assert [d.name for d in outcome_definitions(declared)] == [PURCHASE["name"]]


async def test_an_order_reaching_a_final_state_is_attributed_with_no_measurement_document(cell: Cell) -> None:
    """The agent that acted on the order before it reached a final state gets the outcome, by the `order`
    method's last action, as a declared definition would give it."""
    cell.config.put(undeclared())  # type: ignore[arg-type]
    flow = Flow.build(cell)
    await flow.send(WHATSAPP_SOURCE, order(flow, "delivered", "o-delivered", LINES))
    await flow.drain()
    await flow.send(BILLING_SOURCE, acted_on_the_order(flow))
    await flow.drain()
    flow.cell.clock.advance(hours=2)
    await flow.send(WHATSAPP_SOURCE, order(flow, "kept", "o-kept", LINES))
    ran = await flow.drain()
    assert TaskName.ATTRIBUTE_OUTCOMES in [t.name for t in ran]
    [link] = (await links(flow)).values()
    assert (link.outcome, link.state, link.finality, link.method) == ("order", "kept", "final", "order")


def acted_on_the_order(flow: Flow) -> dict[str, object]:
    return {
        "type": "event",
        "kind": "action",
        "idempotency_key": "agent-acted-8812",
        "channel": "erp",
        "handles": [{"type": "phone_e164", "value": PHONE}],
        "object_refs": [{"type": "order", "namespace": "shop", "id": "8812"}],
        "speaker": {"role": "ai_agent", "id": "service-agent"},
        "action": {"operation": "credit", "result": "Crédito do frete combinado", "purpose": "billing"},
        "occurred_at": (flow.cell.clock.now() + timedelta(hours=1, minutes=30)).isoformat(),
    }
