"""Purpose and legal basis per memory item (13, 6.6): why each item was collected, who may read it for
which purpose, how long each purpose keeps it, and what a data subject's objection to one purpose takes.

The rules are the space's `purposes` document. An item is tagged once, when it is written, with the
purposes it was collected for and the legal basis each one had then: the basis in force at collection is
what the access package reports, and a later change of the document never rewrites it (GDPR art. 6 and
LGPD art. 7 fix the basis before the processing). A read for a purpose sees an item collected for that
purpose or for one the policy declares compatible with it; retention runs per purpose and the item leaves
when the last purpose that holds it expires.

Everything here is pure: the application loads the rows' attributes (never their content) and applies
what these functions decide.
"""

from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Any, Protocol
from uuid import UUID


class LegalBasis(StrEnum):
    """The legal bases of LGPD arts. 7 and 11 and GDPR art. 6, by one English name each."""

    CONSENT = "consent"
    CONTRACT = "contract"
    LEGAL_OBLIGATION = "legal_obligation"
    LEGITIMATE_INTEREST = "legitimate_interest"
    VITAL_INTEREST = "vital_interest"
    HEALTH_PROTECTION = "health_protection"
    PUBLIC_TASK = "public_task"
    RESEARCH = "research"
    LEGAL_CLAIMS = "legal_claims"
    CREDIT_PROTECTION = "credit_protection"
    FRAUD_PREVENTION = "fraud_prevention"


class ItemKind(StrEnum):
    FACT = "fact"
    EPISODE = "episode"
    OPEN_ITEM = "open_item"
    VALUE = "value"


@dataclass(frozen=True, slots=True)
class PurposeTag:
    """One purpose an item was collected for, with the legal basis it had when it was collected."""

    purpose: str
    legal_basis: str

    def to_json(self) -> dict[str, str]:
        return {"purpose": self.purpose, "legal_basis": self.legal_basis}


Tags = tuple[PurposeTag, ...]


def tags_from_json(raw: Any) -> Tags | None:
    """The stored form is a JSON object `{purpose: basis}`; None is a row not tagged yet."""
    if raw is None:
        return None
    return tuple(PurposeTag(str(p), str(b)) for p, b in sorted(dict(raw).items()))


def tags_to_json(tags: Tags | None) -> dict[str, str] | None:
    return None if tags is None else {t.purpose: t.legal_basis for t in sorted(tags, key=_by_name)}


def _by_name(tag: PurposeTag) -> str:
    return tag.purpose


class PurposeSpecLike(Protocol):
    @property
    def name(self) -> str: ...
    @property
    def legal_basis(self) -> str: ...
    @property
    def retention_days(self) -> int | None: ...


class PurposeRules(Protocol):
    """The attributes of the `purposes` document the decisions read."""

    @property
    def purposes(self) -> Sequence[PurposeSpecLike]: ...
    @property
    def categories(self) -> Mapping[str, Sequence[str]]: ...
    @property
    def event_types(self) -> Mapping[str, Sequence[str]]: ...
    @property
    def default(self) -> Sequence[str]: ...


@dataclass(frozen=True, slots=True)
class Collected:
    """What decides an item's purposes, read from the row and its session; never its content."""

    kind: ItemKind
    category: str | None = None
    """The fact's category, the conversation's normalized category for the rest."""
    event_types: tuple[str, ...] = ()
    """Canonical types of the system events among the item's evidence."""
    source_purposes: frozenset[str] = frozenset()
    """What the source that collected it declares it works for."""


def configured(rules: PurposeRules | None) -> bool:
    return rules is not None and bool(rules.purposes)


def collection_purposes(item: Collected, rules: PurposeRules) -> frozenset[str]:
    """The purposes an item is collected for, the first rule that names any: its category, the system
    events it came from, the collecting source's declared purposes, the document's default. Only purposes
    the document declares count, so a source's free-form purpose that the legal team never declared tags
    nothing."""
    declared = {p.name for p in rules.purposes}
    by_event = [p for t in item.event_types for p in _event_purposes(t, rules.event_types)]
    for found in (
        rules.categories.get(item.category or "", ()),
        by_event,
        sorted(item.source_purposes),
        rules.default,
    ):
        named = frozenset(found) & declared
        if named:
            return named
    return frozenset()


def _event_purposes(event_type: str, mapping: Mapping[str, Sequence[str]]) -> list[str]:
    found: list[str] = []
    for pattern, purposes in sorted(mapping.items()):
        if pattern == event_type or (pattern.endswith("*") and event_type.startswith(pattern[:-1])):
            found += purposes
    return found


def tag(purposes: Iterable[str], rules: PurposeRules, objected: Collection[str] = ()) -> Tags:
    """The tags of the purposes, each with the basis the document gives it now; objected purposes left out."""
    bases = {p.name: p.legal_basis for p in rules.purposes}
    return tuple(PurposeTag(p, bases[p]) for p in sorted(set(purposes)) if p in bases and p not in objected)


def readers(tags: Tags | None, compatible: Mapping[str, Collection[str]]) -> frozenset[str]:
    """The reading purposes that may see an item: the ones it was collected for and those the policy
    declares compatible with them. Empty for an item without tags, which the purposes leave open."""
    if not tags:
        return frozenset()
    own = {t.purpose for t in tags}
    return frozenset(own | {r for p in own for r in compatible.get(p, ())})


def retention(rules: PurposeRules) -> dict[str, timedelta]:
    """Per purpose with a limit, how long an item collected for it is kept."""
    return {p.name: timedelta(days=p.retention_days) for p in rules.purposes if p.retention_days}


def expired(tags: Tags | None, anchor: datetime, now: datetime, keep: Mapping[str, timedelta]) -> bool:
    """An item expires when every purpose that holds it has a limit and all of them ran out: the last
    purpose to end decides. An untagged item, or one with a purpose kept without a limit, stays."""
    if not tags:
        return False
    return all(t.purpose in keep and anchor + keep[t.purpose] <= now for t in tags)


def cutoffs(rules: PurposeRules, now: datetime) -> dict[str, datetime]:
    """Per purpose with a limit, the instant before which an item collected only for it has expired."""
    return {name: now - keep for name, keep in retention(rules).items()}


class Objection(StrEnum):
    """What an objection does to one item that carries the purpose."""

    UNTOUCHED = "untouched"
    NARROWED = "narrowed"
    """The item keeps its other purposes; only the objected one leaves it."""
    REMOVED = "removed"
    """The objected purpose was the last that held it: the item goes."""


def after_objection(tags: Tags | None, objected: Collection[str]) -> tuple[Objection, Tags | None]:
    if not tags or not any(t.purpose in objected for t in tags):
        return Objection.UNTOUCHED, tags
    kept = tuple(t for t in tags if t.purpose not in objected)
    return (Objection.NARROWED, kept) if kept else (Objection.REMOVED, None)


@dataclass(frozen=True, slots=True)
class TagRow:
    """An item as the tagging reads it: its key and what decides its purposes."""

    key: "ItemKey"
    collected: Collected
    episode_id: UUID | None = None
    """The conversation a value was said in: a value takes its conversation's purposes, since it is
    read only where the conversation is (11, 3.2)."""


@dataclass(frozen=True, slots=True)
class TagDecision:
    row: TagRow
    tags: Tags | None
    """None: the item goes (every purpose it had was objected to)."""


def decide_tags(
    rows: Sequence[TagRow],
    rules: PurposeRules,
    objected: Mapping[UUID, Collection[str]],
    episode_tags: Mapping[UUID, Tags] | None = None,
) -> list[TagDecision]:
    """The tags of rows written without them. `objected` holds, per origin handle, the purposes its
    customer objected to; `episode_tags` the tags of the conversations the values were said in."""
    decided: list[TagDecision] = []
    of_episodes = dict(episode_tags or {})
    for row in sorted(rows, key=lambda r: (r.key.kind != ItemKind.EPISODE, str(r.key.item_id))):
        refused = objected.get(row.key.origin_handle_id, ())
        if row.key.kind is ItemKind.VALUE and row.episode_id is not None and row.episode_id in of_episodes:
            wanted: Iterable[str] = [t.purpose for t in of_episodes[row.episode_id]]
        else:
            wanted = collection_purposes(row.collected, rules)
        purposes = set(wanted)
        tags = tag(purposes, rules, refused)
        if purposes and not tags and purposes <= set(refused):
            decided.append(TagDecision(row, None))
            continue
        if row.key.kind is ItemKind.EPISODE:
            of_episodes[row.key.item_id] = tags
        decided.append(TagDecision(row, tags))
    return decided


@dataclass(frozen=True, slots=True)
class ItemKey:
    kind: ItemKind
    origin_handle_id: UUID
    item_id: UUID
    """The fact, episode (every version), open item or value id."""


@dataclass(frozen=True, slots=True)
class Untagged:
    """A row as the repository returns it for tagging: attributes only, never content."""

    key: ItemKey
    category: str | None = None
    """A fact's category, an episode's normalized category; None for items that take their session's."""
    session_id: UUID | None = None
    source_id: UUID | None = None
    episode_id: UUID | None = None
    event_ids: tuple[UUID, ...] = ()
    """The customer's evidence (a fact's mentions without the company's feedback), the events an open item
    or a value came from."""


@dataclass(frozen=True, slots=True)
class Tagged:
    key: ItemKey
    tags: Tags | None
