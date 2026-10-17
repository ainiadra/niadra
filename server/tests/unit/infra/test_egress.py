"""Outbound HTTP only reaches public HTTPS destinations, on the address that was checked (7.5)."""

import ipaddress

import httpx
import pytest

from niadra.adapters.outbound.egress import SafeEgress, is_blocked
from niadra.domain.common.errors import Forbidden, Unavailable

BLOCKED = [
    "10.0.0.1",
    "172.16.5.4",
    "192.168.1.1",
    "127.0.0.1",
    "0.0.0.0",  # noqa: S104
    "169.254.169.254",
    "100.64.0.1",
    "224.0.0.1",
    "255.255.255.255",
    "::1",
    "::",
    "fe80::1",
    "fd00:ec2::254",
    "::ffff:10.0.0.1",
    "::ffff:169.254.169.254",
    "2002:a00:1::1",
    "64:ff9b::a00:1",
]
PUBLIC = ["93.184.216.34", "8.8.8.8", "2606:4700:4700::1111"]


@pytest.mark.parametrize("address", BLOCKED)
def test_private_loopback_link_local_and_metadata_ranges_are_blocked(address: str) -> None:
    assert is_blocked(ipaddress.ip_address(address))


@pytest.mark.parametrize("address", PUBLIC)
def test_public_addresses_pass(address: str) -> None:
    assert not is_blocked(ipaddress.ip_address(address))


def resolver(*answers: str):  # type: ignore[no-untyped-def]
    async def resolve(host: str, port: int) -> list[str]:
        return list(answers)

    return resolve


async def test_only_https_is_allowed() -> None:
    egress = SafeEgress(resolver("93.184.216.34"), httpx.MockTransport(lambda r: httpx.Response(200)))
    with pytest.raises(Forbidden):
        await egress.post("http://hooks.example.com/x", b"{}", {}, 1.0)


@pytest.mark.parametrize(
    "answers", [("10.0.0.7",), ("93.184.216.34", "169.254.169.254"), ()], ids=["private", "mixed", "empty"]
)
async def test_a_name_that_resolves_to_any_private_address_is_refused(answers: tuple[str, ...]) -> None:
    calls: list[httpx.Request] = []
    transport = httpx.MockTransport(lambda r: calls.append(r) or httpx.Response(200))  # type: ignore[func-returns-value]
    egress = SafeEgress(resolver(*answers), transport)
    with pytest.raises(Forbidden):
        await egress.post("https://hooks.example.com/x", b"{}", {}, 1.0)
    assert calls == []


async def test_ip_literals_are_checked_too() -> None:
    egress = SafeEgress(resolver(), httpx.MockTransport(lambda r: httpx.Response(200)))
    with pytest.raises(Forbidden):
        await egress.post("https://127.0.0.1/admin", b"", {}, 1.0)
    with pytest.raises(Forbidden):
        await egress.post("https://[::1]/admin", b"", {}, 1.0)


async def test_the_request_goes_to_the_pinned_address_with_the_real_host_for_tls() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(202, content=b"ok", headers={"x-id": "1"})

    egress = SafeEgress(resolver("93.184.216.34"), httpx.MockTransport(handler))
    response = await egress.post(
        "https://hooks.example.com:8443/in?t=1", b'{"a":1}', {"webhook-id": "w1"}, 2.0
    )
    assert (response.status, response.body, response.headers["x-id"]) == (202, b"ok", "1")
    [request] = seen
    assert request.url.host == "93.184.216.34"
    assert request.url.port == 8443
    assert request.url.query == b"t=1"
    assert request.headers["host"] == "hooks.example.com:8443"
    assert request.headers["webhook-id"] == "w1"
    assert request.extensions["sni_hostname"] == "hooks.example.com"
    assert request.content == b'{"a":1}'


async def test_redirects_are_not_followed() -> None:
    transport = httpx.MockTransport(lambda r: httpx.Response(302, headers={"location": "https://10.0.0.1/"}))
    egress = SafeEgress(resolver("93.184.216.34"), transport)
    assert (await egress.post("https://hooks.example.com/", b"", {}, 1.0)).status == 302


async def test_timeouts_and_network_errors_are_unavailable() -> None:
    def timeout(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("slow", request=request)

    egress = SafeEgress(resolver("93.184.216.34"), httpx.MockTransport(timeout))
    with pytest.raises(Unavailable):
        await egress.post("https://hooks.example.com/", b"", {}, 1.0)

    async def unresolvable(host: str, port: int) -> list[str]:
        raise OSError("nodename nor servname provided")

    with pytest.raises(Unavailable):
        await SafeEgress(unresolvable).post("https://nowhere.invalid/", b"", {}, 1.0)
