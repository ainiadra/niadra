"""Undoing a retraction or a correction (ai-memory memo 12, WP-G): replace, never delete, works backwards.

A retracted fact keeps its row, its value and its evidence, so the security role can bring it back when
the retraction was a mistake, without extracting the session again. The restored row is the same row: the
facts table keeps one row per handle, predicate and value. What stops a restore:

- the fact is not retracted (an active one needs nothing, an expired one was replaced by newer evidence,
  a quarantined one has its own review);
- the fact came through an unconfirmed link (5.3): a claimed e-mail never writes to the confirmed memory;
- the customer's evidence is gone: an erasure took every event that stated it, and the only mentions left
  are the company's own feedback events. An erased fact never comes back;
- the fact is a correction that a restore of the fact it corrected undid: only the company ever stated it,
  and bringing it back would reverse that restore; correcting the fact again says it;
- the fact was merged into another by the consolidation: the value lives on in the row that kept it,
  with all of its evidence.
"""

from collections.abc import Collection, Mapping, Sequence
from dataclasses import replace
from datetime import datetime
from enum import StrEnum
from uuid import UUID

from niadra.domain.extract.result import MemoryRef
from niadra.domain.memory.facts import Relation, ref
from niadra.domain.records import Fact
from niadra.domain.vocabulary import FactStatus, RelationType

RESTORE_ACTION = "restore_fact"
CORRECT_ACTION = "correct_fact"


class Refusal(StrEnum):
    NOT_RETRACTED = "not_retracted"
    UNCONFIRMED_LINK = "unconfirmed_link"
    ERASED = "erased"
    UNDONE = "undone"
    MERGED = "merged"


REFUSAL_DETAIL = {
    Refusal.NOT_RETRACTED: "only a retracted fact can be restored",
    Refusal.UNCONFIRMED_LINK: "the fact came through an unconfirmed link; confirm the link first",
    Refusal.ERASED: "the evidence of the fact was erased; an erased fact is not restored",
    Refusal.UNDONE: (
        "a restore of the fact this correction replaced undid it; correct that fact again to state this value"
    ),
    Refusal.MERGED: "the fact was merged into another one, which holds its value",
}


def refusal(
    fact: Fact,
    *,
    provisional: Collection[UUID],
    feedback_event_ids: Collection[UUID],
    replaced_by: Sequence[Fact],
    undone_by: Collection[UUID] = (),
) -> Refusal | None:
    """Why `fact` cannot be restored, or `None`.

    `feedback_event_ids` are the fact's mentions that are feedback events; `replaced_by` the facts with a
    `SUPERSEDES` edge to it other than the corrections of it, which a restore undoes; `undone_by` the
    restores of another fact, which retract the corrections of that fact.
    """
    if fact.status is not FactStatus.RETRACTED:
        return Refusal.NOT_RETRACTED
    if fact.origin_handle_id in provisional:
        return Refusal.UNCONFIRMED_LINK
    said = {e for e in fact.evidence_event_ids if e not in feedback_event_ids}
    if not said:
        # A row only the company stated: a correction a restore retracted, or what an erasure left.
        undone = set(undone_by) & set(fact.evidence_event_ids)
        return Refusal.UNDONE if undone else Refusal.ERASED
    # The consolidation's merge moves the dropped row's evidence onto the row it keeps.
    if any(said <= set(other.evidence_event_ids) for other in replaced_by):
        return Refusal.MERGED
    return None


def replacing(fact: Fact, relations: Sequence[Relation]) -> list[MemoryRef]:
    """The facts with a `SUPERSEDES` edge to `fact`."""
    target = ref(fact)
    found = {r.a for r in relations if r.type is RelationType.SUPERSEDES and r.b == target}
    return sorted(found, key=lambda r: (str(r.origin_handle_id), str(r.id)))


def is_correction_of(fact: Fact, candidate: Fact, events: Mapping[UUID, tuple[str, str | None]]) -> bool:
    """Whether `candidate` is the value a correction of `fact` wrote: its evidence holds the
    `feedback.correct_fact` event that named `fact`. `events` maps an event id to its action and the fact
    id it named."""
    return any(events.get(e) == (CORRECT_ACTION, str(fact.fact_id)) for e in candidate.evidence_event_ids)


def restored(fact: Fact, at: datetime) -> Fact:
    """The fact active again, as it was: value, validity, evidence and confidence unchanged. The company
    states it now, so it was last confirmed now, which ranks it above the correction it undoes (5.8)."""
    return replace(
        fact,
        status=FactStatus.ACTIVE,
        invalid_at=None,
        expired_at=None,
        last_confirmed_at=max(at, fact.last_confirmed_at),
    )
