"""The public documents name each schema by its model alone. Two models of one name in different modules
would publish as `niadra__contracts__<module>__<Model>`: a renamed schema, and a module path in public."""

import json
from pathlib import Path

import pytest

OPENAPI = Path(__file__).resolve().parents[3] / "openapi"
KNOWN = frozenset({"niadra__contracts__common__SourceCoverage", "niadra__contracts__ingest__SourceCoverage"})
"""Published before the rule; they stay as they are."""


@pytest.mark.parametrize("name", ["cell.json", "control.json"])
def test_no_schema_is_named_by_its_module_path(name: str) -> None:
    schemas = json.loads((OPENAPI / name).read_text())["components"]["schemas"]
    assert sorted({key for key in schemas if "__" in key} - KNOWN) == []


@pytest.mark.parametrize("name", ["cell.json", "control.json"])
def test_no_request_body_takes_the_framework_default_name(name: str) -> None:
    """A body of an annotated union is published inline and titled `Body`, and an SDK generator names its type
    after it: every body is a model of its own name, or an optional one."""
    document = json.loads((OPENAPI / name).read_text())
    named = [key for key in document["components"]["schemas"] if key == "Body" or key.startswith("Body_")]
    inline = [
        f"{method.upper()} {path}"
        for path, operations in document["paths"].items()
        for method, operation in operations.items()
        for content in operation.get("requestBody", {}).get("content", {}).values()
        if str(content.get("schema", {}).get("title", "")).startswith("Body")
        and not _one_model(content["schema"])
    ]
    assert (named, inline) == ([], [])


def _one_model(schema: dict[str, object]) -> bool:
    """A reference to one named model, or to one or nothing."""
    options = schema.get("anyOf")
    if not isinstance(options, list):
        return "$ref" in schema
    refs = [o for o in options if isinstance(o, dict) and "$ref" in o]
    return len(refs) == 1 and all(o in refs or o == {"type": "null"} for o in options)
