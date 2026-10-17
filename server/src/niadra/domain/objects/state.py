"""Derived object state and action reconciliation, deterministic and without an LLM (5.8, 18.4).

The system of record wins: object state comes only from system events, merged field by field by
`occurred_at` with the source version as tie-breaker (24.3.1), and an agent's action is only a
declaration until the system emits the matching event.
"""

import re
from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any
from uuid import UUID

from niadra.domain.objects.operations import (
    canonical_operation,
    operation_of_event_type,
    trusts,
)
from niadra.domain.records import AgentAction, EventRecord, HandleRecord, ObjectKey
from niadra.domain.state.observed import Report
from niadra.domain.vocabulary import ActionStatus, HandleType

# How long an action waits for its system event before it turns `divergent` (18.4). The same window
# pairs an action with a system event on the same object and operation.
CONFIRMATION_WINDOW = timedelta(hours=6)
COORDINATION_NAMESPACE = "coordination"
"""The namespace of the objects coordination's own actions name, a commitment and a handoff: what an agent
promised and what the side that took a handoff decided are records, not effects on a system of record, so no
system event ever confirms them and they stay `declared` past the window (18.4). Only coordination names
objects in it."""

_NUMBER = re.compile(r"[-+]?\d+(?:[.,]\d+)?")


def operation_of(event: EventRecord) -> str | None:
    """The canonical operation of an action or a system event: `credit` for `invoice.credited`."""
    if event.action and event.action.get("operation"):
        return canonical_operation(str(event.action["operation"]))
    explicit = event.fields.get("operation")
    if isinstance(explicit, str) and explicit:
        return canonical_operation(explicit)
    return operation_of_event_type(event.canonical_type)


def object_of_action(event: EventRecord) -> ObjectKey | None:
    """The object an action acts on: `action.object` when given, else its first object reference."""
    ref = (event.action or {}).get("object")
    if isinstance(ref, Mapping) and {"type", "namespace", "id"} <= ref.keys():
        return ObjectKey(str(ref["type"]), str(ref["namespace"]), str(ref["id"]))
    return event.object_refs[0] if event.object_refs else None


SYSTEM_PREFIX = "src:"
"""An object's fields name the source of a system event or a push by its key's source: `src:<source id>`."""
TOOL_PREFIX = "tool:"
"""And the source of what a tool's result showed by the tool: `tool:<tool name>`."""
CLOCK_SOURCE = "clock"
"""And what a timer changed (a transition the clock makes, an outcome that expired) by the clock."""


BOOKKEEPING = frozenset(
    {"last_event_type", "source_version", "version", "record_ref", "input_refs", "operation"}
)
"""What a system event carries about itself, `operation` (what it confirms, 5.8) included: never drift, and
served only when the type declares it as a field."""
LIFECYCLE = "state"
"""A reported field of this name is the lifecycle state, which no declaration may name as a field."""
EVENT_TYPE = "last_event_type"
"""The bookkeeping field that holds the type of the last system event merged into the object."""
_ONLY_CHANGED = frozenset({"created", "updated", "changed", "synced"})
"""Event verbs that say only that the object changed: its fields say how."""


def last_recorded(obj_type: str, state: Mapping[str, Any]) -> str | None:
    """What the system of record last recorded about an object, in words and without the object's own type:
    "fee.refunded" on a dispute is "fee refunded", "claim.reimbursed" on the claim is "reimbursed"; a mere
    change ("legal_case.updated") says nothing. Left out of the line with the rest of the event's bookkeeping,
    a refund the ERP issued read as an amount and a receipt only, and the agent could not say the refund went
    through (benchmark promise_action, 81% on 30/09 to 20% on 09/10/2026)."""
    raw = state.get(EVENT_TYPE)
    if not isinstance(raw, str) or not raw:
        return None
    rest = raw.removeprefix(f"{obj_type}.")
    words = " ".join(rest.replace(".", " ").replace("_", " ").replace("-", " ").split())
    return words if words and words not in _ONLY_CHANGED else None


def system_source(source_id: UUID) -> str:
    return f"{SYSTEM_PREFIX}{source_id}"


def report_of(event: EventRecord) -> Report:
    """A system event as a report of the object's fields: its time in the world orders it (5.8)."""
    fields = {**event.fields, "last_event_type": event.canonical_type}
    return Report(
        system_source(event.source_id),
        fields,
        valid_at=event.occurred_at,
        observed_at=event.occurred_at,
        received_at=event.received_at,
        version=state_version_of(event),
    )


def state_version_of(event: EventRecord) -> str | None:
    version = event.fields.get("source_version", event.fields.get("version"))
    return None if version is None else str(version)


def record_ref_of(event: EventRecord) -> str | None:
    ref = event.fields.get("record_ref")
    return None if ref is None else str(ref)


def choose_owner(handles: Sequence[HandleRecord]) -> HandleRecord | None:
    """The handle an object is pinned to (L6): an organization before a person (18.2, rev. 9), and the
    customer id in that system (`system_id`) before any other handle of the same subject."""
    if not handles:
        return None
    ranked = sorted(
        enumerate(handles),
        key=lambda ih: (
            not ih[1].subject_kind.is_organization,
            ih[1].type is not HandleType.SYSTEM_ID,
            ih[0],
        ),
    )
    return ranked[0][1]


def within_window(a: datetime, b: datetime, window: timedelta = CONFIRMATION_WINDOW) -> bool:
    return abs(a - b) <= window


def reconciled_status(action_result: str | None, event: EventRecord) -> ActionStatus:
    """Confirmed unless both sides state a result and they disagree; then the system of record wins
    and the action is `divergent` (5.8, 14 item 11)."""
    reported = event.fields.get("result", event.fields.get("amount"))
    if action_result is None or reported is None:
        return ActionStatus.CONFIRMED
    return ActionStatus.CONFIRMED if _same_result(action_result, str(reported)) else ActionStatus.DIVERGENT


def _same_result(a: str, b: str) -> bool:
    na, nb = _number(a), _number(b)
    if na is not None and nb is not None:
        return na == nb
    return a.strip().casefold() == b.strip().casefold()


def _number(text: str) -> Decimal | None:
    found = _NUMBER.search(text)
    if not found:
        return None
    try:
        return Decimal(found.group().replace(",", "."))
    except InvalidOperation:
        return None


def flagged_for_injection(content_meta: Mapping[str, Any] | None) -> bool:
    """The gate marks a session's content as injection (5.6); its actions are quarantined (18.4)."""
    if not content_meta:
        return False
    return bool(content_meta.get("injection")) or content_meta.get("guard") == "injection"


def closes_items(status: ActionStatus, operation: str, trusted_ops: frozenset[str]) -> bool:
    """A declared action only closes open items when its source trusts that operation (18.4, `trusts`): a
    source with no list trusts every one, and the closing's own gates decide."""
    if status is ActionStatus.CONFIRMED:
        return True
    return status is ActionStatus.DECLARED and trusts(trusted_ops, operation)


def current_actions(actions: Iterable[AgentAction]) -> list[AgentAction]:
    """Actions are never rewritten (18.4): a later action that names another in `corrects_action_id`
    takes its place, and the corrected one leaves the context and the search while staying on record.
    Only a source corrects its own actions: another agent (another vendor's) naming one changes nothing."""
    actions = list(actions)
    source_of = {a.action_id: a.source_id for a in actions}
    corrected = {
        a.corrects_action_id
        for a in actions
        if a.corrects_action_id is not None and source_of.get(a.corrects_action_id) == a.source_id
    }
    return [a for a in actions if a.action_id not in corrected]
