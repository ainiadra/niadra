"""Checks a proposed webhook mapping against sample payloads, inside the cell (5.2, 22.4).

The report carries counts and event-type labels only; a label is a shared code such as
`invoice.voided` or a hash of the payload's top-level keys, never a customer value.
"""

import hashlib
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import jmespath
from jmespath.exceptions import JMESPathError

from niadra.domain.configure.masking import is_code

_TYPE_KEYS = ("type", "event", "event_type", "eventType", "topic", "kind", "action")


@dataclass(frozen=True, slots=True)
class RuleExpressions:
    match: str
    required: tuple[str, ...]
    """Expressions that must yield a value for a mapped event: idempotency key and time."""
    optional: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class MappingReport:
    samples: int
    mapped: int
    incomplete: int
    unmapped: int
    unmapped_types: tuple[tuple[str, int], ...]
    invalid_expressions: tuple[str, ...]

    def to_json(self) -> dict[str, Any]:
        return {
            "samples": self.samples,
            "mapped": self.mapped,
            "incomplete": self.incomplete,
            "unmapped": self.unmapped,
            "unmapped_types": [{"type": t, "count": n} for t, n in self.unmapped_types],
            "invalid_expressions": list(self.invalid_expressions),
        }


def check_mapping(rules: Sequence[RuleExpressions], payloads: Sequence[Mapping[str, Any]]) -> MappingReport:
    invalid = tuple(
        sorted({e for r in rules for e in (r.match, *r.required, *r.optional) if not _compiles(e)})
    )
    usable = [r for r in rules if r.match not in invalid and not set(r.required) & set(invalid)]
    mapped = incomplete = 0
    unmapped: Counter[str] = Counter()
    for payload in payloads:
        rule = next((r for r in usable if _truthy(r.match, payload)), None)
        if rule is None:
            unmapped[event_type_label(payload)] += 1
        elif all(_value(e, payload) not in (None, "") for e in rule.required):
            mapped += 1
        else:
            incomplete += 1
    return MappingReport(
        samples=len(payloads),
        mapped=mapped,
        incomplete=incomplete,
        unmapped=sum(unmapped.values()),
        unmapped_types=tuple(sorted(unmapped.items(), key=lambda kv: (-kv[1], kv[0]))),
        invalid_expressions=invalid,
    )


def event_type_label(payload: Mapping[str, Any]) -> str:
    for key in _TYPE_KEYS:
        value = payload.get(key)
        if isinstance(value, str) and is_code(value):
            return value
    shape = ",".join(sorted(str(k) for k in payload if is_code(str(k))))
    return "shape:" + hashlib.sha256(shape.encode()).hexdigest()[:8]


def _compiles(expression: str) -> bool:
    try:
        jmespath.compile(expression)
    except JMESPathError:
        return False
    return True


def _value(expression: str, payload: Mapping[str, Any]) -> Any:
    try:
        return jmespath.search(expression, payload)
    except JMESPathError:
        return None


def _truthy(expression: str, payload: Mapping[str, Any]) -> bool:
    return bool(_value(expression, payload))
