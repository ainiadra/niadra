"""All outbound HTTP to customer endpoints (7.5).

HTTPS only. The host is resolved here, every resolved address must be public, and the
request connects to the address that was checked (the "pinned" IP) while TLS still verifies
the certificate for the hostname. Resolving again inside the HTTP client would let a DNS
answer change between the check and the connection (DNS rebinding). Redirects are not
followed: a redirect is another destination that was never checked.
"""

import asyncio
import ipaddress
import socket
from collections.abc import Awaitable, Callable, Mapping

import httpx

from niadra.domain.common.errors import Forbidden, Unavailable
from niadra.ports.infra import HttpResponse

IpAddress = ipaddress.IPv4Address | ipaddress.IPv6Address
Resolver = Callable[[str, int], Awaitable[list[str]]]

MAX_RESPONSE_BYTES = 64 * 1024
# NAT64 embeds an IPv4 address that could be private; nothing we call needs it.
EXTRA_BLOCKED = (ipaddress.ip_network("64:ff9b::/96"), ipaddress.ip_network("64:ff9b:1::/48"))


def is_blocked(ip: IpAddress) -> bool:
    """Private, loopback, link-local (cloud metadata at 169.254.169.254), CGNAT, reserved, multicast."""
    if isinstance(ip, ipaddress.IPv6Address):
        embedded = ip.ipv4_mapped or ip.sixtofour or (ip.teredo[1] if ip.teredo else None)
        if embedded is not None and is_blocked(embedded):
            return True
        if any(ip in net for net in EXTRA_BLOCKED):
            return True
    return not ip.is_global or ip.is_multicast


async def system_resolver(host: str, port: int) -> list[str]:
    infos = await asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM)
    return [str(info[4][0]) for info in infos]


class SafeEgress:
    def __init__(
        self, resolver: Resolver = system_resolver, transport: httpx.AsyncBaseTransport | None = None
    ) -> None:
        self._resolve = resolver
        self._client = httpx.AsyncClient(transport=transport, follow_redirects=False, trust_env=False)

    async def post(self, url: str, body: bytes, headers: Mapping[str, str], timeout_s: float) -> HttpResponse:
        return await self._send("POST", url, body, headers, timeout_s)

    async def get(self, url: str, headers: Mapping[str, str], timeout_s: float) -> HttpResponse:
        return await self._send("GET", url, None, headers, timeout_s)

    async def _send(
        self, method: str, url: str, body: bytes | None, headers: Mapping[str, str], timeout_s: float
    ) -> HttpResponse:
        target = httpx.URL(url)
        if target.scheme != "https" or not target.host:
            raise Forbidden("only https destinations are allowed")
        port = target.port or 443
        address = await self._pinned_address(target.host, port)
        host_header = target.host if port == 443 else f"{target.host}:{port}"
        request = self._client.build_request(
            method,
            target.copy_with(host=address),
            content=body,
            headers={**headers, "host": host_header},
            timeout=timeout_s,
            # TLS verifies the certificate against the real hostname, not the pinned IP.
            extensions={"sni_hostname": target.host},
        )
        try:
            async with asyncio.timeout(timeout_s):
                response = await self._client.send(request, stream=True)
                try:
                    content = await _read_capped(response)
                finally:
                    await response.aclose()
        except (TimeoutError, httpx.TimeoutException):
            raise Unavailable("the destination timed out") from None
        except httpx.HTTPError as exc:
            raise Unavailable(f"the destination failed: {type(exc).__name__}") from None
        return HttpResponse(status=response.status_code, body=content, headers=dict(response.headers))

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _pinned_address(self, host: str, port: int) -> str:
        try:
            candidates = [host] if _is_ip(host) else await self._resolve(host, port)
        except OSError:
            raise Unavailable("the destination does not resolve") from None
        addresses = [ipaddress.ip_address(a.split("%", 1)[0]) for a in candidates]
        # One private answer is enough to refuse: round-robin DNS must not pick for us.
        if not addresses or any(is_blocked(a) for a in addresses):
            raise Forbidden("the destination is not a public address")
        return str(addresses[0])


def _is_ip(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return False
    return True


async def _read_capped(response: httpx.Response) -> bytes:
    content = bytearray()
    async for chunk in response.aiter_bytes():
        content += chunk
        if len(content) >= MAX_RESPONSE_BYTES:
            break
    return bytes(content[:MAX_RESPONSE_BYTES])
