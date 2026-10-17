"""The turn routes over HTTP (24.3.5, 3.7): compressed bodies, their limits, one error per refused turn,
the viewer's reads, and OTLP answered in the encoding it came in."""

import asyncio
import gzip
from dataclasses import dataclass, field
from typing import Any

import httpx
import orjson
import zstandard
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceResponse

from niadra.adapters.inbound.http import ingest, turns
from niadra.adapters.inbound.http.app import create_app
from niadra.adapters.inbound.http.bodies import LargeBodies
from niadra.adapters.memory.fakes import StaticConfig
from niadra.app.replay import ReplayService
from niadra.contracts.events import BatchResponse
from niadra.contracts.turns import MAX_DECODED_BYTES, TurnsResponse
from niadra.domain.common.errors import Unauthenticated
from niadra.domain.common.ids import uuid7
from niadra.domain.common.space import PersonPrincipal, SourcePrincipal
from niadra.domain.vocabulary import Scope
from niadra.testing.cell import WHATSAPP_SOURCE, principal
from tests.unit.turns.conftest import World
from tests.unit.turns.records import turn
from tests.unit.turns.test_otlp import TRACE, json_trace, proto_trace

T1, T2 = "0192f5a0-7c3e-7a10-9d2b-5e61a4c0d001", "0192f5a0-7c3e-7a10-9d2b-5e61a4c0d002"


class Edge:
    def __init__(self, world: World) -> None:
        self._source = principal(
            WHATSAPP_SOURCE, world.cell.config.space(world.caller.space.space_id), scopes=frozenset(Scope)
        )
        self._person = PersonPrincipal(world.caller.space, uuid7(), frozenset({"integration"}))

    async def authenticate_key(self, token: str) -> SourcePrincipal:
        if token != "nia_sk_all":
            raise Unauthenticated("unknown key")
        return self._source

    async def authenticate_person(self, token: str) -> PersonPrincipal:
        if token != "person":
            raise Unauthenticated("unknown token")
        return self._person

    async def check_rate(self, principal: Any, path: str) -> None:
        return None


class Messages:
    """The conversation's messages from the spans are the ingest module's; here they are accepted as none."""

    async def ingest_otlp(self, caller: SourcePrincipal, body: Any) -> BatchResponse:
        return BatchResponse(accepted=0, duplicates=0, errors=[])


@dataclass
class Container:
    edge: Edge
    config: StaticConfig
    turns: Any
    ingest: Messages
    replay: ReplayService
    large_bodies: LargeBodies = field(default_factory=lambda: LargeBodies(2, 1024 * 1024))


def _client(world: World) -> httpx.AsyncClient:
    replay = ReplayService(
        world.cell.uow,
        world.service,
        world.cell.objects,
        world.cell.clock,
        world.cell.config,
        world.audit,  # type: ignore[arg-type]
    )
    container = Container(Edge(world), world.cell.config, world.service, Messages(), replay)
    routers = (turns.router, turns.admin_router, ingest.router)
    app = create_app("niadra-test", container, routers)
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://cell")


SOURCE = {"authorization": "Bearer nia_sk_all"}
PERSON = {"authorization": "Bearer person"}


async def test_a_zstd_or_gzip_body_is_taken_and_a_bad_turn_is_one_error(world: World) -> None:
    body = orjson.dumps({"turns": [turn(T1), {**turn(T2), "content_mode": "raw"}]})
    async with _client(world) as client:
        zstd = await client.post(
            "/v1/turns",
            content=zstandard.ZstdCompressor().compress(body),
            headers={**SOURCE, "content-encoding": "zstd", "content-type": "application/json"},
        )
        again = await client.post(
            "/v1/turns",
            content=gzip.compress(body),
            headers={**SOURCE, "content-encoding": "gzip", "content-type": "application/json"},
        )
    assert zstd.status_code == 207
    assert zstd.json() == {
        "accepted": 1,
        "duplicates": 0,
        "errors": [{"index": 1, "code": "invalid_input", "detail": zstd.json()["errors"][0]["detail"]}],
    }
    assert "content_mode" in zstd.json()["errors"][0]["detail"]
    assert (again.status_code, again.json()["duplicates"]) == (207, 1)


async def test_a_body_over_its_limits_is_refused_as_too_large(world: World) -> None:
    padding = b" " * (17 * 1024 * 1024)
    inflating = zstandard.ZstdCompressor().compress(b'{"turns": [' + padding + b"]}")
    async with _client(world) as client:
        inflated = await client.post(
            "/v1/turns", content=inflating, headers={**SOURCE, "content-encoding": "zstd"}
        )
        heavy = await client.post("/v1/turns", content=b"x" * (4 * 1024 * 1024 + 1), headers=SOURCE)
        empty = await client.post("/v1/turns", json={"turns": []}, headers=SOURCE)
    assert (inflated.status_code, inflated.json()["code"]) == (413, "turn_too_large")
    assert (heavy.status_code, heavy.json()["code"]) == (413, "turn_too_large")
    assert (empty.status_code, empty.json()["code"]) == (422, "invalid_input")


async def test_the_viewer_reads_a_turn_by_id_and_searches_the_kept_tier(world: World) -> None:
    kept = turn(T2, flags=["handoff"])
    async with _client(world) as client:
        await client.post("/v1/turns", json={"turns": [turn(T1), kept]}, headers=SOURCE)
        promoted = await client.post(
            "/v1/turns/promote",
            json={"turn_ids": [T1], "reason": "complaint"},
            headers={**SOURCE, "idempotency-key": "p-1"},
        )
        one = await client.get(f"/v1/turns/{T1}", headers=PERSON)
        missing = await client.get("/v1/turns/never-sent", headers=PERSON)
        found = await client.post("/v1/turns/search", json={"conversation_id": "c-8812"}, headers=PERSON)
        no_key = await client.post(
            "/v1/turns/promote", json={"turn_ids": [T1], "reason": "complaint"}, headers=SOURCE
        )
    assert promoted.json() == {"promoted": 1, "already_kept": 0, "not_found": 0}
    assert (one.status_code, one.json()["tier"], one.json()["record"]["turn_id"]) == (200, "kept", T1)
    assert one.json()["record"]["blobs"]["b:1"]["content"]["plan"] == "family"
    assert missing.status_code == 404
    assert [i["turn_id"] for i in found.json()["items"]] == [T2, T1]
    assert no_key.status_code == 422, "promotion needs an Idempotency-Key"


async def test_otlp_is_answered_in_the_encoding_it_came_in(world: World) -> None:
    async with _client(world) as client:
        as_json = await client.post(
            "/v1/otel/v1/traces", content=json_trace(), headers={**SOURCE, "content-type": "application/json"}
        )
        as_proto = await client.post(
            "/v1/otel/v1/traces",
            content=gzip.compress(proto_trace()),
            headers={**SOURCE, "content-type": "application/x-protobuf", "content-encoding": "gzip"},
        )
        other = await client.post(
            "/v1/otel/v1/traces", content=b"{}", headers={**SOURCE, "content-type": "text/plain"}
        )
    assert as_json.json() == {"partialSuccess": {"rejectedSpans": 0, "errorMessage": ""}}
    assert as_proto.headers["content-type"] == "application/x-protobuf"
    answer = ExportTraceServiceResponse()
    answer.ParseFromString(as_proto.content)
    assert answer.partial_success.rejected_spans == 0
    assert other.status_code == 422
    [applied] = world.taken.turns
    assert applied.record.turn_id == f"otel-{TRACE}"


async def test_a_pin_mismatch_names_each_pin_in_the_problem_and_a_run_answers_its_verdict(
    world: World,
) -> None:
    await world.service.record(world.caller, [turn(T1)])
    build = {"pins": {"prompts": {"core": "v16"}, "model": "model-2026-09"}}
    async with _client(world) as client:
        stopped = await client.post("/v1/replay/cases", headers=PERSON, json={"turn_id": T1, "build": build})
        made = await client.post(
            "/v1/scenarios",
            headers={**PERSON, "idempotency-key": "s1"},
            json={"name": "quote", "turn_ids": [T1]},
        )
        scenario = made.json()
        results = [
            {"scenario_id": scenario["scenario_id"], "turn_id": T1, "run": run, "status": "completed"}
            for run in range(1, 6)
        ]
        run = await client.post(
            "/v1/scenario-runs",
            headers={**PERSON, "idempotency-key": "r1"},
            json={
                "scenario_ids": [scenario["scenario_id"]],
                "build": build,
                "vary": ["model"],
                "results": results,
            },
        )
        listed = await client.get("/v1/scenarios", headers=PERSON, params={"ids": scenario["scenario_id"]})

    assert stopped.status_code == 422
    assert stopped.headers["content-type"] == "application/problem+json"
    assert stopped.json()["code"] == "pin_mismatch"
    assert stopped.json()["pins"] == [
        {"name": "model", "recorded": "model-2026-08", "running": "model-2026-09"}
    ]
    assert made.status_code == 201
    assert (run.status_code, run.json()["verdict"], run.json()["status"]) == (201, "pass", "done")
    assert [s["scenario_id"] for s in listed.json()["items"]] == [scenario["scenario_id"]]


class HeldTurns:
    """Holds each request inside `record` until released, so the requests overlap as a burst does."""

    def __init__(self) -> None:
        self.inside = 0
        self.most_inside = 0
        self.release = asyncio.Event()

    async def record(self, caller: SourcePrincipal, items: list[Any]) -> TurnsResponse:
        self.inside += 1
        self.most_inside = max(self.most_inside, self.inside)
        await self.release.wait()
        self.inside -= 1
        return TurnsResponse(accepted=len(items), duplicates=0)


async def test_four_maximal_bodies_at_once_take_two_slots_and_the_others_are_told_to_retry(
    world: World,
) -> None:
    held = HeldTurns()
    container = Container(Edge(world), world.cell.config, held, Messages(), None)  # type: ignore[arg-type]
    app = create_app("niadra-test", container, (turns.router,))
    # 16 MB decoded, the most a request may carry, from a few KB of zstd or gzip.
    maximal = orjson.dumps({"turns": [turn(T1)], "padding": "x" * (MAX_DECODED_BYTES - 4096)})
    bodies = [
        (zstandard.ZstdCompressor().compress(maximal), "zstd"),
        (gzip.compress(maximal), "gzip"),
        (zstandard.ZstdCompressor().compress(maximal), "zstd"),
        (gzip.compress(maximal), "gzip"),
    ]
    small = orjson.dumps({"turns": [turn(T2)]})
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://cell") as client:

        def send(body: bytes, encoding: str) -> Any:
            return client.post("/v1/turns", content=body, headers={**SOURCE, "content-encoding": encoding})

        burst = [asyncio.create_task(send(body, encoding)) for body, encoding in bodies]
        while held.inside < 2 or sum(task.done() for task in burst) < 2:
            await asyncio.sleep(0.01)
        # A small body is never held back by the large ones.
        small_task = asyncio.create_task(send(small, "identity"))
        while held.inside < 3:
            await asyncio.sleep(0.01)
        held.release.set()
        answers = await asyncio.gather(*burst, small_task)
        again = await send(*bodies[0])
    statuses = sorted(answer.status_code for answer in answers[:4])
    assert statuses == [200, 200, 429, 429]
    refused = [answer for answer in answers[:4] if answer.status_code == 429]
    assert all(r.headers["retry-after"] == "2" and r.json()["code"] == "rate_limited" for r in refused)
    assert answers[4].status_code == 200
    assert held.most_inside == 3
    assert container.large_bodies.held == 0
    assert again.status_code == 200, "a slot comes back when its request ends"
