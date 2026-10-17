"""The rows the history navigation searches and pages through (16.2).

They are the compiler's candidates (same templates, same policy attributes) enriched with what a
navigation row shows: channel, source, outcome, origin event. Reusing the candidates keeps the
pack and the navigation from ever describing the same item in two ways.
"""

import base64
from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

from niadra.domain.common.errors import InvalidInput
from niadra.domain.compile.candidates import object_text, trait_text
from niadra.domain.compile.model import Candidate, ItemKind, MemorySnapshot
from niadra.domain.compile.templates import Formatter
from niadra.domain.memory.items import OPEN_STATUSES, closed_by_of
from niadra.domain.memory.vectors import Embeddable
from niadra.domain.objects.undeclared import shown
from niadra.domain.policy import ItemAttrs
from niadra.domain.records import (
    AgentAction,
    BusinessObject,
    Episode,
    EventRecord,
    Fact,
    ObjectKey,
    OpenItem,
    Trait,
)
from niadra.domain.serve.where import Where
from niadra.domain.vocabulary import EventKind, SubjectKind

NAVIGABLE = frozenset(
    {ItemKind.EPISODE, ItemKind.FACT, ItemKind.OPEN_ITEM, ItemKind.ACTION, ItemKind.OBJECT, ItemKind.TRAIT}
)
TIMELINE_KINDS = frozenset({ItemKind.EPISODE, ItemKind.ACTION, ItemKind.SYSTEM_EVENT})
"""What the timeline lists by default: what happened, conversations, agent actions and system events."""
# Conversation plumbing, not something that happened to the customer.
_NOT_HISTORY = ("handoff", "feedback.")
EPISODE_TAIL = timedelta(minutes=15)
"""What happened right after a conversation still belongs to its timeline: the credit an agent promised
lands in the ERP a few minutes after the call."""
VECTOR_CHUNK_CHARS = 400
"""Characters of one piece a search vector is made from: the encoder reads the first 128 tokens of a text,
about 400 characters. A longer row is never cut: each piece is encoded and the row's vector is their mean
(`pooled`), so its end is found as its start is."""


@dataclass(frozen=True, slots=True)
class HistoryDoc:
    id: str
    """The compiler's key, `<kind>:<id>`; the API names the row by `ref`."""
    kind: ItemKind
    text: str
    at: datetime
    attrs: ItemAttrs
    channel: str | None = None
    source_id: UUID | None = None
    outcome: str | None = None
    confidence: float | None = None
    origin_event_id: str | None = None
    object_key: ObjectKey | None = None
    valid_until: datetime | None = None
    expected_operation: str | None = None
    """An open item's: the operation that closes it when an action or a system event confirms it."""
    status: str | None = None
    """An open item's: `open`, `overdue` or `resolved`."""
    closed_at: datetime | None = None
    closed_by: Mapping[str, Any] | None = None
    """A resolved item's: what closed it (`domain.memory.items.closed_by_of`)."""
    item_kind: str | None = None
    """An open item's: `promise`, `dispute` or `request`."""
    audience: str | None = None
    """A fact's or an open item's about an object: who besides the person may read it."""
    object_keys: tuple[ObjectKey, ...] = ()
    """A conversation's: the objects it was about, so the conversations about an object are a filter."""

    @property
    def category(self) -> str:
        return self.attrs.category

    @property
    def ref(self) -> UUID:
        """The row's own id, the one the API names it by."""
        return UUID(self.id.partition(":")[2])

    def row(self, vendors: Mapping[UUID, str]) -> dict[str, Any]:
        """The fields a `where` expression reads (domain/serve/where.py)."""
        key = self.object_key
        return {
            "id": str(self.ref),
            "kind": self.kind.value,
            "channel": self.channel,
            "category": self.category,
            "outcome": self.outcome,
            "source_id": str(self.source_id) if self.source_id else None,
            "vendor": vendors.get(self.source_id) if self.source_id else None,
            "at": self.at,
            "valid_until": self.valid_until,
            "confidence": self.confidence,
            "text": self.text,
            "object_type": key.type if key else None,
            "object_namespace": key.namespace if key else None,
        }


@dataclass(frozen=True, slots=True)
class HistoryFilter:
    since: datetime | None = None
    until: datetime | None = None
    channels: frozenset[str] = frozenset()
    categories: frozenset[str] = frozenset()
    kinds: frozenset[str] = frozenset()
    outcome: str | None = None
    object_key: ObjectKey | None = None
    where: Where | None = None
    vendors: Mapping[UUID, str] = field(default_factory=dict)
    statuses: frozenset[str] = frozenset()
    """The open items' statuses asked for; rows of other kinds pass."""

    def accepts(self, doc: HistoryDoc) -> bool:
        return (
            (self.since is None or doc.at >= self.since)
            and (self.until is None or doc.at < self.until)
            and (not self.channels or doc.channel in self.channels)
            and (not self.categories or doc.category in self.categories)
            and (not self.kinds or doc.kind.value in self.kinds)
            and (self.outcome is None or doc.outcome == self.outcome)
            and (not self.statuses or doc.kind is not ItemKind.OPEN_ITEM or doc.status in self.statuses)
            and (
                self.object_key is None
                or doc.object_key == self.object_key
                or self.object_key in doc.object_keys
            )
            and (self.where is None or self.where.matches(doc.row(self.vendors)))
        )


def timeline_kinds(asked: Iterable[str], statuses: Collection[str] = ()) -> frozenset[ItemKind]:
    """What a timeline lists: what happened, and the facts, open items, patterns and objects `item_kinds`
    names, each at its own date; open items too when `item_statuses` names their statuses. Listing them
    takes no question; search only ranks them by one."""
    items = frozenset({ItemKind.OPEN_ITEM}) if statuses else frozenset()
    return TIMELINE_KINDS | items | frozenset(ItemKind(kind) for kind in asked)


def system_event_docs(
    events: Sequence[EventRecord],
    fmt: Formatter,
    subject_kind: SubjectKind,
    profile_id: UUID | None = None,
    private: Mapping[str, Collection[str]] | None = None,
    declared: Mapping[str, Collection[str]] | None = None,
) -> list[HistoryDoc]:
    """A system event as its own row: its type, the object it touched and every field it may show.
    The policy reads it as it reads that object (same kind and category), so a source that may not see
    invoices sees none of their events either; the fields that object's type holds private stay out, and so
    does a field its type does not declare (`declared`) that may be personal data or free text
    (`domain.objects.undeclared`). Every other field is said, the declared ones first: it said four,
    picked by name."""
    docs = []
    for e in events:
        kind = e.canonical_type or ""
        if e.kind is not EventKind.SYSTEM_EVENT or kind.startswith(_NOT_HISTORY):
            continue
        ref = e.object_refs[0] if e.object_refs else None
        hidden = (private or {}).get(ref.type, ()) if ref is not None else ()
        named = (declared or {}).get(ref.type, frozenset()) if ref is not None else frozenset()
        said = sorted(
            (
                (k not in named, k, v)
                for k, v in e.fields.items()
                if isinstance(v, int | float | str) and v != "" and k not in hidden and shown(k, v, named)
            ),
            key=lambda f: (f[0], f[1]),
        )
        parts = [fmt.value(kind or "system_event")]
        if ref is not None:
            parts.append(f"{fmt.value(ref.type)} {ref.external_id}")
        text = " · ".join([*parts, *(f"{k}: {v}" for _, k, v in said)])
        attrs = ItemAttrs(
            kind="object",
            category=ref.type if ref else kind or "system_event",
            visibility=e.visibility,
            subject_kind=subject_kind,
            quarantined=bool((e.content_meta or {}).get("injection")),
            handle_ids=tuple(sorted(e.handle_ids, key=str)),
            profile_id=profile_id,
        )
        docs.append(
            HistoryDoc(
                f"system_event:{e.event_id}",
                ItemKind.SYSTEM_EVENT,
                text,
                e.occurred_at,
                attrs,
                channel=e.channel,
                source_id=e.source_id,
                origin_event_id=str(e.event_id),
                object_key=ref,
            )
        )
    return docs


def during(doc: HistoryDoc, episode: Episode) -> bool:
    """A row of the episode's own timeline: it happened while the conversation lasted, or just after."""
    return episode.started_at <= doc.at <= episode.ended_at + EPISODE_TAIL


def history_docs(
    snapshot: MemorySnapshot, candidates: Sequence[Candidate], account: MemorySnapshot | None = None
) -> list[HistoryDoc]:
    """One row per navigable candidate, once; with `account`, the organization's items the person's view
    shows too (21.4)."""
    both = (snapshot, *((account,) if account is not None else ()))
    episodes = {f"episode:{e.episode_id}": e for s in both for e in s.episodes}
    facts = {f"fact:{f.fact_id}": f for s in both for f in s.facts}
    items = {f"open_item:{i.item_id}": i for s in both for i in (*s.open_items, *s.kept_items)}
    actions = {f"action:{a.action_id}": a for s in both for a in s.actions}
    objects = {f"object:{o.object_id}": o for s in both for o in s.objects}
    docs = []
    seen: set[str] = set()
    for c in candidates:
        if c.kind not in NAVIGABLE or c.item_id in seen:
            continue
        seen.add(c.item_id)
        doc = HistoryDoc(
            c.item_id,
            c.kind,
            c.text,
            c.at,
            c.attrs,
            origin_event_id=c.origin_events[0] if c.origin_events else None,
        )
        if (episode := episodes.get(c.item_id)) is not None:
            doc = replace(
                doc,
                channel=episode.channel,
                source_id=episode.source_id,
                outcome=episode.outcome,
                at=episode.started_at,
                object_keys=episode.object_refs,
            )
        elif (fact := facts.get(c.item_id)) is not None:
            doc = replace(
                doc,
                confidence=fact.confidence,
                object_key=fact.object_ref,
                valid_until=fact.valid_until,
                audience=fact.audience.value if fact.audience else None,
            )
        elif (item := items.get(c.item_id)) is not None:
            doc = replace(
                doc,
                outcome=item.status.value,
                object_key=item.object,
                expected_operation=item.expected_operation,
                status=item.status.value,
                closed_at=item.closed_at if item.status not in OPEN_STATUSES else None,
                closed_by=closed_by_of(item),
                item_kind=item.kind.value,
                audience=item.audience.value if item.audience else None,
            )
        elif (action := actions.get(c.item_id)) is not None:
            doc = replace(doc, source_id=action.source_id, outcome=action.status.value)
        elif (obj := objects.get(c.item_id)) is not None:
            doc = replace(doc, source_id=obj.source_id, object_key=obj.key)
        docs.append(doc)
    return docs


_VERSIONED = ("summary", "intent", "category", "outcome", "resolution")


def episode_changes(versions: Sequence[Episode]) -> list[tuple[int, datetime, str]]:
    """(version, when, what changed) for each version of an episode, oldest first: `created` for the
    first, then the fields a re-extraction changed, comma-separated."""
    out: list[tuple[int, datetime, str]] = []
    previous: Episode | None = None
    for episode in versions:
        if previous is None:
            what = "created"
        else:
            changed = [f for f in _VERSIONED if getattr(episode, f) != getattr(previous, f)]
            what = ",".join(changed) or "ended_at"
        out.append((episode.version, episode.ended_at, what))
        previous = episode
    return out


def vector_text(
    record: Embeddable,
    fmt: Formatter,
    private: Mapping[str, Collection[str]] | None = None,
    declared: Mapping[str, Collection[str]] | None = None,
) -> str:
    """What an item's stored vector encodes: its meaning, without the date, status or channel of the row
    the navigation shows, so a status change never calls for a new vector. A fact encodes its value, as
    facts always did (15.7)."""
    match record:
        case Fact():
            text = record.value
        case Episode():
            parts = [record.summary]
            if record.resolution:
                parts.append(f"{fmt.word('solution')}: {record.resolution}")
            text = " · ".join(parts)
        case OpenItem():
            text = record.description
        case AgentAction():
            text = record.result or fmt.value(record.operation)
        case BusinessObject():
            text = object_text(
                record,
                fmt,
                (private or {}).get(record.key.type, ()),
                declared=(declared or {}).get(record.key.type, frozenset()),
            )
        case Trait():
            text = trait_text(record, fmt)
    return text


def vector_chunks(text: str) -> list[str]:
    """The pieces of a row's text a search vector is made from, each of at most `VECTOR_CHUNK_CHARS` and
    ending at a word where one does; together they are the whole text."""
    pieces: list[str] = []
    rest = text
    while len(rest) > VECTOR_CHUNK_CHARS:
        cut = rest.rfind(" ", 0, VECTOR_CHUNK_CHARS + 1)
        cut = cut if cut > 0 else VECTOR_CHUNK_CHARS
        pieces.append(rest[:cut])
        rest = rest[cut:].lstrip(" ")
    return [*pieces, rest]


def pooled(vectors: Sequence[Sequence[float]]) -> list[float]:
    """One vector for a row encoded in pieces: their mean, of unit length as the encoder's own. A row of
    one piece keeps its vector as it came."""
    if len(vectors) == 1:
        return list(vectors[0])
    mean = [sum(column) / len(vectors) for column in zip(*vectors, strict=True)]
    norm = sum(x * x for x in mean) ** 0.5
    return [x / norm for x in mean] if norm else mean


def newest_first(docs: Collection[HistoryDoc]) -> list[HistoryDoc]:
    return sorted(docs, key=lambda d: (d.at, d.id), reverse=True)


def encode_cursor(doc: HistoryDoc) -> str:
    return base64.urlsafe_b64encode(f"{doc.at.isoformat()}|{doc.id}".encode()).decode().rstrip("=")


def after_cursor(docs: Sequence[HistoryDoc], cursor: str | None) -> list[HistoryDoc]:
    """The docs strictly older than the cursor, in the newest-first order of `docs`."""
    if not cursor:
        return list(docs)
    try:
        raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)).decode()
        at_text, _, doc_id = raw.partition("|")
        at = datetime.fromisoformat(at_text)
    except ValueError as exc:
        raise InvalidInput("invalid cursor") from exc
    return [d for d in docs if (d.at, d.id) < (at, doc_id)]
