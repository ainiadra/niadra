"""Typed state: objects pushed by a system of record, state reads with a purpose, the agent's working state
and the SDK profile (7.7, 24.3).

A read answers each object in the form of the open spec `object-state.v1` (`ObjectRead`), generated from these
models, and so does `GET /v1/objects/...`.
"""

from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import Field, StringConstraints, model_validator

from niadra.contracts.bindings import ToolBinding
from niadra.contracts.claims import ClaimContractSummary
from niadra.contracts.common import Handle, IdStr, Model, ObjectRef, ShortStr
from niadra.contracts.turns import TurnRecordingSummary
from niadra.domain.vocabulary import Feature

Purpose = Literal["display", "claim", "decide"]
Logic = Literal["yes", "no", "unobserved", "known_defect"]
Freshness = Literal["fresh", "stale", "expired"]
TypeName = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]{0,39}$")]


class StateRef(ObjectRef):
    """An object of the type registry: a subject's or a shared one, whose key may name a variant."""

    variant: ShortStr | None = None


class Provenance(Model):
    """Where pushed values come from: only a `live` source sustains a claim, and the age of every value counts
    from `source_observed_at`."""

    source: Literal["live", "snapshot", "cache"]
    source_observed_at: datetime
    scope: Literal["global", "customer", "context"] = "global"


class ObjectPush(Model):
    ref: StateRef
    version: int = Field(
        ge=0,
        le=2**53 - 1,
        description="The source's version of the object. A field moves only when this is greater than the "
        "version that last wrote it; a source never reuses a version for other content.",
    )
    fields: dict[str, Any] = Field(min_length=1, max_length=200)
    provenance: Provenance
    inputs: dict[ShortStr, ShortStr] | None = Field(
        default=None,
        max_length=20,
        description=(
            "For a derived object (a customer's quote, a shared best offer): the objects its inputs are "
            "fields of, by the name its type gives them, as `type:namespace:id` "
            '(`{"lead": "lead:crm:L-9"}`). When a field of one of them changes, the object expires, naming '
            "the input. A shared object's inputs are shared objects: a customer's object is never one."
        ),
    )
    request_id: IdStr | None = Field(
        default=None,
        description="The refresh request this item answers, from the resolver worker's lease: the request is "
        "settled, and a `watch_revalidation` is decided on these values even when they are not newer than "
        "the ones held.",
    )


class ObjectPushRequest(Model):
    objects: list[ObjectPush] = Field(min_length=1, max_length=1000)


class ObjectPushResponse(Model):
    """How many items of the request each decision took, against the hot layer, with no database statement.

    A shared object's item is `applied` when at least one of its fields moved forward: reads serve it at once,
    and the consolidator writes it within a second with the same per-field rule, so the stored object never
    disagrees with the answer. `stale_version`: every field was already written by this version or a greater
    one, as a retried request finds. `out_of_set`: the object is not in the working set, and nothing is kept.
    A subject's object is `recorded`: accepted as a system event about it, in the request's one statement,
    and its fields move by the same rule when the event is applied."""

    applied: int
    stale_version: int
    out_of_set: int
    recorded: int = 0


class ObjectSnapshotResponse(Model):
    """A snapshot taken in: its lines are applied as pushes of provenance `snapshot`, which never sustain a
    claim, by the same version rule."""

    import_id: IdStr
    objects: int = Field(description="The lines taken in.")


class DriftChanges(Model):
    """What changed between the declaration and the type the schema proposes now, as counts: never a new name,
    a value or a definition (object-type spec, 8.8.2)."""

    fields_added: int = Field(ge=0)
    fields_removed: int = Field(ge=0)
    fields_retyped: int = Field(ge=0)
    states_added: int = Field(ge=0)
    states_removed: int = Field(ge=0)
    relations_added: int = Field(ge=0)
    relations_removed: int = Field(ge=0)
    key_changed: bool
    fields: list[Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]{0,63}$")]] = Field(
        default_factory=list,
        max_length=50,
        description="The declaration's fields removed or retyped, and its state field when the states "
        "differ.",
    )


class TypeFingerprintRequest(Model):
    """A deriving tool's check of a declared type: the fingerprint of the schema it read now."""

    type: TypeName
    fingerprint: Annotated[str, StringConstraints(pattern=r"^sha256:[0-9a-f]{64}$")]
    changes: DriftChanges | None = None


class TypeFingerprintResponse(Model):
    drift: bool = Field(description="Whether the fingerprint differs from the declaration's.")
    issue_id: IdStr | None = Field(
        default=None,
        description="The data issue of kind `drift` the check opened or counted in, when the type alerts.",
    )


class StateViewRequest(Model):
    subject: Handle
    purpose: Purpose = "display"


class StateReadRequest(Model):
    refs: list[StateRef] = Field(min_length=1, max_length=20)
    fields: list[ShortStr] | None = None
    readings: list[ShortStr] | None = None
    purpose: Purpose = "display"
    subject: Handle | None = None


class PreviousValue(Model):
    """A field's value before its latest change, kept for the fields that declare `track_changes`."""

    v: Any = None
    logic: Logic


class ContentMarker(Model):
    """A content field whose text Niadra does not hold (`pointer`, `hash_only`), or holds under scan."""

    mode: Literal["full", "pointer", "hash_only"]
    sha256: Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")] | None = None
    pointer: IdStr | None = Field(
        default=None, description="Where the SDK reads the text inside the company's boundary."
    )
    scan: Literal["pending", "clean", "flagged"]


class FieldState(Model):
    """One field as a read serves it: the value with its logical value, stamps, source and observer, and the
    freshness computed now against the type's current rule."""

    v: Any = None
    logic: Logic
    status: Freshness
    age_s: int | None = Field(default=None, description="Seconds since `observed_at`.")
    observed_at: datetime | None = Field(default=None, description="When the source observed the value.")
    src: ShortStr | None = None
    observer: ShortStr | None = None
    valid_at: datetime | None = None
    known_at: datetime | None = Field(default=None, description="When Niadra first knew it; never moves.")
    role: ShortStr | None = None
    claim_safe: bool
    completeness: ShortStr | None = None
    masked: bool = Field(default=False, description="The reader's access masks the value: `v` is absent.")
    was: PreviousValue | None = None
    content: ContentMarker | None = None


class ValueState(Model):
    """A value the company computed, kept as a versioned fact with the rule that produced it."""

    v: Any = None
    logic: Logic
    absent_as: ShortStr | None = Field(default=None, description="The name of the absence, when absent.")
    version: int = Field(ge=1)
    rule: ShortStr
    computed_at: datetime
    status: Freshness
    supersedes_version: int | None = None
    declared_gaps: list[str] = Field(default_factory=list)
    gap_effect: Literal["errs_early", "errs_late", "unknown"] = "unknown"
    claim_safe: bool


class DerivedState(Model):
    """A field the type derives from the related objects of one relation (a look's pieces), computed at this
    read and never stored. A related object whose data is not known leaves it `unobserved`, unless the answer
    is already decided."""

    v: Any = None
    logic: Literal["yes", "no", "unobserved"]
    over: int = Field(ge=0, description="The related objects it was computed over.")
    unknown: int = Field(
        ge=0,
        description="How many of them were not known: out of the working set, not an exact match "
        "(`status_field`), or with the data the condition reads not observed.",
    )
    pieces_out: list[str] = Field(
        default_factory=list,
        max_length=10,
        description="When it is not `yes`, the related objects, as `type:namespace:id`, that kept it from "
        "`yes`: for `all` and `any` the ones the condition does not hold for or that are not known, for "
        "`count`, `min` and `max` the ones not known. In the relation's order, the first 10.",
    )
    partial: bool = Field(
        default=False,
        description="The relation names more related objects than one read takes (50): the first ones were"
        " read and the rest counted as not known, so `over` and `unknown` are at least what they say.",
    )


class TimerState(Model):
    name: ShortStr
    due_at: datetime | None = Field(default=None, description="Computed now from the rule and the values.")
    state: Literal["armed", "fired", "cancelled"]
    fired_at: datetime | None = None
    supersedes_firing_id: IdStr | None = Field(
        default=None,
        description="The firing this one replaces: a timer that already fired fires again once the value "
        "that armed it is revised.",
    )


class OutcomeState(Model):
    state: ShortStr = Field(description="A canonical state of the type, or `expired_without_outcome`.")
    final: bool
    at: datetime | None = None


class ReadingResult(Model):
    """A named reading's value, as niadra-expr computes it: the logical value, and the kind and value when
    known (dates as `YYYY-MM-DD`, times in UTC, durations in milliseconds)."""

    logic: Logic
    type: Literal["bool", "number", "string", "duration", "date", "datetime", "list"] | None = None
    v: Any = None
    absent: ShortStr | None = None
    inputs: dict[str, str] | None = Field(
        default=None,
        description="For a reading that includes its inputs: the derived object's inputs, by the name its "
        "type gives them, as `type:namespace:id`.",
    )


class Refetch(Model):
    reason: ShortStr
    priority: Literal["high", "normal", "low"]
    admitted: bool


class Refusal(Model):
    reason: ShortStr
    action: ShortStr | None = None


class SaidAbout(Model):
    """One row of what people and agents said about an object, kept under the person who said it."""

    kind: Literal["observation", "item", "instruction"]
    id: str = Field(description="The fact, open item or instruction it is.")
    text: str
    said_at: datetime
    audience: Literal["shared", "internal"] = Field(
        description="`shared` reaches every participant of the object, a customer-facing agent included; "
        "`internal`, the internal readers only."
    )
    role: ShortStr | None = Field(
        default=None, description="The role of who said it on the object (`owner`, `participant`, `lawyer`)."
    )
    by: str | None = Field(default=None, description="The handle of who said it; only in the Console.")
    predicate: ShortStr | None = None
    item_kind: Literal["promise", "dispute", "request"] | None = None
    due_at: datetime | None = None
    overdue: bool = False
    strength: Literal["must", "must_not"] | None = None
    attr: ShortStr | None = None
    expires_at: datetime | None = None
    observes: ShortStr | None = Field(
        default=None,
        description="The field of the object's type an observation is about: shown beside the value the "
        "system of record holds, never in its place.",
    )
    recorded: Any = Field(default=None, description="The value the system of record holds for `observes`.")
    diverges: bool = Field(
        default=False, description="The value said differs from the one recorded: a data issue was opened."
    )
    claim_safe: Literal[False] = Field(
        default=False,
        description="Always false: what was said may be reported as said, never stated as the record.",
    )


class ObjectMemoryOut(Model):
    """What the owner and participants of an object said about it (memory bound to the object), newest
    first, at most 20 of each kind, as this reader may read it."""

    said: list[SaidAbout] = Field(default_factory=list, description="Observations about the object.")
    instructions: list[SaidAbout] = Field(
        default_factory=list, description="What must or must not be done about the object."
    )
    items: list[SaidAbout] = Field(
        default_factory=list, description="Promises, disputes and requests about the object still open."
    )


class ObjectRead(Model):
    """One object as a state read serves it: fields with four logical values and their stamps, computed
    values, lifecycle, timers and readings, and what may not be claimed with data this old. A read never
    refuses; a `decide` read of a type that asks for it carries `refusal` beside the values, for the company's
    tool."""

    ref: StateRef
    type_version: ShortStr | None = None
    as_of: datetime | None = None
    state: ShortStr | None = None
    latches: dict[str, datetime] = Field(default_factory=dict)
    outcome: OutcomeState | None = None
    derived_status: Literal["current", "expired_by_input"] | None = None
    expired_by: list[str] = Field(default_factory=list, description="The inputs whose change expired it.")
    axes: dict[str, str] = Field(
        default_factory=dict,
        description="Each time axis of the type whose value is known: a date as `YYYY-MM-DD`, an instant in "
        "UTC. The platform keeps `known_at` (when the producer first knew of the object) and the time its "
        "content last changed.",
    )
    fields: dict[str, FieldState] = Field(default_factory=dict)
    values: dict[str, ValueState] = Field(default_factory=dict)
    derived: dict[str, DerivedState] = Field(
        default_factory=dict, description="The type's derived fields, from the related objects."
    )
    timers: list[TimerState] = Field(default_factory=list)
    readings: dict[str, ReadingResult] = Field(default_factory=dict)
    prohibitions: list[str] = Field(default_factory=list)
    declared_gaps: list[str] = Field(default_factory=list)
    withheld: dict[str, Literal["access", "licence", "scan"]] = Field(
        default_factory=dict, description="Fields left out for this reader, and why."
    )
    blocked: dict[ShortStr, list[str]] = Field(
        default_factory=dict,
        description="What the fields whose value is not known block, as the type declares it "
        "(`unobserved_blocks`): each activity (`model_read`, `derive`, `claim`, or a task of the company "
        "such as `decide:close`) with the fields that block it. Nothing else is blocked.",
    )
    refetch: Refetch | None = None
    refusal: Refusal | None = None
    memory: ObjectMemoryOut | None = Field(
        default=None,
        description="What its owner and participants said about it, in a context read that serves a pack.",
    )


class StateReadResponse(Model):
    objects: list[ObjectRead]
    degraded: bool = False


class InterestState(Model):
    """A shared object the subject showed interest in, as it stands now."""

    ref: StateRef
    reason: ShortStr = Field(description="`presented`, `engaged`, `feedback` or `watch`.")
    at: datetime
    object: ObjectRead | None = Field(
        default=None,
        description="The object as a `display` read serves it; absent once it left the working set.",
    )


class ChangeSinceSeen(Model):
    """A field of an interest whose value is not the one the subject was shown."""

    ref: StateRef
    field: ShortStr
    seen: Any = None
    now: Any = None
    observed_at: datetime | None = Field(
        default=None, description="When the source observed the value now held."
    )


class StateView(Model):
    """The state of one subject (the `now` view): their objects of the declared types, each as a `display`
    read serves it; the shared objects they showed interest in, and what changed since they saw them; and
    the same as short lines for the turn block."""

    objects: list[ObjectRead] = Field(default_factory=list)
    interests: list[InterestState] = Field(default_factory=list)
    changes_since_seen: list[ChangeSinceSeen] = Field(default_factory=list)
    text: str | None = Field(
        default=None,
        description="The view as lines for the turn block, after `slots`: never part of the pack's `text`, "
        "so the cached prefix keeps its bytes. Absent when there is nothing to say.",
    )
    degraded: bool = False


class VerifyCheck(Model):
    ref: StateRef
    field: ShortStr
    value: Any = None


class StateVerifyRequest(Model):
    checks: list[VerifyCheck] = Field(min_length=1, max_length=50)
    subject: Handle | None = None


class Verdict(Model):
    ref: StateRef
    field: ShortStr
    claim_safe: bool
    matches: bool | None = None
    status: Freshness
    prohibitions: list[str] = Field(default_factory=list)
    declared_gaps: list[str] = Field(default_factory=list)


class StateVerifyResponse(Model):
    verdicts: list[Verdict]


class RefreshRequest(Model):
    request_id: IdStr
    ref: StateRef
    reason: ShortStr
    priority: Literal["high", "normal", "low"]
    budget_units: int
    lease_until: datetime


class RefreshRequestPage(Model):
    items: list[RefreshRequest]


class RefreshRelease(Model):
    """A leased request the resolver worker cannot answer: the object is not at the source, or fetching it
    failed. The request leaves at once and its paid call counts; a `watch_revalidation` falls back to the
    type's `unconfirmed_watch`."""

    outcome: Literal["not_found", "failed"]


class RefreshReleased(Model):
    request_id: IdStr
    reason: ShortStr


class AgentStateScope(Model):
    """What one working state belongs to. The id travels in the body, never in a URL: a conversation id may
    look like a phone number."""

    kind: Literal["conversation", "task", "object", "subject"]
    id: IdStr


class AgentStateReadRequest(Model):
    scope: AgentStateScope
    agent: ShortStr


class AgentState(Model):
    body: dict[str, Any] = Field(default_factory=dict)
    version: int
    updated_at: datetime | None = None


class AgentStateWrite(Model):
    """A write of an agent's working state, by its code and never by a model: `cas` replaces the whole state
    at `if_version` (0 when it must not exist yet); `merge_by_key` replaces each top-level field it names and
    removes a field written as exactly `{"$delete": true}`."""

    scope: AgentStateScope
    agent: ShortStr
    mode: Literal["cas", "merge_by_key"]
    if_version: int | None = Field(default=None, ge=0)
    body: dict[str, Any]
    subject: Handle | None = Field(
        default=None,
        description="The customer the state is about, when its scope does not name them: erasing the "
        "customer and their data package find the state by it.",
    )

    @model_validator(mode="after")
    def _mode(self) -> "AgentStateWrite":
        removals = {name: v for name, v in self.body.items() if isinstance(v, dict) and "$delete" in v}
        if self.mode == "cas":
            if self.if_version is None:
                raise ValueError("a compare-and-swap names the version it read: if_version")
            if removals:
                raise ValueError("a compare-and-swap writes the whole state: leave a field out to remove it")
        for name, removal in removals.items():
            if removal.keys() != {"$delete"} or removal["$delete"] is not True:
                raise ValueError(f'{name}: a removal is exactly {{"$delete": true}}')
        return self


class AgentStateMeta(Model):
    """One working state of a customer, as the Console shows it: never its content."""

    agent: ShortStr
    scope_kind: Literal["conversation", "task", "object", "subject"]
    source_id: IdStr
    bytes: int = Field(description="The size of the state's body.")
    version: int
    updated_at: datetime
    valid_until: datetime | None = Field(
        default=None, description="When the state goes, by its type's retention; null while it names none."
    )
    schema_version: ShortStr | None = None


class AgentStateMetaPage(Model):
    items: list[AgentStateMeta]


class AgentStateWriteResult(Model):
    stored: bool
    version: int
    reason: Literal["not_declared_field"] | None = None


class FieldCoverage(Model):
    field: ShortStr
    coverage: float = Field(
        ge=0, le=1, description="The share of the type's objects some source observed the field of."
    )
    age_p50_s: int | None = Field(
        default=None, description="The median age, in seconds, of the field's newest observation."
    )


class TypeCoverage(Model):
    type: TypeName
    objects: int = Field(description="The objects measured: the ones of the type that changed last.")
    fields: list[FieldCoverage]


class ObjectCoverage(Model):
    """How much of each declared field the objects of each type carry, and how old it is, from the stamps
    alone: never a value."""

    types: list[TypeCoverage]


class ContentRelease(Model):
    reason: Annotated[str, StringConstraints(min_length=1, max_length=500)]


class ContentReleaseResult(Model):
    released: bool


class SdkProfile(Model):
    """What the SDK keeps in its local cache: the features on, the source's tool bindings, the summarized
    type registry, the claim contract and turn recording (7.7)."""

    features: list[Feature]
    tool_bindings: list[ToolBinding] = Field(
        default_factory=list, description="The bindings of the tools this source's agents call."
    )
    types: list[dict[str, Any]] = Field(default_factory=list)
    claim_contract: ClaimContractSummary | None = None
    recording: TurnRecordingSummary | None = Field(
        default=None, description="How this source records turns, once the space records them."
    )
    valid_for_s: int
