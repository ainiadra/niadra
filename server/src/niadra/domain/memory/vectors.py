"""Embeddings are stored in half precision and sealed like any content (6, `facts`).

Every navigable item stores the vector history search compares a question with (16.2): the write side
encodes items when they are created or change, a backfill fills the ones it missed, and a search encodes
only the question.
"""

import struct
from dataclasses import dataclass
from uuid import UUID

from niadra.domain.compile.model import ItemKind
from niadra.domain.records import AgentAction, BusinessObject, Episode, Fact, OpenItem, Trait

type Embeddable = Fact | Episode | OpenItem | AgentAction | BusinessObject | Trait

# A structured fact never gets a vector (15.7); dimension 0 records that decision, so the backfill does
# not pick the fact up again on every pass.
NO_VECTOR_DIM = 0


@dataclass(frozen=True, slots=True)
class ItemVector:
    kind: ItemKind
    item_id: UUID
    vector: list[float]
    model: str

    @property
    def doc_id(self) -> str:
        """The history navigation's id of the item, `<kind>:<uuid>`."""
        return f"{self.kind.value}:{self.item_id}"


@dataclass(frozen=True, slots=True)
class PendingVector:
    """An item without a vector of the wanted encoder. `fence` is the row's change sequence when it was
    read, for kinds whose text changes in place (objects, patterns, open items): the vector is stored only
    if the row is still in that state, so a vector of an older text never lands on a newer row."""

    record: Embeddable
    fence: int | None = None

    @property
    def kind(self) -> ItemKind:
        return kind_of(self.record)

    @property
    def item_id(self) -> UUID:
        return item_id_of(self.record)


def kind_of(record: Embeddable) -> ItemKind:
    match record:
        case Fact():
            return ItemKind.FACT
        case Episode():
            return ItemKind.EPISODE
        case OpenItem():
            return ItemKind.OPEN_ITEM
        case AgentAction():
            return ItemKind.ACTION
        case BusinessObject():
            return ItemKind.OBJECT
        case Trait():
            return ItemKind.TRAIT


def item_id_of(record: Embeddable) -> UUID:
    match record:
        case Fact():
            return record.fact_id
        case Episode():
            return record.episode_id
        case OpenItem():
            return record.item_id
        case AgentAction():
            return record.action_id
        case BusinessObject():
            return record.object_id
        case Trait():
            return record.trait_id


def pack_half(vector: list[float]) -> bytes:
    return struct.pack(f"<{len(vector)}e", *vector)


def unpack_half(data: bytes) -> list[float]:
    return list(struct.unpack(f"<{len(data) // 2}e", data))
