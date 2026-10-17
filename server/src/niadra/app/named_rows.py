"""What derived memory of others writes of an erased person (5.14.0): a fact, an episode's summary, an open
item, a profile slot, a profile's tail summary, an agent's note or note proposal, an extraction's result, an
agent action's result, a settled value, a business object's fields or one of its computed values that names
them by a phone, an e-mail, a document or an id keeps, with those replaced by `ERASED`.

A business object mirrors a record the company's system keeps: its copy here is redacted, the request counts
it (`in_source`) for the company to erase the record there, and the object keeps the keyed digests of what
was redacted (`Typed.erased`), so the next push of the same record does not bring the person back.

The rows are found by the index of mentions (`domain.privacy.mentions`), a page at a time, never by reading
the space; the rows written before the index existed are read into it once, by the first erasure. A rewritten
row loses its vector, which the next `embed` makes again from the redacted text; the owners' packs, traits and
summaries are compiled again by the privacy service. A row under a legal hold keeps as it is, and is counted.
"""

import dataclasses
import logging
from collections import defaultdict
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

import orjson

from niadra.app.raw_index import sealed_tokens
from niadra.domain.extract.result import ExtractionResult
from niadra.domain.memory.facts import normalize_value
from niadra.domain.objects.state import COORDINATION_NAMESPACE
from niadra.domain.privacy.erasure import ErasureScope
from niadra.domain.privacy.holds import Held
from niadra.domain.privacy.mentions import (
    MENTION_PAGE,
    MORE_MENTIONS_READY,
    TEXT_MENTIONS_READY,
    MentionKey,
    MentionStore,
    Redacted,
)
from niadra.domain.privacy.purposes import ItemKey, ItemKind
from niadra.domain.privacy.raw_index import body_tokens, redact_value
from niadra.domain.records import BusinessObject
from niadra.domain.state.observed import Holding, Observation, Previous, named_text
from niadra.domain.turns.storage import HMAC_PURPOSE as TURN_HMAC
from niadra.ports.infra import KeyVault
from niadra.ports.uow import UnitOfWork, UnitOfWorkFactory

log = logging.getLogger(__name__)

_ID = 36
"""A UUID's length as text: the first part of a composite row key."""


@dataclass
class _Pass:
    """One page of rows that name the target, as one transaction rewrites them."""

    uow: UnitOfWork
    keys: KeyVault
    space_id: UUID
    plain: frozenset[str]
    scope: ErasureScope
    held: Held
    tokens: frozenset[bytes] = frozenset()
    done: Redacted = field(default_factory=Redacted)
    texts: dict[MentionKey, str] = field(default_factory=dict)
    gone: list[MentionKey] = field(default_factory=list)

    def redact(self, text: str | None) -> tuple[str | None, int]:
        return redact_value(text, self.plain) if text else (text, 0)

    def own(self, handle_id: UUID) -> bool:
        return handle_id in self.scope.handle_ids

    def handle_held(self, handle_id: UUID) -> bool:
        return handle_id in self.held.handles

    async def profile_held(self, profile_id: UUID | None) -> bool:
        if profile_id is None or not self.held.handles:
            return False
        profile = await self.uow.identity.get_profile(profile_id)
        return profile is not None and any(h in self.held.handles for h in profile.handle_ids)

    async def conversation_held(self, conversation_id: str | None) -> bool:
        if not conversation_id or not self.held.conversations:
            return False
        digest = await self.keys.hmac(self.space_id, TURN_HMAC, conversation_id.encode())
        return digest in self.held.conversations

    def kept(self, key: MentionKey, text: str) -> None:
        """What the row writes now, for its index entry."""
        self.texts[key] = text


async def _facts(p: _Pass, rows: Sequence[str]) -> None:
    found = {str(f.fact_id): f for f in await p.uow.memory.facts_by_ids([UUID(r) for r in rows])}
    for row in rows:
        key, fact = MentionKey(MentionStore.FACT, row), found.get(row)
        if fact is None:
            p.gone.append(key)
            continue
        if p.own(fact.origin_handle_id):
            continue
        value, named = p.redact(fact.value)
        if not named or value is None:
            p.kept(key, fact.value)
            continue
        if p.handle_held(fact.origin_handle_id):
            p.done.held += 1
            continue
        if await p.uow.memory.rewrite_fact(dataclasses.replace(fact, value=value), normalize_value(value)):
            p.kept(key, value)
        else:  # the redacted fact is one its owner holds already
            await p.uow.memory.remove_items([ItemKey(ItemKind.FACT, fact.origin_handle_id, fact.fact_id)])
            p.gone.append(key)
        p.done.rows += 1
        p.done.handles.add(fact.origin_handle_id)


async def _episodes(p: _Pass, rows: Sequence[str]) -> None:
    versions: dict[str, list[Any]] = defaultdict(list)
    for episode in await p.uow.memory.episodes_by_ids([UUID(r) for r in rows]):
        versions[str(episode.episode_id)].append(episode)
    for row in rows:
        key, found = MentionKey(MentionStore.EPISODE, row), versions.get(row)
        if not found:
            p.gone.append(key)
            continue
        owner = found[0].origin_handle_id
        if p.own(owner):
            continue
        texts: list[str] = []
        fixed = []
        for episode in found:
            summary, in_summary = p.redact(episode.summary)
            resolution, in_resolution = p.redact(episode.resolution)
            texts += [summary or "", resolution or ""]
            if in_summary + in_resolution:
                fixed.append(dataclasses.replace(episode, summary=summary, resolution=resolution))
        if fixed and p.handle_held(owner):
            p.done.held += 1
            continue
        for episode in fixed:
            await p.uow.memory.rewrite_episode(episode)
        p.kept(key, "\n".join(texts))
        if fixed:
            p.done.rows += 1
            p.done.handles.add(owner)


async def _items(p: _Pass, rows: Sequence[str]) -> None:
    found = {str(i.item_id): i for i in await p.uow.memory.items_by_ids([UUID(r) for r in rows])}
    for row in rows:
        key, item = MentionKey(MentionStore.OPEN_ITEM, row), found.get(row)
        if item is None:
            p.gone.append(key)
            continue
        if p.own(item.origin_handle_id):
            continue
        description, named = p.redact(item.description)
        if not named or description is None:
            p.kept(key, item.description)
            continue
        if p.handle_held(item.origin_handle_id):
            p.done.held += 1
            continue
        await p.uow.memory.rewrite_item(dataclasses.replace(item, description=description))
        p.kept(key, description)
        p.done.rows += 1
        p.done.handles.add(item.origin_handle_id)


async def _attrs(p: _Pass, rows: Sequence[str]) -> None:
    handles = sorted({UUID(r[:_ID]) for r in rows}, key=str)
    found = {f"{a.origin_handle_id}:{a.slot}:{a.source_id}": a for a in await p.uow.memory.attrs_for(handles)}
    for row in rows:
        key, attr = MentionKey(MentionStore.ATTR, row), found.get(row)
        if attr is None:
            p.gone.append(key)
            continue
        if p.own(attr.origin_handle_id):
            continue
        value, named = p.redact(attr.value)
        if not named or value is None:
            p.kept(key, attr.value)
            continue
        if p.handle_held(attr.origin_handle_id):
            p.done.held += 1
            continue
        await p.uow.memory.put_attr(dataclasses.replace(attr, value=value))
        p.kept(key, value)
        p.done.rows += 1
        p.done.handles.add(attr.origin_handle_id)


async def _summaries(p: _Pass, rows: Sequence[str]) -> None:
    for row in rows:
        key = MentionKey(MentionStore.SUMMARY, row)
        profile_id, level = UUID(row[:_ID]), row[_ID + 1 :]
        state = await p.uow.memory.tail_state(profile_id, level)
        if state is None:
            p.gone.append(key)
            continue
        if profile_id == p.scope.profile_id:
            continue
        text, named = p.redact(state.text)
        if not named or text is None:
            p.kept(key, state.text)
            continue
        if await p.profile_held(profile_id):
            p.done.held += 1
            continue
        await p.uow.memory.put_tail_summary(profile_id, level, dataclasses.replace(state, text=text))
        p.kept(key, text)
        p.done.rows += 1
        p.done.profiles.add(profile_id)


async def _notes(p: _Pass, rows: Sequence[str]) -> None:
    for row in rows:
        key = MentionKey(MentionStore.NOTE, row)
        note = await p.uow.agent_memory.get_note(UUID(row))
        if note is None:
            p.gone.append(key)
            continue
        title, in_title = p.redact(note.draft.title)
        body, in_body = p.redact(note.draft.body)
        if not in_title + in_body:
            p.kept(key, f"{note.draft.title}\n{note.draft.body}")
            continue
        if await p.conversation_held(note.evidence_conversation_id):
            p.done.held += 1
            continue
        draft = dataclasses.replace(note.draft, title=title or "", body=body or "")
        await p.uow.agent_memory.rewrite_note(dataclasses.replace(note, draft=draft))
        p.kept(key, f"{draft.title}\n{draft.body}")
        p.done.rows += 1


async def _runs(p: _Pass, rows: Sequence[str]) -> None:
    for row in rows:
        key = MentionKey(MentionStore.RUN, row)
        session_id, versions_key = UUID(row[:_ID]), row[_ID + 1 :]
        run = await p.uow.extract.get_run(session_id, versions_key)
        if run is None or run.result is None:
            p.gone.append(key)
            continue
        session = await p.uow.sessions.get_session(session_id)
        profile_id = session.profile_id if session is not None else None
        if profile_id is not None and profile_id == p.scope.profile_id:
            continue
        before = run.result.to_dict()
        after, named = redact_value(before, p.plain)
        if not named:
            p.kept(key, orjson.dumps(before).decode())
            continue
        if await p.profile_held(profile_id) or await p.conversation_held(
            session.conversation_id if session is not None else None
        ):
            p.done.held += 1
            continue
        try:
            result = ExtractionResult.from_dict(after)
        except (KeyError, TypeError, ValueError):  # an id field that was the identifier: the result keeps
            log.warning("an extraction result names an erased person where only an id fits; it keeps")
            p.kept(key, orjson.dumps(before).decode())
            continue
        await p.uow.extract.rewrite_result(dataclasses.replace(run, result=result))
        p.kept(key, orjson.dumps(after).decode())
        p.done.rows += 1
        if profile_id is not None:
            p.done.profiles.add(profile_id)


async def _proposals(p: _Pass, rows: Sequence[str]) -> None:
    for row in rows:
        key = MentionKey(MentionStore.PROPOSAL, row)
        proposal = await p.uow.agent_memory.get_proposal(UUID(row))
        if proposal is None:
            p.gone.append(key)
            continue
        title, in_title = p.redact(proposal.draft.title)
        body, in_body = p.redact(proposal.draft.body)
        if not in_title + in_body:
            p.kept(key, f"{proposal.draft.title}\n{proposal.draft.body}")
            continue
        if await p.conversation_held(proposal.evidence_conversation_id):
            p.done.held += 1
            continue
        draft = dataclasses.replace(proposal.draft, title=title or "", body=body or "")
        await p.uow.agent_memory.rewrite_proposal(dataclasses.replace(proposal, draft=draft))
        p.kept(key, f"{draft.title}\n{draft.body}")
        p.done.rows += 1


async def _settled(p: _Pass, rows: Sequence[str]) -> None:
    found = {str(v.value_id): v for v in await p.uow.memory.values_by_ids([UUID(r) for r in rows])}
    for row in rows:
        key, value = MentionKey(MentionStore.SETTLED, row), found.get(row)
        if value is None:
            p.gone.append(key)
            continue
        if p.own(value.origin_handle_id):
            continue
        raw, named = p.redact(value.raw)
        if not named or raw is None:
            p.kept(key, value.raw)
            continue
        if p.handle_held(value.origin_handle_id) or await p.conversation_held(value.conversation_id):
            p.done.held += 1
            continue
        await p.uow.memory.rewrite_value(dataclasses.replace(value, raw=raw))
        p.kept(key, raw)
        p.done.rows += 1
        p.done.handles.add(value.origin_handle_id)


async def _actions(p: _Pass, rows: Sequence[str]) -> None:
    found = {str(a.action_id): a for a in await p.uow.objects.actions_by_ids([UUID(r) for r in rows])}
    objects = {
        o.object_id: o
        for o in await p.uow.objects.get_objects_by_id(
            sorted({a.object_id for a in found.values() if a.object_id is not None}, key=str)
        )
    }
    for row in rows:
        key, action = MentionKey(MentionStore.ACTION, row), found.get(row)
        if action is None or action.result is None:
            p.gone.append(key)
            continue
        on = objects.get(action.object_id) if action.object_id is not None else None
        owner = action.closes_handle_id or (on.origin_handle_id if on is not None else None)
        if owner is not None and p.own(owner):
            continue  # the target's own: the cascade takes it
        result, named = p.redact(action.result)
        if not named or result is None:
            p.kept(key, action.result)
            continue
        if owner is not None and p.handle_held(owner):
            p.done.held += 1
            continue
        await p.uow.objects.rewrite_result(dataclasses.replace(action, result=result))
        p.kept(key, result)
        p.done.rows += 1
        if owner is not None:
            p.done.handles.add(owner)


async def _objects(p: _Pass, rows: Sequence[str]) -> None:
    found = {str(o.object_id): o for o in await p.uow.objects.get_objects_by_id([UUID(r) for r in rows])}
    for row in rows:
        key, obj = MentionKey(MentionStore.OBJECT, row), found.get(row)
        if obj is None:
            p.gone.append(key)
            continue
        owner = obj.origin_handle_id
        if owner is not None and p.own(owner):
            continue
        state, in_state = redact_value(obj.state, p.plain)
        fields, in_fields = _holding(obj.fields, p.plain)
        if not in_state + in_fields:
            p.kept(key, named_text(obj.state, obj.fields).decode())
            continue
        if owner is not None and p.handle_held(owner):
            p.done.held += 1
            continue
        erased = obj.typed.erased | {t.hex() for t in p.tokens}
        redacted = dataclasses.replace(
            obj, state=state, fields=fields, typed=dataclasses.replace(obj.typed, erased=frozenset(erased))
        )
        await p.uow.objects.rewrite_state(redacted, _unmatched(obj, state))
        p.kept(key, named_text(state, fields).decode())
        p.done.rows += 1
        p.done.in_source += obj.key.namespace != COORDINATION_NAMESPACE
        if owner is not None:
            p.done.handles.add(owner)


async def _values(p: _Pass, rows: Sequence[str]) -> None:
    wanted = {row: _value_row(row) for row in rows}
    found = await p.uow.typed.values_named([w for w in wanted.values() if w is not None])
    owners = {
        o.object_id: o.origin_handle_id
        for o in await p.uow.objects.get_objects_by_id(
            sorted({w[0] for w in wanted.values() if w is not None}, key=str)
        )
    }
    for row, at in wanted.items():
        key = MentionKey(MentionStore.VALUE, row)
        if at is None or at not in found:
            p.gone.append(key)
            continue
        owner = owners.get(at[0])
        if owner is not None and p.own(owner):
            continue
        value, named = redact_value(found[at], p.plain)
        if not named:
            p.kept(key, orjson.dumps(found[at], default=str).decode())
            continue
        if owner is not None and p.handle_held(owner):
            p.done.held += 1
            continue
        await p.uow.typed.rewrite_value(*at, value)
        p.kept(key, orjson.dumps(value, default=str).decode())
        p.done.rows += 1
        if owner is not None:
            p.done.handles.add(owner)


def _holding(fields: Holding, plain: frozenset[str]) -> tuple[dict[str, dict[str, Observation]], int]:
    """Each source's observation with the target redacted out of its value and the value it replaced."""
    out: dict[str, dict[str, Observation]] = {}
    found = 0
    for name, by in fields.items():
        out[name] = {}
        for source, o in by.items():
            value, in_value = redact_value(o.value, plain)
            was, in_was = redact_value(o.was.value, plain) if o.was is not None else (None, 0)
            if in_value + in_was:
                o = dataclasses.replace(o, value=value, was=Previous(was) if o.was is not None else None)
            out[name][source] = o
            found += in_value + in_was
    return out, found


def _unmatched(before: BusinessObject, state: dict[str, Any]) -> list[tuple[str, str]]:
    """(field, value) of each id a field held before its redaction: a reverse relation no longer finds the
    object by it."""
    pairs = set()
    for name, value in before.state.items():
        if state.get(name) == value:
            continue
        for held in value if isinstance(value, list) else [value]:
            if isinstance(held, str | int) and not isinstance(held, bool):
                pairs.add((name, str(held)))
    return sorted(pairs)


def _value_row(row: str) -> tuple[UUID, str, int] | None:
    """A computed value's row key, `<object id>/<name>/<version>`, as its ciphertext is bound to it."""
    object_id, _, rest = row.partition("/")
    name, _, version = rest.rpartition("/")
    try:
        return UUID(object_id), name, int(version)
    except ValueError:
        return None


_REDACTORS: dict[str, Callable[[_Pass, Sequence[str]], Awaitable[None]]] = {
    MentionStore.FACT: _facts,
    MentionStore.EPISODE: _episodes,
    MentionStore.OPEN_ITEM: _items,
    MentionStore.ATTR: _attrs,
    MentionStore.SUMMARY: _summaries,
    MentionStore.NOTE: _notes,
    MentionStore.RUN: _runs,
    MentionStore.PROPOSAL: _proposals,
    MentionStore.SETTLED: _settled,
    MentionStore.ACTION: _actions,
    MentionStore.OBJECT: _objects,
    MentionStore.VALUE: _values,
}


class NamedRowsService:
    """`NamedInText` for derived memory: the rows of others the index of mentions finds."""

    def __init__(self, uow: UnitOfWorkFactory, keys: KeyVault) -> None:
        self._uow = uow
        self._keys = keys

    async def redact_named(
        self, space_id: UUID, scope: ErasureScope, plain: frozenset[str], tokens: frozenset[bytes], held: Held
    ) -> Redacted:
        """A page of rows at a time, each page one transaction: the rows rewritten and their index entries
        replaced by what they write now. Repeating finds nothing left to redact."""
        await self._index_written_before(space_id)
        done = Redacted()
        after: MentionKey | None = None
        while True:
            async with self._uow(space_id) as uow:
                keys = await uow.mentions.naming(
                    sorted(tokens), plain, stores=list(_REDACTORS), after=after, limit=MENTION_PAGE
                )
                step = _Pass(uow, self._keys, space_id, plain, scope, held, tokens)
                by_store: dict[str, list[str]] = defaultdict(list)
                for key in keys:
                    by_store[key.store].append(key.row)
                for store, rows in sorted(by_store.items()):
                    if (redactor := _REDACTORS.get(store)) is not None:
                        await redactor(step, rows)
                await uow.mentions.drop(step.gone)
                await uow.mentions.index(
                    [(k, await self._tokens(space_id, t)) for k, t in sorted(step.texts.items())],
                    replace=True,
                )
            done.add(step.done)
            if len(keys) < MENTION_PAGE:
                return done
            after = keys[-1]

    async def _tokens(self, space_id: UUID, text: str) -> frozenset[bytes]:
        return await sealed_tokens(self._keys, space_id, body_tokens(text.encode()))

    async def _index_written_before(self, space_id: UUID) -> None:
        """Reads, once, the rows written before the index existed into it, a page per transaction, by the
        first erasure of the space; a row written since is indexed as it is sealed."""
        stages = (
            (
                TEXT_MENTIONS_READY,
                (
                    self._facts_page,
                    self._episodes_page,
                    self._items_page,
                    self._attrs_page,
                    self._tails_page,
                    self._notes_page,
                    self._runs_page,
                ),
            ),
            (
                MORE_MENTIONS_READY,
                (
                    self._proposals_page,
                    self._settled_page,
                    self._actions_page,
                    self._objects_page,
                    self._values_page,
                ),
            ),
        )
        for ready, pages in stages:
            async with self._uow(space_id) as uow:
                if await uow.mentions.ready(ready):
                    continue
            for page in pages:
                await self._backfill(space_id, page)
            async with self._uow(space_id) as uow:
                await uow.mentions.mark_ready(ready)

    async def _backfill(
        self,
        space_id: UUID,
        page: Callable[[UnitOfWork, Any], Awaitable[tuple[list[tuple[MentionKey, str]], Any]]],
    ) -> None:
        after: Any = None
        while True:
            async with self._uow(space_id) as uow:
                rows, last = await page(uow, after)
                entries = [(k, await self._tokens(space_id, t)) for k, t in rows if t]
                await uow.mentions.index([(k, t) for k, t in entries if t])
            if last is None:
                return
            after = last

    @staticmethod
    async def _facts_page(uow: UnitOfWork, after: Any) -> tuple[list[tuple[MentionKey, str]], Any]:
        facts = await uow.memory.facts_page(after, MENTION_PAGE)
        rows = [(MentionKey(MentionStore.FACT, str(f.fact_id)), f.value) for f in facts]
        return rows, facts[-1].fact_id if len(facts) == MENTION_PAGE else None

    @staticmethod
    async def _episodes_page(uow: UnitOfWork, after: Any) -> tuple[list[tuple[MentionKey, str]], Any]:
        episodes = await uow.memory.episodes_page(after, MENTION_PAGE)
        rows = [
            (MentionKey(MentionStore.EPISODE, str(e.episode_id)), f"{e.summary}\n{e.resolution or ''}")
            for e in episodes
        ]
        last = (episodes[-1].episode_id, episodes[-1].version) if len(episodes) == MENTION_PAGE else None
        return rows, last

    @staticmethod
    async def _items_page(uow: UnitOfWork, after: Any) -> tuple[list[tuple[MentionKey, str]], Any]:
        items = await uow.memory.items_page(after, MENTION_PAGE)
        rows = [(MentionKey(MentionStore.OPEN_ITEM, str(i.item_id)), i.description) for i in items]
        return rows, items[-1].item_id if len(items) == MENTION_PAGE else None

    @staticmethod
    async def _attrs_page(uow: UnitOfWork, after: Any) -> tuple[list[tuple[MentionKey, str]], Any]:
        attrs = await uow.memory.attrs_page(after, MENTION_PAGE)
        rows = [
            (MentionKey(MentionStore.ATTR, f"{a.origin_handle_id}:{a.slot}:{a.source_id}"), a.value)
            for a in attrs
        ]
        last = attrs[-1] if len(attrs) == MENTION_PAGE else None
        return rows, (last.origin_handle_id, last.slot, last.source_id) if last else None

    @staticmethod
    async def _tails_page(uow: UnitOfWork, after: Any) -> tuple[list[tuple[MentionKey, str]], Any]:
        tails = await uow.memory.tails_page(after, MENTION_PAGE)
        rows = [(MentionKey(MentionStore.SUMMARY, f"{p}:{level}"), s.text) for p, level, s in tails]
        return rows, (tails[-1][0], tails[-1][1]) if len(tails) == MENTION_PAGE else None

    @staticmethod
    async def _notes_page(uow: UnitOfWork, after: Any) -> tuple[list[tuple[MentionKey, str]], Any]:
        notes = await uow.agent_memory.list_notes(
            source_id=None, status=None, before_id=after, limit=MENTION_PAGE
        )
        rows = [
            (MentionKey(MentionStore.NOTE, str(n.note_id)), f"{n.draft.title}\n{n.draft.body}") for n in notes
        ]
        return rows, notes[-1].note_id if len(notes) == MENTION_PAGE else None

    @staticmethod
    async def _runs_page(uow: UnitOfWork, after: Any) -> tuple[list[tuple[MentionKey, str]], Any]:
        runs = await uow.extract.runs_page(after, MENTION_PAGE)
        rows = [
            (
                MentionKey(MentionStore.RUN, f"{r.session_id}:{r.versions_key}"),
                orjson.dumps(r.result.to_dict()).decode(),
            )
            for r in runs
            if r.result is not None
        ]
        return rows, (runs[-1].session_id, runs[-1].versions_key) if len(runs) == MENTION_PAGE else None

    @staticmethod
    async def _proposals_page(uow: UnitOfWork, after: Any) -> tuple[list[tuple[MentionKey, str]], Any]:
        proposals = await uow.agent_memory.list_proposals(
            source_id=None, status=None, before_id=after, limit=MENTION_PAGE
        )
        rows = [
            (MentionKey(MentionStore.PROPOSAL, str(p.proposal_id)), f"{p.draft.title}\n{p.draft.body}")
            for p in proposals
        ]
        return rows, proposals[-1].proposal_id if len(proposals) == MENTION_PAGE else None

    @staticmethod
    async def _settled_page(uow: UnitOfWork, after: Any) -> tuple[list[tuple[MentionKey, str]], Any]:
        values = await uow.memory.values_page(after, MENTION_PAGE)
        rows = [(MentionKey(MentionStore.SETTLED, str(v.value_id)), v.raw) for v in values]
        return rows, values[-1].value_id if len(values) == MENTION_PAGE else None

    @staticmethod
    async def _actions_page(uow: UnitOfWork, after: Any) -> tuple[list[tuple[MentionKey, str]], Any]:
        actions = await uow.objects.actions_page(after, MENTION_PAGE)
        rows = [(MentionKey(MentionStore.ACTION, str(a.action_id)), a.result or "") for a in actions]
        return rows, actions[-1].action_id if len(actions) == MENTION_PAGE else None

    @staticmethod
    async def _objects_page(uow: UnitOfWork, after: Any) -> tuple[list[tuple[MentionKey, str]], Any]:
        objects = await uow.objects.objects_page(after, MENTION_PAGE)
        rows = [
            (MentionKey(MentionStore.OBJECT, str(o.object_id)), named_text(o.state, o.fields).decode())
            for o in objects
        ]
        return rows, objects[-1].object_id if len(objects) == MENTION_PAGE else None

    @staticmethod
    async def _values_page(uow: UnitOfWork, after: Any) -> tuple[list[tuple[MentionKey, str]], Any]:
        values = await uow.typed.values_page(after, MENTION_PAGE)
        rows = [
            (MentionKey(MentionStore.VALUE, f"{o}/{name}/{version}"), orjson.dumps(v, default=str).decode())
            for (o, name, version), v in values
        ]
        return rows, values[-1][0] if len(values) == MENTION_PAGE else None
