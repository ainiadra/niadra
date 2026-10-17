"""People in the Console read profile data through the same policy as agents (5.22).

Each role reads as an audience class; a source key with the `admin` scope reads as itself. There is
no conversation, so the customer's proven level does not limit governance reads, and no task, so no
purpose does either: a purpose binds what an agent's task may see, while what a person may see is
set by the audience class of their role. Sensitivity, visibility and the tenant's rules still apply.
"""

from niadra.domain.common.errors import Forbidden
from niadra.domain.common.space import Principal, SourcePrincipal
from niadra.domain.policy.rules import PolicyContext, Reader
from niadra.domain.vocabulary import Audience, Scope, Verification

# Most revealing first: a person with several roles reads as the first one they hold.
ROLE_AUDIENCES: tuple[tuple[str, Audience], ...] = (
    ("admin", Audience.HUMAN),
    ("security", Audience.HUMAN),
    ("review", Audience.REVIEWER),
    ("integration", Audience.ANALYST),
    ("analysis", Audience.ANALYST),
)
CONSOLE_CONTEXT = PolicyContext(Verification.V4)
MEMORY_ROLES = frozenset({"admin", "security", "review", "analysis"})
"""The Console roles that open a customer's memory (`GET /v1/profiles/{id}/memory`): what it holds, a name
included, reaches a person through one of them, under the policy of the role."""


def reads_memory(principal: Principal) -> bool:
    """A person with one of `MEMORY_ROLES`, or a source key with the `admin` scope, which reads as its own
    audience."""
    if isinstance(principal, SourcePrincipal):
        return principal.allows(Scope.ADMIN)
    return bool(principal.roles & MEMORY_ROLES)


def console_reader(principal: Principal, declared_purposes: frozenset[str]) -> Reader:
    """`declared_purposes` are every purpose the tenant's configuration names."""
    if isinstance(principal, SourcePrincipal):
        return Reader(principal.audience, principal.purposes, principal.source_id)
    for role, audience in ROLE_AUDIENCES:
        if role in principal.roles:
            return Reader(audience, declared_purposes)
    raise Forbidden("the role cannot read customer memory")
