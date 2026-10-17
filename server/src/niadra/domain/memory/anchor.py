"""Where a memory item lives: the anchor rule (5.8, rev. 10), applied through identity's `choose_anchor`.

A fact belongs to the speaker's handle present in its evidence with the highest verification;
without one, to a handle of the conversation; ties go to the smallest handle id. Facts from system
events belong to the subject's `system_id`. The rule decides where a fact goes on an unmerge, so
it never changes.
"""

from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from uuid import UUID

from niadra.domain.identity.rules import choose_anchor
from niadra.domain.records import EventRecord, HandleRecord
from niadra.domain.vocabulary import EventKind


@dataclass(frozen=True, slots=True)
class Anchor:
    origin_handle_id: UUID
    mention_handle_ids: tuple[UUID, ...]


def anchor(
    evidence: Sequence[EventRecord],
    handles: Mapping[UUID, HandleRecord],
    conversation_handles: Collection[UUID],
) -> Anchor:
    """`handles` are the profile's; `conversation_handles` are its handles seen anywhere in the session."""
    present = {h for e in evidence for h in e.handle_ids if h in handles}
    system_event = any(e.kind is not EventKind.MESSAGE for e in evidence)
    pool = present or {h for h in conversation_handles if h in handles} or set(handles)
    origin = choose_anchor((handles[h] for h in pool), system_event=system_event and bool(present)).handle_id
    others = {*conversation_handles, *present} - {origin}
    return Anchor(origin, tuple(sorted(h for h in others if h in handles)))
