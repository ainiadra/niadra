from collections.abc import Sequence

import pytest

from niadra_models.capabilities import Span
from niadra_models.rules.pii import (
    LayeredPiiDetector,
    RulePiiDetector,
    cnpj_valid,
    cpf_valid,
    find_spans,
    iban_valid,
    luhn_valid,
)


def found(text: str) -> list[tuple[str, str]]:
    return [(text[s.start : s.end], s.kind) for s in find_spans(text)]


POSITIVES = [
    ("Meu CPF é 529.982.247-25", "529.982.247-25", "cpf"),
    ("cpf: 111.444.777-35", "111.444.777-35", "cpf"),
    ("CPF 52998224725 ok", "52998224725", "cpf"),
    ("CNPJ 11.222.333/0001-81", "11.222.333/0001-81", "cnpj"),
    ("cnpj 11222333000181", "11222333000181", "cnpj"),
    ("novo CNPJ 12.ABC.345/01DE-35", "12.ABC.345/01DE-35", "cnpj"),
    ("cartão 4111 1111 1111 1111 validade 12/29", "4111 1111 1111 1111", "card"),
    ("card 5555-5555-5555-4444", "5555-5555-5555-4444", "card"),
    ("amex 3782 822463 10005", "3782 822463 10005", "card"),
    ("IBAN GB82 WEST 1234 5698 7654 32", "GB82 WEST 1234 5698 7654 32", "iban"),
    ("cuenta DE89370400440532013000", "DE89370400440532013000", "iban"),
    ("whats +55 11 98765-4321", "+55 11 98765-4321", "phone"),
    ("liga no (21) 3456-7890", "(21) 3456-7890", "phone"),
    ("meu cel 11987654321", "11987654321", "phone"),
    ("ligue 98765-4321", "98765-4321", "phone"),
    ("móvil +34 612 345 678", "+34 612 345 678", "phone"),
    ("celular antigo +55 11 9876-5432", "+55 11 9876-5432", "phone"),
    ("office +44 20 7946 0958", "+44 20 7946 0958", "phone"),
    ("call (415) 555-2671", "(415) 555-2671", "phone"),
    ("e-mail joão.silva+x@empresa.com.br!", "joão.silva+x@empresa.com.br", "email"),
    ("CEP 01310-100", "01310-100", "postal_code"),
    ("cep 01310100", "01310100", "postal_code"),
    ("C.P. 28001 Madrid", "28001", "postal_code"),
    ("ZIP 94105", "94105", "postal_code"),
    ("SW1A 1AA London", "SW1A 1AA", "postal_code"),
    ("Moro na Rua das Flores, 123 apto 45", "Rua das Flores, 123 apto 45", "address"),
    ("Avenida Brasil nº 1500, bloco B", "Avenida Brasil nº 1500, bloco B", "address"),
    ("Av. Paulista 1578", "Av. Paulista 1578", "address"),
    ("vivo en Calle Mayor 5, piso 2", "Calle Mayor 5, piso 2", "address"),
    ("ship to 221B Baker Street", "221B Baker Street", "address"),
    # Documents of other countries.
    ("SSN 123-45-6789 on file", "123-45-6789", "national_id"),
    ("my SIN is 130 692 544", "130 692 544", "national_id"),
    ("NI number AB 12 34 56 C", "AB 12 34 56 C", "national_id"),
    ("mi DNI es 12345678Z", "12345678Z", "national_id"),
    ("NIE X1234567L", "X1234567L", "national_id"),
    ("CURP GODE561231HDFRRN09", "GODE561231HDFRRN09", "national_id"),
    ("mi RUT 12.345.678-5", "12.345.678-5", "national_id"),
    ("Aadhaar 2345 6789 0124", "2345 6789 0124", "national_id"),
    ("NIF 123 456 789", "123 456 789", "national_id"),
    ("RG 12.345.678-9", "12.345.678-9", "national_id"),
    ("passaporte nº FT123456", "FT123456", "national_id"),
    ("mi cédula 1.023.456.789", "1.023.456.789", "national_id"),
    # Bank details: the agency and the account, and a random PIX key.
    ("agência 0001 conta corrente 12345-6", None, None),
    ("ag. 1234-5", "1234-5", "bank_account"),
    ("c/c 98765-4", "98765-4", "bank_account"),
    (
        "chave pix 123e4567-e89b-12d3-a456-426614174000",
        "123e4567-e89b-12d3-a456-426614174000",
        "bank_account",
    ),
]

NEGATIVES = [
    "doc 529.982.247-26",  # wrong check digit
    "111.111.111-11",  # repeated digits pass the arithmetic but are not issued
    "123.456.789-00",
    "CNPJ 11.222.333/0001-82",
    "4111 1111 1111 1112",  # fails Luhn
    "1234 5678 9012 3456",
    "GB82 WEST 1234 5698 7654 33",
    "pedido 1234567890",  # ten bare digits: an order number, not a landline
    "protocolo 20240512",
    "valor R$ 1.234,56 em 12/05/2024",
    "I waited 20 minutes on the road",
    "London sw1a 1aa is lowercase chat",
    "o produto custa 199,90 e chega em 3 dias",
    "número 00 11 22",
    "SSN 000-12-3456",  # no area 000
    "order 123 456 789",  # nine digits that fail Luhn are not a SIN
    "ref ab 12 34 56 c",  # lowercase: chat, not a NI number
    "code 12345678A",  # wrong DNI letter
    "RUT 12.345.678-9",  # wrong check digit
    "Aadhaar 2345 6789 0123",  # fails Verhoeff
    "NIF 123 456 788",
    "a conta ficou em 3 parcelas",
    "agendei para as 10",
]


@pytest.mark.parametrize(("text", "span", "kind"), POSITIVES)
def test_detects_valid_identifiers(text: str, span: str | None, kind: str | None) -> None:
    if span is None:
        return  # several spans: checked on their own below
    assert found(text) == [(span, kind)]


def test_an_agency_and_an_account_in_one_sentence() -> None:
    assert found("agência 0001 conta corrente 12345-6") == [
        ("0001", "bank_account"),
        ("12345-6", "bank_account"),
    ]


@pytest.mark.parametrize("text", NEGATIVES)
def test_ignores_lookalikes(text: str) -> None:
    assert found(text) == []


def test_check_digit_validators() -> None:
    assert cpf_valid("529.982.247-25")
    assert not cpf_valid("529.982.247-52")
    assert cnpj_valid("11.444.777/0001-61")
    assert cnpj_valid("12ABC34501DE35")
    assert not cnpj_valid("12.ABC.345/01DE-36")
    assert luhn_valid("4111111111111111")
    assert not luhn_valid("9111111111111111")  # no card network starts with 9
    assert iban_valid("DE89 3704 0044 0532 0130 00")
    assert not iban_valid("DE88 3704 0044 0532 0130 00")


def test_offsets_survive_accents_and_invisible_characters() -> None:
    text = "Olá, é você? Açaí\u200b CPF 529.982.247-25"
    (span,) = find_spans(text)
    assert text[span.start : span.end] == "529.982.247-25"


def test_valid_cpf_wins_over_phone_reading_of_the_same_digits() -> None:
    # These 11 digits are both a valid CPF and a valid Sao Paulo mobile; the CPF rule is listed first.
    assert found("11987654374") == [("11987654374", "cpf")]


@pytest.mark.parametrize(
    "text",
    ["ligue no +55 11 91234-522.", "ligue no (11) 91234-522", "call +44 20 12345-678", "+55 (11) 91234-522"],
)
def test_a_phone_number_written_in_groups_of_a_postal_code_is_a_phone(text: str) -> None:
    # Its last groups have the shape of a CEP; what a country or an area code leads is dialed, in any country.
    assert [kind for _, kind in found(text)] == ["phone"]


def test_a_postal_code_after_a_phone_number_is_still_one() -> None:
    text = "tel +55 11 3456-7890, CEP 01310-100; Rua Augusta 123 04551-000"
    assert found(text) == [
        ("+55 11 3456-7890", "phone"),
        ("01310-100", "postal_code"),
        ("Rua Augusta 123", "address"),
        ("04551-000", "postal_code"),
    ]


def test_several_kinds_in_one_text_are_sorted() -> None:
    text = "CPF 529.982.247-25, email a@b.co, tel (11) 98765-4321, CEP 01310-100"
    assert [k for _, k in found(text)] == ["cpf", "email", "phone", "postal_code"]


class FakeNer:
    version = "fake-ner"

    def __init__(self) -> None:
        self.seen: list[str] = []

    def detect(self, texts: Sequence[str]) -> list[list[Span]]:
        self.seen.extend(texts)
        out = []
        for t in texts:
            spans = [Span(t.index("Maria"), t.index("Maria") + 5, "person")] if "Maria" in t else []
            # Claims the blanked CPF area too; the rules layer must keep it.
            spans.append(Span(4, 18, "location"))
            out.append(spans)
        return out


def test_layered_detector_blanks_rule_spans_before_the_ner() -> None:
    ner = FakeNer()
    detector = LayeredPiiDetector(ner)
    text = "CPF 529.982.247-25 da Maria"
    (spans,) = detector.detect([text])
    assert ner.seen == ["CPF                da Maria"]
    assert [(text[s.start : s.end], s.kind) for s in spans] == [
        ("529.982.247-25", "cpf"),
        ("Maria", "person"),
    ]
    assert detector.version == "pii-rules@3+fake-ner"


def test_rule_detector_batches() -> None:
    assert RulePiiDetector().detect(["a@b.co", "nada"]) == [[Span(0, 6, "email")], []]
