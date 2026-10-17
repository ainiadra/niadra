"""Where a person's identifiers can be written in the text of someone else's memory (5.14.0), and how an
erasure finds them there: by the keyed hashes of what each row and object writes (`domain.privacy.raw_index`),
kept beside it, never by reading the space.

A row of derived memory is indexed when it is sealed, by the store its table belongs to and the row its
ciphertext is bound to; an object (a turn recording) by its key. What an erasure finds of someone else keeps,
with the target's identifiers replaced by `ERASED`; what is under a legal hold is not rewritten, and the
erasure says how many such rows it left.
"""

from collections.abc import Hashable, Iterable
from dataclasses import dataclass, field
from enum import StrEnum
from uuid import UUID


class MentionStore(StrEnum):
    """A kind of row whose text the index of mentions covers, by the table its sealer writes."""

    FACT = "fact"
    EPISODE = "episode"
    OPEN_ITEM = "open_item"
    ATTR = "attr"
    SUMMARY = "summary"
    NOTE = "note"
    RUN = "run"
    SIGNALS = "signals"
    ACTION = "action"
    OBJECT = "object"
    VALUE = "value"
    SETTLED = "settled"
    PROPOSAL = "proposal"
    INTAKE = "intake"
    STATE = "state"
    COMMITMENT = "commitment"
    HANDOFF = "handoff"


INDEXED_TABLES: dict[str, MentionStore] = {
    "facts": MentionStore.FACT,
    "episodes": MentionStore.EPISODE,
    "open_items": MentionStore.OPEN_ITEM,
    "profile_attrs": MentionStore.ATTR,
    "tail_summaries": MentionStore.SUMMARY,
    "agent_notes": MentionStore.NOTE,
    "extraction_runs": MentionStore.RUN,
    "agent_actions": MentionStore.ACTION,
    "business_objects": MentionStore.OBJECT,
    "object_values": MentionStore.VALUE,
    "settled_values": MentionStore.SETTLED,
    "agent_note_proposals": MentionStore.PROPOSAL,
}
"""The tables whose sealed text is indexed as it is sealed, and the store each row goes under. A vector is
sealed in the same tables and carries no text: only text columns are read. A repository whose write has a
statement budget carries the entries inside that statement (`postgres.mentions.with_mentions`); what is left
goes to the index with the commit."""
TEXT_MENTIONS_READY = "text_mentions_ready"
"""The `space_state` key set once the rows written before the index existed are in it."""
MORE_MENTIONS_READY = "text_mentions_ready_2"
"""The same for the stores indexed since: agent actions, business objects, computed values, settled values
and note proposals."""
OBJECT_MENTIONS_READY = "object_mentions_ready"
"""The same for turn recordings."""
SIGNALS_MENTIONS_READY = "signals_mentions_ready"
"""The same for subjects' signals, sealed outside the sealer and indexed by their derivation."""
AGENT_STATE_MENTIONS_READY = "agent_state_mentions_ready"
"""The same for the agents' working states, sealed by their module and indexed as they are stored."""
COORDINATION_MENTIONS_READY = "coordination_mentions_ready"
"""The same for commitments' terms and handoffs' packages and outcomes."""
INTAKE_MENTIONS_READY = "intake_mentions_ready"
"""The same for the interactions waiting to be folded into signals, indexed as they are sealed."""
ARCHIVE_MENTIONS_READY = "archive_mentions_ready"
"""The same for the events of the cold archive, each indexed under its part (`archived_key`)."""
MENTION_PAGE = 500
"""Rows an erasure reads, rewrites or indexes per transaction."""


@dataclass(frozen=True, slots=True, order=True)
class MentionKey:
    """A row of one store: `row` is what its ciphertext is bound to (a fact's id, an attribute's handle,
    slot and source, a run's session and versions)."""

    store: str
    row: str


@dataclass(slots=True)
class Redacted:
    """What one module's pass over the rows of others that name the target did: rows rewritten with the
    target redacted out, rows left as they were because a legal hold keeps them, and whose memory changed
    (the handles and profiles that own the rewritten rows), so their packs, vectors and summaries are made
    again."""

    rows: int = 0
    held: int = 0
    handles: set[UUID] = field(default_factory=set)
    profiles: set[UUID] = field(default_factory=set)
    in_source: int = 0
    """Of `rows`, the business objects: each mirrors a record the company's system keeps, which holds its own
    copy of what was redacted here, for the company to erase there."""

    def add(self, other: "Redacted") -> None:
        self.rows += other.rows
        self.held += other.held
        self.in_source += other.in_source
        self.handles |= other.handles
        self.profiles |= other.profiles


def archived_key(part_key: str, event_id: object) -> str:
    """An archived event's entry in the index of objects: the Parquet part it is in, and its id."""
    return f"{part_key}#{event_id}"


def archived_at(key: str) -> tuple[str, str]:
    """The part and the event id of an `archived_key`."""
    part, _, event_id = key.rpartition("#")
    return part, event_id


def signals_key(handle_id: UUID, beneficiary_id: UUID) -> MentionKey:
    """A subject's signals body for one beneficiary, as `subject_signals` keys it."""
    return MentionKey(MentionStore.SIGNALS, f"{handle_id}:{beneficiary_id}")


def by_key[K: Hashable](entries: Iterable[tuple[K, frozenset[bytes]]]) -> list[tuple[K, frozenset[bytes]]]:
    """One entry per row or object, its tokens the union of every entry the caller gave it, in the order the
    keys first came. A caller may give one row several entries (a page of an episode's versions, several
    columns of one row); a database write takes each key once, so the index writers go through this."""
    merged: dict[K, frozenset[bytes]] = {}
    for key, tokens in entries:
        merged[key] = merged.get(key, frozenset()) | tokens
    return list(merged.items())
