"""What the state store decided, on its way to the durable record (24.3.4): the check and the declarations
publish these on the confirmed queue, and the batch writer stores a batch of them with one statement per
space. Each record carries the state after the call, so the latest one wins whatever order they arrive in."""

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from uuid import UUID

from niadra.domain.coordinate.model import EffectSnapshot


@dataclass(frozen=True, slots=True)
class EffectRecord:
    """`subject_hmac` is the subject a check reserved the effect for, when it named one: a handoff lists the
    subject's effects by it, and an erasure finds them."""

    space_id: UUID
    key_hash: bytes
    snapshot: EffectSnapshot
    source_id: UUID
    subject_hmac: bytes | None = None


@dataclass(frozen=True, slots=True)
class OwnershipRecord:
    """A claim the store accepted, or its release when `released_at_ms` is set. A subject is named by its
    keyed hash, an object by `<type>\\t<namespace>\\t<id>`. An observation names its ownership source in
    `observer`, and a mapped one the event it came from in `basis_event_id`; a task lock names its `task`."""

    space_id: UUID
    claim_id: UUID
    subject_hmac: bytes | None
    object: str | None
    kind: str
    holder: str
    level: str
    via: str
    source_id: UUID
    claimed_at_ms: int
    valid_until_ms: int
    intents: tuple[str, ...]
    epoch: int
    released_at_ms: int | None = None
    holder_kind: str = "agent"
    task: str | None = None
    observer: str | None = None
    basis_event_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class ReleaseRecord:
    space_id: UUID
    claim_id: UUID
    released_at_ms: int


@dataclass(frozen=True, slots=True)
class SuppressionRecord:
    """An entry added (or renewed) by a source: the destination is kept as its keyed hash and sealed canonical
    form, since the list's readers each key it with their own salt."""

    space_id: UUID
    suppression_id: UUID
    handle_type: str
    handle_hmac: bytes
    handle_ct: bytes
    dek_version: int
    purpose: str
    channel: str | None
    reason: str
    source_id: UUID
    since_ms: int
    until_ms: int | None
    resume_after_s: int | None
    lifted_at_ms: int | None = None
    window: str | None = None
    """The local hours it holds in (`Window.encode`), or None for every hour."""


@dataclass(frozen=True, slots=True)
class LiftRecord:
    space_id: UUID
    suppression_id: UUID
    lifted_at_ms: int


@dataclass(frozen=True, slots=True)
class ContactRecord:
    """A contact the coordination saw: `reserved` by an allowed outbound check (`contact_id` is its decision
    id, which a contact token also carries as `jti`), `made` by a `contact.made` declaration. Each declaration
    brings its own `use_id`, so a token used twice shows two uses, and a batch written again counts none
    twice."""

    space_id: UUID
    contact_id: UUID
    subject_hmac: bytes | None
    purpose: str
    channel: str
    source_id: UUID
    agent: str | None
    gateway: str | None
    decision_id: UUID | None
    state: str
    unchecked: bool = False
    reserved_at_ms: int | None = None
    used_at_ms: int | None = None
    use_ids: tuple[UUID, ...] = ()


@dataclass(frozen=True, slots=True)
class CommitmentRecord:
    """A commitment an agent made, with its terms sealed, and whether it holds (`active`) or another of its
    type or an exclusive one held first (`superseded`)."""

    space_id: UUID
    commitment_id: UUID
    external_id: str
    subject_hmac: bytes | None
    type: str
    compatibility: str
    terms_ct: bytes
    dek_version: int
    status: str
    source_id: UUID
    agent: str | None
    made_at_ms: int
    valid_until_ms: int
    score: float | None = None
    named: tuple[bytes, ...] = ()
    """What its terms name a person by (`domain.privacy.mentions`), keyed-hashed: indexed as it is stored."""


@dataclass(frozen=True, slots=True)
class CommitmentStatusRecord:
    """A commitment that no longer holds: superseded, withdrawn, accepted or declined, at `at_ms`."""

    space_id: UUID
    commitment_id: UUID
    status: str
    at_ms: int


@dataclass(frozen=True, slots=True)
class SpendRecord:
    """Units a budget spent on a target, from `spent_at_ms` until `until_ms`, which is what reloads the budget
    after the state store came back empty. `budget` names the state store's window (`c:<purpose>` for a
    contact budget, `p:<operation>` for a paid one) and `slice` the funnel stage's; a settlement carries the
    real cost with `settled`, and a reservation never taken back from it."""

    space_id: UUID
    spend_id: UUID
    budget: str
    slice: str | None
    target_kind: str
    target: bytes
    units: int
    spent_at_ms: int
    until_ms: int
    settled: bool = False


type CoordinationRecord = (
    EffectRecord
    | OwnershipRecord
    | ReleaseRecord
    | SuppressionRecord
    | LiftRecord
    | ContactRecord
    | SpendRecord
    | CommitmentRecord
    | CommitmentStatusRecord
)


@dataclass(frozen=True, slots=True)
class SpaceBatch:
    """One space's share of a batch, folded so each row is written once: the latest state of an effect, a
    release or lift merged into the claim or entry it ends when both are in the batch."""

    space_id: UUID
    effects: tuple[EffectRecord, ...]
    claims: tuple[OwnershipRecord, ...]
    releases: tuple[ReleaseRecord, ...]
    suppressions: tuple[SuppressionRecord, ...]
    lifts: tuple[LiftRecord, ...]
    contacts: tuple[ContactRecord, ...] = ()
    spends: tuple[SpendRecord, ...] = ()
    commitments: tuple[CommitmentRecord, ...] = ()
    statuses: tuple[CommitmentStatusRecord, ...] = ()

    @property
    def size(self) -> int:
        return (
            len(self.effects)
            + len(self.claims)
            + len(self.releases)
            + len(self.suppressions)
            + len(self.lifts)
            + len(self.contacts)
            + len(self.spends)
            + len(self.commitments)
            + len(self.statuses)
        )


def by_space(records: Iterable[CoordinationRecord]) -> list[SpaceBatch]:
    grouped: dict[UUID, list[CoordinationRecord]] = {}
    for record in records:
        grouped.setdefault(record.space_id, []).append(record)
    return [_fold(space_id, items) for space_id, items in grouped.items()]


def _fold(space_id: UUID, records: Sequence[CoordinationRecord]) -> SpaceBatch:
    effects: dict[bytes, EffectRecord] = {}
    claims: dict[UUID, OwnershipRecord] = {}
    releases: dict[UUID, ReleaseRecord] = {}
    suppressions: dict[UUID, SuppressionRecord] = {}
    lifts: dict[UUID, LiftRecord] = {}
    contacts: dict[UUID, ContactRecord] = {}
    spends: dict[UUID, SpendRecord] = {}
    commitments: dict[UUID, CommitmentRecord] = {}
    statuses: dict[UUID, CommitmentStatusRecord] = {}
    for record in records:
        match record:
            case EffectRecord():
                held = effects.get(record.key_hash)
                if held is None or held.snapshot.order <= record.snapshot.order:
                    effects[record.key_hash] = record
            case OwnershipRecord():
                claims[record.claim_id] = record
            case ReleaseRecord():
                releases[record.claim_id] = record
            case SuppressionRecord():
                suppressions[record.suppression_id] = record
            case LiftRecord():
                lifts[record.suppression_id] = record
            case ContactRecord():
                held_contact = contacts.get(record.contact_id)
                contacts[record.contact_id] = (
                    record if held_contact is None else _joined(held_contact, record)
                )
            case SpendRecord():
                held_spend = spends.get(record.spend_id)
                if held_spend is None or record.settled or not held_spend.settled:
                    spends[record.spend_id] = record
            case CommitmentRecord():
                commitments[record.commitment_id] = record
            case CommitmentStatusRecord():
                held_status = statuses.get(record.commitment_id)
                if held_status is None or held_status.at_ms <= record.at_ms:
                    statuses[record.commitment_id] = record
    for claim_id in list(releases):
        if claim_id in claims:
            claims[claim_id] = replace(claims[claim_id], released_at_ms=releases.pop(claim_id).released_at_ms)
    for commitment_id in list(statuses):
        if commitment_id in commitments:
            commitments[commitment_id] = replace(
                commitments[commitment_id], status=statuses.pop(commitment_id).status
            )
    for suppression_id in list(lifts):
        if suppression_id in suppressions:
            lifted = lifts.pop(suppression_id).lifted_at_ms
            suppressions[suppression_id] = replace(suppressions[suppression_id], lifted_at_ms=lifted)
    return SpaceBatch(
        space_id,
        tuple(effects.values()),
        tuple(claims.values()),
        tuple(releases.values()),
        tuple(suppressions.values()),
        tuple(lifts.values()),
        tuple(contacts.values()),
        tuple(spends.values()),
        tuple(commitments.values()),
        tuple(statuses.values()),
    )


def _joined(held: ContactRecord, later: ContactRecord) -> ContactRecord:
    """One row per contact in a statement: its reservation and every use the batch declared."""
    made = held if held.state == "made" else later if later.state == "made" else None
    return replace(
        made or later,
        subject_hmac=held.subject_hmac or later.subject_hmac,
        agent=held.agent or later.agent,
        gateway=held.gateway or later.gateway,
        decision_id=held.decision_id or later.decision_id,
        reserved_at_ms=held.reserved_at_ms or later.reserved_at_ms,
        used_at_ms=max((t for t in (held.used_at_ms, later.used_at_ms) if t is not None), default=None),
        unchecked=held.unchecked and later.unchecked,
        use_ids=tuple(dict.fromkeys((*held.use_ids, *later.use_ids))),
    )


def about(record: CoordinationRecord) -> tuple[bytes, int] | None:
    """The subject a record names, by keyed hash, and when it was made; None for a record about no subject
    or one that only ends a row (a release, a lift, a commitment's status)."""
    match record:
        case EffectRecord(subject_hmac=bytes() as subject):
            return subject, record.snapshot.reserved_at_ms
        case OwnershipRecord(subject_hmac=bytes() as subject):
            return subject, record.claimed_at_ms
        case ContactRecord(subject_hmac=bytes() as subject):
            return subject, record.reserved_at_ms or record.used_at_ms or 0
        case SpendRecord(target_kind="subject"):
            return record.target, record.spent_at_ms
        case CommitmentRecord(subject_hmac=bytes() as subject):
            return subject, record.made_at_ms
    return None


def unfenced(records: Iterable[CoordinationRecord], fences: Mapping[bytes, int]) -> list[CoordinationRecord]:
    """The records an erasure did not fence off: what a record says of a subject erased at or after the record
    was made never reaches the durable record again (24.5.5). What was made later stays."""
    kept = []
    for record in records:
        named = about(record)
        if named is None or named[0] not in fences or named[1] > fences[named[0]]:
            kept.append(record)
    return kept
