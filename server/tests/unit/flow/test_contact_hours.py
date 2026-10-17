"""The hours a customer says they may be contacted in, end to end, with nothing configured (zero touch): the
extraction finds "não me liguem antes das 9h", the coordination holds a call at 07:00 until 09:00 and lets one
at 14:00 go, on their phone and for every purpose, and the person's block says it in words. juridico-zero,
09/10/2026: the preference was kept as a fact's words and held nothing."""

from datetime import UTC, datetime, timedelta
from typing import Any

from niadra.adapters.memory.fakes import ScriptedLlm
from niadra.app.serve import BlockAsk
from niadra.config.models import CategoryRule, FeatureSettings, PredicateSpec
from niadra.contracts.common import Handle
from niadra.contracts.context import ContextRequest
from niadra.contracts.coordinate import CheckRequest
from niadra.domain.vocabulary import Feature, Verification
from niadra.testing.cell import SPACE_ID, VOICE_SOURCE, WHATSAPP_SOURCE, Cell, principal
from tests.unit.flow.harness import Flow

CUSTOMER = {"type": "phone_e164", "value": "+5511955550007"}
SAID = "Bom dia. Por favor, não me liguem antes das 9h, eu trabalho de madrugada."
EPISODE = {
    "summary": "Cliente pediu para não ligarem antes das 9h",
    "intent": "other",
    "category": "contract",
    "outcome": "resolved",
    "resolution": None,
    "sentiment": None,
}


def _flow(*answers: dict[str, Any]) -> Flow:
    cell = Cell()
    cell.clock.at = datetime(2026, 10, 9, 18, 0, tzinfo=UTC)  # 15:00 in Sao Paulo
    config = cell.config.space(SPACE_ID)
    # The starter's predicate a space nobody configured has (zero touch): where a contact time is kept.
    extraction = config.extraction.model_copy(
        update={
            "categories": [*config.extraction.categories, CategoryRule(name="preferences")],
            "predicates": [
                *config.extraction.predicates,
                PredicateSpec(name="preferred_contact_time", category="preferences", single_valued=True),
            ],
        }
    )
    policy = config.policy.model_copy(
        update={
            "categories": [
                *config.policy.categories,
                CategoryRule(name="preferences", min_verification=Verification.V1, mirror=True),
            ]
        }
    )
    config = config.model_copy(
        update={
            "features": FeatureSettings(enabled=frozenset({Feature.COORDINATION, Feature.SIGNALS})),
            "extraction": extraction,
            "policy": policy,
        }
    )
    cell.config.put(config)
    return Flow.build(cell, ScriptedLlm(*answers))


async def _say(flow: Flow, conversation: str, text: str) -> None:
    clock = flow.cell.clock
    clock.advance(minutes=1)
    turn = {
        "type": "event",
        "idempotency_key": f"{conversation}-1",
        "channel": "whatsapp",
        "conversation_id": conversation,
        "handles": [CUSTOMER],
        "speaker": {"role": "customer"},
        "content": {"type": "text", "text": text},
        "occurred_at": clock.now().isoformat(),
    }
    clock.advance(minutes=1)
    ended = {
        "type": "conversation.ended",
        "idempotency_key": f"{conversation}-end",
        "conversation_id": conversation,
        "occurred_at": clock.now().isoformat(),
    }
    assert (await flow.send(WHATSAPP_SOURCE, turn, ended)).errors == []
    await flow.drain()


async def _call(flow: Flow, at: datetime) -> Any:
    flow.cell.clock.at = at
    body = {
        "subject": CUSTOMER,
        "agent": "agent:collection",
        "intent": "reminder",
        "direction": "outbound",
        "purpose": "service",
        "channel": "voice",
    }
    return await flow.coordinate.check(principal(VOICE_SOURCE), CheckRequest.model_validate(body))


async def test_a_customers_hours_hold_a_call_before_nine_and_their_block_says_so() -> None:
    hours = {"channel": "voice", "not_before": "09:00", "not_after": None, "days": [], "evidence": ["e1"]}
    flow = _flow({"episode": EPISODE, "hours": [hours]})
    await _say(flow, "wa-hours", SAID)

    tomorrow = datetime(2026, 10, 10, tzinfo=UTC)
    early = await _call(flow, tomorrow + timedelta(hours=10))  # 07:00 in Sao Paulo
    assert (early.decision, early.reasons) == ("defer", ["contact_window"])
    assert early.contact_window_until == tomorrow + timedelta(hours=12)  # 09:00 in Sao Paulo
    afternoon = await _call(flow, tomorrow + timedelta(hours=17))  # 14:00
    assert afternoon.decision == "allow"

    await flow.prove(WHATSAPP_SOURCE, "wa-later", CUSTOMER["value"], "V2")
    request = ContextRequest(
        subject=Handle.model_validate(CUSTOMER),
        conversation_id="wa-later",
        verification=Verification.V2,
        include=["constraints"],
    )
    response = await flow.serve.context(principal(WHATSAPP_SOURCE), request)
    assert response.constraints is not None
    [window] = response.constraints.contact_windows
    assert (window.channel, window.window.from_, window.window.to) == ("voice", "00:00", "09:00")
    # Read beside the pack, the hours are said once, in the pack's contact preferences.
    assert response.text is not None
    assert "ligação: não antes das 09:00, horário de America/Sao_Paulo" in response.text
    # On its own route, with no pack beside it, the block says them, as before.
    alone = await flow.serve.read_block(
        principal(WHATSAPP_SOURCE),
        "constraints",
        Handle.model_validate(CUSTOMER),
        BlockAsk("wa-later", None, "chat"),
    )
    assert "ligação: não antes das 09:00, horário de America/Sao_Paulo" in alone.text


async def test_a_customer_who_takes_their_hours_back_may_be_called_at_any_hour() -> None:
    said = {"channel": "voice", "not_before": "09:00", "not_after": None, "days": [], "evidence": ["e1"]}
    lifted = {"channel": "voice", "not_before": None, "not_after": None, "lift": True, "evidence": ["e1"]}
    flow = _flow({"episode": EPISODE, "hours": [said]}, {"episode": EPISODE, "hours": [lifted]})
    await _say(flow, "wa-hours", SAID)
    await _say(flow, "wa-anytime", "Pode ligar a qualquer hora agora, mudei de emprego.")
    early = await _call(flow, datetime(2026, 10, 10, 10, 0, tzinfo=UTC))
    assert "contact_window" not in early.reasons


async def _read(flow: Flow, conversation: str, include: list[str]) -> Any:
    await flow.prove(WHATSAPP_SOURCE, conversation, CUSTOMER["value"], "V2")
    request = ContextRequest(
        subject=Handle.model_validate(CUSTOMER),
        conversation_id=conversation,
        verification=Verification.V2,
        include=include,
    )
    return await flow.serve.context(principal(WHATSAPP_SOURCE), request)


WORDS = "ligação: não antes das 09:00, horário de America/Sao_Paulo"


async def test_the_default_block_says_a_customers_hours_with_no_include() -> None:
    # juridico-zero-sandbox, 10/10/2026: the window held a call at 07:00, but a plain read had no line
    # about it (p26 took the sentence out of the facts, and a person's block reads no instruction of its
    # own).
    hours = {"channel": "voice", "not_before": "09:00", "not_after": None, "days": [], "evidence": ["e1"]}
    flow = _flow({"episode": EPISODE, "hours": [hours]})
    await _say(flow, "wa-hours", SAID)
    plain = await _read(flow, "wa-plain", [])
    assert plain.text is not None
    assert WORDS in plain.text


async def test_the_customers_own_words_stay_the_fact_when_the_model_kept_them() -> None:
    hours = {"channel": "voice", "not_before": "09:00", "not_after": None, "days": [], "evidence": ["e1"]}
    fact = {
        "op": "add",
        "predicate": "preferred_contact_time",
        "value": "Não ligar antes das 9h",
        "evidence": ["e1"],
    }
    flow = _flow({"episode": EPISODE, "hours": [hours], "facts": [fact]})
    await _say(flow, "wa-hours", SAID)
    plain = await _read(flow, "wa-plain", [])
    assert plain.text is not None
    assert "Não ligar antes das 9h" in plain.text
    assert WORDS not in plain.text


async def test_both_blocks_read_together_say_the_hours_once() -> None:
    hours = {"channel": "voice", "not_before": "09:00", "not_after": None, "days": [], "evidence": ["e1"]}
    flow = _flow({"episode": EPISODE, "hours": [hours]})
    await _say(flow, "wa-hours", SAID)
    both = await _read(flow, "wa-both", ["constraints"])
    assert both.text is not None
    assert both.text.count(WORDS) == 1
    assert both.constraints is not None
    assert WORDS not in both.constraints.text
    # The entries stay for a program: the window is still listed.
    [window] = both.constraints.contact_windows
    assert window.channel == "voice"


async def test_a_lift_says_any_hour_in_the_default_block() -> None:
    said = {"channel": "voice", "not_before": "09:00", "not_after": None, "days": [], "evidence": ["e1"]}
    lifted = {"channel": "voice", "not_before": None, "not_after": None, "lift": True, "evidence": ["e1"]}
    flow = _flow({"episode": EPISODE, "hours": [said]}, {"episode": EPISODE, "hours": [lifted]})
    await _say(flow, "wa-hours", SAID)
    await _say(flow, "wa-anytime", "Pode ligar a qualquer hora agora, mudei de emprego.")
    plain = await _read(flow, "wa-plain", [])
    assert plain.text is not None
    assert "ligação: a qualquer hora" in plain.text
    assert WORDS not in plain.text
