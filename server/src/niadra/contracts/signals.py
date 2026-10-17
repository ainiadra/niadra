"""Subject signals, the constraints block, the reviewable profile, review requests, legal holds and the
measurement of outcomes and experiments (7.7, 24.3.2, 24.5, 24.7).

The interactions are the `interaction.v0` spec and the block the `constraints.v0` spec.
"""

from datetime import date, datetime
from typing import Annotated, Any, Literal, Self
from uuid import UUID

from pydantic import Field, StringConstraints, model_validator

from niadra.contracts.common import Handle, IdStr, Model, ObjectRef, ShortStr
from niadra.contracts.window import ContactWindow

Reason = Annotated[str, StringConstraints(min_length=1, max_length=500)]
Ref = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]{0,39}:[^:\s]{1,256}:\S{1,512}$")]
"""An object as `type:namespace:id`, the `ObjectRef` of 7.1 in one string; the id may hold colons."""
AttrPath = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]{0,39}\.[a-z][a-z0-9_]{0,63}$")]
"""A field of a declared type, `type.field`: what a preference or a constraint is about."""
AttrName = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]{0,63}(?:\.[a-z0-9_]{1,64}){0,3}$")]
"""A subject attribute, dotted: `size.pants`, `network.preferred`, `diet`."""
FieldName = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]{0,63}$")]
For = Annotated[str, StringConstraints(pattern=r"^(?:self|gift|beneficiary:[A-Za-z0-9_-]{1,64})$")]
"""Whom an interaction or a block is for: the subject, a beneficiary under the profile, or a gift."""
ExposureId = UUID
"""A UUIDv7 the SDK mints at delivery; the exposure token carries its 16 bytes."""
Text = Annotated[str, StringConstraints(max_length=256)]
Scalar = bool | int | float | Text
Op = Literal["in", "not_in", "eq", "ne", "lt", "lte", "gt", "gte", "between"]
Scope = Literal["turn", "session", "persistent"]
HardScope = Literal["turn", "session", "persistent", "object"]
"""`object`: an instruction someone said about the object the read is about, never the subject's own."""

_ONE_VALUE = frozenset({"eq", "ne", "lt", "lte", "gt", "gte"})


def _arity(op: str, values: list[Any]) -> None:
    if op in _ONE_VALUE and len(values) != 1:
        raise ValueError(f"`{op}` takes exactly one value")
    if op == "between" and len(values) != 2:
        raise ValueError("`between` takes exactly two values, the low and the high end")


class PresentedItem(Model):
    pos: int = Field(ge=1, le=10_000, description="1-based position in the whole list, across pages.")
    ref: Ref
    shown: dict[FieldName, Scalar | None] = Field(
        default_factory=dict,
        max_length=32,
        description="The values the item showed, for the fields its type tracks or lets be claimed; what "
        "`changes_since_seen` later compares against.",
    )
    team: Literal["a", "b"] | None = Field(
        default=None, description="Which of two interleaved lists contributed the item (24.7)."
    )


class _Interaction(Model):
    for_: For = Field(default="self", alias="for")


class Presented(_Interaction):
    """A list delivered to the person: the exposure. Its canonical moment is `delivered_at`, when the
    interface bridge handed the list to the client, never when a tool returned it or the model named it."""

    kind: Literal["presented"]
    exposure_id: ExposureId
    list_id: ShortStr
    list_kind: ShortStr = "results"
    page: int = Field(default=1, ge=1, le=1000, description='"See more" is the same list with `page + 1`.')
    offset: int = Field(default=0, ge=0, le=10_000)
    parent_list_id: ShortStr | None = None
    items: list[PresentedItem] = Field(min_length=1, max_length=100)
    visible_k: int | None = Field(
        default=None, ge=1, le=100, description="How many of the first items were visible on delivery."
    )
    delivered_at: datetime
    method: Literal["bridge", "otel", "tool_result"] = Field(
        default="bridge",
        description="`bridge` from the interface bridge, `otel` from a `niadra.exposure` span, `tool_result` "
        "inferred from what a tool returned: labeled, because it counts more than was shown.",
    )

    @model_validator(mode="after")
    def _positions(self) -> Self:
        positions = [item.pos for item in self.items]
        if len(set(positions)) != len(positions) or len({item.ref for item in self.items}) != len(self.items):
            raise ValueError("each position and each object appears once in an exposure")
        if min(positions) <= self.offset:
            raise ValueError("positions count from the start of the list: every `pos` is past `offset`")
        return self


class Seen(_Interaction):
    """A ping of the list component: how far the person saw."""

    kind: Literal["seen"]
    exposure_id: ExposureId
    max_index_seen: int = Field(ge=1, le=10_000, description="The highest `pos` the component showed.")


class Engaged(_Interaction):
    kind: Literal["engaged"]
    exposure_id: ExposureId | None = Field(default=None, description="Absent for an item of no list.")
    ref: Ref
    how: Literal["click", "detail", "mention", "add_to_cart", "compare", "share", "other"]


class Feedback(_Interaction):
    kind: Literal["feedback"]
    ref: Ref
    polarity: Literal["positive", "negative"]
    reason_attrs: list[AttrPath] = Field(default_factory=list, max_length=10)


class Preference(_Interaction):
    """What the person wants or refuses, said or captured from a tool's arguments; only these become hard."""

    kind: Literal["preference"]
    attr: AttrPath
    op: Op
    values: list[Scalar] = Field(min_length=1, max_length=50)
    strength: Literal["must", "must_not", "prefer", "avoid"]
    scope: Scope = "session"
    category: ShortStr | None = Field(default=None, description="Holds only in this category of the type.")
    expires_at: datetime | None = None
    source: Literal["stated", "tool_args", "correction"] = "stated"

    @model_validator(mode="after")
    def _values_fit_op(self) -> Self:
        _arity(self.op, self.values)
        return self


class Attribute(_Interaction):
    """What the person is: a size, a preferred network, a diet. Said here; the lifecycle's outcomes (bought,
    kept, returned for size) derive the rest on the server, never an agent."""

    kind: Literal["attribute"]
    name: AttrName
    system: ShortStr | None = Field(default=None, description="The measuring system: `BR`, `EU`, `US`.")
    value: Scalar
    source: Literal["stated", "correction"] = "stated"
    category: ShortStr | None = None


class Watch(_Interaction):
    """An explicit "let me know when": the only way an interest raises a notice."""

    kind: Literal["watch"]
    ref: Ref
    condition: Annotated[str, StringConstraints(min_length=1, max_length=500)] = Field(
        description="A `niadra-expr` expression over the object's fields."
    )
    consent_event_key: IdStr
    expires_at: datetime | None = None


class Unwatch(_Interaction):
    kind: Literal["unwatch"]
    ref: Ref


Interaction = Annotated[
    Presented | Seen | Engaged | Feedback | Preference | Attribute | Watch | Unwatch,
    Field(discriminator="kind"),
]


class ConstraintsRequest(Model):
    subject: Handle
    conversation_id: IdStr | None = None
    for_: For = Field(default="self", alias="for")
    tool: ShortStr | None = Field(default=None, description="Rendered for this tool's bindings.")


class BlockSubject(Model):
    for_: For = Field(default="self", alias="for")


class ConstraintOrigin(Model):
    kind: Literal["stated", "tool_args", "correction"]
    event_key: IdStr | None = None
    turn_id: IdStr | None = None


class HardConstraint(Model):
    """Only what the person said: an inference is never hard."""

    id: Annotated[str, StringConstraints(pattern=r"^h[0-9]{1,4}$")]
    attr: AttrPath
    op: Op
    values: list[Scalar] = Field(min_length=1, max_length=50)
    source: Literal["stated", "tool_args", "correction"]
    scope: HardScope
    category: ShortStr | None = None
    relax: Literal["never", "ask"] = "never"
    origin: ConstraintOrigin
    expires_at: datetime | None = None
    object: Ref | None = Field(
        default=None, description="With `scope: object`, the object the instruction is about."
    )

    @model_validator(mode="after")
    def _values_fit_op(self) -> Self:
        _arity(self.op, self.values)
        if (self.scope == "object") != (self.object is not None):
            raise ValueError("`object` goes with `scope: object`, and only with it")
        return self


class ObjectInstruction(Model):
    """What someone said must or must not be done about an object the read is about: a task on the object,
    a read by the object, or a task view of its type. Hard, never relaxed, never inferred; one about a field
    of the object's type is also in `hard`, where a tool binding applies it."""

    id: Annotated[str, StringConstraints(pattern=r"^i[0-9]{1,4}$")]
    object: Ref | None = Field(default=None, description="The object it is about.")
    about: UUID | None = Field(
        default=None,
        description="Naming no object: the organization (its profile) it is about, a client the read is "
        "about, its own reads or a contact's read with `about`.",
    )
    text: Text = Field(description="As it was said, short: what a reader honors without a tool binding.")
    strength: Literal["must", "must_not"]
    attr: AttrPath | None = Field(default=None, description="The field of the object's type it is about.")
    hard: Annotated[str, StringConstraints(pattern=r"^h[0-9]{1,4}$")] | None = Field(
        default=None, description="The entry of `hard` that carries it for a tool, when `attr` is set."
    )
    role: ShortStr | None = Field(
        default=None, description="The role on the object of who said it (`owner`, `participant`, `lawyer`)."
    )
    only_with: ShortStr | None = Field(
        default=None,
        description="The one person it holds with, as the conversation named them: it binds the readers "
        "dealing with that person, not whoever is speaking. With `contact`, the person it is the "
        "preference of.",
    )
    contact: bool = Field(
        default=False,
        description="A contact preference (a channel, hours): how the company reaches `only_with`, never a "
        "limit on what may be said to them.",
    )
    said_at: datetime
    expires_at: datetime | None = None

    @model_validator(mode="after")
    def _one_target(self) -> Self:
        if (self.object is None) == (self.about is None):
            raise ValueError("an instruction is about an object or an organization, one of them")
        return self


class SoftConstraint(Model):
    id: Annotated[str, StringConstraints(pattern=r"^s[0-9]{1,4}$")]
    attr: AttrPath
    value: Scalar
    polarity: Literal["prefer", "avoid"] = "prefer"
    weight: float = Field(ge=0, le=1)
    confidence: float = Field(ge=0, le=1)
    source: Literal["inferred", "stated"]
    category: ShortStr | None = None


class AttributeEntry(Model):
    id: Annotated[str, StringConstraints(pattern=r"^z[0-9]{1,4}$")]
    name: AttrName
    system: ShortStr | None = None
    value: Scalar
    confidence: float = Field(ge=0, le=1)
    source: Literal["stated", "correction", "acquired", "kept", "returned_for_size", "inferred"]
    apply: Literal["always", "when_asked"]
    category: ShortStr | None = None

    @model_validator(mode="after")
    def _only_said_applies_unasked(self) -> Self:
        if self.apply == "always" and self.source not in ("stated", "correction"):
            raise ValueError("an attribute the person did not say applies only when asked (`when_asked`)")
        return self


class ShownValue(Model):
    """A number an object showed the subject, as they were shown it."""

    v: float
    role: ShortStr | None = Field(default=None, description="The field's role, for claims.")
    claim_safe: bool = Field(
        description="Whether it may be claimed now: its type allows claims of the field, and no more than "
        "the field's age for claims has passed since it was shown."
    )


class Shown(Model):
    ref: Ref
    times: int = Field(ge=1)
    last_at: datetime | None = None
    values: dict[str, ShownValue] = Field(
        default_factory=dict,
        description="The numbers the object was last shown with (a price, a total, a discount, an "
        "installment), by field: each field its type declares as `money`, `percent` or `number`. A claim "
        "check takes them as evidence for the offer an agent quotes back.",
    )


class CompanyRule(Model):
    """A rule of the company delivered in the block, with where it lives and its rank in the order the company
    declared: the block never ranks two origins by itself."""

    id: Annotated[str, StringConstraints(pattern=r"^r[0-9]{1,4}$")]
    origin: ShortStr = Field(description="`code`, `db:<table>`, `prompt:<version>`, `policy:<version>`...")
    rank: int = Field(ge=1, le=100)
    attr: AttrPath | None = None
    op: Op | None = None
    values: list[Scalar] = Field(default_factory=list, max_length=50)


class Conflict(Model):
    """Two entries that cannot both hold, and the one that won: the current utterance over what was stated
    before, and a stated entry over an inferred one. Between two company rules, it is also a data issue."""

    ids: list[str] = Field(min_length=2, max_length=10)
    kept: str
    by: Literal["current_utterance", "precedence", "rule_rank"]


class Rendered(Model):
    """The block for one tool, through its bindings (`constraints.v0`, rendering)."""

    tool: ShortStr
    mode: Literal["advisory", "apply"]
    args: dict[str, Any] = Field(description="Argument values that carry hard constraints and attributes.")
    residual: list[str] = Field(default_factory=list, description="Ids no argument of the tool can express.")
    post_filter: list[str] = Field(
        default_factory=list, description="Residual ids the SDK filters from the results of an overfetch."
    )


PRECEDENCE: list[Literal["current_utterance", "stated_persistent", "inferred"]] = [
    "current_utterance",
    "stated_persistent",
    "inferred",
]


class ContactWindowEntry(Model):
    """A contact window the subject said, in their own block (the suppression-list spec, 6.1)."""

    channel: ShortStr | None = None
    window: ContactWindow
    said_at: datetime


class ConstraintsBlock(Model):
    """What one subject wants, refuses and is, for the tools of this turn (`constraints.v0`)."""

    version: Annotated[str, StringConstraints(pattern=r"^cv_[0-9a-f]{8,64}$")] = Field(
        description="A digest of the block, which the turn record cites."
    )
    subject: BlockSubject = Field(default_factory=BlockSubject)
    hard: list[HardConstraint] = Field(default_factory=list, max_length=100)
    soft: list[SoftConstraint] = Field(default_factory=list, max_length=100)
    attributes: list[AttributeEntry] = Field(default_factory=list, max_length=50)
    exclude: list[Ref] = Field(default_factory=list, max_length=200)
    already_presented: list[Shown] = Field(default_factory=list, max_length=200)
    relaxation_order: list[str] = Field(
        default_factory=list, description="What gives way first when a search finds nothing: `soft`, ids."
    )
    precedence: list[Literal["current_utterance", "stated_persistent", "inferred"]] = Field(
        default_factory=lambda: list(PRECEDENCE)
    )
    rules: list[CompanyRule] = Field(default_factory=list, max_length=50)
    conflicts: list[Conflict] = Field(default_factory=list, max_length=50)
    instructions: list[ObjectInstruction] = Field(
        default_factory=list,
        max_length=500,
        description="What people said must or must not be done about the objects the read is about.",
    )
    omitted: dict[str, int] = Field(
        default_factory=dict,
        description="How many entries of each kind (`hard`, `soft`, `exclude`, `already_presented`, "
        "`instructions`) the block's sizes left out; the restrictions keep their room first. Absent when "
        "nothing was left out; `text` says it too.",
    )
    ask: list[ShortStr] = Field(
        default_factory=list, description="Questions the agent should ask, as `for_whom`, before assuming."
    )
    contact_windows: list[ContactWindowEntry] = Field(
        default_factory=list,
        max_length=10,
        description="The local hours the subject said they may not be contacted in, one per channel (null: "
        "every channel), the latest said: a contact of any purpose waits for the window's end.",
    )
    text: str = Field(
        description="The block as lines for a model, in the space's language, each field by its label and "
        "each operator in words; an empty string when the block has nothing to say. Not part of `version`.",
    )
    rendered: Rendered | None = None

    @model_validator(mode="after")
    def _ids(self) -> Self:
        ids = [
            *(c.id for c in self.hard),
            *(c.id for c in self.soft),
            *(a.id for a in self.attributes),
            *(r.id for r in self.rules),
            *(i.id for i in self.instructions),
        ]
        if len(set(ids)) != len(ids):
            raise ValueError("every entry of the block has its own id")
        hard = {c.id for c in self.hard}
        if any(i.hard is not None and i.hard not in hard for i in self.instructions):
            raise ValueError("an instruction names an entry of `hard`")
        known = set(ids)
        never = {c.id for c in self.hard if c.relax == "never"}
        for entry in self.relaxation_order:
            if entry != "soft" and (entry not in known or entry in never):
                raise ValueError(f"`relaxation_order` names `{entry}`, not an entry that may give way")
        for conflict in self.conflicts:
            if not set(conflict.ids) <= known or conflict.kept not in conflict.ids:
                raise ValueError("a conflict names entries of the block and keeps one of them")
        return self


class BudgetUse(Model):
    """What this agent's recorded turns added up to, in one conversation or one case."""

    turns: int
    model_calls: int = Field(description="Each one reads the prompt's prefix again.")
    tool_calls: int
    tokens_in: int
    tokens_cached: int
    tokens_out: int
    cost_usd: float


class BudgetPack(Model):
    """What the pack of this read costs, in estimated tokens, whole and per section."""

    total: int
    sections: dict[str, int]


class BudgetCut(Model):
    """What the measurement of context use says this agent leaves unused, by unit of items: delivered in
    enough measured conversations and never used."""

    units: list[str]
    applied: bool = Field(description="The pack already leaves them out.")
    window_days: int
    min_deliveries: int


class BudgetBlock(Model):
    """The context budget of one read: what the pack costs, what this agent already spent in the conversation
    and the case, and what can go without loss. It is shown, never enforced: the agent's loop decides."""

    pack: BudgetPack | None = Field(default=None, description="Absent when the read served no pack.")
    conversation: BudgetUse | None = Field(
        default=None, description="This agent's turns in the conversation; absent without one."
    )
    case: BudgetUse | None = Field(default=None, description="This agent's turns in the case (`task_id`).")
    other_agents_turns: int = Field(
        default=0, description="Turns other agents recorded in the same conversation."
    )
    cut: BudgetCut | None = Field(default=None, description="Absent until the measurement found a unit.")
    counted: bool = Field(
        description="False when the counters could not be read: the numbers above are then missing, not zero."
    )


class Inference(Model):
    """One thing the signals infer about the subject, with what it rests on: the subject can see it, correct
    it and delete it. What the subject said is not an inference, and is not listed."""

    key: IdStr = Field(description="A keyed hash that names the inference; it never carries its value.")
    kind: Literal["affinity", "soft_constraint", "attribute", "interest", "instruction"] = Field(
        description="`soft_constraint` when the constraints block carries it as a soft entry, `attribute` "
        "for the inferred size, `affinity` for a value with evidence and no entry yet, `interest` for an "
        "object, `instruction` for what the subject said must or must not be done about an object (`ref`, "
        "the words in `value`): said, not inferred, and listed so a correction can withdraw it."
    )
    for_: For = Field(default="self", alias="for")
    attr: AttrPath | None = Field(default=None, description="The field whose value is inferred.")
    value: Scalar | None = None
    polarity: Literal["prefer", "avoid"] | None = None
    ref: Ref | None = Field(default=None, description="The object of an interest.")
    origin: ShortStr = Field(
        description="`interactions`, `implicit_negative` (shown often and never engaged with), or why an "
        "interest was kept: `engaged`, `feedback`, `watch`."
    )
    evidence: dict[str, Any] = Field(
        default_factory=dict,
        description="What it rests on, as counts: effective exposure, weighted signal, events, sessions and "
        "lift for a value; times for an interest.",
    )
    confidence: float | None = None
    used_for: list[ShortStr] = Field(default_factory=list, description="The purposes it is used for.")
    at: datetime = Field(description="When its latest evidence happened.")


class InferencePage(Model):
    items: list[Inference]
    next_cursor: str | None = None


class InferenceCorrection(Model):
    """The value the subject says instead: a stated preference, or a stated attribute for an inferred size
    (`source: correction`). The inference it corrects is deleted. An interest is deleted, not corrected."""

    value: Scalar
    reason: Reason | None = None


class ReviewRequestCreate(Model):
    """A request to review an automated decision, sent to the controller's data protection officer."""

    decision_receipt_id: UUID | None = Field(
        default=None, description="The receipt of the decision; absent, the subject's recent decisions."
    )
    reason: Reason


class ReviewRequest(Model):
    request_id: IdStr
    profile_id: IdStr
    status: Literal["open", "resolved"]
    created_at: datetime
    decision_receipt_id: UUID | None = None
    reason: str | None = None
    explanation: dict[str, Any] = Field(
        default_factory=dict,
        description="Built from receipts: their ids, kinds, surfaces, rules, versions and inputs by "
        "reference, never personal content.",
    )
    outcome: Literal["upheld", "reversed", "corrected"] | None = None
    resolution: str | None = Field(default=None, description="The reviewer's note.")
    resolved_at: datetime | None = None


class ReviewRequestPage(Model):
    items: list[ReviewRequest]
    next_cursor: str | None = None


class ReviewResolution(Model):
    outcome: Literal["upheld", "reversed", "corrected"]
    note: Reason | None = None


class LegalHoldCreate(Model):
    """Exactly one of `handle`, `object` or `conversation_id`: what is kept from purging while the hold is
    active."""

    handle: Handle | None = None
    object: ObjectRef | None = None
    conversation_id: IdStr | None = None
    reason: Reason

    @model_validator(mode="after")
    def _one_target(self) -> Self:
        if sum(x is not None for x in (self.handle, self.object, self.conversation_id)) != 1:
            raise ValueError("a hold names exactly one of `handle`, `object` or `conversation_id`")
        return self


class LegalHold(Model):
    hold_id: IdStr
    scope: Literal["handle", "object", "conversation"]
    status: Literal["active", "released"]
    created_at: datetime
    released_at: datetime | None = None


class LegalHoldRelease(Model):
    reason: Reason


Method = Literal["line", "order", "identity", "assisted_handoff"]
Band = Literal["deterministic", "probable"]
Finality = Literal["provisional", "final", "expired_without_outcome"]


class OutcomeLink(Model):
    """One outcome attributed to what an agent did, by one method. `line` and `order` are deterministic: the
    exposure token the order line or the order carried, or the agent's own action on the object.
    `identity` and `assisted_handoff` are probable: the same item engaged with or shown within a window, or a
    handoff that assisted the sale. A link carries its line's value whole or none at all: a value is never
    weighted."""

    link_id: IdStr
    outcome: ShortStr = Field(description="The outcome definition of the `measurement` document.")
    object: Ref
    line: ShortStr | None = None
    state: ShortStr = Field(description="The state the object or its line is in.")
    method: Method
    band: Band
    finality: Finality = Field(
        description="`provisional` until the exchange window ends or the object reaches a final state; "
        "`expired_without_outcome` when its deadline passed first."
    )
    value_minor: int | None = Field(
        default=None,
        description="In the currency's minor units, as the signed revenue definition counts it; absent: "
        "none.",
    )
    currency: ShortStr | None = None
    purchase: Ref | None = Field(
        default=None,
        description="The purchase it counts in, when the revenue definition counts a split purchase's "
        "parts as one order.",
    )
    exposure_id: IdStr | None = None
    position: int | None = None
    turn_id: IdStr | None = None
    action_id: IdStr | None = None
    handoff_id: IdStr | None = None
    agent: ShortStr | None = None
    confidence: float | None = Field(
        default=None,
        description="Of an `identity` link: how often that method names the exposure a line's own token "
        "names in this space, where both exist. Beside the value, never multiplied by it.",
    )
    valid_at: datetime = Field(description="When the outcome first counted.")
    known_at: datetime
    final_at: datetime | None = Field(default=None, description="When a provisional link becomes final.")


class OutcomePage(Model):
    items: list[OutcomeLink]
    next_cursor: str | None = None


class AttributionRow(Model):
    day: date
    outcome: ShortStr
    agent: str
    method: Method
    band: Band
    finality: Finality
    currency: str
    outcomes: int
    lines: int
    value_minor: int


class AttributionTotal(Model):
    """One method, band and finality over the period: deterministic and probable are never added together."""

    outcome: ShortStr
    method: Method
    band: Band
    finality: Finality
    currency: str
    outcomes: int
    lines: int
    value_minor: int


class IdentityCalibration(Model):
    """How often the identity method names the exposure the line's own token names, on the lines where both
    exist: the confidence of that method in this space."""

    agreeing: int
    compared: int
    rate: float | None = None


class AttributionReport(Model):
    """Value by day, agent, method and band, deterministic and probable apart; a value is never multiplied by
    a confidence, and no total adds a probable value to a deterministic one."""

    since: date
    until: date
    rows: list[AttributionRow]
    totals: list[AttributionTotal]
    identity: IdentityCalibration


MAX_BI_BYTES = 50 * 1024 * 1024


class BiUploadRequest(Model):
    """Reserves the upload of the company's BI file: its size and SHA-256, which the storage checks."""

    size_bytes: int = Field(ge=1, le=MAX_BI_BYTES)
    sha256: Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]


class BiUpload(Model):
    upload_id: UUID
    upload_url: str
    upload_headers: dict[str, str] = Field(
        default_factory=dict, description="Send exactly these headers; the storage refuses other bytes."
    )
    expires_at: datetime


class BiFile(Model):
    """The company's BI export for the period, uploaded first (`POST /v1/measure/reconcile/uploads`), as CSV
    with a header: `order` (the id the company knows the object by), `line` (optional), `value` (in major
    units, as the signed definition nets it) and `currency`. It is read once and deleted."""

    format: Literal["csv"] = "csv"
    upload_id: UUID


class ReconcileRequest(Model):
    since: date
    until: date
    file: BiFile


class ReconciledLine(Model):
    order: IdStr
    line: str
    ours_minor: int
    bi_minor: int


class ReconcileResult(Model):
    """Our value of each line against the BI's, on the lines both know; lines only one side knows are
    counted apart and never enter the deviation."""

    report_id: IdStr
    deviation: float | None = Field(description="|ours - BI| / BI on the lines both know.")
    tolerance: float
    within_tolerance: bool
    matched: int
    only_ours: int
    only_bi: int
    ours_minor: int
    bi_minor: int
    currency: str | None = None
    worst: list[ReconciledLine] = Field(default_factory=list, description="The lines that differ most.")


class InterleavingRow(Model):
    day: date
    experiment: ShortStr
    tool: ShortStr
    impressions: int
    wins_a: int = Field(description="Impressions whose engagement went to the items ranking `a` contributed.")
    wins_b: int
    ties: int = Field(description="Impressions engaged with equally, or not at all.")


class InterleavingTotal(Model):
    experiment: ShortStr
    tool: ShortStr
    impressions: int
    wins_a: int
    wins_b: int
    ties: int
    p_value: float | None = Field(
        description="The two-sided sign test on the decided impressions; absent before any was decided."
    )


class InterleavingReport(Model):
    """Interleaved lists of a tool's two rankings (team draft, seeded by the turn): which ranking the
    person's engagement credited, impression by impression, by day and over the period."""

    since: date
    until: date
    rows: list[InterleavingRow]
    totals: list[InterleavingTotal]


CounterfactualElement = Literal["constraints", "hard", "size", "exclude"]
"""What the variant goes without: the whole constraints block, its hard constraints, its attributes (the
size) or its exclusions, as they render into the tool's arguments and post filter."""
Tool = Annotated[str, StringConstraints(pattern=r"^[A-Za-z][A-Za-z0-9_.:-]{0,63}$")]
CaseStatus = Literal["completed", "no_dry_run", "tool_error", "infrastructure_error"]
LimitCode = Literal[
    "not_quality", "model_reaction_not_measured", "trivial_for_hard", "few_cases", "noisy_tool",
    "cases_skipped", "dry_run",
]  # fmt: skip


class CounterfactualEngaged(Model):
    """An item the person engaged with in the recorded turn, by its 1-based position in each live list;
    absent where the list does not hold it."""

    base: int | None = Field(default=None, ge=1, le=10_000)
    variant: int | None = Field(default=None, ge=1, le=10_000)


class CounterfactualCase(Model):
    """One recorded tool call run live three times at the same moment: twice with the element (the base,
    whose two lists measure the tool's own noise) and once without it (the variant). Positions and
    overlaps only: never an item, an argument or a result."""

    turn_id: Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")]
    call_id: ShortStr
    status: CaseStatus = Field(
        description="`no_dry_run` for a tool that writes state and has no dry run: it is never called. "
        "`tool_error` when a call failed, `infrastructure_error` when the runner could not run the case."
    )
    dry_run: bool = Field(default=False, description="The calls ran as the tool's dry run.")
    k: int | None = Field(
        default=None,
        ge=1,
        le=100,
        description="The positions compared: the recorded list's `visible_k`, else the run's `k`.",
    )
    overlap: float | None = Field(
        default=None, ge=0, le=1, description="overlap@k of the base's first list and the variant's."
    )
    noise: float | None = Field(
        default=None, ge=0, le=1, description="overlap@k of the base's two lists: the tool's own noise."
    )
    base_count: int | None = Field(default=None, ge=0, description="Items in the base's first list.")
    variant_count: int | None = Field(default=None, ge=0, description="Items in the variant's list.")
    engaged: list[CounterfactualEngaged] = Field(default_factory=list, max_length=50)

    @model_validator(mode="after")
    def _measured_when_completed(self) -> Self:
        measured = (self.k, self.overlap, self.noise, self.base_count, self.variant_count)
        if self.status == "completed":
            if any(x is None for x in measured):
                raise ValueError("a completed case carries `k`, `overlap`, `noise` and both counts")
        elif any(x is not None for x in measured) or self.engaged:
            raise ValueError("only a completed case carries measures")
        return self


class CounterfactualRunCreate(Model):
    """What a runner measured in the company's CI for one tool and one element (the tool counterfactual
    spec)."""

    tool: Tool
    element: CounterfactualElement
    label: ShortStr | None = Field(
        default=None, description="A name for the run, such as the commit or the build it ran on."
    )
    k: int = Field(
        default=10,
        ge=1,
        le=100,
        description="The positions compared when a recorded list has no `visible_k`.",
    )
    cases: list[CounterfactualCase] = Field(min_length=1, max_length=5000)


class CounterfactualEngagement(Model):
    """Where the items the person engaged with went without the element, over the completed cases."""

    items: int
    shown: int = Field(description="Within the first `k` of the base's list.")
    kept: int = Field(description="Within the first `k` of both lists.")
    lost: int = Field(description="Within the base's first `k` and not the variant's.")
    gained: int = Field(description="Within the variant's first `k` and not the base's.")
    mean_shift: float | None = Field(
        default=None,
        description="The variant's position minus the base's, averaged over the items both lists hold; "
        "positive when they fell.",
    )


class CounterfactualRun(Model):
    """Whether the element changes what the tool returns, beyond the tool's own noise. It never says whether
    the result got better, nor how the model reacts: `limits` says so, and what else holds the answer
    back."""

    run_id: IdStr
    tool: str
    element: CounterfactualElement
    label: str | None = None
    created_at: datetime
    cases: int
    completed: int
    skipped: dict[CaseStatus, int] = Field(default_factory=dict, description="The cases not completed.")
    overlap: float | None = Field(description="Mean overlap@k of the base and the variant.")
    noise_floor: float | None = Field(description="Mean overlap@k of the base with itself.")
    effect: float | None = Field(
        description="`noise_floor - overlap`: how much of the first positions the element moves beyond noise."
    )
    below_noise: int = Field(description="Completed cases whose overlap is below their own noise.")
    above_noise: int
    ties: int
    p_value: float | None = Field(
        description="The two-sided sign test of `below_noise` against `above_noise`: how likely a split "
        "this uneven is when the element changes nothing."
    )
    engaged: CounterfactualEngagement
    limits: list[LimitCode]


class CounterfactualRunPage(Model):
    items: list[CounterfactualRun]
    next_cursor: str | None = None


Metric = Literal["conversion", "value"]
"""`conversion`: the share of people with an outcome in a success state; `value`: the money of their lines,
each line once, in minor units."""


class ArmOutcomes(Model):
    """The people one arm holds and what they did after they were assigned, from the outcome links."""

    arm: str
    people: int
    converted: int = Field(description="People with an outcome in a success state.")
    outcomes: int
    value_minor: int = Field(description="Their lines' money, each line once whatever the method.")


class ArmComparison(Model):
    """One arm against `control` on one metric, adjusted by CUPED when the history varies, with the mixture
    sequential test: its p-value and interval hold at any look, so the report may be read every day."""

    arm: str
    metric: Metric
    control_mean: float = Field(description="Per person, before the adjustment.")
    arm_mean: float
    difference: float = Field(description="The arm minus the control, per person, adjusted.")
    relative: float | None = Field(description="`difference` over the control's mean.")
    ci_low: float = Field(description="The always-valid interval of `difference` at the test's `alpha`.")
    ci_high: float
    variance_reduction: float | None = Field(
        description="The share of the variance CUPED removed; absent when it did not apply."
    )
    p_value: float = Field(description="The running minimum over the daily looks and this one.")
    decided: bool = Field(description="`p_value` at or below `alpha`: the difference is real.")
    looks: int = Field(description="The daily looks kept before this one.")


class ExperimentResult(Model):
    experiment: ShortStr
    kind: Literal["element", "agent"]
    element: str | None = None
    agent: str | None = None
    starts_at: datetime
    ends_at: datetime | None = None
    alpha: float
    cuped_days: int = Field(description="The days before each person's assignment taken as the covariate.")
    arms: list[ArmOutcomes]
    comparisons: list[ArmComparison] = Field(
        description="Empty until each arm holds enough people for the normal approximation."
    )


class ExperimentReport(Model):
    """Every experiment on an element or an agent, arm by arm, from what people did after they were assigned:
    incremental, as only a holdout says."""

    as_of: datetime
    experiments: list[ExperimentResult]


class PowerRequest(Model):
    """How long the space's traffic takes to detect a difference of `effect` (relative: 0.1 is 10%) on
    `metric`. The traffic and the baseline come from the space's history, the named experiment's or the
    busiest one's of the last 28 days, unless given."""

    metric: Metric = "conversion"
    effect: float = Field(gt=0, le=10, description="The relative difference to detect: 0.1 is 10%.")
    alpha: float = Field(default=0.05, gt=0, le=0.2)
    power: float = Field(default=0.8, ge=0.5, lt=1)
    control_fraction: float = Field(default=0.5, gt=0, lt=1)
    experiment: ShortStr | None = Field(default=None, description="The experiment whose history to use.")
    daily_people: int | None = Field(default=None, ge=1, description="New people a day entering the test.")
    baseline_mean: float | None = Field(
        default=None, ge=0, description="Per person: a rate for `conversion`, minor units for `value`."
    )
    baseline_sd: float | None = Field(
        default=None, gt=0, description="Per person, for `value`; a rate's follows from its mean."
    )


class PowerResult(Model):
    metric: Metric
    effect: float
    baseline_mean: float
    baseline_sd: float
    daily_people: float
    people: int | None = Field(description="Both arms together, at a fixed horizon.")
    days: int | None = Field(description="At a fixed horizon, looked at once, at the end.")
    people_sequential: int | None = Field(
        description="Both arms together, for the daily-looked sequential test to have decided with `power`."
    )
    days_sequential: int | None
    traffic_from: Literal["given", "history"]
    baseline_from: Literal["given", "history"]
    experiment: str | None = Field(default=None, description="The experiment whose history was used.")


class AskedAttribute(Model):
    attr: AttrPath
    op: Op
    values: list[Scalar]


class UnmetDemand(Model):
    """One combination people asked a tool for, in one week, that came back with nothing or with fewer than
    three results. Never a person: a combination fewer than the space's minimum of people asked is left
    out."""

    week: date = Field(description="The Monday the week starts on.")
    tool: str
    asked: list[AskedAttribute] = Field(
        description="What the tool's arguments asked, on the type registry's fields, values normalized; "
        "empty when the arguments named none."
    )
    calls: int
    empty: int = Field(description="Calls that returned nothing; the rest returned one or two results.")
    people: int = Field(description="Distinct people who asked it that week.")


class UnmetDemandPage(Model):
    since: date
    until: date
    min_people: int
    items: list[UnmetDemand]
    next_cursor: str | None = None
