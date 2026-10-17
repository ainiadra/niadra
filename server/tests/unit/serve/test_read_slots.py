"""The per-turn slots on the read path (11, 3.3): chat and voice pin a skeleton for the whole conversation,
and each read with `query` gets per-turn slots in the suffix, with a receipt of what was chosen and why."""

import asyncio
import json
from collections.abc import Sequence
from dataclasses import replace
from datetime import timedelta
from uuid import UUID

import pytest
from pydantic import ValidationError

from niadra.app.compile import open_pack, seal_pack
from niadra.app.retrieval import RetrievalCache, RetrievalService
from niadra.contracts.context import ContextRequest, ContextResponse, PrefetchRequest
from niadra.domain.compile import COMPILER_VERSION
from niadra.domain.compile.model import MemorySnapshot
from niadra.domain.compile.templates import Formatter
from niadra.domain.memory.values import SettledValue, ValueType
from niadra.domain.records import ProfileRecord
from niadra.domain.serve.cache_keys import pin_key
from niadra.domain.serve.slots import ChannelHits, SlotItem
from niadra.domain.serve.turn import TurnAnalysis
from niadra.domain.vocabulary import DeliveryPath, ReceiptSurface, Speaker, Verification
from niadra.testing.cell import SPACE_ID, WHATSAPP_SOURCE, Cell, principal, sample_config
from tests.unit.compile.factories import PHONE, PROFILE, T0, episode, marina, uid
from tests.unit.serve.harness import SUBJECT, Harness, default_world

AGENT = principal()


def history() -> MemorySnapshot:
    return replace(
        marina(),
        episodes=(
            episode(1, "technical_visit", days_ago=0.2, summary="A internet caiu de novo ontem à noite"),
            episode(2, "technical_visit", days_ago=20, summary="O técnico registrou a visita como 88280"),
            episode(3, "technical_visit", days_ago=60, summary="Internet caindo outra vez à noite"),
            episode(4, "billing", days_ago=190, summary="Segunda via da fatura 55121"),
        ),
    )


def harness(cell: Cell, **extra: object) -> Harness:
    cell.config.put(sample_config())
    world = default_world()
    world.snapshots = [history()]
    return Harness(cell, world, **extra)  # type: ignore[arg-type]


def ask(**extra: object) -> ContextRequest:
    defaults = {"subject": SUBJECT, "verification": Verification.V1, "conversation_id": "wa-now"}
    return ContextRequest(**(defaults | extra))  # type: ignore[arg-type]


async def test_the_body_stays_pinned_and_each_turn_gets_its_own_slots(cell: Cell) -> None:
    h = harness(cell)
    await h.compile()
    first = await h.serve.context(AGENT, ask(query="Qual foi o número do registro da visita?"))
    second = await h.serve.context(AGENT, ask(query="Quantas vezes eu já reclamei da internet caindo?"))
    assert second.text == first.text
    assert second.etag == first.etag
    assert second.path is DeliveryPath.T0
    assert first.slots is not None
    assert "88280" in first.slots
    assert "88280" not in (first.text or "")
    assert second.slots is not None
    # Counted by the matter asked: the visit registered as 88280 is not a complaint of the internet dropping.
    assert "reclamações sobre este assunto nos últimos 90 dias: 2" in second.slots
    # The latest conversation is pinned; the older ones wait for a turn that asks for them.
    assert "A internet caiu de novo" in (first.text or "")
    assert "conversas desde" not in (first.text or "")  # the history index is a slot's
    assert "slots" in first.timing


async def test_a_read_without_query_is_the_pinned_skeleton_alone(cell: Cell) -> None:
    h = harness(cell)
    await h.compile()
    plain = await h.serve.context(AGENT, ask())
    asked = await h.serve.context(AGENT, ask(query="Qual foi o número do registro da visita?"))
    assert plain.slots is None
    assert plain.text == asked.text


async def test_the_slots_receipt_says_what_was_chosen_and_why_never_the_words(cell: Cell) -> None:
    h = harness(cell)
    await h.compile()
    await h.serve.context(AGENT, ask(query="Qual foi o número do registro da visita?"))
    pack, slots = h.receipts.sent[-2:]
    assert pack.surface is ReceiptSurface.PACK
    assert slots.surface is ReceiptSurface.SLOTS
    assert slots.item_ids[0] == str(uid(0x202))
    assert slots.query_hash is not None
    manifest = json.loads(slots.detail["slots"])
    assert manifest["items"][0]["id"] == f"episode:{uid(0x202)}"
    assert "exact" in manifest["items"][0]["channels"]
    analysis = json.loads(slots.detail["analysis"])
    assert analysis["asked_types"] == ["record"]
    assert "88280" not in str(slots.detail)
    assert "visita" not in str(slots.detail)


async def test_json_reads_get_the_slots_typed(cell: Cell) -> None:
    h = harness(cell)
    await h.compile()
    response = await h.serve.context(AGENT, ask(query="Quantas vezes reclamei da internet?", format="json"))
    assert response.pack is not None
    first = response.pack.slots[0]
    assert (first.section, first.derived) == ("derived", "count")


async def test_withheld_conversations_never_reach_the_slots(cell: Cell) -> None:
    h = harness(cell)
    await h.compile()
    response = await h.serve.context(AGENT, ask(query="E a fatura 55121?"))
    assert "55121" not in (response.slots or "")
    assert "55121" not in (response.text or "")


def value(n: int, raw: str, *, episode_n: int, channel: str = "email", days: float = 5) -> SettledValue:
    return SettledValue(
        value_id=uid(0xA00 + n),
        origin_handle_id=PHONE,
        type=ValueType.PROTOCOL,
        raw=raw,
        said_at=T0 - timedelta(days=days),
        speaker=Speaker.AI_AGENT,
        session_id=uid(0x300 + episode_n),
        channel=channel,
        source_id=WHATSAPP_SOURCE,
        created_at=T0 - timedelta(days=days),
        episode_id=uid(0x200 + episode_n),
    )


async def test_settled_values_answer_by_type_and_only_from_visible_conversations(cell: Cell) -> None:
    h = harness(cell)
    h.world.values = [
        value(1, "81220", episode_n=2),
        value(2, "99001", episode_n=4, days=190),  # said in the billing conversation, withheld at V1
    ]
    await h.compile()
    response = await h.serve.context(AGENT, ask(query="Qual o protocolo que me passaram por e-mail?"))
    assert response.slots is not None
    # The line says what the value is about: the first words of its conversation.
    assert "[Valor] protocolo 81220 · O técnico registrou a visita como 88280 · email" in response.slots
    assert "99001" not in response.slots


class Slow:
    name = "semantic"
    deadline_s = 0.01

    async def hits(
        self,
        space_id: UUID,
        profile: ProfileRecord,
        turn: TurnAnalysis,
        pool: Sequence[SlotItem],
        *,
        query: str,
        fmt: Formatter,
        voice: bool,
    ) -> ChannelHits:
        await asyncio.sleep(1)
        return ChannelHits(self.name)


class Broken(Slow):
    name = "broken"

    async def hits(self, *args: object, **kwargs: object) -> ChannelHits:  # type: ignore[override]
        raise RuntimeError("down")


async def test_a_channel_past_its_deadline_or_failing_is_skipped_and_the_read_goes_on(cell: Cell) -> None:
    h = harness(cell, slot_sources=[Slow(), Broken()])
    await h.compile()
    response = await h.serve.context(AGENT, ask(query="Qual foi o número do registro da visita?"))
    assert "88280" in (response.slots or "")
    manifest = json.loads(h.receipts.sent[-1].detail["slots"])
    assert manifest["channels"]["semantic"] == {"hits": 0, "skipped": True}
    assert manifest["channels"]["broken"] == {"hits": 0, "skipped": True}


async def test_the_voice_view_gets_sentences_within_its_budget(cell: Cell) -> None:
    h = harness(cell)
    await h.compile()
    response = await h.serve.context(
        AGENT, ask(view="voice", query="Qual foi o número do registro da visita?")
    )
    assert response.slots is not None
    lines = response.slots.splitlines()[1:-1]
    assert lines
    assert all(line.endswith(".") for line in lines)


async def test_after_the_first_turn_the_values_come_from_the_retrieval_cache(cell: Cell) -> None:
    h = harness(cell)
    h.world.values = [value(1, "81220", episode_n=2)]
    await h.compile()
    await h.serve.context(AGENT, ask(query="Oi"))
    await h.serve.retrieval.drain()
    h.world.values = []  # the database no longer answers: only the cache can
    response = await h.serve.context(AGENT, ask(query="Qual o protocolo que me passaram por e-mail?"))
    assert "[Valor] protocolo 81220 · O técnico registrou a visita" in (response.slots or "")


class Encoder:
    async def embed_query(self, text: str) -> tuple[list[float], str]:
        return [1.0, 0.0], "test-encoder"


async def test_the_semantic_channel_runs_in_chat_and_only_when_the_rules_found_nothing_in_voice(
    cell: Cell,
) -> None:
    h = harness(cell)
    h.serve.retrieval = RetrievalService(h.uow, cell.config, cell.clock, RetrievalCache(1 << 20), Encoder())
    await h.compile()
    await h.serve.context(AGENT, ask(query="Qual foi o número do registro da visita?"))
    manifest = json.loads(h.receipts.sent[-1].detail["slots"])
    assert manifest["channels"]["semantic"]["hits"] == 0  # no stored vectors: nothing to rank by meaning
    # In voice, a turn the rules already answered never pays for an encoding.
    await h.serve.context(AGENT, ask(view="voice", query="Qual foi o número do registro da visita?"))
    manifest = json.loads(h.receipts.sent[-1].detail["slots"])
    assert "semantic" not in manifest["channels"]


# Why each slot was chosen (12, WP-E).

QUESTION = "Qual foi o número do registro da visita?"


def _without_why(response: ContextResponse) -> dict[str, object]:
    doc = response.model_dump(exclude={"timing"})
    for slot in (doc.get("pack") or {}).get("slots", []):
        slot.pop("why", None)
    return doc


def untimed(detail: dict[str, str]) -> dict[str, str]:
    return {k: v for k, v in detail.items() if k != "server_ms"}


async def test_explain_says_why_and_changes_no_other_byte_nor_the_receipt(cell: Cell) -> None:
    h = harness(cell)
    await h.compile()
    plain = await h.serve.context(AGENT, ask(query=QUESTION, format="json"))
    # Each read's receipt keeps its own server time; everything else is the same.
    plain_receipts = [(r.surface, r.item_ids, untimed(r.detail), r.pack_hash) for r in h.receipts.sent[-2:]]
    explained = await h.serve.context(AGENT, ask(query=QUESTION, format="json", explain=True))
    receipts = [(r.surface, r.item_ids, untimed(r.detail), r.pack_hash) for r in h.receipts.sent[-2:]]
    assert _without_why(explained) == _without_why(plain)
    assert receipts == plain_receipts
    assert plain.pack is not None
    assert explained.pack is not None
    assert all(s.why is None for s in plain.pack.slots)
    slot = explained.pack.slots[0]
    assert slot.why is not None
    assert slot.why.item_id == str(uid(0x202))
    assert slot.why.channels[0].channel == "exact"
    assert slot.why.score == round(sum(c.contribution for c in slot.why.channels), 6)
    # The typed answer and the receipt say the same numbers.
    why = json.loads(receipts[1][2]["why"])
    assert why["items"][0]["id"] == f"episode:{uid(0x202)}"
    assert why["items"][0]["score"] == slot.why.score
    assert {name: c["position"] for name, c in why["items"][0]["channels"].items()} == {
        c.channel: c.position for c in slot.why.channels
    }


async def test_the_receipts_why_never_carries_a_line_a_value_or_the_question(cell: Cell) -> None:
    h = harness(cell)
    await h.compile()
    await h.serve.context(AGENT, ask(query=QUESTION))
    await h.serve.context(AGENT, ask(query="Quantas vezes reclamei da internet?"))
    for receipt in [r for r in h.receipts.sent if r.surface is ReceiptSurface.SLOTS]:
        why = receipt.detail["why"]
        for word in ("88280", "visita", "internet", "registro", "Marina", "reclam"):
            assert word not in why
    count = json.loads(h.receipts.sent[-1].detail["why"])
    assert count["derived"][0]["rule"] == "count_topic_complaints"


def test_explain_answers_in_the_typed_pack_only() -> None:
    with pytest.raises(ValidationError, match="explain"):
        ask(query=QUESTION, explain=True)
    assert ask(query=QUESTION, explain=True, format="json").explain


# Guard lines (13, 6.1).


def visit_date(raw: str, speaker: Speaker, *, n: int, days: float, episode_n: int = 2) -> SettledValue:
    return replace(
        value(n, raw, episode_n=episode_n, days=days),
        type=ValueType.DATE,
        speaker=speaker,
        channel="whatsapp",
    )


async def contradicted(cell: Cell, *types: str) -> None:
    """What measurement leaves behind: contradictions of these types in the last days."""
    from niadra.adapters.memory.measure import InMemoryMeasureRepository
    from niadra.domain.measure.slots import GuardCounters, GuardDailyKey

    repo = InMemoryMeasureRepository(cell.uow.store)
    for value_type in types:
        key = GuardDailyKey(cell.clock.now().date(), WHATSAPP_SOURCE, value_type, "value")
        await repo.add_guard_daily(key, GuardCounters(slots=3, contradicted=1))


def guarded_harness(cell: Cell, arm: str, monkeypatch: pytest.MonkeyPatch) -> Harness:
    import niadra.app.serve as serve_module

    monkeypatch.setattr(serve_module, "guard_arm", lambda space_id, profile_id: arm)
    h = harness(cell)
    h.world.values = [
        visit_date("18/09/2026", Speaker.SYSTEM, n=1, days=9),
        visit_date("19/09/2026", Speaker.CUSTOMER, n=2, days=1),
        visit_date("30/09/2026", Speaker.SYSTEM, n=3, days=1, episode_n=4),  # withheld at V1
    ]
    return h


async def test_a_measured_type_gets_a_guard_line_first_in_the_slots(
    cell: Cell, monkeypatch: pytest.MonkeyPatch
) -> None:
    h = guarded_harness(cell, "guards", monkeypatch)
    await contradicted(cell, "date")
    await h.compile()
    response = await h.serve.context(AGENT, ask(query="Quando vem o técnico?", format="json"))
    assert response.slots is not None
    guard_line = response.slots.splitlines()[1]
    assert guard_line.startswith("[Guarda] data 18/09/2026, sistema")
    assert "30/09" not in response.slots  # said in a conversation this reader may not see
    [guard] = response.guards
    assert (guard.value_type, guard.value) == ("date", "18/09/2026")
    assert response.pack is not None
    [slot] = [s for s in response.pack.slots if s.section == "guard"]
    assert (slot.id, slot.text, slot.derived) == (guard.id, guard_line, None)
    receipt = h.receipts.sent[-1]
    assert receipt.surface is ReceiptSurface.SLOTS
    assert json.loads(receipt.detail["slots"])["guards"][0]["arm"] == "guards"
    assert "18/09" not in str(receipt.detail)


async def test_no_guard_for_a_type_never_contradicted(cell: Cell, monkeypatch: pytest.MonkeyPatch) -> None:
    h = guarded_harness(cell, "guards", monkeypatch)
    await contradicted(cell, "amount")
    await h.compile()
    response = await h.serve.context(AGENT, ask(query="Quando vem o técnico?"))
    assert response.guards == []
    assert "[Guarda]" not in (response.slots or "")


async def test_the_control_group_reads_the_bytes_it_would_without_guards_and_the_guard_is_recorded(
    cell: Cell, monkeypatch: pytest.MonkeyPatch
) -> None:
    h = guarded_harness(cell, "control", monkeypatch)
    await h.compile()
    before = await h.serve.context(AGENT, ask(query="Quando vem o técnico?"))
    await contradicted(cell, "date")
    h.serve._space.clear()
    after = await h.serve.context(AGENT, ask(query="Quando vem o técnico?"))
    assert after.slots == before.slots
    assert after.guards == []
    manifest = json.loads(h.receipts.sent[-1].detail["slots"])
    assert manifest["guards"][0]["arm"] == "control"


async def test_a_closed_form_turn_gets_the_pinned_skeleton_alone_and_its_receipt_says_intent_none(
    cell: Cell,
) -> None:
    h = harness(cell)
    await h.compile()
    asked = await h.serve.context(AGENT, ask(query="Qual foi o número do registro da visita?"))
    thanks = await h.serve.context(AGENT, ask(query="Ok, obrigado!", format="json"))
    assert thanks.slots is None
    assert thanks.guards == []
    assert thanks.text == asked.text  # the pinned bytes of the conversation, unchanged
    assert thanks.pack is not None
    assert thanks.pack.slots == []
    receipt = h.receipts.sent[-1]
    assert receipt.surface is ReceiptSurface.SLOTS
    assert receipt.item_ids == ()
    assert receipt.detail["intent"] == "none"
    assert json.loads(receipt.detail["slots"])["items"] == []
    assert json.loads(receipt.detail["analysis"]) == {"intent": "none", "forms": ["confirmation", "thanks"]}
    assert "obrigado" not in str(receipt.detail).lower()


async def test_a_short_turn_that_needs_memory_is_never_gated(cell: Cell) -> None:
    h = harness(cell)
    await h.compile()
    before = len(h.receipts.sent)
    await h.serve.context(AGENT, ask(query="E a outra?"))
    assert all(r.detail.get("intent") != "none" for r in h.receipts.sent[before:])


async def test_a_closed_form_turn_is_not_prefetched(cell: Cell) -> None:
    h = harness(cell)
    await h.compile()
    request = PrefetchRequest(subject=SUBJECT, verification=Verification.V1, conversation_id="v", query="Oi")
    assert h.serve.prefetch(AGENT, request) is False
    assert len(h.serve.prefetches) == 0


async def test_a_pin_of_an_older_compiler_is_compiled_again_once(cell: Cell) -> None:
    """A conversation pinned under another compiler's label: the next read never serves it, the conversation
    gets today's skeleton with its slots, and that one is pinned from then on."""
    h = harness(cell)
    await h.compile()
    first = await h.serve.context(AGENT, ask())
    key = pin_key(h.epoch.value, SPACE_ID, PROFILE, "wa-now", "chat", WHATSAPP_SOURCE)
    pinned = await open_pack(cell.keys, SPACE_ID, key, cell.cache.values[key])
    old = replace(pinned, compiler_version="compiler-1.6", text="<old/>")
    cell.cache.values[key] = await seal_pack(cell.keys, SPACE_ID, key, old)
    again = await h.serve.context(AGENT, ask(query="Qual foi o número do registro da visita?"))
    assert again.text == first.text != "<old/>"
    assert again.slots is not None
    assert "88280" in again.slots
    repinned = await open_pack(cell.keys, SPACE_ID, key, cell.cache.values[key])
    assert (repinned.variant, repinned.compiler_version) == (pinned.variant, COMPILER_VERSION)
