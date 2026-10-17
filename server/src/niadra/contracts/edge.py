"""Edge governance answers (4.7, 7.1)."""

from datetime import datetime
from uuid import UUID

from pydantic import Field

from niadra.contracts.common import Model
from niadra.domain.vocabulary import Audience, Environment, Verification


class SourceRevocationOut(Model):
    source_id: UUID
    revoked_key_ids: list[str]
    revoked_at: datetime


class KeyIdentity(Model):
    """`GET /v1/sources/me`: what a key authenticates as, to check a key before wiring an agent to it."""

    space_id: UUID
    tenant_id: UUID
    region: str
    environment: Environment
    source_id: UUID
    source_name: str | None = None
    vendor: str | None = None
    key_id: str
    scopes: list[str] = Field(description="Sorted.")
    audience: Audience
    verification_ceiling: Verification
    purposes: list[str] = Field(description="Sorted.")
    agent_memory: bool = Field(description="Whether the space has the agents' own notes turned on.")
