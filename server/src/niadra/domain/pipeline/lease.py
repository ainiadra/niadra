"""Leases with fencing (5.5): who may write a profile's state right now.

A lease row has `owner`, `epoch` and `expires_at`. Taking an expired lease bumps the epoch, and
every transaction made under a lease first checks that owner and epoch are still its own, so a
slow worker whose lease expired never writes over the new owner.
"""

from contextvars import ContextVar
from dataclasses import dataclass
from uuid import UUID

from niadra.domain.common.errors import InvalidInput
from niadra.domain.tasks import Lease, Task

LEASE_TTL_S = 60
HEARTBEAT_S = 20
BUSY_DELAY_S = 5


@dataclass(frozen=True, slots=True)
class LeaseGrant:
    space_id: UUID
    owner: str
    epochs: dict[str, int]

    @property
    def unit_keys(self) -> tuple[str, ...]:
        return tuple(sorted(self.epochs))


# Set by the worker runtime around a handler, so services can fence their writes without
# threading the grant through every call.
current_lease: ContextVar[LeaseGrant | None] = ContextVar("current_lease", default=None)


def profile_unit(profile_id: UUID) -> str:
    return f"profile:{profile_id}"


def lease_units(task: Task) -> tuple[str, ...]:
    """The units a task must hold, in the fixed order that keeps multi-profile leases deadlock-free."""
    match task.spec.lease:
        case Lease.NONE:
            return ()
        case Lease.SPACE:
            return ("space",)
        case Lease.OWN:
            return (f"task:{task.name}",)
        case Lease.PROFILE:
            if task.profile_id is None:
                if task.spec.unowned:
                    return ()
                raise InvalidInput(f"task {task.name} needs a profile")
            return (profile_unit(task.profile_id),)
        case Lease.PROFILES:
            ids = {UUID(str(p)) for p in task.payload.get("profile_ids", [])}
            if task.profile_id is not None:
                ids.add(task.profile_id)
            if not ids:
                raise InvalidInput(f"task {task.name} needs profile_ids")
            return tuple(sorted(profile_unit(p) for p in ids))
