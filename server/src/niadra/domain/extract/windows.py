"""Every turn read whole: a batch whose turns pass what one extraction call reads is read in ordered windows,
each one call, and their answers are put back together per session. Memory never cuts what it was told: the
old reading kept the head and the tail of a long e-mail and dropped its middle, and a session kept only its
last 400 turns.

A window holds whole turns, in the batch's order, up to `TURN_CHARS_BUDGET` characters and `WINDOW_TURNS`
turns. A turn longer than a window is read in parts of at most `PART_CHARS`, each part overlapping the one
before by `PART_OVERLAP` characters and marked `[part k/n]` (the prompt tells the model what the mark means).
Consecutive windows share their last few whole turns (`OVERLAP_CHARS`, `OVERLAP_TURNS`), so a promise said
across the cut is read whole in one of them; what both windows extracted is the same entry twice, and the
merge keeps it once (`merged`), as memory's single id and restatement rules would anyway. Pure.
"""

import dataclasses
from collections.abc import Sequence
from typing import Any

from niadra.domain.common.text import pieces as split
from niadra.domain.extract.prompt import TURN_CHARS_BUDGET, BatchMaterial, SessionMaterial, turns
from niadra.domain.extract.result import EpisodeDraft, ExtractionResult
from niadra.domain.extract.validate import FALLBACK_CATEGORY
from niadra.domain.records import EventRecord

WINDOW_TURNS = 400
"""Turns one call reads at most: a session of thousands of short turns is read in several calls."""
PART_CHARS = 30_000
"""The longest part of one long turn. Measured on 08/10/2026 with the extraction model: an instruction at the
start of a 30,000-character e-mail was kept in every call, at the start of a 60,000-character part in none of
three. A window never holds two parts of the same turn: each part is read on its own, with the whole turns
around it, and its PII spans stay its own (`redactable` keys a turn by its event)."""
PART_OVERLAP = 2_000
"""What each part of a long turn repeats of the one before: a sentence cut by a part's end is read whole."""
OVERLAP_CHARS = 4_000
OVERLAP_TURNS = 4
"""The whole turns a window carries over from the end of the one before."""
PART_MARK = "[part {k}/{n}]"


@dataclasses.dataclass(frozen=True, slots=True)
class _Unit:
    """One turn, or one part of a long turn, in the batch's order."""

    session: int
    event: EventRecord
    part: bool = False

    @property
    def size(self) -> int:
        return len(self.event.text or "")


def windows(
    batch: BatchMaterial, budget: int = TURN_CHARS_BUDGET, most: int = WINDOW_TURNS
) -> list[BatchMaterial]:
    """The batch as the calls read it: itself when its turns fit one call and none is longer than a part,
    else ordered windows that together read every turn whole, each a batch of the sessions it holds turns
    of."""
    units = [
        unit
        for index, material in enumerate(batch.sessions)
        for event in turns(material.events)
        for unit in _units(index, event, budget)
    ]
    if sum(u.size for u in units) <= budget and len(units) <= most and not any(u.part for u in units):
        return [batch]
    groups: list[list[_Unit]] = []
    current: list[_Unit] = []
    size = 0
    for unit in units:
        again = any(u.event.event_id == unit.event.event_id for u in current)
        if current and (again or size + unit.size > budget or len(current) >= most):
            groups.append(current)
            current = _carried(current, unit, budget)
            size = sum(u.size for u in current)
        current.append(unit)
        size += unit.size
    groups.append(current)
    return [_batch(batch, group) for group in groups]


def _units(session: int, event: EventRecord, budget: int) -> list[_Unit]:
    text = event.text or ""
    size = min(PART_CHARS, budget // 2)
    if len(text) <= size:
        return [_Unit(session, event)]
    overlap = min(PART_OVERLAP, size // 10)
    pieces = split(text, size, overlap)
    n = len(pieces)
    return [
        _Unit(session, dataclasses.replace(event, text=f"{PART_MARK.format(k=k, n=n)} {piece}"), part=True)
        for k, piece in enumerate(pieces, start=1)
    ]


def _carried(closed: Sequence[_Unit], following: _Unit, budget: int) -> list[_Unit]:
    """The last whole turns of a closed window that the next one reads again: never a part of a long turn
    (its parts overlap already), and never so much that the next unit no longer fits."""
    carried: list[_Unit] = []
    size = 0
    for unit in reversed(closed):
        if unit.part or len(carried) >= OVERLAP_TURNS or size + unit.size > OVERLAP_CHARS:
            break
        carried.insert(0, unit)
        size += unit.size
    if size + following.size > budget:
        return []
    return carried


def _batch(batch: BatchMaterial, group: Sequence[_Unit]) -> BatchMaterial:
    by_session: dict[int, list[EventRecord]] = {}
    for unit in group:
        by_session.setdefault(unit.session, []).append(unit.event)
    sessions = tuple(
        SessionMaterial(batch.sessions[index].session, tuple(events)) for index, events in by_session.items()
    )
    return dataclasses.replace(batch, sessions=sessions)


def merged(results: Sequence[ExtractionResult]) -> ExtractionResult:
    """One session's answers from its windows, in their order, as one result: every entry once, the episode
    told from start to end."""
    if len(results) == 1:
        return results[0]
    return ExtractionResult(
        episode=_episode([r.episode for r in results if r.episode is not None]),
        facts=_once(f for r in results for f in r.facts),
        open_items=_once(i for r in results for i in r.open_items),
        resolved_items=_once(m for r in results for m in r.resolved_items),
        profile=_once(s for r in results for s in r.profile),
        agent_questions=_once(q for r in results for q in r.agent_questions),
        agent_claims=_once(c for r in results for c in r.agent_claims),
        values=_once(v for r in results for v in r.values),
        dropped=sum(r.dropped for r in results),
        flagged_event_ids=tuple(sorted({e for r in results for e in r.flagged_event_ids})),
        withheld=sum(r.withheld for r in results),
        instructions=_once(i for r in results for i in r.instructions),
        instruction_unscoped=sum(r.instruction_unscoped for r in results),
        object_unresolved=_summed(r.object_unresolved for r in results),
        hours=_once(h for r in results for h in r.hours),
    )


def _episode(episodes: Sequence[EpisodeDraft]) -> EpisodeDraft | None:
    """What the session was about from its first window, how it ended from its last, and its summary told
    by every window in order."""
    if not episodes:
        return None
    first, last = episodes[0], episodes[-1]
    told: list[str] = []
    for episode in episodes:
        summary = episode.summary.strip()
        if summary and summary not in told:
            told.append(summary)
    return dataclasses.replace(
        last,
        summary=" ".join(told),
        intent=first.intent,
        category=next((e.category for e in episodes if e.category != FALLBACK_CATEGORY), first.category),
        matter=next((e.matter for e in episodes if e.matter), None),
        matter_label=next((e.matter_label for e in episodes if e.matter), None),
    )


def _once(entries: Any) -> tuple[Any, ...]:
    """Each entry once, in order, resting on every turn any copy of it cited: the same turn read by two
    windows gives the same entry twice."""
    out: list[Any] = []
    at: dict[str, int] = {}
    for entry in entries:
        key = repr(_without_evidence(entry))
        if key not in at:
            at[key] = len(out)
            out.append(entry)
        elif hasattr(entry, "evidence_event_ids"):
            kept = out[at[key]]
            cited = tuple(dict.fromkeys([*kept.evidence_event_ids, *entry.evidence_event_ids]))
            out[at[key]] = dataclasses.replace(kept, evidence_event_ids=cited)
    return tuple(out)


def _without_evidence(entry: Any) -> Any:
    """What an entry says, whatever turns it rests on: the same promise read in two windows cites the same
    turn, or the two parts of one long turn."""
    if hasattr(entry, "evidence_event_ids"):
        return dataclasses.replace(entry, evidence_event_ids=())
    return entry


def _summed(counts: Any) -> dict[str, int]:
    out: dict[str, int] = {}
    for count in counts:
        for key, value in count.items():
            out[key] = out.get(key, 0) + value
    return out
