"""Where the cell delivers a configuration diff proposed by the assistant, and what its observed layer
inferred: the control plane (7.4, 22.4)."""

from dataclasses import dataclass
from typing import Any, Protocol
from uuid import UUID


@dataclass(frozen=True, slots=True)
class ConfigProposal:
    """Configuration only: samples and customer values never leave the cell (22.4)."""

    space_id: UUID
    run_id: UUID
    config_type: str
    document: Any
    reason: str


class ConfigProposals(Protocol):
    async def submit(self, proposal: ConfigProposal) -> str:
        """Returns the id of the diff the control plane created; raises `Unavailable` to retry later."""
        ...


OBSERVED_AUTHOR = "niadra:observed"
"""Who the control plane records as the author of what the cell inferred from a space's traffic: never a
person, so the Console shows it as inferred."""


class ObservedRecords(Protocol):
    async def record(self, space_id: UUID, config_type: str, document: Any, reason: str) -> str:
        """Records what the cell inferred of one document as an applied diff by `OBSERVED_AUTHOR`, which
        changes no declaration; returns its id, raises `Unavailable` to retry later."""
        ...
