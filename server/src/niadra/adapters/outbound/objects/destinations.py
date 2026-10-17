"""`ExportDestinations` for the continuous export (22.3): the customer's own bucket, written only.

The credential lives in the cell vault under the schedule's `credential_ref`, written through
`PUT /v1/exports/destinations/{schedule_id}/credential` as JSON:

- `s3`: `{"access_key_id": "...", "secret_access_key": "..."}`, over the S3 protocol, at the schedule's
  endpoint or the AWS endpoint of its region;
- `gcs`: the same shape with an HMAC key of Google Cloud Storage, through its S3-compatible API;
- `azure`: `{"sas_url": "https://<account>.blob.core.windows.net/<container>?<SAS>"}`, a container SAS.

Every credential must be write-only: one that can list the bucket, or a SAS with more than
create, write and add, is refused when it is registered. The destination's host must resolve only to
public addresses, as for webhooks (adapters/outbound/egress.py): the endpoint and the SAS URL are text
the tenant writes, and the cell must not reach its own network, or the cloud's metadata, on their behalf.
"""

import ipaddress
import json
from collections.abc import AsyncIterator
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from typing import Protocol
from urllib.parse import parse_qs, quote, urlsplit

import httpx

from niadra.adapters.outbound.egress import Resolver, is_blocked, system_resolver
from niadra.adapters.outbound.objects.s3 import S3ObjectStore
from niadra.config.models import ExportSchedule
from niadra.domain.common.errors import InvalidInput, Unavailable

SUPPORTED_KINDS = frozenset({"s3", "gcs", "azure"})
GCS_ENDPOINT = "https://storage.googleapis.com"
AZURE_HOST = ".blob.core.windows.net"
AZURE_VERSION = "2021-08-06"
WRITE_ONLY_SAS = frozenset("cwa")
"""Create, write and add: never read, list or delete."""


class ExportSink(Protocol):
    async def put(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> None: ...


class S3ExportDestinations:
    def __init__(self, *, timeout_s: float = 30.0, resolver: Resolver | None = None) -> None:
        self._timeout_s = timeout_s
        self._resolver = resolver

    def open(self, schedule: ExportSchedule, credential: str) -> AbstractAsyncContextManager[ExportSink]:
        if schedule.destination_kind not in SUPPORTED_KINDS:
            kind = schedule.destination_kind
            raise InvalidInput(f"export destinations of kind {kind} are not supported yet")
        if schedule.destination_kind == "azure":
            return self._azure(parse_sas(schedule, credential))
        access_key_id, secret_access_key = parse_credential(credential)
        return self._connect(schedule, access_key_id, secret_access_key)

    async def check_write_only(self, schedule: ExportSchedule, credential: str) -> None:
        """Refuses a credential that can read the destination."""
        if schedule.destination_kind == "azure":
            await self._check_public(parse_sas(schedule, credential))
            return
        access_key_id, secret_access_key = parse_credential(credential)
        async with self._connect(schedule, access_key_id, secret_access_key) as store:
            try:
                readable = await store.can_list(schedule.prefix)
            except Exception as exc:
                raise Unavailable("the destination did not answer; try again", retry_after_s=30) from exc
        if readable:
            raise InvalidInput("the credential can list the bucket; give it only permission to write")

    @asynccontextmanager
    async def _connect(
        self, schedule: ExportSchedule, access_key_id: str, secret_access_key: str
    ) -> AsyncIterator[S3ObjectStore]:
        gcs = schedule.destination_kind == "gcs"
        endpoint_url = schedule.endpoint_url or (GCS_ENDPOINT if gcs else aws_endpoint(schedule.region))
        await self._check_public(endpoint_url)
        async with S3ObjectStore.connect(
            endpoint_url=endpoint_url,
            bucket=schedule.bucket,
            # Google signs S3-style requests with any region name; its XML API calls it `auto`.
            region="auto" if gcs else schedule.region,
            access_key_id=access_key_id,
            secret_access_key=secret_access_key,
            timeout_s=self._timeout_s,
            checksums=not gcs,
        ) as store:
            yield store

    @asynccontextmanager
    async def _azure(self, sas_url: str) -> AsyncIterator[ExportSink]:
        await self._check_public(sas_url)
        async with httpx.AsyncClient(timeout=httpx.Timeout(self._timeout_s)) as http:
            yield AzureBlobSink(http, sas_url)

    async def _check_public(self, url: str) -> None:
        """Every address the destination's host resolves to must be public (never the cell's network, a
        loopback or the metadata address). The client resolves again when it connects; TLS still verifies
        the certificate for the host, so an answer that changed in between reaches no service of ours."""
        parts = urlsplit(url)
        host = parts.hostname or ""
        try:
            addresses = (
                [host] if _is_ip(host) else await (self._resolver or system_resolver)(host, parts.port or 443)
            )
        except OSError:
            raise Unavailable("the destination does not resolve", retry_after_s=60) from None
        if not addresses or any(is_blocked(ipaddress.ip_address(a.split("%", 1)[0])) for a in addresses):
            raise InvalidInput("the destination is not a public address")


def _is_ip(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return False
    return True


class AzureBlobSink:
    """Block blobs into one container through its SAS; nothing is ever read or listed."""

    def __init__(self, http: httpx.AsyncClient, sas_url: str) -> None:
        parts = urlsplit(sas_url)
        self._http = http
        self._base = f"{parts.scheme}://{parts.netloc}{parts.path.rstrip('/')}"
        self._sas = parts.query

    async def put(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> None:
        url = f"{self._base}/{quote(key)}?{self._sas}"
        headers = {"x-ms-blob-type": "BlockBlob", "x-ms-version": AZURE_VERSION, "Content-Type": content_type}
        try:
            response = await self._http.put(url, content=data, headers=headers)
        except httpx.HTTPError as exc:
            raise Unavailable("the Azure container did not answer", retry_after_s=60) from exc
        if response.status_code != 201:
            # Never the URL: it carries the signature.
            raise Unavailable(f"the Azure container answered {response.status_code}", retry_after_s=60)


def aws_endpoint(region: str) -> str:
    return f"https://s3.{region}.amazonaws.com"


def parse_credential(credential: str) -> tuple[str, str]:
    # The message never echoes the credential: it ends up in the run's error column.
    try:
        data = json.loads(credential)
        return str(data["access_key_id"]), str(data["secret_access_key"])
    except (ValueError, TypeError, KeyError):
        raise InvalidInput(
            "the destination credential must be JSON with access_key_id and secret_access_key"
        ) from None


def parse_sas(schedule: ExportSchedule, credential: str) -> str:
    """The container SAS URL, checked: https, the schedule's container, write-only permissions."""
    try:
        sas_url = str(json.loads(credential)["sas_url"])
    except (ValueError, TypeError, KeyError):
        raise InvalidInput("the destination credential must be JSON with sas_url") from None
    parts = urlsplit(sas_url)
    expected = urlsplit(schedule.endpoint_url).netloc if schedule.endpoint_url else None
    host_ok = parts.netloc == expected if expected else parts.netloc.endswith(AZURE_HOST)
    if parts.scheme != "https" or not host_ok:
        raise InvalidInput("the SAS URL must be https on the account's blob endpoint")
    if parts.path.strip("/") != schedule.bucket:
        raise InvalidInput("the SAS URL must name the schedule's container")
    permissions = set(parse_qs(parts.query).get("sp", [""])[0])
    if not permissions or not permissions <= WRITE_ONLY_SAS:
        raise InvalidInput("the SAS must allow only create, write and add; never read, list or delete")
    return sas_url
