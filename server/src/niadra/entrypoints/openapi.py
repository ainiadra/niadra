"""Writes the public OpenAPI documents the docs site and the SDKs are checked against (7.0).

    python -m niadra.entrypoints.openapi --target cell --out openapi/cell.json
    python -m niadra.entrypoints.openapi --target control --out openapi/control.json
    python -m niadra.entrypoints.openapi --target space-config --out openapi/space-config.json

No process serves `/openapi.json`; the document is a build artifact. The copies in `openapi/` are what
the SDKs mirror, and a test fails when they fall behind the code. `space-config` is the JSON schema of the
space document the control plane stores in each cell: the deploy treats a change to it as it treats a
migration, and never rolls a release that changed it back onto servers that cannot read what it wrote.
"""

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

from fastapi import FastAPI

from niadra.adapters.inbound.http import control, sso
from niadra.config.models import SpaceConfig
from niadra.contracts.common import Problem
from niadra.entrypoints.serve import ROUTERS, routers

# Docstrings cite the internal design document, e.g. "(5.11)" or "(06, 4.4)"; the public text doesn't.
_SECTION_REF = re.compile(r"\s*\((?:\d{2}, )?\d+(?:\.\d+)*(?:, \d+(?:\.\d+)*)*\)")


def public_text(node: Any) -> Any:
    if isinstance(node, dict):
        return {k: _clean(v) if k in ("description", "summary") else public_text(v) for k, v in node.items()}
    if isinstance(node, list):
        return [public_text(v) for v in node]
    return node


def _clean(value: Any) -> Any:
    return _SECTION_REF.sub("", value).strip() if isinstance(value, str) else public_text(value)


def problem_errors(document: dict[str, Any]) -> dict[str, Any]:
    """Declares the error body the API really sends: `application/problem+json`, not FastAPI's default."""
    schemas = document.setdefault("components", {}).setdefault("schemas", {})
    schemas["Problem"] = Problem.model_json_schema(ref_template="#/components/schemas/{model}")
    problem = {"application/problem+json": {"schema": {"$ref": "#/components/schemas/Problem"}}}
    for operations in document.get("paths", {}).values():
        for operation in operations.values():
            responses = operation.get("responses", {})
            if "422" in responses:
                responses["422"] = {
                    "description": "The request does not match the contract.",
                    "content": problem,
                }
    for unused in ("HTTPValidationError", "ValidationError"):
        schemas.pop(unused, None)
    return document


def hoist_defs(document: dict[str, Any]) -> dict[str, Any]:
    """Moves the `$defs` Pydantic nests inside some schemas up to `components.schemas`.

    Pydantic points at them with `#/$defs/<name>`, which only resolves inside the schema that
    holds them; OpenAPI tools resolve `#` against the whole document. The nested definitions
    are the same models the components already have, so the names do not collide.
    """
    schemas = document.setdefault("components", {}).setdefault("schemas", {})

    def local(ref: Any) -> Any:
        if isinstance(ref, str) and ref.startswith("#/$defs/"):
            return "#/components/schemas/" + ref.removeprefix("#/$defs/")
        return ref

    def walk(node: Any) -> None:
        if isinstance(node, list):
            for item in node:
                walk(item)
            return
        if not isinstance(node, dict):
            return
        if "$ref" in node:
            node["$ref"] = local(node["$ref"])
        mapping = node.get("discriminator", {}).get("mapping")
        if isinstance(mapping, dict):
            for key, ref in mapping.items():
                mapping[key] = local(ref)
        for name, schema in node.pop("$defs", {}).items():
            walk(schema)
            schemas.setdefault(name, schema)
        for value in node.values():
            walk(value)

    walk(document.get("paths", {}))
    for schema in list(schemas.values()):
        walk(schema)
    return document


def cell_document() -> dict[str, Any]:
    app = FastAPI(title="Niadra API", version="1")
    seen: set[int] = set()
    for role in ROUTERS:
        for router in routers(role):
            if id(router) not in seen:
                seen.add(id(router))
                app.include_router(router)
    return app.openapi()


def control_document() -> dict[str, Any]:
    app = FastAPI(title="Niadra control plane", version="1")
    app.include_router(control.router)
    app.include_router(sso.router)
    return app.openapi()


def published(target: str) -> str:
    if target == "space-config":
        schema = SpaceConfig.model_json_schema()
        return json.dumps(schema, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    raw = cell_document() if target == "cell" else control_document()
    document = public_text(hoist_defs(problem_errors(raw)))
    # Sorted keys keep the file stable between builds, so a diff shows only real contract changes.
    return json.dumps(document, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(prog="niadra-openapi")
    parser.add_argument("--target", choices=["cell", "control", "space-config"], required=True)
    parser.add_argument("--out", type=Path, help="write to this file; stdout when absent")
    args = parser.parse_args()
    text = published(args.target)
    if args.out is None:
        sys.stdout.write(text)
        return
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(text)


if __name__ == "__main__":
    main()
