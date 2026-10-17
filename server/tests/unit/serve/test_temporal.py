"""The temporal channel of the per-turn slots (11, 3.3): anchors on system events filter and boost,
calendar words only boost, and a question without time words leaves every item alone."""

import statistics
import time
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest

from niadra.config.models import SpaceConfig
from niadra.domain.compile.model import Candidate, ItemKind, Section
from niadra.domain.policy import ItemAttrs
from niadra.domain.records import BusinessObject, ObjectKey
from niadra.domain.serve.relative_time import Relation, SystemMark, marks_from_objects
from niadra.domain.serve.temporal import TemporalResult, temporal_channel
from tests.latency import within

TZ = "America/Sao_Paulo"
NOW = datetime(2026, 9, 25, 15, 0, tzinfo=UTC)
LEXICON = SpaceConfig(
    space_id=uuid4(), tenant_id=uuid4(), region="us-east-2", environment="sandbox"
).anchor_lexicon()


@dataclass(frozen=True, slots=True)
class Item:
    item_id: str
    at: datetime
    text: str
    object_id: UUID | None = None


def ago(days: float) -> datetime:
    return NOW - timedelta(days=days)


def run(question: str | None, items: list[Item], marks: Sequence[SystemMark]) -> TemporalResult:
    return temporal_channel(question, items, lexicon=LEXICON, marks=marks, reference=NOW, timezone=TZ)


@dataclass(frozen=True, slots=True)
class OrderCase:
    """The benchmark's order-and-time shape: a report before a system milestone, the milestone on an
    object of the ERP, a report after it that settles a number, and conversations about other things."""

    question: str
    canonical_type: str
    object_type: str
    before: str
    after: str


# One case per domain of the dataset, in the two languages it has and in Spanish, with the event words
# each language uses. The customer's own words name the milestone ("Depois da vistoria, ..."), and so
# does the summary the extraction writes of that conversation.
ORDER_CASES = [
    OrderCase(
        "O que eu relatei depois da vistoria? Qual foi o número do registro?",
        "inspection.completed",
        "claim",
        "O cliente relatou que o farol estava trincado; registro 12927.",
        "O cliente relatou que, depois da vistoria, apareceu um barulho na suspensão; registro 88280.",
    ),
    OrderCase(
        "O que eu relatei depois da emissão do cartão novo? Qual foi o número do registro?",
        "card.reissued",
        "dispute",
        "O cliente relatou que o limite foi reduzido sem aviso; registro 56957.",
        "Depois da emissão do cartão novo, apareceu uma cobrança não reconhecida; registro 52903.",
    ),
    OrderCase(
        "O que eu relatei depois da entrega? Qual foi o número do registro?",
        "order.delivered",
        "order",
        "O cliente relatou que o pagamento ficou pendente; registro 58412.",
        "Após a entrega, a nota fiscal veio com o endereço errado; registro 78279.",
    ),
    OrderCase(
        "O que eu relatei depois da coleta? Qual foi o número do registro?",
        "pickup.completed",
        "shipment",
        "O cliente relatou que a etiqueta saiu ilegível; registro 30211.",
        "Depois da coleta, o rastreio parou de atualizar; registro 44120.",
    ),
    OrderCase(
        "O que eu relatei depois da visita do técnico? Qual foi o número do registro?",
        "field_visit.completed",
        "ticket",
        "O cliente relatou que a internet caía à noite; registro 70114.",
        "Depois da visita do técnico, o roteador passou a reiniciar sozinho; registro 91356.",
    ),
    OrderCase(
        "What did I report after the inspection? What was the reference number?",
        "inspection.completed",
        "claim",
        "The customer reported a cracked headlight; reference 31747.",
        "After the inspection, the customer heard a noise in the suspension; reference 60221.",
    ),
    OrderCase(
        "What did I report after the new card was issued? What was the reference number?",
        "card.reissued",
        "dispute",
        "The customer reported the limit was lowered without notice; reference 11802.",
        "After the new card was issued, an unknown charge appeared; reference 37014.",
    ),
    OrderCase(
        "What did I report after the delivery? What was the reference number?",
        "order.delivered",
        "order",
        "The customer reported the payment was pending; reference 20931.",
        "After the delivery, the invoice had the wrong address; reference 80182.",
    ),
    OrderCase(
        "What did I report after the pickup? What was the reference number?",
        "pickup.completed",
        "shipment",
        "The customer reported an unreadable label; reference 45510.",
        "After the pickup, tracking stopped updating; reference 27893.",
    ),
    OrderCase(
        "What did I report after the technician's visit? What was the reference number?",
        "field_visit.completed",
        "ticket",
        "The customer reported the internet dropping at night; reference 61170.",
        "After the technician's visit, the router kept restarting; reference 33805.",
    ),
    OrderCase(
        "¿Qué reporté después de la inspección? ¿Cuál fue el número de registro?",
        "inspection.completed",
        "claim",
        "El cliente reportó un faro roto; registro 55120.",
        "Después de la inspección, apareció un ruido en la suspensión; registro 70431.",
    ),
    OrderCase(
        "¿Qué reporté después de la emisión de la tarjeta nueva? ¿Cuál fue el número de registro?",
        "card.reissued",
        "dispute",
        "El cliente reportó que le bajaron el límite sin aviso; registro 22817.",
        "Después de recibir la tarjeta nueva, apareció un cobro que no reconoce; registro 64092.",
    ),
    OrderCase(
        "¿Qué reporté después de la entrega? ¿Cuál fue el número de registro?",
        "order.delivered",
        "order",
        "El cliente reportó que el pago quedó pendiente; registro 18820.",
        "Después de la entrega, la factura llegó con la dirección equivocada; registro 90117.",
    ),
    OrderCase(
        "¿Qué reporté después de la recogida? ¿Cuál fue el número de registro?",
        "pickup.completed",
        "shipment",
        "El cliente reportó una etiqueta ilegible; registro 41009.",
        "Después de la recogida, el seguimiento dejó de actualizarse; registro 53318.",
    ),
    OrderCase(
        "¿Qué reporté después de la visita del técnico? ¿Cuál fue el número de registro?",
        "field_visit.completed",
        "ticket",
        "El cliente reportó que internet se cortaba de noche; registro 38275.",
        "Después de la visita del técnico, el router se reiniciaba solo; registro 80623.",
    ),
]


def order_memory(case: OrderCase, milestone_days: float = 12.6) -> tuple[list[Item], tuple[SystemMark, ...]]:
    """The customer's memory in the dataset's shape, with the milestone as the ERP object's last event."""
    obj = BusinessObject(
        object_id=uuid4(),
        key=ObjectKey(case.object_type, "erp", "21889"),
        state={"status": "completed", "last_event_type": case.canonical_type},
        as_of=ago(milestone_days),
        source_id=uuid4(),
    )
    items = [
        Item("episode:profile", ago(72), "Cadastro atualizado no CRM."),
        Item("episode:before", ago(22.8), case.before),
        Item(
            f"object:{obj.object_id}",
            obj.as_of,
            f"{case.object_type} 21889 · status: completed",
            obj.object_id,
        ),
        Item("episode:after", ago(4.5), case.after),
        Item("value:after", ago(4.5), "registro 88280"),
        Item(
            "episode:filler-old", ago(57.3), "A loja abre no feriado? Abre em horário reduzido, aviso 83658."
        ),
        Item(
            "episode:filler-recent",
            ago(8.9),
            "Posso trocar o tamanho? A política de troca 68245 vale 30 dias.",
        ),
    ]
    return items, marks_from_objects([obj])


@pytest.mark.parametrize("case", ORDER_CASES, ids=lambda c: f"{c.canonical_type}:{c.question[:24]}")
def test_an_order_and_time_question_reads_what_came_after_the_milestone(case: OrderCase) -> None:
    items, marks = order_memory(case)
    result = run(case.question, items, marks)

    assert result.resolved is not None
    assert result.resolved.phrase.relation is Relation.AFTER
    assert result.resolved.mark.canonical_type == case.canonical_type
    # The report after the milestone ranks first: it names the milestone itself.
    assert result.hits[0].item_id == "episode:after"
    assert {h.item_id for h in result.hits} == {"episode:after", "value:after", "episode:filler-recent"}
    # The report before it and the ERP's own record are out: the agent answered with the ERP number before.
    object_id = next(i.item_id for i in items if i.item_id.startswith("object:"))
    assert {"episode:before", object_id, "episode:filler-old", "episode:profile"} <= result.outside
    assert result.analysis()["anchor"] == {
        "relation": "after",
        "event": result.resolved.phrase.name,
        "resolved": True,
        "event_type": case.canonical_type,
        "event_at": ago(12.6).isoformat(),
    }


@pytest.mark.parametrize("case", ORDER_CASES[:5], ids=lambda c: c.canonical_type)
def test_before_the_milestone_reads_the_earlier_report_first(case: OrderCase) -> None:
    items, marks = order_memory(case)
    question = case.question.replace("depois", "antes")
    result = run(question, items, marks)
    assert result.resolved is not None
    assert result.resolved.phrase.relation is Relation.BEFORE
    assert result.hits[0].item_id == "episode:before"
    assert {"episode:after", "value:after"} <= result.outside


def test_since_reads_the_most_recent_first() -> None:
    items = [
        Item("episode:a", ago(9), "Pediu segunda via."),
        Item("episode:b", ago(2), "Reclamou de lentidão."),
        Item("episode:c", ago(20), "Contratou o plano."),
    ]
    marks = [SystemMark("installation.completed", ago(10))]
    result = run("Desde a instalação o que eu reclamei?", items, marks)
    assert [h.item_id for h in result.hits] == ["episode:b", "episode:a"]
    assert result.outside == {"episode:c"}


def test_an_anchor_without_its_event_filters_nothing() -> None:
    items, _ = order_memory(ORDER_CASES[0])
    result = run(ORDER_CASES[0].question, items, [SystemMark("order.delivered", ago(3))])
    assert result.anchor is not None
    assert result.resolved is None
    assert result.hits == ()
    assert result.outside == frozenset()
    assert not result.outside
    assert result.analysis() == {"anchor": {"relation": "after", "event": "inspection", "resolved": False}}


def test_a_calendar_window_boosts_and_never_filters() -> None:
    items = [
        Item("episode:last-week", datetime(2026, 9, 16, 15, tzinfo=UTC), "Fatura veio alta."),
        Item("episode:this-week", datetime(2026, 9, 23, 15, tzinfo=UTC), "Pediu segunda via."),
        Item("episode:old", ago(60), "Mudou de plano."),
    ]
    result = run("O que eu falei na semana passada?", items, [])
    assert [h.item_id for h in result.hits] == ["episode:last-week"]
    assert result.outside == frozenset()
    assert result.anchor is None
    assert set(result.analysis()) == {"window"}


def test_a_calendar_word_narrows_an_anchored_window() -> None:
    items = [
        Item("episode:august", datetime(2026, 8, 20, 15, tzinfo=UTC), "Depois da entrega, faltou um item."),
        Item("episode:september", datetime(2026, 9, 10, 15, tzinfo=UTC), "Depois da entrega, veio quebrado."),
    ]
    marks = [SystemMark("order.delivered", datetime(2026, 8, 1, tzinfo=UTC))]
    result = run("O que eu relatei depois da entrega, no mês passado?", items, marks)
    assert [h.item_id for h in result.hits] == ["episode:august"]
    assert result.outside == {"episode:september"}


# Every question of the dataset's other categories, in its three shapes, and a few more: none of them has a
# time word the channel reads, so the channel returns nothing and the other channels decide as before.
UNTOUCHED = [
    "Oi, estou ligando por causa do que eu escrevi no WhatsApp. Até que dia eu disse que precisava da "
    "solução?",
    "Hi, I'm calling about what I wrote on WhatsApp. By what day did I say I needed it solved?",
    "Hola, llamo por lo que escribí en WhatsApp. ¿Hasta qué día dije que necesitaba la solución?",
    "Oi, mandei um e-mail para vocês esses dias. Qual é o número de protocolo que me passaram?",
    "Hi, I sent you an email the other day. What protocol number did you give me?",
    "Hola, les mandé un correo el otro día. ¿Qué número de protocolo me dieron?",
    "Quantas vezes eu já reclamei da internet caindo com vocês antes desta ligação?",
    "Quantas vezes eu já reclamei de item faltando na entrega com vocês antes desta ligação?",
    "How many times have I complained about failed delivery attempts before this call?",
    "How many times have I complained about my card being declined before this call?",
    "¿Cuántas veces me quejé de la entrega fallida antes de esta llamada?",
    "O estorno que vocês me prometeram saiu? Qual é o número do comprovante?",
    "Did the shipping refund you promised go through? What is the receipt number?",
    "¿Salió el reembolso que me prometieron? ¿Cuál es el número del comprobante?",
    "Oi, você pode me dizer qual é o número de documento que está no meu cadastro?",
    "Hi, can you tell me what my overdraft balance is?",
    "Hola, ¿me puede decir qué diagnóstico está registrado en mi ficha?",
    "Qual é o CEP de coleta que vocês têm hoje no meu cadastro?",
    "Qual é o CEP de entrega do cartão que vocês têm hoje no meu cadastro?",
    "What is the ZIP code of the installation address you have on file now?",
    "¿Cuál es el código postal de entrega que tienen hoy en mi ficha?",
    "Quero a segunda via da fatura",
    "Can you find out where my order is? I need to set up the router.",
    "May I change the delivery address?",
]


@pytest.mark.parametrize("question", [*UNTOUCHED, "", None])
def test_a_question_without_time_words_is_left_to_the_other_channels(question: str | None) -> None:
    case = ORDER_CASES[2]
    items, marks = order_memory(case)
    marks = (*marks, SystemMark("installation.completed", ago(3)), SystemMark("invoice.issued", ago(1)))
    result = run(question, items, marks)
    assert result == TemporalResult()
    assert result.analysis() == {}
    assert not result.outside


def test_the_same_input_ranks_the_same_whatever_the_order_of_the_items() -> None:
    items, marks = order_memory(ORDER_CASES[0])
    forward = run(ORDER_CASES[0].question, items, marks)
    backward = run(ORDER_CASES[0].question, list(reversed(items)), list(reversed(marks)))
    assert forward == backward


def test_the_compilers_candidates_are_items_of_the_channel() -> None:
    object_id = uuid4()
    candidate = Candidate(
        item_id=f"object:{object_id}",
        kind=ItemKind.OBJECT,
        section=Section.OBJECTS,
        attrs=ItemAttrs("object", "orders"),
        at=ago(1),
        text="order 71333 · status: delivered",
        masked_text="order",
        object_id=object_id,
    )
    marks = [SystemMark("order.delivered", ago(5))]
    result = temporal_channel(
        "since the delivery", [candidate], lexicon=LEXICON, marks=marks, reference=NOW, timezone=TZ
    )
    assert [h.item_id for h in result.hits] == [candidate.item_id]


def test_the_channel_fits_its_share_of_the_read_budget() -> None:
    # The design gives exact, lexical and temporal channels 3 ms together at p50 (11, 5). A customer
    # with 300 items, a third of them after the anchor, each naming it: the slowest shape of the channel.
    marks = [SystemMark("inspection.completed", ago(30))] * 20
    text = "O cliente relatou que, depois da vistoria, apareceu um barulho na suspensão; registro 88280."
    items = [Item(f"episode:{n}", ago(n * 0.3), text) for n in range(300)]
    question = ORDER_CASES[0].question
    run(question, items, marks)
    timings = []
    for _ in range(15):
        started = time.perf_counter()
        run(question, items, marks)
        timings.append(time.perf_counter() - started)
    within("the temporal channel over 300 items, median", statistics.median(timings), 0.005)
