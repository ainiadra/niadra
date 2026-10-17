"""The lexicon the slot channels read turns and lines through: stems, concepts, units (no model)."""

from uuid import UUID

import pytest

from niadra.config.models import EpisodeCategory, SpaceConfig, SynonymGroup
from niadra.config.starter import STARTER_SYNONYMS
from niadra.domain.serve.lexicon import (
    CATEGORY_KEY,
    CONCEPT_KEY,
    EMPTY,
    WORD_KEY,
    Concept,
    Lexicon,
    stem,
)
from niadra.domain.serve.slots import turn_units
from niadra.domain.serve.turn import analyze

SPACE = UUID("0192f5a0-0000-7000-8000-00000000aa01")


def config(**kwargs: object) -> SpaceConfig:
    return SpaceConfig(space_id=SPACE, tenant_id=SPACE, region="us-east-2", environment="sandbox", **kwargs)  # type: ignore[arg-type]


STARTER = config().lexicon()


@pytest.mark.parametrize(
    "words",
    [
        ("recusado", "recusou", "recusada"),
        ("dropped", "dropping", "drops", "drop"),
        ("entrega", "entregar", "entregou"),
        ("declined", "decline"),
        ("items", "item"),
        ("instalacao", "instalar"),
    ],
)
def test_inflections_of_one_word_meet(words: tuple[str, ...]) -> None:
    assert len({stem(w) for w in words}) == 1


def test_different_words_stay_apart_and_numbers_are_their_own_stem() -> None:
    assert stem("missing") == "miss"  # a doubled letter stays when the stem would be too short
    assert stem("17674") == "17674"
    assert stem("wifi") == "wifi"  # short words are never cut


def test_a_line_answers_with_its_stems_concepts_and_category() -> None:
    keys = STARTER.keys("Quando o técnico vier, a portaria libera com o código 17674.", "technical")
    assert {CONCEPT_KEY + "technician", CONCEPT_KEY + "doorman", CONCEPT_KEY + "access_code"} <= keys
    assert WORD_KEY + "17674" in keys
    assert CATEGORY_KEY + "technical" in keys
    assert CATEGORY_KEY + "technical" not in STARTER.keys("Quando o técnico vier.")


def test_a_phrase_across_stop_words_is_one_unit() -> None:
    units = turn_units(STARTER, analyze("Once more the card did not go through, how many times?"))
    concepts = [u.concepts for u in units]
    assert (CONCEPT_KEY + "declined",) in concepts
    assert all(u.word != WORD_KEY + "through" for u in units)


def test_a_turn_in_other_words_meets_the_line_in_the_three_languages() -> None:
    line = STARTER.keys("The code on the box label is 50847.")
    for question in (
        "What number was printed on the package sticker, as I told you?",
        "Que número estava no rótulo da embalagem?",
        "¿Qué número tenía la pegatina del paquete?",
    ):
        units = turn_units(STARTER, analyze(question))
        assert sum(u.matched_by(line) for u in units) >= 2, question


def test_without_a_lexicon_a_turn_is_its_content_words() -> None:
    units = turn_units(EMPTY, analyze("Qual o protocolo da internet?"))
    assert [u.word for u in units] == [WORD_KEY + "protocol", WORD_KEY + "internet"]
    assert all(u.concepts == () for u in units)


def test_the_space_configures_its_groups_and_its_own_categories_become_words() -> None:
    starter = config()
    assert [g.name for g in starter.synonyms] == [g.name for g in STARTER_SYNONYMS]
    own = config(
        synonyms=[SynonymGroup(name="roaming", terms={"pt": ["roaming", "uso no exterior"]})],
        extraction=starter.extraction.model_copy(
            update={"episode_categories": [EpisodeCategory(name="guincho", normalized_as="guincho")]}
        ),
    )
    lexicon = own.lexicon()
    assert CONCEPT_KEY + "roaming" in lexicon.keys("Cobraram uso no exterior")
    units = turn_units(lexicon, analyze("Quantas vezes pedi guincho?"))
    assert any(CATEGORY_KEY + "guincho" in u.categories for u in units)
    assert CONCEPT_KEY + "doorman" not in lexicon.keys("o porteiro")  # the starter's groups were replaced


def test_the_lexicon_is_built_once_per_distinct_list() -> None:
    concepts = [Concept("tow", ("guincho", "tow truck"))]
    assert Lexicon.build(concepts) is Lexicon.build(list(concepts))
    assert config().lexicon() is config().lexicon()
