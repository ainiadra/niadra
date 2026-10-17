"""`/v1/sources/...` on the cell's governance API: the instant local cut and per-source coverage."""

from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import timedelta
from uuid import UUID, uuid4

import httpx
import pytest

from niadra.adapters.inbound.http.app import create_app
from niadra.adapters.inbound.http.sources import router
from niadra.app.edge import EdgeService
from niadra.app.ingest import IngestService
from niadra.domain.common.errors import Unauthenticated
from niadra.domain.common.space import PersonPrincipal, SourcePrincipal, SpaceRef
from niadra.domain.vocabulary import Environment
from niadra.testing.cell import BILLING_SOURCE, SPACE_ID, TENANT_ID, VOICE_SOURCE, WHATSAPP_SOURCE, Cell
from tests.unit.edge.test_service import PEPPER, issue, make_edge
from tests.unit.identity.test_routes import RecordingAudit
from tests.unit.ingest.fakes import build


class PeopleEdge(EdgeService):
    """The real edge, with the person token check replaced: roles come from the test."""

    roles: frozenset[str] = frozenset({"security"})

    async def authenticate_person(self, token: str) -> PersonPrincipal:
        space = SpaceRef(SPACE_ID, TENANT_ID, "us-east-2", Environment.SANDBOX)
        return PersonPrincipal(space=space, user_id=UUID(int=7), roles=self.roles)

    async def authenticate_key(self, token: str) -> SourcePrincipal:
        raise Unauthenticated("people only here")


@dataclass
class Container:
    edge: EdgeService
    ingest: IngestService
    admin_audit: RecordingAudit


@pytest.fixture
async def world(cell: Cell) -> AsyncIterator[tuple[httpx.AsyncClient, PeopleEdge, RecordingAudit]]:
    edge = PeopleEdge(cell.uow, cell.cache, cell.config, cell.clock, {}, PEPPER, databases=cell.databases)
    audit = RecordingAudit()
    ingest, _, _ = build(cell)
    app = create_app("admin", Container(edge, ingest, audit), [router])
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://admin") as http:
        http.headers["authorization"] = "Bearer person-jwt"
        yield http, edge, audit


async def test_revoke_cuts_the_source_writes_a_receipt_and_replays(
    cell: Cell, world: tuple[httpx.AsyncClient, PeopleEdge, RecordingAudit]
) -> None:
    http, _, audit = world
    token, row = await issue(make_edge(cell))
    headers = {"Idempotency-Key": str(uuid4())}

    first = await http.post(f"/v1/sources/{VOICE_SOURCE}/revoke", headers=headers)
    again = await http.post(f"/v1/sources/{VOICE_SOURCE}/revoke", headers=headers)

    assert first.status_code == 200
    assert first.json()["revoked_key_ids"] == [row.key_id]
    assert again.json() == first.json()
    assert audit.actions == ["source.revoked"]
    assert audit.receipts[0].target == f"source:{VOICE_SOURCE}"
    with pytest.raises(Unauthenticated):
        await make_edge(cell).authenticate_key(token)


async def test_revoke_needs_the_security_role_and_a_key(
    world: tuple[httpx.AsyncClient, PeopleEdge, RecordingAudit],
) -> None:
    http, edge, _ = world
    missing_key = await http.post(f"/v1/sources/{VOICE_SOURCE}/revoke")
    edge.roles = frozenset({"integration"})
    wrong_role = await http.post(f"/v1/sources/{VOICE_SOURCE}/revoke", headers={"Idempotency-Key": "k"})

    assert missing_key.status_code == 422
    assert wrong_role.status_code == 403


async def test_coverage_per_source_and_day_lists_mute_sources_too(
    cell: Cell, world: tuple[httpx.AsyncClient, PeopleEdge, RecordingAudit]
) -> None:
    http, edge, _ = world
    edge.roles = frozenset({"analysis"})
    now = cell.clock.now()
    async with cell.uow(SPACE_ID) as uow:
        for at, sent, received in (
            (now - timedelta(days=1), 5, 4),
            (now, 3, 3),
            (now - timedelta(days=9), 50, 50),
        ):
            window = at.replace(second=0, microsecond=0)
            await uow.ingest.bump_heartbeat(SPACE_ID, WHATSAPP_SOURCE, window, sent=sent, received=received)
        await uow.ingest.bump_heartbeat(
            SPACE_ID, WHATSAPP_SOURCE, now.replace(second=0), rejected=1, last_event_at=now
        )

    response = await http.get("/v1/sources/coverage", params={"days": 7})
    too_long = await http.get("/v1/sources/coverage", params={"days": 91})

    body = response.json()
    by_source = {item["source_id"]: item for item in body["items"]}
    whatsapp = by_source[str(WHATSAPP_SOURCE)]
    assert (whatsapp["sent"], whatsapp["received"], whatsapp["rejected"]) == (8, 7, 1)
    assert [d["day"] for d in whatsapp["days"]] == [str((now - timedelta(days=1)).date()), str(now.date())]
    assert whatsapp["last_event_at"] is not None
    assert whatsapp["name"] == "whatsapp-bot"
    assert by_source[str(BILLING_SOURCE)]["days"] == []
    assert by_source[str(BILLING_SOURCE)]["received"] == 0
    assert too_long.status_code == 422


async def test_any_console_role_learns_whether_events_arrived_and_nothing_else(
    cell: Cell, world: tuple[httpx.AsyncClient, PeopleEdge, RecordingAudit]
) -> None:
    http, edge, _ = world
    edge.roles = frozenset({"review"})
    now = cell.clock.now()

    before = (await http.get("/v1/sources/arrived")).json()
    async with cell.uow(SPACE_ID) as uow:
        window = (now - timedelta(days=40)).replace(second=0, microsecond=0)
        await uow.ingest.bump_heartbeat(SPACE_ID, WHATSAPP_SOURCE, window, sent=3, received=2)
        await uow.ingest.bump_heartbeat(SPACE_ID, BILLING_SOURCE, now.replace(second=0), rejected=4)
    after = await http.get("/v1/sources/arrived")
    this_week = (await http.get("/v1/sources/arrived", params={"days": 7})).json()
    coverage = await http.get("/v1/sources/coverage")

    assert before["arrived"] is False
    assert after.json()["arrived"] is True
    assert set(after.json()) == {"arrived", "since"}
    assert this_week["arrived"] is False  # the rejected events of today never arrived
    assert coverage.status_code == 403
    assert (await http.get("/v1/sources/arrived", params={"days": 91})).status_code == 422
