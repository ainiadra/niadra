"""Retention and visibility by purpose and legal basis, per memory item (13, 6.6).

Three moments. When memory writes a row, `tag_rows` gives it the purposes it was collected for, in the same
transaction, so no read ever sees it untagged once the space declared its purposes; rows written before the
document existed are tagged by the per-space loop in small batches. Once a day, after 03:00 in the space's
time zone, the loop removes the rows whose every purpose ran out and applies each objection again to rows a
merge or a confirmation brought under it. And `object` takes a data subject's objection to one purpose: the
rows collected only for it leave, the others keep their other purposes.

Reads never call anything here: the compiler turns a row's tags into the purposes that may read it
(`domain.privacy.purposes.readers`), and the policy decides as it does for every item.
"""

import json
import logging
import zlib
from collections.abc import Collection, Sequence
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID
from zoneinfo import ZoneInfo

from niadra.config.models import SpaceConfig
from niadra.contracts.privacy import ErasureOut, ForgetRequest
from niadra.domain.common.errors import InvalidInput, NotFound
from niadra.domain.identity.handles import normalize_handle
from niadra.domain.identity.provenance import derivation_view
from niadra.domain.privacy.erasure import ErasedRow, request_id_for
from niadra.domain.privacy.export import EXPORT_TABLES
from niadra.domain.privacy.purposes import (
    Collected,
    ItemKey,
    ItemKind,
    Objection,
    Tagged,
    TagRow,
    Untagged,
    after_objection,
    configured,
    cutoffs,
    decide_tags,
    retention,
)
from niadra.domain.records import Episode, ProfileRecord
from niadra.domain.signals.records import PURPOSE as SIGNALS_PURPOSE
from niadra.domain.tasks import Task, TaskName
from niadra.domain.vocabulary import EventKind
from niadra.ports.infra import Clock, ConfigProvider
from niadra.ports.uow import UnitOfWork, UnitOfWorkFactory

log = logging.getLogger(__name__)

WRITE_BATCH = 500
"""Rows of one profile a write tags at most; a conversation writes a handful."""
BACKFILL_BATCH = 200
EXPIRY_BATCH = 500
EXPIRY_BATCHES = 20
"""Batches of expired rows one daily pass removes; what is left goes the next day."""
OBJECTION_PAGE = 200
NIGHT_HOUR = 3
FINGERPRINT = "purposes_fingerprint"
DAILY = "purposes_day"


@dataclass(frozen=True, slots=True)
class TagOutcome:
    tagged: int = 0
    removed: tuple[ErasedRow, ...] = ()


async def tag_rows(
    uow: UnitOfWork,
    config: SpaceConfig,
    handle_ids: Sequence[UUID] | None,
    now: datetime,
    *,
    limit: int,
    recompile: bool = False,
) -> TagOutcome:
    """Tags the rows written without purposes, the handles' only (a write) or any (the backfill). A row
    whose every purpose its customer objected to is not kept: it goes in the same transaction. A write
    asks for its profile's packs anyway; the backfill passes `recompile`."""
    rules = config.purposes
    if not configured(rules):
        return TagOutcome()
    rows = await uow.memory.untagged(handle_ids=handle_ids, limit=limit)
    if not rows:
        return TagOutcome()
    events = {
        e.event_id: e for e in await uow.ingest.get_events(sorted({i for r in rows for i in r.event_ids}))
    }
    # An open item or a value is about its conversation's category and takes a tagged conversation's
    # purposes; the conversation may be anchored on another handle of the profile.
    sessions: dict[tuple[UUID, UUID], Episode | None] = {}
    for row in rows:
        if row.key.kind in (ItemKind.OPEN_ITEM, ItemKind.VALUE) and row.session_id is not None:
            key = (row.key.origin_handle_id, row.session_id)
            if key not in sessions:
                look = list(handle_ids) if handle_ids else [row.key.origin_handle_id]
                sessions[key] = await uow.memory.current_episode(look, row.session_id)
    episode_tags = {
        e.episode_id: e.purposes for e in sessions.values() if e is not None and e.purposes is not None
    }

    def collected(row: Untagged) -> TagRow:
        found = [events[i] for i in row.event_ids if i in events]
        category = row.category
        if category is None and row.session_id is not None:
            episode = sessions.get((row.key.origin_handle_id, row.session_id))
            category = episode.normalized_category if episode else None
        sources = {row.source_id} if row.source_id else {e.source_id for e in found}
        declared = frozenset(p for s in sources if (c := config.source(s)) for p in c.purposes)
        types = tuple(
            sorted({e.canonical_type for e in found if e.kind is EventKind.SYSTEM_EVENT and e.canonical_type})
        )
        return TagRow(row.key, Collected(row.key.kind, category, types, declared), row.episode_id)

    objected = await objections_for(uow, {r.key.origin_handle_id for r in rows})
    decisions = decide_tags([collected(r) for r in rows], rules, objected, episode_tags)
    tagged = [Tagged(d.row.key, d.tags) for d in decisions if d.tags is not None]
    await uow.memory.set_purposes(tagged)
    removed = await _remove(uow, config, [d.row.key for d in decisions if d.tags is None], now)
    if recompile and tagged:
        # Rows read before they had purposes sit in compiled packs that every reader may see.
        await _recompile(uow, config, {t.key.origin_handle_id for t in tagged}, (TaskName.COMPILE,))
    return TagOutcome(len(tagged), tuple(removed))


async def objections_for(uow: UnitOfWork, handle_ids: Collection[UUID]) -> dict[UUID, frozenset[str]]:
    """Per handle, the purposes its customer objected to: on a confirmed handle, every objection on the
    profile's confirmed handles; on a handle joined by an unconfirmed claim, its own only (5.3)."""
    found: dict[UUID, frozenset[str]] = {}
    for handle_id in sorted(handle_ids, key=str):
        profile = await uow.identity.profile_of(handle_id)
        view = derivation_view(profile) if profile else None
        scope = list(view.handle_ids) if view and handle_id in view.handle_ids else [handle_id]
        mine = await uow.memory.objections(scope)
        found[handle_id] = frozenset(p for purposes in mine.values() for p in purposes)
    return {h: p for h, p in found.items() if p}


async def _remove(
    uow: UnitOfWork, config: SpaceConfig, keys: Sequence[ItemKey], now: datetime
) -> list[ErasedRow]:
    """Deletes the rows, leaves the export's tombstones and asks for the owners' packs and patterns again."""
    if not keys:
        return []
    rows = await uow.memory.remove_items(keys)
    exported = [r for r in rows if r.table in EXPORT_TABLES]
    if exported:
        await uow.privacy.add_tombstones(config.space_id, exported, now)
    tasks = (TaskName.COMPILE, TaskName.DERIVE_TRAITS)
    await _recompile(uow, config, {k.origin_handle_id for k in keys}, tasks)
    return rows


async def _recompile(
    uow: UnitOfWork, config: SpaceConfig, handles: Collection[UUID], tasks: Sequence[TaskName]
) -> None:
    owners = {p.profile_id for h in handles if (p := await uow.identity.profile_of(h)) is not None}
    for profile_id in sorted(owners, key=str):
        for name in tasks:
            task = Task(name, config.space_id, {"reason": "purpose"}, profile_id=profile_id)
            await uow.pipeline.request_work(task)


def fingerprint(config: SpaceConfig) -> int:
    """What decides a row's tags: when it changes, the rows no rule named are tagged again."""
    document = {
        "purposes": config.purposes.model_dump(mode="json"),
        "sources": sorted((str(s.source_id), sorted(s.purposes)) for s in config.sources),
    }
    return zlib.crc32(json.dumps(document, sort_keys=True).encode())


class PurposeService:
    def __init__(self, uow: UnitOfWorkFactory, clock: Clock, config: ConfigProvider) -> None:
        self._uow = uow
        self._clock = clock
        self._config = config

    async def step(self, space_id: UUID) -> int:
        """One pass of the per-space loop: rows no rule named tagged again after the document changed,
        rows written before it tagged, and once a day the expiry and the objections. Returns the work
        found, so an idle space is visited less often."""
        try:
            config = self._config.space(space_id)
        except NotFound:
            return 0
        if not configured(config.purposes):
            return 0
        now = self._clock.now()
        work = 0
        async with self._uow(space_id) as uow:
            mark = fingerprint(config)
            if await uow.memory.purpose_state(FINGERPRINT) != mark:
                reset = await uow.memory.untag_empty(limit=BACKFILL_BATCH)
                work += reset
                if reset < BACKFILL_BATCH:
                    await uow.memory.set_purpose_state(FINGERPRINT, mark)
        async with self._uow(space_id) as uow:
            outcome = await tag_rows(uow, config, None, now, limit=BACKFILL_BATCH, recompile=True)
            work += outcome.tagged + len(outcome.removed)
        work += await self._daily(space_id, config, now)
        return work

    async def _daily(self, space_id: UUID, config: SpaceConfig, now: datetime) -> int:
        local = now.astimezone(ZoneInfo(config.settings.timezone))
        if local.hour < NIGHT_HOUR:
            return 0
        day = local.date().toordinal()
        async with self._uow(space_id) as uow:
            if await uow.memory.purpose_state(DAILY) == day:
                return 0
            await uow.memory.set_purpose_state(DAILY, day)
        removed = await self.expire(space_id, now)
        narrowed = await self.enforce_objections(space_id)
        if removed or narrowed:
            log.info("purposes: %d rows expired, %d rows under an objection", removed, narrowed)
        return removed + narrowed

    async def expire(self, space_id: UUID, now: datetime | None = None) -> int:
        """Removes the rows whose every purpose's retention ran out."""
        config = self._config.space(space_id)
        now = now or self._clock.now()
        cuts = cutoffs(config.purposes, now)
        if not retention(config.purposes):
            return 0
        removed = 0
        for _ in range(EXPIRY_BATCHES):
            async with self._uow(space_id) as uow:
                keys = await uow.memory.purpose_expired(cuts, limit=EXPIRY_BATCH)
                await _remove(uow, config, keys, now)
            removed += len(keys)
            if len(keys) < EXPIRY_BATCH:
                break
        return removed

    async def enforce_objections(self, space_id: UUID) -> int:
        """Every objection again, on the rows of the customer's confirmed handles: rows a merge or a
        confirmation brought under it since it was made."""
        config = self._config.space(space_id)
        now = self._clock.now()
        changed = 0
        after: tuple[UUID, str] | None = None
        while True:
            async with self._uow(space_id) as uow:
                page = await uow.memory.objected_handles(after=after, limit=OBJECTION_PAGE)
                for handle_id, purpose in page:
                    profile = await uow.identity.profile_of(handle_id)
                    view = derivation_view(profile) if profile else None
                    handles = list(view.handle_ids) if view and handle_id in view.handle_ids else [handle_id]
                    changed += sum(await _apply_objection(uow, config, handles, purpose, now))
            if len(page) < OBJECTION_PAGE:
                return changed
            after = page[-1]

    async def object(self, space_id: UUID, request: ForgetRequest, *, idempotency_key: str) -> ErasureOut:
        """A data subject's objection to one purpose (LGPD art. 18 par. 2, GDPR art. 21), or the withdrawal
        of a consent (GDPR art. 7(3), LGPD art. 18 IX): recorded on the customer's confirmed handles, and
        every row collected for it leaves, or keeps only its other purposes. The events stay: they are the
        record of what was said, and a later extraction of them leaves the objected purpose out. Whether
        the objection applies is the company's decision; the product carries it out."""
        assert request.purpose is not None
        config = self._config.space(space_id)
        if request.purpose not in {p.name for p in config.purposes.purposes}:
            raise InvalidInput("the purpose is not declared in the space's purposes document")
        now = self._clock.now()
        request_id = request_id_for(space_id, f"objection:{request.purpose}:{idempotency_key}")
        async with self._uow(space_id) as uow:
            profile, handles, target_id = await _subject(uow, request)
            await uow.memory.add_objection(handles, request.purpose, now)
            if request.purpose == SIGNALS_PURPOSE:
                # The subject's signals go, and their derivation stops for good (24.5.3).
                withdraw = {"handles": [str(h) for h in handles]}
                await uow.pipeline.enqueue([Task(TaskName.WITHDRAW_SIGNALS, space_id, withdraw)])
            erased: list[ErasedRow] = []
            narrowed, removed = await _apply_objection(uow, config, handles, request.purpose, now, erased)
            if profile is not None and (narrowed or removed):
                version = await uow.identity.bump_privacy_version(profile.profile_id)
                await uow.privacy.record_feed(
                    kind="retraction",
                    profile_ids=[profile.profile_id],
                    handle_ids=list(handles),
                    privacy_version=version,
                )
        counts: dict[str, int] = {}
        for row in erased:
            counts[row.table] = counts.get(row.table, 0) + 1
        if narrowed:
            counts["purpose_narrowed"] = narrowed
        return ErasureOut(
            request_id=str(request_id),
            status="completed",
            target_kind=request.target,
            target_id=target_id,
            requested_at=now,
            completed_at=now,
            erased=counts,
        )


async def _apply_objection(
    uow: UnitOfWork,
    config: SpaceConfig,
    handles: Sequence[UUID],
    purpose: str,
    now: datetime,
    erased: list[ErasedRow] | None = None,
) -> tuple[int, int]:
    """The objection on the handles' rows: narrowed ones keep their other purposes, the rest go. Returns
    how many were narrowed and how many went."""
    narrowed: list[Tagged] = []
    gone: list[ItemKey] = []
    for row in await uow.memory.tagged_with(handles, purpose):
        outcome, tags = after_objection(row.tags, {purpose})
        if outcome is Objection.NARROWED:
            narrowed.append(Tagged(row.key, tags))
        elif outcome is Objection.REMOVED:
            gone.append(row.key)
    await uow.memory.set_purposes(narrowed)
    rows = await _remove(uow, config, gone, now)
    if erased is not None:
        erased += rows
    if narrowed and not gone:
        await _recompile(uow, config, {k.key.origin_handle_id for k in narrowed}, (TaskName.COMPILE,))
    return len(narrowed), len(gone)


async def _subject(uow: UnitOfWork, request: ForgetRequest) -> tuple[ProfileRecord | None, list[UUID], str]:
    """The customer an objection names and the handles it is recorded on: the profile's confirmed ones, or
    the one handle named when it was joined by an unconfirmed claim."""
    if request.target == "profile" and request.profile_id is not None:
        profile = await uow.identity.get_profile(request.profile_id)
        if profile is None:
            raise NotFound("unknown profile")
        return profile, list(derivation_view(profile).handle_ids), str(profile.profile_id)
    if request.target == "handle" and request.handle is not None:
        h = request.handle
        key = normalize_handle(h.type, h.value, h.scope, h.subject_kind).key
        handle = await uow.identity.find_handle(key.type, key.scope, key.value)
        if handle is None:
            raise NotFound("unknown handle")
        profile = await uow.identity.profile_of(handle.handle_id)
        view = derivation_view(profile) if profile else None
        handles = (
            list(view.handle_ids) if view and handle.handle_id in view.handle_ids else [handle.handle_id]
        )
        return profile, handles, str(handle.handle_id)
    raise InvalidInput("an objection names a profile or a handle")
