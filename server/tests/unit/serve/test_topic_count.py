"""The count by matter (17, 6.2): the extraction's key groups the conversations about one problem, whatever
words each used, and the line names the matter and lists every occurrence. Summaries are shaped like the
extraction model writes them (a sentence of what the customer said, one of what the agent registered)."""

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta

from niadra.domain.compile.model import CompiledPack, EpisodeRef
from niadra.domain.compile.templates import Formatter
from niadra.domain.compile.tokens import estimate_tokens
from niadra.domain.serve.slots import SlotItem, SlotPlan, plan_slots, run_channels, standard_channels
from niadra.domain.serve.turn import analyze
from niadra.domain.vocabulary import Verification
from niadra.testing.cell import sample_config
from tests.unit.compile.factories import marina
from tests.unit.compile.test_compiler import pack_for

NOW = datetime(2026, 9, 25, 12, 0, tzinfo=UTC)
PT = Formatter("America/Sao_Paulo", "pt-BR")
EN = Formatter("America/Sao_Paulo", "en-US")
LEXICON = sample_config().lexicon()


def item(n: int, text: str, *, days: float, channel: str, in_body: bool = False) -> SlotItem:
    line = f"[Conversa] {text}"
    return SlotItem(
        f"episode:{n:04d}",
        "episode",
        text,
        line,
        estimate_tokens(line + "\n"),
        at=NOW - timedelta(days=days),
        channel=channel,
        in_body=in_body,
    )


def ref(
    n: int,
    matter: str | None,
    label: str | None,
    *,
    days: float,
    channel: str,
    category: str = "orders",
    intent: str = "complaint",
) -> EpisodeRef:
    return EpisodeRef(
        f"episode:{n:04d}", category, intent, NOW - timedelta(days=days), channel, matter, label
    )


def plan(
    question: str,
    pool: list[SlotItem],
    episodes: tuple[EpisodeRef, ...],
    fmt: Formatter = PT,
    *,
    voice: bool = True,
) -> SlotPlan:
    turn = analyze(question)
    hits = run_channels(turn, pool, standard_channels((), lexicon=LEXICON))
    return plan_slots(
        turn,
        pool,
        hits,
        fmt=fmt,
        voice=voice,
        budget=120,
        max_items=2,
        episodes=episodes,
        now=NOW,
        lexicon=LEXICON,
    )


# Four failed deliveries said four ways, and three damaged boxes: the question asks for the first matter.
DELIVERY_POOL = [
    item(
        1,
        "Cliente relata que marcaram tentativa de entrega sem ninguém vir. "
        "O agente registrou a ocorrência 55721.",
        days=19.8,
        channel="whatsapp",
    ),
    item(
        2,
        "O cliente disse que o entregador não conseguiu entregar de novo. Ocorrência 24487 registrada.",
        days=36.2,
        channel="voice",
    ),
    item(
        3,
        "A cliente relatou que a entrega falhou outra vez. O agente registrou a ocorrência 47579.",
        days=55.5,
        channel="email",
    ),
    item(
        4,
        "Cliente informa que mais uma vez a entrega não foi feita. Registrada a ocorrência 49688.",
        days=87.2,
        channel="app",
    ),
    item(
        5,
        "A caixa chegou amassada de novo. O agente registrou a ocorrência 88026.",
        days=84.5,
        channel="whatsapp",
    ),
    item(6, "Cliente recebeu de novo a caixa rasgada. Ocorrência 53630.", days=68.6, channel="whatsapp"),
    item(
        7,
        "O cliente relatou que outra vez a carga chegou avariada. O agente registrou a ocorrência 70365.",
        days=2.0,
        channel="voice",
        in_body=True,
    ),
]
DELIVERY_EPISODES = (
    ref(1, "delivery_failed", "entrega não realizada", days=19.8, channel="whatsapp"),
    ref(2, "delivery_failed", "entrega não realizada", days=36.2, channel="voice"),
    # The same matter, keyed the other way round by a conversation extracted before the others existed.
    ref(3, "failed_delivery", "falha na entrega", days=55.5, channel="email"),
    ref(4, "delivery_failed", "entrega não feita", days=87.2, channel="app"),
    ref(5, "package_damaged", "caixa danificada", days=84.5, channel="whatsapp"),
    ref(6, "package_damaged", "caixa danificada", days=68.6, channel="whatsapp"),
    ref(7, "package_damaged", "carga avariada", days=2.0, channel="voice"),
)


def test_the_count_by_matter_finds_every_way_the_customer_said_it_and_names_it() -> None:
    got = plan(
        "Quantas vezes eu já reclamei de entrega não realizada com vocês antes desta ligação?",
        DELIVERY_POOL,
        DELIVERY_EPISODES,
    )
    assert [d.kind for d in got.derived] == ["count"]
    # A voice agent says days (tpl-12); a text view lists the number each was registered under too.
    assert got.derived[0].line == (
        "Histórico: reclamações de entrega não realizada nos últimos 90 dias: 4 (05/09, 20/08, 31/07, 30/06)."
    )
    text = plan(
        "Quantas vezes eu já reclamei de entrega não realizada com vocês antes desta ligação?",
        DELIVERY_POOL,
        DELIVERY_EPISODES,
        voice=False,
    )
    assert text.derived[0].line == (
        "[Histórico] reclamações de entrega não realizada nos últimos 90 dias: 4 "
        "(05/09 55721, 20/08 24487, 31/07 47579, 30/06 49688)"
    )
    why = got.why()["derived"][0]
    assert why["rule"] == "count_matter_complaints"
    assert why["counted"] == 4
    assert why["matters"] == 1  # two spellings of one key are one matter
    assert got.slots == ()  # every counted conversation is dated on the line


def test_the_other_matter_is_counted_apart_including_the_conversation_in_the_body() -> None:
    got = plan("Quantas vezes a caixa chegou danificada?", DELIVERY_POOL, DELIVERY_EPISODES, voice=False)
    line = got.derived[0].line
    assert ": 3 (" in line
    assert "carga avariada" in line  # the newest label names the matter
    assert "23/09 70365" in line  # the conversation the body already shows is counted too


def test_an_english_question_counts_english_summaries_in_a_portuguese_space() -> None:
    pool = [
        item(
            1,
            "The customer says the internet dropped again in the middle of the night. "
            "The agent logged incident 49185.",
            days=14,
            channel="whatsapp",
        ),
        item(
            2,
            "Customer lost internet in the evening once more; incident 24947 logged.",
            days=28,
            channel="voice",
        ),
        item(3, "The internet dropped again last night. Incident 75263.", days=51.6, channel="email"),
        item(
            4,
            "The connection went down again, same as before. Incident 57348 logged.",
            days=63.1,
            channel="app",
        ),
        item(5, "Customer asked about a plan with more data; offer 26709.", days=21.9, channel="whatsapp"),
    ]
    episodes = (
        ref(1, "internet_down", "internet dropping", days=14, channel="whatsapp", category="technical"),
        ref(2, "internet_outage", "lost internet", days=28, channel="voice", category="technical"),
        ref(3, "internet_down", "internet down", days=51.6, channel="email", category="technical"),
        ref(4, "internet_down", "connection down", days=63.1, channel="app", category="technical"),
        ref(
            5, "plan_upgrade", "more data", days=21.9, channel="whatsapp", category="sales", intent="request"
        ),
    )
    got = plan(
        "How many times have I complained about the internet dropping before this call?", pool, episodes, EN
    )
    line = got.derived[0].line
    # Three conversations share a key; the fourth, keyed apart, answers the question through its label.
    assert line == (
        "History: complaints about internet dropping in the last 90 days: 4 (09-11, 08-28, 08-04, 07-24)."
    )
    assert got.why()["derived"][0]["matters"] == 2


def test_conversations_extracted_before_matters_join_when_their_words_answer() -> None:
    episodes = (
        *DELIVERY_EPISODES[:2],
        ref(3, None, None, days=55.5, channel="email"),  # an older run: no matter
        ref(4, None, None, days=87.2, channel="app"),
        *DELIVERY_EPISODES[4:],
    )
    got = plan("Quantas vezes a entrega falhou?", DELIVERY_POOL, episodes, voice=False)
    line = got.derived[0].line
    assert "47579" in line  # "a entrega falhou outra vez", said in the question's words
    assert "88026" not in line
    assert "70365" not in line


def test_without_any_matter_the_count_by_words_runs_as_before() -> None:
    episodes = tuple(
        EpisodeRef(e.item_id, e.category, e.intent, e.started_at, e.channel) for e in DELIVERY_EPISODES
    )
    got = plan("Quantas vezes eu já reclamei de entrega não realizada?", DELIVERY_POOL, episodes)
    assert got.why()["derived"][0]["rule"].startswith("count_topic")


def test_an_amount_in_a_summary_is_never_the_occurrence_number() -> None:
    pool = [item(1, "Pediu o reembolso de R$ 41,78 do frete; protocolo 44713.", days=3, channel="voice")]
    episodes = (ref(1, "refund_fee", "reembolso do frete", days=3, channel="voice", category="billing"),)
    got = plan("Quantas vezes pedi o reembolso do frete?", pool, episodes, voice=False)
    assert "22/09 44713" in got.derived[0].line
    assert "4178" not in got.derived[0].line


def test_the_pack_carries_the_matters_and_a_reader_one_release_behind_still_loads_it() -> None:
    snapshot = marina()
    keyed = replace(
        snapshot,
        episodes=tuple(
            replace(e, matter="visit_missed", matter_label="visita não feita") if n % 2 == 0 else e
            for n, e in enumerate(snapshot.episodes)
        ),
    )
    pack = pack_for(keyed, slotted=True, verification=Verification.V2)
    matters = {e.item_id: e.matter for e in pack.episode_index}
    assert "visit_missed" in matters.values()
    assert None in matters.values()
    assert CompiledPack.from_bytes(pack.to_bytes()) == pack
    body = json.loads(pack.to_bytes())
    assert {row[0]: row[5] for row in body["episode_index"]} == matters
