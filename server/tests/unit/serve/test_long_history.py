"""Long histories and questions memory has no answer for: an old conversation that answers the
turn is never dropped for a value the body holds of another matter, in whatever language each was said; the
conversation the turn is about keeps other matters' numbers out of the slots; of two postal codes, the one
that holds now answers "the one you have today"."""

from niadra.domain.serve.slots import ChannelHits
from tests.unit.serve.test_slots import FMT, item, plan

TV_PACK = item(
    1,
    "24/09/2026 · whatsapp · Cliente pediu o cancelamento do pacote de canais de TV. O agente informou que o "
    "cancelamento ocorrerá a partir do próximo mês e forneceu o protocolo 12863. · resolvido",
    days=1,
    in_body=True,
)
FIBER = item(
    2,
    "17/08/2026 · email · Cliente relata que a fibra perde sinal após as 20h e pede o registro do problema. "
    "O agente informa que registrou a ocorrência com o protocolo 20082. · não resolvido",
    days=39,
    channel="email",
)
DUE_DATE = item(
    3,
    "03/05/2026 · app · Cliente pediu para alterar a data de vencimento. O agente informou que a alterou "
    "para o dia solicitado e forneceu o protocolo 37409. · resolvido",
    days=145,
    channel="app",
)


def test_an_old_conversation_in_another_language_is_not_taken_for_the_bodys_protocol() -> None:
    # The body holds a protocol of another matter; the question, in English, is about the Portuguese line.
    question = (
        "A while ago I complained that the fiber loses signal after eight pm. What protocol number did you "
        "give me that time?"
    )
    got = plan(question, [TV_PACK, FIBER, DUE_DATE])
    assert got.item_ids[:1] == ("episode:0002",)
    assert "20082" in (got.render(FMT) or "")


def test_the_protocol_the_body_already_answers_with_still_supersedes_a_line_that_says_nothing_more() -> None:
    # "The protocol" alone: the body's answers, and an older line of no matter asked stays out.
    got = plan("Qual é o protocolo?", [TV_PACK, DUE_DATE])
    assert "episode:0003" not in got.item_ids


FIBER_EMAIL = item(
    4,
    "17/09/2026 · email · Gustavo Oliveira escreveu sobre o chamado 65547: a fibra fica sem sinal depois das "
    "oito. A empresa recebeu a mensagem e vai analisar o caso. · pendente",
    days=8,
    channel="email",
    in_body=True,
)
INTERNET_NIGHTS = item(
    5,
    "09/09/2026 · whatsapp · Cliente relata que a internet está caindo toda noite. Atendimento registrado "
    "com protocolo 77277. · pendente",
    days=16,
)


def test_a_conversation_sharing_only_a_synonym_with_the_asked_one_stays_out() -> None:
    # "internet caindo" is the same kind of trouble as "fibra sem sinal", but another conversation: its
    # protocol beside the e-mail the customer asks about was given as the answer (pt-151).
    question = (
        "Mandei um e-mail sobre o chamado porque a fibra fica sem sinal depois das oito. Qual é o número "
        "desse registro e qual protocolo vocês me passaram na resposta?"
    )
    text = plan(question, [FIBER_EMAIL, INTERNET_NIGHTS]).render(FMT) or ""
    assert "não há protocolo" in text
    assert "65547" in text
    assert "77277" not in text


TOW_EMAIL = item(
    6,
    "18/09/2026 · email · Emily Carter escreveu sobre claim 87345: o caminhão de reboque não chegou no "
    "horário. O agente confirmou o recebimento da mensagem e disse que investigaria. · pendente",
    days=7,
    channel="email",
    in_body=True,
)
GARAGE = item(
    7,
    "19/08/2026 · app · The customer reported that the partner garage does not have the part. The report was "
    "logged under protocol 93658. · não resolvido",
    days=37,
    channel="app",
)


def test_the_channel_the_customer_wrote_on_names_the_conversation_with_one_word_of_its_matter() -> None:
    # "I emailed you" names the e-mail; its summary says "reboque" and nothing else of the question (en-147).
    question = (
        "I emailed you about my claim because the tow truck did not show up on time. What is its number, and "
        "what protocol number did you give me in your reply?"
    )
    text = plan(question, [TOW_EMAIL, GARAGE]).render(FMT) or ""
    assert "na conversa por email de 18/09/2026" in text
    assert "93658" not in text


TRANSFER_EMAIL = item(
    8,
    "24/09/2026 · email · Ethan Keller escreveu sobre a disputa 81802 porque o app bloqueou sua "
    "transferência. O agente confirmou o recebimento e disse que verificaria o caso. · pendente",
    days=1,
    channel="email",
    in_body=True,
)
CARD_DECLINED = item(
    9,
    "06/09/2026 · whatsapp · Customer reported that their card was declined at the grocery store. The "
    "protocol number for this is 94171. · não resolvido",
    days=19,
)


def test_an_app_that_did_something_is_not_the_channel_the_customer_wrote_on() -> None:
    # "the app blocked my transfer" names the app as what failed, not where the customer wrote (en-153).
    question = (
        "I emailed you about my dispute because the app blocked my transfer. What is its number, and what "
        "protocol number did you give me in your reply?"
    )
    text = plan(question, [TRANSFER_EMAIL, CARD_DECLINED]).render(FMT) or ""
    assert "na conversa por email de 24/09/2026" in text
    assert "94171" not in text


OLD_ZIP = item(
    10,
    "15/06/2026 · app · Cliente informou que o CEP de entrega do cartão é 68822-590 para atualização. O "
    "agente confirmou que foi atualizado. · resolvido",
    days=102,
    channel="app",
)
NEW_ZIP = item(
    11,
    "14/07/2026 · voice · A cliente mudou de endereço e informou que o CEP de entrega do cartão agora é "
    "87305-667. O agente atualizou para 87305-667. · resolvido",
    days=73,
    channel="voice",
)
NEW_ZIP_VALUE = item(
    12,
    "CEP 87305-667 · A cliente mudou de endereço · voice · 14/07/2026",
    days=73,
    channel="voice",
    kind="value",
    value_type="postal_code",
)


def test_the_postal_code_held_today_never_comes_with_the_one_it_replaced() -> None:
    # The older line names its code beside the word "CEP"; the newer one only through its settled value, the
    # line an agent then passed over for the replaced one (pt-138).
    values = (ChannelHits("values", (NEW_ZIP_VALUE.item_id,), (NEW_ZIP_VALUE,)),)
    pool = [OLD_ZIP, NEW_ZIP]
    today = plan("Qual é o CEP de entrega do cartão que vocês têm hoje no meu cadastro?", pool, extra=values)
    text = today.render(FMT) or ""
    assert "87305-667" in text
    assert "68822-590" not in text
    before = plan("Qual era o meu CEP antigo de entrega do cartão?", pool, extra=values)
    assert "68822-590" in (before.render(FMT) or "")
