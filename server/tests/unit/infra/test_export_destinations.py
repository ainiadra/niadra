"""The continuous export opens the customer's bucket from the schedule and the vault credential."""

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import httpx
import pytest

from niadra.adapters.outbound.objects import destinations
from niadra.adapters.outbound.objects.destinations import S3ExportDestinations
from niadra.config.models import ExportSchedule
from niadra.domain.common.errors import InvalidInput, Unavailable

CREDENTIAL = json.dumps({"access_key_id": "AKIA-TEST", "secret_access_key": "s3cr3t"})


def schedule(**changes: Any) -> ExportSchedule:
    base: dict[str, Any] = {
        "schedule_id": "daily",
        "destination_kind": "s3",
        "bucket": "customer-lake",
        "prefix": "niadra/",
        "region": "sa-east-1",
        "tables": ["facts"],
        "credential_ref": "spaces/x/export/daily",
    }
    return ExportSchedule.model_validate(base | changes)


@pytest.fixture(autouse=True)
def public_dns(monkeypatch: pytest.MonkeyPatch) -> dict[str, list[str]]:
    """Every name resolves to a public address unless a test says otherwise; no test touches real DNS."""
    answers: dict[str, list[str]] = {}

    async def resolve(host: str, port: int) -> list[str]:
        return answers.get(host, ["93.184.216.34"])

    monkeypatch.setattr(destinations, "system_resolver", resolve)
    return answers


@pytest.fixture
def opened(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []

    @asynccontextmanager
    async def connect(**kwargs: Any) -> AsyncIterator[str]:
        calls.append({**kwargs, "closed": False})
        try:
            yield "store"
        finally:
            calls[-1]["closed"] = True

    monkeypatch.setattr(destinations.S3ObjectStore, "connect", connect)
    return calls


async def test_opens_the_schedule_endpoint_and_closes_the_client(opened: list[dict[str, Any]]) -> None:
    target = schedule(endpoint_url="https://minio.customer.example")
    async with S3ExportDestinations(timeout_s=5).open(target, CREDENTIAL) as store:
        assert store == "store"
        assert not opened[0]["closed"]
    assert opened == [
        {
            "endpoint_url": "https://minio.customer.example",
            "bucket": "customer-lake",
            "region": "sa-east-1",
            "access_key_id": "AKIA-TEST",
            "secret_access_key": "s3cr3t",
            "timeout_s": 5,
            "checksums": True,
            "closed": True,
        }
    ]


async def test_without_an_endpoint_the_regional_aws_endpoint_is_used(opened: list[dict[str, Any]]) -> None:
    async with S3ExportDestinations().open(schedule(), CREDENTIAL):
        pass
    assert opened[0]["endpoint_url"] == "https://s3.sa-east-1.amazonaws.com"


async def test_google_cloud_storage_goes_through_its_s3_api_without_the_newer_checksums(
    opened: list[dict[str, Any]],
) -> None:
    # An HMAC key of Google Cloud Storage, on its XML API.
    async with S3ExportDestinations().open(schedule(destination_kind="gcs"), CREDENTIAL):
        pass
    assert opened[0]["endpoint_url"] == "https://storage.googleapis.com"
    assert (opened[0]["region"], opened[0]["checksums"]) == ("auto", False)


SAS = "https://acct.blob.core.windows.net/customer-lake?sv=2022-11-02&sp=cw&sig=abc%2Bdef"


def azure(sas: str) -> str:
    return json.dumps({"sas_url": sas})


async def test_azure_blob_writes_block_blobs_through_the_container_sas() -> None:
    seen: list[httpx.Request] = []

    def answer(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(201)

    async with httpx.AsyncClient(transport=httpx.MockTransport(answer)) as http:
        sink = destinations.AzureBlobSink(http, SAS)
        await sink.put("niadra/facts/part-0.parquet", b"PAR1", "application/vnd.apache.parquet")
    [request] = seen
    assert str(request.url).startswith(
        "https://acct.blob.core.windows.net/customer-lake/niadra/facts/part-0.parquet?"
    )
    assert request.url.params["sp"] == "cw"
    assert (request.headers["x-ms-blob-type"], request.content) == ("BlockBlob", b"PAR1")


async def test_an_azure_error_never_echoes_the_signature() -> None:
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(403))) as http:
        with pytest.raises(Unavailable) as raised:
            await destinations.AzureBlobSink(http, SAS).put("k", b"x")
    assert "sig" not in str(raised.value)


@pytest.mark.parametrize(
    ("sas", "why"),
    [
        (SAS.replace("sp=cw", "sp=rcw"), "only create, write and add"),
        (SAS.replace("sp=cw", "sp=cwl"), "only create, write and add"),
        (SAS.replace("customer-lake", "other"), "the schedule's container"),
        (SAS.replace("https://", "http://"), "https"),
        (SAS.replace("acct.blob.core.windows.net", "evil.example"), "https"),
    ],
)
async def test_an_azure_sas_that_reads_or_points_elsewhere_is_refused(sas: str, why: str) -> None:
    with pytest.raises(InvalidInput, match=why):
        await S3ExportDestinations().check_write_only(schedule(destination_kind="azure"), azure(sas))


async def test_an_s3_key_that_can_list_the_bucket_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    class Store:
        def __init__(self, readable: bool) -> None:
            self.readable = readable

        async def can_list(self, prefix: str) -> bool:
            return self.readable

    for readable in (True, False):

        @asynccontextmanager
        async def connect(**_: Any) -> AsyncIterator[Store]:
            yield Store(readable)  # noqa: B023

        monkeypatch.setattr(destinations.S3ObjectStore, "connect", connect)
        check = S3ExportDestinations().check_write_only(schedule(), CREDENTIAL)
        if readable:
            with pytest.raises(InvalidInput, match="only permission to write"):
                await check
        else:
            await check


@pytest.mark.parametrize("credential", ["AKIA:secret", '["a", "b"]', '{"access_key_id": "a"}', '"x"'])
def test_a_malformed_credential_is_refused_without_echoing_it(credential: str) -> None:
    with pytest.raises(InvalidInput) as raised:
        S3ExportDestinations().open(schedule(), credential)
    assert credential not in str(raised.value)


def test_plain_http_endpoints_are_rejected_by_the_schedule() -> None:
    with pytest.raises(ValueError, match="https"):
        schedule(endpoint_url="http://minio.customer.example")


@pytest.mark.parametrize(
    ("endpoint", "answer"),
    [
        ("https://127.0.0.1:9000", None),
        ("https://169.254.169.254", None),
        ("https://[::1]", None),
        ("https://lake.customer.example", ["10.0.1.23"]),
        ("https://lake.customer.example", ["93.184.216.34", "192.168.0.4"]),
    ],
)
async def test_a_destination_that_resolves_inside_the_network_is_never_opened(
    opened: list[dict[str, Any]], public_dns: dict[str, list[str]], endpoint: str, answer: list[str] | None
) -> None:
    # The endpoint is text the tenant writes: the cell never reaches its own network on its behalf.
    if answer is not None:
        public_dns["lake.customer.example"] = answer
    target = schedule(endpoint_url=endpoint)
    with pytest.raises(InvalidInput, match="not a public address"):
        await S3ExportDestinations().check_write_only(target, CREDENTIAL)
    with pytest.raises(InvalidInput, match="not a public address"):
        async with S3ExportDestinations().open(target, CREDENTIAL):
            pass
    assert opened == []


async def test_an_azure_sas_on_a_private_address_is_refused(public_dns: dict[str, list[str]]) -> None:
    public_dns["blob.customer.example"] = ["172.16.0.9"]
    target = schedule(destination_kind="azure", endpoint_url="https://blob.customer.example")
    sas = azure("https://blob.customer.example/customer-lake?sp=cw&sig=x")
    with pytest.raises(InvalidInput, match="not a public address"):
        await S3ExportDestinations().check_write_only(target, sas)
    with pytest.raises(InvalidInput, match="not a public address"):
        async with S3ExportDestinations().open(target, sas):
            pass
