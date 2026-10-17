"""The index of mentions in memory: the database keeps each row's keyed-hashed tokens as its sealer writes
it; the in-memory store keeps its rows in the clear, so the rows that name the target are read from them by
the same tokens, unhashed. What is sealed outside a repository (a subject's signals, turn recordings, the
cold archive) is indexed as written, by its keyed hashes, as in the database."""

from collections.abc import Collection, Iterator, Sequence
from typing import Any

import orjson

from niadra.adapters.memory.store import MemoryStore
from niadra.domain.privacy.mentions import MentionKey, MentionStore, by_key
from niadra.domain.privacy.raw_index import body_tokens
from niadra.domain.state.observed import named_text


def index_in[K](
    kept: dict[K, frozenset[bytes]], entries: Sequence[tuple[K, frozenset[bytes]]], *, replace: bool
) -> None:
    """Each entry's tokens added to what its row or object named, or instead of it with `replace`; one that
    names no one then leaves."""
    for key, tokens in by_key(entries):
        merged = tokens if replace else tokens | kept.get(key, frozenset())
        if merged:
            kept[key] = merged
        else:
            kept.pop(key, None)


def mentions_of(store: MemoryStore) -> dict[str, Any]:
    """The index's tables in the store, for the repositories that write an entry with their own rows."""
    module = store.module("mentions")
    module.setdefault("rows", {})
    module.setdefault("objects", {})
    module.setdefault("ready", set())
    return module


class InMemoryMentionRepository:
    def __init__(self, store: MemoryStore) -> None:
        self._store = store
        self._s = mentions_of(store)

    async def index(
        self, entries: Sequence[tuple[MentionKey, frozenset[bytes]]], *, replace: bool = False
    ) -> None:
        index_in(self._s["rows"], entries, replace=replace)

    async def drop(self, keys: Sequence[MentionKey]) -> None:
        for key in keys:
            self._s["rows"].pop(key, None)

    async def naming(
        self,
        tokens: Sequence[bytes],
        plain: frozenset[str],
        *,
        stores: Collection[str],
        after: MentionKey | None,
        limit: int,
    ) -> list[MentionKey]:
        wanted = set(tokens)
        rows: dict[MentionKey, frozenset[bytes]] = self._s["rows"]
        found = sorted(
            {
                *(key for key, text in self._texts() if text and body_tokens(text) & plain),
                *(key for key, indexed in rows.items() if indexed & wanted),
            }
        )
        return [k for k in found if k.store in stores and (after is None or k > after)][:limit]

    async def index_objects(
        self, entries: Sequence[tuple[str, frozenset[bytes]]], *, replace: bool = True
    ) -> None:
        index_in(self._s["objects"], entries, replace=replace)

    async def drop_objects(self, keys: Sequence[str]) -> None:
        for key in keys:
            self._s["objects"].pop(key, None)

    async def objects_naming(
        self, tokens: Sequence[bytes], *, prefix: str, after: str | None, limit: int
    ) -> list[str]:
        wanted = set(tokens)
        objects: dict[str, frozenset[bytes]] = self._s["objects"]
        found = sorted(
            k
            for k, t in objects.items()
            if k.startswith(prefix) and t & wanted and (after is None or k > after)
        )
        return found[:limit]

    async def ready(self, key: str) -> bool:
        return key in self._s["ready"]

    async def mark_ready(self, key: str) -> None:
        self._s["ready"].add(key)

    def _texts(self) -> Iterator[tuple[MentionKey, bytes]]:
        memory: dict[str, Any] = self._store.module("memory")
        for fact in memory.get("facts", {}).values():
            yield MentionKey(MentionStore.FACT, str(fact.fact_id)), fact.value.encode()
        for episode, _ in memory.get("episodes", {}).values():
            both = f"{episode.summary}\n{episode.resolution or ''}"
            yield MentionKey(MentionStore.EPISODE, str(episode.episode_id)), both.encode()
        for item in memory.get("items", {}).values():
            yield MentionKey(MentionStore.OPEN_ITEM, str(item.item_id)), item.description.encode()
        for (handle, slot, source), attr in memory.get("attrs", {}).items():
            yield MentionKey(MentionStore.ATTR, f"{handle}:{slot}:{source}"), attr.value.encode()
        for (profile, level), state in memory.get("tails", {}).items():
            yield MentionKey(MentionStore.SUMMARY, f"{profile}:{level}"), state.text.encode()
        for note in self._store.module("agent_memory").get("notes", {}).values():
            text = f"{note.draft.title}\n{note.draft.body}"
            yield MentionKey(MentionStore.NOTE, str(note.note_id)), text.encode()
        for (session_id, versions_key), (run, _) in self._store.module("extract").get("runs", {}).items():
            if run.result is not None:
                key = MentionKey(MentionStore.RUN, f"{session_id}:{versions_key}")
                yield key, orjson.dumps(run.result.to_dict())
        for proposal in self._store.module("agent_memory").get("proposals", {}).values():
            text = f"{proposal.draft.title}\n{proposal.draft.body}"
            yield MentionKey(MentionStore.PROPOSAL, str(proposal.proposal_id)), text.encode()
        for value in memory.get("values", {}).values():
            yield MentionKey(MentionStore.SETTLED, str(value.value_id)), value.raw.encode()
        objects: dict[str, Any] = self._store.module("objects")
        for action in objects.get("actions", {}).values():
            if action.result is not None:
                yield MentionKey(MentionStore.ACTION, str(action.action_id)), action.result.encode()
        for obj in objects.get("objects", {}).values():
            yield MentionKey(MentionStore.OBJECT, str(obj.object_id)), named_text(obj.state, obj.fields)
        for (object_id, name, version), computed in objects.get("values", {}).items():
            if computed.value is not None:
                key = MentionKey(MentionStore.VALUE, f"{object_id}/{name}/{version}")
                yield key, orjson.dumps(computed.value, default=str)
