"""History navigation (16): search, timeline and open, over the profile's own rows, no LLM (L3).

Same policy, same verification level and same receipts as `context()`, item by item; the control
group gets empty answers (19.7). Ranking is BM25 plus exact cosine fused by rank, and the answer is
cut by value with the compiler's knapsack, never by arrival order (16.5). The cosine uses the vectors
stored with the items; a search encodes only its question (16.2).
"""

import asyncio
import json
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

from niadra.app.compile import compile_settings, load_snapshot
from niadra.app.history_rows import HistoryRows, Missed, Recorded, Recording, Replay
from niadra.app.serve_reads import ReadSupport, Target
from niadra.config.models import SpaceConfig
from niadra.contracts.common import Handle, ObjectRef
from niadra.contracts.context import (
    ClosedBy,
    HistoryFilters,
    HistoryItem,
    ItemVersion,
    OpenedItem,
    Promise,
    Recurrence,
    SearchRequest,
    SearchResponse,
    TimelineRequest,
    TimelineResponse,
    TimeWindow,
    ToolDefinition,
)
from niadra.domain.common.errors import AboutWithoutLink, Forbidden, InvalidInput, NotFound
from niadra.domain.common.space import SourcePrincipal
from niadra.domain.compile import CompileInput, MemorySnapshot, Variant
from niadra.domain.compile.candidates import CandidateBuilder
from niadra.domain.compile.knapsack import Choice, select
from niadra.domain.compile.model import ItemKind, Section, public_item_id
from niadra.domain.compile.templates import Formatter
from niadra.domain.compile.tokens import estimate_tokens
from niadra.domain.identity.provenance import reader_view
from niadra.domain.memory.vectors import ItemVector
from niadra.domain.policy import Effect, PolicyContext, evaluate
from niadra.domain.policy.audience import claimed_kind, in_conversation
from niadra.domain.records import Episode, ObjectKey, ProfileRecord
from niadra.domain.serve.history import (
    HistoryDoc,
    HistoryFilter,
    after_cursor,
    during,
    encode_cursor,
    episode_changes,
    history_docs,
    newest_first,
    system_event_docs,
    timeline_kinds,
)
from niadra.domain.serve.holdout import Assignment, overdue_company_items
from niadra.domain.serve.recurrence import RECURRENCE_WINDOW_DAYS, recurrence
from niadra.domain.serve.relative_time import Window, read_when
from niadra.domain.serve.search import bm25, cosine, norm, ranking, reciprocal_rank_fusion
from niadra.domain.serve.tools import NAVIGATION_TOOLS, in_language
from niadra.domain.serve.where import parse_where
from niadra.domain.vocabulary import OpenItemStatus, ReceiptKind, ReceiptSurface, Scope, Verification
from niadra.ports.infra import Clock, ConfigProvider, KeyVault, ModelsClient
from niadra.ports.uow import UnitOfWork, UnitOfWorkFactory

SYSTEM_EVENTS_LIMIT = 200
VOICE_BUDGET = 300
TEXT_BUDGET = 800
_SCALE = 1_000_000


NOT_OPENED = "only conversations (episode) and objects (object) open; the other items are listed whole"
OPENED_KINDS = (ItemKind.EPISODE, ItemKind.OBJECT)


@dataclass(frozen=True, slots=True)
class _Visible:
    """The profile's rows the caller may see, after the policy; `withheld` counts the rest."""

    target: Target | None
    snapshot: MemorySnapshot | None
    docs: list[HistoryDoc]
    withheld: int
    group: str | None
    level: Verification
    account: MemorySnapshot | None = None
    """With `about`, the organization's memory the person's view shows (21.4), as the pack's account block."""
    about_unlinked: bool = False

    def merged(self) -> MemorySnapshot:
        """The subject's memory with the organization's beside it, for what an open looks up by id."""
        assert self.snapshot is not None
        mine, theirs = self.snapshot, self.account
        if theirs is None:
            return mine
        return replace(
            mine,
            facts=(*mine.facts, *theirs.facts),
            episodes=(*mine.episodes, *theirs.episodes),
            open_items=(*mine.open_items, *theirs.open_items),
            objects=(*mine.objects, *theirs.objects),
            actions=(*mine.actions, *theirs.actions),
        )


class HistoryNavigator:
    def __init__(
        self,
        uow: UnitOfWorkFactory,
        keys: KeyVault,
        models: ModelsClient,
        clock: Clock,
        config: ConfigProvider,
        reads: ReadSupport,
        generation: Callable[[UUID, UUID], Awaitable[int | None]],
    ) -> None:
        """`generation` reads a profile's current generation from the cache (None when it holds none)."""
        self._uow = uow
        self._generation = generation
        self._rows = HistoryRows()
        self._keys = keys
        self._models = models
        self._clock = clock
        self._config = config
        self._reads = reads

    async def search(self, caller: SourcePrincipal, request: SearchRequest) -> SearchResponse:
        caller, verification = _reading(
            caller, request.subject, request.conversation_id, request.verification
        )
        request = request.model_copy(update={"verification": verification})
        config = self._begin(caller)
        request = request.model_copy(update={"filters": in_zone(request.filters, config)})
        await self._reads.follow_feeds(caller.space.space_id)
        await self._reads.ensure_can_record()
        # The question is encoded while the rows load: the two waits overlap instead of adding up.
        encoding = asyncio.create_task(self._models.embed_query(request.query))
        try:
            visible, vectors, window = await self._search_rows(caller, config, request)
        except BaseException:
            _discard(encoding)
            raise
        accepts = _filter(_narrowed(request.filters, window), config).accepts
        docs = [d for d in visible.docs if accepts(d)]
        lexical = bm25(request.query, {d.id: d.text for d in docs})
        semantic, degraded = await self._semantic(encoding, docs, vectors)
        fused = reciprocal_rank_fusion([ranking(lexical), ranking(semantic)])
        budget = (
            request.max_tokens
            if "max_tokens" in request.model_fields_set
            else self._default_budget(caller, config)
        )
        by_id = {d.id: d for d in docs}
        chosen = select(
            [
                Choice(i, "results", estimate_tokens(by_id[i].text), round(score * _SCALE))
                for i, score in fused.items()
            ],
            budget,
            {},
        )
        ordered = [by_id[i] for i in ranking({i: fused[i] for i in chosen})][: request.limit]
        response = SearchResponse(
            items=[_item(d) for d in ordered],
            recurrence=self._recurrence(visible, ordered),
            withheld=visible.withheld,
            as_of=max((d.at for d in visible.docs), default=None),
            tokens_used=sum(estimate_tokens(d.text) for d in ordered),
            degraded=degraded,
            window=_window_out(window),
            ignored=["when"] if request.filters.when and window is None else [],
            about_unlinked=visible.about_unlinked,
        )
        query_hash = await self._keys.hmac(caller.space.space_id, "history_query", request.query.encode())
        await self._receipt(
            caller,
            visible,
            ReceiptSurface.SEARCH,
            [d.id for d in ordered],
            conversation_id=request.conversation_id,
            task_id=request.task_id,
            query_hash=query_hash,
            detail=await self._filters_detail(caller, request.filters),
        )
        return response

    async def timeline(self, caller: SourcePrincipal, request: TimelineRequest) -> TimelineResponse:
        caller, verification = _reading(
            caller, request.subject, request.conversation_id, request.verification
        )
        request = request.model_copy(update={"verification": verification})
        config = self._begin(caller)
        request = request.model_copy(update={"filters": in_zone(request.filters, config)})
        await self._reads.follow_feeds(caller.space.space_id)
        await self._reads.ensure_can_record()
        held = await self._reads.held(
            caller, request.verification, conversation_id=request.conversation_id, task_id=None
        )
        async with self._uow(caller.space.space_id) as uow:
            if request.subject is not None:
                target = await self._reads.resolve_subject(uow, request.subject)
            else:
                assert request.profile_id is not None
                target = await self._reads.resolve_profile(uow, request.profile_id)
            target, unlinked = await self._about(uow, target, request.about)
            visible = await self._visible(
                uow,
                caller,
                config,
                target,
                request.verification,
                request.conversation_id,
                None,
                held=held,
                show_expired=request.filters.show_expired,
                closed=_closed(request.filters),
            )
            visible = replace(visible, about_unlinked=unlinked)
            window = await self._window(uow, config, request.filters, request.conversation_id, None)
        return await self._page(
            caller, visible, request.filters, request.cursor, request.limit, request.conversation_id, window
        )

    async def timeline_for_profile(
        self,
        caller: SourcePrincipal,
        profile_id: UUID,
        *,
        cursor: str | None,
        limit: int,
        verification: Verification,
        conversation_id: str | None,
    ) -> TimelineResponse:
        caller, verification = _reading(caller, None, conversation_id, verification)
        config = self._begin(caller)
        await self._reads.follow_feeds(caller.space.space_id)
        await self._reads.ensure_can_record()
        held = await self._reads.held(caller, verification, conversation_id=conversation_id, task_id=None)
        async with self._uow(caller.space.space_id) as uow:
            target = await self._reads.resolve_profile(uow, profile_id)
            visible = await self._visible(
                uow, caller, config, target, verification, conversation_id, None, held=held
            )
        return await self._page(caller, visible, HistoryFilters(), cursor, limit, conversation_id)

    async def open_item(
        self,
        caller: SourcePrincipal,
        item_id: str,
        *,
        verification: Verification,
        conversation_id: str | None,
        profile_id: UUID | None,
        subject: Handle | None = None,
        about: Handle | None = None,
    ) -> OpenedItem:
        caller, verification = _reading(caller, subject, conversation_id, verification)
        config = self._begin(caller)
        await self._reads.follow_feeds(caller.space.space_id)
        await self._reads.ensure_can_record()
        try:
            ref = UUID(item_id)
        except ValueError:
            raise NotFound("item not found") from None
        if about is not None and subject is None:
            raise InvalidInput("`about` opens through the person's view: pass `subject` too")
        replayed = (
            await self._open_replayed(caller, config, ref, verification, conversation_id, profile_id, subject)
            if about is None
            else None
        )
        if replayed is not None:
            visible, opened = replayed
        else:
            visible, opened = await self._open_read(
                caller, config, ref, verification, conversation_id, profile_id, subject, about
            )
        await self._receipt(
            caller,
            visible,
            ReceiptSurface.OPEN,
            [opened.id, *(d.id for d in opened.derived), *(d.id for d in opened.timeline)],
            conversation_id=conversation_id,
        )
        return opened

    async def _open_replayed(
        self,
        caller: SourcePrincipal,
        config: SpaceConfig,
        ref: UUID,
        verification: Verification,
        conversation_id: str | None,
        profile_id: UUID | None,
        subject: Handle | None,
    ) -> "tuple[_Visible, OpenedItem] | None":
        """An open answered from the customer's rows a search or an open recorded (`app.history_rows`), under
        the same conditions as a search's replay: the customer behind the handle, the conversation's proof
        and the customer's generation known without the database. The rows go through the same policy at
        this read; an item they do not show goes to the database path, which answers for every id. None:
        read the database."""
        space_id = caller.space.space_id
        if subject is None or config.experiments:
            return None
        target = self._reads.cached_subject(space_id, subject)
        if target is None or (profile_id is not None and target.profile.profile_id != profile_id):
            return None
        level = self._reads.cached_verification(
            caller, verification, conversation_id=conversation_id, task_id=None, target=target
        )
        key = _rows_key(space_id, target, False)
        if level is None or key is None:
            return None
        generation = await self._generation(space_id, target.profile.profile_id)
        recorded = self._rows.get(key, generation, target.profile.privacy_version)
        if recorded is None:
            return None
        try:
            visible = await self._visible_rows(
                Replay(recorded), caller, config, target, level.effective, None, False, recorded
            )
        except Missed:
            return None
        shown = {d.id: d for d in visible.docs}
        doc = next((d for d in visible.docs if d.ref == ref), None)
        if doc is None or visible.snapshot is None:
            return None
        if doc.kind not in OPENED_KINDS:
            raise InvalidInput(NOT_OPENED)
        opened = self._open(visible, shown, doc)
        if doc.kind is ItemKind.EPISODE:
            episode = next((e for e in visible.snapshot.episodes if e.episode_id == ref), None)
            if episode is None or episode.origin_handle_id is None:
                return None
            origin = episode.origin_handle_id
            try:
                versions = await Replay(recorded).memory.episode_versions(origin, ref)
            except Missed:
                # One short statement, kept with the rows for the next opens of this episode.
                async with self._uow(space_id) as uow:
                    versions = await Recording(uow, recorded).memory.episode_versions(origin, ref)
            opened = _with_versions(opened, versions)
        if generation is not None and self._rows.due(recorded):
            self._reads.later(
                ("rows", key), self._record(caller, config, target, level.effective, False, key, generation)
            )
        return visible, opened

    async def _open_read(
        self,
        caller: SourcePrincipal,
        config: SpaceConfig,
        ref: UUID,
        verification: Verification,
        conversation_id: str | None,
        profile_id: UUID | None,
        subject: Handle | None,
        about: Handle | None = None,
    ) -> "tuple[_Visible, OpenedItem]":
        """The open read from the database, in one transaction. A kit bound to a customer (`subject`) records
        the rows it read, as a search does, so the next opens and searches of the customer replay them."""
        space_id = caller.space.space_id
        held = await self._reads.held(caller, verification, conversation_id=conversation_id, task_id=None)
        key: tuple[Any, ...] | None = None
        generation: int | None = None
        async with self._uow(space_id) as uow:
            origin = await self._origin_handle(uow, ref)
            owner = await uow.identity.profile_of(origin) if origin else None
            if owner is None:
                raise NotFound("item not found")
            if subject is not None:
                # A kit bound to a customer only opens what that customer's view shows (16.3), at its
                # handle's level: an account's view shows its contacts' recent conversations.
                bound = await self._reads.resolve_subject(uow, subject)
                if bound is None or (profile_id is not None and bound.profile.profile_id != profile_id):
                    raise NotFound("item not found")
                # With `about`, also what the person's view of the organization shows (21.4).
                attached, _ = await self._about(uow, bound, about)
                target = attached or bound
            elif profile_id is not None and profile_id != owner.profile_id:
                viewer = await uow.identity.get_profile(profile_id)
                if viewer is None:
                    raise NotFound("item not found")
                target = Target(reader_view(viewer))
            else:
                # Opened by id, with no handle: the view of a read by profile, without unconfirmed links
                # (5.3).
                target = Target(reader_view(owner))
            level = await self._reads.verification(
                uow,
                caller,
                verification,
                conversation_id=conversation_id,
                task_id=None,
                target=target,
                held=held,
                inactivity=config.settings.session_inactivity_minutes,
            )
            assignment = await self._reads.assignment(uow, caller, config, target)
            if subject is not None and about is None and assignment is None and not config.experiments:
                key = _rows_key(space_id, target, False)
            if key is not None:
                generation = await self._generation(space_id, target.profile.profile_id)
            recorded = self._start(generation or 0, target.profile.privacy_version, config)
            rows = Recording(uow, recorded)
            visible = await self._visible_rows(
                rows, caller, config, target, level.effective, assignment, False, recorded
            )
            # A withheld item and a missing one look the same from outside (L7, L8).
            shown = {d.id: d for d in visible.docs}
            doc = next((d for d in visible.docs if d.ref == ref and d.kind in OPENED_KINDS), None)
            if doc is None or visible.snapshot is None:
                raise NotFound("item not found")
            opened = self._open(visible, shown, doc)
            if doc.kind is ItemKind.EPISODE and origin is not None:
                opened = _with_versions(opened, await rows.memory.episode_versions(origin, ref))
        if key is not None and generation is not None:
            self._rows.put(key, recorded)
        return visible, opened

    async def _about(
        self, uow: UnitOfWork, target: Target | None, about: Handle | None
    ) -> tuple[Target | None, bool]:
        """The target with `about` attached when the subject has an active link to that organization; else
        the subject alone and True: navigation fails soft as the context read does (L10), the same whether the
        organization exists or not."""
        if about is None:
            return target, False
        # The link the read state keeps, as the context read takes it: looking the organization, the link and
        # its generation up again cost 4 statements of every `about` search (09/10/2026).
        if target is not None and uow.space_id is not None:
            kept = self._reads.cached_about(uow.space_id, target, about)
            if kept is not None:
                return replace(target, about=kept), False
        try:
            return await self._reads.attach_about(uow, target, about), False
        except AboutWithoutLink:
            return target, True

    async def can_read(
        self,
        caller: SourcePrincipal,
        subject: Handle,
        item_id: str,
        *,
        verification: Verification,
        conversation_id: str | None,
        task_id: str | None,
    ) -> bool:
        """Whether this reader, at this conversation's level, may see the customer's row `item_id`: a write
        that names a row (a correction) is refused for a row the reader could not have read."""
        config = self._begin(caller)
        held = await self._reads.held(caller, verification, conversation_id=conversation_id, task_id=task_id)
        async with self._uow(caller.space.space_id) as uow:
            target = await self._reads.resolve_subject(uow, subject)
            visible = await self._visible(
                uow,
                caller,
                config,
                target,
                verification,
                conversation_id,
                task_id,
                held=held,
                show_expired=True,
            )
        return any(d.id == item_id for d in visible.docs)

    def tools_definitions(self, caller: SourcePrincipal) -> list[ToolDefinition]:
        self._begin(caller)
        tools = in_language(NAVIGATION_TOOLS, self.history_label(caller))
        return [ToolDefinition(function=tool.as_function()) for tool in tools]

    def history_label(self, caller: SourcePrincipal) -> str:
        """The label of the context's "From history" line in the caller's space."""
        settings = self._config.space(caller.space.space_id).settings
        return Formatter(settings.timezone, settings.locale).section(Section.HISTORY)

    async def _search_rows(
        self, caller: SourcePrincipal, config: SpaceConfig, request: SearchRequest
    ) -> tuple["_Visible", dict[str, ItemVector], Window | None]:
        """The rows a search reads: replayed from memory when the customer, the conversation's proof and
        the customer's generation are known without the database (`app.history_rows`); else read in one
        transaction, and recorded for the next searches.

        With `about`, the organization's rows are a side of their own, kept by the organization's generation
        and replayed for every contact that searches about it; the person's side is the one a search by the
        customer keeps. Each `about` search read both again, 41 statements, every call (09/10/2026)."""
        space_id = caller.space.space_id
        show_expired = request.filters.show_expired
        closed = _closed(request.filters)
        cacheable = not config.experiments and not request.filters.when
        target = self._reads.cached_subject(space_id, request.subject) if cacheable else None
        if target is not None and request.about is not None:
            kept = self._reads.cached_about(space_id, target, request.about)
            target = replace(target, about=kept) if kept is not None else None
        if target is not None:
            level = self._reads.cached_verification(
                caller,
                request.verification,
                conversation_id=request.conversation_id,
                task_id=request.task_id,
                target=target,
            )
            if level is not None:
                person, account = await self._sides(space_id, config, target, show_expired, closed, True)
                if person.held and (account is None or account.held):
                    try:
                        visible, vectors = await self._read_sides(
                            None,
                            caller,
                            config,
                            target,
                            level.effective,
                            None,
                            show_expired,
                            closed,
                            person,
                            account,
                        )
                    except Missed:
                        pass  # another reader's view of the rows: read them
                    else:
                        self._renew(
                            caller, config, target, level.effective, show_expired, closed, person, account
                        )
                        return visible, vectors, None
        held = await self._reads.held(
            caller, request.verification, conversation_id=request.conversation_id, task_id=request.task_id
        )
        async with self._uow(space_id) as uow:
            target = self._reads.cached_subject(
                space_id, request.subject
            ) or await self._reads.resolve_subject(uow, request.subject)
            target, unlinked = await self._about(uow, target, request.about)
            level = await self._reads.verification(
                uow,
                caller,
                request.verification,
                conversation_id=request.conversation_id,
                task_id=request.task_id,
                target=target,
                held=held,
                inactivity=config.settings.session_inactivity_minutes,
            )
            if target is None:
                return _Visible(None, None, [], 0, None, level.effective, about_unlinked=unlinked), {}, None
            assignment = await self._reads.assignment(uow, caller, config, target)
            keep = cacheable and assignment is None
            person, account = await self._sides(space_id, config, target, show_expired, closed, keep)
            try:
                visible, vectors = await self._read_sides(
                    uow,
                    caller,
                    config,
                    target,
                    level.effective,
                    assignment,
                    show_expired,
                    closed,
                    person,
                    account,
                )
            except Missed:
                # A side held in memory lacks a call this read makes (another reader's view): read both.
                person, account = self._unheld(config, person), account and self._unheld(config, account)
                visible, vectors = await self._read_sides(
                    uow,
                    caller,
                    config,
                    target,
                    level.effective,
                    assignment,
                    show_expired,
                    closed,
                    person,
                    account,
                )
            visible = replace(visible, about_unlinked=unlinked)
            window = await self._window(
                uow, config, request.filters, request.conversation_id, request.task_id
            )
        for side in (person, account):
            if side is not None and not side.held and side.key is not None and side.generation is not None:
                self._rows.put(side.key, side.recorded)
        return visible, vectors, window

    async def _sides(
        self,
        space_id: UUID,
        config: SpaceConfig,
        target: Target,
        show_expired: bool,
        closed: frozenset[OpenItemStatus],
        keep: bool,
    ) -> "tuple[_Side, _Side | None]":
        """The person's side of the rows and, with `about`, the organization's: each held in memory while
        its profile's generation and privacy version hold, else a recording to read it into, kept under its
        key when `keep`."""
        person = await self._side(
            space_id,
            config,
            target.profile,
            _rows_key(space_id, target, show_expired, closed=closed) if keep else None,
        )
        about = target.about
        if about is None or about.profile is None:
            return person, None
        key = _account_key(space_id, about.profile, show_expired) if keep else None
        return person, await self._side(space_id, config, about.profile, key)

    async def _side(
        self, space_id: UUID, config: SpaceConfig, profile: ProfileRecord, key: tuple[Any, ...] | None
    ) -> "_Side":
        generation = await self._generation(space_id, profile.profile_id) if key is not None else None
        if key is not None and (held := self._rows.get(key, generation, profile.privacy_version)) is not None:
            return _Side(key, generation, held, True)
        return _Side(key, generation, self._start(generation or 0, profile.privacy_version, config), False)

    def _unheld(self, config: SpaceConfig, side: "_Side") -> "_Side":
        recorded = self._start(side.generation or 0, side.recorded.privacy_version, config)
        return _Side(side.key, side.generation, recorded, False)

    async def _read_sides(
        self,
        uow: UnitOfWork | None,
        caller: SourcePrincipal,
        config: SpaceConfig,
        target: Target,
        level: Verification,
        assignment: Assignment | None,
        show_expired: bool,
        closed: frozenset[OpenItemStatus],
        person: "_Side",
        account: "_Side | None",
    ) -> tuple["_Visible", dict[str, ItemVector]]:
        """The rows of both sides, each replayed or read from `uow` and recorded."""
        rows = person.source(uow)
        theirs = account.source(uow) if account is not None else None
        visible = await self._visible_rows(
            rows,
            caller,
            config,
            target,
            level,
            assignment,
            show_expired,
            person.recorded,
            closed=closed,
            account_rows=theirs,
            account_window=account.recorded if account is not None else None,
        )
        return visible, await self._stored_vectors(rows, visible, theirs)

    def _renew(
        self,
        caller: SourcePrincipal,
        config: SpaceConfig,
        target: Target,
        level: Verification,
        show_expired: bool,
        closed: frozenset[OpenItemStatus],
        person: "_Side",
        account: "_Side | None",
    ) -> None:
        """The sides a replay found old read again in the background, each on its own."""
        if person.key is not None and person.generation is not None and self._rows.due(person.recorded):
            self._reads.later(
                ("rows", person.key),
                self._record(
                    caller,
                    config,
                    replace(target, about=None),
                    level,
                    show_expired,
                    person.key,
                    person.generation,
                    closed=closed,
                ),
            )
        about = target.about.profile if target.about is not None else None
        if (
            about is not None
            and account is not None
            and account.key is not None
            and account.generation is not None
            and self._rows.due(account.recorded)
        ):
            self._reads.later(
                ("rows", account.key),
                self._record_account(caller, config, about, show_expired, account.key, account.generation),
            )

    async def _record(
        self,
        caller: SourcePrincipal,
        config: SpaceConfig,
        target: Target,
        level: Verification,
        show_expired: bool,
        key: tuple[Any, ...],
        generation: int,
        *,
        closed: frozenset[OpenItemStatus] = frozenset(),
    ) -> None:
        """The rows of `key` read again for the next searches, as the search that found them old read them."""
        recorded = self._start(generation, target.profile.privacy_version, config)
        async with self._uow(caller.space.space_id) as uow:
            rows = Recording(uow, recorded)
            visible = await self._visible_rows(
                rows, caller, config, target, level, None, show_expired, recorded, closed=closed
            )
            await self._stored_vectors(rows, visible)
        self._rows.put(key, recorded)

    async def _record_account(
        self,
        caller: SourcePrincipal,
        config: SpaceConfig,
        organization: ProfileRecord,
        show_expired: bool,
        key: tuple[Any, ...],
        generation: int,
    ) -> None:
        """The organization's side of `key` read again for the next `about` searches."""
        recorded = self._start(generation, organization.privacy_version, config)
        async with self._uow(caller.space.space_id) as uow:
            rows = Recording(uow, recorded)
            account, _ = await self._account_rows(rows, config, organization, show_expired, recorded)
            await _vectors(rows, account)
        self._rows.put(key, recorded)

    def _start(self, generation: int, privacy_version: int, config: SpaceConfig) -> Recorded:
        now = self._clock.now()
        window = now - timedelta(days=config.settings.events_hot_days)
        return self._rows.start(generation, privacy_version, window, now)

    def _window_start(self, config: SpaceConfig) -> datetime:
        return self._clock.now() - timedelta(days=config.settings.events_hot_days)

    # Internals.

    def _begin(self, caller: SourcePrincipal) -> SpaceConfig:
        self._reads.require(caller, Scope.SEARCH)
        config = self._config.space(caller.space.space_id)
        source = config.source(caller.source_id)
        if source is not None and not source.navigation_enabled:
            raise Forbidden("history navigation is not enabled for this source")
        return config

    async def _visible(
        self,
        uow: UnitOfWork,
        caller: SourcePrincipal,
        config: SpaceConfig,
        target: Target | None,
        requested: Verification,
        conversation_id: str | None,
        task_id: str | None,
        *,
        held: Verification | None,
        show_expired: bool = False,
        closed: frozenset[OpenItemStatus] = frozenset(),
    ) -> _Visible:
        """`held` comes from `ReadSupport.held`, read before the transaction."""
        level = await self._reads.verification(
            uow,
            caller,
            requested,
            conversation_id=conversation_id,
            task_id=task_id,
            target=target,
            held=held,
            inactivity=config.settings.session_inactivity_minutes,
        )
        if target is None:
            return _Visible(None, None, [], 0, None, level.effective)
        assignment = await self._reads.assignment(uow, caller, config, target)
        return await self._visible_rows(
            uow,
            caller,
            config,
            target,
            level.effective,
            assignment,
            show_expired,
            self._window_start(config),
            closed=closed,
        )

    async def _visible_rows(
        self,
        uow: Any,
        caller: SourcePrincipal,
        config: SpaceConfig,
        target: Target,
        level: Verification,
        assignment: Assignment | None,
        show_expired: bool,
        window: "Recorded | datetime",
        *,
        closed: frozenset[OpenItemStatus] = frozenset(),
        account_rows: Any = None,
        account_window: "Recorded | datetime | None" = None,
    ) -> "_Visible":
        """`uow` is a unit of work, or a recording or a replay of one (`app.history_rows`): the rows are
        built by the same code either way. With `closed`, the subject's rows hold every item of those
        statuses instead of the promises kept lately (`item_statuses`). With `about`, the organization's rows
        come from `account_rows` and `account_window` when given, a side recorded on its own."""
        since = _since(window)
        # `target.profile` is the reader's view (5.3): rows of unconfirmed links it may not see never
        # become rows here, so they are neither found, listed, opened nor counted as withheld.
        snapshot = await load_snapshot(
            uow,
            target.profile,
            None if show_expired else self._clock.now(),
            timezone=config.settings.timezone,
            closed=closed,
            read_at=_read_at(window),
        )
        if assignment is not None and overdue_company_items(snapshot.open_items):
            assignment = None  # out of both groups, as in context() (19.7)
        group = assignment.label if assignment else None
        if assignment is not None and assignment.holdout:
            return _Visible(target, None, [], 0, group, level)
        about = target.about
        # With `about`, the organization's memory the person's view shows, as the pack's account block
        # reads it (21.4): the same snapshot, link role and contacts' gate, so navigation reaches what the
        # pack shows and nothing more.
        account, theirs = (
            await self._account_rows(
                uow if account_rows is None else account_rows,
                config,
                about.profile,
                show_expired,
                window if account_window is None else account_window,
            )
            if about is not None and about.profile is not None
            else (None, [])
        )
        settings = compile_settings(config)
        variant = Variant(
            verification=level,
            audience=caller.audience,
            purposes=caller.purposes,
            source_id=caller.source_id,
            about_handle_id=about.handle_id if about else None,
            link_role=about.role if about else None,
            can_see_contacts=about.can_see_contacts if about else True,
        )
        fmt = Formatter(settings.timezone, settings.locale)
        person = (target.handle.handle_id,) if target.handle and account is not None else ()
        candidates = CandidateBuilder(CompileInput(snapshot, variant, settings, account, person), fmt).build()
        # System events are rows of their own, from the hot window: past it they leave the database.
        events = await uow.ingest.system_events_for_handles(
            target.profile.handle_ids, since, SYSTEM_EVENTS_LIMIT
        )
        docs = [
            *history_docs(snapshot, candidates, account),
            *system_event_docs(
                events,
                fmt,
                target.profile.kind,
                target.profile.profile_id,
                settings.private_fields,
                settings.declared_names,
            ),
        ]
        if account is not None:
            listed = {d.id for d in docs}
            docs += [
                d
                for d in system_event_docs(
                    theirs,
                    fmt,
                    account.profile.kind,
                    account.profile.profile_id,
                    settings.private_fields,
                    settings.declared_names,
                )
                if d.id not in listed
            ]
        reader = self._reads.reader(caller)
        context = PolicyContext(
            level,
            variant.link_role,
            variant.can_see_contacts,
            deny_sensitive=self._reads.deny_sensitive,
        )
        allowed, withheld = [], 0
        for doc in docs:
            # Navigation shows whole rows or nothing: a masked row counts as withheld.
            if evaluate(config.policy, reader, doc.attrs, context).effect is Effect.ALLOW:
                allowed.append(doc)
            else:
                withheld += 1
        return _Visible(target, snapshot, allowed, withheld, group, level, account)

    async def _account_rows(
        self,
        rows: Any,
        config: SpaceConfig,
        organization: ProfileRecord,
        show_expired: bool,
        window: "Recorded | datetime",
    ) -> tuple[MemorySnapshot, list[Any]]:
        """The organization's side of an `about` read: its memory as the account block reads it, and its
        system events. The same for every contact that reads about it, so a side recorded once serves them
        all."""
        account = await load_snapshot(
            rows,
            reader_view(organization),
            None if show_expired else self._clock.now(),
            timezone=config.settings.timezone,
            read_at=_read_at(window),
        )
        events = await rows.ingest.system_events_for_handles(
            account.profile.handle_ids, _since(window), SYSTEM_EVENTS_LIMIT
        )
        return account, events

    async def _stored_vectors(
        self, uow: Any, visible: "_Visible", account_rows: Any = None
    ) -> dict[str, ItemVector]:
        """The vectors the write side stored with the customer's rows, and with the organization's beside
        them, each side read from its own rows (`account_rows`, else `uow`)."""
        if visible.target is None or visible.snapshot is None or not visible.docs:
            return {}
        found = await _vectors(uow, visible.snapshot, visible.target.profile.handle_ids)
        if visible.account is not None:
            found += await _vectors(uow if account_rows is None else account_rows, visible.account)
        return {v.doc_id: v for v in found}

    async def _semantic(
        self,
        encoding: "asyncio.Task[tuple[list[float], str]]",
        docs: Sequence[HistoryDoc],
        stored: dict[str, ItemVector],
    ) -> tuple[dict[str, float], str | None]:
        """Exact cosine between the question and the stored vectors. Only the question is encoded here; a
        row without a vector of the question's encoder ranks by its words alone until the backfill runs."""
        if not docs:
            _discard(encoding)
            return {}, None
        try:
            query_vector, model = await encoding
        except Exception:
            # The encoder is down or busy: full-text only, and the answer says so (16.6).
            return {}, "text_only"
        query_norm = norm(query_vector)
        scores = {
            d.id: cosine(query_vector, vector.vector, query_norm)
            for d in docs
            if (vector := stored.get(d.id)) is not None and vector.model == model
        }
        return {i: s for i, s in scores.items() if s > 0}, None

    async def _window(
        self,
        uow: UnitOfWork,
        config: SpaceConfig,
        filters: HistoryFilters,
        conversation_id: str | None,
        task_id: str | None,
    ) -> Window | None:
        """`when` read from the last event of the conversation in progress, so a replay reads the same
        words the same way; from now when the read names no conversation (10, D.1)."""
        if not filters.when:
            return None
        reference = self._clock.now()
        if conversation_id or task_id:
            session = await uow.sessions.open_session(conversation_id=conversation_id, task_id=task_id)
            if session is not None:
                reference = session.last_event_at
        return read_when(filters.when, reference, config.settings.timezone)

    def _default_budget(self, caller: SourcePrincipal, config: SpaceConfig) -> int:
        source = config.source(caller.source_id)
        return VOICE_BUDGET if source is not None and source.channel == "voice" else TEXT_BUDGET

    def _recurrence(self, visible: _Visible, results: Sequence[HistoryDoc]) -> Recurrence | None:
        episodes = self._visible_episodes(visible)
        top = next((d for d in results if d.kind is ItemKind.EPISODE), None)
        if top is None or not episodes:
            return None
        found = recurrence(episodes, top.category, now=self._clock.now())
        if found is None:
            return None
        return Recurrence(
            category=found.category,
            occurrences=found.occurrences,
            window_days=RECURRENCE_WINDOW_DAYS,
            last_at=found.last_at,
            last_outcome=found.last_outcome,
            last_resolution=found.last_resolution,
        )

    @staticmethod
    def _visible_episodes(visible: _Visible) -> list[Episode]:
        if visible.snapshot is None:
            return []
        shown = {d.id for d in visible.docs if d.kind is ItemKind.EPISODE}
        return [e for e in visible.merged().episodes if f"episode:{e.episode_id}" in shown]

    async def _page(
        self,
        caller: SourcePrincipal,
        visible: _Visible,
        filters: HistoryFilters,
        cursor: str | None,
        limit: int,
        conversation_id: str | None,
        window: Window | None = None,
    ) -> TimelineResponse:
        accepts = _filter(_narrowed(filters, window), self._config.space(caller.space.space_id)).accepts
        kinds = timeline_kinds(filters.item_kinds, filters.item_statuses)
        rows = newest_first([d for d in visible.docs if d.kind in kinds and accepts(d)])
        remaining = after_cursor(rows, cursor)
        page = remaining[:limit]
        await self._receipt(
            caller,
            visible,
            ReceiptSurface.TIMELINE,
            [d.id for d in page],
            conversation_id=conversation_id,
            detail=await self._filters_detail(caller, filters),
        )
        return TimelineResponse(
            items=[_item(d) for d in page],
            next_cursor=encode_cursor(page[-1]) if len(remaining) > limit else None,
            withheld=visible.withheld,
            as_of=max((d.at for d in visible.docs), default=None),
            window=_window_out(window),
            ignored=["when"] if filters.when and window is None else [],
            about_unlinked=visible.about_unlinked,
        )

    async def _origin_handle(self, uow: UnitOfWork, ref: UUID) -> UUID | None:
        """Whose the conversation or object with this id is; ids are unique across kinds."""
        episode = await uow.memory.get_episode(ref)
        if episode is not None:
            return episode.origin_handle_id
        obj = await uow.objects.get_object_by_id(ref)
        return obj.origin_handle_id if obj else None

    @staticmethod
    def _open(visible: _Visible, shown: dict[str, HistoryDoc], doc: HistoryDoc) -> OpenedItem:
        assert visible.snapshot is not None
        snapshot = visible.merged()
        as_of = max((d.at for d in visible.docs), default=None)
        if doc.kind is ItemKind.EPISODE:
            episode = next(e for e in snapshot.episodes if e.episode_id == doc.ref)
            origin_ids = {str(episode.episode_id), str(episode.session_id), episode.conversation_id}
            promises = [
                Promise(
                    by="company" if i.owner == "company" else "customer",
                    what=i.description,
                    due_at=i.due_at,
                    status=i.status.value,
                )
                for i in snapshot.open_items
                if f"open_item:{i.item_id}" in shown and origin_ids & {str(v) for v in i.origin.values()}
            ]
            derived = [
                _item(shown[f"fact:{f.fact_id}"])
                for f in snapshot.facts
                if f"fact:{f.fact_id}" in shown
                and episode.conversation_id
                and f.origin_conversation_id == episode.conversation_id
            ]
            # What happened during the conversation, or right after it: agent actions and system events.
            timeline = [
                _item(d)
                for d in visible.docs
                if d.kind in (ItemKind.ACTION, ItemKind.SYSTEM_EVENT) and during(d, episode)
            ]
            return OpenedItem(
                id=str(episode.episode_id),
                kind="episode",
                summary=episode.summary,
                requested=episode.intent,
                promises=promises,
                outcome=episode.outcome,
                resolution=episode.resolution,
                derived=derived,
                timeline=sorted(timeline, key=lambda i: (i.at, i.id), reverse=True),
                as_of=as_of,
            )
        obj = next(o for o in snapshot.objects if o.object_id == doc.ref)
        actions = [
            _item(shown[f"action:{a.action_id}"])
            for a in snapshot.actions
            if a.object_id == obj.object_id and f"action:{a.action_id}" in shown
        ]
        actions += [
            _item(d) for d in visible.docs if d.kind is ItemKind.SYSTEM_EVENT and d.object_key == obj.key
        ]
        promises = [
            Promise(
                by="company" if i.owner == "company" else "customer",
                what=i.description,
                due_at=i.due_at,
                status=i.status.value,
            )
            for i in snapshot.open_items
            if i.object == obj.key and f"open_item:{i.item_id}" in shown
        ]
        return OpenedItem(
            id=str(obj.object_id),
            kind="object",
            summary=doc.text,
            promises=promises,
            timeline=sorted(actions, key=lambda i: (i.at, i.id), reverse=True),
            as_of=obj.as_of,
        )

    async def _receipt(
        self,
        caller: SourcePrincipal,
        visible: _Visible,
        surface: ReceiptSurface,
        item_ids: Sequence[str],
        *,
        conversation_id: str | None = None,
        task_id: str | None = None,
        query_hash: bytes | None = None,
        detail: dict[str, str] | None = None,
    ) -> None:
        await self._reads.emit(
            [
                self._reads.draft(
                    caller,
                    ReceiptKind.HISTORY,
                    surface,
                    profile_id=visible.target.profile.profile_id if visible.target else None,
                    conversation_id=conversation_id,
                    task_id=task_id,
                    item_ids=tuple(public_item_id(i) for i in item_ids),
                    verification=visible.level.value,
                    experiment_group=visible.group,
                    query_hash=query_hash,
                    withheld=visible.withheld,
                    detail=detail or {},
                )
            ]
        )

    async def _filters_detail(self, caller: SourcePrincipal, filters: HistoryFilters) -> dict[str, str]:
        """The filters a navigation used, as a keyed hash like the question's (16.5): the receipt proves
        which filters were asked without keeping what they say. Nothing when none was set."""
        chosen = filters.model_dump(mode="json", exclude_defaults=True)
        if not chosen:
            return {}
        canonical = json.dumps(chosen, sort_keys=True, separators=(",", ":")).encode()
        digest = await self._keys.hmac(caller.space.space_id, "history_filters", canonical)
        return {"filters_hash": digest.hex()}


def _with_versions(opened: OpenedItem, versions: Sequence[Episode]) -> OpenedItem:
    return opened.model_copy(
        update={
            "versions": [
                ItemVersion(version=v, changed_at=at, what_changed=what)
                for v, at, what in episode_changes(versions)
            ]
        }
    )


def _filter(filters: HistoryFilters, config: SpaceConfig) -> HistoryFilter:
    obj = filters.object
    return HistoryFilter(
        where=parse_where(filters.where) if filters.where else None,
        vendors={s.source_id: s.vendor for s in config.sources if s.vendor},
        since=filters.since,
        until=filters.until,
        channels=frozenset(filters.channels),
        categories=frozenset(filters.categories),
        kinds=frozenset(filters.item_kinds),
        statuses=frozenset(filters.item_statuses),
        outcome=filters.outcome,
        object_key=ObjectKey(obj.type, obj.namespace, obj.id) if obj else None,
    )


def in_zone(filters: HistoryFilters, config: SpaceConfig) -> HistoryFilters:
    """`since` and `until` written without an offset ("2026-10-01T00:00:00", as a model writes a tool's
    arguments) are times of the space's time zone: compared with the rows' instants as they came, they
    raised a TypeError and the search answered 500 (juridico-sandbox, 05/10/2026)."""
    zone = ZoneInfo(config.settings.timezone)
    update = {
        name: value.replace(tzinfo=zone)
        for name in ("since", "until")
        if (value := getattr(filters, name)) is not None and value.tzinfo is None
    }
    return filters.model_copy(update=update) if update else filters


def _narrowed(filters: HistoryFilters, window: Window | None) -> HistoryFilters:
    """The window of `when` narrows `since` and `until`; it never widens what the caller asked."""
    if window is None:
        return filters
    since = max(filters.since, window.since) if filters.since else window.since
    until = min(filters.until, window.until) if filters.until else window.until
    return filters.model_copy(update={"since": since, "until": until})


def _window_out(window: Window | None) -> TimeWindow | None:
    return TimeWindow(since=window.since, until=window.until) if window else None


def _item(doc: HistoryDoc) -> HistoryItem:
    key = doc.object_key
    return HistoryItem(
        id=str(doc.ref),
        kind=doc.kind.value,
        text=doc.text,
        at=doc.at,
        valid_until=doc.valid_until,
        channel=doc.channel,
        source_id=str(doc.source_id) if doc.source_id else None,
        outcome=doc.outcome,
        confidence=doc.confidence,
        origin_event_id=doc.origin_event_id,
        object=ObjectRef(type=key.type, namespace=key.namespace, id=key.external_id) if key else None,
        expected_operation=doc.expected_operation,
        status=doc.status,
        closed_at=doc.closed_at,
        closed_by=ClosedBy.model_validate(doc.closed_by) if doc.closed_by else None,
        item_kind=doc.item_kind,
        audience=doc.audience,
    )


def _closed(filters: HistoryFilters) -> frozenset[OpenItemStatus]:
    """The statuses past open whose every item is read: only when asked for, as the rows otherwise hold the
    promises kept lately."""
    return frozenset(OpenItemStatus(s) for s in filters.item_statuses) - {
        OpenItemStatus.OPEN,
        OpenItemStatus.OVERDUE,
    }


def _rows_key(
    space_id: UUID, target: Target, show_expired: bool, *, closed: frozenset[OpenItemStatus] = frozenset()
) -> tuple[Any, ...] | None:
    """A reader's view of the customer: the same handles and marks read the same rows. An organization read
    as the subject is read each time; an `about` search keeps its rows as a side of their own
    (`_account_key`)."""
    profile = target.profile
    if profile.kind.is_organization:
        return None
    return (
        space_id,
        profile.profile_id,
        profile.handle_ids,
        profile.hidden_handle_ids,
        profile.provisional_handle_ids,
        show_expired,
        tuple(sorted(s.value for s in closed)),
    )


def _account_key(space_id: UUID, organization: ProfileRecord, show_expired: bool) -> tuple[Any, ...]:
    """The organization's side of an `about` search, the same for every contact that searches about it. Its
    rows name the contacts' recent conversations and items: what changes a contact's memory compiles the
    organization again, an erasure moves its privacy version, and what moves neither (a contact linked since)
    is bounded by `ROWS_FOR_S`, as the account block of a pack compiled on the read path is."""
    view = reader_view(organization)
    return (
        "account",
        space_id,
        organization.profile_id,
        view.handle_ids,
        organization.membership_version,
        show_expired,
    )


@dataclass(frozen=True, slots=True)
class _Side:
    """One side of a search's rows (`app.history_rows`): the person's, or the organization's an `about`
    search reads beside them. Held, it replays from memory; else it is read now into `recorded`, and kept
    under `key` when there is one."""

    key: tuple[Any, ...] | None
    generation: int | None
    recorded: Recorded
    held: bool

    def source(self, uow: Any) -> Any:
        return Replay(self.recorded) if self.held else Recording(uow, self.recorded)


def _since(window: "Recorded | datetime") -> datetime:
    return window.window_start if isinstance(window, Recorded) else window


def _read_at(window: "Recorded | datetime") -> datetime | None:
    return window.read_at if isinstance(window, Recorded) else None


async def _vectors(rows: Any, snapshot: MemorySnapshot, handle_ids: Sequence[UUID] = ()) -> list[ItemVector]:
    """The vectors the write side stored with one profile's rows, read through the modules that own them.
    Every object, action and pattern of the snapshot is asked for, whatever this reader sees, so the same
    calls serve every reader of the rows; only the visible ones are ever looked up."""
    return [
        *await rows.memory.vectors_for(sorted(set(snapshot.profile.handle_ids) | set(handle_ids))),
        *await rows.objects.vectors_of(
            sorted(o.object_id for o in snapshot.objects), sorted(a.action_id for a in snapshot.actions)
        ),
        *await rows.traits.vectors_of(sorted(t.trait_id for t in snapshot.traits)),
    ]


def _discard(task: "asyncio.Task[Any]") -> None:
    task.cancel()
    task.add_done_callback(lambda done: done.cancelled() or done.exception())


def _reading(
    caller: SourcePrincipal, subject: Handle | None, conversation_id: str | None, verification: Verification
) -> tuple[SourcePrincipal, Verification]:
    """Navigation reads in a conversation as the context does: an internal key talking with
    anyone but staff named by a system id reads the customer-facing view, at what the conversation proved."""
    kind = claimed_kind(subject.type, subject.subject_kind) if subject else None
    narrowed = in_conversation(caller, kind, conversation_id)
    if narrowed is not caller and verification is Verification.NO_CUSTOMER:
        verification = Verification.V0
    return narrowed, verification
