"""Who reads which note, how the block for the prompt is made, how a search ranks, and what counts as
personal data in a note. Pure functions: same notes, same bytes (L5)."""

import hashlib
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID

from niadra.domain.agent_memory.model import AgentNoteRecord, NoteOrigin, NoteVisibility
from niadra.domain.compile.tokens import estimate_tokens
from niadra.domain.serve.search import bm25, ranking, tokenize

WRITES_PER_HOUR = 20
"""Per agent (10, C.3): enough to note what a run taught, too few to fill the memory with a loop."""
WRITE_WINDOW = timedelta(hours=1)
TASK_VIEW_TOKENS = 200
"""What a task view with `include_agent_memory` may spend on notes (10, C.3)."""

# The detectors also mark organizations and places; a procedure names systems, companies and sites all the
# time ("post the credit in the ERP of the Campinas branch"), and neither is a person. Everything else is.
NOT_PERSONAL = frozenset({"organization", "location"})

_HEADER = {
    "pt": "Do próprio agente (procedimentos e notas de trabalho, não dados de clientes)",
    "en": "From the agent itself (procedures and working notes, not customer data)",
    "es": "Del propio agente (procedimientos y notas de trabajo, no datos de clientes)",
}
_MORE = {
    "pt": "+{count} notas fora do bloco: busque nas notas.",
    "en": "+{count} notes left out of the block: search the notes.",
    "es": "+{count} notas fuera del bloque: busque en las notas.",
}


def personal_kinds(kinds: Collection[str]) -> list[str]:
    return sorted({k for k in kinds if k not in NOT_PERSONAL})


@dataclass(frozen=True, slots=True)
class Reader:
    source_id: UUID | None
    vendor: str | None
    person: bool = False
    """A person in the Console sees every note of the space; the policy is for agents."""


def may_read(note: AgentNoteRecord, reader: Reader, vendors: Mapping[UUID, str | None]) -> bool:
    """`vendors` maps each source to its vendor: the vendor visibility is the same neutrality as the customer
    memory's, so vendor A never reads what vendor B's agent learned (10, C.5)."""
    if reader.person:
        return True
    visibility = note.draft.visibility
    if visibility is NoteVisibility.SPACE or note.source_id == reader.source_id:
        return True
    if visibility is NoteVisibility.VENDOR:
        owner = vendors.get(note.source_id)
        return owner is not None and owner == reader.vendor
    return False


def _matches(note: AgentNoteRecord, wanted: frozenset[str]) -> int:
    return len(wanted & set(note.draft.tags))


def order_for_block(notes: Sequence[AgentNoteRecord], wanted: Collection[str]) -> list[AgentNoteRecord]:
    """Notes a person wrote first, then those whose tags match the view or the task, then the most revised,
    then the oldest line: a stable order, so the block keeps the same bytes between customers and the prompt
    cache holds."""
    tags = frozenset(wanted)
    return sorted(
        notes,
        key=lambda n: (
            n.origin is not NoteOrigin.HUMAN,
            -_matches(n, tags),
            -n.version,
            n.created_at,
            str(n.note_id),
        ),
    )


@dataclass(frozen=True, slots=True)
class Block:
    text: str
    note_ids: tuple[UUID, ...]
    tokens: int
    etag: str
    left_out: int = 0
    """Notes the block's size left out: each stays, and the note search reads it."""


def render_block(
    notes: Sequence[AgentNoteRecord], *, language: str, max_tokens: int, wanted: Collection[str] = ()
) -> Block:
    """The notes for the prompt, in `order_for_block`, as many as `max_tokens` holds. A note that does not
    fit is left out of this block only: every note stays, the search reads it, and the block's last line
    says how many it left out."""
    header = _HEADER.get(language, _HEADER["en"])
    ordered = order_for_block(notes, wanted)
    budget = max_tokens - estimate_tokens(header) - 4
    lines, chosen = _fitting(ordered, budget)
    left_out = len(ordered) - len(chosen)
    if left_out:
        more = _MORE.get(language, _MORE["en"])
        lines, chosen = _fitting(ordered, budget - estimate_tokens(more.format(count=len(ordered))))
        left_out = len(ordered) - len(chosen)
        lines.append(more.format(count=left_out))
    text = f"<agent_notes>\n{header}\n" + "\n".join(lines) + "\n</agent_notes>" if lines else ""
    digest = hashlib.sha256((text + "|" + ",".join(str(i) for i in chosen)).encode()).hexdigest()[:32]
    return Block(text, tuple(chosen), estimate_tokens(text), f"am-{digest}", left_out)


def _fitting(notes: Sequence[AgentNoteRecord], budget: int) -> tuple[list[str], list[UUID]]:
    lines: list[str] = []
    chosen: list[UUID] = []
    used = 0
    for note in notes:
        line = f"- [{note.draft.kind.value}] {note.draft.title}: {' '.join(note.draft.body.split())}"
        cost = estimate_tokens(line)
        if used + cost > budget:
            continue  # a shorter note further down may still fit
        lines.append(line)
        chosen.append(note.note_id)
        used += cost
    return lines, chosen


def search(
    notes: Sequence[AgentNoteRecord], query: str, tags: Collection[str], limit: int
) -> list[AgentNoteRecord]:
    """Words and tags, no vector: tens to hundreds of notes per agent do not pay for one (10, C.2). With tags
    only notes carrying one of them; a tag that matches a query word also counts as a word."""
    wanted = frozenset(tags)
    pool = [n for n in notes if not wanted or wanted & set(n.draft.tags)]
    if not pool:
        return []
    by_id = {str(n.note_id): n for n in pool}
    texts = {
        i: f"{n.draft.title} {n.draft.title} {n.draft.body} {' '.join(n.draft.tags)}"
        for i, n in by_id.items()
    }
    scores = bm25(query, texts)
    if not scores and not tokenize(query):
        return []
    return [by_id[i] for i in ranking(scores)[:limit]]


def still_valid(notes: Sequence[AgentNoteRecord], now: datetime) -> list[AgentNoteRecord]:
    return [n for n in notes if n.active_at(now)]
