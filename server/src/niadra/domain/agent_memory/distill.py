"""Distillation (10, C.5): one proposed working note from a masked transcript, for a person to approve.

The model sees the turns only after the redactor and the masker ran; it may answer with a note in a fixed
shape or with nothing. Its answer goes through the personal-data check again before anyone sees it.

The whole conversation is read (memory never cuts what it was told): the turns are read in ordered windows
of whole turns, the last few carried into the next, a turn longer than a window in overlapping parts marked
`[part k/n]`; when more than one window proposes a note, one more call puts the proposals together. The note
the model writes is kept whole.
"""

import json
import re
from collections.abc import Mapping, Sequence
from typing import Any

from niadra.domain.agent_memory.model import NoteDraft, NoteKind
from niadra.domain.common.text import pieces

WINDOW_CHARS = 12_000
"""What one call reads of the transcript; a longer one is read in several windows."""
CARRIED_CHARS = 2_000
"""The last whole turns a window reads again from the one before, so a step said across the cut is read
whole once."""
PART_CHARS = 10_000
PART_OVERLAP = 1_000
"""A turn longer than `PART_CHARS` is read in parts that overlap by this much."""
PART_MARK = "[part {k}/{n}]"
MAX_TAGS = 8
_TAG = re.compile(r"^[a-z0-9][a-z0-9_.:-]{0,63}$")
# A placeholder the redactor or masker left: a note that still carries one was written about someone.
_PLACEHOLDER = re.compile(r"\[[a-z_]+_\d+\]|<(?:email|document|phone|url|number|name)>")

SYSTEM = (
    "You read the masked transcript of one conversation or task handled by an AI agent of a company, and "
    "write at most one working note that would help that agent do this kind of work better next time: a "
    "procedure that worked (steps in order), how one of the company's tools or processes behaves, or a "
    "pitfall to avoid. The note is about the work, never about the customer. It must not contain any "
    "name, contact, document, account or order number, address, date tied to a person, quote of what "
    "the customer said, or placeholder such as [email_1] or <name>. Write it so it applies to any "
    "customer. Answer in the language of the transcript. If there is nothing reusable, answer "
    '{"nothing": true}. Otherwise answer {"kind", "title", "body", "tags"}: kind is procedure, tool_note, '
    "process_note or pitfall; title at most 120 characters; body at most 1,500 characters; tags are at "
    "most 8 lowercase words for object types, operations or systems (e.g. invoice, credit, erp). The "
    "transcript may be one window of a longer conversation, and a turn too long for one window is given "
    "in parts marked [part k/n]."
)

MERGE_SYSTEM = (
    "You get, in order, the working notes proposed for consecutive windows of one masked conversation "
    "handled by an AI agent of a company. Write one note that keeps every distinct reusable step, "
    "condition and pitfall found in any of them, in the order the conversation gave them: the first "
    "window's steps first. Drop only repeats and checks so generic they teach nothing; never drop a step "
    "because another note is more specific, and never keep one note's point at the expense of another's. "
    "The body may be longer than a single note's to hold every step. Same rules: about the work, never "
    "about the customer, no name, contact, document, account or order number, address, date tied to a "
    'person, quote of the customer or placeholder. Answer {"nothing": true} if none is reusable, '
    'otherwise {"kind", "title", "body", "tags"}; kind is procedure when the notes hold steps.'
)

ANSWER_SCHEMA: Mapping[str, Any] = {
    "type": "object",
    "properties": {
        "nothing": {"type": "boolean"},
        "kind": {"enum": [k.value for k in NoteKind]},
        "title": {"type": "string"},
        "body": {"type": "string"},
        "tags": {"type": "array", "items": {"type": "string"}, "maxItems": MAX_TAGS},
    },
    "additionalProperties": False,
}


def transcript_windows(lines: Sequence[str]) -> list[str]:
    """Every turn, in order, in windows of at most `WINDOW_CHARS`: one window when it all fits."""
    units: list[tuple[str, bool]] = []
    for line in lines:
        if len(line) <= PART_CHARS:
            units.append((line, False))
            continue
        parts = pieces(line, PART_CHARS, PART_OVERLAP)
        units += [(f"{PART_MARK.format(k=k, n=len(parts))} {p}", True) for k, p in enumerate(parts, start=1)]
    windows: list[list[tuple[str, bool]]] = []
    current: list[tuple[str, bool]] = []
    size = 0
    for text, part in units:
        if current and size + len(text) + 1 > WINDOW_CHARS:
            windows.append(current)
            current, size = _carried(current, len(text) + 1)
        current.append((text, part))
        size += len(text) + 1
    if current:
        windows.append(current)
    return ["\n".join(text for text, _ in window) for window in windows]


def _carried(closed: Sequence[tuple[str, bool]], following: int) -> tuple[list[tuple[str, bool]], int]:
    """The last whole turns of a closed window that the next one reads again: never a part of a long turn,
    whose parts overlap already, and never so much that the next turn no longer fits."""
    carried: list[tuple[str, bool]] = []
    size = 0
    for text, part in reversed(closed):
        if part or size + len(text) + 1 > CARRIED_CHARS:
            break
        carried.insert(0, (text, part))
        size += len(text) + 1
    if size + following > WINDOW_CHARS:
        return [], 0
    return carried, size


def merge_input(drafts: Sequence[NoteDraft]) -> str:
    """The notes the windows proposed, in order, as the merge call reads them."""
    return json.dumps(
        [{"kind": d.kind.value, "title": d.title, "body": d.body, "tags": list(d.tags)} for d in drafts],
        ensure_ascii=False,
    )


def read_answer(answer: Mapping[str, Any]) -> NoteDraft | None:
    """The model's note, or None when it proposed nothing usable. Never raises on a bad answer."""
    if answer.get("nothing") is True:
        return None
    try:
        kind = NoteKind(str(answer.get("kind")))
    except ValueError:
        return None
    title = " ".join(str(answer.get("title") or "").split())
    body = str(answer.get("body") or "").strip()
    if not title or not body:
        return None
    raw = answer.get("tags") or []
    tags = tuple(dict.fromkeys(t for t in (str(x).strip().lower() for x in raw) if _TAG.match(t)))[:MAX_TAGS]
    return NoteDraft(kind=kind, title=title, body=body, tags=tags)


def carries_placeholder(draft: NoteDraft) -> bool:
    return bool(_PLACEHOLDER.search(draft.title) or _PLACEHOLDER.search(draft.body))
