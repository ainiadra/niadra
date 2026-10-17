"""Values bound to their matter and the honest negative: a value line says what it is about;
a number of another matter never answers alone; the conversation the turn is about says when it holds no
value of the kind asked; a value withheld until the verification is never "no record"; of two postal codes
the line says which holds now."""

from dataclasses import replace
from datetime import timedelta
from uuid import UUID

from niadra.domain.memory.values import SettledValue, ValueType
from niadra.domain.serve.slots import (
    SlotItem,
    UnseenValues,
    asked_conversation,
    unseen_values,
    value_hits,
    value_subject,
)
from niadra.domain.serve.turn import analyze
from niadra.domain.vocabulary import Speaker
from tests.unit.serve.test_slots import FMT, HISTORY, LEXICON, NOW, item, plan

DISPUTE = item(
    1,
    "23/09/2026 · email · Sabrina Vieira relatou que seu cartão foi recusado no mercado e escreveu sobre a "
    "contestação 12709. A equipe informou que recebeu a mensagem e vai analisar o caso. · pendente",
    days=2,
    channel="email",
)
JOINT = item(
    2,
    "16/09/2026 · whatsapp · Cliente perguntou se a empresa oferece conta conjunta. O agente informou que o "
    "formulário 41236 está no site. · resolvido",
    days=9,
)
QUESTION = (
    "Mandei um e-mail sobre a contestação porque meu cartão foi recusado no mercado. Qual é o número desse "
    "registro e qual protocolo vocês me passaram na resposta?"
)


def value(n: int, kind: ValueType, raw: str, episode: str, *, days: float, channel: str) -> SettledValue:
    return SettledValue(
        UUID(int=0x800 + n),
        UUID(int=9),
        kind,
        raw,
        NOW - timedelta(days=days),
        Speaker.AI_AGENT,
        UUID(int=0x300 + n),
        channel,
        UUID(int=11),
        NOW,
        episode_id=UUID(episode.partition(":")[2]),
    )


def pool_of(*items: SlotItem) -> list[SlotItem]:
    """The fixture items are keyed `episode:0001`; settled values name their episode by UUID."""
    return [replace(i, item_id=f"episode:{UUID(int=int(i.item_id.partition(':')[2]))}") for i in items]


def test_a_value_line_says_what_it_is_about() -> None:
    assert value_subject(JOINT.text) == "se a empresa oferece conta conjunta"
    assert value_subject(DISPUTE.text) == "seu cartão foi recusado no mercado"
    # A matter of a word says nothing the value does not: no subject.
    assert value_subject("21/09/2026 · whatsapp · Mudei. Agora o CEP é 45778-204.") is None


def test_the_conversation_the_turn_is_about_is_found_by_what_it_says() -> None:
    turn = analyze(QUESTION)
    pool = [DISPUTE, JOINT, *HISTORY]
    assert asked_conversation(turn, LEXICON, pool) == DISPUTE
    # A question that says nothing but the kind of value is about no conversation in particular.
    assert asked_conversation(analyze("Qual é o número de protocolo?"), LEXICON, pool) is None


def test_a_record_of_another_matter_never_answers_alone() -> None:
    pool = pool_of(DISPUTE, JOINT)
    record = value(1, ValueType.RECORD, "41236", pool[1].item_id, days=9, channel="whatsapp")
    turn = analyze(QUESTION)
    hits = value_hits([record], turn, pool, fmt=FMT, voice=False, lexicon=LEXICON)
    [line] = [i.line for i in hits.items]
    assert line == ("[Valor] registro 41236 · se a empresa oferece conta conjunta · whatsapp · 16/09/2026")
    got = plan(QUESTION, pool, extra=(hits,))
    negatives = [d.line for d in got.derived if d.kind == "no_record"]
    # The e-mail holds its own number (12709) but no protocol: said of that conversation, with its number.
    assert negatives == [
        "[Contexto] na conversa por email de 23/09/2026 (seu cartão foi recusado no mercado) não há "
        "protocolo; números citados nela: 12709"
    ]
    assert got.why()["derived"][0]["rule"] == "no_record_matter"
    # And the number of the other matter takes no slot beside it.
    assert f"value:{record.value_id}" not in got.item_ids


def test_the_matters_own_value_comes_first() -> None:
    pool = pool_of(DISPUTE, JOINT)
    other = value(1, ValueType.PROTOCOL, "55120", pool[1].item_id, days=1, channel="whatsapp")
    own = value(2, ValueType.PROTOCOL, "98429", pool[0].item_id, days=2, channel="email")
    hits = value_hits([other, own], analyze(QUESTION), pool, fmt=FMT, voice=False, lexicon=LEXICON)
    assert hits.ranked[0] == f"value:{own.value_id}"
    got = plan(QUESTION, pool, extra=(hits,))
    assert not [d for d in got.derived if d.kind == "no_record"]


def test_a_value_withheld_until_the_verification_is_never_no_record() -> None:
    question = "Oi, mandei um e-mail para vocês esses dias. Qual é o número de protocolo que me passaram?"
    pool = pool_of(JOINT)
    held = value(3, ValueType.PROTOCOL, "98429", f"episode:{UUID(int=7)}", days=4, channel="email")
    unseen = unseen_values([held], pool, frozenset({f"episode:{UUID(int=7)}"}))
    assert unseen == UnseenValues(withheld=(("protocol", "98429"),))
    got = plan(question, pool, unseen=unseen)
    assert [d.line for d in got.derived] == [
        "[Contexto] há protocolo registrado, retido até verificar a identidade: peça a verificação antes de "
        "informá-lo"
    ]
    # Before: "sem registro de protocolo no histórico deste cliente", which was false.
    before = plan(question, pool).derived[0].line
    assert before.endswith("sem registro de protocolo no histórico deste cliente")
    # By the number the customer names, too, without repeating it.
    named = plan("E o protocolo 98429?", pool, unseen=unseen)
    assert [d.rule for d in named.derived] == ["withheld_record"]
    assert "98429" not in named.derived[0].line


def test_a_value_kept_for_another_reason_silences_the_negative() -> None:
    question = "Qual é o número de protocolo que me passaram?"
    pool = pool_of(JOINT)
    kept = value(4, ValueType.PROTOCOL, "98429", f"episode:{UUID(int=8)}", days=4, channel="email")
    unseen = unseen_values([kept], pool, frozenset())
    assert unseen.hides(analyze(question))
    assert plan(question, pool, unseen=unseen).derived == ()


def test_of_two_postal_codes_the_line_says_which_holds_now() -> None:
    old = item(3, "25/07/2026 · app · Para atualizar: o CEP do endereço da apólice é 43937-544.", days=62)
    new = item(4, "16/08/2026 · whatsapp · Mudei de endereço. Agora o CEP é 66617-704.", days=40)
    pool = pool_of(old, new)
    values = [
        value(5, ValueType.POSTAL_CODE, "43937-544", pool[0].item_id, days=62, channel="app"),
        value(6, ValueType.POSTAL_CODE, "66617-704", pool[1].item_id, days=40, channel="whatsapp"),
    ]
    turn = analyze("Qual é o CEP do endereço da apólice que vocês têm hoje no meu cadastro?")
    hits = value_hits(values, turn, pool, fmt=FMT, voice=True, lexicon=LEXICON)
    assert [i.line for i in hits.items] == [
        "Valor: CEP atual 66617-704 · Mudei de endereço · whatsapp · 16/08/2026.",
        "Valor: CEP anterior 43937-544 · app · 25/07/2026.",
    ]
