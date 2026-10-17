"""Data issues (24.3.5, 24.6.6): a problem in the company's data returned to its owner, with no personal data.

An issue is one open row per fingerprint, the class with the type, field and source it is about: finding the
same problem again counts one more occurrence and adds its object references, up to 50, instead of opening
another. Acknowledging closes the row; the next occurrence opens a new one. Pure.
"""

import hashlib
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal, get_args
from uuid import UUID

DataIssueKind = Literal[
    "null_field",
    "out_of_vocabulary",
    "stale_source",
    "coverage_drop",
    "invalid_value",
    "divergence",
    "drift",
    "rule_conflict",
    "type_undeclared",
    "field_held",
    "budget_reached",
    "row_failed",
    "inference_limit",
]
"""`type_undeclared`: tools showed objects of a type the space never declared, so they never became state.
`drift`: the schema a declared type mirrors changed, or its data moved in a way the type does not declare; a
new field is served to agents as it came, so the issue is a note, nothing to fix. `field_held`: a new field
that may be personal data or free text, held from agents until the type declares it
(`domain.objects.undeclared`). `budget_reached`: the space spent its day's AI ceiling for a purpose
(`source`), and memory waits for the next UTC day; counted once a day while it lasts. `row_failed`: a row of
the company's data failed every attempt to be taken in; it is kept aside, sealed, never deleted, and an admin
retries it (`POST /v1/intake/failed/retry`); its `source` names where it came from. `inference_limit`: what
the space's systems send passed a limit of what Niadra infers on its own (types, fields or states of a type,
operations), or a name was not safe to use; what passed it is still stored and served as it came, and `field`
names the limit."""
DATA_ISSUE_KINDS: frozenset[str] = frozenset(get_args(DataIssueKind))
MAX_REFS = 50
"""Object references kept per issue: the first ones seen, enough for the owner to find the rows."""
MAX_NAME = 256
_REF = re.compile(r"^[a-z][a-z0-9_]{0,39}:[^:\s]{1,256}:\S{1,512}$")


@dataclass(frozen=True, slots=True)
class NewDataIssue:
    """An occurrence to open or count. `issue_id` is the id a new row takes; an open row keeps its own."""

    issue_id: UUID
    kind: str
    fingerprint: bytes
    object_type: str | None
    field: str | None
    source: str | None
    refs: tuple[str, ...]
    at: datetime


@dataclass(frozen=True, slots=True)
class DataIssueRow:
    issue_id: UUID
    kind: str
    object_type: str | None
    field: str | None
    source: str | None
    refs: tuple[str, ...]
    occurrences: int
    opened_at: datetime
    last_seen_at: datetime
    acknowledged_at: datetime | None = None
    acknowledged_by: str | None = None


@dataclass(frozen=True, slots=True)
class IssueQuery:
    """A page of issues, newest opened first, keyset paged by (opened_at, issue_id)."""

    open: bool | None = True
    """True for the open ones, False for the acknowledged ones, None for both."""
    kind: str | None = None
    before: tuple[datetime, UUID] | None = None
    limit: int = 50


def fingerprint(kind: str, object_type: str | None, field: str | None, source: str | None) -> bytes:
    """The same class, type, field and source is the same issue; unit separators keep the parts apart."""
    parts = (kind, object_type or "", field or "", source or "")
    return hashlib.sha256("\x1f".join(parts).encode()).digest()


def new_issue(
    issue_id: UUID,
    kind: str,
    at: datetime,
    *,
    object_type: str | None,
    field: str | None,
    source: str | None,
    refs: Iterable[str],
) -> NewDataIssue:
    if kind not in DATA_ISSUE_KINDS:
        raise ValueError(f"unknown data issue kind {kind!r}")
    names = (object_type, field, source)
    if any(name is not None and not 0 < len(name) <= MAX_NAME for name in names):
        raise ValueError("a data issue's type, field and source are 1 to 256 characters")
    return NewDataIssue(
        issue_id=issue_id,
        kind=kind,
        fingerprint=fingerprint(kind, object_type, field, source),
        object_type=object_type,
        field=field,
        source=source,
        refs=merged_refs((), refs),
        at=at,
    )


def merged_refs(kept: Sequence[str], seen: Iterable[str]) -> tuple[str, ...]:
    """The references kept, then the new ones not kept yet, up to `MAX_REFS`; a string that is not a
    `type:namespace:id` reference is left out."""
    merged = list(dict.fromkeys(kept))
    for ref in seen:
        if len(merged) >= MAX_REFS:
            break
        if ref not in merged and _REF.match(ref):
            merged.append(ref)
    return tuple(merged[:MAX_REFS])


DATA_ASSERTIONS = frozenset(
    {"claims_match_state", "claims_traced", "no_denial_with_results", "expected_in_topk", "hard_respected"}
)
"""The assertion kinds that read what the tools showed: when one fails, the data it read may be the cause."""
LIFECYCLE_FIELD = "state"
DefectKey = tuple[DataIssueKind, str, str, str | None]
"""(kind, type, field, tool)."""


@dataclass(frozen=True, slots=True)
class TypeRules:
    """What a declared type says about its values, for attributing a failure: each declared field's maximum
    age in milliseconds (None when it never goes stale), and the lifecycle states with every code a source's
    vocabulary maps to one (empty when the type declares none)."""

    fields: Mapping[str, int | None]
    states: frozenset[str] = frozenset()


def data_defects(
    frame: Mapping[str, Any], rules: Mapping[str, TypeRules]
) -> dict[DefectKey, tuple[str, ...]]:
    """What was wrong with the data a recorded turn's tools showed (24.6.6): a declared field that came back
    null, a lifecycle state no vocabulary knows, a field older at the source than its type allows when the
    turn ran. Keyed by (kind, type, field, tool), with the objects it was seen on. Types the space did not
    declare are left out: they are another issue, raised where observations become state."""
    started = datetime.fromisoformat(frame["started_at"])
    found: dict[DefectKey, list[str]] = {}
    for call in frame.get("calls") or ():
        if call.get("kind") != "tool":
            continue
        for observation in call.get("observations") or ():
            ref = str(observation["ref"])
            rule = rules.get(ref.split(":", 1)[0])
            if rule is None:
                continue
            provenance = observation.get("provenance") or {}
            seen_at = provenance.get("source_observed_at")
            age_ms = (started - datetime.fromisoformat(seen_at)).total_seconds() * 1000 if seen_at else None
            for name, value in (observation.get("fields") or {}).items():
                kind = _defect(name, value, rule, age_ms)
                if kind is not None:
                    key = (kind, ref.split(":", 1)[0], name, call.get("name"))
                    found.setdefault(key, []).append(ref)
    return {key: merged_refs((), refs) for key, refs in found.items()}


def _defect(name: str, value: Any, rule: TypeRules, age_ms: float | None) -> DataIssueKind | None:
    if name == LIFECYCLE_FIELD and rule.states and value is not None and str(value) not in rule.states:
        return "out_of_vocabulary"
    if name not in rule.fields:
        return None
    if value is None:
        return "null_field"
    max_age = rule.fields[name]
    if max_age is not None and age_ms is not None and age_ms > max_age:
        return "stale_source"
    return None
