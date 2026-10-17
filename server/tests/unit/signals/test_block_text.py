"""The constraints block as text for a model: each field by its label and each operator in words, in the
space's language; what lost a conflict is left out, and the version never covers the text."""

import pytest

from niadra.domain.signals.render import version
from niadra.domain.signals.text import block_text

LABELS = {"health_plan.copay": "coparticipação", "health_plan.hospital": "rede"}


def hard(i: int, attr: str, op: str, values: list[object], **extra: object) -> dict[str, object]:
    return {
        "id": f"h{i}",
        "attr": attr,
        "op": op,
        "values": values,
        "source": "stated",
        "scope": "session",
    } | extra


@pytest.mark.parametrize(
    ("entry", "pt", "en"),
    [
        (
            hard(1, "health_plan.copay", "eq", [False]),
            "exigido: sem coparticipação",
            "required: without coparticipação",
        ),
        (
            hard(1, "health_plan.copay", "ne", [False]),
            "exigido: com coparticipação",
            "required: with coparticipação",
        ),
        (
            hard(1, "health_plan.monthly_price", "lte", [700]),
            "exigido: monthly price de no máximo 700",
            "required: monthly price at most 700",
        ),
        (
            hard(1, "health_plan.hospital", "in", ["Hospital Santa Clara"]),
            "exigido: rede igual a Hospital Santa Clara",
            "required: rede equal to Hospital Santa Clara",
        ),
        (
            hard(1, "item_variant.color", "not_in", ["preto", "marrom"]),
            "exigido: color sem nenhum de: preto, marrom",
            "required: color none of: preto, marrom",
        ),
        (
            hard(1, "item_variant.price_sale", "between", [100.0, 250.0], category="vestidos"),
            "exigido: price sale entre 100 e 250 (em vestidos)",
            "required: price sale between 100 and 250 (in vestidos)",
        ),
    ],
)
def test_a_hard_constraint_says_its_field_and_operator_in_words(
    entry: dict[str, object], pt: str, en: str
) -> None:
    block = {"hard": [entry]}
    assert block_text(block, LABELS, "pt") == f"<restrições>\n- {pt}\n</restrições>"
    assert block_text(block, LABELS, "en") == f"<constraints>\n- {en}\n</constraints>"


def test_soft_entries_attributes_exclusions_and_questions_each_have_a_line() -> None:
    block = {
        "soft": [{"id": "s1", "attr": "item_variant.color", "value": "azul", "polarity": "prefer"}],
        "attributes": [{"id": "z1", "name": "size.pants", "value": "40", "apply": "always"}],
        "exclude": ["item_variant:loja:991"],
        "ask": ["for_whom"],
    }
    assert block_text(block, LABELS, "pt") == (
        "<restrições>\n- prefere: color azul\n- pants: 40\n- não mostrar: item_variant:loja:991\n"
        "- perguntar antes de supor: for_whom\n</restrições>"
    )
    # A language no pack is written in reads as the pack beside it does, in English: one rule
    # (`domain.compile.templates.language`), never a block in one language and the pack in another.
    assert block_text(block, LABELS, "es") == block_text(block, LABELS, "en")


def test_what_lost_a_conflict_is_not_said_and_an_empty_block_says_nothing() -> None:
    block = {
        "hard": [
            hard(1, "item_variant.color", "eq", ["azul"]),
            hard(2, "item_variant.color", "eq", ["verde"]),
        ],
        "conflicts": [{"ids": ["h1", "h2"], "kept": "h2", "by": "current_utterance"}],
    }
    assert block_text(block, {}, "pt") == "<restrições>\n- exigido: color igual a verde\n</restrições>"
    assert block_text({"subject": {"for": "self"}}, LABELS, "pt") is None


def test_the_version_never_covers_the_text() -> None:
    block = {"hard": [hard(1, "health_plan.copay", "eq", [False])]}
    assert version(block) == version({**block, "text": block_text(block, LABELS, "pt")})


SHOWN = {
    "health_plan:operadora:essencial": {"monthly_price": 480.0, "copay": True},
    "health_plan:operadora:pleno": {"monthly_price": 690.0, "copay": False},
    "health_plan:operadora:premium": {"monthly_price": 820.0},
    "item_variant:loja:v-1": {"copay": False},
}


@pytest.mark.parametrize(
    ("entry", "lang", "line"),
    [
        (
            hard(1, "health_plan.copay", "eq", [False]),
            "pt",
            "- exigido: sem coparticipação; das opções mostradas, atende: pleno; não atende: essencial",
        ),
        (
            hard(1, "health_plan.monthly_price", "lte", [700]),
            "en",
            "- required: monthly price at most 700; of the options shown, meets it: essencial, pleno; "
            "does not: premium",
        ),
        (
            hard(1, "health_plan.hospital", "in", ["Hospital Santa Clara"]),
            "en",
            "- required: rede equal to Hospital Santa Clara",
        ),
    ],
)
def test_a_hard_constraint_says_which_options_shown_meet_it(
    entry: dict[str, object], lang: str, line: str
) -> None:
    # Only the options of the constraint's type shown with its field: none was shown with a hospital, and an
    # item of another type with the same field name is not an option.
    text = block_text({"hard": [entry]}, LABELS, lang, SHOWN)
    assert text is not None
    assert text.splitlines()[1] == line


def test_an_instruction_that_holds_with_one_person_is_required_only_if_that_person_speaks() -> None:
    # juridico-sandbox, 05 and 06/10/2026: "(com este cliente)", "(vale para quem ela nomeia)" and "(vale só
    # com Clara; não vale para quem fala, se for outra pessoa)" read as Marcos himself, and the attendant
    # refused him his own claim amount (0 of 5); the condition gave him the amount 7 of 8, Clara refused 8/8.
    told = {
        "id": "i1",
        "about": "01a10def-95b4-75a1-a43b-d21554f30542",
        "text": "Não falar de valores dos processos com Clara.",
        "strength": "must_not",
    }
    pt = block_text({"instructions": [told | {"only_with": "Clara"}]}, {}, "pt") or ""
    en = block_text({"instructions": [told | {"only_with": "Clara"}]}, {}, "en") or ""
    said = "- exigido: Não falar de valores dos processos com Clara."
    assert (
        "- exigido se quem fala for Clara: Não falar de valores dos processos com Clara. "
        "(Com qualquer outro contato, não se aplica.)"
    ) in pt
    assert "com este cliente" not in pt
    assert "required if the one speaking is Clara: " in en
    assert "(With any other contact, it does not apply.)" in en
    # Holding with no one in particular, it is the account's, and says no more.
    plain = block_text({"instructions": [told]}, {}, "pt") or ""
    assert f"{said} (instrução da conta)" in plain


def test_a_contact_preference_says_how_to_reach_someone_apart_from_what_is_required() -> None:
    # juridico-sandbox, 06/10/2026 (round 8): "exigido se quem fala for Marcos Lima: Marcos Lima: contatar
    # por WhatsApp e só depois das 10h" left the attendant refusing Marcos his own claim amount 4 of 5;
    # apart from the restrictions, he got it 5 of 5, Clara was still refused and the preference still kept.
    clara = {
        "id": "i1",
        "text": "Não falar de valores com Clara",
        "strength": "must_not",
        "only_with": "Clara",
    }
    marcos = {
        "id": "i2",
        "text": "Marcos Lima: contatar por WhatsApp e só depois das 10h",
        "strength": "must",
        "only_with": "Marcos Lima",
        "contact": True,
    }
    text = block_text({"instructions": [clara, marcos]}, {}, "pt") or ""
    restrictions, _, contact = text.partition("</restrições>")
    assert "Clara" in restrictions
    assert "Marcos" not in restrictions
    assert contact.strip().splitlines() == [
        "<preferências de contato>",
        "- Marcos Lima: contatar por WhatsApp e só depois das 10h (para quando a equipe procurar essa "
        "pessoa; não muda o que se pode responder a ela)",
        "</preferências de contato>",
    ]
    # Alone, it is the only section; with no person named, it says when the team reaches out.
    alone = block_text({"instructions": [marcos | {"only_with": None}]}, {}, "en") or ""
    assert alone.splitlines()[0] == "<contact preferences>"
    assert "restrições" not in alone
    assert "constraints" not in alone
    assert "(for when the team reaches out; it does not change what may be said)" in alone
