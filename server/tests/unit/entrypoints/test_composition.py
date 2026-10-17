"""What the deployables are made of: checked without starting any infrastructure."""

import json
import re
from collections import Counter
from pathlib import Path

from niadra.domain.config.signing import version_tuple
from niadra.domain.tasks import CATALOG
from niadra.entrypoints.cell import CELL_VERSION
from niadra.entrypoints.openapi import cell_document, control_document, hoist_defs, problem_errors, published
from niadra.entrypoints.serve import ROUTERS, routers, worker_modules


def test_the_cell_version_is_comparable_with_manifest_minimums() -> None:
    assert version_tuple(CELL_VERSION)


def test_no_route_is_served_by_two_deployables() -> None:
    routes = Counter(
        (method, route.path)
        for role in ROUTERS
        for router in routers(role)
        for route in router.routes
        for method in getattr(route, "methods", ())
    )
    assert [key for key, count in routes.items() if count > 1] == []


def test_every_task_in_the_catalog_has_a_handler() -> None:
    handled = {name for module in worker_modules() for name in module.handlers(_Everything())}
    assert set(CATALOG) - handled == set()


def test_the_public_documents_build_and_hide_nothing_internal() -> None:
    cell, control = cell_document(), control_document()
    assert cell["paths"]
    assert control["paths"]
    assert "/openapi.json" not in cell["paths"]


class _Everything:
    """Answers any service attribute, so `handlers(c)` can be listed without building services."""

    def __getattr__(self, name: str) -> "_Everything":
        return self

    def __call__(self, *args: object, **kwargs: object) -> "_Everything":
        return self


def test_the_exported_contracts_resolve_every_reference() -> None:
    for build in (cell_document, control_document):
        document = hoist_defs(problem_errors(build()))
        text = json.dumps(document)
        assert '"$defs"' not in text
        assert "#/$defs/" not in text
        schemas = document["components"]["schemas"]
        refs = re.findall(r'"#/components/schemas/([^"]+)"', text)
        assert refs
        assert all(ref in schemas for ref in refs)


def test_the_committed_openapi_documents_match_the_code() -> None:
    """The SDKs and the docs mirror `openapi/`; regenerate with `python -m niadra.entrypoints.openapi`."""
    root = Path(__file__).resolve().parents[3] / "openapi"
    for target in ("cell", "control", "space-config"):
        assert (root / f"{target}.json").read_text() == published(target), f"openapi/{target}.json is stale"


# How the build is planned (the fronts A1 to A5, their phases, the wave, the design study) is internal: the
# descriptions reach the SDKs, the docs and the open specs.
_PLANNING = re.compile(r"\bA[1-5]\b|(?i:\bfronts\b|\bphases?\s+\d|\bwave\b|\bstudy\s+\d)")


def _public_texts(node: object) -> list[str]:
    texts: list[str] = []
    if isinstance(node, dict):
        for key, value in node.items():
            if key in ("description", "summary", "title") and isinstance(value, str):
                texts.append(value)
            texts += _public_texts(value)
    elif isinstance(node, list):
        for value in node:
            texts += _public_texts(value)
    return texts


def test_no_public_description_names_the_internal_planning() -> None:
    for path in sorted((Path(__file__).resolve().parents[3] / "openapi").glob("*.json")):
        leaks = [text for text in _public_texts(json.loads(path.read_text())) if _PLANNING.search(text)]
        assert leaks == [], f"{path.name}: {leaks}"
    assert _PLANNING.search("its fields are the interaction spec's (front A5)")
    assert _PLANNING.search("What a space turns on of the agent core wave")
    assert not _PLANNING.search("a producer that puts a context pack in front of a model")
