"""Errors that cross layers carry a stable `code` from the error catalog (7.0).

HTTP adapters map codes to status and `application/problem+json`; nothing below
the adapters knows about HTTP.
"""

from typing import Any


class NiadraError(Exception):
    code = "internal_error"

    def __init__(self, detail: str = "", **extra: Any) -> None:
        super().__init__(detail or self.code)
        self.detail = detail
        self.extra = extra


class InvalidInput(NiadraError):
    code = "invalid_input"


class NotFound(NiadraError):
    code = "not_found"


class Conflict(NiadraError):
    code = "conflict"


class Forbidden(NiadraError):
    code = "forbidden"


class ScopeMissing(Forbidden):
    """The key lacks the scope of the route or of a batch item: the code the error catalog documents."""

    code = "scope_missing"


class Unauthenticated(NiadraError):
    code = "unauthenticated"


class RateLimited(NiadraError):
    code = "rate_limited"

    def __init__(self, retry_after_s: int, detail: str = "") -> None:
        super().__init__(detail, retry_after_s=retry_after_s)
        self.retry_after_s = retry_after_s


class Unavailable(NiadraError):
    """A dependency is down, or a space's database is being created or restored: retry later.
    `retry_after_s` becomes the `Retry-After` header; the SDKs wait that long and send again."""

    code = "unavailable"

    def __init__(self, detail: str = "", retry_after_s: int | None = None) -> None:
        super().__init__(detail, **({} if retry_after_s is None else {"retry_after_s": retry_after_s}))
        self.retry_after_s = retry_after_s


class ModelsRefused(NiadraError):
    """The in-cell models pod refused the input (413 too large, 422 malformed): the same input would be
    refused again, so the caller settles now instead of retrying. The models client sizes every request to
    the pod's limits (`domain.models_input`), so this means the pod runs with lower limits than the cell."""


class WrongCell(NiadraError):
    """The space moved to another cell; the SDK retries against the new address (7.0)."""

    code = "wrong_cell"


class VerificationNotAllowed(InvalidInput):
    code = "verification_not_allowed"


class AboutWithoutLink(InvalidInput):
    code = "about_without_link"


class UnknownObject(InvalidInput):
    """A customer-facing agent named an object of a type a company system sends, and no system has sent it:
    an agent names the objects it was given and never creates one."""

    code = "unknown_object"


class LeaseLost(NiadraError):
    """The fencing check failed: another worker owns the profile now (5.5)."""

    code = "lease_lost"


class PersonalDataInAgentMemory(InvalidInput):
    """An agent note carried personal data; the write is refused, never masked (10, C.1)."""

    code = "personal_data_in_agent_memory"


# The agent features' error codes (7.7).
class TurnTooLarge(InvalidInput):
    """A `POST /v1/turns` body over its limits, compressed or decompressed (24.3.5)."""

    code = "turn_too_large"


class PinMismatch(InvalidInput):
    """A replay's build does not share a pin with the recording; `members` names each, with both values."""

    code = "pin_mismatch"

    def __init__(self, pins: list[dict[str, Any]]) -> None:
        super().__init__(
            "the build does not share these pins with the recording: " + ", ".join(p["name"] for p in pins)
        )
        self.members = {"pins": pins}


class NotReplayable(InvalidInput):
    """A turn that cannot be run again, with every reason."""

    code = "not_replayable"

    def __init__(self, reasons: list[dict[str, str]]) -> None:
        super().__init__("the turn cannot be run again")
        self.members = {"reasons": reasons}


class ReplayExpired(NiadraError):
    """A scenario's turn that left storage."""

    code = "replay_expired"


class ObjectTypeUnknown(InvalidInput):
    """A state read or write names a type the space's registry does not declare."""

    code = "object_type_unknown"


class AgentStateConflict(Conflict):
    """A compare-and-swap of an agent's working state found another version than the one it read."""

    code = "agent_state_conflict"


class NoFingerprint(InvalidInput):
    """A drift check of a declared type that keeps no fingerprint of the schema it mirrors."""

    code = "no_fingerprint"


class LeaseHeld(Conflict):
    """A declared claim met another holder's claim at least as restrictive (the coordination spec, 6.5)."""

    code = "lease_held"


class TaskLocked(Conflict):
    code = "task_locked"


class EffectInFlight(Conflict):
    """Another attempt holds the effect's key now."""

    code = "effect_in_flight"


class CoordinationUnavailable(Unavailable):
    """The state store or the confirmed queue did not answer: nothing was recorded, and the caller applies
    the direction its purpose fails in (L23)."""

    code = "coordination_unavailable"


class InvalidHandle(InvalidInput):
    """A value that is not a handle of its kind (the suppression-list spec, 3.3)."""

    code = "invalid_handle"


class UnsupportedHandleType(InvalidInput):
    """A handle no contact goes to: only phone numbers and e-mail addresses are destinations."""

    code = "unsupported_type"
