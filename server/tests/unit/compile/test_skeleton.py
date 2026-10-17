"""Skeletons (11, 3.3): the pinned body keeps what is always small, the latest conversation and what just
happened on other channels; the rest waits in the pack's reserve for the per-turn slots."""

from dataclasses import replace
from datetime import timedelta

from hypothesis import given, settings
from hypothesis import strategies as st

from niadra.domain.compile import CompiledPack, CompileInput, MemorySnapshot, Variant, compile_pack
from niadra.domain.compile.model import Section, slot_budget
from niadra.domain.compile.tokens import estimate_tokens
from niadra.domain.compile.views import RECENT_BUDGETS, VIEWS
from niadra.domain.records import AgentAction
from niadra.domain.vocabulary import ActionStatus, Speaker, Verification
from niadra.testing.cell import BILLING_SOURCE
from tests.unit.compile.factories import T0, episode, fact, marina, open_item, uid
from tests.unit.compile.factories import settings as compile_settings
from tests.unit.compile.test_compiler import SUPPORT, pack_for, shuffled


def slotted(snapshot: MemorySnapshot | None = None, **variant: object) -> CompiledPack:
    return pack_for(snapshot, slotted=True, **variant)


def ids(pack: CompiledPack, prefix: str) -> list[str]:
    return sorted(i for i in pack.item_ids if i.startswith(prefix))


def test_a_readers_key_names_the_view_alone_and_parses_back_slotted() -> None:
    reader = Variant(view="voice", purposes=SUPPORT, slotted=True)
    assert reader.key.split("|")[0] == "voice"
    assert Variant.parse(reader.key) == reader
    trimmed = replace(reader, trim=frozenset({"episode:billing"}))
    assert Variant.parse(trimmed.key) == trimmed
    brief = Variant(view="brief", purposes=SUPPORT)
    assert Variant.parse(brief.key) == brief  # no slot budget, so no reserve


def test_the_skeleton_keeps_the_latest_conversation_and_holds_the_rest() -> None:
    whole = pack_for(verification=Verification.V2)
    skeleton = slotted(verification=Verification.V2)
    # The latest conversation, and the older one it repeats as the recurrence line.
    assert ids(skeleton, "episode:") == [f"episode:{uid(0x201)}", f"episode:{uid(0x202)}"]
    assert len(ids(whole, "episode:")) > 2
    assert ids(skeleton, "open_item:") == ids(whole, "open_item:")
    assert ids(skeleton, "action:") == ids(whole, "action:")  # 54 minutes old: within 72 hours
    assert ids(skeleton, "object:") == []  # changed two days ago: not just now
    assert "conversas desde" not in skeleton.text  # the history index is a slot's
    assert "[Resumo]" not in skeleton.text
    assert estimate_tokens(skeleton.text) < estimate_tokens(whole.text)
    held = {e["id"]: e["rule"] for e in skeleton.manifest["excluded"]}
    assert held[str(uid(0x203))] == "skeleton"
    assert held[str(uid(0x700))] == "skeleton"
    assert skeleton.manifest["slots"] == {"skeleton": True, "reserve": len(skeleton.reserve)}


def test_the_reserve_holds_what_the_body_left_out_rendered_and_rechecked() -> None:
    pack = slotted(verification=Verification.V2)
    reserve = {r.item_id: r for r in pack.reserve}
    older = reserve[f"episode:{uid(0x203)}"]
    assert older.section is Section.EPISODES
    assert older.line.startswith("[Conversa] 24/07 · whatsapp")
    assert older.channel == "whatsapp"
    assert older.refs["category"] == "technical_visit"
    assert older.tokens == estimate_tokens(older.line + "\n")
    invoice = reserve[f"object:{uid(0x700)}"]
    assert invoice.line.startswith("[Sistema] 20/09 · invoice 0823")
    # Nothing the policy withholds: the health fact never enters the reserve.
    assert f"fact:{uid(0x103)}" not in reserve
    # Only what the body pins shorter waits in the reserve too, whole (tpl-12).
    for item_id in set(reserve) & set(pack.item_ids):
        body = next(line.text for line in pack.lines if line.item_id == item_id)
        assert len(reserve[item_id].line) > len(body)
    # Every visible conversation, in the body or not, for the count by topic.
    assert {e.item_id for e in pack.episode_index} == {
        f"episode:{uid(n)}" for n in (0x201, 0x202, 0x203, 0x204)
    }
    at_v1 = slotted(verification=Verification.V1)
    assert f"episode:{uid(0x204)}" not in {e.item_id for e in at_v1.episode_index}  # billing needs V2


def test_old_actions_wait_in_the_reserve() -> None:
    old = AgentAction(
        uid(0x501),
        uid(0x961),
        BILLING_SOURCE,
        "credit",
        ActionStatus.CONFIRMED,
        T0 - timedelta(days=5),
        result="Crédito",
    )
    snapshot = replace(marina(), actions=(*marina().actions, old))
    pack = slotted(snapshot, verification=Verification.V2)
    assert ids(pack, "action:") == [f"action:{uid(0x500)}"]
    assert f"action:{uid(0x501)}" in {r.item_id for r in pack.reserve}


def test_a_skeleton_leaves_a_facts_origin_to_the_manifest() -> None:
    # tpl-12: who said a fact, where and when cost six tokens a line on every turn; the manifest keeps the
    # evidence, and the full views still print it.
    said = replace(fact(2, "plan", "fibra 500 mega", "contract"), origin_conversation_id="conv-2")
    system = replace(fact(5, "plan_speed", "500", "contract"), speaker=Speaker.SYSTEM)
    pack = slotted(replace(marina(), facts=(said, system)), verification=Verification.V2)
    lines = [line.text for line in pack.lines if line.item_id and line.item_id.startswith("fact:")]
    assert lines == ["[Fatos] plan: fibra 500 mega", "[Fatos] plan speed: 500"]
    whole = pack_for(replace(marina(), facts=(said, system)), verification=Verification.V2)
    assert "fibra 500 mega · declarado pelo cliente · whatsapp · 12/09" in whole.text


def test_a_complaint_fact_a_conversation_says_leaves_the_skeleton_and_its_reserve() -> None:
    # tpl-12: "technical issue: the customer reported a problem" restates the conversation that carries it
    # with its date and outcome; one that says more stays.
    issue = fact(6, "technical_issue", "O cliente relatou um problema", "contract")
    more = fact(7, "technical_issue", "O roteador novo esquenta muito", "contract")
    snapshot = replace(marina(), facts=(issue, more))
    pack = slotted(snapshot, verification=Verification.V2)
    assert f"fact:{issue.fact_id}" not in {*pack.item_ids, *(r.item_id for r in pack.reserve)}
    rule = {e["id"]: e["rule"] for e in pack.manifest["excluded"]}[str(issue.fact_id)]
    assert rule == "duplicate"
    assert f"fact:{more.fact_id}" in {r.item_id for r in pack.reserve}
    assert "O cliente relatou um problema ·" in pack_for(snapshot, verification=Verification.V2).text


def test_an_unverified_reader_is_told_why_items_are_withheld() -> None:
    pack = slotted(verification=Verification.V0)
    meta = next(line.text for line in pack.lines if line.section is Section.META)
    ask = "peça a verificação antes de falar deles"
    # It counts what a verification would release: the sensitive fact no level releases is not among them.
    for_level = pack.manifest["withheld"]["verification"]
    assert for_level == pack.withheld - 1
    withheld = f"{for_level} itens retidos até verificar a identidade: {ask}"
    assert meta == f"[Contexto] {withheld}"
    # A verified reader's skeleton says nothing of what it withholds (tpl-12): the turn's slots say it of a
    # kind of value asked for.
    verified = slotted(verification=Verification.V2)
    assert not any(line.section is Section.META for line in verified.lines)


def test_the_voice_skeleton_fits_its_budget() -> None:
    long = episode(9, "technical_visit", days_ago=0.05, summary="visita " * 20)
    pack = slotted(
        replace(marina(), episodes=(*marina().episodes, long)), view="voice", verification=Verification.V2
    )
    body = pack.manifest["tokens"]
    # The skeleton's budget, what just happened, and the tags around the customer block.
    assert body["b1"] + body["b2"] <= 110 + RECENT_BUDGETS["voice"] + 30
    assert pack.manifest["tokens"]["total"] + slot_budget("voice") <= VIEWS["voice"].budget


def test_a_pack_no_reader_reads_turn_by_turn_carries_no_reserve() -> None:
    pack = pack_for(verification=Verification.V2)  # a policy preview, a profile view
    assert pack.reserve == ()
    assert b"reserve" not in pack.to_bytes()
    assert "slots" not in pack.manifest


def test_a_slotted_pack_round_trips_with_its_reserve() -> None:
    pack = slotted(verification=Verification.V2)
    assert pack.reserve
    assert CompiledPack.from_bytes(pack.to_bytes()) == pack


@settings(max_examples=30, deadline=None)
@given(seed=st.integers(0, 10_000), view=st.sampled_from(["chat", "voice"]))
def test_same_memory_same_skeleton_and_reserve(seed: int, view: str) -> None:
    first = slotted(view=view, verification=Verification.V2)
    again = slotted(shuffled(marina(), seed), view=view, verification=Verification.V2)
    assert again.to_bytes() == first.to_bytes()


def test_a_task_view_keeps_its_body_and_still_has_a_reserve() -> None:
    small = compile_pack(
        CompileInput(
            marina(),
            Variant(view="brief", purposes=SUPPORT, verification=Verification.V2, slotted=True),
            compile_settings(),
        )
    )
    assert small.manifest["slots"]["skeleton"] is False


# What just happened on other channels: the first read of a call, before any customer turn.


def call_at_1407() -> MemorySnapshot:
    """The home page's story in the compiler: WhatsApp this morning about a technician who never came,
    the same failure in March settled with a credit, and the billing agent's credit 54 minutes ago."""
    today = episode(
        1,
        "technical_visit",
        days_ago=0.1,
        outcome="unresolved",
        summary="A visita técnica prometida para hoje de manhã não aconteceu",
    )
    march = episode(
        2,
        "technical_visit",
        days_ago=194,
        channel="voice",
        summary="A visita técnica não aconteceu",
        resolution="crédito de R$ 40 na fatura",
    )
    unrelated = episode(3, "technical_visit", days_ago=40, summary="Pediu troca da senha do roteador")
    return replace(marina(), episodes=(today, march, unrelated))


def lines_of(pack: CompiledPack, section: Section) -> list[str]:
    return [line.text for line in pack.lines if line.section is section]


def test_the_first_read_pins_what_just_happened_beyond_the_skeletons_own_budget() -> None:
    pack = slotted(call_at_1407(), view="voice", verification=Verification.V2)
    assert lines_of(pack, Section.EPISODES) == [
        "Conversa: 22/09 · whatsapp · A visita técnica prometida para hoje de manhã não aconteceu"
        " · não resolvido."
    ]
    assert lines_of(pack, Section.HISTORY) == [
        "Histórico: já ocorreu antes: 12/03 · voice · A visita técnica não aconteceu · resolvido"
        " · solução: crédito de R$ 40 na fatura."
    ]
    assert lines_of(pack, Section.ACTIONS) == [
        "Outro agente: Crédito de R$ 40 na fatura de agosto · Cobrança · 22/09 13:06"
        " · confirmado pelo sistema."
    ]
    # The skeleton's own sections keep their room: the customer line and the open item are still there.
    assert lines_of(pack, Section.CUSTOMER)
    assert lines_of(pack, Section.PENDING)
    assert pack.manifest["tokens"]["total"] + slot_budget("voice") <= VIEWS["voice"].budget
    # The recurrence line is the March conversation's item, entered where it is.
    entry = next(i for i in pack.manifest["items"] if i["id"] == str(uid(0x202)))
    assert (entry["kind"], entry["section"]) == ("episode", "history")
    # What the body carries whole never waits in the reserve; the unrelated conversation does.
    reserve = {r.item_id: r.line for r in pack.reserve}
    assert f"episode:{uid(0x202)}" not in reserve
    assert f"episode:{uid(0x203)}" in reserve
    assert not {i for i in pack.item_ids if i.startswith(("fact:", "open_item:", "action:"))} & set(reserve)


def test_the_recurrence_line_needs_the_same_reason_and_words_in_common() -> None:
    snapshot = call_at_1407()
    other_reason = replace(snapshot.episodes[1], normalized_category="billing", category="billing")
    for older in (other_reason, replace(snapshot.episodes[1], summary="Pediu a segunda via da conta")):
        pack = slotted(
            replace(snapshot, episodes=(snapshot.episodes[0], older)),
            view="voice",
            verification=Verification.V2,
        )
        assert lines_of(pack, Section.HISTORY) == []
    # A question is not a complaint that came back.
    asked = replace(snapshot.episodes[1], intent="request")
    pack = slotted(replace(snapshot, episodes=(snapshot.episodes[0], asked)), view="voice")
    assert lines_of(pack, Section.HISTORY) == []


def test_the_recurrence_line_never_joins_two_matters_by_the_words_of_a_report() -> None:
    # The local A/B of 27/09: a sofa in the wrong color "repeated" a broken pot lid, sharing only how each
    # was reported, and the agent gave the lid's protocol for the sofa.
    snapshot = call_at_1407()
    sofa = replace(
        snapshot.episodes[0],
        summary="A cliente informou que veio a cor errada do sofá. Atendimento registrado.",
    )
    lid = replace(
        snapshot.episodes[1],
        summary="A cliente informou que a panela chegou com a tampa quebrada. Atendimento registrado sob "
        "protocolo 73659.",
    )
    pack = slotted(replace(snapshot, episodes=(sofa, lid)), view="voice", verification=Verification.V2)
    assert lines_of(pack, Section.HISTORY) == []


def test_the_recurrence_line_follows_the_matter_the_extraction_keyed() -> None:
    snapshot = call_at_1407()
    today, march = snapshot.episodes[0], snapshot.episodes[1]
    same = (replace(today, matter="technician_no_show"), replace(march, matter="no_show_technician"))
    pack = slotted(replace(snapshot, episodes=same), view="voice", verification=Verification.V2)
    assert len(lines_of(pack, Section.HISTORY)) == 1
    other = (replace(today, matter="technician_no_show"), replace(march, matter="router_password"))
    pack = slotted(replace(snapshot, episodes=other), view="voice", verification=Verification.V2)
    assert lines_of(pack, Section.HISTORY) == []
    # A conversation stored before matters existed still joins by its words.
    legacy = (replace(today, matter="technician_no_show"), march)
    pack = slotted(replace(snapshot, episodes=legacy), view="voice", verification=Verification.V2)
    assert len(lines_of(pack, Section.HISTORY)) == 1


def test_a_conversation_the_reader_may_not_see_never_becomes_the_recurrence_line() -> None:
    snapshot = call_at_1407()
    billing = replace(
        snapshot.episodes[1], category="billing", normalized_category="billing", resolution="estorno"
    )
    today = replace(snapshot.episodes[0], category="billing", normalized_category="billing")
    at_v1 = slotted(replace(snapshot, episodes=(today, billing)), view="voice", verification=Verification.V1)
    assert "estorno" not in at_v1.text  # billing needs V2
    at_v2 = slotted(replace(snapshot, episodes=(today, billing)), view="voice", verification=Verification.V2)
    assert "estorno" in at_v2.text


def test_what_is_recent_is_measured_from_the_memory_never_the_clock() -> None:
    # Two days after the WhatsApp conversation, another agent acts: the conversation is no longer "just
    # happened", so it is only the latest conversation, and nothing says the matter repeats.
    snapshot = call_at_1407()
    [action] = snapshot.actions
    later = replace(snapshot, actions=(replace(action, occurred_at=T0 + timedelta(days=2)),))
    pack = slotted(later, view="voice", verification=Verification.V2)
    assert lines_of(pack, Section.HISTORY) == []
    assert lines_of(slotted(snapshot, view="voice", verification=Verification.V2), Section.HISTORY)
    again = slotted(shuffled(later, 7), view="voice", verification=Verification.V2)
    assert again.to_bytes() == pack.to_bytes()


def test_a_system_record_changed_in_the_last_hours_is_pinned_unless_an_action_names_it() -> None:
    snapshot = replace(call_at_1407(), episodes=call_at_1407().episodes[:1])
    [invoice] = snapshot.objects
    fresh = replace(invoice, as_of=T0 - timedelta(hours=2))
    pack = slotted(replace(snapshot, objects=(fresh,)), view="voice", verification=Verification.V2)
    assert ids(pack, "object:") == [f"object:{uid(0x700)}"]
    [action] = snapshot.actions
    named = replace(snapshot, objects=(fresh,), actions=(replace(action, object_id=fresh.object_id),))
    pack = slotted(named, view="voice", verification=Verification.V2)
    assert ids(pack, "object:") == []
    assert ids(pack, "action:") == [f"action:{uid(0x500)}"]


def test_what_just_happened_ranks_the_open_matter_first_within_its_budget() -> None:
    snapshot = call_at_1407()
    busy = tuple(episode(10 + n, "orders", days_ago=0.1 + n * 0.01, summary="pedido " * 25) for n in range(4))
    pack = slotted(
        replace(snapshot, episodes=(*snapshot.episodes, *busy)), view="voice", verification=Verification.V2
    )
    body = pack.manifest["tokens"]
    assert body["b1"] + body["b2"] <= 110 + RECENT_BUDGETS["voice"] + 30
    # The newest conversation is an order; the credit and what fits of the rest follow it.
    assert ids(pack, "action:") == [f"action:{uid(0x500)}"]


def test_a_learned_lower_ceiling_holds_in_what_just_happened_too() -> None:
    pack = slotted(call_at_1407(), view="voice", verification=Verification.V2, budgets=(("actions", 10),))
    assert ids(pack, "action:") == []


def long_story() -> MemorySnapshot:
    """The same story with summaries and a solution as long as a real extraction writes them."""
    snapshot = call_at_1407()
    today, march, unrelated = snapshot.episodes
    today = replace(
        today,
        summary="Marina disse que o técnico não apareceu hoje de manhã para a visita prometida e que vai "
        "ligar para resolver. O agente registrou a reclamação e pediu desculpas pela falha.",
    )
    march = replace(
        march,
        summary="Marina relatou que a visita técnica agendada para a manhã não aconteceu e que ficou "
        "esperando o técnico em casa. O agente pediu desculpas e lançou um crédito na fatura.",
        resolution="Crédito de R$ 40 lançado na fatura pela visita técnica que não aconteceu naquela manhã.",
    )
    return replace(snapshot, episodes=(today, march, unrelated))


def test_what_another_agent_did_in_the_last_hours_always_has_a_place() -> None:
    pack = slotted(long_story(), view="voice", verification=Verification.V2)
    assert ids(pack, "action:") == [f"action:{uid(0x500)}"]
    # The open matter is compacted around it, never left out: the newest conversation to its first
    # sentence, the older one cut short and marked, with its date, outcome and the start of its solution.
    [conversation] = lines_of(pack, Section.EPISODES)
    assert "O agente registrou" not in conversation
    # Without its reporting clause (tpl-12): what happened, not who reported it.
    assert conversation.startswith("Conversa: 22/09 · whatsapp · o técnico não apareceu")
    assert conversation.endswith(" · não resolvido.")
    [history] = lines_of(pack, Section.HISTORY)
    assert history.startswith("Histórico: já ocorreu antes: 12/03 · voice · a visita técnica")
    assert "... · resolvido · solução: Crédito de R$ 40" in history
    assert ".." not in history.replace("...", "")
    body = pack.manifest["tokens"]
    assert body["b1"] + body["b2"] <= 110 + RECENT_BUDGETS["voice"] + 30
    # The whole conversations still wait for a turn that asks: the slots read the lines as the body has them.
    entry = next(i for i in pack.manifest["items"] if i["id"] == str(uid(0x201)))
    assert entry["section"] == "episodes"


def test_an_open_item_said_twice_is_one_line() -> None:
    snapshot = marina()
    again = replace(
        open_item(2, "visita técnica remarcada para 23/09, manhã."),
        created_at=T0 - timedelta(hours=1),
    )
    twice = replace(snapshot, open_items=(*snapshot.open_items, again))
    for view in ("chat", "voice"):
        pack = slotted(twice, view=view, verification=Verification.V2)
        assert len(ids(pack, "open_item:")) == 1
        pending = [line for line in lines_of(pack, Section.PENDING) if "remarcada" in line]
        assert len(pending) == 1
        left = {e["id"]: e["rule"] for e in pack.manifest["excluded"] if e["kind"] == "open_item"}
        assert list(left.values()) == ["duplicate"]
        assert not {r.item_id for r in pack.reserve} & {f"open_item:{i}" for i in left}


# Token cuts (17, 6.10): a skeleton says each thing once. Summaries, solutions and facts as the extraction
# model writes them over dataset v2: a solution that repeats the summary's number, a fact that says the
# complaint again.


def extracted_story() -> MemorySnapshot:
    today = episode(
        1,
        "orders",
        days_ago=0.1,
        outcome="pending",
        summary="Patrícia informou que o pedido 21048 chegou com a tampa quebrada e pediu o protocolo. O "
        "protocolo informado foi 23929; a empresa prometeu responder em até dois dias úteis.",
        resolution="Protocolo 23929 informado; resposta em até dois dias úteis.",
    )
    older = episode(
        2,
        "sales",
        days_ago=40,
        channel="app",
        summary="Cliente perguntou se há um cupom para o primeiro pedido. O agente respondeu que o cupom "
        "83247 "
        "dá frete grátis.",
        resolution="O cupom 83247 dá frete grátis, mas vale só para pedidos acima de R$ 99.",
    )
    echo = replace(
        fact(1, "order_problem", "O pedido 21048 chegou com a tampa quebrada.", "contract"),
        origin_conversation_id="conv-1",
    )
    other = replace(fact(2, "plan", "fibra 500 mega", "contract"), origin_conversation_id="conv-2")
    return replace(
        marina(),
        facts=(echo, other),
        episodes=(today, older),
        open_items=(),
        actions=(),
        objects=(),
        traits=(),
    )


def test_a_voice_skeleton_says_each_thing_once_in_bytes() -> None:
    pack = slotted(extracted_story(), view="voice", verification=Verification.V1)
    body = pack.text.split("\n")[2:-1]
    # A voice skeleton pins the conversation's headline (tpl-12); the protocol is a slot's when asked.
    assert body == [
        "Cliente: Marina · prefere: WhatsApp.",
        "Fatos: plan: fibra 500 mega.",
        "Conversa: 22/09 · whatsapp · o pedido 21048 chegou com a tampa quebrada e pediu o protocolo..."
        " · pendente.",
    ]
    whole = next(r for r in pack.reserve if r.item_id == f"episode:{uid(0x201)}")
    assert "O protocolo informado foi 23929" in whole.line
    # The fact the conversation already says leaves the body and the reserve; the manifest says why.
    left = {e["id"]: e["rule"] for e in pack.manifest["excluded"] if e["kind"] == "fact"}
    assert left == {str(uid(0x101)): "duplicate"}
    assert f"fact:{uid(0x101)}" not in {r.item_id for r in pack.reserve}
    # A solution that says more than its summary (the minimum of R$ 99) still goes with its conversation.
    older = next(r for r in pack.reserve if r.item_id == f"episode:{uid(0x202)}")
    assert older.line.endswith(
        "· resolvido · solução: O cupom 83247 dá frete grátis, mas vale só para pedidos acima de R$ 99."
    )
    again = slotted(shuffled(extracted_story(), 3), view="voice", verification=Verification.V1)
    assert again.to_bytes() == pack.to_bytes()


def test_no_skeleton_says_when_memory_was_updated() -> None:
    voice = slotted(extracted_story(), view="voice", verification=Verification.V1)
    chat = slotted(extracted_story(), view="chat", verification=Verification.V1)
    assert "atualizado em" not in voice.text
    assert "atualizado em" not in chat.text  # every line it pins is dated (tpl-12)
    # What the chat skeleton pins says each thing once too.
    assert "solução: Protocolo 23929" not in chat.text
    assert f"fact:{uid(0x101)}" not in chat.item_ids
    unverified = slotted(extracted_story(), view="voice", verification=Verification.V0)
    assert "retidos até verificar a identidade" in unverified.text


def test_a_fact_that_says_more_than_the_conversation_stays() -> None:
    story = extracted_story()
    own = replace(
        fact(3, "order_problem", "A caixa do pedido 21048 veio sem o manual.", "contract"),
        origin_conversation_id="conv-1",
    )
    pack = slotted(replace(story, facts=(*story.facts, own)), view="voice", verification=Verification.V1)
    # A complaint fact that says more waits in the reserve for a turn about its matter (tpl-12); the one
    # the conversation says leaves both.
    assert f"fact:{uid(0x103)}" in {r.item_id for r in pack.reserve}
    assert f"fact:{uid(0x101)}" not in {*pack.item_ids, *(r.item_id for r in pack.reserve)}
    # A pack no reader reads turn by turn keeps its whole body, the fact included.
    assert f"fact:{uid(0x101)}" in pack_for(story).item_ids
