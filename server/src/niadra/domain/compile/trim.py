"""A leaner pack for each agent from what it actually uses (15.8, 19.2).

Context-use measurement counts, per source and view, how many deliveries carried each item unit (an item
kind and its category, like `fact:contract` or `episode:technical_visit`) and in how many of them the
agent used one. A unit that source never used across enough deliveries becomes its trim, and the compiler
leaves the unit's items out of that source's packs. Some items never leave, whatever the measurement says:

- open items and promises, the customer line, patterns and summaries: only the kinds whose use the
  measurement can see (a fact by its predicate, an episode as the reason of contact, an action or an
  object by the object it is about) can be trimmed;
- what another agent did that the reading agent must never deny or redo (`kept_actions`);
- sensitive or restricted items;
- items the policy releases to that source by naming its purpose.
"""

from datetime import datetime, timedelta
from uuid import UUID

from niadra.domain.compile.model import Candidate, ItemKind, MemorySnapshot
from niadra.domain.policy import PolicySpec, Reader, item_sensitivity, released_by_name
from niadra.domain.vocabulary import HANDOFF_OUTCOME, ActionStatus, Sensitivity

TRIMMABLE_KINDS = frozenset({ItemKind.FACT, ItemKind.EPISODE, ItemKind.ACTION, ItemKind.OBJECT})
MAX_TRIM_UNITS = 16
"""A pack key carries its trim; more units than this and the least delivered stay in."""
RECENT_ACTIONS = timedelta(hours=72)
"""How recent another agent's action is (11, 3.3): a skeleton pins those of the last 72 hours before `as_of`,
and no trim leaves them out. Counted from the latest thing the memory holds, never the wall clock, so the same
memory keeps the same lines."""


def unit_of(kind: str, category: str | None) -> str:
    """The unit an item is measured and trimmed by. Pack keys use `|` and `+` as separators."""
    unit = f"{kind}:{category}" if category else kind
    return unit.replace("|", "_").replace("+", "_")


def trimmable_kind(unit: str) -> bool:
    return unit.partition(":")[0] in TRIMMABLE_KINDS


def whole_kind(kind: str) -> str:
    """The unit of every item of a kind: what an experiment that holds a pack's section out trims."""
    return f"{kind}:*"


def kept_actions(
    subject: MemorySnapshot, as_of: datetime | None, reader_source: UUID | None
) -> frozenset[str]:
    """The actions no trim leaves out, whatever the measurement says (15.8): one agent must know what another
    did or is doing, or it denies or redoes it. Older actions, and the reader's own once no longer
    pending, stay trimmable.

    - another source's action of the last `RECENT_ACTIONS`;
    - a handoff's outcome, whoever reported it and however old: what the receiving side settled;
    - an action whose effect is still pending: declared and not yet confirmed by the system of record
      (18.4), or on an object an open item still waits on."""
    since = as_of - RECENT_ACTIONS if as_of is not None else None
    waited = {i.object for i in subject.open_items if i.object is not None}
    waited_on = {o.object_id for o in subject.objects if o.key in waited}
    return frozenset(
        f"action:{a.action_id}"
        for a in subject.actions
        if (since is not None and a.occurred_at >= since and a.source_id != reader_source)
        or a.operation == HANDOFF_OUTCOME
        or a.status is ActionStatus.DECLARED
        or (a.object_id is not None and a.object_id in waited_on)
    )


def trims(
    candidate: Candidate, trim: frozenset[str], policy: PolicySpec, reader: Reader, kept: frozenset[str]
) -> bool:
    """Whether the compiler leaves this candidate out of a pack compiled with `trim`; `kept` are the
    `kept_actions` of the pack's memory."""
    unit = unit_of(candidate.kind, candidate.attrs.category)
    if candidate.kind not in TRIMMABLE_KINDS or (unit not in trim and whole_kind(candidate.kind) not in trim):
        return False
    if candidate.item_id in kept:
        return False
    if item_sensitivity(policy, candidate.attrs) is not Sensitivity.NORMAL:
        return False
    return not released_by_name(policy, reader, candidate.attrs)
