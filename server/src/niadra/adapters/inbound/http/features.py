"""The agent features' routes answer only in a space that turned their feature on (7.7, 24.8).

A route of a feature the space did not list answers 404, as it did before the route existed, so nothing
changes for a space that did not ask.
"""

from collections.abc import Awaitable, Callable
from typing import Annotated, Any

from fastapi import Depends, Header, Query, Request

from niadra.adapters.inbound.http.auth import authenticate
from niadra.domain.common.errors import Forbidden, NotFound, ScopeMissing
from niadra.domain.common.space import Principal, SourcePrincipal
from niadra.domain.control.records import Role
from niadra.domain.vocabulary import Feature, Scope

Auth = Callable[[Request], Awaitable[Principal]]
IdempotencyKey = Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=256)]
Cursor = Annotated[str | None, Query(max_length=200)]
Limit = Annotated[int, Query(ge=1, le=200)]


def gated(auth: Auth, *features: Feature) -> Any:
    """The caller `auth` lets in, once its space turned on one of `features` (any of them when empty,
    for the discovery route that says which are on)."""
    wanted = frozenset(features or Feature)

    async def dependency(request: Request) -> Principal:
        principal = await auth(request)
        provider = request.app.state.container.config
        space_id = principal.space.space_id
        if provider.space(space_id).features.enabled & wanted:
            return principal
        # Each process reads its copy on a timer: a feature the space just turned on may be in the cell's copy
        # already. The miss reads it now, at most once a second, before answering 404.
        if await provider.refresh_on_miss() and provider.space(space_id).features.enabled & wanted:
            return principal
        raise NotFound("not enabled for this space")

    return Depends(dependency)


def source_or_person(scope: Scope, *roles: Role) -> Auth:
    """A source key with `scope`, or a person with one of `roles` (admins always)."""

    async def dependency(request: Request) -> Principal:
        principal = await authenticate(request)
        if isinstance(principal, SourcePrincipal):
            if not principal.allows(scope):
                raise ScopeMissing(f"the key lacks the scope {scope}")
            await request.app.state.container.edge.check_rate(principal, request.url.path)
            return principal
        if "admin" not in principal.roles and not (set(roles) & principal.roles):
            raise Forbidden("the role cannot do this")
        return principal

    return dependency
