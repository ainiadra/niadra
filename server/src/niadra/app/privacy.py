"""Forget, retention, the cold archive and exports (5.14, 8.4, 22.3).

The erasure cascade crosses modules: privacy drives it, and each owner erases its own rows through
`ErasureParticipant` (4.3: a cross-module use case lives in the application layer, over both ports).
Every batch is its own short transaction, well under the 60 s cap on write transactions (6).
"""

import dataclasses
import hashlib
import json
import logging
from collections import defaultdict
from collections.abc import AsyncIterator, Awaitable, Callable, Collection, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any, Literal, cast
from uuid import UUID

from niadra.app.archive import ArchiveReader, content_aad, index_key
from niadra.app.collaborators import IntakeSettler
from niadra.app.holds import LegalHoldsService, exported_holds
from niadra.app.metering import record_usage_in
from niadra.app.named_rows import NamedRowsService
from niadra.app.notify import enqueue_event
from niadra.app.object_memory import participant_profiles, ref_of
from niadra.app.outcomes import exported_outcomes
from niadra.app.profile_view import traits_in_view
from niadra.app.raw_index import raw_object, sealed_tokens
from niadra.app.review_requests import exported_review_requests
from niadra.app.signals import INACTIVE as SIGNALS_INACTIVE
from niadra.app.signals import (
    exported_beneficiaries,
    exported_interactions,
    exported_signals,
    exported_suppressions,
)
from niadra.config.models import ExportSchedule, SpaceConfig
from niadra.contracts.common import Handle, Page
from niadra.contracts.privacy import ErasureOut, ExportPackageOut, ExportRequest, ExportRunOut, ForgetRequest
from niadra.domain.common.errors import Conflict, InvalidInput, NiadraError, NotFound
from niadra.domain.common.ids import uuid7, uuid7_ms
from niadra.domain.common.space import owns_secret, secret_prefix
from niadra.domain.coordinate.destination import SUBJECT_HMAC, subject_canonical
from niadra.domain.identity.handles import normalize_handle
from niadra.domain.identity.provenance import reader_view
from niadra.domain.ingest.events import export_row, is_feedback, record_from_json
from niadra.domain.ingest.storage import NO_HANDLE
from niadra.domain.metering import Metric
from niadra.domain.notify.cursor import decode_cursor, encode_cursor
from niadra.domain.notify.feed import FEED_KEEP
from niadra.domain.pipeline.delay import DelayReason
from niadra.domain.privacy.erasure import (
    COLD_FEEDBACK,
    EVENTS_STEP,
    EVIDENCE_STEPS,
    FEEDBACK_LINKS,
    FEEDBACK_VALUES,
    LEDGER_DAYS,
    MAX_OBJECTS_TOUCHED,
    OBJECT_BOUND,
    OBJECTS_TOUCHED,
    ORIGIN_STEPS,
    SESSION_ARTIFACTS_STEP,
    ErasedRow,
    ErasureRequest,
    ErasureScope,
    ErasureStatus,
    TargetKind,
    blob_prefixes,
    erasure_receipt_hash,
    feedback_audit,
    ledger_aad,
    ledger_entry,
    ledger_key,
    ledger_prefix,
    request_id_for,
    tally,
)
from niadra.domain.privacy.export import (
    EXPORT_TABLES,
    ON_DEMAND,
    SCHEMA_VERSION,
    SETTLE,
    ExportRun,
    ExportStatus,
    TombstoneRecord,
    corrections,
    file_path,
    manifest_path,
    pseudonymized,
    settled,
    split_erased,
)
from niadra.domain.privacy.files import JsonLinesZip, from_parquet, json_line, sha256_hex, to_parquet
from niadra.domain.privacy.holds import Held
from niadra.domain.privacy.mentions import (
    ARCHIVE_MENTIONS_READY,
    MENTION_PAGE,
    Redacted,
    archived_at,
    archived_key,
)
from niadra.domain.privacy.raw_index import (
    RAW_INDEX_PAGE,
    RawObject,
    event_tokens,
    handle_tokens,
    redact_body,
    redact_event,
)
from niadra.domain.privacy.retention import (
    DataClass,
    cutoffs,
    month_bounds,
    month_expired,
    month_key,
    month_to_archive,
    raw_content_at,
    raw_content_prefixes,
)
from niadra.domain.privacy.reviews import KEPT_DAYS as REVIEW_KEPT_DAYS
from niadra.domain.records import BusinessObject, EventRecord, HandleRecord, ObjectKey, ProfileRecord
from niadra.domain.signals.body import BODY_VERSION, decode, encode, tells, told
from niadra.domain.signals.records import SELF, SESSION_PURPOSE, SignalRow, body_aad, session_digest
from niadra.domain.signals.storage import cache_key as signals_cache_key
from niadra.domain.tasks import Task, TaskName
from niadra.domain.turns.storage import (
    BY_ID,
    KeptBy,
    Tier,
    by_id_key,
    conversation_prefixes,
    short_turn_ids,
    turn_key,
)
from niadra.domain.turns.storage import HMAC_PURPOSE as TURN_HMAC
from niadra.domain.vocabulary import FactStatus, OpenItemStatus
from niadra.ports.erasure import ErasureFence, KeptAside, NamedInText, Recordings
from niadra.ports.holds import LegalHolds
from niadra.ports.infra import (
    Clock,
    ConfigProvider,
    HotLayer,
    KeyValueCache,
    KeyVault,
    ObjectStore,
    SecretStore,
)
from niadra.ports.repos.privacy import ErasureParticipant, ExportDestinations, ExportSink, ExportSource
from niadra.ports.signals import NoObjectInstructions, ObjectInstructions
from niadra.ports.uow import UnitOfWork, UnitOfWorkFactory

log = logging.getLogger(__name__)

EPOCH = datetime(2000, 1, 1, tzinfo=UTC)
STALE_RUN = timedelta(hours=6)
PACKAGE_PAGE = 1_000
PACKAGE_VALUES = 100_000
"""A customer settles a handful of values per conversation: one read holds all of them."""
PACKAGE_CHUNK = 1 << 20
ARCHIVE_EPOCH = datetime(2026, 1, 1, tzinfo=UTC)
"""No space holds events from before the product existed; the search for months left behind starts here."""
DOWNLOAD_TTL = timedelta(minutes=15)
# A package is the subject's data decrypted; it stays downloadable for a day, then leaves storage.
PACKAGE_TTL = timedelta(hours=24)
PARQUET = "application/vnd.apache.parquet"
REBUILT = (TaskName.COMPILE, TaskName.DERIVE_TRAITS, TaskName.CONSOLIDATE)
"""What an erasure has made again for the people whose memory named the target and was redacted."""
COLD_FEEDBACK_VALUES = "cold_feedback_values"
"""The count, on the request, of archived feedback events `erase_cold` left with ids and kinds only."""
FEEDBACK_STEP = "memory"
DAY_MARKERS = "_days"
"""The handle segment of the raw interactions' day markers, which the nightly pass deletes once read."""
"""The origin step whose rows carry the company's feedback on them (`_scrub_feedback`)."""

SecretKind = Literal["webhook", "model", "gateway"]


class PrivacyService:
    def __init__(
        self,
        uow: UnitOfWorkFactory,
        keys: KeyVault,
        objects: ObjectStore,
        hot: HotLayer,
        clock: Clock,
        config: ConfigProvider,
        *,
        secrets: SecretStore,
        destinations: ExportDestinations,
        batch_size: int = 500,
        archive_part_rows: int = 50_000,
        cache: KeyValueCache | None = None,
        intake: IntakeSettler | None = None,
        holds: LegalHolds | None = None,
        fences: Sequence[ErasureFence] = (),
        instructions: ObjectInstructions | None = None,
    ) -> None:
        """`intake` stores what ingestion acknowledged and has not stored yet, before an erasure or a
        package reads the log: what was sent before the request is in it. `holds` says what legal holds keep
        from purging (24.5.1). `fences` are the modules that write a subject's data outside their transaction:
        an erasure fences them before its first row goes and purges them once it is done (`ports.erasure`).
        `instructions` reads what the person said must or must not be done about an object, counted on the
        receipt with the other rows bound to objects."""
        self._holds: LegalHolds = holds if holds is not None else LegalHoldsService(uow, keys, clock)
        self._cache = cache
        self._intake = intake
        self._uow = uow
        self._keys = keys
        self._objects = objects
        self._hot = hot
        self._clock = clock
        self._config = config
        self._secrets = secrets
        self._destinations = destinations
        self._batch = batch_size
        self._part_rows = archive_part_rows
        self._fences = tuple(fences)
        self._instructions: ObjectInstructions = instructions or NoObjectInstructions()
        self._archive = ArchiveReader(objects, keys, config)
        self._named_rows = NamedRowsService(uow, keys)

    async def forget(
        self, space_id: UUID, request: ForgetRequest, *, idempotency_key: str, requested_by: str
    ) -> ErasureOut:
        """Records the request and enqueues `erase` under the profile lease (8.4); replays get it back."""
        if not idempotency_key:
            raise InvalidInput("forget requires an Idempotency-Key")
        request_id = request_id_for(space_id, idempotency_key)
        body_hash = await self._request_hash(space_id, request)
        async with self._uow(space_id) as uow:
            existing = await uow.privacy.get_erasure(space_id, request_id)
            if existing is not None:
                _same_request(existing, body_hash)
                return erasure_out(existing)
            now = self._clock.now()
            scope = await _resolve(uow, request, request_id, now)
            created = ErasureRequest(
                request_id=request_id,
                target_kind=scope.kind,
                target_id=_target_id(request, scope),
                status=ErasureStatus.PENDING,
                requested_by=requested_by,
                requested_at=now,
                counts={"scope": scope.to_json(), "request_hash": body_hash},
            )
            stored, is_new = await uow.privacy.create_erasure(space_id, created)
            if is_new:
                erase = Task(
                    TaskName.ERASE, space_id, {"request_id": str(request_id)}, profile_id=scope.profile_id
                )
                await uow.pipeline.enqueue([erase])
        return erasure_out(stored)

    async def _request_hash(self, space_id: UUID, request: ForgetRequest) -> str:
        """The body a key was first used with (7.0), keyed per space: a phone number never sits in the
        request row, not even as a plain digest a dictionary could reverse."""
        target: dict[str, Any] = {"target": request.target}
        if request.handle is not None:
            key = normalize_handle(
                request.handle.type, request.handle.value, request.handle.scope, request.handle.subject_kind
            ).key
            target["handle"] = [key.type.value, key.scope, key.value]
        else:
            target["id"] = str(request.profile_id or request.conversation_id)
        body = json.dumps(target, sort_keys=True, separators=(",", ":")).encode()
        return (await self._keys.hmac(space_id, "forget_request", body)).hex()

    async def erasure_status(self, space_id: UUID, request_id: UUID) -> ErasureOut:
        async with self._uow(space_id) as uow:
            request = await uow.privacy.get_erasure(space_id, request_id)
        if request is None:
            raise NotFound("unknown erasure request")
        return erasure_out(request)

    async def erasures(
        self, space_id: UUID, *, status: ErasureStatus | None, cursor: str | None, limit: int
    ) -> Page[ErasureOut]:
        """The space's requests, newest first, for the Console's list (5.22)."""
        async with self._uow(space_id) as uow:
            found = await uow.privacy.list_erasures(
                space_id, status=status, before=decode_cursor(cursor), limit=limit + 1
            )
        page = found[:limit]
        more = len(found) > limit
        next_cursor = encode_cursor(page[-1].requested_at, page[-1].request_id) if more else None
        return Page[ErasureOut](items=[erasure_out(r) for r in page], next_cursor=next_cursor)

    async def erase(self, task: Task) -> None:
        """The cascade of 8.4, resumable: every step only returns rows it has not erased yet."""
        space_id, request_id = task.space_id, UUID(task.payload["request_id"])
        async with self._uow(space_id) as uow:
            request = await uow.privacy.get_erasure(space_id, request_id)
        if request is None or request.status is ErasureStatus.COMPLETED:
            return
        if self._intake is not None:
            await self._intake.settle(space_id)
        # What the target's archived events produced goes like what its live events produced.
        scope = await self._with_turns(space_id, await self._with_archive(space_id, request.scope))
        # Turn records under a legal hold leave every read with the rest and wait for the release (24.5.1).
        held = await self._holds.held(space_id)
        waiting = tuple(h for h in scope.conversation_hashes if h in held.conversations)
        scope = dataclasses.replace(
            scope, conversation_hashes=tuple(h for h in scope.conversation_hashes if h not in waiting)
        )
        scope = await self._with_subjects(space_id, scope)
        # What is on its way to being written about the target stops before the first row goes.
        for fence in self._fences:
            await fence.fence(space_id, scope)
        # After the fences, which add up what waited (a turn's part merges into its turn): it is redacted too.
        await self._erase_named(space_id, request_id, scope, held)
        cold = await self._cold_feedback(space_id, scope)
        async with self._uow(space_id) as uow:
            request = await uow.privacy.get_erasure(space_id, request_id)
            assert request is not None
            # Reads stop serving the old pack now, before the first row goes (5.11).
            await _announce(uow, scope)
            counts = {**request.counts, "scope": scope.to_json()}
            if ACTS_FOR not in counts:
                # The organizations the person speaks for carry what they said about them:
                # read before the links go, their packs are compiled again at the end.
                counts[ACTS_FOR] = [str(p) for p in await _acts_for(uow, scope.handle_ids)]
                # What they said must or must not be done about an object lives in their signals, whose rows
                # say nothing of it once sealed: counted now, before they go, with the other bound rows.
                # The packs of others that carry what the person said (the organizations they speak for, the
                # owners and participants of the objects they take part in) stop being served too.
                mine = await uow.objects.objects_for(list(scope.handle_ids), limit=MAX_OBJECTS_TOUCHED)
                carriers = {
                    *map(UUID, counts[ACTS_FOR]),
                    *await participant_profiles(uow, [o.key for o in mine]),
                }
                await _announce_carriers(uow, carriers - {scope.profile_id})
                said = await _object_instructions(uow, space_id, scope.handle_ids, mine, self._instructions)
                if said:
                    counts[OBJECT_BOUND] = int(counts.get(OBJECT_BOUND, 0)) + len(said)
                    touched = {*counts.get(OBJECTS_TOUCHED, []), *said}
                    counts[OBJECTS_TOUCHED] = sorted(touched)[:MAX_OBJECTS_TOUCHED]
            await uow.privacy.save_erasure(
                space_id, dataclasses.replace(request, status=ErasureStatus.RUNNING, counts=counts)
            )

        async def notes_batch(uow: UnitOfWork) -> tuple[list[ErasedRow], bool]:
            # Before the conversations' rows go, since their aliases name them too.
            names = {*await uow.ingest.conversation_names(scope)}
            if scope.conversation_id is not None:
                names.add(scope.conversation_id)
            rows = await uow.agent_memory.forget_conversations(sorted(names))
            # Interactions of these conversations still waiting for them to name a subject: once the
            # conversations' rows go, nothing would say whose they were.
            sessions = [
                session_digest(await self._keys.hmac(space_id, SESSION_PURPOSE, name.encode()))
                for name in sorted(names)
            ]
            return [*rows, *await uow.signals.drop_unattributed(sessions)], True

        async def events_batch(uow: UnitOfWork) -> tuple[list[ErasedRow], bool]:
            erased = await _participants(uow)[EVENTS_STEP].erase_for(scope, limit=self._batch)
            event_ids = tuple(UUID(r.key["event_id"]) for r in erased if r.table == "events")
            rows = [*erased, *await self._evidence(uow, scope, event_ids)]
            rows += await _scrub_feedback(uow, rows, cold)
            return rows, len(erased) < self._batch

        def archived_batch(
            event_ids: tuple[UUID, ...],
        ) -> Callable[[UnitOfWork], Awaitable[tuple[list[ErasedRow], bool]]]:
            async def batch(uow: UnitOfWork) -> tuple[list[ErasedRow], bool]:
                rows = await self._evidence(uow, scope, event_ids)
                rows += await _scrub_feedback(uow, rows, cold)
                return rows, True

            return batch

        await self._drain(space_id, request_id, notes_batch)
        # Events leave as the system role: the application role may add events, never change or delete them.
        await self._drain(space_id, request_id, events_batch, system=True)
        archived = scope.archived_event_ids
        for start in range(0, len(archived), self._batch):
            chunk = archived[start : start + self._batch]
            await self._drain(space_id, request_id, archived_batch(chunk), system=True)
        for module in ORIGIN_STEPS:
            # Memory's step rewrites the feedback on the rows it takes, which only the system role may do.
            system = module == FEEDBACK_STEP
            await self._drain(space_id, request_id, self._origin_batch(module, scope, cold), system=system)
        for fence in self._fences:
            await fence.purge(space_id, scope)
        await self._hot.purge(space_id, list(scope.handle_ids))
        # Subject signals keep a session overlay, a cache copy and raw interactions outside the database:
        # their own task removes them (24.5.5).
        async with self._uow(space_id) as uow:
            await uow.pipeline.enqueue(
                [Task(TaskName.PURGE_SIGNALS, space_id, {"request_id": str(request_id)})]
            )
        if scope.profile_id is not None and self._cache is not None:
            # Compiled context of the profile lives in the cache too; the read path already refuses it by
            # privacy version, and now it leaves the store instead of waiting for its expiry.
            try:
                await self._cache.purge_profile(space_id, scope.profile_id)
            except (ConnectionError, TimeoutError, OSError):
                log.warning("compiled context not purged from the cache; it expires on its own")
        if scope.kind is not TargetKind.CONVERSATION:
            for prefix in blob_prefixes(space_id, scope.handle_ids):
                await self._objects.delete_prefix(prefix)
            async with self._uow(space_id) as uow:
                await uow.privacy.unindex_raw_owned(space_id, [str(h) for h in scope.handle_ids])
        turn_objects = 0
        for conversation in scope.conversation_hashes:
            turn_objects += await self._erase_turn_objects(space_id, conversation)
        if turn_objects:
            await self._count(space_id, request_id, "turn_objects", turn_objects)
        if scope.profile_id is not None:
            # A package made before the request holds what was just erased, decrypted.
            packages = await self._delete_packages(space_id, profile_id=scope.profile_id)
            if packages:
                await self._count(space_id, request_id, "export_packages", packages)
        await self._retain(space_id, request_id, scope, waiting, held)
        await self._write_ledger(space_id, request_id)
        await self._complete(space_id, request_id)

    async def _retain(
        self, space_id: UUID, request_id: UUID, scope: ErasureScope, waiting: tuple[bytes, ...], held: Held
    ) -> None:
        """What the erasure leaves, by class, for its receipt (24.5.5): the turn records a legal hold keeps
        until its release, the subject's review requests and the holds themselves, both kept by a legal
        obligation. The held conversations stay on the request for `release_held`."""
        async with self._uow(space_id) as uow:
            requests = await uow.review_requests.for_profiles([scope.profile_id] if scope.profile_id else [])
            holds = await uow.legal_holds.holds_on(scope.handle_ids, [*scope.conversation_hashes, *waiting])
            request = await uow.privacy.get_erasure(space_id, request_id)
            assert request is not None
            retained = {
                name: count
                for name, count in (
                    ("held_turn_conversations", len(waiting)),
                    ("review_requests", len(requests)),
                    ("legal_holds", len(holds)),
                )
                if count
            }
            counts = {**request.counts, "retained": retained}
            if waiting:
                counts["held"] = {
                    "conversations": [h.hex() for h in waiting],
                    "holds": [str(h) for h in held.holds_on(waiting)],
                }
            await uow.privacy.save_erasure(space_id, dataclasses.replace(request, counts=counts))

    async def release_held(self, task: Task) -> None:
        """A legal hold released (24.5.1): the turn records completed erasures left waiting under it go now,
        unless another hold still keeps them, and each request's counts say so."""
        space_id = task.space_id
        held = await self._holds.held(space_id)
        before: tuple[datetime, UUID] | None = None
        while True:
            async with self._uow(space_id) as uow:
                page = await uow.privacy.list_erasures(
                    space_id, status=ErasureStatus.COMPLETED, before=before, limit=self._batch
                )
            for request in page:
                waiting = [bytes.fromhex(h) for h in request.counts.get("held", {}).get("conversations", [])]
                free = tuple(h for h in waiting if h not in held.conversations)
                if free:
                    await self._finish_held(space_id, request, free, held)
            if len(page) < self._batch:
                return
            before = (page[-1].requested_at, page[-1].request_id)

    async def _finish_held(
        self, space_id: UUID, request: ErasureRequest, free: tuple[bytes, ...], held: Held
    ) -> None:
        scope = dataclasses.replace(request.scope, conversation_hashes=free)
        await self._drain(space_id, request.request_id, self._origin_batch("turns", scope, lambda _: False))
        objects = 0
        for conversation in free:
            objects += await self._erase_turn_objects(space_id, conversation)
        async with self._uow(space_id) as uow:
            current = await uow.privacy.get_erasure(space_id, request.request_id)
            assert current is not None
            still = tuple(
                bytes.fromhex(h)
                for h in current.counts.get("held", {}).get("conversations", [])
                if bytes.fromhex(h) not in free
            )
            retained = {**current.counts.get("retained", {}), "held_turn_conversations": len(still)}
            counts = {
                **current.counts,
                "turn_objects": int(current.counts.get("turn_objects", 0)) + objects,
                "retained": {k: v for k, v in retained.items() if v},
            }
            counts.pop("held", None)
            if still:
                counts["held"] = {
                    "conversations": [h.hex() for h in still],
                    "holds": [str(h) for h in held.holds_on(still)],
                }
            await uow.privacy.save_erasure(space_id, dataclasses.replace(current, counts=counts))

    async def _evidence(
        self, uow: UnitOfWork, scope: ErasureScope, event_ids: tuple[UUID, ...]
    ) -> list[ErasedRow]:
        """What pointed at `event_ids`: mentions, trait evidence, actions, firings, and the rows left
        without evidence (L17)."""
        rows: list[ErasedRow] = []
        for module in EVIDENCE_STEPS if event_ids else ():
            rows += await self._erase_all(_participants(uow)[module], scope.with_events(event_ids))
        return rows

    async def _with_turns(self, space_id: UUID, scope: ErasureScope) -> ErasureScope:
        """The target's conversations by the keyed hash turn records are kept under (24.5), found while the
        conversations' rows still exist; kept with the request, so a retry erases the same ones."""
        async with self._uow(space_id) as uow:
            names = set(await uow.ingest.conversation_names(scope))
        if scope.conversation_id is not None:
            names.add(scope.conversation_id)
        hashes = [await self._keys.hmac(space_id, TURN_HMAC, name.encode()) for name in sorted(names)]
        return scope.with_conversation_hashes(tuple(hashes))

    async def _with_subjects(self, space_id: UUID, scope: ErasureScope) -> ErasureScope:
        """The keyed hashes coordination names the target's handles by (24.5.5), found while the handles
        still exist; kept with the request, so a retry erases the same subjects. A conversation's erasure
        keeps the person's coordination."""
        if scope.kind is TargetKind.CONVERSATION or not scope.handle_ids:
            return scope
        return scope.with_subject_hashes(await self._subject_hashes(space_id, scope.handle_ids))

    async def _subject_hashes(self, space_id: UUID, handle_ids: tuple[UUID, ...]) -> tuple[bytes, ...]:
        """The keyed hashes coordination names these handles by (24.3.4)."""
        if not handle_ids:
            return ()
        async with self._uow(space_id) as uow:
            handles = await uow.identity.get_handles(handle_ids)
        hashes = []
        for handle in handles:
            if not handle.value:
                continue
            try:
                canonical, _ = subject_canonical(
                    handle.type, handle.value, handle.scope or None, handle.subject_kind
                )
            except NiadraError:
                continue  # a handle coordination never keyed a subject by
            hashes.append(await self._keys.hmac(space_id, SUBJECT_HMAC, canonical.encode()))
        return tuple(hashes)

    async def _erase_turn_objects(self, space_id: UUID, conversation: bytes) -> int:
        """One conversation's turn records by prefix, a legal hold's copies excepted, and the short tier's
        by-id entries."""
        prefixes = conversation_prefixes(space_id, conversation)
        for turn_id in short_turn_ids(space_id, conversation, await self._objects.list_keys(prefixes[0])):
            await self._objects.delete(by_id_key(space_id, turn_id))
        return sum([await self._objects.delete_prefix(prefix) for prefix in prefixes])

    async def _with_archive(self, space_id: UUID, scope: ErasureScope) -> ErasureScope:
        """The target's events in the cold archive, by the columns each row keeps in the clear (event id,
        conversation, handles), so nothing is decrypted. A retried or re-applied request keeps the ids it
        found before: `erase_cold` may have taken those rows from the archive since."""
        events: list[UUID] = []
        handles: list[UUID] = []
        index = await self._archive_index(space_id)
        async for _, rows in self._archive_parts(index):
            for row in rows:
                if _archived_row_hit(row, scope):
                    events.append(UUID(row["event_id"]))
                    handles += [UUID(h) for h in row.get("handle_ids") or ()]
        return scope.with_archive(tuple(events), tuple(dict.fromkeys(handles)))

    async def _cold_feedback(self, space_id: UUID, scope: ErasureScope) -> Callable[[UUID], bool]:
        """Whether an event the cascade named but no longer finds live may be a feedback event in the cold
        archive: its month is archived, and it is not one of the target's own rows, which `erase_cold`
        drops whole."""
        months = set((await self._archive_index(space_id))["months"])
        own = set(scope.archived_event_ids)
        return lambda event_id: event_id not in own and _id_month(event_id) in months

    async def _write_ledger(self, space_id: UUID, request_id: UUID) -> None:
        """Before the request completes, so a retried task writes it again; a restore reads it."""
        async with self._uow(space_id) as uow:
            request = await uow.privacy.get_erasure(space_id, request_id)
        assert request is not None
        data = await seal_ledger(self._keys, space_id, request)
        await self._objects.put(ledger_key(space_id, request, self._clock.now()), data, "application/json")

    async def _erase_named(self, space_id: UUID, request_id: UUID, scope: ErasureScope, held: Held) -> None:
        """What names the target without being kept under one of their handles, found by the index of raw
        content and the indexes of what rows, recordings and archived events write (`NamedInText`,
        `domain.privacy.mentions`), never by listing or reading the space. Runs after the fences, so what
        they added up is reached too, and before the cascade, while the handles still exist.

        A payload no mapping rule matched is kept under no handle (`domain.ingest.storage`), so no prefix
        reaches it: it goes. One kept under someone else's handle is theirs: it keeps, with the target's
        phones, e-mails, documents and ids replaced by `ERASED`, and so do the events, derived memory, turn
        recordings and archived events of others that name them. What a legal hold keeps is not rewritten,
        and the request counts it (`named_held`). The owners' packs stop being served and are compiled
        again. The target's own go with their prefix, after."""
        if scope.kind is TargetKind.CONVERSATION or not scope.handle_ids:
            return
        await self._index_written_before(space_id)
        async with self._uow(space_id) as uow:
            handles = await uow.identity.get_handles(scope.handle_ids)
        plain = handle_tokens((h.type, h.value) for h in handles)
        tokens = await sealed_tokens(self._keys, space_id, plain)
        if not tokens:
            return
        async with self._uow(space_id) as uow:
            rows = await uow.privacy.raw_named(space_id, tokens)
        own = {str(h) for h in scope.handle_ids}
        unowned = [r.key for r in rows if r.owner == NO_HANDLE]
        for key in unowned:
            await self._objects.delete(key)
        if unowned:
            async with self._uow(space_id) as uow:
                await uow.privacy.unindex_raw(space_id, unowned)
            await self._count(space_id, request_id, "unmapped_payloads", len(unowned))
        redacted = 0
        kept = Redacted()
        for row in rows:
            if row.owner == NO_HANDLE or row.owner in own:
                continue
            if UUID(row.owner) in held.handles:
                kept.held += 1
                continue
            redacted += await self._redact_raw(space_id, row, plain)
        if redacted:
            await self._count(space_id, request_id, "named_payloads_redacted", redacted)
        for module in self._named_in_text():
            done = await module.redact_named(space_id, scope, plain, tokens, held)
            if done.rows:
                events = cast(object, module) is self._intake
                key = "named_events_redacted" if events else "named_rows_redacted"
                await self._count(space_id, request_id, key, done.rows)
            if done.in_source:
                # Mirrored records: the company's system keeps its own copy, for the company to erase.
                await self._count(space_id, request_id, "named_objects_in_source", done.in_source)
            kept.add(done)
        archived = await self._redact_archive(space_id, scope, plain, tokens, held)
        if archived.rows:
            await self._count(space_id, request_id, "named_archived_redacted", archived.rows)
        kept.add(archived)
        if kept.held:
            await self._count(space_id, request_id, "named_held", kept.held)
        await self._rebuild_named(space_id, scope, kept)

    async def _rebuild_named(self, space_id: UUID, scope: ErasureScope, done: Redacted) -> None:
        """The profiles whose memory changed: their packs stop being served (the privacy version moves and
        the cache drops them), the read path's copies of their objects are read again from the database (a
        retraction marks the handles that own the rewritten rows), their vectors are made again from the
        redacted text, and their packs, traits and summaries are compiled again."""
        async with self._uow(space_id) as uow:
            if others := done.handles - set(scope.handle_ids):
                await uow.privacy.record_feed(
                    kind="retraction", profile_ids=[], handle_ids=sorted(others, key=str), privacy_version=0
                )
            owners = set(done.profiles)
            for handle_id in sorted(done.handles, key=str):
                if (profile := await uow.identity.profile_of(handle_id)) is not None:
                    owners.add(profile.profile_id)
            if scope.profile_id is not None:
                owners.discard(scope.profile_id)
            if not owners:
                return
            await _announce_carriers(uow, owners)
            for profile_id in sorted(owners, key=str):
                await uow.pipeline.enqueue([Task(TaskName.EMBED, space_id, profile_id=profile_id)])
                for name in REBUILT:
                    await uow.pipeline.request_work(
                        Task(name, space_id, {"reason": "erasure"}, profile_id=profile_id)
                    )
        if self._cache is None:
            return
        for profile_id in sorted(owners, key=str):
            try:
                await self._cache.purge_profile(space_id, profile_id)
            except (ConnectionError, TimeoutError, OSError):
                log.warning("compiled context not purged from the cache; its privacy version refuses it")

    async def _redact_raw(self, space_id: UUID, row: RawObject, plain: frozenset[str]) -> int:
        """One payload kept for someone else, written again with the target redacted out of it and indexed by
        what it names now. A payload gone since the index was read is nothing to redact."""
        try:
            body = await self._objects.get(row.key)
        except NotFound:
            return 0
        redacted, found = redact_body(body, plain)
        if not found:
            return 0
        await self._objects.put(row.key, redacted, "application/json")
        indexed = await raw_object(self._keys, space_id, row.key, row.owner, row.written_at, redacted)
        async with self._uow(space_id) as uow:
            await uow.privacy.retoken_raw(space_id, row.key, indexed.tokens)
        return 1

    def _named_in_text(self) -> list[NamedInText]:
        """The modules whose rows write people's identifiers in text (an event's, derived memory's, a
        subject's signals, a turn's recording), each once."""
        found: dict[int, NamedInText] = {}
        for module in (*self._fences, self._intake, self._named_rows, self._instructions):
            if isinstance(module, NamedInText):
                found.setdefault(id(module), module)
        return list(found.values())

    async def _index_written_before(self, space_id: UUID) -> None:
        """Indexes, once, the raw content written before the index existed (`domain.privacy.raw_index`): the
        one pass that lists every raw object of the space. Every payload is read for what it names from its
        stored, masked copy (media and interactions are not text); a key's day is when it was written."""
        async with self._uow(space_id) as uow:
            if await uow.privacy.raw_index_ready(space_id):
                return
        payloads = f"raw/{space_id}/"
        rows: list[RawObject] = []
        for prefix in raw_content_prefixes(space_id):
            for key in await self._objects.list_keys(prefix):
                found = raw_content_at(space_id, key)
                if found is None or found[0] == DAY_MARKERS:
                    continue
                body = await self._objects.get(key) if key.startswith(payloads) else None
                rows.append(await raw_object(self._keys, space_id, key, found[0], found[1], body))
        for start in range(0, len(rows), RAW_INDEX_PAGE):
            async with self._uow(space_id) as uow:
                await uow.privacy.index_raw(space_id, rows[start : start + RAW_INDEX_PAGE])
        async with self._uow(space_id) as uow:
            await uow.privacy.mark_raw_index_ready(space_id)

    async def _expire_raw_content(self, space_id: UUID, before: datetime | None, held: Held) -> int:
        """Raw content (payloads as they came, an unmapped one too, media and raw interactions) is kept until
        the customer sets `raw_content` (5.14.0); then what was written before goes, read from the index a
        page at a time, never by listing the space. A handle under a legal hold keeps its own (24.5.1)."""
        await self._index_written_before(space_id)
        if before is None:
            return 0
        keep = {str(h) for h in held.handles}
        gone = 0
        while True:
            async with self._uow(space_id) as uow:
                keys = await uow.privacy.raw_written_before(space_id, before, keep=keep, limit=RAW_INDEX_PAGE)
            for key in keys:
                await self._objects.delete(key)
            if keys:
                async with self._uow(space_id) as uow:
                    await uow.privacy.unindex_raw(space_id, keys)
            gone += len(keys)
            if len(keys) < RAW_INDEX_PAGE:
                return gone

    async def _expire_ledger(self, space_id: UUID) -> None:
        """The ledger outlives every dump it guards, then goes by whole days."""
        today = self._clock.now().astimezone(UTC).date()
        for back in range(LEDGER_DAYS + 1, LEDGER_DAYS + 8):
            day = today - timedelta(days=back)
            await self._objects.delete_prefix(f"{ledger_prefix(space_id)}{day:%Y-%m-%d}/")

    async def erase_cold(self, task: Task) -> None:
        """Rewrites only the archived files that hold rows of the erased target (5.14.0, <= 30 days): the
        target's rows leave, and the company's feedback on what the erasure took (`COLD_FEEDBACK`) stays
        with ids and kinds only, as the live copy did. Idempotent: a rerun finds nothing left to change."""
        space_id, request_id = task.space_id, UUID(task.payload["request_id"])
        async with self._uow(space_id) as uow:
            request = await uow.privacy.get_erasure(space_id, request_id)
        if request is None:
            return
        scope = request.scope
        feedback = {str(e) for e in request.counts.get(COLD_FEEDBACK, [])}
        index = await self._archive_index(space_id)
        removed = scrubbed = 0
        for parts in index["months"].values():
            for part in parts:
                rows = from_parquet(await self._objects.get(part["key"]))
                kept = [row for row in rows if not _archived_row_hit(row, scope)]
                rewritten = [
                    await self._scrubbed_row(space_id, r) if r["event_id"] in feedback else r for r in kept
                ]
                changed = sum(1 for before, after in zip(kept, rewritten, strict=True) if before is not after)
                if len(kept) != len(rows) or changed:
                    data = to_parquet(rewritten, {"niadra.schema_version": SCHEMA_VERSION})
                    await self._objects.put(part["key"], data, PARQUET)
                    part.update(rows=len(rewritten), sha256=sha256_hex(data))
                    removed += len(rows) - len(kept)
                    scrubbed += changed
        await self._save_archive_index(space_id, index)
        async with self._uow(space_id) as uow:
            current = await uow.privacy.get_erasure(space_id, request_id)
            if current is not None:
                counts = {
                    **current.counts,
                    "cold_rows": int(current.counts.get("cold_rows", 0)) + removed,
                    COLD_FEEDBACK_VALUES: int(current.counts.get(COLD_FEEDBACK_VALUES, 0)) + scrubbed,
                }
                await uow.privacy.save_erasure(space_id, dataclasses.replace(current, counts=counts))

    async def _index_archived(self, space_id: UUID, events: Sequence[tuple[str, EventRecord]]) -> None:
        """What archived events name a person by, each under its part (`archived_key`), a page at a time."""
        for start in range(0, len(events), MENTION_PAGE):
            entries = [
                (key, await sealed_tokens(self._keys, space_id, event_tokens(e)))
                for key, e in events[start : start + MENTION_PAGE]
            ]
            async with self._uow(space_id) as uow:
                await uow.mentions.index_objects(entries)

    async def _index_archive(self, space_id: UUID) -> None:
        """Indexes, once, the events archived before the archive had an index: every part is read, by the
        first erasure of the space."""
        async with self._uow(space_id) as uow:
            if await uow.mentions.ready(ARCHIVE_MENTIONS_READY):
                return
        index = await self._archive_index(space_id)
        for month in sorted(index["months"]):
            for part in index["months"][month]:
                try:
                    rows = from_parquet(await self._objects.get(part["key"]))
                except NotFound:
                    continue
                events = [
                    (archived_key(part["key"], r["event_id"]), await self._sealed_record(space_id, r))
                    for r in rows
                ]
                await self._index_archived(space_id, events)
        async with self._uow(space_id) as uow:
            await uow.mentions.mark_ready(ARCHIVE_MENTIONS_READY)

    async def _redact_archive(
        self, space_id: UUID, scope: ErasureScope, plain: frozenset[str], tokens: frozenset[bytes], held: Held
    ) -> Redacted:
        """The archived events of others that name the target keep, with the target redacted out of them:
        found by the index of archived events, only their parts are read and written again, each with its
        new digest in the archive's index. The target's own archived events go whole with `erase_cold`; an
        event of a held handle or conversation keeps as it is."""
        await self._index_archive(space_id)
        done = Redacted()
        named: dict[str, set[str]] = defaultdict(set)
        after: str | None = None
        while True:
            async with self._uow(space_id) as uow:
                keys = await uow.mentions.objects_naming(
                    sorted(tokens), prefix=f"archive/events/{space_id}/", after=after, limit=MENTION_PAGE
                )
            for key in keys:
                part, event_id = archived_at(key)
                named[part].add(event_id)
            if len(keys) < MENTION_PAGE:
                break
            after = keys[-1]
        if not named:
            return done
        own = {str(e) for e in scope.archived_event_ids}
        index = await self._archive_index(space_id)
        for month, parts in index["months"].items():
            for part in parts:
                if part["key"] not in named:
                    continue
                wanted = named.pop(part["key"]) - own
                await self._redact_part(space_id, month, part, wanted, plain, held, done)
        async with self._uow(space_id) as uow:
            # Parts that are no longer in the archive (a month expired since): nothing left to name anyone.
            await uow.mentions.drop_objects(
                sorted(archived_key(p, e) for p, ids in named.items() for e in ids)
            )
        await self._save_archive_index(space_id, index)
        return done

    async def _redact_part(
        self,
        space_id: UUID,
        month: str,
        part: dict[str, Any],
        wanted: set[str],
        plain: frozenset[str],
        held: Held,
        done: Redacted,
    ) -> None:
        """One part written again with the target redacted out of the `wanted` events, and those events
        indexed by what they name now."""
        try:
            rows = from_parquet(await self._objects.get(part["key"]))
        except NotFound:
            return
        rewritten: list[dict[str, Any]] = []
        indexed: list[tuple[str, EventRecord]] = []
        changed = 0
        for row in rows:
            if row["event_id"] not in wanted:
                rewritten.append(row)
                continue
            record = await self._sealed_record(space_id, row)
            fixed = redact_event(record, plain)
            if fixed is not None and await self._archived_held(space_id, row, held):
                done.held += 1
                fixed = None
            elif fixed is not None:
                changed += 1
                done.rows += 1
                done.handles.update(fixed.handle_ids)
            indexed.append((archived_key(part["key"], row["event_id"]), fixed or record))
            rewritten.append(await self._archive_row(space_id, fixed) if fixed is not None else row)
        if changed:
            data = to_parquet(rewritten, {"niadra.schema_version": SCHEMA_VERSION, "niadra.month": month})
            await self._objects.put(part["key"], data, PARQUET)
            part.update(rows=len(rewritten), sha256=sha256_hex(data))
        await self._index_archived(space_id, indexed)

    async def _archived_held(self, space_id: UUID, row: dict[str, Any], held: Held) -> bool:
        if any(UUID(h) in held.handles for h in row.get("handle_ids") or ()):
            return True
        conversation = row.get("conversation_id")
        if not conversation or not held.conversations:
            return False
        return await self._keys.hmac(space_id, TURN_HMAC, conversation.encode()) in held.conversations

    async def _sealed_record(self, space_id: UUID, row: dict[str, Any]) -> EventRecord:
        """An archived event as it is stored, unmasked: what a rewrite seals again."""
        plain = await self._keys.decrypt(
            space_id, row["content_ct"], content_aad(row["event_id"]), int(row["dek_version"])
        )
        return record_from_json(json.loads(plain))

    async def _scrubbed_row(self, space_id: UUID, row: dict[str, Any]) -> dict[str, Any]:
        """An archived feedback event with ids and kinds only, sealed again under its own AAD; the row
        itself when it is not feedback or holds nothing more already. Only these rows are decrypted."""
        plain = await self._keys.decrypt(
            space_id, row["content_ct"], content_aad(row["event_id"]), int(row["dek_version"])
        )
        audit = _audit_only(record_from_json(json.loads(plain)))
        if audit is None:
            return row
        content, dek_version = await self._keys.encrypt(
            space_id, json_line(audit).encode(), content_aad(row["event_id"])
        )
        return {**row, "content_ct": content, "dek_version": dek_version}

    async def archive_events(self, task: Task) -> None:
        """Monthly Parquet of a space's events past the hot window, each row's content encrypted (5.2);
        then the month leaves PostgreSQL, so the live database keeps only the hot window (5.14.0)."""
        space_id = task.space_id
        hot_days = self._config.space(space_id).settings.events_hot_days
        month = str(task.payload.get("month") or month_to_archive(self._clock.now(), hot_days))
        await self._archive_month(space_id, month)
        await self._next_month_behind(space_id, hot_days, done=month)

    async def _next_month_behind(self, space_id: UUID, hot_days: int, *, done: str) -> None:
        """Every month older than the hot window leaves the database, not only the newest: a shorter window
        or a run missed leaves older months behind. The oldest one left gets its own run, one at a time,
        so each run stays a small batch."""
        edge, _ = month_bounds(month_to_archive(self._clock.now(), hot_days))
        async with self._uow(space_id) as uow:
            oldest = await uow.ingest.archivable_events(
                since=ARCHIVE_EPOCH, until=edge, after_id=None, limit=1
            )
        if not oldest:
            return
        behind = month_key(oldest[0].received_at.astimezone(UTC).date())
        index = await self._archive_index(space_id)
        if behind == done or behind in index.get("purged", {}):
            return  # nothing new to do: never a loop of runs on the same month
        async with self._uow(space_id) as uow:
            await uow.pipeline.enqueue([Task(TaskName.ARCHIVE_EVENTS, space_id, {"month": behind})])

    async def _archive_month(self, space_id: UUID, month: str) -> None:
        since, until = month_bounds(month)
        index = await self._archive_index(space_id)
        if month in index["months"]:
            # Archived already: never rewrite it, since its rows may be gone from the database; only
            # finish removing them (a run that stopped between the two steps resumes here).
            await self._purge_month(space_id, month, index)
            return
        prefix = f"archive/events/{space_id}/{month}/"
        # Idempotent per space and month until the index names it: a rerun rewrites it from scratch.
        await self._objects.delete_prefix(prefix)
        parts: list[dict[str, Any]] = []
        after: UUID | None = None
        while True:
            async with self._uow(space_id) as uow:
                events = await uow.ingest.archivable_events(
                    since=since, until=until, after_id=after, limit=self._part_rows
                )
            if not events:
                break
            rows = [await self._archive_row(space_id, event) for event in events]
            key = f"{prefix}part-{len(parts):05d}.parquet"
            data = to_parquet(rows, {"niadra.schema_version": SCHEMA_VERSION, "niadra.month": month})
            await self._objects.put(key, data, PARQUET)
            await self._index_archived(space_id, [(archived_key(key, e.event_id), e) for e in events])
            parts.append({"key": key, "rows": len(rows), "sha256": sha256_hex(data)})
            after = events[-1].event_id
            if len(events) < self._part_rows:
                break
        index = await self._archive_index(space_id)
        index["months"][month] = parts
        await self._save_archive_index(space_id, index)
        await self._purge_month(space_id, month, index)

    async def _purge_month(self, space_id: UUID, month: str, index: dict[str, Any]) -> None:
        """Deletes the archived month from the live database, in short batches, then notes it in the index."""
        purged = index.setdefault("purged", {})
        if month in purged:
            return
        since, until = month_bounds(month)
        removed = 0
        while True:
            async with self._uow.system(space_id) as uow:
                batch = await uow.ingest.purge_archived(since=since, until=until, limit=self._batch)
            removed += batch
            if batch < self._batch:
                break
        purged[month] = {"at": self._clock.now().isoformat(), "rows": removed}
        await self._save_archive_index(space_id, index)

    async def retention_sweep(self, task: Task) -> None:
        """Expires what the governance modules keep, by data class (5.14.0, 22.5)."""
        space_id = task.space_id
        cut = cutoffs(self._clock.now(), self._config.space(space_id).settings.retention_days)
        async with self._uow(space_id) as uow:
            feed = await uow.privacy.purge_feed(space_id, cut[DataClass.PRIVACY_FEED])
            await uow.serve.purge_invalidations(cut[DataClass.READ_INVALIDATIONS])
            runs = await uow.privacy.purge_export_runs(space_id, cut[DataClass.EXPORT_RUNS])
            tombstones = await uow.privacy.purge_tombstones(space_id, cut[DataClass.TOMBSTONES])
            samples = await uow.review.purge_before(cut[DataClass.REVIEW_SAMPLES])
            firings = await uow.triggers.purge_before(cut[DataClass.TRIGGER_FIRINGS])
            deliveries = await uow.notify.purge_before(cut[DataClass.WEBHOOK_DELIVERIES])
            deliveries += await uow.feed.purge_before(self._clock.now() - FEED_KEEP)
            await uow.turns.purge_tombstones(self._clock.now())
        packages = await self._delete_packages(space_id, expired_before=self._clock.now() - PACKAGE_TTL)
        held = await self._holds.held(space_id)
        raw = await self._expire_raw_content(space_id, cut.get(DataClass.RAW_CONTENT), held)
        turns = await self._expire_turns(space_id, held)
        reviews = await self._expire_review_requests(space_id)
        await self._expire_ledger(space_id)
        index = await self._archive_index(space_id)
        events_cut = cut.get(DataClass.COLD_EVENTS)
        expired = [m for m in index["months"] if events_cut is not None and month_expired(m, events_cut)]
        for month in expired:
            await self._objects.delete_prefix(f"archive/events/{space_id}/{month}/")
            del index["months"][month]
            index.get("purged", {}).pop(month, None)
        if expired:
            await self._save_archive_index(space_id, index)
        memory = await self._expire_memory(space_id, cut)
        if events_cut is not None:
            memory["failed_intake"] = await self._expire_failed_intake(space_id, events_cut)
        memory.update(await self._decay_signals(space_id, held))
        memory.update(await self._expire_coordination(space_id, held))
        log.info("retention sweep, memory: %s", ", ".join(f"{k}={v}" for k, v in sorted(memory.items())))
        log.info(
            "retention sweep: feed=%d export_runs=%d tombstones=%d review_samples=%d firings=%d "
            "deliveries=%d cold_months=%d packages=%d raw_content=%d kept_turns=%d review_requests=%d",
            feed,
            runs,
            tombstones,
            samples,
            firings,
            deliveries,
            len(expired),
            packages,
            raw,
            turns,
            reviews,
        )

    async def _expire_failed_intake(self, space_id: UUID, before: datetime) -> int:
        """Rows set aside after failing every attempt live as long as the company's events: past their window
        they go like the events they would have become."""
        gone = 0
        while True:
            async with self._uow(space_id) as uow:
                events = await uow.ingest.failed_intake(self._batch, before=before)
                turns = await uow.turns.failed_intake(self._batch, before=before)
                await uow.ingest.drop_intake([r.intake_id for r in events])
                await uow.turns.drop_intake([r.intake_id for r in turns])
            gone += len(events) + len(turns)
            if len(events) < self._batch and len(turns) < self._batch:
                return gone

    async def _expire_review_requests(self, space_id: UUID) -> int:
        """Review requests resolved `KEPT_DAYS` ago: kept that long by a legal obligation (24.5.1)."""
        before = self._clock.now() - timedelta(days=REVIEW_KEPT_DAYS)
        gone = 0
        while True:
            async with self._uow(space_id) as uow:
                removed = await uow.review_requests.purge_resolved(before, limit=self._batch)
            gone += removed
            if removed < self._batch:
                return gone

    async def _expire_turns(self, space_id: UUID, held: Held) -> int:
        """Turn recordings by the retention the customer set (`recording.kept_days`, `scenario_days`): with
        none, every recording is kept until an erasure (memory never cuts what it was given). A conversation
        under a legal hold stays (24.5.1)."""
        recording = self._config.space(space_id).recording
        now = self._clock.now()
        kept_before = now - timedelta(days=recording.kept_days) if recording.kept_days is not None else None
        scenario_before = (
            now - timedelta(days=recording.scenario_days) if recording.scenario_days is not None else None
        )
        expired = await self._expire_kept_turns(space_id, held, kept_before, scenario_before)
        if kept_before is not None:
            expired += await self._expire_short_turns(space_id, held, kept_before)
        return expired

    async def _expire_short_turns(self, space_id: UUID, held: Held, before: datetime) -> int:
        """The short tier has no rows: its turns go by the age of their objects, with the by-id entry of each
        turn that went. A held conversation's turns stay."""
        keep = {c.hex() for c in held.conversations}
        prefix = f"turns/{Tier.SHORT.value}/{space_id}/"
        gone: list[str] = []
        for key in await self._objects.list_keys_before(prefix, before):
            conversation, _, name = key.removeprefix(prefix).partition("/")
            if conversation == BY_ID or conversation in keep or not name.endswith(".bin"):
                continue
            await self._objects.delete(key)
            gone.append(name.removesuffix(".bin"))
        for turn_id in gone:
            await self._objects.delete(by_id_key(space_id, turn_id))
        return len(gone)

    async def _expire_kept_turns(
        self, space_id: UUID, held: Held, kept_before: datetime | None, scenario_before: datetime | None
    ) -> int:
        """Kept turns past their `kept_until`, or kept with none before the retention's cut: the row goes
        first, then the object."""
        expired = 0
        keep = frozenset(held.conversations)
        while True:
            async with self._uow(space_id) as uow:
                rows = await uow.turns.expire_kept(
                    self._clock.now(),
                    self._batch,
                    keep=keep,
                    kept_before=kept_before,
                    scenario_before=scenario_before,
                )
            for row in rows:
                await self._objects.delete(turn_key(Tier.KEPT, space_id, row.conversation, row.turn_id))
                if row.kept_by == KeptBy.SCENARIO:
                    await self._objects.delete(
                        turn_key(Tier.SCENARIO, space_id, row.conversation, row.turn_id)
                    )
            expired += len(rows)
            if len(rows) < self._batch:
                return expired

    async def _expire_coordination(self, space_id: UUID, held: Held) -> dict[str, int]:
        """Coordination's classes by their own retention (24.5.1): claims, contacts, budget spends, effects,
        commitments, handoffs, suppressions and shadow reports. A handle under a legal hold keeps its own,
        found by the subject coordination names it by."""
        handles = frozenset(held.handles)
        subjects = frozenset(await self._subject_hashes(space_id, tuple(handles)))
        # Whole weeks of the contact log first, in a transaction of their own: the drop's lock ends with it.
        async with self._uow(space_id) as uow:
            weeks = await uow.coordinate.drop_expired_contact_weeks(self._clock.now(), subjects)
        totals: dict[str, int] = {"coordinate.contact_log_weeks": weeks}
        while True:
            async with self._uow(space_id) as uow:
                counts = await uow.coordinate.expire(
                    self._clock.now(), limit=self._batch, held_subjects=subjects, held_handles=handles
                )
            for name, count in counts.items():
                totals[f"coordinate.{name}"] = totals.get(f"coordinate.{name}", 0) + count
            if all(count < self._batch for count in counts.values()):
                return totals

    async def _decay_signals(self, space_id: UUID, held: Held) -> dict[str, int]:
        """Subject signals 13 months without activity (24.5.1): the derived accumulators go, what the person
        said, the inferences they deleted and the objects they turned down stay (`domain.signals.body.told`),
        and a row that held only derived accumulators goes whole. A handle under a legal hold keeps its body.
        What a person said leaves memory only by an erasure or a retention the customer set (memory never cuts
        what it was told)."""
        before = self._clock.now() - SIGNALS_INACTIVE
        keep = frozenset(held.handles)
        decayed = dropped = 0
        after: tuple[UUID, UUID] | None = None
        while True:
            async with self._uow(space_id) as uow:
                rows = await uow.signals.inactive(before, limit=self._batch, keep=keep, after=after)
            if not rows:
                break
            kept: list[SignalRow] = []
            untold: list[SignalRow] = []
            for row in rows:
                aad = body_aad(row.handle_id, row.beneficiary_id)
                body = told(decode(await self._keys.decrypt(space_id, row.body_ct, aad, row.dek_version)))
                if not tells(body) and row.beneficiary_id == SELF:
                    untold.append(row)
                    continue
                sealed, version = await self._keys.encrypt(space_id, encode(body), aad)
                kept.append(
                    dataclasses.replace(
                        row,
                        body_ct=sealed,
                        dek_version=version,
                        body_version=BODY_VERSION,
                        size_bytes=len(sealed),
                    )
                )
            async with self._uow(space_id) as uow:
                decayed += await uow.signals.decay(kept, self._clock.now())
                dropped += len(await uow.signals.drop_untold(untold))
            handles = {r.handle_id for r in rows}
            if self._cache is not None:
                try:
                    await self._cache.delete([signals_cache_key(space_id, h) for h in sorted(handles)])
                except (ConnectionError, TimeoutError, OSError):
                    log.warning("decayed signals stay in the cache until they expire there")
            after = (rows[-1].handle_id, rows[-1].beneficiary_id)
            if len(rows) < self._batch:
                break
        return {"subject_signals_decayed": decayed, "subject_signals": dropped}

    async def _expire_memory(self, space_id: UUID, cut: dict[DataClass, datetime]) -> dict[str, int]:
        """The memory classes a tenant set a window for, in short batches, each with the tombstones
        of the continuous export. A customer whose facts, episodes or items left gets its packs and patterns
        rebuilt without them."""
        counts: dict[str, int] = {}
        memory_steps: dict[DataClass, Callable[[UnitOfWork, datetime], Awaitable[list[ErasedRow]]]] = {
            DataClass.FACTS: lambda uow, before: uow.memory.expire_facts(before, limit=self._batch),
            DataClass.EPISODES: lambda uow, before: uow.memory.expire_episodes(before, limit=self._batch),
            DataClass.OPEN_ITEMS: lambda uow, before: uow.memory.expire_items(before, limit=self._batch),
        }
        counted = {"facts", "episodes", "open_items"}
        for data_class, step in memory_steps.items():
            if data_class not in cut:
                continue
            counts[data_class.value] = 0
            while True:
                async with self._uow(space_id) as uow:
                    rows = await step(uow, cut[data_class])
                    exported = [r for r in rows if r.table in EXPORT_TABLES]
                    if exported:
                        await uow.privacy.add_tombstones(space_id, exported, self._clock.now())
                    await _rebuild_owners(uow, space_id, rows)
                counts[data_class.value] += sum(1 for r in rows if r.table in counted)
                if len(rows) < self._batch:
                    break
        if DataClass.SESSIONS in cut:
            counts["sessions"] = 0
            while True:
                async with self._uow(space_id) as uow:
                    gone = await uow.sessions.expire_sessions(cut[DataClass.SESSIONS], limit=self._batch)
                    if gone:
                        # What extraction kept of those conversations goes with them.
                        scope = ErasureScope(
                            uuid7(),
                            TargetKind.HANDLE,
                            (),
                            None,
                            None,
                            self._clock.now(),
                            session_ids=tuple(gone),
                        )
                        await self._erase_all(uow.extract, scope)
                counts["sessions"] += len(gone)
                if len(gone) < self._batch:
                    break
        if DataClass.RECEIPTS in cut:
            counts["receipts"] = 0
            before = cut[DataClass.RECEIPTS].astimezone(UTC).date()
            while True:
                async with self._uow(space_id) as uow:
                    removed = await uow.audit.expire_receipts(space_id, before, limit=self._batch)
                counts["receipts"] += removed
                if removed < self._batch:
                    break
        return counts

    async def export_package(
        self, space_id: UUID, request: ExportRequest, *, requested_by: str
    ) -> ExportPackageOut:
        """The subject's portable package (5.14): JSON lines per kind of record, zipped, in object storage.

        Every section is whole: the long ones are read a page at a time, each page in its own
        short transaction, and the zip streams to storage as it is written."""
        if self._intake is not None:
            await self._intake.settle(space_id)
        async with self._uow(space_id) as uow:
            profile = await _subject(uow, request)
        run_id = uuid7()
        location = f"exports/{space_id}/{run_id}.zip"
        archive = JsonLinesZip()
        digest = hashlib.sha256()

        async def chunks() -> AsyncIterator[bytes]:
            async for _ in self._write_package(space_id, profile, archive):
                if archive.pending >= PACKAGE_CHUNK:
                    data = archive.drain()
                    digest.update(data)
                    yield data
            data = archive.finish()
            digest.update(data)
            yield data

        size = await self._objects.put_stream(location, chunks(), "application/zip")
        now = self._clock.now()
        manifest = {
            "profile_id": str(profile.profile_id),
            "location": location,
            "files": archive.digests,
            "sha256": digest.hexdigest(),
            "requested_by": requested_by,
            "expires_at": (now + PACKAGE_TTL).isoformat(),
        }
        run = ExportRun(run_id, ON_DEMAND, 0, 0, ExportStatus.COMPLETED, now, manifest, finished_at=now)
        async with self._uow(space_id) as uow:
            await uow.privacy.create_export_run(space_id, run)
            await record_usage_in(uow, space_id, Metric.EXPORT_BYTES, size, ("package", run_id), 0, now)
        return ExportPackageOut(
            run_id=str(run_id),
            profile_id=str(profile.profile_id),
            download_url=await self._objects.presign_get(location, int(DOWNLOAD_TTL.total_seconds())),
            download_expires_at=now + DOWNLOAD_TTL,
            files=archive.digests,
            sha256=manifest["sha256"],
        )

    async def download_package(self, space_id: UUID, run_id: UUID) -> bytes:
        """Through the cell, after the presigned link expired or from a network that blocks the
        object store's host."""
        async with self._uow(space_id) as uow:
            run = await uow.privacy.get_export_run(space_id, run_id)
        if run is None or run.schedule_id != ON_DEMAND or not run.manifest:
            raise NotFound("unknown export package")
        if package_gone(run.manifest, self._clock.now()):
            raise NotFound("the export package expired; ask for a new one")
        return await self._objects.get(run.manifest["location"])

    async def _delete_packages(
        self, space_id: UUID, *, profile_id: UUID | None = None, expired_before: datetime | None = None
    ) -> int:
        """Deletes decrypted packages from storage: a subject's when it is erased, anyone's past its day."""
        deleted = 0
        while True:
            async with self._uow(space_id) as uow:
                runs = await uow.privacy.stored_packages(
                    space_id, profile_id=profile_id, created_before=expired_before, limit=self._batch
                )
            for run in runs:
                assert run.manifest is not None
                await self._objects.delete_prefix(run.manifest["location"])
                gone = {**run.manifest, "deleted_at": self._clock.now().isoformat()}
                async with self._uow(space_id) as uow:
                    await uow.privacy.save_export_run(space_id, dataclasses.replace(run, manifest=gone))
                deleted += 1
            if len(runs) < self._batch:
                return deleted

    async def export_run(self, task: Task) -> None:
        """Continuous export (22.3) in two phases: observe the sequence, then export once it settled."""
        config = self._config.space(task.space_id)
        schedule = _schedule(config, str(task.payload["schedule_id"]))
        if "run_id" in task.payload:
            await self._execute_run(task, config, schedule, UUID(task.payload["run_id"]))
        else:
            await self._plan_run(task.space_id, schedule)

    async def export_runs(
        self, space_id: UUID, *, schedule_id: str | None, cursor: str | None, limit: int
    ) -> Page[ExportRunOut]:
        async with self._uow(space_id) as uow:
            runs = await uow.privacy.export_runs(
                space_id, schedule_id=schedule_id, before_id=UUID(cursor) if cursor else None, limit=limit
            )
        next_cursor = str(runs[-1].run_id) if len(runs) == limit else None
        return Page[ExportRunOut](items=[export_run_out(r) for r in runs], next_cursor=next_cursor)

    async def put_destination_credential(self, space_id: UUID, schedule_id: str, value: str) -> int:
        """Write-only into the cell vault; the control plane never sees it (4.7, 22.3)."""
        schedule = _schedule(self._config.space(space_id), schedule_id)
        _own_credential(space_id, schedule)
        # A credential that can read or list the customer's bucket is refused before it is kept.
        await self._destinations.check_write_only(schedule, value)
        return await self._secrets.put(schedule.credential_ref, value)

    async def put_secret(self, space_id: UUID, kind: SecretKind, secret_id: str, value: str) -> int:
        """Webhook signing secrets, model credentials and the keys gateways share, write-only (4.7)."""
        self._config.space(space_id)
        return await self._secrets.put(secret_ref(space_id, kind, secret_id), value)

    def _origin_batch(
        self, module: str, scope: ErasureScope, cold: Callable[[UUID], bool]
    ) -> Callable[[UnitOfWork], Awaitable[tuple[list[ErasedRow], bool]]]:
        async def batch(uow: UnitOfWork) -> tuple[list[ErasedRow], bool]:
            step = scope
            if module == SESSION_ARTIFACTS_STEP:
                # Extraction results are keyed by session, which the extract module cannot resolve itself.
                step = scope.with_sessions(tuple(await uow.sessions.session_ids_for(scope)))
            rows = await _participants(uow)[module].erase_for(step, limit=self._batch)
            done = len(rows) < self._batch
            if module == FEEDBACK_STEP:
                rows += await _scrub_feedback(uow, rows, cold)
            return rows, done

        return batch

    async def _erase_all(self, participant: ErasureParticipant, scope: ErasureScope) -> list[ErasedRow]:
        rows: list[ErasedRow] = []
        while True:
            batch = await participant.erase_for(scope, limit=self._batch)
            rows += batch
            if len(batch) < self._batch:
                return rows

    async def _drain(
        self,
        space_id: UUID,
        request_id: UUID,
        batch: Callable[[UnitOfWork], Awaitable[tuple[list[ErasedRow], bool]]],
        *,
        system: bool = False,
    ) -> None:
        done = False
        while not done:
            async with self._uow.system(space_id) if system else self._uow(space_id) as uow:
                rows, done = await batch(uow)
                await self._record(uow, space_id, request_id, rows)

    async def _record(self, uow: UnitOfWork, space_id: UUID, request_id: UUID, rows: list[ErasedRow]) -> None:
        """Tombstones for the export, blobs out of storage, counts on the request; all inside the batch."""
        if not rows:
            return
        # Tombstones exist for the export's consumers; tables that are never exported need none (22.3).
        exported = [row for row in rows if row.table in EXPORT_TABLES]
        if exported:
            await uow.privacy.add_tombstones(space_id, exported, self._clock.now())
        blobs = {k for row in rows for k in row.blob_keys}
        for key in blobs:
            # Deleted before commit: if the batch rolls back, the retry erases the rows again anyway.
            await self._objects.delete_prefix(key)
        await uow.privacy.unindex_raw(space_id, blobs)
        seqs = [row.change_seq for row in rows if row.change_seq is not None]
        runs = await uow.privacy.runs_covering(space_id, seqs) if seqs else []
        request = await uow.privacy.get_erasure(space_id, request_id)
        assert request is not None
        await uow.privacy.save_erasure(
            space_id, dataclasses.replace(request, counts=tally(request.counts, rows, runs))
        )

    async def _count(self, space_id: UUID, request_id: UUID, name: str, value: int) -> None:
        async with self._uow(space_id) as uow:
            request = await uow.privacy.get_erasure(space_id, request_id)
            assert request is not None
            counts = {**request.counts, name: int(request.counts.get(name, 0)) + value}
            await uow.privacy.save_erasure(space_id, dataclasses.replace(request, counts=counts))

    async def _complete(self, space_id: UUID, request_id: UUID) -> None:
        async with self._uow(space_id) as uow:
            request = await uow.privacy.get_erasure(space_id, request_id)
            assert request is not None
            scope = request.scope
            # Again at the end: a pack compiled from a half-erased replica must not outrank the feed (5.11).
            await _announce(uow, scope)
            now = self._clock.now()
            done = dataclasses.replace(request, status=ErasureStatus.COMPLETED, completed_at=now)
            done = dataclasses.replace(done, receipt_hash=erasure_receipt_hash(done, now))
            await uow.privacy.save_erasure(space_id, done)
            await enqueue_event(
                uow, self._config.space(space_id), "erasure.completed", None, erasure_event(done), at=now
            )
            await uow.pipeline.enqueue([Task(TaskName.ERASE_COLD, space_id, {"request_id": str(request_id)})])
            if scope.profile_id and scope.kind is not TargetKind.PROFILE:
                # The person remains: rebuild the pack and re-derive traits without what was forgotten.
                for name in (TaskName.COMPILE, TaskName.DERIVE_TRAITS):
                    await uow.pipeline.request_work(Task(name, space_id, profile_id=scope.profile_id))
            # The packs of the other participants of the objects the person spoke about carried what went:
            # they are compiled again without it.
            touched = [_object_key(ref) for ref in request.counts.get(OBJECTS_TOUCHED, [])]
            others = {*await participant_profiles(uow, touched), *map(UUID, request.counts.get(ACTS_FOR, []))}
            # Their packs, pinned ones too, compiled while the rows went, are never served again.
            await _announce_carriers(uow, others - {scope.profile_id})
            for profile_id in sorted(others - {scope.profile_id}, key=str):
                await uow.pipeline.request_work(Task(TaskName.COMPILE, space_id, profile_id=profile_id))

    async def _plan_run(self, space_id: UUID, schedule: ExportSchedule) -> None:
        now = self._clock.now()
        async with self._uow(space_id) as uow:
            runs = await uow.privacy.export_runs(
                space_id, schedule_id=schedule.schedule_id, before_id=None, limit=50
            )
            in_flight = [
                r
                for r in runs
                if r.status in (ExportStatus.WAITING, ExportStatus.RUNNING) and now - r.started_at < STALE_RUN
            ]
            if in_flight:
                return
            last = next((r for r in runs if r.status is ExportStatus.COMPLETED), None)
            observed = await uow.privacy.current_change_seq()
            run = ExportRun(
                uuid7(), schedule.schedule_id, last.to_seq if last else 0, observed, ExportStatus.WAITING, now
            )
            await uow.privacy.create_export_run(space_id, run)
            follow = {"schedule_id": schedule.schedule_id, "run_id": str(run.run_id)}
            await uow.pipeline.schedule(
                Task(TaskName.EXPORT_RUN, space_id, follow), now + SETTLE, DelayReason.EXPORT_SETTLE
            )

    async def _execute_run(
        self, task: Task, config: SpaceConfig, schedule: ExportSchedule, run_id: UUID
    ) -> None:
        space_id = task.space_id
        now = self._clock.now()
        async with self._uow(space_id) as uow:
            run = await uow.privacy.get_export_run(space_id, run_id)
            if run is None or run.status in (ExportStatus.COMPLETED, ExportStatus.FAILED):
                return
            if not settled(run, now):
                # A fresh id: the queue deduplicates by task id, and this is a new delivery of the same work.
                retry = dataclasses.replace(task, id=uuid7())
                await uow.pipeline.schedule(retry, run.started_at + SETTLE, DelayReason.EXPORT_SETTLE)
                return
            if schedule.region != config.region:
                # The destination must sit in the tenant's region, checked on every run (22.3).
                failed = _failed(run, "destination_outside_region", now)
                await uow.privacy.save_export_run(space_id, failed)
                await _announce_run(uow, config, failed)
                return
            run = dataclasses.replace(run, status=ExportStatus.RUNNING)
            await uow.privacy.save_export_run(space_id, run)
        try:
            manifest = await self._write_run(space_id, schedule, run)
        except Exception as exc:
            log.warning(
                "export run %s of schedule %s failed: %s",
                run.run_id,
                schedule.schedule_id,
                type(exc).__name__,
            )
            async with self._uow(space_id) as uow:
                failed = _failed(run, _run_error(exc), self._clock.now())
                await uow.privacy.save_export_run(space_id, failed)
                await _announce_run(uow, config, failed)
            return
        async with self._uow(space_id) as uow:
            finished = dataclasses.replace(
                run, status=ExportStatus.COMPLETED, manifest=manifest, finished_at=self._clock.now()
            )
            await uow.privacy.save_export_run(space_id, finished)
            await _announce_run(uow, config, finished)
            # The bytes the customer's bucket received, counted once per run (5.15).
            sent = sum(int(f.get("bytes", 0)) for f in manifest.get("files", []))
            await record_usage_in(
                uow, space_id, Metric.EXPORT_BYTES, sent, ("export", run.run_id), 0, self._clock.now()
            )

    async def _write_run(self, space_id: UUID, schedule: ExportSchedule, run: ExportRun) -> dict[str, Any]:
        _own_credential(space_id, schedule)
        credential = await self._secrets.get(schedule.credential_ref)
        async with self._destinations.open(schedule, credential) as destination:
            return await self._write_files(space_id, schedule, run, destination)

    async def _write_files(
        self, space_id: UUID, schedule: ExportSchedule, run: ExportRun, destination: ExportSink
    ) -> dict[str, Any]:
        metadata = {"niadra.schema_version": SCHEMA_VERSION, "niadra.run_id": str(run.run_id)}
        files: list[dict[str, Any]] = []
        erased: list[dict[str, Any]] = []

        async def write(table: str, part: int, rows: list[dict[str, Any]]) -> None:
            path = file_path(schedule.prefix, table, run, part)
            data = to_parquet(rows, metadata)
            await destination.put(path, data, PARQUET)
            files.append(
                {
                    "path": path,
                    "table": table,
                    "rows": len(rows),
                    "sha256": sha256_hex(data),
                    "bytes": len(data),
                }
            )

        tables = [t for t in schedule.tables if t in EXPORT_TABLES]
        archived: list[str] = []
        for table in tables:
            after, part = run.from_seq, 0
            if table == "events" and run.from_seq == 0:
                # A schedule's first run starts at the beginning, and the months past the hot window left the
                # database for the cold archive: they come from there, before the live rows. A
                # month still being purged may send an event twice; each row is an upsert by `event_id`.
                async for month, rows in self._archive_parts(await self._archive_index(space_id)):
                    events = [await self._archived_record(space_id, row) for row in rows]
                    if events:
                        await write(
                            table, part, [{**export_row(e), "change_seq": 0, "op": "upsert"} for e in events]
                        )
                        part += 1
                    if month not in archived:
                        archived.append(month)
            while True:
                async with self._uow(space_id) as uow:
                    rows = await _export_sources(uow)[EXPORT_TABLES[table]].export_rows(
                        table, after_seq=after, upto_seq=run.to_seq, limit=self._batch
                    )
                kept, gone = split_erased(rows)
                erased += [{"table": table, **key} for key in gone]
                if schedule.pseudonymize:
                    kept = [pseudonymized(table, row) for row in kept]
                if kept:
                    await write(table, part, kept)
                    part += 1
                if len(rows) < self._batch:
                    break
                after = max(int(r["change_seq"]) for r in rows)
        async with self._uow(space_id) as uow:
            tombstones = await uow.privacy.tombstones_between(space_id, run.from_seq, run.to_seq)
            later = await uow.privacy.tombstones_between(space_id, run.to_seq, None)
        if tombstones:
            await write("tombstones", 0, [_tombstone_row(t) for t in tombstones])
        if fixes := corrections(set(tables), later):
            await write("corrections", 0, [_tombstone_row(t) for t in fixes])
        manifest = {
            "run_id": str(run.run_id),
            "schedule_id": schedule.schedule_id,
            "space_id": str(space_id),
            "schema_version": SCHEMA_VERSION,
            "from_seq": run.from_seq,
            "to_seq": run.to_seq,
            "files": files,
            "erased": erased,
            "skipped_tables": sorted(set(schedule.tables) - set(tables)),
            "archived_months": archived,
        }
        body = json.dumps(manifest, sort_keys=True, default=str).encode()
        await destination.put(manifest_path(schedule.prefix, run), body, "application/json")
        return manifest

    async def _write_package(
        self, space_id: UUID, profile: ProfileRecord, archive: JsonLinesZip
    ) -> AsyncIterator[None]:
        """Writes the package section by section, yielding after each page so the caller can drain it."""
        handles = list(profile.handle_ids)
        # The index is read before the live events: a month it lists as purged is gone from the database,
        # and any other month may still have rows there, which the archive part then skips.
        index = await self._archive_index(space_id)
        purged = set(index.get("purged", {}))
        live: set[UUID] = set()

        async def whole(read: Callable[[UnitOfWork], Awaitable[list[Any]]]) -> AsyncIterator[list[Any]]:
            async with self._uow(space_id) as uow:
                yield await read(uow)

        async def pages[R](
            read: Callable[[UnitOfWork, Any], Awaitable[list[R]]], cursor: Callable[[R], Any]
        ) -> AsyncIterator[list[R]]:
            after: Any = None
            while True:
                async with self._uow(space_id) as uow:
                    page = await read(uow, after)
                yield page
                if len(page) < PACKAGE_PAGE:
                    return
                after = cursor(page[-1])

        sections: list[tuple[str, AsyncIterator[list[Any]]]] = [
            ("profile", whole(lambda _: _listed(profile))),
            ("handles", whole(lambda uow: uow.identity.get_handles(handles))),
            ("links", whole(lambda uow: uow.identity.active_links(handles))),
            # What agents kept in their working state about the customer (24.3.9), by declared field.
            ("agent_states", whole(lambda uow: uow.agent_state.package_for(handles))),
            ("facts", whole(lambda uow: uow.memory.facts_for(handles, statuses=tuple(FactStatus)))),
            (
                "episodes",
                pages(
                    lambda uow, before: uow.memory.episodes_for(handles, limit=PACKAGE_PAGE, before=before),
                    lambda e: (e.ended_at, e.episode_id),
                ),
            ),
            (
                "open_items",
                whole(lambda uow: uow.memory.open_items_for(handles, statuses=tuple(OpenItemStatus))),
            ),
            ("attributes", whole(lambda uow: uow.memory.attrs_for(handles))),
            # What the customer's sessions settled: protocols, postal codes, amounts (11, 3.2).
            ("settled_values", whole(lambda uow: uow.memory.values_for(handles, limit=PACKAGE_VALUES))),
            (
                "objects",
                pages(
                    lambda uow, before: uow.objects.objects_for(handles, limit=PACKAGE_PAGE, before=before),
                    lambda o: (o.as_of, o.object_id),
                ),
            ),
            # The shared objects the customer asked to be told about, with the condition and its consent
            # (24.3.12).
            ("object_watches", whole(lambda uow: uow.typed.watches_for(handles))),
            (
                "actions",
                pages(
                    lambda uow, before: uow.objects.actions_for(handles, limit=PACKAGE_PAGE, before=before),
                    lambda a: (a.occurred_at, a.action_id),
                ),
            ),
            # Every pattern the profile holds, retracted and expired ones too, each with its evidence and
            # dates, and each objection to profiling (20.6).
            ("traits", whole(lambda uow: traits_in_view(uow, profile))),
            ("profiling_objections", whole(lambda uow: _objections(uow, handles))),
            # Each purpose the subject objected to (13, 6.6); every item above names its purposes and
            # the legal basis each had when it was collected.
            ("purpose_objections", whole(lambda uow: _purpose_objections(uow, handles))),
            # What the subject wants, refuses and is, whom they buy for, and the raw interactions behind it
            # within their retention (24.5.3).
            ("subject_signals", whole(lambda uow: exported_signals(uow, self._keys, space_id, handles))),
            ("beneficiaries", whole(lambda uow: exported_beneficiaries(uow, self._keys, space_id, handles))),
            (
                "interactions",
                whole(lambda uow: exported_interactions(uow, self._keys, self._objects, space_id, handles)),
            ),
            # The outcomes attributed to what agents did for them, by line, within their retention (24.7).
            ("outcomes", whole(lambda uow: exported_outcomes(uow, handles))),
            # The inferences the subject deleted, their requests to review an automated decision, and the
            # legal holds on their data, without the controller's reasons (24.5.3).
            ("inference_suppressions", whole(lambda uow: exported_suppressions(uow, handles))),
            (
                "review_requests",
                whole(lambda uow: exported_review_requests(uow, self._keys, space_id, profile.profile_id)),
            ),
            ("legal_holds", whole(lambda uow: exported_holds(uow, self._keys, space_id, handles))),
        ]
        for name, section in sections:
            archive.section(name)
            async for page in section:
                archive.write(page)
                yield
        archive.section("events")
        before: UUID | None = None
        while True:
            async with self._uow(space_id) as uow:
                page = await uow.ingest.events_for_handles(handles, EPOCH, PACKAGE_PAGE, before_id=before)
            live.update(e.event_id for e in page)
            archive.write(page)
            yield
            if len(page) < PACKAGE_PAGE:
                break
            before = min(e.event_id for e in page)
        # Months past the hot window live only in the cold archive: access and portability read it too,
        # one Parquet part at a time.
        async for rows in self._archived_events(space_id, profile, index, skip=live, purged=purged):
            archive.write(rows)
            yield
        # What the subject sent in rows set aside after failing every attempt, kept and never deleted:
        # theirs to access as everything else, read by the modules that keep them.
        archive.section("kept_aside")
        keepers = [fence for fence in self._fences if isinstance(fence, KeptAside)]
        if keepers:
            scope = ErasureScope(
                uuid7(), TargetKind.PROFILE, tuple(handles), profile.profile_id, None, self._clock.now()
            )
            scope = await self._with_turns(space_id, scope)
            for keeper in keepers:
                archive.write(await keeper.kept_for(space_id, scope))
                yield
        # The recordings of the subject's conversations, kept until an erasure or the customer's retention.
        archive.section("turn_recordings")
        recorders = [fence for fence in self._fences if isinstance(fence, Recordings)]
        if recorders:
            scope = ErasureScope(
                uuid7(), TargetKind.PROFILE, tuple(handles), profile.profile_id, None, self._clock.now()
            )
            scope = await self._with_turns(space_id, scope)
            for recorder in recorders:
                archive.write(await recorder.recorded_for(space_id, scope))
                yield
        archive.section("receipts")
        async for page in pages(
            lambda uow, before: uow.audit.receipts_for_profile(
                profile.profile_id, limit=PACKAGE_PAGE, before_id=before
            ),
            lambda r: r["receipt_id"],
        ):
            archive.write(page)
            yield

    async def _archived_events(
        self,
        space_id: UUID,
        profile: ProfileRecord,
        index: dict[str, Any],
        *,
        skip: set[UUID],
        purged: set[str],
    ) -> AsyncIterator[list[dict[str, Any]]]:
        """The subject's events in the cold archive, decrypted, oldest month first, one part at a time. A
        month not yet purged may still have rows in the database, already read from there: never twice."""
        handles = {str(h) for h in profile.handle_ids}
        async for month, rows in self._archive_parts(index):
            found: list[dict[str, Any]] = []
            for row in rows:
                if not handles & set(row.get("handle_ids") or []):
                    continue
                if month not in purged and UUID(row["event_id"]) in skip:
                    continue
                found.append({**await self._archived_content(space_id, row), "archived": True})
            if found:
                yield found

    def _archive_parts(self, index: dict[str, Any]) -> AsyncIterator[tuple[str, list[dict[str, Any]]]]:
        return self._archive.parts(index)

    async def _archived_content(self, space_id: UUID, row: dict[str, Any]) -> dict[str, Any]:
        return await self._archive.content(space_id, row)

    async def _archived_record(self, space_id: UUID, row: dict[str, Any]) -> EventRecord:
        return await self._archive.record(space_id, row)

    async def _archive_row(self, space_id: UUID, event: EventRecord) -> dict[str, Any]:
        content, dek_version = await self._keys.encrypt(
            space_id, json_line(event).encode(), content_aad(event.event_id)
        )
        return {
            "event_id": str(event.event_id),
            "received_at": event.received_at,
            "occurred_at": event.occurred_at,
            "source_id": str(event.source_id),
            "conversation_id": event.conversation_id,
            "task_id": event.task_id,
            "handle_ids": [str(h) for h in event.handle_ids],
            "dek_version": dek_version,
            "content_ct": content,
        }

    async def _archive_index(self, space_id: UUID) -> dict[str, Any]:
        return await self._archive.index(space_id)

    async def _save_archive_index(self, space_id: UUID, index: dict[str, Any]) -> None:
        await self._objects.put(
            _index_key(space_id), json.dumps(index, sort_keys=True).encode(), "application/json"
        )


async def seal_ledger(keys: KeyVault, space_id: UUID, request: ErasureRequest) -> bytes:
    """The ledger entry encrypted with the space's key: a conversation id can carry a phone number."""
    body = json.dumps(ledger_entry(request), sort_keys=True).encode()
    sealed, version = await keys.encrypt(space_id, body, ledger_aad(space_id, request.request_id))
    envelope = {"request_id": str(request.request_id), "dek_version": version, "sealed": sealed.hex()}
    return json.dumps(envelope).encode()


async def open_ledger(keys: KeyVault, space_id: UUID, data: bytes) -> dict[str, Any]:
    envelope = json.loads(data)
    aad = ledger_aad(space_id, UUID(envelope["request_id"]))
    body = await keys.decrypt(space_id, bytes.fromhex(envelope["sealed"]), aad, int(envelope["dek_version"]))
    entry: dict[str, Any] = json.loads(body)
    return entry


def _own_credential(space_id: UUID, schedule: ExportSchedule) -> None:
    """The vault is the cell's: a schedule neither reads nor overwrites a secret of another space (4.7)."""
    if not owns_secret(space_id, schedule.credential_ref):
        raise InvalidInput(
            f"export schedule {schedule.schedule_id}: credential_ref must be a secret of this space "
            f"({secret_prefix(space_id)}...)"
        )


def secret_ref(space_id: UUID, kind: SecretKind, secret_id: str) -> str:
    """Where the vault keeps a tenant secret; the snapshot's `secret_ref`s use the same convention."""
    return f"spaces/{space_id}/{kind}/{secret_id}"


def erasure_out(request: ErasureRequest) -> ErasureOut:
    return ErasureOut(
        request_id=str(request.request_id),
        status=request.status.value,
        target_kind=request.target_kind.value,
        target_id=request.target_id,
        requested_at=request.requested_at,
        completed_at=request.completed_at,
        erased=request.erased,
        export_run_ids=request.export_run_ids,
        receipt_hash=request.receipt_hash.hex() if request.receipt_hash else None,
        retained=dict(request.counts.get("retained", {})),
        object_bound_rows_erased=int(request.counts.get(OBJECT_BOUND, 0)),
        objects=list(request.counts.get(OBJECTS_TOUCHED, [])),
    )


def export_run_out(run: ExportRun) -> ExportRunOut:
    return ExportRunOut(
        run_id=str(run.run_id),
        schedule_id=run.schedule_id,
        status=run.status.value,
        from_seq=run.from_seq,
        to_seq=run.to_seq,
        started_at=run.started_at,
        finished_at=run.finished_at,
        error=run.error,
        manifest=run.manifest,
    )


def _participants(uow: UnitOfWork) -> dict[str, ErasureParticipant]:
    """The owners of the steps of 8.4 by module name, as `EVENTS_STEP`, `EVIDENCE_STEPS` and
    `ORIGIN_STEPS` list them; typed, so a module that drops `erase_for` fails the type check."""
    return {
        "ingest": uow.ingest,
        "extract": uow.extract,
        "sessions": uow.sessions,
        "memory": uow.memory,
        "traits": uow.traits,
        "objects": uow.objects,
        "identity": uow.identity,
        "compile": uow.compile,
        "measure": uow.measure,
        "triggers": uow.triggers,
        "review": uow.review,
        "notify": uow.notify,
        "turns": uow.turns,
        "feed": uow.feed,
        "signals": uow.signals,
        "outcomes": uow.outcomes,
        "agent_state": uow.agent_state,
        "coordinate": uow.coordinate,
        "typed": uow.typed,
    }


def _export_sources(uow: UnitOfWork) -> dict[str, ExportSource]:
    """The owners of `EXPORT_TABLES` by module name (22.3)."""
    return {
        "ingest": uow.ingest,
        "memory": uow.memory,
        "objects": uow.objects,
        "traits": uow.traits,
        "audit": uow.audit,
        "measure": uow.measure,
        "identity": uow.identity,
        "agent_memory": uow.agent_memory,
    }


async def _scrub_feedback(
    uow: UnitOfWork, rows: list[ErasedRow], cold: Callable[[UUID], bool]
) -> list[ErasedRow]:
    """The company's feedback on a fact or an open item the erasure took stays as an audit record of ids
    and kinds only (8.4): a correction's value and a reason may repeat what the customer said. In the
    batch that took the mentions and items, since nothing leads back to the feedback afterwards; as the
    system role, since the log is append-only for the application. The ids the cold archive may hold go
    with the request (`COLD_FEEDBACK`): the events no longer live that `cold` accepts, and every live
    feedback event, whose month may be in the middle of being archived."""
    erased = {r.key["event_id"] for r in rows if r.table == "events"}
    ids = sorted(
        {UUID(r.key["event_id"]) for r in rows if r.table in FEEDBACK_LINKS} - {UUID(e) for e in erased}
    )
    if not ids:
        return []
    live = await uow.ingest.get_events(ids)
    audits = [(e, _audit_only(e)) for e in live if is_feedback(e)]
    await uow.ingest.rewrite_content([audit for _, audit in audits if audit is not None])
    found = {e.event_id for e in live}
    archived = [e for e in ids if e not in found and cold(e)] + [e.event_id for e, _ in audits]
    return [
        ErasedRow(FEEDBACK_VALUES, {"event_id": str(e.event_id)}) for e, audit in audits if audit is not None
    ] + [ErasedRow(COLD_FEEDBACK, {"event_id": str(e)}) for e in sorted(set(archived))]


def _audit_only(event: EventRecord) -> EventRecord | None:
    """A feedback event with ids and kinds only, or `None` when it is not feedback or already is."""
    if not is_feedback(event):
        return None
    fields = feedback_audit(event.fields)
    if event.text is None and event.fields == fields:
        return None
    return dataclasses.replace(event, text=None, fields=fields)


def _id_month(event_id: UUID) -> str:
    """The archive month of an event: its id is minted when it is received (5.2)."""
    return month_key(datetime.fromtimestamp(uuid7_ms(event_id) / 1000, UTC).date())


async def _announce(uow: UnitOfWork, scope: ErasureScope) -> None:
    """Bumps the privacy version and writes the feed row in the same transaction (5.11)."""
    version = 0
    if scope.profile_id is not None:
        version = await uow.identity.bump_privacy_version(scope.profile_id)
    await uow.privacy.record_feed(
        kind="erasure",
        profile_ids=[scope.profile_id] if scope.profile_id else [],
        handle_ids=list(scope.handle_ids),
        privacy_version=version,
    )


async def _announce_carriers(uow: UnitOfWork, profile_ids: Collection[UUID | None]) -> None:
    """Other profiles whose packs carry what an erased person said: their privacy versions move as the
    person's does, so no pack of theirs compiled before (a conversation's pin included) is served again."""
    for profile_id in sorted({p for p in profile_ids if p is not None}, key=str):
        version = await uow.identity.bump_privacy_version(profile_id)
        await uow.privacy.record_feed(
            kind="erasure", profile_ids=[profile_id], handle_ids=[], privacy_version=version
        )


async def _resolve(uow: UnitOfWork, request: ForgetRequest, request_id: UUID, now: datetime) -> ErasureScope:
    kind = TargetKind(request.target)
    if request.profile_id is not None:
        profile = await uow.identity.get_profile(request.profile_id)
        if profile is None:
            raise NotFound("unknown profile")
        return ErasureScope(
            request_id, kind, profile.handle_ids, profile.profile_id, None, now, pseudonym=profile.pseudonym
        )
    if request.handle is not None:
        handle = await _find_handle(uow, request.handle)
        if handle is None:
            raise NotFound("unknown handle")
        owner = await uow.identity.profile_of(handle.handle_id)
        return ErasureScope(
            request_id,
            kind,
            (handle.handle_id,),
            owner.profile_id if owner else None,
            None,
            now,
            pseudonym=owner.pseudonym if owner else None,
        )
    assert request.conversation_id is not None
    events = await uow.ingest.events_between(conversation_id=request.conversation_id, since=EPOCH, until=now)
    # The conversation's row outlives its events: when every event is in the cold archive, it still finds
    # the conversation and who started it; the cascade reads the archive for the rest.
    known = await uow.ingest.known_conversation(request.conversation_id)
    if not events and known is None:
        raise NotFound("unknown conversation")
    handles = tuple(dict.fromkeys([*(h for e in events for h in e.handle_ids), *(known or ())]))
    owner = await uow.identity.profile_of(handles[0]) if handles else None
    return ErasureScope(
        request_id,
        kind,
        handles,
        owner.profile_id if owner else None,
        request.conversation_id,
        now,
        pseudonym=owner.pseudonym if owner else None,
    )


async def _find_handle(uow: UnitOfWork, handle: Handle) -> HandleRecord | None:
    """By the normalized value, as ingestion stored it: `+55 11 98765-4321` finds `+5511987654321`."""
    key = normalize_handle(handle.type, handle.value, handle.scope, handle.subject_kind).key
    return await uow.identity.find_handle(key.type, key.scope, key.value)


def package_gone(manifest: dict[str, Any], now: datetime) -> bool:
    if manifest.get("deleted_at"):
        return True
    expires = manifest.get("expires_at")
    return bool(expires) and datetime.fromisoformat(str(expires)) <= now


def _target_id(request: ForgetRequest, scope: ErasureScope) -> str:
    if request.target == "handle":
        return str(scope.handle_ids[0])
    if request.target == "profile":
        return str(request.profile_id)
    return str(request.conversation_id)


def _same_request(existing: ErasureRequest, body_hash: str) -> None:
    """A replayed key must carry the same body (7.0): another phone under the same key is a new
    erasure, refused with 409, never a silent replay that leaves the second person in memory."""
    if existing.counts.get("request_hash") != body_hash:
        raise Conflict("the Idempotency-Key was already used with another body")


async def _rebuild_owners(uow: UnitOfWork, space_id: UUID, rows: list[ErasedRow]) -> None:
    handles = {UUID(r.key["origin_handle_id"]) for r in rows if "origin_handle_id" in r.key}
    owners = {p.profile_id for h in handles if (p := await uow.identity.profile_of(h)) is not None}
    for profile_id in sorted(owners):
        for name in (TaskName.COMPILE, TaskName.DERIVE_TRAITS):
            await uow.pipeline.request_work(
                Task(name, space_id, {"reason": "retention"}, profile_id=profile_id)
            )


async def _subject(uow: UnitOfWork, request: ExportRequest) -> ProfileRecord:
    """The person a package is for, as a read through their handle sees them (5.3): the rows of links a
    claim made and nobody confirmed are someone else's until confirmed, so they never leave in a package.
    A request by profile id carries no handle and gets the confirmed rows."""
    profile_id = request.profile_id
    readers: tuple[UUID, ...] = ()
    if request.handle is not None:
        handle = await _find_handle(uow, request.handle)
        owner = await uow.identity.profile_of(handle.handle_id) if handle else None
        profile_id = owner.profile_id if owner else None
        readers = (handle.handle_id,) if handle else ()
    profile = await uow.identity.get_profile(profile_id) if profile_id else None
    if profile is None:
        raise NotFound("unknown subject")
    return reader_view(profile, readers)


async def _listed(record: Any) -> list[Any]:
    return [record]


async def _objections(uow: UnitOfWork, handles: list[UUID]) -> list[dict[str, Any]]:
    return [
        {"handle_id": handle_id, "opted_out_at": at} for handle_id, at in await uow.traits.opt_outs(handles)
    ]


async def _purpose_objections(uow: UnitOfWork, handles: list[UUID]) -> list[dict[str, Any]]:
    return [
        {"handle_id": handle_id, "purpose": purpose, "objected_at": at}
        for handle_id, purpose, at in await uow.memory.objection_list(handles)
    ]


def _schedule(config: SpaceConfig, schedule_id: str) -> ExportSchedule:
    schedule = next((s for s in config.export_schedules if s.schedule_id == schedule_id), None)
    if schedule is None:
        raise NotFound("unknown export schedule")
    return schedule


def _failed(run: ExportRun, error: str, at: datetime) -> ExportRun:
    return dataclasses.replace(run, status=ExportStatus.FAILED, error=error, finished_at=at)


def _tombstone_row(tombstone: TombstoneRecord) -> dict[str, Any]:
    return {
        "table": tombstone.table,
        "key": json.dumps(tombstone.key, sort_keys=True),
        "change_seq": tombstone.change_seq,
        "deleted_at": tombstone.deleted_at,
        "op": "delete",
    }


ACTS_FOR = "acts_for"
"""The profiles of the organizations the erased person spoke for, on the request: compiled again at last."""


async def _acts_for(uow: UnitOfWork, handle_ids: Sequence[UUID]) -> list[UUID]:
    links = await uow.identity.active_links(list(handle_ids)) if handle_ids else []
    mine = set(handle_ids)
    organizations = sorted({link.org_handle_id for link in links if link.person_handle_id in mine}, key=str)
    memberships = await uow.identity.memberships(organizations) if organizations else {}
    return sorted(set(memberships.values()), key=str)


async def _object_instructions(
    uow: UnitOfWork,
    space_id: UUID,
    handle_ids: Sequence[UUID],
    objects: Sequence[BusinessObject],
    instructions: ObjectInstructions,
) -> list[str]:
    """The object of each instruction in force the handles said about one of `objects`."""
    if not handle_ids:
        return []
    refs = [ref_of(o.key) for o in objects]
    held = await instructions.held(uow, space_id, list(handle_ids), refs) if refs else []
    return [h.object for h in held if h.object is not None]


def _object_key(ref: str) -> ObjectKey:
    kind, _, rest = ref.partition(":")
    namespace, _, external_id = rest.partition(":")
    return ObjectKey(kind, namespace, external_id)


def erasure_event(request: ErasureRequest) -> dict[str, Any]:
    """The data of the `erasure.completed` webhook (22.3): ids and counts only, never content; `retained`
    when something stayed under a legal hold or a legal obligation (24.5.5)."""
    data: dict[str, Any] = {
        "event_type": "erasure.completed",
        "request_id": str(request.request_id),
        "target_kind": request.target_kind.value,
        "target_id": request.target_id,
        "erased": request.erased,
        "export_run_ids": request.export_run_ids,
        "receipt_hash": request.receipt_hash.hex() if request.receipt_hash else None,
    }
    if request.counts.get("retained"):
        data["retained"] = dict(request.counts["retained"])
    if request.counts.get(OBJECT_BOUND):
        # What the person said about objects went with them: the company may record again,
        # in its own system, what it needs of those objects.
        data[OBJECT_BOUND] = int(request.counts[OBJECT_BOUND])
        data[OBJECTS_TOUCHED] = list(request.counts.get(OBJECTS_TOUCHED, []))
    return data


async def _announce_run(uow: UnitOfWork, config: SpaceConfig, run: ExportRun) -> None:
    """`export.completed` or `export.failed` (7.5), with the run's ids, range and file count only."""
    data: dict[str, Any] = {
        "schedule_id": run.schedule_id,
        "run_id": str(run.run_id),
        "from_seq": run.from_seq,
        "to_seq": run.to_seq,
    }
    if run.status is ExportStatus.COMPLETED:
        data["files"] = len((run.manifest or {}).get("files", []))
        await enqueue_event(uow, config, "export.completed", None, data, at=run.finished_at)
    else:
        await enqueue_event(
            uow, config, "export.failed", None, {**data, "error": run.error}, at=run.finished_at
        )


def _run_error(exc: Exception) -> str:
    # Our errors are written without personal data (6); anything else is named by type only.
    return str(exc)[:200] if isinstance(exc, NiadraError) else type(exc).__name__


def _archived_row_hit(row: dict[str, Any], scope: ErasureScope) -> bool:
    if scope.kind is TargetKind.CONVERSATION:
        return bool(row.get("conversation_id") == scope.conversation_id)
    targets = {str(h) for h in scope.handle_ids}
    return bool(targets & set(row.get("handle_ids") or ()))


def _index_key(space_id: UUID) -> str:
    return index_key(space_id)
