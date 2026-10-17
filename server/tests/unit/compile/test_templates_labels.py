"""Every fact kind the product ships has a label in every language a pack is written in, and a fact line
never shows its key in another language."""

import pytest

from niadra.config.starter import STARTER_EXTRACTION
from niadra.domain.compile.templates import FACT_LABELS, Formatter
from niadra.testing.cell import sample_config

LANGUAGES = ("pt", "en")
KNOWN = sorted(
    {p.name for p in STARTER_EXTRACTION.predicates} | {p.name for p in sample_config().extraction.predicates}
)


@pytest.mark.parametrize("predicate", KNOWN)
def test_every_known_fact_kind_has_a_label_in_every_language(predicate: str) -> None:
    labels = FACT_LABELS.get(predicate, {})
    assert {lang: bool(labels.get(lang)) for lang in LANGUAGES} == dict.fromkeys(LANGUAGES, True)


def test_a_portuguese_pack_labels_facts_in_portuguese_and_a_spaces_own_kind_stays_readable() -> None:
    pt = Formatter("America/Sao_Paulo", "pt-BR")
    assert pt.value("product_or_service_used") == "produto ou serviço"
    assert pt.value("order_problem") == "problema no pedido"
    assert Formatter("UTC", "en-US").value("product_or_service_used") == "product or service"
    assert pt.value("guincho_contratado") == "guincho contratado"
