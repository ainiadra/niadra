"""The API ingress sends every public route to the deployable that serves it, and the local e2e's proxy
routes the same way.

Needs the server package: run it with the server's environment,
`uv run --directory ../server --with pyyaml pytest ../deploy/tests`.
"""

import re
import sys
from pathlib import Path

import pytest
import yaml

serve = pytest.importorskip("niadra.entrypoints.serve")
INFRA = Path(__file__).parents[1]
VALUES = INFRA / "k8s" / "charts" / "niadra" / "values.yaml"
BEHIND_THE_API_HOST = ("ingest", "read", "insights", "admin")
sys.path.insert(0, str(INFRA / "scripts"))
import local_cell  # noqa: E402


def test_the_proxy_routes_with_the_charts_paths() -> None:
    values = yaml.safe_load(VALUES.read_text())
    assert {k: tuple(v) for k, v in values["routes"].items()} == local_cell.ROUTES
    assert {k: tuple(v) for k, v in values["exactRoutes"].items()} == local_cell.EXACT_ROUTES


def test_the_ingress_sends_every_route_to_the_deployable_that_serves_it() -> None:
    for role in BEHIND_THE_API_HOST:
        for router in serve.routers(role):
            for route in router.routes:
                path = re.sub(r"\{[^}]+\}", "x", getattr(route, "path", ""))
                routed = local_cell.route(path)
                assert routed == role, f"{path} is served by {role} but the ingress sends it to {routed}"


def test_whole_paths_and_prefixes_match_as_traefik_does() -> None:
    assert local_cell.route("/v1/turns") == "ingest"
    assert local_cell.route("/v1/turns/0192abc") == "admin"
    assert local_cell.route("/v1/turns/search") == "admin"
    assert local_cell.route("/v1/objects/coverage") == "admin"
    assert local_cell.route("/v1/objects/push") == "ingest"
    assert local_cell.route("/v1/objects/item:store:1") == "read"
    assert local_cell.route("/mcp/insights") == "insights"
    assert local_cell.route("/mcpx") == "read"  # Traefik's PathPrefix is a plain string prefix
    assert local_cell.route("/v1/context") == "read"
    assert local_cell.route("/v1/context-use") == "admin"
    assert local_cell.route("/v1/context-use/conversations") == "admin"
