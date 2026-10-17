"""Memory bound to an object: what people and agents said or did about an object, kept
under the handle of who said it and gathered, for every reader whose open set holds the object, across the
handles of its owner and participants.

Three kinds of row cross participants: an observation (a fact with the object), an open item with the object
(a promise, a dispute, a request) and an instruction (a `Stated` entry with `scope: object`). Each carries an
audience, set by rule when it is written (`audience_of`) and never by a model: a customer-facing agent reads
only `shared` rows (`reads`). A conversation's word about a field the system of record holds is shown beside
the recorded value, never in its place; only the recorded value may be claimed (2.4). Pure.
"""

import re
import unicodedata
from collections.abc import Callable, Collection, Iterable, Sequence
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta
from typing import Any, Literal
from uuid import UUID

from niadra.domain.memory.items import item_words
from niadra.domain.records import ObjectKey
from niadra.domain.serve.lexicon import stem
from niadra.domain.vocabulary import (
    Audience,
    MemoryAudience,
    OpenItemKind,
    Sensitivity,
    Speaker,
    Visibility,
)

type BoundKind = Literal["observation", "item", "instruction"]

OBJECTS_CEILING = 500
"""A runaway guard, not a window: the objects of a subject a compilation, a resolution or an extraction
reads, newest first. Every open object, and every one final within `FINAL_WINDOW`, is read below it; past
it the rest is counted (`MemorySnapshot.objects_unread`), never dropped in silence. A law firm's client with
60 cases brings about 260 objects (4.3 per case, juridico-zero, 08/10/2026); the 20 newest it once read
lost the memory of every case past the fifth."""
MAX_HANDLES = 50
"""Handles of an object whose rows are gathered: its owner and the participants with a role first, then the
others; past it the rest are counted (`ObjectMemory.handles_unread`). Production had 8 per object after one
week (juridico-zero, 08/10/2026)."""
ROWS_CEILING = 200
"""A runaway guard on the instructions and open items of one object a compilation reads: every one below
it, never the newest few. A case with 25 open items kept its 20 newest and lost the 5 most overdue."""
MAX_ROWS = 20
"""Observations per object a compilation keeps, newest first: what the JSON views carry; and kept items per
object a line says, the newest, with the count of the rest (`OnLine.more`). Instructions, open items and
kept items are all gathered (`ROWS_CEILING`)."""
IN_TEXT = 3
"""Observations per object the line of the object says in text, newest first; the line says how many more
the memory holds (`OnLine.more`). Instructions and open items have no such cap: a directive or a commitment
cut off a line is one the agent breaks without knowing it, so the line says all of them."""
FINAL_WINDOW = timedelta(days=7)
"""An instruction about an object in a final state leaves the reads this long after the state (2.8); the row
stays for the export and the audit."""
CUSTOMER_FACING = frozenset({Audience.CUSTOMER_AGENT, Audience.HUMAN})
"""Sources that talk with the customer: what they hear, in a public turn, the customer said in front of
them."""


def without_due(text: str, due: date) -> str:
    """An item's text without the trailing words that only repeat its own due day ("Juntar a procuração
    atualizada até 07/10/2026" due on 07/10): the line says the due, or the day it was kept, beside it, and a
    kept item that still read "até 07/10" was told as not done yet (juridico-sandbox, 06/10/2026). Only that
    day, written as a number (07/10/2026, 07/10, 7/10, 2026-10-07), with at most three short words before it;
    any other text stays as said."""
    days = (
        rf"0?{due.day}/0?{due.month}(?:/(?:{due.year}|{due.year % 100:02d}))?",
        rf"{due.year}-{due.month:02d}-{due.day:02d}",
    )
    pattern = rf"[\s,;]+(?:[^\W\d_]{{1,5}}\s+){{0,3}}(?:{'|'.join(days)})[.\s]*$"
    cut = re.sub(pattern, "", text, flags=re.IGNORECASE)
    return cut if len(cut.split()) > 1 else text


def staff(source: Audience | None) -> bool:
    """Whether a conversation's source talks with the company's own people, not its customers: the person an
    internal agent talks with (a lawyer at the firm's assistant) is staff, and what they say is the company's
    word, never the customer's."""
    return source is not None and source not in CUSTOMER_FACING


def said_by(source: Audience | None, speaker: Speaker) -> Speaker:
    """Who said a turn, as memory keeps it: the person in a conversation of a `staff` source is a human agent
    of the company. The SDK records them as the conversation's customer, and a row they said read "(cliente)"
    on the client's case, so an attendant took the firm's words for the client's (juridico-sandbox,
    05/10/2026)."""
    return Speaker.HUMAN_AGENT if speaker is Speaker.CUSTOMER and staff(source) else speaker


def audience_of(source: Audience | None, visibility: Visibility, kind: BoundKind) -> MemoryAudience:
    """Who besides the person may read a row about an object (2.6): what a customer-facing source heard in a
    public turn is `shared`; anything internal stays `internal`, except an item or an instruction, which is
    a commitment or a directive about the object and is `shared` by its kind."""
    if source in CUSTOMER_FACING and visibility is Visibility.PUBLIC:
        return MemoryAudience.SHARED
    return MemoryAudience.SHARED if kind in ("item", "instruction") else MemoryAudience.INTERNAL


def reads(audience: MemoryAudience | None, reader: Audience) -> bool:
    """Whether a reader may read a row of this audience: a customer-facing agent only `shared` ones, every
    internal reader both. A row bound to no object (None) never crosses."""
    if audience is None:
        return False
    return audience is MemoryAudience.SHARED or reader is not Audience.CUSTOMER_AGENT


@dataclass(frozen=True, slots=True)
class BoundRow:
    """One row about an object, as a read shows it beside the object's line."""

    kind: BoundKind
    row_id: str
    text: str
    origin_handle_id: UUID
    said_at: datetime
    audience: MemoryAudience
    speaker: Speaker | None = None
    """Who said it, when memory knows: `human_agent` for the company's own people (`said_by`)."""
    role: str | None = None
    """The role of who said it on the object (`lawyer`, `owner`, `participant`), when they take part in it."""
    category: str = "object"
    sensitivity: Sensitivity = Sensitivity.NORMAL
    predicate: str | None = None
    item_kind: OpenItemKind | None = None
    due_at: datetime | None = None
    overdue: bool = False
    owner: str | None = None
    kept_at: datetime | None = None
    """When an item was kept: a promise resolved within `domain.memory.items.KEPT_WINDOW` stays on the
    object's line, said as kept, so a reader learns the act it promised was done."""
    strength: str | None = None
    attr: str | None = None
    expires_at: datetime | None = None
    observes: str | None = None
    """The field of the object's type the observation is about (`observes` on its predicate)."""
    recorded: Any = None
    """The value the system of record holds for `observes`, None when it holds none."""
    recorded_at: datetime | None = None
    only_with: str | None = None
    """An instruction that holds with one person only, as the turns name them ("Clara"): it binds the
    readers dealing with that person, never the one speaking."""
    contact: bool = False
    """An instruction that is a contact preference of `only_with` (a channel, hours): how the company
    reaches that person, never a limit on what may be said to them."""

    @property
    def diverges(self) -> bool:
        """Said one value of a field the system holds another for (2.4)."""
        return (
            self.observes is not None
            and self.recorded is not None
            and not same_value(self.text, self.recorded)
        )

    def to_json(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "kind": self.kind,
            "id": self.row_id,
            "text": self.text,
            "by": str(self.origin_handle_id),
            "at": self.said_at.isoformat(),
            "audience": self.audience.value,
        }
        optional = {
            "speaker": self.speaker.value if self.speaker else None,
            "role": self.role,
            "category": self.category if self.category != "object" else None,
            "sensitivity": self.sensitivity.value if self.sensitivity is not Sensitivity.NORMAL else None,
            "predicate": self.predicate,
            "item_kind": self.item_kind.value if self.item_kind else None,
            "due_at": self.due_at.isoformat() if self.due_at else None,
            "overdue": self.overdue or None,
            "owner": self.owner,
            "kept_at": self.kept_at.isoformat() if self.kept_at else None,
            "strength": self.strength,
            "attr": self.attr,
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
            "observes": self.observes,
            "recorded": self.recorded,
            "recorded_at": self.recorded_at.isoformat() if self.recorded_at else None,
            "only_with": self.only_with,
            "contact": self.contact or None,
        }
        return out | {k: v for k, v in optional.items() if v is not None}

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> "BoundRow":
        return cls(
            kind=data["kind"],
            row_id=data["id"],
            text=data["text"],
            origin_handle_id=UUID(data["by"]),
            said_at=datetime.fromisoformat(data["at"]),
            audience=MemoryAudience(data["audience"]),
            speaker=Speaker(data["speaker"]) if data.get("speaker") else None,
            role=data.get("role"),
            category=data.get("category") or "object",
            sensitivity=Sensitivity(data.get("sensitivity") or Sensitivity.NORMAL),
            predicate=data.get("predicate"),
            item_kind=OpenItemKind(data["item_kind"]) if data.get("item_kind") else None,
            due_at=_time(data.get("due_at")),
            overdue=bool(data.get("overdue")),
            owner=data.get("owner"),
            kept_at=_time(data.get("kept_at")),
            strength=data.get("strength"),
            attr=data.get("attr"),
            expires_at=_time(data.get("expires_at")),
            observes=data.get("observes"),
            recorded=data.get("recorded"),
            recorded_at=_time(data.get("recorded_at")),
            only_with=data.get("only_with"),
            contact=bool(data.get("contact")),
        )


@dataclass(frozen=True, slots=True)
class ObjectMemory:
    """What was said about one object of the reader's open set, newest first within each kind."""

    key: ObjectKey
    object_id: UUID
    rows: tuple[BoundRow, ...] = ()
    focus: bool = False
    """The read is about the object (an object read, a task view of its type): its instructions enter the
    constraints block as hard entries; out of focus they stay on the object's line (2.7)."""
    final_at: datetime | None = None
    """When the object reached a final state of its lifecycle, None while it is open."""
    handles_unread: int = 0
    """Participants past `MAX_HANDLES` whose rows were not gathered: in the JSON, never dropped in silence."""

    @property
    def ref(self) -> str:
        return f"{self.key.type}:{self.key.namespace}:{self.key.external_id}"

    def of(self, kind: BoundKind) -> list[BoundRow]:
        return [r for r in self.rows if r.kind == kind]

    def open_items(self) -> list[BoundRow]:
        return [r for r in self.rows if r.kind == "item" and r.kept_at is None]

    def instructions(self, now: datetime) -> list[BoundRow]:
        """The instructions in force: not expired, and the object open or final for less than the window."""
        if self.final_at is not None and now - self.final_at > FINAL_WINDOW:
            return []
        return [r for r in self.of("instruction") if r.expires_at is None or r.expires_at > now]

    def to_json(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "ref": self.ref,
            "object_id": str(self.object_id),
            "rows": [r.to_json() for r in self.rows],
        }
        if self.focus:
            out["focus"] = True
        if self.final_at is not None:
            out["final_at"] = self.final_at.isoformat()
        if self.handles_unread:
            out["handles_unread"] = self.handles_unread
        return out

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> "ObjectMemory":
        kind, _, rest = str(data["ref"]).partition(":")
        namespace, _, external_id = rest.partition(":")
        return cls(
            key=ObjectKey(kind, namespace, external_id),
            object_id=UUID(data["object_id"]),
            rows=tuple(BoundRow.from_json(r) for r in data.get("rows", ())),
            focus=bool(data.get("focus")),
            final_at=_time(data.get("final_at")),
            handles_unread=int(data.get("handles_unread") or 0),
        )


def kept(rows: Iterable[BoundRow]) -> tuple[BoundRow, ...]:
    """Newest first, one row per id: every instruction, open item and kept item (up to `ROWS_CEILING` of
    each), and at most `MAX_ROWS` observations. A directive or a commitment is never one of the newest few:
    the oldest open item is the most overdue."""
    out: list[BoundRow] = []
    counts: dict[str, int] = {}
    seen: set[tuple[str, str]] = set()
    for row in sorted(rows, key=lambda r: (r.said_at, r.row_id), reverse=True):
        group = _group(row)
        cap = MAX_ROWS if group == "observation" else ROWS_CEILING
        if (row.kind, row.row_id) in seen or counts.get(group, 0) >= cap:
            continue
        seen.add((row.kind, row.row_id))
        counts[group] = counts.get(group, 0) + 1
        out.append(row)
    return tuple(out)


def _group(row: BoundRow) -> str:
    if row.kind == "item":
        return "open" if row.kept_at is None else "done"
    return row.kind


@dataclass(frozen=True, slots=True)
class OnLine:
    """What an object's line says of its memory (`in_text`), and how many more rows of each kind the memory
    holds that the line leaves out: `said` (observations past `IN_TEXT`) and `done` (kept items past
    `MAX_ROWS`). The line says the counts, so an agent knows to look further instead of taking the line for
    everything."""

    rows: list[BoundRow]
    more: dict[str, int]


def by_due(rows: Iterable[BoundRow]) -> list[BoundRow]:
    """Open items in the order an agent has to act on them: the overdue first, then by the date each is due
    (an item with none last), then the newest."""
    newest = sorted(rows, key=lambda r: (r.said_at, r.row_id), reverse=True)
    # A stable sort: items due the same day, and items with no date, stay newest first.
    return sorted(
        newest, key=lambda r: (not r.overdue, r.due_at is None, r.due_at.timestamp() if r.due_at else 0)
    )


def in_text(memory: ObjectMemory, at: datetime | None, own: Collection[UUID] = ()) -> OnLine:
    """The rows an object's line says: every instruction in force and every open item (the overdue first,
    then by due date, `by_due`), then the kept items and up to `IN_TEXT` observations, newest first, with
    the count of the kept items and observations left out (`OnLine.more`). The items and observations of
    `own` handles stay aside (the reader's own words are lines of its pack already). A row that says what an
    earlier one of its kind said is said once: the same promise noted twice is one promise, with the date by
    which either of them was due (a request relayed without its date kept the lawyer's "até 06/10" off the
    line, juridico-sandbox, Q2 of 05/10/2026)."""
    instructions = memory.instructions(at) if at is not None else memory.of("instruction")
    others = [r for r in memory.rows if r.kind != "instruction" and r.origin_handle_id not in own]
    open_items = by_due(r for r in others if r.kind == "item" and r.kept_at is None)
    kept_items = [r for r in others if r.kind == "item" and r.kept_at is not None]
    said = [r for r in others if r.kind == "observation"]
    out: list[BoundRow] = []
    more: dict[str, int] = {}
    for group, rows, cap in (
        ("instruction", instructions, None),
        ("open", open_items, None),
        ("done", kept_items, MAX_ROWS),
        ("said", said, IN_TEXT),
    ):
        picked: dict[tuple[str, str | None, bool], BoundRow] = {}
        for row in rows:
            # The same words held with another person, or as a contact preference, say something else.
            words = (_fold(row.text).rstrip(" ."), row.only_with, row.contact)
            first = picked.get(words)
            if first is None:
                picked[words] = row
            elif first.due_at is None and row.due_at is not None:
                picked[words] = replace(first, due_at=row.due_at, overdue=row.overdue)
        distinct = list(picked.values())
        out += distinct[:cap] if cap is not None else distinct
        if cap is not None and len(distinct) > cap:
            more[group] = len(distinct) - cap
    return OnLine(out, more)


def for_reader(
    memories: Sequence[ObjectMemory], reader: Audience, *, admits: Callable[[BoundRow], bool] | None = None
) -> tuple[ObjectMemory, ...]:
    """What one reader may see of each object's memory: the audience first (2.6), then the policy, which
    only ever takes rows away. A sensitive row never crosses to another participant."""
    out = []
    for memory in memories:
        rows = tuple(
            r
            for r in memory.rows
            if reads(r.audience, reader)
            and r.sensitivity is not Sensitivity.SENSITIVE
            and (admits is None or admits(r))
        )
        out.append(replace(memory, rows=rows))
    return tuple(out)


def same_value(said: str, recorded: Any) -> bool:
    """A said value and a recorded one agree when they read the same, case, accents and spacing aside."""
    return _fold(said) == _fold(str(recorded))


def _fold(text: str) -> str:
    plain = unicodedata.normalize("NFKD", text.strip().lower())
    return " ".join("".join(c for c in plain if not unicodedata.combining(c)).split())


def _time(value: Any) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


_FOLDED = re.compile(r"[a-z0-9]+")
NEGATIONS = frozenset(
    {"nao", "nunca", "jamais", "nem", "tampoco", "not", "never", "no",
     "dont", "doesnt", "didnt", "wont", "cant"}
)  # fmt: skip
"""Words that turn what an instruction or a message says into its opposite, folded, a contraction without
its apostrophe ("don't" is "dont")."""
_APOSTROPHES = str.maketrans("", "", "'\u2019")


def violates(instruction: str, strength: str | None, message: str) -> bool:
    """Whether an agent's message does what a `must_not` instruction says not to do (2.8), by rule: every
    content word of the instruction, its negation aside, is in the message, and the message negates
    nothing. "Neste processo, não propor acordo" and "Posso propor um acordo de R$ 5 mil" is one; "não vou
    propor acordo" is not. A rough rule on purpose: it counts, and never blocks."""
    if strength != "must_not":
        return False
    instruction, message = instruction.translate(_APOSTROPHES), message.translate(_APOSTROPHES)
    # What follows the negation is the act ("neste processo, não propor acordo": propor acordo).
    words = _FOLDED.findall(_fold(instruction))
    after = next((i for i, w in enumerate(words) if w in NEGATIONS), None)
    act = " ".join(words[after + 1 :]) if after is not None else " ".join(words)
    wanted = {stem(w) for w in item_words(act) if w not in NEGATIONS}
    said = item_words(message)
    if not wanted or said & NEGATIONS:
        return False
    return wanted <= {stem(w) for w in said}


def own_words(text: str, who: str | None) -> str:
    """A contact preference's words without the name they start with when the line already says whose it
    is ("Marcos Lima: só por WhatsApp" under "preferência de contato de Marcos Lima")."""
    head, sep, rest = text.partition(":")
    if who and sep and head.strip().casefold() == who.strip().casefold() and rest.strip():
        return rest.strip()
    return text
