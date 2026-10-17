"""Caller authentication for every route (5.1).

Sources (agents and systems) send `Bearer nia_sk_...` keys validated inside the cell; people
send a short-lived JWT issued by the control plane and verified offline. The edge service
owns the actual checks; this module only turns them into FastAPI dependencies.
"""

from collections.abc import Awaitable, Callable

from fastapi import Request

from niadra.domain.common.errors import Forbidden, ScopeMissing, Unauthenticated
from niadra.domain.common.space import PersonPrincipal, Principal, SourcePrincipal
from niadra.domain.control.records import Role
from niadra.domain.measure.access import reads_across_sources
from niadra.domain.vocabulary import Scope

SOURCE_KEY_PREFIX = "nia_sk_"


def _bearer(request: Request) -> str:
    header = request.headers.get("authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise Unauthenticated("missing bearer credential")
    return token.strip()


async def authenticate(request: Request) -> Principal:
    token = _bearer(request)
    edge = request.app.state.container.edge
    principal: Principal
    if token.startswith(SOURCE_KEY_PREFIX):
        principal = await edge.authenticate_key(token)
    else:
        principal = await edge.authenticate_person(token)
    request.state.principal = principal
    return principal


def require_source(
    *scopes: Scope, rate_limited: bool = True
) -> Callable[[Request], Awaitable[SourcePrincipal]]:
    """A source key with `scopes`. `rate_limited=False` leaves the count to the route itself, for routes
    whose cost depends on what the body asks (the MCP endpoint counts per tool)."""

    async def dependency(request: Request) -> SourcePrincipal:
        principal = await authenticate(request)
        if not isinstance(principal, SourcePrincipal):
            raise Forbidden("this route takes a source key")
        missing = [s for s in scopes if not principal.allows(s)]
        if missing:
            raise ScopeMissing("the key lacks the scope " + ", ".join(missing))
        if rate_limited:
            await request.app.state.container.edge.check_rate(principal, request.url.path)
        return principal

    return dependency


def require_governance(*roles: Role) -> Callable[[Request], Awaitable[Principal]]:
    """Console people with one of `roles`, or a source key with the `admin` scope (7.0)."""

    async def dependency(request: Request) -> Principal:
        return _governed(await authenticate(request), roles)

    return dependency


def _governed(principal: Principal, roles: tuple[Role, ...]) -> Principal:
    if isinstance(principal, SourcePrincipal):
        if not principal.allows(Scope.ADMIN):
            raise ScopeMissing("the key lacks the admin scope")
        return principal
    if roles and not (set(roles) & principal.roles) and "admin" not in principal.roles:
        raise Forbidden("the role cannot do this")
    return principal


def require_aggregates(*roles: Role) -> Callable[[Request], Awaitable[Principal]]:
    """What `require_governance` lets in, and an analyst source key with the `analytics` scope: a report
    that sums the space's conversations across its sources and names none, read as Insights reads (19.4).
    Never for a route that lists or opens one conversation."""

    async def dependency(request: Request) -> Principal:
        principal = await authenticate(request)
        if isinstance(principal, SourcePrincipal) and reads_across_sources(principal):
            await request.app.state.container.edge.check_rate(principal, request.url.path)
            return principal
        if isinstance(principal, SourcePrincipal) and not principal.allows(Scope.ADMIN):
            raise ScopeMissing("the key lacks the admin scope, or the analytics scope of an analyst source")
        return _governed(principal, roles)

    return dependency


def governance_or_scope(scope: Scope, *roles: Role) -> Callable[[Request], Awaitable[Principal]]:
    """What `require_governance` lets in, and a source key with `scope`: a narrow governance write a system
    of record makes without the admin scope."""

    async def dependency(request: Request) -> Principal:
        principal = await authenticate(request)
        if isinstance(principal, SourcePrincipal) and principal.allows(scope):
            return principal
        if isinstance(principal, SourcePrincipal) and not principal.allows(Scope.ADMIN):
            raise ScopeMissing(f"the key lacks the {scope} scope or the admin scope")
        return _governed(principal, roles)

    return dependency


def require_person(*roles: Role) -> Callable[[Request], Awaitable[PersonPrincipal]]:
    async def dependency(request: Request) -> PersonPrincipal:
        principal = await authenticate(request)
        if not isinstance(principal, PersonPrincipal):
            raise Forbidden("this route takes a person token")
        if roles and not (set(roles) & principal.roles) and "admin" not in principal.roles:
            raise Forbidden("the role cannot do this")
        return principal

    return dependency
