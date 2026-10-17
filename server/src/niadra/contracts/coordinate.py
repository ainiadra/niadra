"""Ownership and effects: the coordination decision, declarations, claims and locks, effects exactly once,
handoffs, suppressions, the contact token and its public keys, and the shadow report (7.7, 24.3.4).

The open specs `coordination`, `contact-token` and `suppression-list` are generated from these models;
their prose fixes what the fields mean.
"""

from datetime import datetime
from typing import Annotated, Any, Literal, Self
from uuid import UUID

from pydantic import Field, RootModel, StringConstraints, model_validator

from niadra.contracts.common import Handle, IdStr, Model, ObjectRef, ShortStr
from niadra.contracts.events import MAX_EVENT_TEXT
from niadra.contracts.window import ContactWindow
from niadra.domain.coordinate.model import ClaimKind, ClaimVia, EffectKind
from niadra.domain.vocabulary import Verification

Decision = Literal["allow", "defer", "deny", "handoff_to"]
EffectCheckState = Literal["none", "in_flight", "done", "unknown_outcome"]
Reason = Annotated[str, StringConstraints(min_length=1, max_length=500)]
Name = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]{0,39}$")]
KeyedHash = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_-]{43}$")]
"""An HMAC-SHA256 in base64url without padding."""
ContactToken = Annotated[
    str, StringConstraints(pattern=r"^nct1\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]{86}$", max_length=1024)
]

MAX_TOKEN_TTL_S = 120
ANY_PURPOSE = "any"
"""A suppression of `any` purpose applies to every purpose: a person's own contact hours hold whatever the
contact is for. No coordination document declares a purpose of that name."""


class CheckRequest(Model):
    """What an agent is about to do. Without a subject there is no budget, suppression or owner to consult:
    such a check only asks about the effect key."""

    subject: Handle | None = None
    object: ObjectRef | None = None
    agent: ShortStr
    intent: Name
    channel: Name | None = None
    direction: Literal["inbound", "outbound"]
    purpose: Name
    task: Name | None = None
    effect_key: IdStr | None = Field(
        default=None, description="The business fact's key; reserved atomically when nothing holds it."
    )
    effect_kind: EffectKind = EffectKind.DELIVERY
    gateway_id: Name | None = None
    destination_hash: KeyedHash | None = Field(
        default=None,
        description="The token's `rcpt`, computed by a caller that holds the gateway's key, when the "
        "destination is not the subject's handle.",
    )


class Owner(Model):
    """Who holds the subject or the object now: the most restrictive valid claim, where it came from and
    until when. `epoch` fences a release."""

    holder: ShortStr
    kind: ClaimKind
    level: Name
    source: ShortStr
    via: ClaimVia
    since: datetime
    valid_until: datetime
    epoch: int


class Lock(Model):
    kind: ShortStr
    task: ShortStr | None = None
    holder: ShortStr
    until: datetime


class EffectStatus(Model):
    """`none` means nothing held the key and this check reserved it: the caller acts and then declares."""

    state: EffectCheckState
    attempt: int | None = None


class ChannelState(Model):
    free_form_until: datetime | None = None
    template_required: bool = False
    quiet_until: datetime | None = None


class ContactBudget(Model):
    remaining: int
    next_allowed_at: datetime | None = None


class CommitmentRef(Model):
    id: IdStr
    type: Name
    valid_until: datetime | None = None


class PromiseRef(Model):
    id: IdStr
    due: datetime | None = None
    owner: ShortStr


class CheckResult(Model):
    """The coordination decision. `reasons` are codes (the coordination spec, 4); `holder` is who to hand
    off to with `handoff_to`. `contact_token` comes only with an allowed outbound contact of a purpose that
    needs one."""

    decision: Decision
    reasons: list[ShortStr] = Field(default_factory=list)
    holder: ShortStr | None = None
    owner: Owner | None = None
    locks: list[Lock] = Field(default_factory=list)
    effect: EffectStatus | None = None
    channel: ChannelState | None = None
    contact_budget: dict[str, ContactBudget] = Field(default_factory=dict)
    suppressions: list[Name] = Field(default_factory=list)
    contact_window_until: datetime | None = Field(
        default=None,
        description="With `contact_window`: when the subject's contact window on the channel ends, the first "
        "moment the contact may go.",
    )
    commitments_active: list[CommitmentRef] = Field(default_factory=list)
    promises_open: list[PromiseRef] = Field(default_factory=list)
    decision_id: UUID
    valid_for_s: int = Field(ge=0)
    contact_token: ContactToken | None = None

    @model_validator(mode="after")
    def _shape_matches_decision(self) -> Self:
        if self.decision == "handoff_to" and not self.holder:
            raise ValueError("a handoff names the holder")
        if self.contact_token is not None and self.decision != "allow":
            raise ValueError("only an allowed contact carries a token")
        return self


class CheckBatchRequest(Model):
    checks: list[CheckRequest] = Field(min_length=1, max_length=500)


class CheckBatchResponse(Model):
    results: list[CheckResult]


class LeaseDetail(Model):
    """An owner lease or a case, held by the declaring agent. `level` defaults to the space's least
    restrictive level."""

    level: Name | None = None
    lease_s: int = Field(ge=1, le=86_400)
    intents: list[Name] = Field(default_factory=list, max_length=20)


class ReleaseDetail(Model):
    """The claim a case closes, with the epoch it got when it was accepted."""

    claim_id: UUID
    epoch: int = Field(ge=0)


class TaskLockDetail(Model):
    task: Name
    lease_s: int = Field(ge=1, le=86_400)


class ContactMadeDetail(Model):
    """`jti` is the contact token's; `unchecked` says the contact left without a decision, because the
    purpose fails open and none came in time."""

    decision_id: UUID | None = None
    jti: UUID | None = None
    purpose: Name
    channel: Name
    gateway_id: Name | None = None
    unchecked: bool = False


class EffectDetail(Model):
    """How the attempt the caller holds ended."""

    effect_key: IdStr
    state: Literal["done", "failed", "unknown_outcome"]
    attempt: int = Field(ge=1)


class CommitmentMadeDetail(Model):
    """`score` ranks commitments of a `best_wins` type: the higher holds. Its `terms` are kept whole, in the
    commitment and in the action memory records of it (`terms_text`), up to an event's text."""

    commitment_id: IdStr
    type: Name
    terms: dict[str, Any] = Field(default_factory=dict)
    valid_until: datetime | None = None
    score: float | None = None

    @model_validator(mode="after")
    def _terms_fit(self) -> Self:
        if len(terms_text(self.terms)) > MAX_EVENT_TEXT:
            raise ValueError(f"terms as text pass {MAX_EVENT_TEXT} characters")
        return self


def terms_text(terms: dict[str, Any]) -> str:
    """A commitment's terms as the action memory keeps them: `key: value`, by key."""
    return ", ".join(f"{k}: {v}" for k, v in sorted(terms.items()))


class CommitmentDecidedDetail(Model):
    """The person accepted or declined a commitment, maybe in a later conversation."""

    commitment_id: IdStr
    outcome: Literal["accepted", "declined"]


class CommitmentWithdrawnDetail(Model):
    commitment_id: IdStr


class HandoffDetail(Model):
    handoff_id: IdStr
    status: Literal["created", "accepted", "closed"]
    outcome: Name | None = None


class SuppressionAddedDetail(Model):
    """The subject may not be contacted for `purpose` (`any`: every purpose), on `channel` or on every
    channel. `until` defaults to the reason's own interval, or to no end. With `window`, only during those
    local hours (the suppression-list spec, 6.1)."""

    purpose: Name
    channel: Name | None = None
    reason: Name
    until: datetime | None = None
    window: ContactWindow | None = None


class SuppressionLiftedDetail(Model):
    """Lifts what the same source suppressed for `purpose` and `channel`."""

    purpose: Name
    channel: Name | None = None


class _Declaration(Model):
    subject: Handle | None = None
    object: ObjectRef | None = None
    agent: ShortStr


class CaseOpened(_Declaration):
    kind: Literal["case.opened"]
    detail: LeaseDetail


class CaseClosed(_Declaration):
    kind: Literal["case.closed"]
    detail: ReleaseDetail


class LeaseDeclared(_Declaration):
    kind: Literal["lease"]
    detail: LeaseDetail


class TaskLockDeclared(_Declaration):
    kind: Literal["task_lock"]
    detail: TaskLockDetail


class ContactMade(_Declaration):
    kind: Literal["contact.made"]
    detail: ContactMadeDetail


class EffectDeclared(_Declaration):
    kind: Literal["effect"]
    detail: EffectDetail


class CommitmentMade(_Declaration):
    kind: Literal["commitment.made"]
    detail: CommitmentMadeDetail


class CommitmentWithdrawn(_Declaration):
    kind: Literal["commitment.withdrawn"]
    detail: CommitmentWithdrawnDetail


class CommitmentDecided(_Declaration):
    kind: Literal["commitment.decided"]
    detail: CommitmentDecidedDetail


class HandoffDeclared(_Declaration):
    kind: Literal["handoff"]
    detail: HandoffDetail


class SuppressionAdded(_Declaration):
    kind: Literal["suppression.added"]
    detail: SuppressionAddedDetail


class SuppressionLifted(_Declaration):
    kind: Literal["suppression.lifted"]
    detail: SuppressionLiftedDetail


Declaration = Annotated[
    CaseOpened
    | CaseClosed
    | LeaseDeclared
    | TaskLockDeclared
    | ContactMade
    | EffectDeclared
    | CommitmentMade
    | CommitmentWithdrawn
    | CommitmentDecided
    | HandoffDeclared
    | SuppressionAdded
    | SuppressionLifted,
    Field(discriminator="kind"),
]


class DeclareRequest(RootModel[Declaration]):
    """What happened, after the fact: one kind of declaration, with the fields of its kind in `detail` (the
    coordination spec, 5)."""


class DeclareResult(Model):
    accepted: bool
    decision_id: UUID | None = None


class ClaimRequest(Model):
    """A declared claim, held by `holder`. `level` defaults to the space's least restrictive level; a
    declaration is refused (409 `lease_held`) while another holder's claim is at least as restrictive, and the
    same holder renews its own. `lease_s` is capped by the space's longest declared lease. A task lock names
    an object and its `task`, and is refused (409 `task_locked`) while another holder has that task."""

    subject: Handle | None = None
    object: ObjectRef | None = None
    kind: ClaimKind
    holder: ShortStr
    level: Name | None = None
    task: Name | None = None
    lease_s: int = Field(ge=1, le=86_400)
    intents: list[Name] = Field(default_factory=list, max_length=20)
    source: Name | None = Field(
        default=None,
        description="An observation of this ownership source, read and sent by the company's worker: never "
        "refused, replacing what the source said before, and capped by the source's validity.",
    )
    observed_at: datetime | None = Field(
        default=None,
        description="When the source saw it; an observation older than the source's last is ignored.",
    )

    @model_validator(mode="after")
    def _lock_names_its_task(self) -> Self:
        if (self.kind is ClaimKind.TASK_LOCK) != (self.task is not None):
            raise ValueError("a task lock names its task, and only a task lock does")
        if self.kind is ClaimKind.TASK_LOCK and self.object is None:
            raise ValueError("a task lock is on an object")
        return self


class OwnershipClaim(Model):
    claim_id: IdStr
    kind: ClaimKind
    holder: str
    level: Name
    valid_until: datetime
    epoch: int


class ClaimRelease(Model):
    epoch: int


class EffectReserve(Model):
    effect_key: IdStr
    fact: ObjectRef | None = None
    kind: EffectKind
    cost_units: int = Field(default=0, ge=0)


class Effect(Model):
    """`effect_id` is the key's keyed hash: it names the effect in paths, where the key itself, which may
    carry a conversation id, never goes."""

    effect_id: KeyedHash
    state: Literal["reserved", "done", "failed", "unknown_outcome"]
    attempt: int
    reserved_at: datetime
    done_at: datetime | None = None


class EffectSettle(Model):
    state: Literal["done", "failed", "unknown_outcome"]
    attempt: int = Field(ge=1)


class HandoffCreate(Model):
    """Hands the subject to `target`. The package is compiled at `level`, the verification the receiving
    side has of the subject, so it never carries what that side could not read."""

    subject: Handle
    conversation_id: IdStr | None = None
    agent: ShortStr | None = Field(
        default=None, description="The agent handing off; the calling key's name when absent."
    )
    target: ShortStr
    reason: Reason | None = None
    level: Verification = Verification.V0
    expected_by: datetime | None = Field(
        default=None, description="When the outcome is due; the space's default wait when absent."
    )


class HandoffContext(Model):
    """The compiled `handoff` view, at the receiving side's policy and verification level. `etag` is the
    pack's it was compiled from, absent for a subject the memory knows nothing of yet."""

    text: str
    etag: ShortStr | None = None
    policy_version: ShortStr | None = None


class OpenObject(Model):
    ref: ObjectRef
    state: ShortStr | None = None
    since: datetime | None = None


class EffectSeen(Model):
    effect_id: KeyedHash
    kind: EffectKind
    state: Literal["reserved", "done", "failed", "unknown_outcome"]


class OutcomeRequest(Model):
    """How the receiving side reports back: one of `vocabulary`, by `expected_by`."""

    vocabulary: list[Name] = Field(default_factory=list, max_length=50)
    expected_by: datetime | None = None


class HandoffPackage(Model):
    """What a handoff carries to whoever receives it (the coordination spec, 7)."""

    spec: Literal["handoff-package.v0"] = "handoff-package.v0"
    handoff_id: IdStr
    created_at: datetime
    from_agent: ShortStr
    target: ShortStr
    subject: Handle | None = Field(default=None, description="Whom the handoff is about.")
    level: Verification
    reason: Reason | None = None
    context: HandoffContext
    owner: Owner | None = None
    open_objects: list[OpenObject] = Field(default_factory=list, max_length=50)
    open_objects_more: int = Field(
        default=0, ge=0, description="The subject's open objects past the 50 a package carries, left out."
    )
    commitments_active: list[CommitmentRef] = Field(default_factory=list)
    promises_open: list[PromiseRef] = Field(default_factory=list)
    effects: list[EffectSeen] = Field(default_factory=list)
    suppressions: list[Name] = Field(default_factory=list)
    outcome: OutcomeRequest = Field(default_factory=OutcomeRequest)


class Handoff(Model):
    handoff_id: IdStr
    status: Literal["created", "accepted", "closed", "expired"]
    package: HandoffPackage | None = None
    outcome: ShortStr | None = None
    created_at: datetime
    expected_by: datetime | None = None


class HandoffOutcome(Model):
    outcome: Name
    detail: dict[str, Any] = Field(default_factory=dict)


class Suppression(Model):
    """One entry of the suppression list: `key` is the subject's handle keyed with the reading source's salt
    (the suppression-list spec); `removed` takes the entry `id` out of the reader's copy. The reason stays
    with the producer: a vendor learns that it may not contact, never why."""

    id: IdStr
    key: KeyedHash
    purpose: Name
    channel: Name | None = None
    since: datetime
    until: datetime | None = None
    window: ContactWindow | None = None
    removed: bool = False


class SuppressionPage(Model):
    """A page of changes, oldest first. With `reset`, the reader drops its copy before applying `items`:
    the salt changed, or the cursor is older than what the list keeps."""

    salt_id: IdStr
    reset: bool = False
    items: list[Suppression]
    next_cursor: str | None = None


class SuppressionSalt(Model):
    salt_id: IdStr
    salt: KeyedHash
    valid_from: datetime


class ContactTokenClaims(Model):
    """The payload of a contact token `nct1.<payload>.<signature>` (the contact-token spec): what a gateway
    checks offline before it lets an outbound message out. The server issues every token through it, and the
    published contact-token schema is generated from it, so the format and the tokens cannot drift apart."""

    kid: Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_-]{8,64}$")]
    space: UUID
    jti: UUID
    purpose: Name
    channel: Name
    rcpt: KeyedHash
    gateway: Name
    iat: int = Field(ge=0)
    exp: int = Field(ge=0)

    @model_validator(mode="after")
    def _short_lived(self) -> Self:
        if not 0 < self.exp - self.iat <= MAX_TOKEN_TTL_S:
            raise ValueError(f"a contact token lives more than 0 and at most {MAX_TOKEN_TTL_S} seconds")
        return self


class ContactKey(Model):
    """An Ed25519 public key as a JWK (RFC 8037). A `retiring` key still verifies and signs nothing new."""

    kid: Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_-]{8,64}$")]
    kty: Literal["OKP"] = "OKP"
    crv: Literal["Ed25519"] = "Ed25519"
    x: Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_-]{43}$")]
    space: UUID
    status: Literal["active", "retiring"] = "active"
    not_after: datetime | None = None


class ContactKeys(Model):
    """A space's public keys a gateway checks a contact token with, offline."""

    keys: list[ContactKey]


class CoordinationReport(Model):
    """A shadow run: the space's policy applied to its outbound messages already ingested, with nothing
    enforced. `summary` holds the conflicts found and per 1,000 customers a month, without anyone in them."""

    report_id: IdStr
    status: Literal["pending", "done", "failed"]
    days: int
    requested_at: datetime
    finished_at: datetime | None = None
    summary: dict[str, Any] = Field(default_factory=dict)


class CoordinationReportPage(Model):
    items: list[CoordinationReport]
    next_cursor: str | None = None


class OwnershipCount(Model):
    kind: Literal["owner", "case", "task_lock"]
    level: ShortStr
    via: Literal["declaration", "mapping", "worker"]
    holder_kind: Literal["agent", "human", "queue", "system"]
    claims: int = Field(description="Claims that hold now.")
    holders: int = Field(description="Distinct holders among them.")


class EffectCount(Model):
    kind: ShortStr
    state: Literal["reserved", "done", "failed", "unknown_outcome"]
    effects: int


class ConflictCounts(Model):
    """What coordination settled between parties in the window."""

    ownership_overlaps: int = Field(
        description="Subjects and objects two holders claimed at the same time: the more restrictive held."
    )
    commitments_superseded: int = Field(description="Commitments another of the subject's held first.")
    handoffs_expired: int = Field(description="Handoffs whose outcome did not come back in time.")
    tokens_reused: int = Field(description="Contact tokens declared used more than once.")


class ContactBudgetUse(Model):
    purpose: ShortStr
    limit: int
    per_hours: int
    subjects: int = Field(description="Subjects with a contact still in the budget's window.")
    at_limit: int = Field(description="Of them, the ones with no contact left in the window.")
    spent: int


class ContactCount(Model):
    purpose: ShortStr
    direction: Literal["inbound", "outbound"]
    state: Literal["reserved", "made", "expired"]
    contacts: int


class CoordinationOverview(Model):
    """What coordination holds now and decided in the window, in counts: no subject, handle or message is in
    it."""

    since: datetime
    ownership: list[OwnershipCount] = Field(default_factory=list)
    effects: list[EffectCount] = Field(default_factory=list)
    awaiting_decision: int = Field(
        description="Effects whose outcome nobody knows, at any age: never sent again by themselves, they "
        "wait for a system event or a person."
    )
    conflicts: ConflictCounts
    budgets: list[ContactBudgetUse] = Field(default_factory=list)
    contacts: list[ContactCount] = Field(default_factory=list)


class ShadowRequest(Model):
    days: int = Field(default=30, ge=1, le=90)


class ShadowRun(Model):
    report_id: IdStr
    status: Literal["pending", "done"]


class CoordinationBlock(Model):
    """What coordination knows of the subject, read beside the pack by `include`: advice for the turn, never a
    decision, which only a check takes. `contact_budget` has the purposes with a budget."""

    owner: Owner | None = None
    suppressions: list[Name] = Field(default_factory=list)
    contact_budget: dict[str, ContactBudget] = Field(default_factory=dict)
    commitments_active: list[CommitmentRef] = Field(default_factory=list)
