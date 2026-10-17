"""Which sources a governance caller may see: vendor A never sees vendor B's numbers (19.4, 5.22)."""

from uuid import UUID

from niadra.domain.common.space import Principal, SourcePrincipal
from niadra.domain.vocabulary import Audience, Scope


def reads_across_sources(principal: Principal) -> bool:
    """An analyst source key with the `analytics` scope: it reads the space's aggregates across every source,
    as Insights does (19.4), and never one conversation."""
    return (
        isinstance(principal, SourcePrincipal)
        and principal.audience is Audience.ANALYST
        and principal.allows(Scope.ANALYTICS)
    )


def visible_sources(principal: Principal, *, aggregate: bool = False) -> frozenset[UUID] | None:
    """None means every source of the space. Any other source key sees its own source only; an analyst key
    sees every source in an `aggregate`, a report that sums conversations and names none."""
    if isinstance(principal, SourcePrincipal):
        if aggregate and reads_across_sources(principal):
            return None
        return frozenset({principal.source_id})
    return principal.allowed_sources


def can_see(principal: Principal, source_id: UUID, *, aggregate: bool = False) -> bool:
    allowed = visible_sources(principal, aggregate=aggregate)
    return allowed is None or source_id in allowed
