from niadra.config.object_types import ObjectTypeRegistry
from niadra.config.observed import observed_words
from niadra.domain.objects.words import Words


def observed(type_name: str) -> dict[str, object]:
    return {
        "type": type_name,
        "ownership": "shared",
        "mirror_of": {"system": "court", "derived_by": "observation"},
        "fields": {"amount_due": {"type": "string", "label": "Amount due"}},
        "states": ["held", "open"],
    }


def test_a_types_own_word_wins_and_the_space_word_is_beneath() -> None:
    words = Words(
        {"amount_due": "Valor"},
        {"held": "retido", "open": "aberto"},
        {"hearing": Words({}, {"held": "realizada", "open": "aberta"})},
    )
    registry = ObjectTypeRegistry(types=[]).under([observed("hearing"), observed("court_fee")], words)
    hearing, fee = registry.get("hearing"), registry.get("court_fee")
    assert hearing is not None
    assert fee is not None
    assert hearing.value_word("held") == "realizada"
    assert hearing.value_word("open") == "aberta"
    assert fee.value_word("held") == "retido"
    assert fee.value_word("open") == "aberto"
    assert hearing.field_word("amount_due") == "Valor"
    assert registry.value_words()["hearing"]["held"] == "realizada"


def test_a_types_own_field_word_replaces_the_inferred_label() -> None:
    words = Words({"amount_due": "Valor"}, {}, {"court_fee": Words({"amount_due": "Valor da custa"}, {})})
    registry = ObjectTypeRegistry(types=[]).under([observed("hearing"), observed("court_fee")], words)
    assert registry.field_labels()["court_fee"]["amount_due"] == "Valor da custa"
    assert registry.field_labels()["hearing"]["amount_due"] == "Valor"


def test_the_observed_document_keeps_the_words_per_type_and_an_older_one_reads_as_before() -> None:
    document = {
        "words": {
            "fields": {"amount_due": "Valor"},
            "values": {"held": "retido"},
            "types": {"hearing": {"fields": {}, "values": {"held": "realizada"}}},
        }
    }
    words = observed_words(document)
    assert words.values == {"held": "retido"}
    assert words.types["hearing"].values == {"held": "realizada"}
    older = observed_words({"words": {"fields": {"a": "b"}, "values": {"held": "retido"}}})
    assert older.types == {}
    assert older.values == {"held": "retido"}
