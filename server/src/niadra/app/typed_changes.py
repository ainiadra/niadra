"""What a write of an object changes besides its fields (24.3.6 to 24.3.8): the versions of its computed
values, its lifecycle latches and its timers.

Pure: the object before and after a report, its declaration and the moment go in; the typed stamps to keep,
the value versions to record and the timer rows to write come out. A computed value is a fact (L26): a
report that brings a different value, rule or input hash is a new version naming the one it supersedes. A
timer is armed from the rule and the values of the moment; when the value that armed a timer that already
fired is revised to another due time, the timer fires again, naming the firing it supersedes, so a notice
stays once per fact and per version.
"""

import hashlib
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

import orjson

from niadra.app.typed_state import Evaluated, Resolved, TypedObject, due_of, evaluated, outcome_deadline
from niadra.config.object_types import ObjectType, ValueSpec
from niadra.domain.common.ids import uuid7
from niadra.domain.ingest.scan import ScanResult, ScanState, scan
from niadra.domain.state.expr import (
    Arith,
    BoolOp,
    Call,
    Compare,
    ListLit,
    Member,
    Name,
    Node,
    Not,
    duration_ms,
    parse,
)
from niadra.domain.state.lifecycle import EXPIRED_WITHOUT_OUTCOME, latched
from niadra.domain.state.logic import Logic
from niadra.domain.state.typed import NewValue, ScanMark, TimerMark, TimerWrite, Typed, ValueVersion

ARMED, FIRED, CANCELLED = "armed", "fired", "cancelled"


@dataclass(frozen=True, slots=True)
class Planned:
    typed: Typed
    values: list[NewValue] = field(default_factory=list)
    timers: list[TimerWrite] = field(default_factory=list)
    scans: dict[str, ScanMark] = field(default_factory=dict)
    """The content fields whose content is new, with the scan's verdict on it (L27)."""
    erase: frozenset[str] = frozenset()
    """The fields a transition into a sealing state takes with it (`erase_derived`)."""

    @property
    def revised(self) -> list[NewValue]:
        return [v for v in self.values if v.supersedes is not None]


def plan(
    before: TypedObject,
    after: TypedObject,
    declared: ObjectType,
    names: Mapping[UUID, str],
    now: datetime,
    *,
    utc_offset_min: int = 0,
) -> Planned:
    was = evaluated(before, declared, names, now, utc_offset_min=utc_offset_min)
    seen = evaluated(after, declared, names, now, utc_offset_min=utc_offset_min)
    typed = before.typed
    values = _versions(was, seen, declared, typed, now)
    versions = dict(typed.values) | {v.name: _version(v) for v in values}
    latches = latched(typed.latches, seen.stage, declared.lifecycle.latches if declared.lifecycle else ())
    scans = _scans(declared, seen, typed)
    erase = _erased(declared, was, seen, after)
    kept = {name: mark for name, mark in {**typed.scans, **scans}.items() if name not in erase}
    typed = replace(typed, values=versions, latches=latches, scans=kept)
    timers, writes = _timers(declared, after, seen, typed)
    return Planned(replace(typed, timers=timers), values, writes, scans, erase)


@dataclass(frozen=True, slots=True)
class SharedPlanned:
    typed: Typed
    values: list[NewValue] = field(default_factory=list)
    timers: list[TimerWrite] = field(default_factory=list)
    changed: tuple[str, ...] = ()
    """The fields named as inputs whose value is new since the last pass, in order."""

    @property
    def revised(self) -> list[NewValue]:
        return [v for v in self.values if v.supersedes is not None]


def shared_plan(
    obj: TypedObject,
    seen: Evaluated,
    declared: ObjectType,
    digests: Mapping[str, str],
    inputs: Iterable[str],
    now: datetime,
) -> SharedPlanned:
    """The typed pass of a shared object (24.3.12): no event and no earlier state, only what the last pass
    kept (`obj.typed`). `seen` is the object evaluated now, and `digests` the space's keyed digest of the
    known value of each computed value and each field in `inputs`. A computed value gets a new version when
    its value or its rule changed, so the same value pushed again is no revision; a field in `inputs` changed
    when its digest did, or when no pass saw it before, since the objects derived from it may not have."""
    typed = obj.typed
    values: list[NewValue] = []
    for name, rule in value_names(declared).items():
        current = seen.resolved.get(name)
        digest = digests.get(name)
        if current is None or current.chosen is None or current.logic is Logic.KNOWN_DEFECT or digest is None:
            continue
        meta = typed.values.get(name)
        if meta is not None and (meta.digest, meta.rule) == (digest, rule):
            continue
        spec = declared.values.get(name)
        inputs_hash = _inputs_hash(tuple(spec.inputs) if spec else (), seen)
        values.append(_new_value(name, rule, current, spec, inputs_hash, meta, now, digest))
    versions = dict(typed.values) | {v.name: _version(v) for v in values}
    latches = latched(typed.latches, seen.stage, declared.lifecycle.latches if declared.lifecycle else ())
    kept = dict(typed.inputs)
    changed: list[str] = []
    for name in sorted(set(inputs)):
        digest = digests.get(name)
        if digest is not None and kept.get(name) != digest:
            changed.append(name)
            kept[name] = digest
    typed = replace(typed, values=versions, latches=latches, inputs=kept)
    timers, writes = _timers(declared, obj, seen, typed)
    return SharedPlanned(replace(typed, timers=timers), values, writes, tuple(changed))


def _erased(declared: ObjectType, was: Evaluated, seen: Evaluated, after: TypedObject) -> frozenset[str]:
    """Entering a sealing state takes the fields its transition names; what else it names is derived memory
    outside the object."""
    stage, before = seen.stage, was.stage
    if stage is None or (before is not None and before.state == stage.state):
        return frozenset()
    transitions = declared.lifecycle.transitions if declared.lifecycle else []
    named = {name for t in transitions if t.to == stage.state for name in t.erase_derived}
    return frozenset(named & set(after.fields))


def _scans(declared: ObjectType, seen: Evaluated, typed: Typed) -> dict[str, ScanMark]:
    """The verdict on each content field whose content changed (L27): the rules on the text Niadra holds;
    content it holds only as a pointer or a digest waits for a person when its type asks for a scan."""
    content = declared.content
    if content is None or not content.fields:
        return {}
    marks: dict[str, ScanMark] = {}
    required = content.scan == "required"
    for name in content.fields:
        found = seen.resolved.get(name)
        if found is None or found.value is None:
            continue
        text = found.value if isinstance(found.value, str) else orjson.dumps(found.value).decode()
        digest = hashlib.sha256(text.encode()).hexdigest()
        held = typed.scans.get(name)
        if held is not None and held.sha256 == digest:
            continue
        if content.scan == "off":
            result = ScanResult(ScanState.CLEAN)
        elif content.mode != "full":
            result = ScanResult(ScanState.PENDING if required else ScanState.CLEAN)
        else:
            result = scan(text, required=required)
        marks[name] = ScanMark(digest, result.state.value, result.reason)
    return marks


def value_names(declared: ObjectType) -> dict[str, str]:
    """Every computed value with its rule: the declared values and the time axes derived by a rule."""
    rules = {name: spec.rule for name, spec in declared.values.items()}
    for name, axis in declared.axes.items():
        if axis.origin == "derived" and axis.rule is not None:
            rules[name] = axis.rule
    return rules


def _versions(
    was: Evaluated, seen: Evaluated, declared: ObjectType, typed: Typed, now: datetime
) -> list[NewValue]:
    found: list[NewValue] = []
    for name, rule in value_names(declared).items():
        current = seen.resolved.get(name)
        if current is None or current.chosen is None or current.logic is Logic.KNOWN_DEFECT:
            continue
        previous = was.resolved.get(name)
        spec = declared.values.get(name)
        digest = _inputs_hash(tuple(spec.inputs) if spec else (), seen)
        meta = typed.values.get(name)
        moved = previous is None or previous.chosen is None or previous.chosen != current.chosen
        if meta is not None and not (
            moved
            and (
                current.value != (previous.value if previous else None)
                or rule != meta.rule
                or digest.hex() != meta.inputs_hash
            )
        ):
            continue
        found.append(_new_value(name, rule, current, spec, digest, meta, now))
    return found


def _new_value(
    name: str,
    rule: str,
    current: Resolved,
    spec: ValueSpec | None,
    inputs_hash: bytes,
    meta: ValueVersion | None,
    now: datetime,
    digest: str | None = None,
) -> NewValue:
    absent_as = spec.absent_as if spec else None
    named = current.value is None or current.value == absent_as
    assert current.chosen is not None
    at = current.chosen.observation.valid_at
    validity = spec.validity if spec else "until_input_or_rule_change"
    return NewValue(
        name=name,
        version=meta.version + 1 if meta else 1,
        value=None if named else current.value,
        absent_as=absent_as if named else None,
        rule=rule,
        inputs_hash=inputs_hash,
        inputs=tuple(spec.inputs) if spec else (),
        computed_at=at,
        valid_until=None
        if validity == "until_input_or_rule_change"
        else at + timedelta(milliseconds=duration_ms(validity)),
        declared_gaps=tuple(spec.declared_gaps) if spec else (),
        gap_effect=spec.gap_effect if spec else "unknown",
        supersedes=meta.version if meta else None,
        recorded_at=now,
        digest=digest,
    )


def _inputs_hash(inputs: Iterable[str], seen: Evaluated) -> bytes:
    """What a value was computed from, as a digest: each input's value now, and a versioned reference
    (`calendar@2026`) by its name."""
    body: dict[str, Any] = {}
    for name in inputs:
        found = seen.resolved.get(name)
        body[name] = name if "@" in name else (found.value if found and found.logic.known else None)
    return hashlib.sha256(orjson.dumps(body, option=orjson.OPT_SORT_KEYS, default=str)).digest()


def _version(value: NewValue) -> ValueVersion:
    return ValueVersion(
        value.version, value.rule, value.inputs_hash.hex(), value.recorded_at, value.supersedes, value.digest
    )


def _timers(
    declared: ObjectType, after: TypedObject, seen: Evaluated, typed: Typed
) -> tuple[dict[str, TimerMark], list[TimerWrite]]:
    """The timers after the write: each armed at the due time its rule gives now. A final outcome cancels
    what is still armed; so does a due time that is no longer known."""
    outcome = declared.lifecycle.outcome if declared.lifecycle else None
    stage = seen.stage
    final = stage is not None and (
        stage.state == EXPIRED_WITHOUT_OUTCOME or (outcome is not None and stage.state in outcome.final)
    )
    wanted: dict[str, tuple[datetime | None, int | None, str | None]] = {}
    for name, timer in declared.timers.items():
        armed_by = [typed.values[v].version for v in names_read(timer.due) if v in typed.values]
        once = f"{timer.once_per}:{armed_by[0]}" if timer.once_per and armed_by else timer.once_per
        wanted[name] = (due_of(timer.due, seen.env), max(armed_by) if armed_by else None, once)
    if outcome is not None and outcome.expired_without_outcome is not None:
        wanted[EXPIRED_WITHOUT_OUTCOME] = (outcome_deadline(declared, after, seen.env), None, None)
    marks = dict(typed.timers)
    writes: list[TimerWrite] = []
    for name, (due, version, once) in wanted.items():
        mark = marks.get(name)
        if final or due is None:
            if mark is not None and mark.state == ARMED:
                marks[name] = replace(mark, state=CANCELLED)
                writes.append(_write(name, marks[name], once))
            continue
        if mark is None or mark.state == CANCELLED:
            marks[name] = TimerMark(uuid7(), ARMED, due, version)
        elif mark.state == ARMED:
            if (mark.due_at, mark.value_version) == (due, version):
                continue
            marks[name] = replace(mark, due_at=due, value_version=version)
        elif version is not None and version != mark.value_version and due != mark.due_at:
            # A revised value moved a timer that already fired: it fires again, naming that firing (L26).
            marks[name] = TimerMark(uuid7(), ARMED, due, version, supersedes_firing_id=mark.timer_id)
        else:
            continue
        writes.append(_write(name, marks[name], once))
    return marks, writes


def _write(name: str, mark: TimerMark, once: str | None) -> TimerWrite:
    return TimerWrite(
        mark.timer_id,
        name,
        mark.state,
        mark.due_at,
        mark.value_version,
        once,
        mark.supersedes_firing_id,
        mark.fired_at,
    )


def names_read(text: str) -> set[str]:
    """The plain names an expression reads (fields, values, axes)."""
    found: set[str] = set()

    def walk(node: Node) -> None:
        match node:
            case Name(parts=parts) if len(parts) == 1:
                found.add(parts[0])
            case Member(target=target):
                walk(target)
            case Call(args=args) | ListLit(items=args) | BoolOp(operands=args):
                for arg in args:
                    walk(arg)
            case Not(operand=operand):
                walk(operand)
            case Compare(left=left, right=right) | Arith(left=left, right=right):
                walk(left)
                walk(right)

    walk(parse(text))
    return found
