"""The Console's customer list: order, pages, masks, what never appears, and its one receipt per page."""

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import httpx
import pytest

from niadra.adapters.inbound.http.app import create_app
from niadra.adapters.inbound.http.identity import router
from niadra.domain.common.errors import InvalidInput
from niadra.domain.identity.listing import ListCursor, ListOrder
from niadra.domain.identity.masking import mask_listed
from niadra.domain.vocabulary import HandleType, SubjectKind
from tests.unit.identity.conftest import Env, cpf, handle
from tests.unit.identity.test_routes import Container, FakeEdge, RecordingAudit

T = HandleType
START = datetime(2026, 9, 1, 12, tzinfo=UTC)


@pytest.fixture
def edge() -> FakeEdge:
    return FakeEdge()


@pytest.fixture
def audit() -> RecordingAudit:
    return RecordingAudit()


@pytest.fixture
async def http(env: Env, edge: FakeEdge, audit: RecordingAudit) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app("admin", Container(edge, env.service, audit), [router])
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://admin") as client:
        client.headers["authorization"] = "Bearer person-jwt"
        yield client


def phone(n: int) -> str:
    return f"+551198765{n:04d}"


async def people(env: Env, count: int) -> list[str]:
    """`count` customers, each seen a minute after the one before; their profile ids, oldest first."""
    return [
        str((await env.attach(handle(T.PHONE_E164, phone(n)), at=START + timedelta(minutes=n))).profile_id)
        for n in range(1, count + 1)
    ]


async def test_the_list_pages_through_every_customer_once_by_latest_activity(
    env: Env, http: httpx.AsyncClient
) -> None:
    ids = await people(env, 5)

    first = (await http.get("/v1/profiles", params={"limit": 2})).json()
    second = (await http.get("/v1/profiles", params={"limit": 2, "cursor": first["next_cursor"]})).json()
    third = (await http.get("/v1/profiles", params={"limit": 2, "cursor": second["next_cursor"]})).json()
    oldest_first = (await http.get("/v1/profiles", params={"order": "last_seen_asc"})).json()

    listed = [p["profile_id"] for page in (first, second, third) for p in page["items"]]
    assert listed == ids[::-1]
    assert third["next_cursor"] is None
    assert [p["profile_id"] for p in oldest_first["items"]] == ids
    assert first["items"][0]["last_seen"] == (START + timedelta(minutes=5)).isoformat().replace("+00:00", "Z")


async def test_a_customer_with_several_identifiers_is_one_row_at_its_latest_activity(
    env: Env, http: httpx.AsyncClient
) -> None:
    email = handle(T.EMAIL, "ana.silva@acme.com.br")
    old = await env.attach(handle(T.PHONE_E164, phone(1)), email, at=START)
    other = await env.attach(handle(T.PHONE_E164, phone(2)), at=START + timedelta(hours=1))
    await env.attach(email, at=START + timedelta(hours=2))

    page = (await http.get("/v1/profiles", params={"limit": 1})).json()
    rest = (await http.get("/v1/profiles", params={"cursor": page["next_cursor"]})).json()

    [row] = page["items"]
    assert row["profile_id"] == str(old.profile_id)
    assert row["handle_count"] == 2
    assert [h["type"] for h in row["handles"]] == ["email", "phone_e164"]
    assert [p["profile_id"] for p in rest["items"]] == [str(other.profile_id)]


async def test_no_identifier_leaves_in_full_whatever_the_role(
    env: Env, edge: FakeEdge, http: httpx.AsyncClient
) -> None:
    edge.roles = frozenset({"admin", "security"})
    await env.attach(
        handle(T.PHONE_E164, "+5511987654321"),
        handle(T.EMAIL, "ana.silva@acme.com.br"),
        handle(T.SYSTEM_ID, "crm-customer-7781", "crm"),
        at=START,
    )
    await env.attach(handle(T.GOV_ID_HMAC, cpf("529982247")), at=START)

    body = (await http.get("/v1/profiles")).text

    for value in ("987654321", "ana.silva", "crm-customer-7781", cpf("529982247")):
        assert value not in body
    assert mask_listed(T.SYSTEM_ID, "crm-customer-7781") == "cr*************81"
    assert mask_listed(T.SYSTEM_ID, "a1b2") == "****"
    assert mask_listed(T.EMAIL, "ana.silva@acme.com.br") == "a***@acme.com.br"


async def test_an_erased_or_merged_away_profile_is_never_listed(env: Env, http: httpx.AsyncClient) -> None:
    kept, erased = await people(env, 2)
    await env.repo.retire_profile(UUID(erased), datetime.now(UTC))

    listed = (await http.get("/v1/profiles")).json()["items"]

    assert [p["profile_id"] for p in listed] == [kept]


async def test_the_kind_filter_lists_only_that_kind(env: Env, http: httpx.AsyncClient) -> None:
    await people(env, 2)
    org = await env.attach(
        handle(T.SYSTEM_ID, "acct-9", "crm", SubjectKind.ACCOUNT), kind=SubjectKind.ACCOUNT, at=START
    )

    accounts = (await http.get("/v1/profiles", params={"kind": "account"})).json()["items"]
    everyone = (await http.get("/v1/profiles")).json()["items"]

    assert [(p["profile_id"], p["kind"]) for p in accounts] == [(str(org.profile_id), "account")]
    assert len(everyone) == 3


async def test_a_page_leaves_one_receipt_and_names_no_customer(
    env: Env, http: httpx.AsyncClient, audit: RecordingAudit
) -> None:
    await people(env, 3)

    page = await http.get("/v1/profiles", params={"limit": 2})
    await http.get("/v1/profiles", params={"limit": 2, "cursor": page.json()["next_cursor"]})

    assert [(r.action, r.target, r.profile_id) for r in audit.receipts] == [
        ("profiles.listed", "profiles:2", None),
        ("profiles.listed", "profiles:1", None),
    ]


async def test_the_list_takes_the_roles_of_the_profile_read(
    edge: FakeEdge, http: httpx.AsyncClient, audit: RecordingAudit
) -> None:
    edge.roles = frozenset({"analysis"})

    refused = await http.get("/v1/profiles")

    assert refused.status_code == 403
    assert audit.receipts == []


async def test_a_cursor_from_elsewhere_is_refused(env: Env, http: httpx.AsyncClient) -> None:
    assert (await http.get("/v1/profiles", params={"cursor": "not-a-cursor"})).status_code == 422
    assert (await http.get("/v1/profiles", params={"limit": 101})).status_code == 422
    with pytest.raises(InvalidInput):
        ListCursor.decode("12.zz")


def test_a_cursor_round_trips_to_the_microsecond() -> None:
    cursor = ListCursor(datetime(2026, 9, 30, 23, 59, 59, 999_999, tzinfo=UTC), uuid4())

    assert ListCursor.decode(cursor.encode()) == cursor
    assert ListOrder("last_seen_desc") is ListOrder.LAST_SEEN_DESC
