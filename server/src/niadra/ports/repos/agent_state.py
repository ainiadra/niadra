"""The agents' working state in the space's database (24.3.9): the durable copy its batch writer keeps."""

from collections.abc import Collection, Sequence
from datetime import datetime
from typing import Protocol
from uuid import UUID

from niadra.domain.privacy.erasure import ErasedRow, ErasureScope
from niadra.domain.state.agent_state import DurableState, PackagedState


class AgentStateRepository(Protocol):
    """Owned by the `objects` module."""

    async def get(self, scope_key: bytes, source_id: UUID, agent: str) -> DurableState | None: ...

    async def states_page(self, after: tuple[bytes, UUID, str] | None, limit: int) -> list[DurableState]:
        """The space's states by (scope, source, agent) after `after`."""
        ...

    async def upsert(self, states: Sequence[DurableState]) -> int:
        """Stores the states in one statement: a row moves only to a greater version. The customer's handle is
        found by `subject_key` among the space's handles. Returns how many rows moved."""
        ...

    async def for_handles(self, handle_ids: Sequence[UUID]) -> list[DurableState]:
        """The states whose customer is one of these handles, for the customer's package."""
        ...

    async def package_for(self, handle_ids: Sequence[UUID]) -> list[PackagedState]:
        """The states whose customer is one of these handles, opened, for the customer's data package."""
        ...

    async def older_than(
        self, cutoff: datetime, *, limit: int, keep: Collection[UUID] = ()
    ) -> list[DurableState]:
        """States last written before `cutoff`: where a field's own retention may have passed. The states of
        the customers in `keep` (under a legal hold) are left out."""
        ...

    async def replace_body(self, state: DurableState) -> bool:
        """Rewrites a state's body at its version, when no later write moved it; False otherwise."""
        ...

    async def resolve_owners(self, *, limit: int) -> int:
        """Up to `limit` states written before their customer's handle existed get it now, found by their
        `subject_key`; says how many."""
        ...

    async def expire(self, now: datetime, *, limit: int, keep: Collection[UUID] = ()) -> int:
        """Deletes up to `limit` states past their `valid_until`, but not those of the customers in `keep`."""
        ...

    async def for_scopes(self, scope_keys: Sequence[bytes]) -> list[DurableState]:
        """The states of these keyed scopes (a conversation's, all its agents')."""
        ...

    async def erase_marked(self, marks: Sequence[bytes], *, limit: int) -> int:
        """Deletes up to `limit` states whose customer's keyed handle or whose keyed scope is one of `marks`:
        what an erasure reached, the ones stored while it ran among them."""
        ...

    async def erase_for(self, scope: ErasureScope, *, limit: int) -> list[ErasedRow]:
        """`ErasureParticipant` (8.4): the states of the target's handles, or of the conversation erased."""
        ...
