"""In-memory `MemoryRepository` for unit tests.

It enforces what PostgreSQL enforces: the unique dedupe key of facts, primary keys of mentions,
relations and items, and "current version" of episodes. Mentions are read back into
`evidence_event_ids` the same way the SQL join does.
"""

from collections.abc import Mapping, Sequence
from dataclasses import replace
from datetime import datetime
from typing import Any
from uuid import UUID

from niadra.adapters.memory.change_seq import ChangeSeq
from niadra.adapters.memory.store import MemoryStore
from niadra.domain.compile.model import ItemKind
from niadra.domain.exported import export_row
from niadra.domain.extract.result import MemoryRef
from niadra.domain.memory.erasure import held_by_feedback_only, plan_evidence_erasure
from niadra.domain.memory.facts import Relation, RelationOrigin, normalize_value
from niadra.domain.memory.items import CLOSED_BY, evidence_event_ids
from niadra.domain.memory.summaries import TailState
from niadra.domain.memory.values import SettledValue, ValueType
from niadra.domain.memory.vectors import NO_VECTOR_DIM, ItemVector, PendingVector
from niadra.domain.privacy.erasure import OPEN_ITEM_FEEDBACK, ErasedRow, ErasureScope, TargetKind
from niadra.domain.privacy.purposes import ItemKey, Tagged, Untagged
from niadra.domain.privacy.purposes import ItemKind as PurposeKind
from niadra.domain.records import Episode, Fact, ObjectKey, OpenItem, ProfileAttr
from niadra.domain.vocabulary import FactStatus, OpenItemStatus


class InMemoryMemoryRepository:
    def __init__(self, store: MemoryStore) -> None:
        self._s = store.module("memory")
        # `vectors` maps (table, *row key) to (vector or None, model or None, dimension), as the columns do.
        # `item_feedback` maps an open item to the feedback event that resolved it.
        for name in (
            "facts",
            "dedupe",
            "vectors",
            "episodes",
            "items",
            "attrs",
            "tails",
            "values",
            "item_feedback",
            "objections",
            "purpose_state",
        ):
            self._s.setdefault(name, {})
        # `feedback` holds the mentions (origin, fact, event) whose event is the company's feedback;
        # `item_refusals` the (item, event) pairs of resolutions that did not close the item.
        for name in ("mentions", "relations", "feedback", "item_refusals"):
            self._s.setdefault(name, set())
        self._seq = ChangeSeq(store, "memory")

    def _t(self, name: str) -> Any:
        return self._s[name]

    async def facts_for(
        self, handle_ids: Sequence[UUID], statuses: Sequence[FactStatus] = (FactStatus.ACTIVE,)
    ) -> list[Fact]:
        handles, wanted = set(handle_ids), set(statuses)
        found = [f for f in self._t("facts").values() if f.origin_handle_id in handles and f.status in wanted]
        return [self._with_evidence(f) for f in sorted(found, key=lambda f: (f.valid_at, f.fact_id))]

    async def episodes_for(
        self,
        handle_ids: Sequence[UUID],
        *,
        since: datetime | None = None,
        limit: int = 200,
        before: tuple[datetime, UUID] | None = None,
    ) -> list[Episode]:
        handles = set(handle_ids)
        found = [
            e
            for e, current in self._t("episodes").values()
            if current
            and e.origin_handle_id in handles
            and (since is None or e.ended_at >= since)
            and (before is None or (e.ended_at, e.episode_id) < before)
        ]
        return sorted(found, key=lambda e: (e.ended_at, e.episode_id), reverse=True)[:limit]

    async def get_episode(self, episode_id: UUID) -> Episode | None:
        return next(
            (e for e, current in self._t("episodes").values() if current and e.episode_id == episode_id), None
        )

    async def episode_versions(self, origin_handle_id: UUID, episode_id: UUID) -> list[Episode]:
        found = [
            e
            for e, _ in self._t("episodes").values()
            if e.origin_handle_id == origin_handle_id and e.episode_id == episode_id
        ]
        return sorted(found, key=lambda e: e.version)

    async def open_items_for(
        self, handle_ids: Sequence[UUID], statuses: Sequence[OpenItemStatus] = (OpenItemStatus.OPEN,)
    ) -> list[OpenItem]:
        handles, wanted = set(handle_ids), set(statuses)
        found = [i for i in self._t("items").values() if i.origin_handle_id in handles and i.status in wanted]
        return sorted(found, key=lambda i: (i.created_at, i.item_id))

    async def open_items_due(self, until: datetime, *, limit: int) -> list[OpenItem]:
        found = [
            i
            for i in self._t("items").values()
            if i.status is OpenItemStatus.OPEN and i.due_at is not None and i.due_at <= until
        ]
        return sorted(found, key=lambda i: (i.due_at, i.item_id), reverse=True)[:limit]

    async def object_facts(
        self, keys: Sequence[ObjectKey], handle_ids: Sequence[UUID], *, per_object: int
    ) -> list[Fact]:
        handles, wanted = set(handle_ids), set(keys)
        found = [
            f
            for f in self._t("facts").values()
            if f.origin_handle_id in handles
            and f.object_ref in wanted
            and f.audience is not None
            and f.status is FactStatus.ACTIVE
        ]
        ordered = sorted(found, key=lambda f: (-f.last_confirmed_at.timestamp(), f.fact_id))
        return [self._with_evidence(f) for f in ordered[: per_object * len(wanted)]]

    async def object_items(
        self,
        keys: Sequence[ObjectKey],
        handle_ids: Sequence[UUID],
        *,
        per_object: int,
        kept_since: datetime | None = None,
    ) -> list[OpenItem]:
        handles, wanted = set(handle_ids), set(keys)
        live = (OpenItemStatus.OPEN, OpenItemStatus.OVERDUE)

        def taken(i: OpenItem) -> bool:
            if i.status in live:
                return True
            return (
                kept_since is not None
                and i.status is OpenItemStatus.RESOLVED
                and i.closed_at is not None
                and i.closed_at >= kept_since
            )

        found = [
            i
            for i in self._t("items").values()
            if i.origin_handle_id in handles and i.object in wanted and i.audience is not None and taken(i)
        ]
        ordered = sorted(found, key=lambda i: (i.status not in live, -i.created_at.timestamp(), i.item_id))
        return ordered[: per_object * len(wanted)]

    async def consolidated_day(self) -> int | None:
        value: int | None = self._s.get("consolidated_day")
        return value

    async def set_consolidated_day(self, day: int) -> None:
        self._s["consolidated_day"] = day

    async def attrs_for(self, handle_ids: Sequence[UUID]) -> list[ProfileAttr]:
        handles = set(handle_ids)
        found = [a for a in self._t("attrs").values() if a.origin_handle_id in handles]
        return sorted(found, key=lambda a: (a.slot, a.observed_at, str(a.source_id)))

    async def name_rows(
        self, handle_ids: Sequence[UUID], names: Sequence[str]
    ) -> tuple[list[Fact], list[ProfileAttr]]:
        handles, wanted = set(handle_ids), set(names)
        facts = [f for f in await self.facts_for(list(handles)) if f.predicate in wanted]
        attrs = [a for a in await self.attrs_for(list(handles)) if a.slot in wanted]
        return facts, attrs

    async def facts_valued(self, predicates: Sequence[str], dedupe_value: str, *, limit: int) -> list[Fact]:
        wanted = set(predicates)
        found = [
            f
            for f in self._t("facts").values()
            if f.status is FactStatus.ACTIVE
            and f.predicate in wanted
            and f.object_ref is None
            and normalize_value(f.value) == dedupe_value
        ]
        found.sort(key=lambda f: (-f.last_confirmed_at.timestamp(), str(f.fact_id)))
        return [self._with_evidence(f) for f in found[:limit]]

    async def tail_summary(self, profile_id: UUID, level: str) -> str | None:
        state = self._t("tails").get((profile_id, level))
        return state.text if state else None

    async def relations_for(
        self, handle_ids: Sequence[UUID], *, origin: RelationOrigin | None = None
    ) -> list[Relation]:
        handles = set(handle_ids)
        return sorted(
            (
                r
                for r in self._t("relations")
                if r.a.origin_handle_id in handles and (origin is None or r.origin == origin)
            ),
            key=lambda r: (str(r.a.id), str(r.b.id), r.type.value),
        )

    async def episode_vectors(self, episode_ids: Sequence[UUID]) -> list[ItemVector]:
        wanted, vectors = set(episode_ids), self._t("vectors")
        found = []
        for key, (episode, current) in sorted(self._t("episodes").items(), key=lambda kv: str(kv[0])):
            stored = vectors.get(("episodes", *key))
            if current and episode.episode_id in wanted and stored and stored[0] is not None:
                found.append(ItemVector(ItemKind.EPISODE, episode.episode_id, list(stored[0]), stored[1]))
        return found

    async def vectors_for(self, handle_ids: Sequence[UUID]) -> list[ItemVector]:
        vectors = self._t("vectors")
        found = []
        for fact in await self.facts_for(handle_ids):
            stored = vectors.get(("facts", fact.origin_handle_id, fact.fact_id))
            if stored and stored[0] is not None:
                found.append(ItemVector(ItemKind.FACT, fact.fact_id, list(stored[0]), stored[1]))
        for key, (episode, current) in sorted(self._t("episodes").items(), key=lambda kv: str(kv[0])):
            stored = vectors.get(("episodes", *key))
            if current and episode.origin_handle_id in handle_ids and stored and stored[0] is not None:
                found.append(ItemVector(ItemKind.EPISODE, episode.episode_id, list(stored[0]), stored[1]))
        for key, item in sorted(self._t("items").items(), key=lambda kv: str(kv[0])):
            stored = vectors.get(("open_items", *key))
            if item.origin_handle_id in handle_ids and stored and stored[0] is not None:
                found.append(ItemVector(ItemKind.OPEN_ITEM, item.item_id, list(stored[0]), stored[1]))
        return found

    async def values_for(
        self,
        handle_ids: Sequence[UUID],
        *,
        types: Sequence[ValueType] | None = None,
        since: datetime | None = None,
        limit: int = 500,
    ) -> list[SettledValue]:
        handles, wanted = set(handle_ids), set(types) if types is not None else None
        found = [
            v
            for v in self._t("values").values()
            if v.origin_handle_id in handles
            and (wanted is None or v.type in wanted)
            and (since is None or v.said_at >= since)
        ]
        return sorted(found, key=lambda v: (v.said_at, v.value_id), reverse=True)[:limit]

    async def values_by_keys(self, handle_ids: Sequence[UUID], keys: Sequence[str]) -> list[SettledValue]:
        wanted = set(keys)
        return [v for v in await self.values_for(handle_ids, limit=10_000) if v.key in wanted]

    async def insert_values(self, values: Sequence[SettledValue]) -> int:
        stored, new = self._t("values"), 0
        for value in values:
            key = (value.origin_handle_id, value.value_id)
            if key not in stored:
                stored[key] = value
                new += 1
        return new

    async def insert_fact(self, fact: Fact, dedupe_value: str) -> bool:
        key = (fact.origin_handle_id, fact.predicate, normalize_value(dedupe_value))
        if key in self._t("dedupe"):
            return False
        self._t("dedupe")[key] = fact.fact_id
        self._t("facts")[(fact.origin_handle_id, fact.fact_id)] = replace(fact, evidence_event_ids=())
        self._seq.touch("facts", (fact.origin_handle_id, fact.fact_id))
        return True

    async def find_fact(self, origin_handle_id: UUID, predicate: str, dedupe_value: str) -> Fact | None:
        fact_id = self._t("dedupe").get((origin_handle_id, predicate, normalize_value(dedupe_value)))
        fact = self._t("facts").get((origin_handle_id, fact_id)) if fact_id else None
        return self._with_evidence(fact) if fact else None

    async def update_fact(self, fact: Fact) -> None:
        key = (fact.origin_handle_id, fact.fact_id)
        if key in self._t("facts"):
            self._t("facts")[key] = replace(fact, evidence_event_ids=())
            self._seq.touch("facts", key)

    async def add_mentions(self, ref: MemoryRef, event_ids: Sequence[UUID], *, feedback: bool = False) -> int:
        mentions = self._t("mentions")
        fresh = {(ref.origin_handle_id, ref.id, e) for e in event_ids} - mentions
        mentions |= fresh
        marked = self._feedback_events()
        self._t("feedback").update(m for m in fresh if feedback or m[2] in marked)
        for mention in fresh:
            self._seq.touch("fact_mentions", mention)
        return len(fresh)

    async def add_relations(self, relations: Sequence[Relation]) -> None:
        # The primary key leaves the origin out: the first edge of a (pair, type) stays, as in PostgreSQL.
        stored = self._t("relations")
        keys = {(r.a, r.b, r.type) for r in stored}
        for relation in relations:
            if (relation.a, relation.b, relation.type) not in keys:
                stored.add(relation)
                keys.add((relation.a, relation.b, relation.type))

    async def current_episode(self, handle_ids: Sequence[UUID], session_id: UUID) -> Episode | None:
        handles = set(handle_ids)
        return next(
            (
                e
                for e, current in self._t("episodes").values()
                if current and e.session_id == session_id and e.origin_handle_id in handles
            ),
            None,
        )

    async def add_episode_version(self, episode: Episode, replaces: Episode | None = None) -> None:
        episodes = self._t("episodes")
        if replaces is not None:
            key = (replaces.origin_handle_id, replaces.episode_id, replaces.version)
            episodes[key] = (episodes[key][0], False)
            self._seq.touch("episodes", key)
        key = (episode.origin_handle_id, episode.episode_id, episode.version)
        episodes[key] = (episode, True)
        self._seq.touch("episodes", key)

    async def insert_open_item(self, item: OpenItem) -> bool:
        key = (item.origin_handle_id, item.item_id)
        if key in self._t("items"):
            return False
        self._t("items")[key] = item
        self._seq.touch("open_items", key)
        return True

    async def update_open_item(self, item: OpenItem, *, closed_by_event: UUID | None = None) -> None:
        key = (item.origin_handle_id, item.item_id)
        if key in self._t("items"):
            if closed_by_event is not None:
                self._t("item_feedback")[key] = closed_by_event
            stored = self._t("items")[key]
            origin = dict(stored.origin)
            if (closed_by := item.origin.get(CLOSED_BY)) is not None:
                origin[CLOSED_BY] = closed_by
            elif item.status in (OpenItemStatus.OPEN, OpenItemStatus.OVERDUE):
                origin.pop(CLOSED_BY, None)
            self._t("items")[key] = replace(
                stored,
                status=item.status,
                closed_by_action_id=item.closed_by_action_id,
                closed_at=item.closed_at,
                origin=origin,
            )
            self._seq.touch("open_items", key)

    async def note_item_feedback(self, links: Sequence[tuple[UUID, UUID]]) -> int:
        new = set(links) - self._t("item_refusals")
        self._t("item_refusals").update(new)
        return len(new)

    async def put_attr(self, attr: ProfileAttr) -> None:
        self._t("attrs")[(attr.origin_handle_id, attr.slot, attr.source_id)] = attr

    async def tail_state(self, profile_id: UUID, level: str) -> TailState | None:
        state: TailState | None = self._t("tails").get((profile_id, level))
        return state

    async def put_tail_summary(self, profile_id: UUID, level: str, state: TailState) -> None:
        self._t("tails")[(profile_id, level)] = state

    async def unembedded(
        self, *, model: str | None, handle_ids: Sequence[UUID] | None = None, limit: int
    ) -> list[PendingVector]:
        def wanted(key: tuple[Any, ...], origin: UUID) -> bool:
            if handle_ids is not None and origin not in handle_ids:
                return False
            stored = self._t("vectors").get(key)
            if stored is None:
                return True
            return model is not None and stored[2] != NO_VECTOR_DIM and stored[1] != model

        found = [
            PendingVector(self._with_evidence(f))
            for k, f in sorted(self._t("facts").items(), key=lambda kv: str(kv[0]))
            if f.status is FactStatus.ACTIVE and wanted(("facts", *k), f.origin_handle_id)
        ]
        found += [
            PendingVector(e)
            for k, (e, current) in sorted(self._t("episodes").items(), key=lambda kv: str(kv[0]))
            if current and wanted(("episodes", *k), e.origin_handle_id)
        ]
        found += [
            PendingVector(i)
            for k, i in sorted(self._t("items").items(), key=lambda kv: str(kv[0]))
            if i.status in (OpenItemStatus.OPEN, OpenItemStatus.OVERDUE)
            and wanted(("open_items", *k), i.origin_handle_id)
        ]
        return found[:limit]

    async def set_vector(self, pending: PendingVector, vector: Sequence[float] | None, model: str) -> bool:
        record = pending.record
        if isinstance(record, Fact):
            key: tuple[Any, ...] = ("facts", record.origin_handle_id, record.fact_id)
            exists = key[1:] in self._t("facts")
        elif isinstance(record, Episode):
            key = ("episodes", record.origin_handle_id, record.episode_id, record.version)
            exists = key[1:] in self._t("episodes")
        elif isinstance(record, OpenItem):
            key = ("open_items", record.origin_handle_id, record.item_id)
            exists = key[1:] in self._t("items")
        else:
            raise ValueError(f"memory holds no {pending.kind.value} rows")
        if not exists:
            return False
        assert vector is not None or isinstance(record, Fact), "only a structured fact goes without a vector"
        self._t("vectors")[key] = (
            (tuple(vector), model, len(vector)) if vector is not None else (None, None, NO_VECTOR_DIM)
        )
        self._seq.touch(key[0], key[1:])
        return True

    async def expire_facts(self, before: datetime, *, limit: int) -> list[ErasedRow]:
        keys = sorted((k for k, f in self._t("facts").items() if f.last_confirmed_at < before), key=str)[
            :limit
        ]
        erased = [
            self._drop("fact_mentions", m) for m in sorted(self._t("mentions"), key=str) if m[:2] in keys
        ]
        return erased + [self._drop("facts", k) for k in keys]

    async def expire_episodes(self, before: datetime, *, limit: int) -> list[ErasedRow]:
        keys = sorted((k for k, (e, _) in self._t("episodes").items() if e.ended_at < before), key=str)
        erased = [self._drop("episodes", k) for k in keys[:limit]]
        values = sorted((k for k, v in self._t("values").items() if v.said_at < before), key=str)
        return erased + [self._drop("settled_values", k) for k in values[: limit - len(erased)]]

    async def expire_items(self, closed_before: datetime, *, limit: int) -> list[ErasedRow]:
        def expired(item: OpenItem) -> bool:
            closed = item.closed_at
            return item.status is OpenItemStatus.RESOLVED and closed is not None and closed < closed_before

        keys = sorted((k for k, i in self._t("items").items() if expired(i)), key=str)[:limit]
        return [row for key in keys for row in self._drop_item(key)]

    async def erase_for(self, scope: ErasureScope, *, limit: int) -> list[ErasedRow]:
        if scope.event_ids:
            return self._erase_evidence(set(scope.event_ids), limit)
        erased: list[ErasedRow] = []
        tails = [k for k in self._t("tails") if k[0] == scope.profile_id]
        if scope.kind is TargetKind.CONVERSATION:
            handles = set(scope.handle_ids)
            episodes = [
                k
                for k, (e, _) in self._t("episodes").items()
                if e.conversation_id == scope.conversation_id
                and (not handles or e.origin_handle_id in handles)
            ]
            values = [
                k
                for k, v in self._t("values").items()
                if v.conversation_id == scope.conversation_id
                and (not handles or v.origin_handle_id in handles)
            ]
            plan = [("episodes", episodes), ("settled_values", values), ("tail_summaries", tails)]
        else:
            handles = set(scope.handle_ids)
            plan = [
                ("fact_mentions", [m for m in self._t("mentions") if m[0] in handles]),
                ("facts", [k for k in self._t("facts") if k[0] in handles]),
                ("episodes", [k for k in self._t("episodes") if k[0] in handles]),
                ("open_items", [k for k in self._t("items") if k[0] in handles]),
                ("settled_values", [k for k in self._t("values") if k[0] in handles]),
                ("profile_attrs", [k for k in self._t("attrs") if k[0] in handles]),
                ("purpose_objections", [k for k in self._t("objections") if k[0] in handles]),
                ("tail_summaries", tails),
            ]
        for table, keys in plan:
            for key in sorted(keys, key=str)[: limit - len(erased)]:
                erased += self._drop_item(key) if table == "open_items" else [self._drop(table, key)]
        return erased

    async def untagged(self, *, handle_ids: Sequence[UUID] | None, limit: int) -> list[Untagged]:
        def wanted(origin: UUID, purposes: object) -> bool:
            return purposes is None and (handle_ids is None or origin in handle_ids)

        facts = [
            Untagged(
                ItemKey(PurposeKind.FACT, *k),
                f.category,
                event_ids=tuple(
                    sorted(
                        e
                        for o, i, e in self._t("mentions")
                        if (o, i) == k and (o, i, e) not in self._t("feedback")
                    )
                ),
            )
            for k, f in sorted(self._t("facts").items(), key=lambda kv: str(kv[0]))
            if wanted(f.origin_handle_id, f.purposes)
        ][:limit]
        episodes = [
            Untagged(
                ItemKey(PurposeKind.EPISODE, e.origin_handle_id, e.episode_id),
                e.normalized_category,
                session_id=e.session_id,
                source_id=e.source_id,
            )
            for _, (e, current) in sorted(self._t("episodes").items(), key=lambda kv: str(kv[0]))
            if current and wanted(e.origin_handle_id, e.purposes)
        ][:limit]
        items = [
            Untagged(
                ItemKey(PurposeKind.OPEN_ITEM, *k),
                session_id=_uuid(i.origin.get("session_id")),
                source_id=_uuid(i.origin.get("source_id")),
                event_ids=tuple(evidence_event_ids(i)),
            )
            for k, i in sorted(self._t("items").items(), key=lambda kv: str(kv[0]))
            if wanted(i.origin_handle_id, i.purposes)
        ][:limit]
        values = [
            Untagged(
                ItemKey(PurposeKind.VALUE, *k),
                session_id=v.session_id,
                source_id=v.source_id,
                episode_id=v.episode_id,
                event_ids=v.evidence_event_ids,
            )
            for k, v in sorted(self._t("values").items(), key=lambda kv: str(kv[0]))
            if wanted(v.origin_handle_id, v.purposes)
        ][:limit]
        return [*facts, *episodes, *items, *values]

    async def set_purposes(self, tagged: Sequence[Tagged]) -> None:
        for row in tagged:
            tags = row.tags if row.tags is not None else ()
            key = (row.key.origin_handle_id, row.key.item_id)
            if row.key.kind is PurposeKind.EPISODE:
                episodes = self._t("episodes")
                for k, (e, current) in list(episodes.items()):
                    if (e.origin_handle_id, e.episode_id) == key:
                        episodes[k] = (replace(e, purposes=tags), current)
                continue
            table = self._purpose_table(row.key.kind)
            if key in table:
                table[key] = replace(table[key], purposes=tags)

    async def untag_empty(self, *, limit: int) -> int:
        reset = 0
        for kind in PurposeKind:
            for key, record in self._purpose_rows(kind):
                if reset >= limit:
                    return reset
                if record.purposes == ():
                    self._clear_tags(kind, key)
                    reset += 1
        return reset

    async def purpose_expired(self, cutoffs: Mapping[str, datetime], *, limit: int) -> list[ItemKey]:
        found: list[ItemKey] = []
        for kind in PurposeKind:
            for key, record in self._purpose_rows(kind):
                anchor = _anchor(kind, record)
                tags = record.purposes
                if not tags or anchor is None:
                    continue
                if all(t.purpose in cutoffs and anchor < cutoffs[t.purpose] for t in tags):
                    found.append(ItemKey(kind, *key[:2]))
        return sorted(set(found), key=str)[:limit]

    async def tagged_with(self, handle_ids: Sequence[UUID], purpose: str) -> list[Tagged]:
        handles = set(handle_ids)
        found = {
            ItemKey(kind, *key[:2]): record.purposes
            for kind in PurposeKind
            for key, record in self._purpose_rows(kind)
            if key[0] in handles and record.purposes and any(t.purpose == purpose for t in record.purposes)
        }
        return [Tagged(k, found[k]) for k in sorted(found, key=str)]

    async def remove_items(self, keys: Sequence[ItemKey]) -> list[ErasedRow]:
        erased: list[ErasedRow] = []
        for item in sorted(set(keys), key=str):
            key = (item.origin_handle_id, item.item_id)
            if item.kind is PurposeKind.FACT:
                erased += [
                    self._drop("fact_mentions", m)
                    for m in sorted(self._t("mentions"), key=str)
                    if m[:2] == key
                ]
                if key in self._t("facts"):
                    erased.append(self._drop("facts", key))
            elif item.kind is PurposeKind.EPISODE:
                erased += [
                    self._drop("episodes", k) for k in sorted(self._t("episodes"), key=str) if k[:2] == key
                ]
            elif item.kind is PurposeKind.OPEN_ITEM and key in self._t("items"):
                erased.append(self._drop("open_items", key))
            elif item.kind is PurposeKind.VALUE and key in self._t("values"):
                erased.append(self._drop("settled_values", key))
        return erased

    async def add_objection(self, handle_ids: Sequence[UUID], purpose: str, at: datetime) -> None:
        for handle_id in handle_ids:
            self._t("objections").setdefault((handle_id, purpose), at)

    async def objections(self, handle_ids: Sequence[UUID]) -> dict[UUID, frozenset[str]]:
        found: dict[UUID, set[str]] = {}
        for handle_id, purpose, _ in await self.objection_list(handle_ids):
            found.setdefault(handle_id, set()).add(purpose)
        return {h: frozenset(p) for h, p in found.items()}

    async def objection_list(self, handle_ids: Sequence[UUID]) -> list[tuple[UUID, str, datetime]]:
        handles = set(handle_ids)
        return sorted(
            ((h, p, at) for (h, p), at in self._t("objections").items() if h in handles),
            key=lambda row: (str(row[0]), row[1]),
        )

    async def objected_handles(self, *, after: tuple[UUID, str] | None, limit: int) -> list[tuple[UUID, str]]:
        keys = sorted(self._t("objections"), key=lambda k: (str(k[0]), k[1]))
        if after is not None:
            keys = [k for k in keys if (str(k[0]), k[1]) > (str(after[0]), after[1])]
        return keys[:limit]

    async def purpose_state(self, key: str) -> int | None:
        value: int | None = self._t("purpose_state").get(key)
        return value

    async def set_purpose_state(self, key: str, value: int) -> None:
        self._t("purpose_state")[key] = value

    def _purpose_table(self, kind: PurposeKind) -> Any:
        return {PurposeKind.FACT: self._t("facts"), PurposeKind.OPEN_ITEM: self._t("items")}.get(
            kind, self._t("values")
        )

    def _purpose_rows(self, kind: PurposeKind) -> list[tuple[tuple[Any, ...], Any]]:
        if kind is PurposeKind.EPISODE:
            return [
                ((e.origin_handle_id, e.episode_id), e)
                for _, (e, current) in sorted(self._t("episodes").items(), key=lambda kv: str(kv[0]))
                if current
            ]
        return sorted(self._purpose_table(kind).items(), key=lambda kv: str(kv[0]))

    def _clear_tags(self, kind: PurposeKind, key: tuple[Any, ...]) -> None:
        if kind is PurposeKind.EPISODE:
            episodes = self._t("episodes")
            for k, (e, current) in list(episodes.items()):
                if (e.origin_handle_id, e.episode_id) == key:
                    episodes[k] = (replace(e, purposes=None), current)
            return
        table = self._purpose_table(kind)
        table[key] = replace(table[key], purposes=None)

    async def export_rows(
        self, table: str, *, after_seq: int, upto_seq: int, limit: int
    ) -> list[dict[str, Any]]:
        if table not in ("facts", "fact_mentions", "episodes", "open_items"):
            raise ValueError(f"memory does not export {table}")
        rows = []
        for seq, key in self._seq.between(table, after_seq, upto_seq, limit):
            if table == "facts":
                rows.append(export_row(self._t("facts")[key], seq, drop=("evidence_event_ids",)))
            elif table == "episodes":
                episode, current = self._t("episodes")[key]
                rows.append({**export_row(episode, seq), "is_current": current})
            elif table == "open_items":
                rows.append(export_row(self._t("items")[key], seq))
            else:
                rows.append(
                    {"origin_handle_id": key[0], "fact_id": key[1], "event_id": key[2], "change_seq": seq}
                )
        return rows

    async def facts_page(self, after: UUID | None, limit: int) -> list[Fact]:
        facts = sorted(self._t("facts").values(), key=lambda f: f.fact_id)
        return [self._with_evidence(f) for f in facts if after is None or f.fact_id > after][:limit]

    async def episodes_page(self, after: tuple[UUID, int] | None, limit: int) -> list[Episode]:
        episodes = sorted(
            (e for e, _ in self._t("episodes").values()), key=lambda e: (e.episode_id, e.version)
        )
        return [e for e in episodes if after is None or (e.episode_id, e.version) > after][:limit]

    async def facts_by_ids(self, fact_ids: Sequence[UUID]) -> list[Fact]:
        wanted = set(fact_ids)
        facts = sorted((f for f in self._t("facts").values() if f.fact_id in wanted), key=lambda f: f.fact_id)
        return [self._with_evidence(f) for f in facts]

    async def items_by_ids(self, item_ids: Sequence[UUID]) -> list[OpenItem]:
        wanted = set(item_ids)
        return sorted((i for i in self._t("items").values() if i.item_id in wanted), key=lambda i: i.item_id)

    async def episodes_by_ids(self, episode_ids: Sequence[UUID]) -> list[Episode]:
        wanted = set(episode_ids)
        found = (e for e, _ in self._t("episodes").values() if e.episode_id in wanted)
        return sorted(found, key=lambda e: (e.episode_id, e.version))

    async def tails_page(
        self, after: tuple[UUID, str] | None, limit: int
    ) -> list[tuple[UUID, str, TailState]]:
        tails = sorted(
            ((p, level, state) for (p, level), state in self._t("tails").items()),
            key=lambda t: (str(t[0]), t[1]),
        )
        return [t for t in tails if after is None or (str(t[0]), t[1]) > (str(after[0]), after[1])][:limit]

    async def items_page(self, after: UUID | None, limit: int) -> list[OpenItem]:
        items = sorted(self._t("items").values(), key=lambda i: i.item_id)
        return [i for i in items if after is None or i.item_id > after][:limit]

    async def values_page(self, after: UUID | None, limit: int) -> list[SettledValue]:
        values = sorted(self._t("values").values(), key=lambda v: v.value_id)
        return [v for v in values if after is None or v.value_id > after][:limit]

    async def values_by_ids(self, value_ids: Sequence[UUID]) -> list[SettledValue]:
        wanted = set(value_ids)
        return [v for v in self._t("values").values() if v.value_id in wanted]

    async def rewrite_value(self, value: SettledValue) -> None:
        if (key := (value.origin_handle_id, value.value_id)) in self._t("values"):
            self._t("values")[key] = value

    async def attrs_page(self, after: tuple[UUID, str, UUID] | None, limit: int) -> list[ProfileAttr]:
        keys = sorted(self._t("attrs"), key=lambda k: (str(k[0]), k[1], str(k[2])))
        if after is not None:
            mark = (str(after[0]), after[1], str(after[2]))
            keys = [k for k in keys if (str(k[0]), k[1], str(k[2])) > mark]
        return [self._t("attrs")[k] for k in keys[:limit]]

    async def rewrite_fact(self, fact: Fact, dedupe_value: str) -> bool:
        key = (fact.origin_handle_id, fact.predicate, normalize_value(dedupe_value))
        holder = self._t("dedupe").get(key)
        if holder is not None and holder != fact.fact_id:
            return False
        dedupe = self._t("dedupe")
        for old in [k for k, v in dedupe.items() if v == fact.fact_id]:
            del dedupe[old]
        dedupe[key] = fact.fact_id
        row = (fact.origin_handle_id, fact.fact_id)
        self._t("facts")[row] = replace(fact, evidence_event_ids=())
        self._t("vectors").pop(("facts", *row), None)
        self._seq.touch("facts", row)
        return True

    async def rewrite_episode(self, episode: Episode) -> None:
        episodes = self._t("episodes")
        key = (episode.origin_handle_id, episode.episode_id, episode.version)
        if key in episodes:
            episodes[key] = (episode, episodes[key][1])
            self._t("vectors").pop(("episodes", *key), None)
            self._seq.touch("episodes", key)

    async def rewrite_item(self, item: OpenItem) -> None:
        key = (item.origin_handle_id, item.item_id)
        if key in self._t("items"):
            stored = self._t("items")[key]
            self._t("items")[key] = replace(
                stored, description=item.description, expected_operation=item.expected_operation
            )
            self._t("vectors").pop(("open_items", *key), None)
            self._seq.touch("open_items", key)

    async def restate_item(self, item: OpenItem, *, reworded: bool = False) -> None:
        key = (item.origin_handle_id, item.item_id)
        if key in self._t("items"):
            if reworded:
                self._t("vectors").pop(("open_items", *key), None)
            self._t("items")[key] = replace(
                self._t("items")[key],
                description=item.description,
                due_at=item.due_at,
                object=item.object,
                expected_operation=item.expected_operation,
                about_handle_id=item.about_handle_id,
                kind=item.kind,
                audience=item.audience,
            )
            self._seq.touch("open_items", key)

    async def remove_attr(self, attr: ProfileAttr) -> None:
        self._t("attrs").pop((attr.origin_handle_id, attr.slot, attr.source_id), None)

    def _feedback_events(self) -> set[UUID]:
        return {e for _, _, e in self._t("feedback")}

    def _evidence_of(self, facts: set[tuple[UUID, UUID]]) -> dict[tuple[UUID, UUID], set[UUID]]:
        evidence: dict[tuple[UUID, UUID], set[UUID]] = {}
        for o, f, e in self._t("mentions"):
            if (o, f) in facts:
                evidence.setdefault((o, f), set()).add(e)
        return evidence

    def _erase_evidence(self, events: set[UUID], limit: int) -> list[ErasedRow]:
        feedback = self._feedback_events()
        evidence = self._evidence_of({(o, f) for o, f, e in self._t("mentions") if e in events})
        plan = plan_evidence_erasure(evidence, events.__contains__, limit, lambda e: e not in feedback)
        erased = [self._drop("fact_mentions", (o, f, e)) for (o, f), e in plan.evidence]
        erased += [self._drop("facts", key) for key in plan.items]
        # Rows only the feedback of the facts just erased holds (their corrections) follow them.
        gone = set(plan.items)
        named = {e for key, e in plan.evidence if key in gone and e in feedback}
        seen = set(named)
        while named:
            others = self._evidence_of({(o, f) for o, f, e in self._t("mentions") if e in named})
            doomed = held_by_feedback_only(others, feedback.__contains__)
            named = {e for key in doomed for e in others[key]} - seen
            seen |= named
            for key in doomed:
                erased += [self._drop("fact_mentions", (*key, e)) for e in sorted(others[key], key=str)]
                erased.append(self._drop("facts", key))
        items = [k for k, i in self._t("items").items() if events & set(evidence_event_ids(i))]
        for key in sorted(items, key=str)[: max(limit - len(erased), 0)]:
            erased += self._drop_item(key)
        values = [k for k, v in self._t("values").items() if events & set(v.evidence_event_ids)]
        for key in sorted(values, key=str)[: max(limit - len(erased), 0)]:
            erased.append(self._drop("settled_values", key))
        return erased

    def _drop_item(self, key: tuple[UUID, UUID]) -> list[ErasedRow]:
        """The item, and a row per feedback event that names it (the one that resolved it and the
        resolutions it refused), as the PostgreSQL adapter."""
        erased = [self._drop("open_items", key)]
        events = {e for e in [self._t("item_feedback").pop(key, None)] if e is not None}
        refused = {pair for pair in self._t("item_refusals") if pair[0] == key[1]}
        self._t("item_refusals").difference_update(refused)
        events |= {e for _, e in refused}
        erased += [ErasedRow(OPEN_ITEM_FEEDBACK, {"event_id": str(e)}) for e in sorted(events, key=str)]
        return erased

    def _drop(self, table: str, key: Any) -> ErasedRow:
        bound = _bound_ref(table, self._t("facts").get(key) or self._t("items").get(key))
        if table == "fact_mentions":
            self._t("mentions").discard(key)
            self._t("feedback").discard(key)
            names: tuple[str, ...] = ("origin_handle_id", "fact_id", "event_id")
        elif table == "facts":
            del self._t("facts")[key]
            dedupe = self._t("dedupe")
            for slot in [k for k, fact_id in dedupe.items() if (k[0], fact_id) == key]:
                del dedupe[slot]
            self._t("relations").difference_update(
                {
                    r
                    for r in self._t("relations")
                    if key in ((r.a.origin_handle_id, r.a.id), (r.b.origin_handle_id, r.b.id))
                }
            )
            names = ("origin_handle_id", "fact_id")
        elif table == "episodes":
            del self._t("episodes")[key]
            names = ("origin_handle_id", "episode_id", "version")
        elif table == "open_items":
            del self._t("items")[key]
            names = ("origin_handle_id", "item_id")
        elif table == "profile_attrs":
            del self._t("attrs")[key]
            names = ("origin_handle_id", "slot", "source_id")
        elif table == "settled_values":
            del self._t("values")[key]
            names = ("origin_handle_id", "value_id")
        elif table == "purpose_objections":
            del self._t("objections")[key]
            names = ("handle_id", "purpose")
        else:
            del self._t("tails")[key]
            names = ("profile_id", "level")
        # A vector lives in its row, so it goes with it (5.14).
        self._t("vectors").pop((table, *key), None)
        seq = self._seq.forget(table, key)
        return ErasedRow(
            table, {n: str(v) for n, v in zip(names, key, strict=True)}, change_seq=seq, object_ref=bound
        )

    def _with_evidence(self, fact: Fact) -> Fact:
        events = sorted(
            e for o, f, e in self._t("mentions") if (o, f) == (fact.origin_handle_id, fact.fact_id)
        )
        return replace(fact, evidence_event_ids=tuple(events))


def _uuid(value: object) -> UUID | None:
    try:
        return UUID(str(value)) if value else None
    except ValueError:
        return None


def _anchor(kind: PurposeKind, record: Any) -> datetime | None:
    if kind is PurposeKind.FACT:
        return record.last_confirmed_at  # type: ignore[no-any-return]
    if kind is PurposeKind.EPISODE:
        return record.ended_at  # type: ignore[no-any-return]
    if kind is PurposeKind.OPEN_ITEM:
        return record.closed_at  # type: ignore[no-any-return]
    return record.said_at  # type: ignore[no-any-return]


def _bound_ref(table: str, row: Any) -> str | None:
    """The object a fact or an item bound to one was about, as the receipt names it."""
    key = row.object_ref if table == "facts" else row.object if table == "open_items" else None
    if key is None or row.audience is None:
        return None
    return f"{key.type}:{key.namespace}:{key.external_id}"
