"""The vocabulary of coordination (24.3.4), shared by the `coordination` document, the contracts and the
state store's scripts."""

from dataclasses import dataclass
from enum import StrEnum


class Decision(StrEnum):
    ALLOW = "allow"
    DEFER = "defer"
    DENY = "deny"
    HANDOFF_TO = "handoff_to"


class FailDirection(StrEnum):
    """Which way a purpose's decision goes when Niadra cannot take it."""

    OPEN = "open"
    CLOSED = "closed"


class ClaimKind(StrEnum):
    OWNER = "owner"
    CASE = "case"
    TASK_LOCK = "task_lock"


class ClaimVia(StrEnum):
    """The three ways in: a declaration through the API, an observation mapped from a webhook, an observation
    the company's worker read and sent."""

    DECLARATION = "declaration"
    MAPPING = "mapping"
    WORKER = "worker"


class EffectKind(StrEnum):
    DELIVERY = "delivery"
    DOCUMENT = "document"
    NOTICE = "notice"
    FILING = "filing"
    PAID_CALL = "paid_call"
    EXTERNAL_ACTION = "external_action"


class EffectState(StrEnum):
    """`unknown_outcome` is the ambiguous attempt: it never turns back into `reserved` on its own."""

    RESERVED = "reserved"
    DONE = "done"
    FAILED = "failed"
    UNKNOWN_OUTCOME = "unknown_outcome"


class Settler(StrEnum):
    """Who moves an effect out of `reserved` or `unknown_outcome`."""

    RESERVER = "reserver"
    OBSERVATION = "observation"
    PERSON = "person"


class Compatibility(StrEnum):
    """How a new commitment meets an active one of the same type: the first holds, the best wins, or only
    one may exist."""

    FIRST_HOLDS = "first_holds"
    BEST_WINS = "best_wins"
    EXCLUSIVE = "exclusive"


@dataclass(frozen=True, slots=True)
class Claim:
    """One source's claim on a target, as the state store keeps it. `rank` orders restriction: 0 is the most
    restrictive level the space declares."""

    slot: str
    rank: int
    level: str
    holder: str
    kind: ClaimKind
    via: ClaimVia
    since_ms: int
    until_ms: int
    epoch: int
    claim_id: str
    intents: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ClaimResult:
    """`accepted` is false when a declaration met a claim at least as restrictive by another holder; `owner`
    is who holds the target after the call, if anyone; `released` is the claim a release ended."""

    accepted: bool
    epoch: int
    owner: Claim | None
    released: Claim | None = None


@dataclass(frozen=True, slots=True)
class Admission:
    """The state store's answer to a contact or a paid call: `reason` names why it is not `allow`,
    `remaining` is the budget left after the call, and `retry_at_ms` is when a `defer` or an exhausted
    budget may be asked again."""

    decision: Decision
    reason: str | None
    remaining: int
    retry_at_ms: int | None = None
    holder: Claim | None = None


@dataclass(frozen=True, slots=True)
class EffectSnapshot:
    """An effect as the state store holds it after a call, which is what the durable record stores. `fact` is
    `<type>\t<namespace>\t<id>` when the reservation named one; `ttl_ms` None holds the key for as long as the
    fact exists."""

    state: EffectState
    attempt: int
    kind: str
    source: str
    agent: str | None
    reserved_at_ms: int
    lease_until_ms: int | None
    settled_at_ms: int | None
    settled_by: Settler | None
    done_at_ms: int | None
    cost_units: int
    fact: str | None
    ttl_ms: int | None

    @property
    def order(self) -> tuple[int, int]:
        """Later transitions sort after earlier ones: the attempt, then when it was last settled."""
        return self.attempt, self.settled_at_ms or self.reserved_at_ms


@dataclass(frozen=True, slots=True)
class EffectAttempt:
    """What a reservation found: `reserved` means this caller holds the attempt `attempt` and must settle
    it; `in_flight`, `done` and `unknown_outcome` mean it must not act. `lapsed` says the call found the
    previous attempt's lease run out and made it `unknown_outcome`."""

    outcome: str
    attempt: int
    snapshot: EffectSnapshot
    lapsed: bool = False


@dataclass(frozen=True, slots=True)
class SubjectCheck:
    """The subject's side of a check: the admission, whom an inbound message goes to, and the purposes the
    subject is suppressed for on the channel."""

    admission: Admission
    holder: str | None
    suppressed: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class StoreLife:
    """`whole` when the state store holds everything the durable record does; otherwise it came back empty
    at `since_ms` and is being reloaded."""

    whole: bool
    since_ms: int
