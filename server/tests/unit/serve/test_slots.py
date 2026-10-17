"""Per-turn slots (11, 3.3): channels, fusion, the rank-ordered knapsack, the derived lines."""

import random
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import UUID

from niadra.domain.compile.model import EpisodeRef
from niadra.domain.compile.templates import Formatter
from niadra.domain.compile.tokens import estimate_tokens
from niadra.domain.memory.values import SettledValue, ValueType
from niadra.domain.serve.lexicon import EMPTY
from niadra.domain.serve.relative_time import Window
from niadra.domain.serve.slots import (
    DEFAULT_WEIGHTS,
    RRF_K,
    ChannelHits,
    SlotItem,
    SlotPlan,
    TemporalChannel,
    excerpt,
    fuse,
    plan_slots,
    run_channels,
    standard_channels,
    value_hits,
)
from niadra.domain.serve.turn import analyze
from niadra.domain.vocabulary import Speaker
from niadra.testing.cell import sample_config

NOW = datetime(2026, 9, 25, 12, 0, tzinfo=UTC)
FMT = Formatter("America/Sao_Paulo", "pt-BR")
LEXICON = sample_config().lexicon()


def item(
    n: int,
    text: str,
    *,
    days: float,
    channel: str = "whatsapp",
    kind: str = "episode",
    in_body: bool = False,
    value_type: str | None = None,
) -> SlotItem:
    line = f"[Conversa] {text}"
    return SlotItem(
        f"{kind}:{n:04d}",
        kind,
        text,
        line,
        estimate_tokens(line + "\n"),
        at=NOW - timedelta(days=days),
        channel=channel,
        in_body=in_body,
        value_type=value_type,
    )


def ref(
    n: int, category: str, *, days: float, intent: str = "complaint", channel: str = "whatsapp"
) -> EpisodeRef:
    return EpisodeRef(f"episode:{n:04d}", category, intent, NOW - timedelta(days=days), channel)


def plan(
    question: str,
    pool: list[SlotItem],
    *,
    budget: int = 120,
    max_items: int = 4,
    episodes: tuple[EpisodeRef, ...] = (),
    windows: tuple[Window, ...] = (),
    extra: tuple[ChannelHits, ...] = (),
    **kwargs: object,
) -> SlotPlan:
    turn = analyze(question)
    lexicon = kwargs.pop("lexicon", LEXICON)
    hits = [*run_channels(turn, pool, standard_channels(windows, lexicon=lexicon)), *extra]  # type: ignore[arg-type]
    return plan_slots(
        turn,
        pool,
        hits,
        fmt=FMT,
        voice=False,
        budget=budget,
        max_items=max_items,
        episodes=episodes,
        now=NOW,
        lexicon=lexicon,  # type: ignore[arg-type]
        **kwargs,  # type: ignore[arg-type]
    )


HISTORY = [
    item(1, "O farol estava trincado. / Registrei como 12927.", days=22),
    item(2, "Depois da vistoria, apareceu um barulho na suspensão. / Registrei como 88280.", days=4),
    item(3, "Olá, sou Diego. / O protocolo do seu atendimento é 81220.", days=5, channel="email"),
    item(4, "Tem cupom para primeira compra? / Tem, o cupom 40997 dá frete grátis.", days=20),
    item(5, "Quero a segunda via da conta.", days=40, channel="app"),
]


def test_the_same_turn_over_the_same_pool_gives_the_same_bytes() -> None:
    first = plan("Qual o protocolo que me passaram por e-mail?", HISTORY)
    shuffled = HISTORY[:]
    random.Random(7).shuffle(shuffled)
    again = plan("Qual o protocolo que me passaram por e-mail?", shuffled)
    assert first.render(FMT) == again.render(FMT)
    assert first.manifest() == again.manifest()
    assert first.item_ids == ("episode:0003",)


def test_an_exact_value_of_the_asked_type_comes_first_and_the_budget_holds() -> None:
    got = plan("O que eu relatei depois da vistoria? Qual foi o número do registro?", HISTORY, budget=40)
    assert got.item_ids[0] == "episode:0002"
    assert got.tokens <= 40
    assert got.manifest()["items"][0]["channels"][0] == "exact"


def test_what_the_body_says_is_never_slotted_and_its_older_value_stays_out() -> None:
    pool = [
        item(1, "Mudei. Agora o CEP de coleta é 45778-204.", days=4, in_body=True),
        item(2, "Para atualizar: o CEP de coleta é 88221-615.", days=39, channel="email"),
    ]
    got = plan("Qual é o CEP de coleta que vocês têm hoje?", pool)
    assert got.slots == ()
    assert got.derived == ()  # the body answers: no "no record" line either
    # A turn that names the old number gets the line that holds it.
    named = plan("E o CEP 88221-615, ainda vale?", pool)
    assert named.item_ids == ("episode:0002",)


def test_the_count_is_by_the_topic_asked_not_the_last_conversation() -> None:
    pool = [
        item(1, "A internet caiu de novo ontem à noite.", days=18),
        item(2, "Mais uma vez fiquei sem internet à noite.", days=28, channel="voice"),
        item(3, "A conexão voltou a cair, igual antes.", days=50, channel="email"),
        item(4, "Quero a segunda via da conta.", days=2, in_body=True),
    ]
    episodes = (
        ref(1, "technical", days=18),
        ref(2, "technical", days=28),
        ref(3, "technical", days=50),
        ref(4, "billing", days=2, intent="request"),
        ref(9, "technical", days=120),  # outside the 90 days
    )
    got = plan("Quantas vezes eu já reclamei da internet caindo?", pool, episodes=episodes)
    assert [d.kind for d in got.derived] == ["count"]
    # "A conexão voltou a cair" is the same matter in other words: the lexicon reads it.
    assert got.derived[0].line == (
        "[Histórico] reclamações sobre este assunto nos últimos 90 dias: 3 (07/09, 28/08, 06/08)"
    )
    assert got.slots == ()  # the count line dates every counted conversation already


def test_the_count_leaves_out_another_matter_of_the_same_category() -> None:
    pool = [
        item(1, "Faltou um item na minha entrega.", days=10),
        item(2, "Mais uma vez a entrega veio incompleta.", days=30),
        item(3, "Outra vez recebi um item com defeito.", days=40),
        item(4, "De novo o produto veio danificado.", days=1, in_body=True),
    ]
    episodes = (
        ref(1, "orders", days=10),
        ref(2, "orders", days=30, intent="request"),  # filed as a request: still the matter asked
        ref(3, "orders", days=40),
        ref(4, "orders", days=1),
    )
    got = plan("Quantas vezes eu já reclamei de item faltando na entrega?", pool, episodes=episodes)
    assert got.derived[0].line == (
        "[Histórico] contatos sobre este assunto nos últimos 90 dias: 2 (15/09, 26/08)"
    )
    assert got.why()["derived"] == [
        {"kind": "count", "rule": "count_topic", "units": 3, "matched": 2, "counted": 2, "window_days": 90}
    ]


def test_a_count_no_conversation_answers_in_words_is_by_category() -> None:
    pool = [item(1, "Mensagem de voz sem transcrição.", days=5), item(2, "Sem texto.", days=9)]
    episodes = (ref(1, "technical", days=5), ref(2, "technical", days=9))
    got = plan("Quantas vezes eu já reclamei da internet caindo?", pool, episodes=episodes)
    assert got.derived == ()  # nothing ranked, nothing to count
    question = "Quantas vezes eu já reclamei da mensagem sem texto?"
    words = plan(question, pool, episodes=episodes, lexicon=EMPTY)
    assert words.why()["derived"][0]["rule"] == "count_topic_complaints"


def test_what_memory_has_no_record_of_is_said_and_withheld_items_are_named_instead_when_unverified() -> None:
    got = plan("Qual é o número do comprovante?", HISTORY)
    assert [d.line for d in got.derived] == [
        "[Contexto] sem registro de comprovante no histórico deste cliente"
    ]
    unknown = plan("E o pedido 55555?", HISTORY)
    assert unknown.derived[0].line == "[Contexto] sem registro de 55555 no histórico deste cliente"
    withheld = plan("Qual documento vocês têm no cadastro?", HISTORY, withheld=3, unproven=True)
    assert [d.kind for d in withheld.derived] == ["withheld"]
    assert "3 itens retidos até verificar a identidade" in withheld.derived[0].line
    said = plan(
        "Qual documento vocês têm no cadastro?", HISTORY, withheld=3, unproven=True, withheld_said=True
    )
    assert said.derived == ()
    # Verified, nothing is withheld from this reader: the record does not exist.
    verified = plan("Qual documento vocês têm no cadastro?", HISTORY, withheld=3, unproven=False)
    assert [d.kind for d in verified.derived] == ["no_record"]


def test_a_line_that_may_answer_in_other_words_is_never_contradicted() -> None:
    pool = [item(1, "Abri a ocorrência 31723 sobre a queda da internet.", days=3)]
    got = plan("Qual o número do registro da queda da internet?", pool)
    assert got.derived == ()
    assert got.item_ids == ("episode:0001",)


def test_time_windows_and_named_channels_rank_by_recency() -> None:
    window = Window(NOW - timedelta(days=25), NOW - timedelta(days=15))
    turn = analyze("O que eu falei no app?")
    ranked = TemporalChannel((window,)).rank(turn, HISTORY).ranked
    assert ranked == ("episode:0004", "episode:0001", "episode:0005")


def test_the_latest_of_a_named_channel_can_be_the_bodys() -> None:
    pool = [
        item(1, "Preciso da solução até o dia 26.", days=2, in_body=True),
        item(4, "Tem cupom para primeira compra? / Tem, o cupom 40997 dá frete grátis.", days=20),
    ]
    got = plan("Até que dia eu disse no WhatsApp que precisava da solução?", pool)
    assert "episode:0004" not in got.item_ids


def test_exact_outranks_a_word_in_common() -> None:
    fused = fuse([ChannelHits("lexical", ("a", "b")), ChannelHits("exact", ("b",))])
    assert [f.item_id for f in fused] == ["b", "a"]
    assert fused[0].channels == ("lexical", "exact")


def test_a_channel_brings_items_of_its_own() -> None:
    value = SlotItem("value:0001", "value", "protocolo 81220 · email", "[Valor] protocolo 81220 · email", 8)
    extra = (ChannelHits("values", ("value:0001",), (value,)),)
    got = plan("Qual o protocolo?", [], extra=extra)
    assert got.item_ids == ("value:0001",)
    assert got.derived == ()


def test_a_long_best_match_is_cut_to_the_sentences_that_answer() -> None:
    long = item(
        1,
        "19/09/2026 · email · Olá, sou Juliana Dias. Escrevo sobre a remessa 48552: o entregador não "
        "encontrou o endereço. Podem me passar o protocolo? / Olá, Juliana. O protocolo do seu atendimento é "
        "54286. Respondemos em até dois dias úteis. · resolvido",
        days=6,
        channel="email",
    )
    got = plan("Qual é o número de protocolo que me passaram?", [long], budget=40)
    assert got.item_ids == ("episode:0001",)
    # Too long for the room left: the conversation goes as its atom, the matter with the number.
    line = got.slots[0].item.line
    assert line == "[Conversa] 19/09/2026 · email · sou Juliana Dias · protocolo do seu atendimento é 54286"
    assert got.slots[0].why is not None
    assert got.slots[0].why.atom
    assert got.tokens <= 40
    # Without an atom that answers, the cut keeps the sentence that answers and the first one, which says
    # what the matter was.
    cut = excerpt(long, analyze("Qual é o número de protocolo que me passaram?"), 45)
    assert cut is not None
    assert cut.line.startswith("[Conversa] 19/09/2026 · email · Olá, sou Juliana Dias. ... ")
    assert "54286" in cut.line
    assert cut.line.endswith("· resolvido")
    assert excerpt(long, analyze("Qual o cupom?"), 40) is None


def test_no_turn_no_budget_no_slots() -> None:
    assert plan("", HISTORY).render(FMT) is None
    assert plan("Qual o protocolo?", HISTORY, budget=0).slots == ()


def test_the_block_goes_in_its_own_tags_and_the_manifest_holds_no_text() -> None:
    got = plan("Qual o protocolo que me passaram por e-mail?", HISTORY)
    assert got.render(FMT) == (
        "<turno>\n[Conversa] Olá, sou Diego. / O protocolo do seu atendimento é 81220.\n</turno>"
    )
    manifest = got.manifest()
    assert "81220" not in str(manifest)
    assert manifest["items"] == [
        {"id": "episode:0003", "kind": "episode", "channels": ["exact", "lexical", "temporal"], "position": 1,
         "tokens": got.slots[0].item.tokens}
    ]  # fmt: skip
    assert manifest["channels"]["exact"] == {"hits": 1}


def test_values_of_kinds_memory_never_settles_are_simply_not_looked_up() -> None:
    episode_id = UUID(int=7)
    pool = [replace(item(1, "Mudei. Agora o CEP é 45778-204.", days=4), item_id=f"episode:{episode_id}")]
    zip_code = SettledValue(
        UUID(int=8), UUID(int=9), ValueType.POSTAL_CODE, "45778-204", NOW - timedelta(days=4),
        Speaker.CUSTOMER, UUID(int=10), "whatsapp", UUID(int=11), NOW, episode_id=episode_id,
    )  # fmt: skip
    hits = value_hits([zip_code], analyze("Qual o CEP do endereço da apólice?"), pool, fmt=FMT, voice=False)
    assert hits.ranked == (f"value:{zip_code.value_id}",)
    assert hits.items[0].line == "[Valor] CEP 45778-204 · whatsapp · 21/09/2026"


# Why each slot was chosen (12, WP-E).


def test_why_lists_exactly_the_channels_that_ranked_the_item_and_the_score_is_their_sum() -> None:
    turn = analyze("Qual o protocolo que me passaram por e-mail?")
    hits = run_channels(turn, HISTORY, standard_channels())
    got = plan("Qual o protocolo que me passaram por e-mail?", HISTORY, weights_version=7)
    [slot] = got.slots
    assert slot.why is not None
    ranking = {h.channel: h.ranked for h in hits}
    ranked_it = [name for name, ids in ranking.items() if slot.item.item_id in ids]
    assert [r.channel for r in slot.why.ranks] == ranked_it == list(slot.channels)
    for rank in slot.why.ranks:
        assert ranking[rank.channel][rank.position - 1] == slot.item.item_id
        assert rank.weight == DEFAULT_WEIGHTS[rank.channel]
        assert rank.contribution == rank.weight / (RRF_K + rank.position)
    assert slot.why.score == sum(r.contribution for r in slot.why.ranks)
    assert slot.why.weights_version == 7
    doc = got.why()
    assert doc["weights_version"] == 7
    assert doc["items"][0]["id"] == "episode:0003"
    assert set(doc["items"][0]["channels"]) == set(ranked_it)


def test_why_never_changes_what_is_chosen_nor_carries_a_line_or_a_value() -> None:
    long = item(
        1,
        "19/09/2026 · email · Olá, sou Juliana Dias. Escrevo sobre a remessa 48552: o entregador não "
        "encontrou o endereço. / Olá, Juliana. O protocolo do seu atendimento é 54286. Respondemos em até "
        "dois dias úteis. · resolvido",
        days=6,
        channel="email",
    )
    question = "Qual é o número de protocolo que me passaram?"
    with_version = plan(question, [long, HISTORY[4]], budget=40, weights_version=3)
    without = plan(question, [long, HISTORY[4]], budget=40)
    assert with_version.render(FMT) == without.render(FMT)
    assert with_version.manifest() == without.manifest()
    assert with_version.slots[0].why is not None
    assert with_version.slots[0].why.atom  # the best match went as its atom to fit
    assert with_version.why()["items"][0]["atom"] is True
    text = str(with_version.why())
    for word in ("54286", "48552", "Juliana", "protocolo", "81220"):
        assert word not in text


def test_a_derived_line_says_the_rule_that_wrote_it_without_the_number_asked() -> None:
    episodes = (ref(1, "technical", days=18), ref(2, "technical", days=28), ref(3, "billing", days=40))
    pool = [
        item(1, "A internet caiu de novo ontem à noite.", days=18),
        item(2, "Mais uma vez fiquei sem internet à noite.", days=28),
    ]
    counted = plan("Quantas vezes eu já reclamei da internet caindo?", pool, episodes=episodes)
    assert counted.why()["derived"] == [
        {"kind": "count", "rule": "count_topic_complaints", "units": 2, "matched": 2, "counted": 2,
         "window_days": 90}
    ]  # fmt: skip
    # A turn no line answers in words: the category of the best-ranked conversation, as before.
    by_category = plan("Quantas vezes eu já reclamei?", pool, episodes=episodes, lexicon=EMPTY)
    assert by_category.derived == ()
    unknown = plan("E o pedido 55555?", HISTORY).why()
    assert unknown["derived"] == [
        {"kind": "no_record", "rule": "no_record", "asked_types": ["order"], "identifiers": 1}
    ]
    assert "55555" not in str(unknown)
    withheld = plan("Qual documento vocês têm no cadastro?", HISTORY, withheld=3, unproven=True).why()
    assert withheld["derived"][0]["rule"] == "withheld_may_hold"
    assert withheld["derived"][0]["withheld"] == 3


def test_a_channel_weighing_nothing_adds_nothing_and_names_nothing() -> None:
    fused = fuse(
        [ChannelHits("exact", ("b",)), ChannelHits("experiment", ("c", "b"))],
        {**DEFAULT_WEIGHTS, "experiment": 0.0},
    )
    assert [f.item_id for f in fused] == ["b"]
    assert fused[0].channels == ("exact",)


# Guard lines (13, 6.1).


def _visit(speaker: Speaker, raw: str, *, n: int, days: float) -> SettledValue:
    return SettledValue(
        UUID(int=0x100 + n), UUID(int=9), ValueType.PROTOCOL, raw, NOW - timedelta(days=days),
        speaker, UUID(int=10), "email", UUID(int=11), NOW, episode_id=UUID(int=3),
    )  # fmt: skip


def _guarded(question: str, guard_arm: str) -> tuple[SlotPlan, SlotPlan]:
    """The same turn planned without guards and with the protocol's guard in `guard_arm`."""
    from niadra.domain.serve.guards import guards_for

    turn = analyze(question)
    said = item(3, "Pediu o protocolo por e-mail.", days=5, channel="email")
    pool = [i for i in HISTORY if i.item_id != "episode:0003"] + [
        replace(said, item_id=f"episode:{UUID(int=3)}")
    ]
    values = [_visit(Speaker.SYSTEM, "81220", n=1, days=5)]
    hits = [
        *run_channels(turn, pool, standard_channels()),
        value_hits(values, turn, pool, fmt=FMT, voice=False),
    ]
    guards = guards_for(turn, values, frozenset({"protocol"}), fmt=FMT, voice=False)
    common = {"fmt": FMT, "voice": False, "budget": 120, "max_items": 4, "now": NOW}
    bare = plan_slots(turn, pool, hits, **common)  # type: ignore[arg-type]
    return bare, plan_slots(turn, pool, hits, guards=guards, guard_arm=guard_arm, **common)  # type: ignore[arg-type]


def test_a_guard_goes_first_takes_its_tokens_before_the_items_and_its_value_takes_no_slot() -> None:
    bare, guarded = _guarded("Qual o protocolo que me passaram por e-mail?", "guards")
    [guard] = guarded.derived
    assert guard.kind == "guard"
    assert guard.rule == "guard_protocol"
    assert guard.line.startswith("[Guarda] protocolo 81220")
    assert (guarded.render(FMT) or "").splitlines()[1] == guard.line
    assert f"value:{UUID(int=0x101)}" in bare.item_ids
    assert f"value:{UUID(int=0x101)}" not in guarded.item_ids
    assert guarded.tokens <= 120
    assert guarded.manifest()["guards"] == [
        {"id": f"value:{UUID(int=0x101)}", "value_type": "protocol", "arm": "guards"}
    ]
    [why] = guarded.why()["derived"]
    assert why == {
        "kind": "guard",
        "rule": "guard_protocol",
        "basis": f"value:{UUID(int=0x101)}",
        "value_type": "protocol",
        "origin": "system",
    }
    assert "81220" not in str(guarded.manifest()) + str(guarded.why())


def test_the_control_group_gets_the_same_bytes_as_without_guards_and_the_guard_is_only_recorded() -> None:
    bare, control = _guarded("Qual o protocolo que me passaram por e-mail?", "control")
    assert control.render(FMT) == bare.render(FMT)
    assert control.item_ids == bare.item_ids
    assert control.tokens == bare.tokens
    assert control.why() == bare.why()
    assert control.manifest()["guards"] == [
        {"id": f"value:{UUID(int=0x101)}", "value_type": "protocol", "arm": "control"}
    ]
    assert "guards" not in bare.manifest()
