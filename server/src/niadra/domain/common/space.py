"""The space: a tenant project in one environment (6, L9).

Every key in the cell starts with the space. Sandbox and production of the same
project are different spaces, so they never share data, keys or pseudonyms.
"""

from dataclasses import dataclass
from uuid import UUID, uuid5

from niadra.domain.vocabulary import Audience, Environment, Scope, Verification


@dataclass(frozen=True, slots=True)
class SpaceRef:
    space_id: UUID
    tenant_id: UUID
    region: str
    environment: Environment


@dataclass(frozen=True, slots=True)
class SourcePrincipal:
    """Who is calling: a source key (agents, systems) resolved at the edge (5.1)."""

    space: SpaceRef
    source_id: UUID
    key_id: str
    scopes: frozenset[Scope]
    audience: Audience
    verification_ceiling: Verification
    purposes: frozenset[str]
    vendor: str | None = None
    confirms_before_acting: bool = False
    trusted_action_ops: frozenset[str] = frozenset()

    def allows(self, scope: Scope) -> bool:
        return scope in self.scopes


@dataclass(frozen=True, slots=True)
class PersonPrincipal:
    """A person in the Console, authenticated by a short-lived JWT from the control plane."""

    space: SpaceRef
    user_id: UUID
    roles: frozenset[str]
    allowed_sources: frozenset[UUID] | None = None
    label: str | None = None
    """The person's e-mail as the token states it, to name them where only `user_id` is kept."""


Principal = SourcePrincipal | PersonPrincipal


_GOVERNANCE = UUID("6f1b3c1e-2a8e-4b8a-9c55-0d7f4e2b9a10")


def governance_source(space_id: UUID) -> UUID:
    """The source of the events people write through governance, such as a correction: one per
    space, never a key's, so the lineage tells them from what agents and systems sent."""
    return uuid5(_GOVERNANCE, str(space_id))


def governance_principal(space: SpaceRef, actor: str) -> SourcePrincipal:
    """Writes as the space's governance source, with nothing to prove and nothing to read."""
    return SourcePrincipal(
        space=space,
        source_id=governance_source(space.space_id),
        key_id=actor,
        scopes=frozenset({Scope.TRACK}),
        audience=Audience.INTERNAL_AGENT,
        verification_ceiling=Verification.V0,
        purposes=frozenset(),
    )


def secret_prefix(space_id: UUID) -> str:
    """Where the cell vault keeps every secret of one space: `spaces/<space>/<kind>/<id>` (4.7)."""
    return f"spaces/{space_id}/"


def owns_secret(space_id: UUID, ref: str) -> bool:
    """True when a vault reference a space's configuration names is one of that space's own secrets.

    The vault is the cell's, shared by every space it serves, and a reference is text the tenant writes
    in its configuration: without this check one space could name another's secret, have it sent in a
    webhook's Authorization header or overwrite it with an export credential."""
    return ref.startswith(secret_prefix(space_id))
