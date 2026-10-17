"""The product's word lists for the categories the law calls sensitive, as a customer types them."""

import pytest

from niadra.domain.policy.sensitive import (
    PRODUCT_SENSITIVE_CATEGORIES,
    SENSITIVE_TERMS,
    is_product_sensitive,
    sensitive_topics,
)


def test_every_sensitive_category_has_words_and_nothing_else_does() -> None:
    assert set(SENSITIVE_TERMS) == PRODUCT_SENSITIVE_CATEGORIES


@pytest.mark.parametrize(
    ("text", "category"),
    [
        ("Vocês já têm o resultado do meu exame de sangue?", "health"),
        ("Minha mãe está internada na UTI desde ontem", "health"),
        ("Estou fazendo quimioterapia e não consigo ir à loja", "health"),
        ("Tenho diabetes e preciso do reembolso do remédio", "health"),
        ("Fui ao pronto-socorro e o plano de saúde não cobriu", "health"),
        ("Fiquei internado no hospital da rede semana passada", "health"),
        ("I was hospitalized last week", "health"),
        ("Estoy embarazada y necesito cambiar la fecha de entrega", "health"),
        ("Sou evangélica e não trabalho aos sábados", "religion"),
        ("Pago o dízimo na igreja todo mês", "religion"),
        ("Votei no candidato do partido e agora me arrependo", "political_opinion"),
        ("Sou filiado ao partido desde 2010", "political_opinion"),
        ("A contribuição sindical saiu do meu salário", "union_membership"),
        ("Sou sindicalizado e o sindicato cobre o seguro", "union_membership"),
        ("Sou gay e quero incluir meu marido como dependente", "sexual_orientation"),
        ("Minha filha é não binária, podem trocar o nome no cadastro?", "sexual_orientation"),
        ("É um produto para disfunção erétil, quero discrição na entrega", "sexual_life"),
        ("Sofri racismo na loja do shopping", "racial_or_ethnic_origin"),
        ("Sou indígena e moro na aldeia, a entrega chega lá?", "racial_or_ethnic_origin"),
        ("O resultado do teste de DNA chegou?", "genetic"),
        ("Não autorizo o uso dos meus dados biométricos", "biometric"),
        ("Pediram minha certidão de antecedentes criminais na contratação", "criminal_record"),
        ("Fui preso por engano e perdi a data do pagamento", "criminal_record"),
        ("Sou maçom e a loja maçônica faz o pedido", "philosophical_belief"),
    ],
)
def test_portuguese_and_spanish_phrases_reach_their_category(text: str, category: str) -> None:
    assert category in sensitive_topics(text)


@pytest.mark.parametrize(
    "text",
    [
        "Que transtorno, o pedido atrasou de novo",
        "Por causa da greve dos Correios meu pedido não chegou",
        "Qual a raça do seu cachorro para a ração?",
        "Minha conta foi disabled depois da troca de senha",
        "O técnico fez o diagnóstico do roteador",
        "Meu nome é Cristiane e quero a segunda via",
        "Qual é a política de troca da loja?",
        "A biometria do aplicativo não reconhece meu rosto",
        "Liguei e ninguém atendeu",
        # What a broker sells and where it is served are not anyone's health.
        "Quero ver opções de plano de saúde para mim",
        "Tenho duas opções: o Clássico, que inclui o Hospital Santa Clara, e o Essencial",
        "I'd like a quote for a health plan, or health insurance",
        "Quiero cotizar un seguro de salud",
    ],
)
def test_ordinary_customer_service_talk_is_not_sensitive(text: str) -> None:
    assert sensitive_topics(text) == ()


def test_topics_come_in_name_order_whatever_the_casing_and_accents() -> None:
    assert sensitive_topics("CIRURGIA marcada na igreja?") == ("health", "religion")
    assert sensitive_topics("cirurgia marcada na igreja?") == sensitive_topics("Cirurgía marcada na Igreja?")
    assert sensitive_topics("") == ()
    assert sensitive_topics(None) == ()


@pytest.mark.parametrize(
    "name",
    [
        "Saúde",
        "Política",
        "sindicato",
        "vida sexual",
        "origem-étnica",
        "Religião",
        "salud",
        "exam_authorization",
    ],
)
def test_category_names_in_three_languages_are_sensitive(name: str) -> None:
    assert is_product_sensitive(name)


@pytest.mark.parametrize(
    "name", ["billing", "politica_de_troca", "raca", "technical_visit", "orders", "history", "health_plan"]
)
def test_ordinary_category_names_are_not(name: str) -> None:
    assert not is_product_sensitive(name)
