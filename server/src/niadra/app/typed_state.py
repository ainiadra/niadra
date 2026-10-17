"""A typed object as a read serves it (24.3.1 to 24.3.5; the open spec `object-state.v1`).

Pure: the stored observations, the type's declaration and the moment of the read go in, the object for one
purpose comes out. Nothing here is stored. Which source a field serves, whether it may be claimed and how
fresh it is are decided at every read against the declaration in force (L19), so a new rule applies to every
object at once, without rewriting a row. A read never refuses: the refusal a type asks for travels beside the
values, for the company's tool.
"""

import functools
import re
from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, timedelta
from typing import Any, Literal
from uuid import UUID

from niadra.config.object_types import FieldSpec, ObjectType, SourceSpec, ValueSpec
from niadra.contracts.state import (
    ContentMarker,
    DerivedState,
    FieldState,
    ObjectRead,
    OutcomeState,
    PreviousValue,
    ReadingResult,
    Refusal,
    StateRef,
    TimerState,
    ValueState,
)
from niadra.domain.objects.state import BOOKKEEPING, LIFECYCLE, SYSTEM_PREFIX, TOOL_PREFIX
from niadra.domain.objects.undeclared import fenced, held, shown
from niadra.domain.policy.fields import FieldReader, field_effect, licence_allows
from niadra.domain.policy.rules import Effect
from niadra.domain.records import BusinessObject, ObjectKey
from niadra.domain.state.expr import (
    DAY_MS,
    MINUTE_MS,
    SECOND_MS,
    Environment,
    ExprError,
    Kind,
    Node,
    Slot,
    Value,
    absent,
    boolean,
    duration_ms,
    epoch_ms,
    evaluate,
    format_datetime,
    parse,
    unknown,
)
from niadra.domain.state.lifecycle import Outcome, Stage, Vocabulary, outcome_of, stage_of
from niadra.domain.state.logic import Logic
from niadra.domain.state.observed import Holding, Observation
from niadra.domain.state.typed import UNTYPED, Typed, ValueVersion

Purpose = Literal["display", "claim", "decide"]

PRIORITIES = {"high": 0, "normal": 1, "low": 2}
_SCOPE_PERIOD_S = {"turn": 30, "session": 1800, "conversation": 1800, "day": 86400}
"""A reason's budget counted per turn, session or conversation, which the producer does not see: per
object, for this long."""
DEFAULT_PERIOD_S = 3600
WATCH_REVALIDATION = "watch_revalidation"
"""The refresh reason of a watch whose condition held on a value that did not come live from its source."""
INTEREST_ALERT = "interest_alert"
"""The type's reason whose budget and priority a watch's revalidation takes."""
UNDECLARED_PRECEDENCE = 1000
"""Where a source the type does not declare stands: after every declared one."""
_KIND_OF_TYPE = {
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
_IDENT = re.compile(r"[^a-z0-9]+")
_EPOCH_ORDINAL = date(1970, 1, 1).toordinal()


@dataclass(frozen=True, slots=True)
class TypedObject:
    """What a read needs of a stored object: its key, its observations, when it last changed, and its
    lifecycle, values' versions and timers."""

    key: ObjectKey
    fields: Holding
    as_of: datetime | None = None
    derived_status: str | None = None
    typed: Typed = UNTYPED


@dataclass(frozen=True, slots=True)
class Source:
    """A stored observation's source as the type names it: a declared source, or one it does not declare."""

    name: str
    spec: SourceSpec | None = None

    def order(self, observation: Observation) -> tuple[int, float]:
        precedence = self.spec.precedence if self.spec else UNDECLARED_PRECEDENCE
        return precedence, -observation.valid_at.timestamp()


class Attribution:
    """Names the sources the observations were stored under by the sources the type declares.

    A system's key (a system event, a push) is the declared source of the same name as the key's source, or
    else the type's one source of kind `push`; a tool's observation is the declared source named after the
    tool, or else the type's first source of kind `tool_observation`. Decided at the read, so a declaration
    that names a source later applies to what was stored before it."""

    def __init__(self, declared: ObjectType, names: Mapping[UUID, str]) -> None:
        self._declared = declared
        self._names = names
        pushes = [name for name, spec in declared.sources.items() if spec.kind == "push"]
        self._push = pushes[0] if len(pushes) == 1 else None
        tools = sorted(
            (spec.precedence, name)
            for name, spec in declared.sources.items()
            if spec.kind == "tool_observation"
        )
        self._tool = tools[0][1] if tools else None

    @functools.cache  # noqa: B019 (one instance per read)
    def __call__(self, key: str) -> Source:
        sources = self._declared.sources
        if key.startswith(SYSTEM_PREFIX):
            try:
                own = self._names.get(UUID(key.removeprefix(SYSTEM_PREFIX)))
            except ValueError:
                own = None
            named = _IDENT.sub("_", own.lower()).strip("_") if own else None
            found = named if named in sources else self._push
            return Source(found, sources[found]) if found else Source(own or key)
        if key.startswith(TOOL_PREFIX):
            tool = key.removeprefix(TOOL_PREFIX)
            found = tool if tool in sources else self._tool
            return Source(found, sources[found]) if found else Source(tool)
        return Source(key, sources.get(key))


@dataclass(frozen=True, slots=True)
class Candidate:
    source: Source
    observation: Observation
    observer: str


@dataclass(frozen=True, slots=True)
class Resolved:
    """A field as its sources resolve it, before freshness: the value, its logical value and whose it is."""

    value: Any
    logic: Logic
    chosen: Candidate | None = None
    lapsed: bool = False
    """Some observation stood until its observer's validity ended."""
    licensed_out: bool = False
    """Only sources whose licence bars this reader observed it."""


UNOBSERVED = Resolved(None, Logic.UNOBSERVED)


def resolve(
    name: str,
    observations: Mapping[str, Observation],
    declared: ObjectType | None,
    attribution: Attribution | None,
    now: datetime,
    *,
    barred: Collection[str] = (),
    prefer: str | None = None,
) -> Resolved:
    """Which observation a field serves: active and sane sources whose licence admits the reader (none of
    `barred`), observations whose observer is still valid, the type's union rule (or the source a reading
    `prefer`s, when it observed the field), an observer that overrides another, and a known defect that
    switches the source."""
    spec = declared.fields.get(name) if declared else None
    candidates = [
        _candidate(attribution(key) if attribution else Source(key), o, spec)
        for key, o in sorted(observations.items())
    ]
    licensed = [c for c in candidates if c.source.name not in barred]
    candidates = [c for c in licensed if _usable(c)]
    valid = [c for c in candidates if _valid(c, spec, now)]
    lapsed = len(valid) < len(candidates)
    if not valid:
        return replace(UNOBSERVED, lapsed=lapsed, licensed_out=bool(observations) and not licensed)
    rule = (declared.union.get(name) or declared.union.get("*")) if declared else None
    chosen, value = _union(rule or ("first_by_precedence" if declared else "latest"), name, valid)
    preferred = next((c for c in valid if c.source.name == prefer), None) if prefer else None
    if preferred is not None:
        chosen, value = preferred, preferred.observation.value
    chosen = _overridden(chosen, valid, spec)
    defective = _defect(chosen, name)
    if defective is not None:
        replacement = next((c for c in valid if c.source.name == defective), None)
        if replacement is None:
            return Resolved(None, Logic.KNOWN_DEFECT, chosen, lapsed)
        chosen, value = replacement, replacement.observation.value
    return Resolved(value, _logic(value, chosen), chosen, lapsed)


def resolved_state(
    holding: Holding,
    declared: ObjectType | None,
    names: Mapping[UUID, str],
    now: datetime,
    *,
    barred: Collection[str] = (),
) -> dict[str, Any]:
    """Each field's value as the declaration resolves it, for the readers of the plain state (the context
    pack, the Console, `GET /v1/objects/...`, the measurement of outcomes): what a known defect or a lower
    precedence hides is left out, and the lifecycle state is the canonical one, as a read computes it. The
    values of the `barred` sources are left out too, for a reader their licence excludes."""
    attribution = Attribution(declared, names) if declared else None
    state: dict[str, Any] = {}
    for name, observations in sorted(holding.items()):
        found = resolve(name, observations, declared, attribution, now, barred=barred)
        if found.logic.known:
            state[name] = found.value
    if declared is not None and attribution is not None and LIFECYCLE in holding:
        stage = lifecycle_stage(holding, declared, attribution)
        if stage is None:
            state.pop(LIFECYCLE, None)
        else:
            state[LIFECYCLE] = stage.state
    return state


@dataclass(frozen=True, slots=True)
class ReadRequest:
    purpose: Purpose = "display"
    fields: Collection[str] | None = None
    readings: Collection[str] = ()
    reader: FieldReader | None = None
    """Who reads, for field access and licences; None for the platform's own reads."""
    commercial: Collection[str] = ()
    """The purposes the registry marks commercial, which a licence may forbid."""


def read(
    obj: TypedObject,
    declared: ObjectType,
    request: ReadRequest,
    *,
    names: Mapping[UUID, str],
    now: datetime,
    utc_offset_min: int = 0,
    derived: Mapping[str, DerivedState] | None = None,
    derived_withheld: Collection[str] = (),
) -> ObjectRead:
    """One object as a read with `request.purpose` serves it (object-state v1, sections 3 to 7), to the reader
    the request names: fields its access denies and values only a source its licence bars observed are
    withheld, and fields its access masks are served without their value. `derived` are its derived fields
    as the read computed them from the related objects, which its expressions read too; `derived_withheld`
    the ones that read a related field this reader may not."""
    attribution = Attribution(declared, names)
    reader = request.reader.reading(request.purpose) if request.reader else None
    barred = barred_sources(declared, reader, request.commercial)
    expired_by_input = obj.derived_status == "expired_by_input"
    reported = {name for name in obj.fields if name not in BOOKKEEPING or name in declared.fields}
    known = (set(declared.fields) | reported) - {LIFECYCLE}
    resolved = {
        name: resolve(name, obj.fields.get(name, {}), declared, attribution, now, barred=barred)
        for name in sorted(known)
    }
    stage = lifecycle_stage(obj.fields, declared, attribution)
    env = environment(
        obj, declared, resolved, stage, now, purpose=request.purpose, utc_offset_min=utc_offset_min
    )
    derived = derived or {}
    if derived:
        env = replace(env, fields={**env.fields, **{n: _derived_slot(d) for n, d in derived.items()}})
    blocked = _blocked(declared, resolved, stage)
    purposes = reader.purposes if reader else frozenset({request.purpose})
    served = _served(declared, request, known - set(declared.values) - set(_derived_axes(declared)), purposes)
    access = _access(declared, served, reader)
    markers = _content(declared, obj, resolved)
    withheld: dict[str, Literal["access", "licence", "scan"]] = {
        name: "access" for name, effect in access.items() if effect is Effect.DENY
    }
    for name in sorted(served):
        if name not in withheld and resolved.get(name, UNOBSERVED).licensed_out:
            withheld[name] = "licence"
    for name, marker in markers.items():
        if name not in withheld and _held_back(declared, marker):
            withheld[name] = "scan"
    for name in sorted(derived_withheld):
        withheld[name] = "access"
    if reader is not None:
        # A field the type does not declare is served like a declared one, unless it may be personal data
        # (held as access would hold it) or a third party's free text no scan cleared (held as content is):
        # `domain.objects.undeclared`. The platform's own reads (the Console) see it as it is.
        for name in sorted(served - declared.ruled() - set(withheld)):
            observed = resolved.get(name)
            why = held(name, observed.value) if observed is not None and observed.logic.known else None
            if why == "content" and observed is not None and fenced(name, observed.value) is not None:
                # A third party's short sentence the scan clears goes as content, inside the envelope.
                markers[name] = ContentMarker(mode="full", scan="clean")
            elif why is not None:
                withheld[name] = "access" if why == "personal" else "scan"
    fields = {
        name: _field_state(
            resolved.get(name, UNOBSERVED),
            declared.fields.get(name),
            now,
            expired_by_input=expired_by_input,
            claim_blocked="claim" in blocked,
            masked=access.get(name) is Effect.MASK,
        )
        for name in sorted(served - set(withheld))
    }
    for name, marker in markers.items():
        if name in fields and not fields[name].masked:
            # Content is claimed only once the scan cleared it (object-state 4.4, item 5).
            safe = fields[name].claim_safe and marker.scan == "clean"
            fields[name] = fields[name].model_copy(update={"content": marker, "claim_safe": safe})
    values = {
        name: state
        for name, spec in declared.values.items()
        if name not in withheld
        and (
            state := _value_state(
                resolved.get(name, UNOBSERVED),
                spec,
                now,
                expired_by_input,
                "claim" in blocked,
                obj.typed.values.get(name),
            )
        )
        is not None
    }
    hidden = {name for name, effect in access.items() if effect is not Effect.ALLOW}
    readings = _readings(obj, declared, request, reader, env, hidden, barred, names, now)
    # Stale or unsound data is what the prohibitions are about: a field nobody reported has nothing to claim.
    unsafe = [
        not f.claim_safe
        for name, f in fields.items()
        if _claimable(declared.fields.get(name)) and f.observed_at is not None
    ]
    unsafe += [not v.claim_safe for v in values.values()]
    stale_rules = declared.purposes
    outdated = expired_by_input or any(resolved[name].lapsed for name in served if name in resolved)
    found = _outcome(declared, stage, obj, env, now)
    return ObjectRead(
        ref=StateRef(type=obj.key.type, namespace=obj.key.namespace, id=obj.key.external_id),
        type_version=declared.version,
        as_of=obj.as_of,
        state=stage.state if stage else None,
        latches=dict(obj.typed.latches),
        outcome=OutcomeState(state=found.state, final=found.final, at=found.at) if found else None,
        derived_status=_derived_status(obj.derived_status),
        expired_by=list(obj.typed.expired_by) if expired_by_input else [],
        axes=_axes(declared, env),
        fields=fields,
        values=values,
        derived=dict(derived),
        timers=_timers(declared, obj.typed, env),
        readings=readings,
        prohibitions=list(stale_rules.claim.prohibitions) if stale_rules and any(unsafe) else [],
        declared_gaps=_gaps(values.values()),
        withheld=withheld,
        blocked={what: sorted(names) for what, names in sorted(blocked.items())},
        refusal=_refusal(declared, request.purpose, [*fields.values(), *values.values()], blocked, outdated),
    )


def field_access(declared: ObjectType, reader: FieldReader) -> dict[str, Effect]:
    """The fields of a type this reader may not read as they are, each with its effect (`mask` or `deny`):
    what the SDK masks in a tool's output before the model sees it (24.2.11)."""
    return {
        name: effect
        for name, spec in sorted(declared.fields.items())
        if spec.access and (effect := field_effect(spec.access, reader)) is not Effect.ALLOW
    }


def plain_for_reader(
    obj: BusinessObject,
    declared: ObjectType,
    reader: FieldReader,
    commercial: Collection[str],
    names: Mapping[UUID, str],
    now: datetime,
) -> dict[str, Any]:
    """An object's plain state for one reader (the pack, `GET /v1/objects/...`): without the fields its access
    masks or denies, nor the values only a source its licence bars observed. The stored state as it is when
    the type declares neither."""
    barred = barred_sources(declared, reader, commercial)
    hidden = field_access(declared, reader)
    state = resolved_state(obj.fields, declared, names, now, barred=barred) if barred else dict(obj.state)
    # An undeclared field that may be personal data or free text reaches no agent before it is declared
    # (`domain.objects.undeclared`), as on a read.
    ruled = declared.ruled()
    return {name: value for name, value in state.items() if name not in hidden and shown(name, value, ruled)}


def _access(declared: ObjectType, served: Collection[str], reader: FieldReader | None) -> dict[str, Effect]:
    if reader is None:
        return {}
    found: dict[str, Effect] = {}
    for name in served:
        spec = declared.fields.get(name)
        if spec is not None and spec.access:
            found[name] = field_effect(spec.access, reader)
    return found


def barred_sources(
    declared: ObjectType, reader: FieldReader | None, commercial: Collection[str]
) -> frozenset[str]:
    """The declared sources whose licence keeps their values from this reader (24.2.4)."""
    if reader is None:
        return frozenset()
    return frozenset(
        name
        for name, source in declared.sources.items()
        if not licence_allows(source.licence, reader, commercial)
    )


@dataclass(frozen=True, slots=True)
class Evaluated:
    """An object as the type's expressions see it: each field resolved, its lifecycle stage and the
    environment."""

    resolved: Mapping[str, Resolved]
    stage: Stage | None
    env: Environment


def evaluated(
    obj: TypedObject,
    declared: ObjectType,
    names: Mapping[UUID, str],
    now: datetime,
    *,
    utc_offset_min: int = 0,
    purpose: str = "display",
    barred: Collection[str] = (),
) -> Evaluated:
    """`barred`: the sources whose licence keeps their values from the reader."""
    attribution = Attribution(declared, names)
    known = (set(declared.fields) | set(obj.fields)) - {LIFECYCLE}
    resolved = {
        name: resolve(name, obj.fields.get(name, {}), declared, attribution, now, barred=barred)
        for name in sorted(known)
    }
    stage = lifecycle_stage(obj.fields, declared, attribution)
    return Evaluated(
        resolved,
        stage,
        environment(obj, declared, resolved, stage, now, purpose=purpose, utc_offset_min=utc_offset_min),
    )


def lifecycle_stage(holding: Holding, declared: ObjectType, attribution: Attribution) -> Stage | None:
    """The object's lifecycle state (24.3.8), each system's codes translated by its vocabulary."""
    observations = holding.get(LIFECYCLE)
    if not observations:
        return None
    outcome = declared.lifecycle.outcome if declared.lifecycle else None
    vocabularies = {
        v.source: Vocabulary(v.map, v.precedence) for v in (outcome.vocabularies if outcome else ())
    }
    return stage_of(
        ((attribution(key).name, o) for key, o in sorted(observations.items())),
        declared.lifecycle_states,
        vocabularies,
    )


def environment(
    obj: TypedObject,
    declared: ObjectType,
    resolved: Mapping[str, Resolved],
    stage: Stage | None,
    now: datetime,
    *,
    purpose: str = "display",
    utc_offset_min: int = 0,
) -> Environment:
    """What the type's expressions see of the object: its fields, computed values and time axes, the axes the
    platform keeps (when Niadra first knew of the object, when its content last changed), and its state."""
    timed = set(declared.values) | set(declared.axes)
    slots = {
        name: _slot(found, declared.fields.get(name), temporal=name in timed)
        for name, found in resolved.items()
    }
    for name, axis in declared.axes.items():
        if axis.origin == "platform" and (at := _platform_axis(name, obj.fields, declared)) is not None:
            slots[name] = Slot(Value(Logic.YES, Kind.DATETIME, epoch_ms(at)), at=epoch_ms(at))
    return Environment(
        now=epoch_ms(now),
        utc_offset_min=utc_offset_min,
        fields=slots,
        absent_names=frozenset(v.absent_as for v in declared.values.values() if v.absent_as),
        state=stage.state if stage else None,
        derived_status=obj.derived_status,
        purpose=purpose,
    )


def due_of(text: str, env: Environment) -> datetime | None:
    """When an expression of a date or a datetime falls (a date at the start of its day), if it is known."""
    try:
        value = evaluate(_parsed(text), env)
    except ExprError:
        return None
    if not value.logic.known or value.is_absent:
        return None
    if value.kind is Kind.DATE:
        assert isinstance(value.datum, date)
        ms = (value.datum.toordinal() - _EPOCH_ORDINAL) * DAY_MS - env.utc_offset_min * MINUTE_MS
        return datetime.fromtimestamp(ms / 1000, UTC)
    if value.kind is Kind.DATETIME:
        assert isinstance(value.datum, int)
        return datetime.fromtimestamp(value.datum / 1000, UTC)
    return None


def outcome_deadline(declared: ObjectType, obj: TypedObject, env: Environment) -> datetime | None:
    """When an object with no final outcome expires without one: a duration after Niadra first knew of it,
    or the moment a declared value (a deadline) falls."""
    outcome = declared.lifecycle.outcome if declared.lifecycle else None
    expired = outcome.expired_without_outcome if outcome else None
    if expired is None:
        return None
    if expired.after in declared.values:
        return due_of(expired.after, env)
    first = first_known(obj.fields)
    return first + timedelta(milliseconds=duration_ms(expired.after)) if first else None


def newest(holding: Holding) -> datetime | None:
    """When the latest observation of the object was made at its source."""
    return max((o.observed_at for by in holding.values() for o in by.values()), default=None)


def first_known(holding: Holding) -> datetime | None:
    return min((o.known_at for by in holding.values() for o in by.values()), default=None)


def _platform_axis(name: str, holding: Holding, declared: ObjectType) -> datetime | None:
    """`known_at` is when Niadra first knew of the object; any other axis the platform keeps is when its
    content (the type's content fields, or any field) last changed."""
    if name == "known_at":
        return first_known(holding)
    content = set(declared.content.fields) if declared.content and declared.content.fields else None
    return max(
        (
            o.known_at
            for field, by in holding.items()
            if content is None or field in content
            for o in by.values()
        ),
        default=None,
    )


def _derived_axes(declared: ObjectType) -> list[str]:
    return [name for name, axis in declared.axes.items() if axis.origin == "derived"]


def _axes(declared: ObjectType, env: Environment) -> dict[str, str]:
    """Each declared time axis whose value is known: a date as `YYYY-MM-DD`, an instant in UTC."""
    axes: dict[str, str] = {}
    for name in declared.axes:
        slot = env.fields.get(name)
        if slot is None or not slot.value.logic.known or slot.value.is_absent:
            continue
        if slot.value.kind in (Kind.DATE, Kind.DATETIME):
            axes[name] = _datum(slot.value)
    return axes


def _timers(declared: ObjectType, typed: Typed, env: Environment) -> list[TimerState]:
    """Each declared timer as it stands, its due time computed now from the rule and the values."""
    timers: list[TimerState] = []
    for name, timer in declared.timers.items():
        mark = typed.timers.get(name)
        due = due_of(timer.due, env)
        if mark is None and due is None:
            continue
        timers.append(
            TimerState(
                name=name,
                due_at=due,
                state=mark.state if mark else "armed",
                fired_at=mark.fired_at if mark else None,
                supersedes_firing_id=str(mark.supersedes_firing_id)
                if mark and mark.supersedes_firing_id
                else None,
            )
        )
    return timers


def _outcome(
    declared: ObjectType, stage: Stage | None, obj: TypedObject, env: Environment, now: datetime
) -> Outcome | None:
    outcome = declared.lifecycle.outcome if declared.lifecycle else None
    if outcome is None:
        return None
    return outcome_of(stage, outcome.provisional, outcome.final, outcome_deadline(declared, obj, env), now)


def _value_state(
    resolved: Resolved,
    spec: ValueSpec,
    now: datetime,
    expired_by_input: bool,
    claim_blocked: bool,
    version: ValueVersion | None,
) -> ValueState | None:
    """A value the company computed, as its latest version holds it (L26); the read knows its rule and gaps
    from the declaration. Nothing is served for a value never reported."""
    chosen = resolved.chosen
    if chosen is None or resolved.logic is Logic.KNOWN_DEFECT:
        return None
    observation = chosen.observation
    named = resolved.value is None or resolved.value == spec.absent_as
    status: Literal["fresh", "expired"] = "fresh"
    if expired_by_input or (
        spec.validity != "until_input_or_rule_change"
        and (now - observation.valid_at).total_seconds() * 1000 > duration_ms(spec.validity)
    ):
        status = "expired"
    return ValueState(
        v=None if named else resolved.value,
        logic="no" if named else "yes",
        absent_as=spec.absent_as if named else None,
        version=version.version if version else 1,
        rule=version.rule if version else spec.rule,
        computed_at=observation.valid_at,
        status=status,
        supersedes_version=version.supersedes if version else None,
        declared_gaps=list(spec.declared_gaps),
        gap_effect=spec.gap_effect,
        claim_safe=status == "fresh" and not claim_blocked,
    )


def _gaps(values: Iterable[ValueState]) -> list[str]:
    gaps: list[str] = []
    for value in values:
        gaps += [gap for gap in value.declared_gaps if gap not in gaps]
    return gaps


def _derived_status(status: str | None) -> Literal["current", "expired_by_input"] | None:
    if status == "current":
        return "current"
    return "expired_by_input" if status == "expired_by_input" else None


def _candidate(source: Source, observation: Observation, spec: FieldSpec | None) -> Candidate:
    return Candidate(source, observation, _observer(source, observation, spec))


def _observer(source: Source, observation: Observation, spec: FieldSpec | None) -> str:
    """Who observed a value: as reported; a person for a source of kind `human`; the machine for a field that
    names it among its observers and not the source; otherwise the source itself."""
    if observation.observer:
        return observation.observer
    if source.spec and source.spec.kind == "human":
        return "human"
    own = f"source:{source.name}"
    whos = {o.who for o in spec.observers} if spec else set()
    return "machine" if "machine" in whos and own not in whos else own


def _usable(candidate: Candidate) -> bool:
    """A source is used when its condition holds (none is known for now: `config.<key>` reads absent) and a
    list it reports is as long as the source's sanity asks."""
    spec = candidate.source.spec
    if spec is None:
        return True
    if spec.active_when is not None and _truth(spec.active_when) is not Logic.YES:
        return False
    value = candidate.observation.value
    return not (spec.sanity and isinstance(value, list) and len(value) < spec.sanity.min_items)


def _valid(candidate: Candidate, spec: FieldSpec | None, now: datetime) -> bool:
    if spec is None or not spec.observers:
        return True
    rule = next((o for o in spec.observers if o.who == candidate.observer), None)
    if rule is None or rule.validity == "never":
        return True
    age_ms = (now - candidate.observation.observed_at).total_seconds() * 1000
    return age_ms <= duration_ms(rule.validity)


def _union(rule: str, name: str, candidates: list[Candidate]) -> tuple[Candidate, Any]:
    ordered = sorted(candidates, key=lambda c: c.source.order(c.observation))
    if rule == "first_authoritative_live":
        live = [c for c in ordered if _authoritative(c, name) and c.observation.provenance in (None, "live")]
        chosen = live[0] if live else ordered[0]
        return chosen, chosen.observation.value
    if rule == "latest":
        chosen = max(candidates, key=lambda c: (c.observation.valid_at, c.observation.known_at))
        return chosen, chosen.observation.value
    if rule in ("min", "max"):
        numbers = [c for c in ordered if _number(c.observation.value)]
        if numbers:
            pick = min if rule == "min" else max
            chosen = pick(numbers, key=lambda c: c.observation.value)
            return chosen, chosen.observation.value
    if rule == "union":
        items: list[Any] = []
        for c in ordered:
            value = c.observation.value
            for item in value if isinstance(value, list) else [value]:
                if item is not None and item not in items:
                    items.append(item)
        # The union is as old as its oldest part.
        oldest = min(ordered, key=lambda c: c.observation.observed_at)
        return oldest, items
    return ordered[0], ordered[0].observation.value


def _authoritative(candidate: Candidate, name: str) -> bool:
    spec = candidate.source.spec
    return spec is not None and (name in spec.authoritative_for or "*" in spec.authoritative_for)


def _overridden(chosen: Candidate, candidates: list[Candidate], spec: FieldSpec | None) -> Candidate:
    """A person's observation outranks a machine's when the field says so (`overrides`)."""
    if spec is None or not spec.observers:
        return chosen
    rules = {o.who: o for o in spec.observers}
    for other in sorted(candidates, key=lambda c: c.source.order(c.observation)):
        rule = rules.get(other.observer)
        if rule is not None and chosen.observer in rule.overrides:
            return other
    return chosen


def _defect(chosen: Candidate, name: str) -> str | None:
    spec = chosen.source.spec
    if spec is None:
        return None
    return next((d.use_source for d in spec.known_defects if d.field == name), None)


def _logic(value: Any, chosen: Candidate) -> Logic:
    if value is None:
        absent_means = chosen.source.spec is not None and chosen.source.spec.empty_means == "absent"
        return Logic.NO if absent_means else Logic.UNOBSERVED
    if value is False:
        return Logic.NO
    return Logic.YES


def _number(value: Any) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool)


def _blocked(
    declared: ObjectType, resolved: Mapping[str, Resolved], stage: Stage | None
) -> dict[str, set[str]]:
    """What the unknown fields block, each only what its declaration names (L25); and an object in a sealing
    state is neither read by a model nor derived from, blocked by its `state`."""
    blocked: dict[str, set[str]] = {}
    for name, spec in declared.fields.items():
        if spec.unobserved_blocks and not resolved.get(name, UNOBSERVED).logic.known:
            for what in spec.unobserved_blocks:
                blocked.setdefault(what, set()).add(name)
    if stage is not None and stage.state in sealing_states(declared):
        for what in ("model_read", "derive"):
            blocked.setdefault(what, set()).add(LIFECYCLE)
    return blocked


def sealing_states(declared: ObjectType) -> frozenset[str]:
    """The states a transition that erases derived memory leads to (a case the court put under seal)."""
    transitions = declared.lifecycle.transitions if declared.lifecycle else []
    return frozenset(t.to for t in transitions if t.erase_derived)


def _content(
    declared: ObjectType, obj: TypedObject, resolved: Mapping[str, Resolved]
) -> dict[str, ContentMarker]:
    """Each content field held, with its mode, digest and scan state (L27): what a field holds as a pointer is
    the pointer the reader's SDK resolves inside the company's boundary."""
    content = declared.content
    if content is None:
        return {}
    markers: dict[str, ContentMarker] = {}
    for name in content.fields:
        found = resolved.get(name)
        if found is None or found.value is None:
            continue
        mark = obj.typed.scans.get(name)
        default = "pending" if content.scan == "required" else "clean"
        markers[name] = ContentMarker(
            mode=content.mode,
            sha256=mark.sha256 if mark else None,
            pointer=str(found.value) if content.mode == "pointer" else None,
            scan=mark.state if mark else default,
        )
    return markers


def _held_back(declared: ObjectType, marker: ContentMarker) -> bool:
    """Flagged content never reaches a read; pending content does not either when its type asks for a full
    scan."""
    required = declared.content is not None and declared.content.scan == "required"
    return marker.scan == "flagged" or (marker.scan == "pending" and required)


def _served(
    declared: ObjectType, request: ReadRequest, known: Collection[str], purposes: Collection[str]
) -> set[str]:
    wanted = set(known) if request.fields is None else set(request.fields) - {LIFECYCLE}
    for name in request.readings:
        reading = declared.readings.get(name)
        if reading is not None and (not reading.purposes or not set(purposes).isdisjoint(reading.purposes)):
            wanted |= set(declared.fields) if "*" in reading.fields else set(reading.fields)
    return wanted


def _field_state(
    resolved: Resolved,
    spec: FieldSpec | None,
    now: datetime,
    *,
    expired_by_input: bool,
    claim_blocked: bool,
    masked: bool = False,
) -> FieldState:
    """`masked`: the reader's access masks the field; its stamps are served, never its value."""
    chosen = resolved.chosen
    observation = chosen.observation if chosen else None
    age_ms = int((now - observation.observed_at).total_seconds() * 1000) if observation else None
    status = _status(resolved, spec, age_ms, expired_by_input)
    claim_safe = (
        _claimable(spec)
        and resolved.logic.known
        and status == "fresh"
        and age_ms is not None
        and age_ms <= _claim_max_age(spec)
        and observation is not None
        and observation.provenance in (None, "live")
        and not expired_by_input
        and not claim_blocked
        and not masked
    )
    was = observation.was if observation and spec and spec.track_changes and not masked else None
    held = resolved.value if resolved.logic is Logic.YES or resolved.value is not None else None
    return FieldState(
        v=None if masked else held,
        logic=resolved.logic.value,
        status=status,
        age_s=max(0, age_ms // SECOND_MS) if age_ms is not None else None,
        observed_at=observation.observed_at if observation else None,
        src=chosen.source.name if chosen else None,
        observer=chosen.observer if chosen else None,
        valid_at=observation.valid_at if observation else None,
        known_at=observation.known_at if observation else None,
        role=spec.role if spec else None,
        claim_safe=claim_safe,
        masked=masked,
        was=PreviousValue(v=was.value, logic=_logic_of(was.value).value) if was is not None else None,
    )


def _status(
    resolved: Resolved, spec: FieldSpec | None, age_ms: int | None, expired_by_input: bool
) -> Literal["fresh", "stale", "expired"]:
    if expired_by_input or resolved.logic is Logic.UNOBSERVED or age_ms is None:
        return "expired"
    max_age = _max_age(spec)
    return "stale" if max_age is not None and age_ms > max_age else "fresh"


def _max_age(spec: FieldSpec | None) -> int | None:
    return spec.max_age_ms() if spec else None


def _claim_max_age(spec: FieldSpec | None) -> float:
    return spec.claim_max_age_ms() if spec else float("inf")


def _claimable(spec: FieldSpec | None) -> bool:
    return spec is not None and spec.claimable


def _logic_of(value: Any) -> Logic:
    if value is None:
        return Logic.UNOBSERVED
    return Logic.NO if value is False else Logic.YES


def _refusal(
    declared: ObjectType,
    purpose: Purpose,
    served: Sequence[FieldState | ValueState],
    blocked: Mapping[str, Collection[str]],
    outdated: bool,
) -> Refusal | None:
    """Only for a `decide` read of a type that asks for it: a field too old to decide on (stale, or observed
    until its observer's validity ended), an input that changed, or an unknown field that blocks deciding."""
    decide = declared.purposes.decide if declared.purposes else None
    if purpose != "decide" or decide is None or decide.on_stale != "structured_refusal":
        return None
    if not (outdated or "decide" in blocked or any(f.status == "stale" for f in served)):
        return None
    assert decide.reason is not None
    return Refusal(reason=decide.reason, action=decide.action)


def _readings(
    obj: TypedObject,
    declared: ObjectType,
    request: ReadRequest,
    reader: FieldReader | None,
    env: Environment,
    hidden: Collection[str],
    barred: Collection[str],
    names: Mapping[UUID, str],
    now: datetime,
) -> dict[str, ReadingResult]:
    """The named readings asked for (24.2.13), each only for a purpose it serves: the read's own or one of the
    reader's. A reading's expression never sees what the reader's access hides, reads its fields from the
    source it prefers when that source observed them, and carries the object's inputs when it asks for
    them."""
    purposes = reader.purposes if reader else frozenset({request.purpose})
    seen = replace(env, fields={n: s for n, s in env.fields.items() if n not in hidden})
    found: dict[str, ReadingResult] = {}
    for name in request.readings:
        reading = declared.readings.get(name)
        if reading is None or (reading.purposes and purposes.isdisjoint(reading.purposes)):
            continue
        if reading.expr is None and not reading.include_inputs:
            continue  # made only of fields: they are served among the fields
        result = ReadingResult(logic="yes") if reading.expr is None else None
        if reading.expr is not None:
            scope = seen
            if reading.prefer_source:
                scope = _preferring(obj, declared, reading.prefer_source, barred, names, now, env)
                scope = replace(scope, fields={n: s for n, s in scope.fields.items() if n not in hidden})
            try:
                result = _result(evaluate(_parsed(reading.expr), scope))
            except ExprError:
                # Data of another kind than the declaration's: the reading is not known, never an error.
                result = ReadingResult(logic="unobserved")
        assert result is not None
        if reading.include_inputs:
            result = result.model_copy(update={"inputs": _inputs(obj, declared, names, now)})
        found[name] = result
    return found


def _preferring(
    obj: TypedObject,
    declared: ObjectType,
    source: str,
    barred: Collection[str],
    names: Mapping[UUID, str],
    now: datetime,
    env: Environment,
) -> Environment:
    attribution = Attribution(declared, names)
    known = (set(declared.fields) | set(obj.fields)) - {LIFECYCLE}
    resolved = {
        name: resolve(
            name, obj.fields.get(name, {}), declared, attribution, now, barred=barred, prefer=source
        )
        for name in sorted(known)
    }
    stage = lifecycle_stage(obj.fields, declared, attribution)
    return environment(
        obj, declared, resolved, stage, now, purpose=env.purpose, utc_offset_min=env.utc_offset_min
    )


def _inputs(
    obj: TypedObject, declared: ObjectType, names: Mapping[UUID, str], now: datetime
) -> dict[str, str]:
    """A derived object's inputs, by the name its type gives them, as the objects they are fields of."""
    found = resolve(
        "input_refs", obj.fields.get("input_refs", {}), declared, Attribution(declared, names), now
    )
    refs = found.value if isinstance(found.value, dict) else {}
    return {str(name): str(ref) for name, ref in sorted(refs.items()) if isinstance(ref, str)}


@functools.lru_cache(maxsize=2048)
def _parsed(text: str) -> Node:
    """Expressions were checked against their type when the declaration was accepted."""
    return parse(text)


def _derived_slot(derived: DerivedState) -> Slot:
    return Slot(_value(derived.v, Logic(derived.logic), None))


def _slot(found: Resolved, spec: FieldSpec | None, *, temporal: bool = False) -> Slot:
    """`temporal`: a computed value or a time axis, whose type the declaration does not state; a text in ISO
    form reads as the date or the instant it names."""
    observation = found.chosen.observation if found.chosen else None
    kind = _KIND_OF_TYPE.get(spec.type) if spec else _temporal(found.value) if temporal else None
    was = observation.was if observation else None
    return Slot(
        value=_value(found.value, found.logic, kind),
        at=epoch_ms(observation.observed_at) if observation else None,
        observer=found.chosen.observer if found.chosen else None,
        was=_value(was.value, _logic_of(was.value), kind) if was is not None else None,
    )


def _temporal(raw: Any) -> Kind | None:
    if not isinstance(raw, str):
        return None
    try:
        return Kind.DATE if len(raw) == 10 and date.fromisoformat(raw) else Kind.DATETIME
    except ValueError:
        return None


def _value(raw: Any, logic: Logic, kind: Kind | None) -> Value:
    """A stored value as niadra-expr reads it; a value of another kind than declared reads not observed."""
    if not logic.known:
        return unknown(logic)
    if logic is Logic.NO and raw is not True:
        return boolean(False) if kind is Kind.BOOL or raw is False else absent()
    kind = kind or _kind_of(raw)
    try:
        return _typed(raw, kind)
    except (TypeError, ValueError):
        return unknown(Logic.UNOBSERVED)


def _kind_of(raw: Any) -> Kind | None:
    if isinstance(raw, bool):
        return Kind.BOOL
    if _number(raw):
        return Kind.NUMBER
    if isinstance(raw, str):
        return Kind.STRING
    if isinstance(raw, list):
        return Kind.LIST
    return None


def _typed(raw: Any, kind: Kind | None) -> Value:
    match kind:
        case Kind.BOOL if isinstance(raw, bool):
            return boolean(raw)
        case Kind.NUMBER if _number(raw):
            return Value(Logic.YES, Kind.NUMBER, float(raw))
        case Kind.STRING if isinstance(raw, str):
            return Value(Logic.YES, Kind.STRING, raw)
        case Kind.DATE if isinstance(raw, str):
            return Value(Logic.YES, Kind.DATE, date.fromisoformat(raw))
        case Kind.DATETIME if isinstance(raw, str):
            return Value(Logic.YES, Kind.DATETIME, epoch_ms(datetime.fromisoformat(raw)))
        case Kind.DURATION if isinstance(raw, int) and not isinstance(raw, bool):
            return Value(Logic.YES, Kind.DURATION, raw)
        case Kind.LIST if isinstance(raw, list):
            return Value(Logic.YES, Kind.LIST, tuple(_typed(item, _kind_of(item)) for item in raw))
    raise TypeError(f"not a {kind}")


def _result(value: Value) -> ReadingResult:
    if not value.logic.known:
        return ReadingResult(logic=value.logic.value)
    if value.is_absent:
        return ReadingResult(logic="no", absent=value.absent)
    assert value.kind is not None
    return ReadingResult(logic=value.logic.value, type=value.kind.value, v=_datum(value))


def _datum(value: Value) -> Any:
    match value.kind:
        case Kind.DATE:
            assert isinstance(value.datum, date)
            return value.datum.isoformat()
        case Kind.DATETIME:
            assert isinstance(value.datum, int)
            return format_datetime(value.datum)
        case Kind.LIST:
            assert isinstance(value.datum, tuple)
            return [_datum(item) for item in value.datum]
    return value.datum


def _truth(text: str) -> Logic:
    """A source's condition, evaluated with nothing but the clock: the company's configuration values are
    not known to the producer yet, so `config.<key>` reads absent."""
    try:
        value = evaluate(_parsed(text), Environment(now=0))
    except ExprError:
        return Logic.UNOBSERVED
    return value.logic if value.kind is Kind.BOOL or not value.logic.known else Logic.UNOBSERVED


@dataclass(frozen=True, slots=True)
class Wanted:
    """The reason an object should be fetched again, as its type declares it."""

    reason: str
    priority: str
    units: int | None
    """None for a cost not known: the reservation takes the highest measured."""
    unlimited: bool
    period_s: int
    never: bool


def wanted(declared: ObjectType, env: Environment, newest: datetime | None, now: datetime) -> Wanted | None:
    """The reason with the highest priority that holds (the first declared among equals); past the type's
    `floor` with none holding, `floor`."""
    refetch = declared.refetch
    if refetch is None:
        return None
    found: list[tuple[int, int, Wanted]] = []
    for position, reason in enumerate(refetch.reasons):
        try:
            holds = evaluate(_parsed(reason.when), env).logic is Logic.YES
        except ExprError:
            holds = False
        if not holds:
            continue
        budget = reason.budget
        units: int | None = None
        unlimited = False
        if budget is not None:
            unlimited = budget.units == "unlimited"
            units = None if unlimited else int(budget.units)
        found.append(
            (
                PRIORITIES[reason.priority],
                position,
                Wanted(
                    reason.name,
                    reason.priority,
                    units,
                    unlimited,
                    _period_s(
                        budget.per if budget else None,
                        refetch.budget.min_interval if refetch.budget else None,
                    ),
                    reason.name in refetch.never,
                ),
            )
        )
    if found:
        return min(found, key=lambda f: (f[0], f[1]))[2]
    floor = refetch.budget.floor if refetch.budget else None
    if floor and newest is not None and (now - newest).total_seconds() * 1000 > duration_ms(floor):
        return Wanted("floor", "low", None, False, DEFAULT_PERIOD_S, "floor" in refetch.never)
    return None


def revalidation(declared: ObjectType) -> Wanted | None:
    """The request a watch's revalidation makes: the priority and budget of the type's `interest_alert`
    reason, whatever its condition. None when the type declares no refetch or names the reason in `never`:
    no worker is asked."""
    refetch = declared.refetch
    if refetch is None or WATCH_REVALIDATION in refetch.never:
        return None
    reason = next((r for r in refetch.reasons if r.name == INTEREST_ALERT), None)
    budget = reason.budget if reason else None
    unlimited = budget is not None and budget.units == "unlimited"
    units = None if budget is None or budget.units == "unlimited" else int(budget.units)
    period = _period_s(
        budget.per if budget else None, refetch.budget.min_interval if refetch.budget else None
    )
    return Wanted(
        WATCH_REVALIDATION, reason.priority if reason else "normal", units, unlimited, period, False
    )


def _period_s(per: str | None, min_interval: str | None) -> int:
    """The seconds a reason's budget counts in: `item/1h` is an hour per object; a scope the producer does not
    see (a turn, a session) counts per object for a fixed while."""
    if per is None:
        return duration_ms(min_interval) // 1000 if min_interval else DEFAULT_PERIOD_S
    scope, _, period = per.partition("/")
    if period:
        return max(1, duration_ms(period) // 1000)
    return _SCOPE_PERIOD_S.get(scope, DEFAULT_PERIOD_S)
