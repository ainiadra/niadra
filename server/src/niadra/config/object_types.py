"""The type registry (24.2): the object types a space declares, or derives from its own database schema.

Signed configuration like the policy and the mappings: the `object-types` document of a space, validated here
before a diff is accepted and again by the cell. These models are the source of the open spec
`schemas/object-type.v0.json`, so the published format and the running validation never drift apart; what JSON
Schema cannot say (an expression that does not parse, a transition to an undeclared state) is checked in
`ObjectType._consistent` and named precisely, with the path of the offending entry.

Setting a field's `sensitivity`, `access`, `licence` or `purposes` is a security decision as well (24.2); the
diff approval of the control plane asks for that role.
"""

import itertools
import re
from collections.abc import Iterable, Mapping, Sequence
from typing import Annotated, Any, Literal, Self

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    PrivateAttr,
    StringConstraints,
    ValidationError,
    model_validator,
)

from niadra.domain.control.records import Role
from niadra.domain.objects.words import Words
from niadra.domain.policy.fields import PUBLIC, FieldReader, field_effect
from niadra.domain.policy.rules import Effect
from niadra.domain.policy.sensitive import PRODUCT_SENSITIVE_CATEGORIES
from niadra.domain.state.expr import (
    RESERVED,
    Expect,
    ExprError,
    Kind,
    Scope,
    anchor,
    compile_expression,
    duration_ms,
)
from niadra.domain.state.freshness import DEFAULT_MAX_AGE_MS, FreshnessClass

_IDENT = r"[a-z][a-z0-9_]{0,63}"
TypeName = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]{0,39}$")]
Ident = Annotated[str, StringConstraints(pattern=rf"^{_IDENT}$")]
"""The name of a field, value, time axis, state, source, relation, timer or reason."""
Label = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_.:-]{0,63}$")]
"""A name the company chooses for a task, purpose, role, prohibition, gap, reader or unit."""
FieldOrAll = Annotated[str, StringConstraints(pattern=rf"^(\*|{_IDENT})$")]
RuleRef = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_.-]{0,63}@[A-Za-z0-9_.-]{1,32}$")]
"""A rule and its version, `prazo_forense@v9`: the rule is the company's, Niadra keeps its name."""
InputRef = Annotated[str, StringConstraints(pattern=rf"^{_IDENT}(\.{_IDENT})*(@[A-Za-z0-9_.-]{{1,32}})?$")]
Duration = Annotated[str, StringConstraints(pattern=r"^[0-9]{1,6}(ms|s|min|h|d)$")]
Path = Annotated[str, StringConstraints(min_length=1, max_length=256)]
"""A JMESPath into the source's payload."""
Expression = Annotated[str, StringConstraints(min_length=1, max_length=1024)]
"""A niadra-expr expression."""
Who = Annotated[str, StringConstraints(pattern=rf"^(machine|human|source:{_IDENT})$")]
ReadingName = Annotated[str, StringConstraints(pattern=rf"^{_IDENT}(\.{_IDENT})?$")]
Version = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_.-]{1,32}$")]
FieldType = Literal[
    "string",
    "text",
    "number",
    "money",
    "percent",
    "date",
    "datetime",
    "duration",
    "bool",
    "enum",
    "list",
    "ref",
]


def _sensitivity(value: str) -> str:
    if value != "none" and value not in PRODUCT_SENSITIVE_CATEGORIES:
        raise ValueError(f"{value!r} is not a sensitive category of the product")
    return value


Sensitivity = Annotated[
    str,
    AfterValidator(_sensitivity),
    Field(json_schema_extra={"enum": ["none", *sorted(PRODUCT_SENSITIVE_CATEGORIES)]}),
]

KIND_OF_TYPE: dict[str, Kind] = {
    "string": Kind.STRING,
    "text": Kind.STRING,
    "enum": Kind.STRING,
    "ref": Kind.STRING,
    "number": Kind.NUMBER,
    "money": Kind.NUMBER,
    "percent": Kind.NUMBER,
    "date": Kind.DATE,
    "datetime": Kind.DATETIME,
    "duration": Kind.DURATION,
    "bool": Kind.BOOL,
    "list": Kind.LIST,
}
"""How a field of each type reads in an expression."""


class Doc(BaseModel):
    """As `config.models.Doc`, which imports this module for its `object_types` section."""

    model_config = ConfigDict(extra="forbid", frozen=True, defer_build=True)


class AliasedDoc(Doc):
    """For the keys that are Python keywords (`class`, `from`): read and written by their published name."""

    model_config = ConfigDict(serialize_by_alias=True, validate_by_name=True, validate_by_alias=True)


class MirrorOf(Doc):
    """The company's system this type mirrors: Niadra mirrors and never is the barrier."""

    system: Label
    derived_by: Literal["declaration", "introspection", "observation"] = "declaration"
    fingerprint: Annotated[str, StringConstraints(min_length=1, max_length=128)] | None = Field(
        default=None, description="The fingerprint of the catalog an introspection read."
    )
    drift: Literal["alert", "ignore"] = "alert"


Pattern = Annotated[str, StringConstraints(min_length=1, max_length=256)]
"""A regular expression, Python syntax."""


class Key(Doc):
    natural: list[Path] = Field(min_length=1, max_length=8, description="Paths of the natural key.")
    fingerprint: Expression | None = Field(
        default=None, description="An expression whose value identifies the same observation across sources."
    )
    variant: Path | None = None
    match: dict[Ident, Pattern] = Field(
        default_factory=dict,
        max_length=8,
        description="A pattern per identifier a conversation writes as it is (a case number, an order "
        "number), a path of the natural key or a declared field: what the text says in that shape names the "
        "customer's own object holding that value, never an object the customer is not tied to.",
    )

    @model_validator(mode="after")
    def _matchable(self) -> Self:
        for name, pattern in self.match.items():
            try:
                compiled = re.compile(pattern)
            except re.error as exc:
                raise ValueError(f"key.match.{name}: the pattern does not compile ({exc.msg})") from None
            if compiled.match(""):
                raise ValueError(f"key.match.{name}: the pattern matches an empty text")
        return self


class Revisions(Doc):
    allowed: bool = False
    novelty: Literal["keep_first_seen"] = Field(
        default="keep_first_seen",
        description="A revision of the same observation keeps the time Niadra first knew of it.",
    )


class ContentPolicy(Doc):
    mode: Literal["full", "pointer", "hash_only"] = "full"
    scan: Literal["required", "rules", "off"] = "rules"
    fields: list[Ident] = Field(
        default_factory=list, max_length=20, description="The fields holding content."
    )


class Axis(Doc):
    origin: Literal["source", "derived", "platform"]
    rule: RuleRef | None = None
    immutable: bool = False


class TimeAxes(Doc):
    """Named time axes: each key other than `timers_on` is an axis."""

    model_config = ConfigDict(extra="allow", json_schema_extra={"propertyNames": {"pattern": rf"^{_IDENT}$"}})
    __pydantic_extra__: dict[str, Axis] = Field(init=False)
    timers_on: Ident | None = Field(default=None, description="The axis timers count on.")

    @model_validator(mode="after")
    def _named(self) -> "TimeAxes":
        if bad := sorted(name for name in self.axes if not re.fullmatch(_IDENT, name)):
            raise ValueError(f"axis names are lowercase identifiers: {', '.join(bad)}")
        if len(self.axes) > 16:
            raise ValueError("a type has at most 16 time axes")
        return self

    @property
    def axes(self) -> dict[str, Axis]:
        return dict(self.__pydantic_extra__ or {})


class Observer(Doc):
    who: Who
    rule: RuleRef | None = None
    validity: Duration | Literal["never"]
    overrides: list[Who] = Field(default_factory=list, max_length=8)


class ClaimPolicy(AliasedDoc):
    allowed: bool
    class_: Literal["money", "percent", "date", "quantity", "count", "duration", "dosage", "text"] | None = (
        Field(default=None, alias="class")
    )
    nature: Literal["computed", "quoted", "observed"] | None = None


class Attribute(Doc):
    family: Label
    vocabulary: Label | None = None


class FieldFreshness(AliasedDoc):
    class_: FreshnessClass = Field(alias="class")
    max_age: Duration | None = None
    claim_max_age: Duration | None = None


class Completeness(Doc):
    levels: list[Label] | None = Field(default=None, min_length=2, max_length=8)
    total_field: Ident | None = None


class AccessRule(Doc):
    readers: list[Label] = Field(min_length=1, max_length=50)
    purposes: list[Label] = Field(default_factory=list, max_length=50)
    effect: Literal["allow", "mask", "deny"]


class FieldSpec(Doc):
    type: FieldType
    label: Annotated[str, StringConstraints(min_length=1, max_length=60)] | None = Field(
        default=None,
        description="The field's name in the space's language, as a text for a model says it "
        "(`coparticipação`); the field's own name otherwise.",
    )
    logic: Literal["plain", "tri"] = "plain"
    observers: list[Observer] = Field(default_factory=list, max_length=8)
    unobserved_blocks: list[Label] = Field(default_factory=list, max_length=20)
    role: Label | None = None
    claim: ClaimPolicy | None = None
    attribute: Attribute | None = None
    freshness: FieldFreshness | None = None
    completeness: Completeness | None = None
    sensitivity: Sensitivity | None = None
    access: list[AccessRule] = Field(default_factory=list, max_length=20)
    pii: bool = False
    track_changes: bool = False

    def max_age_ms(self) -> int | None:
        """How old a value may be before a read calls it stale: its `max_age`, else its class's default.
        None for a field with no freshness, or of class `none`, which never goes stale."""
        freshness = self.freshness
        if freshness is None or freshness.class_ is FreshnessClass.NONE:
            return None
        return duration_ms(freshness.max_age) if freshness.max_age else DEFAULT_MAX_AGE_MS[freshness.class_]

    def claim_max_age_ms(self) -> float:
        """How old a value may be and still be claimed: its `claim_max_age`, else `max_age_ms()`, else no
        limit."""
        if self.freshness is not None and self.freshness.claim_max_age:
            return duration_ms(self.freshness.claim_max_age)
        limit = self.max_age_ms()
        return float("inf") if limit is None else limit

    @property
    def claimable(self) -> bool:
        return bool(self.claim and self.claim.allowed)


class ValueSpec(Doc):
    """A value the company computes and Niadra keeps as a versioned fact: the rule stays the company's."""

    rule: RuleRef
    inputs: list[InputRef] = Field(default_factory=list, max_length=20)
    validity: Literal["until_input_or_rule_change"] | Duration = "until_input_or_rule_change"
    declared_gaps: list[Label] = Field(default_factory=list, max_length=20)
    gap_effect: Literal["errs_early", "errs_late", "unknown"] = "unknown"
    absent_as: Ident | None = Field(default=None, description="The name the value takes when it is absent.")


class KnownDefect(Doc):
    field: Ident
    use_source: Ident


class Sanity(Doc):
    min_items: int = Field(ge=1)


class Licence(Doc):
    commercial_use: Literal["allowed", "forbidden"] = "allowed"
    exclude_from_purposes: list[Label] = Field(default_factory=list, max_length=20)


class SourceSpec(Doc):
    kind: Literal["push", "pull", "tool_observation", "batch", "quote", "human"]
    precedence: int = Field(ge=0, le=100, description="Lower comes first.")
    authoritative_for: list[FieldOrAll] = Field(default_factory=list, max_length=100)
    scope: Literal["global", "customer", "context"] = "global"
    sanity: Sanity | None = None
    known_defects: list[KnownDefect] = Field(default_factory=list, max_length=20)
    active_when: Expression | None = None
    empty_means: Literal["absent", "unobserved"] = "unobserved"
    cost: Literal["free", "paid"] = "free"
    licence: Licence | None = None


UnionRule = Literal[
    "first_authoritative_live", "first_by_precedence", "union", "min", "max", "latest", "divergence"
]
Actor = Literal["source", "human", "agent", "clock"]


class Transition(AliasedDoc):
    from_: Ident | Literal["*"] = Field(alias="from")
    to: Ident
    by: list[Actor] = Field(min_length=1, max_length=4)
    when: Expression | None = None
    evidence: Literal["required"] | None = None
    erase_derived: list[Label] = Field(default_factory=list, max_length=20)


class Forbidden(AliasedDoc):
    from_: Ident = Field(alias="from")
    to: Ident


class ExpiredWithoutOutcome(Doc):
    after: Duration | Ident = Field(description="A duration, or the name of a value holding the deadline.")


class OutcomeVocabulary(Doc):
    source: Ident
    map: dict[Annotated[str, StringConstraints(min_length=1, max_length=64)], Ident] = Field(max_length=200)
    precedence: int = Field(default=0, ge=0, le=100)


class Outcome(Doc):
    provisional: list[Ident] = Field(default_factory=list, max_length=20)
    final: list[Ident] = Field(default_factory=list, max_length=20)
    expired_without_outcome: ExpiredWithoutOutcome | None = None
    vocabularies: list[OutcomeVocabulary] = Field(default_factory=list, max_length=10)


class Lifecycle(Doc):
    transitions: list[Transition] = Field(default_factory=list, max_length=100)
    forbidden: list[Forbidden] = Field(default_factory=list, max_length=100)
    latches: list[Ident] = Field(default_factory=list, max_length=20)
    outcome: Outcome | None = None


TimerAction = Annotated[
    str, StringConstraints(pattern=rf"^(notify|refresh\({_IDENT}\)|transition\({_IDENT}->{_IDENT}\))$")
]
_TRANSITION = re.compile(rf"^transition\(({_IDENT})->({_IDENT})\)$")
_REFRESH = re.compile(rf"^refresh\(({_IDENT})\)$")


class Timer(Doc):
    due: Expression = Field(description="When it fires: a date or a datetime on the `timers_on` axis.")
    once_per: Ident | None = None
    on_fire: list[TimerAction] = Field(min_length=1, max_length=5)


class Budget(Doc):
    units: Annotated[int, Field(ge=0)] | Literal["unlimited"]
    per: (
        Annotated[
            str,
            StringConstraints(
                pattern=r"^(item|object|turn|session|conversation|subject|space|day)(/[0-9]{1,6}(ms|s|min|h|d))?$"
            ),
        ]
        | None
    ) = Field(default=None, description="The scope and period the units count in, `item/1h`.")


class RefetchReason(Doc):
    name: Ident
    when: Expression
    priority: Literal["high", "normal", "low"] = "normal"
    budget: Budget | None = None


class RefetchBudget(Doc):
    unit: Label
    never_money: Literal[True] = Field(
        default=True, description="Budgets are in the company's units, never in money."
    )
    min_interval: Duration | None = None
    floor: Duration | None = None


class Refetch(Doc):
    reasons: list[RefetchReason] = Field(default_factory=list, max_length=20)
    never: list[Ident] = Field(default_factory=list, max_length=20)
    budget: RefetchBudget | None = None
    unconfirmed_watch: Literal["fire", "drop"] = Field(
        default="fire",
        description="A watch whose condition holds on a value that did not come live from its source asks "
        "the resolver worker for the value again (`watch_revalidation`, on the `interest_alert` budget). "
        "When none comes (no worker, the budget refused, the worker released it or gave up), the watch "
        "fires with `revalidated: false`, or is dropped.",
    )


class DisplayPurpose(Doc):
    on_stale: Literal["serve_with_age"] = "serve_with_age"


class ClaimPurpose(Doc):
    on_stale: Literal["serve_with_prohibitions"] = "serve_with_prohibitions"
    prohibitions: list[Label] = Field(default_factory=list, max_length=20)


class DecidePurpose(Doc):
    on_stale: Literal["serve_with_age", "structured_refusal"] = "serve_with_age"
    reason: Label | None = None
    action: Label | None = None


class Purposes(Doc):
    """What a read with each purpose does with stale data: the read itself never refuses."""

    display: DisplayPurpose = Field(default_factory=DisplayPurpose)
    claim: ClaimPurpose = Field(default_factory=ClaimPurpose)
    decide: DecidePurpose = Field(default_factory=DecidePurpose)


class Reading(Doc):
    expr: Expression | None = None
    fields: list[Annotated[str, StringConstraints(pattern=rf"^(\*|{_IDENT})$")]] = Field(
        default_factory=list, max_length=50
    )
    purposes: list[Label] = Field(default_factory=list, max_length=20)
    prefer_source: Ident | None = None
    include_inputs: bool = False


class Relation(Doc):
    type: TypeName
    via: Path = Field(
        description="The path in this object's state to the related objects' ids; with `match`, to the value "
        "they name this object by."
    )
    many: bool = False
    status_field: Ident | None = None
    required_field: Ident | None = None
    match: Ident | None = Field(
        default=None,
        description="A reverse relation: the related objects are the customers' objects of `type` whose "
        "field of this name holds the value at `via` (a lawsuit's notices by its `case_number`).",
    )

    @model_validator(mode="after")
    def _reverse_names_no_entries(self) -> Self:
        if self.match is not None and (self.status_field or self.required_field):
            raise ValueError("a reverse relation (`match`) has no entries: no status_field or required_field")
        return self


class DerivedField(Doc):
    """A field computed when the object is read, from the related objects of one relation (a look's pieces):
    never stored, so it follows every change of a related object at once."""

    agg: Literal["min", "max", "count", "all", "any"]
    over: Ident = Field(description="The relation whose related objects it is computed over.")
    where: Expression | None = Field(
        default=None,
        description="A condition on each related object, in its own type's terms: `count` counts the ones it "
        "holds for, `all` and `any` say whether it holds for every one or for some, and `min` and `max` "
        "compare only those.",
    )
    field: Ident | None = Field(
        default=None, description="The related objects' field `min` and `max` compare; only they name one."
    )


class WorkingSet(Doc):
    enter_on: list[Literal["presented", "engaged", "related", "watched", "committed"]] = Field(
        min_length=1, max_length=5
    )
    leave_after: Duration
    cap: int | None = Field(default=None, ge=1)


class AgentStatePolicy(Doc):
    """How an agent's working state of a type is scoped and written. A state is kept whole, with no cap: a
    write is bounded only by the request's own size limit, and refused there."""

    scope: Literal["conversation", "task", "object", "subject"] = "conversation"
    write: Literal["cas", "merge_by_key", "both"] = Field(
        default="both", description="The write modes allowed."
    )


class ObjectType(Doc):
    """One object type: a sale, a lawsuit, a proposal, an item, an agent's working state. Declared by the
    company or derived from its schema; every value it describes keeps its source, its observer and its
    stamps, and freshness is computed when it is read."""

    type: TypeName
    version: Version = "1"
    ownership: Literal["subject", "shared", "agent"]
    nature: Literal["observed", "derived"] = "observed"
    mirror_of: MirrorOf | None = None
    key: Key | None = None
    revisions: Revisions | None = None
    content: ContentPolicy | None = None
    time: TimeAxes | None = None
    inputs: list[InputRef] = Field(
        default_factory=list, max_length=20, description="What a derived object is a function of."
    )
    fields: dict[Ident, FieldSpec] = Field(default_factory=dict, max_length=200)
    values: dict[Ident, ValueSpec] = Field(default_factory=dict, max_length=50)
    sources: dict[Ident, SourceSpec] = Field(default_factory=dict, max_length=20)
    union: dict[FieldOrAll, UnionRule] = Field(default_factory=dict, max_length=200)
    states: list[Ident] = Field(default_factory=list, max_length=50)
    lifecycle: Lifecycle | None = None
    timers: dict[Ident, Timer] = Field(default_factory=dict, max_length=20)
    refetch: Refetch | None = None
    purposes: Purposes | None = None
    readings: dict[ReadingName, Reading] = Field(default_factory=dict, max_length=50)
    relations: dict[Ident, Relation] = Field(default_factory=dict, max_length=20)
    derived_fields: dict[Ident, DerivedField] = Field(default_factory=dict, max_length=50)
    working_set: WorkingSet | None = None
    agent_state: AgentStatePolicy | None = None
    retention: dict[Ident, Duration | Literal["while_in_working_set"]] = Field(
        default_factory=dict, max_length=20
    )
    _observed: frozenset[str] = PrivateAttr(default_factory=frozenset)
    _inferred: bool = PrivateAttr(default=False)
    _field_words: dict[str, str] = PrivateAttr(default_factory=dict)
    _value_words: dict[str, str] = PrivateAttr(default_factory=dict)

    @property
    def lifecycle_states(self) -> list[str]:
        """The states an object of the type may be in, as a person declared them; none for a type Niadra
        inferred, whose list is what it saw so far: a state a system sends later is the object's state at
        once, never dropped until a next run lists it."""
        return [] if self._inferred else self.states

    @property
    def observed_fields(self) -> frozenset[str]:
        """The text fields Niadra declared from the traffic, never a person: an identifier
        names the object and finds it in a conversation as a declared one does, and each value still reaches
        an agent only past the hold an undeclared field's value passes (`domain.objects.undeclared`), since
        no person ruled the field not personal."""
        return self._observed

    def ruled(self) -> frozenset[str]:
        """Every name a person declared on the type: its fields but the ones Niadra declared from the traffic,
        its computed values, derived fields and time axes. Any other field reaches an agent past the hold of
        an undeclared one (`domain.objects.undeclared.shown`)."""
        return (
            (frozenset(self.fields) - self.observed_fields)
            | frozenset(self.values)
            | frozenset(self.derived_fields)
            | frozenset(self.axes)
        )

    def field_word(self, name: str) -> str | None:
        """What a line calls a field: its declared label, else the space's word for it written from the
        traffic (`domain.objects.words`); None when it has neither."""
        spec = self.fields.get(name)
        if spec is not None and spec.label:
            return spec.label
        return self._field_words.get(name)

    def value_word(self, value: Any) -> Any:
        """A code the space's systems send ("business_days", a state "ingested") in the space's words, when
        one was written; any other value as it is."""
        return self._value_words.get(value, value) if isinstance(value, str) else value

    @property
    def axes(self) -> dict[str, Axis]:
        return self.time.axes if self.time else {}

    def tracked_fields(self) -> frozenset[str]:
        """The fields that keep their previous value (`track_changes`)."""
        return frozenset(name for name, spec in self.fields.items() if spec.track_changes)

    def scope(self) -> Scope:
        """What the type's expressions may name."""
        return Scope(
            fields={name: KIND_OF_TYPE[spec.type] for name, spec in self.fields.items()}
            | dict.fromkeys(self.derived_fields),
            completeness=frozenset(n for n, spec in self.fields.items() if spec.completeness is not None),
            values=frozenset(self.values),
            axes=frozenset(self.axes),
            absent_names=frozenset(v.absent_as for v in self.values.values() if v.absent_as),
            inputs=frozenset(ref for ref in self.inputs if "@" not in ref),
            states=frozenset(self.states),
            sources={name: source.kind for name, source in self.sources.items()},
        )

    @model_validator(mode="after")
    def _consistent(self) -> "ObjectType":
        problems = _Problems()
        self._ownership(problems)
        self._names(problems)
        self._fields(problems)
        self._sources(problems)
        self._lifecycle(problems)
        self._timers(problems)
        self._expressions(problems)
        problems.raise_if_any()
        return self

    def _ownership(self, problems: "_Problems") -> None:
        if self.ownership != "agent" and self.mirror_of is None:
            problems.add("mirror_of", "a subject or shared type mirrors a system of the company")
        if self.working_set is not None and self.ownership != "shared":
            problems.add("working_set", "only a shared type has a working set")
        if self.agent_state is not None and self.ownership != "agent":
            problems.add("agent_state", "only an agent's type has an agent_state policy")
        if self.nature == "derived" and not self.inputs:
            problems.add("inputs", "a derived type names the inputs it is a function of")
        if self.nature != "derived" and self.inputs:
            problems.add("inputs", "only a derived type has inputs")
        if self.mirror_of and self.mirror_of.derived_by == "introspection" and not self.mirror_of.fingerprint:
            problems.add(
                "mirror_of.fingerprint", "a type derived by introspection keeps the fingerprint it read"
            )
        for name, kept in self.retention.items():
            if kept == "while_in_working_set" and self.ownership != "shared":
                problems.add(f"retention.{name}", "only a shared type is kept while in the working set")

    def _names(self, problems: "_Problems") -> None:
        seen: dict[str, str] = {}
        groups: dict[str, Iterable[str]] = {
            "fields": self.fields,
            "values": self.values,
            "time": self.axes,
            "derived_fields": self.derived_fields,
        }
        for group, names in groups.items():
            for name in names:
                if name in RESERVED:
                    problems.add(f"{group}.{name}", f"{name!r} is reserved by the expression language")
                if name in seen:
                    problems.add(f"{group}.{name}", f"{name!r} is already declared in {seen[name]}")
                seen.setdefault(name, group)
        for name, value in self.values.items():
            if value.absent_as in RESERVED:
                problems.add(f"values.{name}.absent_as", f"{value.absent_as!r} is reserved")
            if value.absent_as in self.fields:
                problems.add(f"values.{name}.absent_as", f"{value.absent_as!r} is a field")
        if len(set(self.states)) != len(self.states):
            problems.add("states", "a state is listed twice")
        timers_on = self.time.timers_on if self.time else None
        if timers_on is not None and timers_on not in self.axes:
            problems.add("time.timers_on", f"{timers_on!r} is not a declared time axis")
        for name, axis in self.axes.items():
            if axis.origin == "derived" and axis.rule is None:
                problems.add(f"time.{name}", "a derived axis names the rule that computes it")
            if axis.origin != "derived" and axis.rule is not None:
                problems.add(f"time.{name}", "only a derived axis has a rule")
        if self.content:
            for name in self.content.fields:
                if name not in self.fields:
                    problems.add("content.fields", f"{name!r} is not a declared field")
        for name, derived in self.derived_fields.items():
            if derived.over not in self.relations:
                problems.add(f"derived_fields.{name}.over", f"{derived.over!r} is not a declared relation")
            if (derived.agg in ("min", "max")) != (derived.field is not None):
                problems.add(
                    f"derived_fields.{name}.field", "min and max name the field they compare, only they"
                )
        reasons = [reason.name for reason in self.refetch.reasons] if self.refetch else []
        if len(set(reasons)) != len(reasons):
            problems.add("refetch.reasons", "a reason is named twice")

    def line_order(self) -> tuple[str, ...]:
        """The fields in the order an object's line says them, all of them: the ones whose changes the type
        tracks (`track_changes`) first, then the ones an agent may claim (`claim` allowed, a claim's amount),
        then the rest, each in the order the type declares them. An order only: the line says every field
        the object holds a value for, except what no text for a model carries."""
        order = list(self.fields)

        def rank(name: str) -> tuple[int, int]:
            spec = self.fields[name]
            tier = 0 if spec.track_changes else 1 if spec.claim is not None and spec.claim.allowed else 2
            return tier, order.index(name)

        return tuple(sorted(order, key=rank))

    def written_keys(self) -> tuple[str, ...]:
        """The fields a conversation may name one of this type's objects by, writing the value as it is: the
        ones `key.match` gives a shape, then the natural keys that are fields, then every other plain string
        field. Never personal data, a sensitive or access-ruled field, nor content. With no `key.match` at
        all, a case number or an order number still names its object: `domain.objects.written` says which
        values are specific enough, and resolution only reads the objects the customer is tied to."""
        declared = list(self.key.match) if self.key else []
        natural = [name for name in self.key.natural if name in self.fields] if self.key else []
        rest = [name for name, spec in self.fields.items() if spec.type == "string"]
        content = set(self.content.fields) if self.content else set()
        out: list[str] = []
        for name in (*declared, *natural, *rest):
            spec = self.fields.get(name)
            if name in out or name in content:
                continue
            if spec is not None and (spec.pii or spec.sensitivity is not None or spec.access):
                continue
            out.append(name)
        return tuple(out)

    def _fields(self, problems: "_Problems") -> None:
        if self.key is not None:
            for name in self.key.match:
                if name not in self.key.natural and name not in self.fields:
                    problems.add(f"key.match.{name}", f"{name!r} is neither a natural key path nor a field")
        for name, spec in self.fields.items():
            at = f"fields.{name}"
            if spec.logic != "tri" and (spec.observers or spec.unobserved_blocks):
                problems.add(at, "observers and unobserved_blocks belong to a field with logic: tri")
            whos = [observer.who for observer in spec.observers]
            if len(set(whos)) != len(whos):
                problems.add(f"{at}.observers", "an observer is listed twice")
            for observer in spec.observers:
                for other in observer.overrides:
                    if other not in whos or other == observer.who:
                        problems.add(
                            f"{at}.observers", f"{observer.who} overrides {other}, not another observer"
                        )
            if spec.claim and spec.claim.allowed and spec.claim.class_ is None:
                problems.add(f"{at}.claim", "a claim that is allowed names its class")
            if spec.completeness:
                levels, total = spec.completeness.levels, spec.completeness.total_field
                if (levels is None) == (total is None):
                    problems.add(f"{at}.completeness", "completeness has either levels or a total_field")
                if total is not None and total not in self.fields:
                    problems.add(f"{at}.completeness.total_field", f"{total!r} is not a declared field")
            if spec.freshness:
                self._freshness(problems, at, spec.freshness)
        for name in self.union:
            if name != "*" and name not in self.fields and name not in self.values:
                problems.add(f"union.{name}", f"{name!r} is not a declared field or value")

    @staticmethod
    def _freshness(problems: "_Problems", at: str, freshness: FieldFreshness) -> None:
        if freshness.class_ is FreshnessClass.NONE:
            if freshness.max_age or freshness.claim_max_age:
                problems.add(f"{at}.freshness", "a field of class none never goes stale and has no max_age")
            return
        max_age = (
            duration_ms(freshness.max_age) if freshness.max_age else DEFAULT_MAX_AGE_MS[freshness.class_]
        )
        claim_max_age = duration_ms(freshness.claim_max_age) if freshness.claim_max_age else None
        if max_age is not None and claim_max_age is not None and claim_max_age > max_age:
            problems.add(f"{at}.freshness", "claim_max_age is at most max_age")

    def _sources(self, problems: "_Problems") -> None:
        for name, source in self.sources.items():
            for defect in source.known_defects:
                at = f"sources.{name}.known_defects"
                if defect.field not in self.fields and defect.field not in self.values:
                    problems.add(at, f"{defect.field!r} is not a declared field or value")
                if defect.use_source not in self.sources or defect.use_source == name:
                    problems.add(at, f"{defect.use_source!r} is not another declared source")
        for name, reading in self.readings.items():
            if reading.expr is None and not reading.fields:
                problems.add(f"readings.{name}", "a reading has an expression, fields or both")
            if reading.prefer_source and reading.prefer_source not in self.sources:
                problems.add(
                    f"readings.{name}.prefer_source", f"{reading.prefer_source!r} is not a declared source"
                )
        purposes = self.purposes
        if purposes and purposes.decide.on_stale == "structured_refusal" and purposes.decide.reason is None:
            problems.add("purposes.decide", "a structured refusal names its reason")

    def _lifecycle(self, problems: "_Problems") -> None:
        lifecycle, states = self.lifecycle, set(self.states)
        if lifecycle is None:
            return
        if not states:
            problems.add("lifecycle", "a lifecycle needs the type's states")

        def known(at: str, names: Iterable[str]) -> None:
            for state in names:
                if state not in states:
                    problems.add(at, f"{state!r} is not a declared state")

        declared = set()
        for i, transition in enumerate(lifecycle.transitions):
            known(
                f"lifecycle.transitions.{i}",
                [transition.to] + ([] if transition.from_ == "*" else [transition.from_]),
            )
            declared.add((transition.from_, transition.to))
        for i, rule in enumerate(lifecycle.forbidden):
            known(f"lifecycle.forbidden.{i}", [rule.from_, rule.to])
            if (rule.from_, rule.to) in declared:
                problems.add(f"lifecycle.forbidden.{i}", f"{rule.from_} -> {rule.to} is also a transition")
        known("lifecycle.latches", lifecycle.latches)
        outcome = lifecycle.outcome
        if outcome is None:
            return
        known("lifecycle.outcome", [*outcome.provisional, *outcome.final])
        if set(outcome.provisional) & set(outcome.final):
            problems.add("lifecycle.outcome", "a state is either a provisional or a final outcome")
        expired = outcome.expired_without_outcome
        after = expired.after if expired else None
        if after and not re.fullmatch(r"[0-9]{1,6}(ms|s|min|h|d)", after) and after not in self.values:
            problems.add(
                "lifecycle.outcome.expired_without_outcome.after", f"{after!r} is not a declared value"
            )
        for i, vocabulary in enumerate(outcome.vocabularies):
            if vocabulary.source not in self.sources:
                problems.add(
                    f"lifecycle.outcome.vocabularies.{i}", f"{vocabulary.source!r} is not a declared source"
                )
            known(f"lifecycle.outcome.vocabularies.{i}.map", vocabulary.map.values())

    def _timers(self, problems: "_Problems") -> None:
        transitions = self.lifecycle.transitions if self.lifecycle else []
        if "expired_without_outcome" in self.timers:
            problems.add("timers.expired_without_outcome", "the name is the outcome's own deadline")
        for name, timer in self.timers.items():
            for action in timer.on_fire:
                at = f"timers.{name}.on_fire"
                if (refresh := _REFRESH.match(action)) and refresh.group(1) not in self.fields:
                    problems.add(at, f"{action} names an undeclared field")
                if move := _TRANSITION.match(action):
                    source, target = move.groups()
                    fits = [t for t in transitions if t.to == target and t.from_ in (source, "*")]
                    if not fits:
                        problems.add(at, f"{action} is not a transition of the lifecycle")
                    elif not any("clock" in t.by for t in fits):
                        problems.add(
                            at, f"{action} fires on the clock, and the lifecycle does not let the clock"
                        )

    def _expressions(self, problems: "_Problems") -> None:
        scope = self.scope()
        for at, text, expect in self._expression_texts():
            try:
                compile_expression(text, scope, expect)
            except ExprError as exc:
                problems.add(at, exc.detail)

    def _expression_texts(self) -> Iterable[tuple[str, str, Expect]]:
        if self.key and self.key.fingerprint:
            yield "key.fingerprint", self.key.fingerprint, Expect.ANY
        for name, source in self.sources.items():
            if source.active_when:
                yield f"sources.{name}.active_when", source.active_when, Expect.CONDITION
        for i, transition in enumerate(self.lifecycle.transitions if self.lifecycle else []):
            if transition.when:
                yield f"lifecycle.transitions.{i}.when", transition.when, Expect.CONDITION
        for name, timer in self.timers.items():
            yield f"timers.{name}.due", timer.due, Expect.TIME
        for reason in self.refetch.reasons if self.refetch else []:
            yield f"refetch.reasons.{reason.name}.when", reason.when, Expect.CONDITION
        for name, reading in self.readings.items():
            if reading.expr:
                yield f"readings.{name}.expr", reading.expr, Expect.ANY


class _Problems:
    """Every inconsistency of a declaration at once, each with its path, so one diff fixes them all."""

    def __init__(self) -> None:
        self.items: list[str] = []

    def add(self, at: str, detail: str) -> None:
        self.items.append(f"{at}: {detail}")

    def raise_if_any(self) -> None:
        if self.items:
            raise ValueError("; ".join(self.items))


class ObjectTypeRegistry(Doc):
    """The `object-types` document of a space: every type it declared or derived."""

    version: str = "0"
    types: list[ObjectType] = Field(default_factory=list, max_length=200)
    commercial_purposes: list[Label] = Field(
        default_factory=list,
        max_length=20,
        description="The purposes whose reads are commercial: a source whose licence forbids commercial use "
        "never feeds them.",
    )
    derived_fields_in_text: bool = Field(
        default=False,
        description="Whether the state block's lines for the model say the objects' derived fields. A state "
        "read carries them either way.",
    )

    def get(self, name: str) -> ObjectType | None:
        return next((declared for declared in self.types if declared.type == name), None)

    def under(
        self, observed: Sequence[Mapping[str, Any]], words: Words | None = None
    ) -> "ObjectTypeRegistry":
        """This registry with the types the space's traffic showed and the company did not declare:
        a declared type is never touched. An observed type that does not hold together alone, or
        with the rest, is left out, so the declared registry always serves. The observed types say their
        fields and codes in the space's `words` (`domain.objects.words.Words`) where no label was declared."""
        if not observed:
            return self
        declared = {t.type for t in self.types}
        joined: list[ObjectType] = list(self.types)
        for doc in observed:
            if doc.get("type") in declared:
                continue
            # The type's own words over the space's, for what it shares with another type.
            fields, values = words.of(str(doc.get("type"))) if words is not None else ({}, {})
            # An observed field's label is its name made readable: the space's word replaces it.
            worded = {
                name: {**spec, "label": fields[name]}
                if name in fields and isinstance(spec, Mapping)
                else spec
                for name, spec in (doc.get("fields") or {}).items()
            }
            try:
                inferred = ObjectType.model_validate({**doc, "fields": worded} if worded else doc)
            except ValidationError:
                continue
            inferred._observed = frozenset(n for n, spec in inferred.fields.items() if spec.type == "string")
            inferred._inferred = True
            inferred._field_words = fields
            inferred._value_words = values
            joined.append(inferred)
        try:
            merged = ObjectTypeRegistry(types=joined).types
        except ValidationError:
            return self
        for kept, built in zip(merged, joined, strict=True):
            kept._observed = built._observed
            kept._inferred = built._inferred
            kept._field_words = built._field_words
            kept._value_words = built._value_words
        return self.model_copy(update={"types": merged})

    def private_fields(self) -> dict[str, frozenset[str]]:
        """Per type, the fields no text for a model carries: personal data (`pii`), a sensitive category, or
        access rules that some reader may not pass. The pack's system and event lines, the history and the
        state block leave them out; a state read with its purpose is where an agent gets them, under field
        access."""
        return {
            declared.type: frozenset(
                name
                for name, spec in declared.fields.items()
                if spec.pii or spec.sensitivity is not None or spec.access
            )
            for declared in self.types
        }

    def content_fields(self) -> dict[str, frozenset[str]]:
        """Per type, the fields holding a third party's content (`content.fields`): a model reads them only
        inside the state block's envelope, after their scan, never bare on a pack's line."""
        return {
            declared.type: frozenset(declared.content.fields)
            for declared in self.types
            if declared.content is not None and declared.content.fields
        }

    def field_labels(self) -> dict[str, dict[str, str]]:
        """Per type, the label of each field that has one: what a line calls the field."""
        return {
            declared.type: {
                **declared._field_words,
                **{name: spec.label for name, spec in declared.fields.items() if spec.label},
            }
            for declared in self.types
        }

    def value_words(self) -> dict[str, dict[str, str]]:
        """Per type, the space's words for the codes its systems send (`ObjectType.value_word`)."""
        return {
            declared.type: dict(declared._value_words) for declared in self.types if declared._value_words
        }

    def date_fields(self) -> dict[str, frozenset[str]]:
        """Per type, its date and datetime fields and the dates its timers count from (a computed `due_on`): a
        line writes them as the space writes dates. A pack line read "due_on: 2026-10-13" (juridico-sandbox,
        06/10/2026)."""
        return {
            declared.type: frozenset(
                name for name, spec in declared.fields.items() if spec.type in ("date", "datetime")
            )
            | {name for timer in declared.timers.values() if (name := anchor(timer.due)) is not None}
            for declared in self.types
        }

    def name_fields(self) -> dict[str, tuple[tuple[str, ...], frozenset[str], frozenset[str]]]:
        """Per type, the fields an object may go by on a line (`ObjectType.written_keys`), with the ones
        `key.match` gives a shape, which name it with any value, and its natural keys, which name it with one
        digit (`domain.objects.written.distinctive`)."""
        return {
            declared.type: (
                declared.written_keys(),
                frozenset(declared.key.match if declared.key else ()),
                frozenset(declared.key.natural if declared.key else ()),
            )
            for declared in self.types
        }

    def parent_fields(self) -> dict[str, tuple[tuple[str, str], ...]]:
        """Per type, (field, related type) of each to-one relation whose path is one of its fields: the
        object a line names a child by ("audiência de Número CNJ ..."), its opaque id said nothing."""
        return {
            declared.type: found
            for declared in self.types
            if (
                found := tuple(
                    (r.via, r.type)
                    for r in declared.relations.values()
                    if not r.many and r.match is None and r.via.isidentifier()
                )
            )
        }

    def declared_names(self) -> dict[str, frozenset[str]]:
        """Per type, every name it declares: its fields, computed values, derived fields and time axes. A
        field an object holds outside them is one the company's system sent undeclared (24.2.1)."""
        return {declared.type: declared.ruled() for declared in self.types}

    def line_order(self) -> dict[str, tuple[str, ...]]:
        """Per type, the fields in the order an object's line says them: the ones whose changes the type
        tracks (`track_changes`) first, then the ones an agent may claim (`claim` allowed, a claim's amount),
        then the rest, each in the order the type declares them."""
        return {declared.type: declared.line_order() for declared in self.types}

    def labels(self) -> dict[str, str]:
        """The label of each field that has one, keyed `type.field`."""
        return {
            f"{declared.type}.{name}": spec.label
            for declared in self.types
            for name, spec in declared.fields.items()
            if spec.label
        }

    def matched_fields(self, type_: str) -> frozenset[str]:
        """The fields of `type_` a reverse relation of any type finds its objects by, and the natural keys
        the type's own `key.match` finds them by in a conversation."""
        declared = self.get(type_)
        return frozenset(
            relation.match
            for other in self.types
            for relation in other.relations.values()
            if relation.match is not None and relation.type == type_
        ) | frozenset(declared.key.match if declared is not None and declared.key else ())

    def key_patterns(self) -> list[tuple[str, str, re.Pattern[str]]]:
        """(type, natural key, compiled pattern) of every `key.match` the subject's types declare."""
        return [
            (declared.type, name, re.compile(pattern))
            for declared in self.types
            if declared.ownership == "subject" and declared.key is not None
            for name, pattern in sorted(declared.key.match.items())
        ]

    def guards_reads(self) -> bool:
        """Whether any field declares access rules or any source a licence: otherwise every reader reads the
        same objects."""
        return any(
            any(spec.access for spec in declared.fields.values())
            or any(source.licence for source in declared.sources.values())
            for declared in self.types
        )

    @model_validator(mode="after")
    def _unique(self) -> "ObjectTypeRegistry":
        names = [t.type for t in self.types]
        if twice := sorted({name for name in names if names.count(name) > 1}):
            raise ValueError(f"types declared twice: {', '.join(twice)}")
        problems = _Problems()
        for declared in self.types:
            for name, reverse in declared.relations.items():
                target = self.get(reverse.type)
                if reverse.match is None or target is None:
                    continue
                at = f"{declared.type}.relations.{name}.match"
                if target.ownership != "subject":
                    problems.add(at, "a reverse relation reads customers' objects; a shared type's are named")
                elif reverse.match not in target.fields:
                    problems.add(at, f"{reverse.match!r} is not a field of {target.type}")
            for name, derived in declared.derived_fields.items():
                relation = declared.relations.get(derived.over)
                related = self.get(relation.type) if relation else None
                if relation is None:
                    continue
                at = f"{declared.type}.derived_fields.{name}"
                if related is None:
                    problems.add(at, f"the relation's type {relation.type!r} is not declared")
                    continue
                if derived.field is not None and derived.field not in related.fields:
                    problems.add(f"{at}.field", f"{derived.field!r} is not a field of {related.type}")
                if derived.where:
                    try:
                        compile_expression(derived.where, related.scope(), Expect.CONDITION)
                    except ExprError as exc:
                        problems.add(f"{at}.where", exc.detail)
        problems.raise_if_any()
        return self


def escalate(before: ObjectTypeRegistry, after: ObjectTypeRegistry) -> frozenset[Role]:
    """The roles a diff of the registry needs besides `integration` (24.2): `security` when it loosens what
    the privacy of a field rests on (its sensitivity, access, personal data or licence, and the purposes a
    licence calls commercial) or what a read may do with it (the purposes). A change that only tightens
    them needs nothing more: restricting a field is never held for an approval."""
    return frozenset({Role.SECURITY}) if loosens(before, after) else frozenset()


_EFFECT_RANK = {Effect.DENY: 0, Effect.MASK: 1, Effect.ALLOW: 2}
_MAX_PURPOSES_COMPARED = 6
"""Beyond this many purposes named by a field's rules, a change of its access is compared no further and
taken as loosening: the comparison enumerates every combination of them."""


def loosens(before: ObjectTypeRegistry, after: ObjectTypeRegistry) -> bool:
    """Whether the diff leaves some reader with more than it had: a field's personal data or sensitivity
    taken away or changed, a reader whose access effect rises (deny, then mask, then allow), a licence
    relaxed, a field's content envelope dropped or its scan relaxed, a reading opened to more purposes, a
    commercial purpose dropped, or a declared type's read purposes changed. A new type loosens nothing: what
    it declares only restricts. Everything else, and every change that only narrows, is a tightening."""
    was, now = _guarded(before), _guarded(after)
    existing = {t.type for t in before.types}
    for key in was.keys() | now.keys():
        old, new = was.get(key), now.get(key)
        if old == new:
            continue
        if ".fields." in key:
            if _field_loosens(old, new):
                return True
        elif ".sources." in key:
            if not _licence_narrows(old, new):
                return True
        elif key.endswith(".content"):
            if _content_loosens(old, new):
                return True
        elif ".readings." in key:
            if old is not None and (new is None or not _labels(new) <= _labels(old)):
                return True
        elif key == "commercial_purposes":
            if not _labels(old) <= _labels(new):
                return True
        elif key.endswith(".purposes") and key.removesuffix(".purposes") in existing:
            # What a read may do with a type's stale values: any change of a declared type's asks.
            return True
    return False


_SCAN_RANK = {"off": 0, "rules": 1, "required": 2}
_MODE_RANK = {"hash_only": 0, "pointer": 1, "full": 2}


def _content_loosens(old: object, new: object) -> bool:
    """A third party's words lose their envelope: a field taken out of `content` (a declared plain field goes
    on a line as it is, unscanned), a scan relaxed, or more of the text kept."""
    if not isinstance(old, tuple):
        return False
    if not isinstance(new, tuple):
        return True
    mode, scan, fields = old
    new_mode, new_scan, new_fields = new
    return (
        not set(fields) <= set(new_fields)
        or _SCAN_RANK[new_scan] < _SCAN_RANK[scan]
        or _MODE_RANK[new_mode] > _MODE_RANK[mode]
    )


def _labels(value: object) -> set[str]:
    return {str(v) for v in value} if isinstance(value, tuple | list) else set()


def _field_loosens(old: object, new: object) -> bool:
    sensitivity, access, pii = old if isinstance(old, tuple) else (None, [], False)
    new_sensitivity, new_access, new_pii = new if isinstance(new, tuple) else (None, [], False)
    if pii and not new_pii:
        return True
    if sensitivity is not None and new_sensitivity != sensitivity:
        return True
    return access != new_access and _access_loosens(access, new_access)


def _access_loosens(old: Sequence[AccessRule], new: Sequence[AccessRule]) -> bool:
    names = {n for rule in (*old, *new) for n in rule.readers} - {PUBLIC}
    purposes = sorted({p for rule in (*old, *new) for p in rule.purposes})
    if len(purposes) > _MAX_PURPOSES_COMPARED:
        return True
    readers = [frozenset({PUBLIC})] + [frozenset({PUBLIC, *_twins(n)}) for n in sorted(names)]
    purpose_sets = [
        frozenset(c) for size in range(len(purposes) + 1) for c in itertools.combinations(purposes, size)
    ]
    for reader_names in readers:
        for reading_for in purpose_sets:
            reader = FieldReader(reader_names, reading_for)
            if _EFFECT_RANK[field_effect(new, reader)] > _EFFECT_RANK[field_effect(old, reader)]:
                return True
    return False


def _twins(name: str) -> set[str]:
    """A source's reader carries both its `agent:` and `source:` names (`policy.fields.reader_of`)."""
    kind, _, rest = name.partition(":")
    if kind in ("agent", "source") and rest:
        return {f"agent:{rest}", f"source:{rest}"}
    return {name}


def _licence_narrows(old: object, new: object) -> bool:
    if not isinstance(old, Licence):
        return True
    if not isinstance(new, Licence):
        return False
    if old.commercial_use == "forbidden" and new.commercial_use != "forbidden":
        return False
    return set(old.exclude_from_purposes) <= set(new.exclude_from_purposes)


def _guarded(registry: ObjectTypeRegistry) -> dict[str, object]:
    guarded: dict[str, object] = {}
    for declared in registry.types:
        name = declared.type
        for field, spec in declared.fields.items():
            if spec.sensitivity or spec.access or spec.pii:
                guarded[f"{name}.fields.{field}"] = (spec.sensitivity, spec.access, spec.pii)
        for source, origin in declared.sources.items():
            if origin.licence:
                guarded[f"{name}.sources.{source}"] = origin.licence
        if declared.content and declared.content.fields:
            content = declared.content
            guarded[f"{name}.content"] = (content.mode, content.scan, tuple(sorted(content.fields)))
        if declared.purposes:
            guarded[f"{name}.purposes"] = declared.purposes
        for reading, how in declared.readings.items():
            if how.purposes:
                guarded[f"{name}.readings.{reading}"] = tuple(how.purposes)
    if registry.commercial_purposes:
        guarded["commercial_purposes"] = tuple(sorted(registry.commercial_purposes))
    return guarded
