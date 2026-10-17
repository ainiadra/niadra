"""Declared operations may say what they mean, in the company's words (B1): the prompt shows the meaning next
to the name, which is what lets an item said in the company's language name an operation named in English."""

import pytest
from pydantic import ValidationError

from niadra.app.extract import rules_for
from niadra.config.models import DeclaredOperation, ExtractionSchema, SpaceConfig
from niadra.domain.extract.prompt import build_prompt
from niadra.domain.extract.rules import EXTRACTOR_VERSION, PROMPT_VERSION
from niadra.domain.vocabulary import Speaker
from niadra.testing.cell import WHATSAPP_SOURCE, sample_config
from tests.unit.extract.helpers import event, material

FILE = "protocolar uma peça ou petição no processo"


def with_operations(*operations: object) -> SpaceConfig:
    config = sample_config()
    extraction = config.extraction.model_copy(update={"operations": list(operations), "version": "s1"})
    return config.model_copy(update={"extraction": ExtractionSchema.model_validate(extraction.model_dump())})


def operations_line(config: SpaceConfig) -> str:
    m = material([event(Speaker.CUSTOMER, "Pode protocolar a réplica?", 1)])
    system = build_prompt(m, rules_for(config, WHATSAPP_SOURCE), flagged=frozenset(), spans={}).system
    return next(line for line in system.splitlines() if line.startswith("Operations: "))


def test_an_operation_is_a_name_or_a_name_with_its_meaning() -> None:
    schema = ExtractionSchema.model_validate(
        {"version": "s1", "operations": ["approve", {"name": "file", "description": f"  {FILE} "}]}
    )
    assert schema.operation_names() == ["approve", "file"]
    assert schema.operation_meanings() == {"file": FILE}
    assert schema.operations[1] == DeclaredOperation(name="file", description=FILE)
    with pytest.raises(ValidationError):
        ExtractionSchema.model_validate(
            {"version": "s1", "operations": ["file", {"name": "file", "description": FILE}]}
        )
    with pytest.raises(ValidationError):
        ExtractionSchema.model_validate(
            {"version": "s1", "operations": [{"name": "file", "description": "x" * 201}]}
        )


def test_a_document_of_bare_names_is_stored_as_before() -> None:
    """What a server one version behind reads: no meaning declared, nothing new in the document."""
    schema = ExtractionSchema.model_validate({"version": "s1", "operations": ["file", "approve"]})
    assert schema.model_dump(mode="json", exclude_defaults=True) == {
        "version": "s1",
        "operations": ["file", "approve"],
    }


def test_the_prompt_shows_each_meaning_next_to_its_operation() -> None:
    line = operations_line(with_operations("approve", {"name": "File", "description": FILE}))
    names = line.removeprefix("Operations: ").split(", ")
    assert f"file ({FILE})" in names  # by its canonical name
    assert "approve" in names
    assert "credit" in names  # the catalog's, which need no meaning


def test_meanings_extract_again_and_a_space_without_them_keeps_its_key() -> None:
    bare = rules_for(with_operations("file"), WHATSAPP_SOURCE)
    assert bare.versions_key == f"{EXTRACTOR_VERSION}.{PROMPT_VERSION}.s1"
    described = rules_for(with_operations({"name": "file", "description": FILE}), WHATSAPP_SOURCE)
    other = rules_for(with_operations({"name": "file", "description": "protocolar"}), WHATSAPP_SOURCE)
    assert described.versions_key.startswith(f"{bare.versions_key}.o")
    assert described.versions_key != other.versions_key
