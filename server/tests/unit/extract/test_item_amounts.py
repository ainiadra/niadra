"""An item keeps the amount its own turn states (benchmark promise_action, 09/10/2026): "Vou solicitar o
estorno de R$ 112,64 da tarifa" written as "Solicitar estorno da tarifa" never closed when the system refunded
112.64, since a promise closes by an amount its text states, and its line no longer said how much."""

import json
from typing import Any

from niadra.domain.memory.items import amounts_in
from niadra.domain.vocabulary import Speaker
from tests.unit.extract.helpers import event, material, prompt_for, rules, validate_one

CALL = [
    event(Speaker.CUSTOMER, "O aplicativo bloqueou minha transferência, e isso me deu prejuízo.", 1),
    event(Speaker.AI_AGENT, "Peço desculpas. Vou solicitar o estorno de R$ 112,64 da tarifa.", 2),
    event(Speaker.CUSTOMER, "Obrigado, fico no aguardo.", 3),
]
BOTH = [
    event(Speaker.CUSTOMER, "A caixa chegou amassada, e isso me deu prejuízo.", 1),
    event(Speaker.AI_AGENT, "Vou solicitar a nova entrega sem custo e o abatimento de R$ 211,80.", 2),
]


def answer(*descriptions: str) -> str:
    return json.dumps(
        {
            "episode": {
                "summary": "Transferência bloqueada; o agente vai solicitar o estorno da tarifa.",
                "intent": "complaint",
                "category": "billing",
                "outcome": "pending",
                "resolution": None,
                "sentiment": -0.4,
                "matter": "transfer_blocked",
                "matter_label": "transferência bloqueada",
            },
            "open_items": [item(d) for d in descriptions],
        }
    )


def item(description: str) -> dict[str, Any]:
    return {
        "description": description,
        "owner": "company",
        "due_at": None,
        "evidence": ["e2"],
        "object": None,
        "expected_operation": "refund_fee",
        "about": None,
    }


def test_an_item_written_without_its_amount_keeps_the_one_its_turn_states() -> None:
    m = material(CALL)
    [kept] = validate_one(answer("Solicitar estorno da tarifa."), prompt_for(m), m, rules()).open_items
    assert kept.description == "Solicitar estorno da tarifa (R$ 112,64)"
    assert {str(a) for a in amounts_in(kept.description)} == {"112.64"}


def test_an_item_that_states_its_amount_is_left_as_written() -> None:
    m = material(CALL)
    written = "Solicitar estorno de R$ 112,64 da tarifa"
    [kept] = validate_one(answer(written), prompt_for(m), m, rules()).open_items
    assert kept.description == written


def test_two_items_of_one_turn_never_take_its_amount_from_it() -> None:
    """Whose amount it is the turn tells the model only: the new delivery is not R$ 211,80."""
    m = material(BOTH)
    found = validate_one(
        answer("Solicitar nova entrega sem custo", "Solicitar abatimento"), prompt_for(m), m, rules()
    ).open_items
    assert sorted(i.description for i in found) == [
        "Solicitar abatimento",
        "Solicitar nova entrega sem custo",
    ]


def test_a_number_without_its_currency_is_no_amount_to_keep() -> None:
    """A case number holds a bare "8.26": the client's "mando os extratos" stays as written."""
    said = [event(Speaker.CUSTOMER, "Sobre o processo 1021234-23.2026.8.26.0224: mando os extratos.", 1)]
    m = material(said)
    payload = json.loads(answer("Cliente vai enviar os extratos"))
    payload["open_items"][0] |= {"evidence": ["e1"], "owner": "customer", "expected_operation": None}
    [kept] = validate_one(json.dumps(payload), prompt_for(m), m, rules()).open_items
    assert kept.description == "Cliente vai enviar os extratos"
