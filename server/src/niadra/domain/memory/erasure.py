"""The evidence rule of the erasure cascade (8.4, L17), shared by facts and traits.

When events are erased, the evidence rows that point at them go, and so does every item left
without evidence. The plan works in batches of about `limit` rows and is idempotent: rows it
erased are not seen again, so a later batch continues where this one stopped.

Only what the customer said keeps a fact alive. The company's own feedback (a retraction, a correction,
a restore) also leaves a mention on the rows it acts on, for the lineage (L1, L12), but it never counts
as evidence: once every event that stated a value is erased, the row goes with the feedback's mentions,
and so does every row that only the feedback of an erased row holds (a correction of it).
"""

from collections.abc import Callable, Collection, Mapping
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class EvidencePlan[K, E]:
    items: tuple[K, ...] = ()
    """Items left without evidence: they go with all their evidence rows."""
    evidence: tuple[tuple[K, E], ...] = ()
    """Evidence rows to erase, including those of the items above."""

    @property
    def rows(self) -> int:
        return len(self.items) + len(self.evidence)


def _always(_: object) -> bool:
    return True


def plan_evidence_erasure[K, E](
    evidence: Mapping[K, Collection[E]],
    erased: Callable[[E], bool],
    limit: int,
    supports: Callable[[E], bool] = _always,
) -> EvidencePlan[K, E]:
    """`evidence` holds every evidence row of the items the erased events touch.

    `supports` says whether a row can keep its item alive; an item whose supporting rows are all erased
    goes with every row it has, the unsupporting ones (the company's feedback) included.

    The cascade reads a batch shorter than `limit` as "nothing left", so the plan fills the batch
    whenever work remains, taking part of an item's erased evidence when the whole item does not fit.
    An item left without evidence must go together with its last erased row (afterwards nothing would
    lead back to it), so a batch may run over `limit` by the item and its unsupporting rows.
    """
    items: list[K] = []
    rows: list[tuple[K, E]] = []
    for item in sorted(evidence, key=str):
        own = sorted(evidence[item], key=str)
        doomed = [e for e in own if erased(e)]
        if not doomed:
            continue
        room = limit - len(items) - len(rows)
        if room <= 0:
            break
        orphaned = not any(supports(e) and not erased(e) for e in own)
        if not orphaned or len(doomed) > room:
            # Part of an orphan's erased rows at most: one stays, so the next batch still finds the item.
            rows.extend((item, e) for e in doomed[:room])
            continue
        rows.extend((item, e) for e in own)
        items.append(item)
    return EvidencePlan(tuple(items), tuple(rows))


def held_by_feedback_only[K, E](
    evidence: Mapping[K, Collection[E]], feedback: Callable[[E], bool]
) -> list[K]:
    """The items among `evidence` that only the company's feedback holds: every row they have is feedback.

    The caller passes the rows named by the feedback of rows the erasure just took, so what this returns
    goes too: a correction written for an erased fact states nothing the customer still has on record.
    """
    return sorted((k for k, own in evidence.items() if own and all(feedback(e) for e in own)), key=str)
