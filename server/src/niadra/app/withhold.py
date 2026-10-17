"""The pass that scrubs stored memory by the rules of `domain/extract/withhold.py`.

New extractions are withheld at validation. What earlier ones wrote under older rules stays until this pass
reaches it: whenever `WITHHOLD_VERSION` moves, every fact, episode version,
open item, settled value, profile slot and stored extraction result of the space is read once, in key
order, one page per task, and rewritten by the same rules. A row is decrypted because nothing else says
what it holds; the turns of its session are decrypted only when the row carries a number of five digits
or more, the one case where they decide. Nothing is logged but counts.

Bounded and idempotent: a page is at most `PAGE` rows, pages come `PAUSE` apart on the background queue,
and a scrubbed row scrubs to itself, so a retried or repeated page changes nothing. A row that is only a
document number, or sensitive content the schema does not collect, is deleted with a tombstone for the
export; the owners' packs, patterns and summaries are rebuilt from what is left.
"""

import dataclasses
import logging
import re
from collections.abc import Awaitable, Callable, Collection, Sequence
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any
from uuid import UUID

from niadra.config.models import SpaceConfig
from niadra.domain.common.errors import NotFound
from niadra.domain.extract.result import ExtractionRun
from niadra.domain.extract.rules import ExtractionRules
from niadra.domain.extract.withhold import (
    MIN_HELD_DIGITS,
    WITHHOLD_VERSION,
    held_numbers,
    misfiled,
    only_withheld,
    sensitive_home,
    withhold_result,
    withhold_text,
)
from niadra.domain.ingest.masking import language
from niadra.domain.memory.facts import normalize_value
from niadra.domain.memory.values import MARKER
from niadra.domain.pipeline.delay import DelayReason
from niadra.domain.policy.sensitive import sensitive_topics
from niadra.domain.privacy.erasure import ErasedRow
from niadra.domain.privacy.export import EXPORT_TABLES
from niadra.domain.privacy.purposes import ItemKey, ItemKind
from niadra.domain.records import Episode, EventRecord, Fact, OpenItem, SessionRecord
from niadra.domain.tasks import Task, TaskName
from niadra.ports.infra import Clock, ConfigProvider
from niadra.ports.uow import UnitOfWork, UnitOfWorkFactory

log = logging.getLogger(__name__)

STATE_KEY = "withhold_version"
PAGE = 200
PAUSE = timedelta(seconds=5)
KINDS = ("fact", "episode", "open_item", "value", "attr", "run")
RECOMPUTED = (TaskName.COMPILE, TaskName.DERIVE_TRAITS, TaskName.CONSOLIDATE)
_LONG_NUMBER = re.compile(rf"\d(?:[.\-/]?\d){{{MIN_HELD_DIGITS - 1},}}")

RulesFor = Callable[[SpaceConfig, UUID], ExtractionRules]
SessionTurns = Callable[[UnitOfWork, SessionRecord], Awaitable[list[EventRecord]]]


@dataclass
class _Page:
    """What one page changed: rewritten rows, deleted rows, and whose memory must be rebuilt."""

    rewritten: int = 0
    removed: list[ItemKey] = field(default_factory=list)
    attrs_removed: int = 0
    handles: set[UUID] = field(default_factory=set)
    last: Any = None
    read: int = 0


class WithholdPass:
    """Run by `ExtractService`, which lends it the extraction's rules and a session's turns."""

    def __init__(
        self,
        uow: UnitOfWorkFactory,
        config: ConfigProvider,
        clock: Clock,
        rules: RulesFor,
        turns: SessionTurns,
    ) -> None:
        self._uow = uow
        self._config = config
        self._clock = clock
        self._rules = rules
        self._turns = turns

    async def watch(self, space_id: UUID) -> int:
        """A per-space loop step: starts the pass when the space has not run this version of the rules."""
        try:
            self._config.space(space_id)
        except NotFound:
            return 0
        async with self._uow(space_id) as uow:
            if await uow.memory.purpose_state(STATE_KEY) == WITHHOLD_VERSION:
                return 0
            await uow.memory.set_purpose_state(STATE_KEY, WITHHOLD_VERSION)
            payload = {"version": WITHHOLD_VERSION, "kind": KINDS[0], "after": None, "changed": 0}
            await uow.pipeline.enqueue([Task(TaskName.WITHHOLD_STORED, space_id, payload)])
        return 1

    async def page(self, task: Task) -> None:
        """One page of one kind of row; the next page, or the next kind, comes `PAUSE` later."""
        space_id = task.space_id
        try:
            config = self._config.space(space_id)
        except NotFound:
            return
        if int(task.payload["version"]) != WITHHOLD_VERSION:
            return  # newer rules started their own pass
        kind = str(task.payload["kind"])
        after = task.payload.get("after")
        changed = int(task.payload.get("changed", 0))
        async with self._uow.system(space_id) as uow:
            done = await _SCRUBBERS[kind](_Scrub(uow, config, self._rules, self._turns), after)
            if done.removed:
                rows = await uow.memory.remove_items(done.removed)
                await _tombstones(uow, space_id, rows, self._clock)
            await _rebuild(uow, space_id, done.handles)
            changed += done.rewritten + len(done.removed) + done.attrs_removed
            following: dict[str, Any] | None = None
            if done.read == PAGE:
                following = {**task.payload, "after": done.last, "changed": changed}
            elif KINDS.index(kind) + 1 < len(KINDS):
                following = {**task.payload, "kind": KINDS[KINDS.index(kind) + 1], "after": None}
                following["changed"] = changed
            if following is None:
                log.info("withholding pass done: %d stored rows scrubbed", changed)
                return
            await uow.pipeline.schedule(
                Task(TaskName.WITHHOLD_STORED, space_id, following),
                self._clock.now() + PAUSE,
                DelayReason.DELAY,
            )


class _Scrub:
    """The rules applied to one space's stored rows, with the session turns each row may need."""

    def __init__(self, uow: UnitOfWork, config: SpaceConfig, rules: RulesFor, turns: SessionTurns) -> None:
        self.uow = uow
        self.config = config
        self._rules_for = rules
        self._turns = turns
        self.lang = language(config.extraction.language)
        self._rules: dict[UUID | None, ExtractionRules] = {}
        self._sessions: dict[UUID, dict[str, str]] = {}

    def rules(self, source_id: UUID | None = None) -> ExtractionRules:
        if source_id not in self._rules:
            self._rules[source_id] = self._rules_for(self.config, source_id or UUID(int=0))
        return self._rules[source_id]

    async def held_for_events(self, text: str, event_ids: Sequence[UUID]) -> dict[str, str]:
        if not _needs_turns(text) or not event_ids:
            return {}
        events = await self.uow.ingest.get_events(list(event_ids))
        return held_numbers(e.text for e in events if e.text)

    async def held_for_session(self, text: str, session_id: UUID | None) -> dict[str, str]:
        if not _needs_turns(text) or session_id is None:
            return {}
        if session_id not in self._sessions:
            session = await self.uow.sessions.get_session(session_id)
            events = await self._turns(self.uow, session) if session is not None else []
            self._sessions[session_id] = held_numbers(e.text for e in events if e.text)
        return self._sessions[session_id]

    def text(self, text: str, held: dict[str, str]) -> str:
        return withhold_text(text, held, self.lang).text


async def _facts(s: _Scrub, after: Any) -> _Page:
    page = _Page()
    facts = await s.uow.memory.facts_page(UUID(after) if after else None, PAGE)
    for fact in facts:
        fixed = await _fact(s, fact)
        if fixed is None:
            page.removed.append(ItemKey(ItemKind.FACT, fact.origin_handle_id, fact.fact_id))
        elif fixed != fact:
            if await s.uow.memory.rewrite_fact(fixed, normalize_value(fixed.value)):
                page.rewritten += 1
            else:  # the scrubbed fact is one the handle already holds
                page.removed.append(ItemKey(ItemKind.FACT, fact.origin_handle_id, fact.fact_id))
        else:
            continue
        page.handles.add(fact.origin_handle_id)
    return _read(page, facts, lambda f: str(f.fact_id))


async def _fact(s: _Scrub, fact: Fact) -> Fact | None:
    """The fact as memory may keep it, or None when it must go."""
    value = s.text(fact.value, await s.held_for_events(fact.value, fact.evidence_event_ids))
    if only_withheld(value):
        return None
    topics = misfiled(value, fact.sensitivity, fact.category)
    if not topics:
        return dataclasses.replace(fact, value=value)
    home = sensitive_home(s.rules(), topics, value)
    if home is None:
        return None
    return dataclasses.replace(
        fact,
        value=value,
        predicate=home.name,
        namespace=home.namespace,
        category=home.category,
        sensitivity=home.sensitivity,
    )


async def _episodes(s: _Scrub, after: Any) -> _Page:
    page = _Page()
    mark = (UUID(after[0]), int(after[1])) if after else None
    episodes = await s.uow.memory.episodes_page(mark, PAGE)
    for episode in episodes:
        fixed = await _episode(s, episode)
        if fixed != episode:
            await s.uow.memory.rewrite_episode(fixed)
            page.rewritten += 1
            page.handles.add(episode.origin_handle_id)
    return _read(page, episodes, lambda e: [str(e.episode_id), e.version])


async def _episode(s: _Scrub, episode: Episode) -> Episode:
    both = f"{episode.summary}\n{episode.resolution or ''}"
    held = await s.held_for_session(both, episode.session_id)
    summary = s.text(episode.summary, held)
    resolution = s.text(episode.resolution, held) if episode.resolution else episode.resolution
    return dataclasses.replace(episode, summary=summary, resolution=resolution)


async def _items(s: _Scrub, after: Any) -> _Page:
    page = _Page()
    items = await s.uow.memory.items_page(UUID(after) if after else None, PAGE)
    for item in items:
        held = await s.held_for_session(item.description, _session_of(item))
        description = s.text(item.description, held)
        if description == item.description:
            continue
        if only_withheld(description):
            page.removed.append(ItemKey(ItemKind.OPEN_ITEM, item.origin_handle_id, item.item_id))
        else:
            await s.uow.memory.rewrite_item(dataclasses.replace(item, description=description))
            page.rewritten += 1
        page.handles.add(item.origin_handle_id)
    return _read(page, items, lambda i: str(i.item_id))


async def _values(s: _Scrub, after: Any) -> _Page:
    page = _Page()
    values = await s.uow.memory.values_page(UUID(after) if after else None, PAGE)
    for value in values:
        held = await s.held_for_events(value.raw, value.evidence_event_ids)
        if s.text(value.raw, held) != value.raw:
            page.removed.append(ItemKey(ItemKind.VALUE, value.origin_handle_id, value.value_id))
            page.handles.add(value.origin_handle_id)
    return _read(page, values, lambda v: str(v.value_id))


async def _attrs(s: _Scrub, after: Any) -> _Page:
    page = _Page()
    mark = (UUID(after[0]), str(after[1]), UUID(after[2])) if after else None
    attrs = await s.uow.memory.attrs_page(mark, PAGE)
    for attr in attrs:
        value = s.text(attr.value, {})
        if only_withheld(value) or sensitive_topics(value):
            await s.uow.memory.remove_attr(attr)
            page.attrs_removed += 1
        elif value != attr.value:
            await s.uow.memory.put_attr(dataclasses.replace(attr, value=value))
            page.rewritten += 1
        else:
            continue
        page.handles.add(attr.origin_handle_id)
    return _read(page, attrs, lambda a: [str(a.origin_handle_id), a.slot, str(a.source_id)])


async def _runs(s: _Scrub, after: Any) -> _Page:
    page = _Page()
    runs = await s.uow.extract.runs_page((UUID(after[0]), str(after[1])) if after else None, PAGE)
    for run in runs:
        fixed = await _run(s, run)
        if fixed is not None:
            await s.uow.extract.rewrite_result(fixed)
            page.rewritten += 1
    return _read(page, runs, lambda r: [str(r.session_id), r.versions_key])


async def _run(s: _Scrub, run: ExtractionRun) -> ExtractionRun | None:
    if run.result is None:
        return None
    blob = str(run.result.to_dict())
    held = await s.held_for_session(blob, run.session_id)
    if not held:  # a session past the hot window: the turns the result cites still say what a number is
        held = await s.held_for_events(blob, _evidence(run))
    session = await s.uow.sessions.get_session(run.session_id)
    rules = s.rules(session.source_id if session else None)
    result = withhold_result(run.result, rules, held, s.lang)
    if result == run.result:
        return None
    added = result.dropped - run.result.dropped
    return dataclasses.replace(run, result=result, dropped_items=run.dropped_items + added)


_SCRUBBERS: dict[str, Callable[[_Scrub, Any], Awaitable[_Page]]] = {
    "fact": _facts,
    "episode": _episodes,
    "open_item": _items,
    "value": _values,
    "attr": _attrs,
    "run": _runs,
}


def _evidence(run: ExtractionRun) -> list[UUID]:
    result = run.result
    if result is None:
        return []
    cited = [
        *(e for f in result.facts for e in f.evidence_event_ids),
        *(e for i in result.open_items for e in i.evidence_event_ids),
        *(e for p in result.profile for e in p.evidence_event_ids),
        *(e for v in result.values for e in v.evidence_event_ids),
    ]
    return list(dict.fromkeys(cited))


def _read[T](page: _Page, rows: Sequence[T], key: Callable[[T], Any]) -> _Page:
    page.read = len(rows)
    page.last = key(rows[-1]) if rows else None
    return page


def _needs_turns(text: str | None) -> bool:
    """Only a long number outside a marker can be one the session's turns called a document."""
    return bool(text) and bool(_LONG_NUMBER.search(MARKER.sub(" ", text or "")))


def _session_of(item: OpenItem) -> UUID | None:
    raw = item.origin.get("session_id")
    try:
        return UUID(str(raw)) if raw else None
    except ValueError:
        return None


async def _tombstones(uow: UnitOfWork, space_id: UUID, rows: Sequence[ErasedRow], clock: Clock) -> None:
    exported = [r for r in rows if r.table in EXPORT_TABLES]
    if exported:
        await uow.privacy.add_tombstones(space_id, exported, clock.now())


async def _rebuild(uow: UnitOfWork, space_id: UUID, handles: Collection[UUID]) -> None:
    owners = {p.profile_id for h in handles if (p := await uow.identity.profile_of(h)) is not None}
    for profile_id in sorted(owners, key=str):
        for name in RECOMPUTED:
            await uow.pipeline.request_work(
                Task(name, space_id, {"reason": "withheld"}, profile_id=profile_id)
            )
