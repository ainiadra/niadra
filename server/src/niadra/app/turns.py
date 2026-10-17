"""Turn records (24.3.5): what an agent read, called, showed, claimed and decided, turn by turn.

The write path answers `POST /v1/turns` with object storage and one statement (24.6). Each turn is checked,
its stored values verified against their digests and masked like events, sealed and written once to the
short tier; the conditional write of that object is what makes the same turn sent twice one turn. The
turns written for the first time go to the intake, as frames without their values, in the only statement
the answer waits for. A `pointer` or `hash_only` turn never waits for object storage (24.4.3): when the
store fails or is slow, the turn goes to the intake as it is, since its frame is all of it, and the worker
writes its object later with the same conditional write, which then decides whether it was a repeat.

A worker adds a group of intakes up in one statement (the day's totals, the builds, the tool schemas and
the kept tier's rows) and hands the frames to the modules that take something from them (`ports.turns`).
No model runs on a turn, and nothing calls the customer's systems (L18).

A turn is read by id from either tier; the kept tier alone is searched (L20).
"""

import asyncio
import base64
import contextlib
import hashlib
import logging
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from enum import StrEnum
from typing import Any, NoReturn
from uuid import UUID

import orjson
from pydantic import ValidationError

from niadra.app.admin_audit import AdminAudit
from niadra.app.notify import enqueue_event, subscribes
from niadra.app.raw_index import sealed_tokens
from niadra.config.models import SpaceConfig
from niadra.contracts.events import ItemError
from niadra.contracts.turns import (
    MAX_TURNS_PER_REQUEST,
    PromoteRequest,
    PromoteResponse,
    ReplayBlocker,
    TurnIndexEntry,
    TurnIndexPage,
    TurnRecord,
    TurnSearchRequest,
    TurnSearchResponse,
    TurnsResponse,
    TurnSummary,
    TurnView,
)
from niadra.domain.common.errors import InvalidInput, NiadraError, NotFound, Unavailable
from niadra.domain.common.ids import uuid7
from niadra.domain.common.space import PersonPrincipal, Principal, SourcePrincipal
from niadra.domain.ingest.hot import pack_envelope, unpack_envelope
from niadra.domain.ingest.intake import ERASE_PAGE, MAX_FAILED_LISTED, FailedIntake
from niadra.domain.ingest.masking import language, mask_value
from niadra.domain.ingest.otlp import otlp_turns
from niadra.domain.notify.webhooks import erasure_needles, external_reference
from niadra.domain.privacy.erasure import ErasureScope
from niadra.domain.privacy.holds import Held
from niadra.domain.privacy.mentions import MENTION_PAGE, OBJECT_MENTIONS_READY, Redacted
from niadra.domain.privacy.raw_index import body_tokens, redact_value
from niadra.domain.tasks import Task, TaskName
from niadra.domain.turns.daily import build_hash, builds_seen, daily_totals, started
from niadra.domain.turns.digest import digest
from niadra.domain.turns.frames import derived_flags, frame_of, merge_part, part_digest, part_of
from niadra.domain.turns.index import entry, entry_digest, index_root
from niadra.domain.turns.pii import Scan, argument_leaves, batches, redacted, to_scan
from niadra.domain.turns.records import (
    MAX_INTAKE_ATTEMPTS,
    TOMBSTONE_DAYS,
    AppliedTurns,
    KeptTurn,
    NewTurnIntake,
    TurnIntakeRow,
    TurnQuery,
    conversation_mark,
    intake_aad,
    needle_mark,
)
from niadra.domain.turns.replay import blockers, replayable
from niadra.domain.turns.storage import (
    HMAC_PURPOSE,
    KeptBy,
    Tier,
    by_id_key,
    conversation_prefix,
    conversation_prefixes,
    keep_flags,
    kept_until_after,
    part_key,
    record_aad,
    sampled,
    short_turn_ids,
    turn_at,
    turn_key,
)
from niadra.domain.vocabulary import Feature, ReceiptKind, ReceiptSurface
from niadra.ports.data_issues import DataIssues
from niadra.ports.holds import LegalHolds
from niadra.ports.infra import Clock, ConfigProvider, Doorbell, KeyVault, ModelsClient, ObjectStore, TaskBus
from niadra.ports.repos.turns import TurnIntakeLog
from niadra.ports.turns import AppliedTurn, TurnConsumers
from niadra.ports.uow import UnitOfWorkFactory

log = logging.getLogger(__name__)

APPLY_GROUP = 50
"""Intakes added up per transaction: the budget of 24.6 is 7 statements for up to 50 requests."""
PROMOTE_BATCH = 500
"""Turns a promotion copies and records per transaction. A conversation with more is kept whole, a batch at a
time; it used to keep only its newest 500."""
OBJECT_WRITES = 25
"""Turns whose objects one request writes at once, two writes each (the record and its by-id entry)."""
INDEX_PAGE = 1000
"""Rows of the kept tier's index read per statement when the day's root is made."""
HAND_OVER_TIMEOUT_S = 0.1
FRAME_WRITE_TIMEOUT_S = 1.0
"""How long a request of `pointer` and `hash_only` turns waits for object storage before deferring them."""
STORAGE_PAUSE = timedelta(seconds=30)
"""After object storage failed, such requests defer their writes without trying it for this long."""
# Modes whose record is its frame: nothing in them needs object storage to be taken.
FRAME_ONLY = frozenset({"pointer", "hash_only"})


class Written(StrEnum):
    CREATED = "created"
    REPEAT = "repeat"
    DEFERRED = "deferred"
    # A later export's part of a turn built from spans, which a worker merges into the turn.
    PART = "part"


@dataclass(frozen=True, slots=True)
class _Prepared:
    """A checked turn, ready to be written: its sealed object and what the intake carries."""

    turn_id: str
    conversation: bytes
    sealed: bytes
    frame: dict[str, Any]
    kept_by: KeptBy | None
    flags: tuple[str, ...]
    named: tuple[str, ...]
    """What the record names a person by (`body_tokens`): the worker indexes its object by them."""

    @property
    def frame_only(self) -> bool:
        return self.frame["content_mode"] in FRAME_ONLY


class TurnService:
    def __init__(
        self,
        uow: UnitOfWorkFactory,
        intake: TurnIntakeLog,
        keys: KeyVault,
        objects: ObjectStore,
        clock: Clock,
        config: ConfigProvider,
        audit: AdminAudit,
        *,
        consumers: TurnConsumers | None = None,
        workers: TaskBus | None = None,
        doorbell: Doorbell | None = None,
        holds: LegalHolds | None = None,
        models: ModelsClient | None = None,
        issues: DataIssues | None = None,
    ) -> None:
        """With `workers`, an acknowledged request's intake is handed to a worker right away; without them
        it is added up before the answer (unit tests). The outbox copy covers a hand-over that fails.
        `models` is the small models pod, for the PII pass a space may ask for over stored arguments."""
        self._uow = uow
        self._intake = intake
        self._keys = keys
        self._objects = objects
        self._clock = clock
        self._config = config
        self._audit = audit
        self._consumers = consumers or TurnConsumers()
        self._workers = workers
        self._doorbell = doorbell
        self._handing: set[asyncio.Task[None]] = set()
        self._storage_down_until: datetime | None = None
        self._holds = holds
        self._models = models
        self._issues = issues

    # The write path

    async def record(self, caller: SourcePrincipal, turns: Sequence[Any]) -> TurnsResponse:
        """Up to 50 turns as the SDK or the OpenTelemetry mapper built them; each refused one is an error
        by its index, and the rest are taken."""
        space_id = caller.space.space_id
        config = self._config.space(space_id)
        received_at = self._clock.now()
        intake_id = uuid7()
        errors: list[ItemError] = []
        prepared: list[_Prepared] = []
        seen: set[str] = set()
        repeated = 0
        for index, raw in enumerate(turns):
            try:
                turn = await self._prepare(caller, config, raw, received_at, intake_id)
            except _Refused as refused:
                errors.append(ItemError(index=index, code=refused.code, detail=refused.detail))
                continue
            if turn.turn_id in seen:
                repeated += 1
                continue
            seen.add(turn.turn_id)
            prepared.append(turn)
        written = await self._write(space_id, prepared)
        fresh = [(t, w) for t, w in zip(prepared, written, strict=True) if w is not Written.REPEAT]
        if fresh:
            body = orjson.dumps(
                {
                    "turns": [
                        {
                            "conversation": t.conversation.hex(),
                            "frame": t.frame,
                            "kept_by": t.kept_by.value if t.kept_by else None,
                            "flags": list(t.flags),
                            "deferred": w is Written.DEFERRED,
                            "part": part_digest(t.frame) if w is Written.PART else None,
                            "named": list(t.named),
                        }
                        for t, w in fresh
                    ]
                }
            )
            sealed, version = await self._keys.encrypt(space_id, body, intake_aad(intake_id))
            intake = NewTurnIntake(
                intake_id, space_id, received_at, caller.source_id, len(fresh), sealed, version
            )
            try:
                await self._intake.accept(intake)
            except BaseException:
                # A retry must find these turns new again, or they would never be added up.
                await self._unwrite(space_id, [t for t, w in fresh if w is Written.CREATED])
                await self._unwrite_parts(space_id, [t for t, w in fresh if w is Written.PART])
                raise
            await self._hand_over(Task(TaskName.APPLY_TURNS, space_id, {"intake_id": str(intake_id)}))
        duplicates = repeated + len(prepared) - len(fresh)
        return TurnsResponse(accepted=len(fresh), duplicates=duplicates, errors=errors)

    async def record_otlp(self, caller: SourcePrincipal, body: Mapping[str, Any]) -> TurnsResponse:
        """Bronze and silver records from OTLP spans, in a space that records turns. The values stay out
        unless the source keeps them in Niadra (the OpenTelemetry conventions, 7): only digests otherwise."""
        config = self._config.space(caller.space.space_id)
        if Feature.TURNS not in config.features.enabled:
            return TurnsResponse(accepted=0, duplicates=0, errors=[])
        source = config.source(caller.source_id)
        mode = "stored" if config.recording.mode_for(caller.source_id) == "stored" else "hash_only"
        records = otlp_turns(body, content_mode=mode, default_agent=source.name if source else "agent")
        accepted = duplicates = 0
        errors: list[ItemError] = []
        for start in range(0, len(records), MAX_TURNS_PER_REQUEST):
            result = await self.record(caller, records[start : start + MAX_TURNS_PER_REQUEST])
            accepted += result.accepted
            duplicates += result.duplicates
            errors += [e.model_copy(update={"index": e.index + start}) for e in result.errors]
        return TurnsResponse(accepted=accepted, duplicates=duplicates, errors=errors)

    async def _prepare(
        self, caller: SourcePrincipal, config: SpaceConfig, raw: Any, received_at: datetime, intake_id: UUID
    ) -> _Prepared:
        try:
            record = TurnRecord.model_validate(raw)
        except ValidationError as exc:
            raise _Refused("invalid_input", _describe(exc)) from None
        recording = config.recording
        if not recording.accepts(caller.source_id, record.content_mode):
            raise _Refused(
                "content_mode_refused",
                f"this source records `{recording.mode_for(caller.source_id)}`: a turn keeps that or less",
            )
        document = record.model_dump(mode="json", by_alias=True, exclude_none=True)
        if record.content_mode == "stored":
            self._verify_and_mask(document, config)
        space_id = caller.space.space_id
        conversation = await self._conversation(space_id, record.conversation_id, record.task_id)
        frame = frame_of(document)
        flags = derived_flags(frame)
        kept_by = None
        if keep_flags(flags):
            kept_by = KeptBy.FLAG
        elif sampled(space_id, conversation, recording.sample_rate):
            kept_by = KeptBy.SAMPLE
        sealed = await self._seal(space_id, caller.source_id, received_at, intake_id, document)
        named = tuple(sorted(body_tokens(orjson.dumps(document))))
        return _Prepared(record.turn_id, conversation, sealed, frame, kept_by, tuple(sorted(flags)), named)

    async def _seal(
        self, space_id: UUID, source_id: UUID, received_at: datetime, intake_id: UUID, record: dict[str, Any]
    ) -> bytes:
        """A turn's object. `intake_id` names the request that wrote it, so a worker that writes a deferred
        turn and finds its object tells its own earlier attempt from a repeat."""
        envelope = {
            "source_id": str(source_id),
            "received_at": received_at.isoformat(),
            "intake_id": str(intake_id),
            "record": record,
        }
        return await self._seal_envelope(space_id, envelope)

    async def _seal_envelope(self, space_id: UUID, envelope: Mapping[str, Any]) -> bytes:
        sealed, version = await self._keys.encrypt(
            space_id, orjson.dumps(envelope), record_aad(envelope["record"]["turn_id"])
        )
        return pack_envelope(version, sealed)

    def _verify_and_mask(self, document: dict[str, Any], config: SpaceConfig) -> None:
        """A stored value must be the one its digest names (Turn Record 6.2.1); then its text leaves are
        masked with the rules events get, and a masked value takes the digest of what is kept (6.2.3)."""
        lang, types = language(config.settings.locale), config.masking_types()
        for key, blob in document.get("blobs", {}).items():
            if "content" not in blob:
                continue
            try:
                sha256, size = digest(blob["content"])
            except ValueError:
                raise _Refused("blob_digest_mismatch", f"blob {key} is not a JSON value") from None
            if sha256 != blob["sha256"] or blob.get("size", size) != size:
                raise _Refused("blob_digest_mismatch", f"blob {key}: the content does not match its digest")
            masked, held = mask_value(None, blob["content"], lang, types)
            if held:
                blob["content"] = masked
                blob["sha256"], blob["size"] = digest(masked)

    async def _conversation(self, space_id: UUID, conversation_id: str | None, task_id: str | None) -> bytes:
        """The keyed hash every storage key and row names a conversation by; a task's is kept apart."""
        name = conversation_id.encode() if conversation_id else b"task:" + (task_id or "").encode()
        return await self._keys.hmac(space_id, HMAC_PURPOSE, name)

    async def _write(self, space_id: UUID, turns: Sequence[_Prepared]) -> list[Written]:
        """Each turn's object, once: REPEAT for a turn the short tier already holds. The by-id entry is
        written again for a repeat, so a turn whose first request failed halfway is still found. When the
        store fails, what this request created goes again, so the retry finds those turns new; a request of
        `pointer` and `hash_only` turns only then defers them all, and a request with a stored turn fails."""
        frame_only = all(turn.frame_only for turn in turns)
        now = self._clock.now()
        if frame_only and self._storage_down_until is not None and now < self._storage_down_until:
            return [Written.DEFERRED] * len(turns)
        created_here: list[_Prepared] = []
        parts_here: list[_Prepared] = []

        async def write(turn: _Prepared) -> Written:
            created = await self._write_object(
                space_id, turn.turn_id, turn.conversation, turn.kept_by, turn.sealed
            )
            if created:
                created_here.append(turn)
                return Written.CREATED
            if turn.frame.get("fidelity", "gold") == "gold":
                return Written.REPEAT
            # Spans of a turn already recorded: a part that adds some waits beside it for a worker's merge.
            main = turn_key(Tier.SHORT, space_id, turn.conversation, turn.turn_id)
            existing = await self._open(space_id, turn.turn_id, await self._objects.get(main))
            part = part_key(space_id, turn.conversation, turn.turn_id, part_digest(turn.frame))
            if merge_part(existing["record"], turn.frame) is None or not await self._objects.put_if_absent(
                part, turn.sealed
            ):
                return Written.REPEAT
            parts_here.append(turn)
            return Written.PART

        try:
            async with asyncio.timeout(FRAME_WRITE_TIMEOUT_S if frame_only else None):
                return await _gather(write, turns)
        except Exception as exc:
            await self._unwrite(space_id, created_here)
            await self._unwrite_parts(space_id, parts_here)
            if isinstance(exc, NiadraError) and not isinstance(exc, Unavailable):
                raise
            log.warning("turn records could not reach object storage", exc_info=True)
            if frame_only:
                self._storage_down_until = now + STORAGE_PAUSE
                return [Written.DEFERRED] * len(turns)
            raise Unavailable("object storage is unavailable", retry_after_s=5) from None

    async def _write_object(
        self, space_id: UUID, turn_id: str, conversation: bytes, kept_by: KeptBy | None, sealed: bytes
    ) -> bool:
        """The short tier's object by its conditional write, its by-id entry, and the kept tier's copy for a
        turn created now; False, writing no copy, when the object was there."""
        key = turn_key(Tier.SHORT, space_id, conversation, turn_id)
        created, indexed = await asyncio.gather(
            self._objects.put_if_absent(key, sealed),
            self._objects.put(by_id_key(space_id, turn_id), conversation.hex().encode()),
            return_exceptions=True,
        )
        try:
            for failure in (created, indexed):
                if isinstance(failure, BaseException):
                    raise failure
            if created is True and kept_by is not None:
                await self._objects.put(turn_key(Tier.KEPT, space_id, conversation, turn_id), sealed)
        except BaseException:
            if created is True:
                # A record written beside a failed write goes, so the retry finds the turn new.
                with contextlib.suppress(Exception):
                    await self._objects.delete(key)
            raise
        return created is True

    async def _unwrite_parts(self, space_id: UUID, turns: Sequence[_Prepared]) -> None:
        for turn in turns:
            with contextlib.suppress(Exception):
                await self._objects.delete(
                    part_key(space_id, turn.conversation, turn.turn_id, part_digest(turn.frame))
                )

    async def _unwrite(self, space_id: UUID, turns: Sequence[_Prepared]) -> None:
        for turn in turns:
            for key in (
                turn_key(Tier.SHORT, space_id, turn.conversation, turn.turn_id),
                turn_key(Tier.KEPT, space_id, turn.conversation, turn.turn_id),
                by_id_key(space_id, turn.turn_id),
            ):
                with contextlib.suppress(Exception):
                    await self._objects.delete(key)

    async def _hand_over(self, task: Task) -> None:
        if self._workers is None:
            await self.apply_turns(task)
            return
        job = asyncio.create_task(self._publish(task))
        self._handing.add(job)
        job.add_done_callback(self._handing.discard)

    async def _publish(self, task: Task) -> None:
        assert self._workers is not None
        try:
            async with asyncio.timeout(HAND_OVER_TIMEOUT_S):
                await self._workers.publish([task])
        except Exception:  # the relay publishes the intake's outbox copy (5.5)
            log.info("handing recorded turns to the workers deferred to the relay")
            if self._doorbell is not None:
                with contextlib.suppress(Exception):
                    await self._doorbell.ring(task.space_id, self._clock.now())

    async def close(self) -> None:
        while self._handing:
            await asyncio.gather(*self._handing, return_exceptions=True)

    # The worker

    async def apply_turns(self, task: Task) -> None:
        """Adds up everything the space acknowledged, a group at a time. A worker that finds another one
        adding the space up leaves it to that one, and to the relay's copy of the intake."""
        while await self._apply_next(task.space_id, APPLY_GROUP):
            pass

    async def fence(self, space_id: UUID, scope: ErasureScope) -> None:
        """Before an erasure's cascade (24.5.5): the target's conversations, and the ids webhook bodies name
        it by, are fenced off for `TOMBSTONE_DAYS`. A group a worker is adding up now commits first, then what
        waits in the intake is added up, so a turn of the target's that sat there, or arrives after, is
        dropped with its objects instead of coming back once the cascade is done."""
        marks = [conversation_mark(h) for h in scope.conversation_hashes]
        marks += [needle_mark(n) for n in erasure_needles(scope)]
        if not marks:
            return
        async with self._uow(space_id) as uow:
            await uow.turns.tombstone(marks, self._clock.now() + timedelta(days=TOMBSTONE_DAYS))
        async with self._uow(space_id) as uow:
            await uow.turns.wait_for_apply()
        while await self._apply_next(space_id, APPLY_GROUP):
            pass
        await self._erase_failed(space_id, scope)

    async def purge(self, space_id: UUID, scope: ErasureScope) -> None:
        """Once the cascade is done: what reached the intake meanwhile is added up, the fenced conversations'
        turns dropped with their objects, and those set aside lose them too. The fence's marks stay up for
        late copies."""
        while await self._apply_next(space_id, APPLY_GROUP):
            pass
        await self._erase_failed(space_id, scope)

    async def redact_named(
        self, space_id: UUID, scope: ErasureScope, plain: frozenset[str], tokens: frozenset[bytes], held: Held
    ) -> Redacted:
        """`NamedInText`: the recordings of others' conversations that name the target keep, with the
        target's phones, e-mails, documents and ids replaced by `ERASED` in every tier that holds a copy,
        found by the index of turn objects a page at a time; a stored blob takes the digest of what it keeps.
        The target's own conversations go with the cascade; a held conversation's recordings keep as they
        are. Recordings written before the index existed are read into it once. The intakes set aside after
        failing are read whole, as the fence reads them: others' turns in them, and the parts of turns that
        wait beside them for a merge, are redacted too."""
        await self._index_recorded(space_id)
        done = await self._redact_failed(space_id, scope, plain, held)
        own = set(scope.conversation_hashes)
        after: str | None = None
        while True:
            async with self._uow(space_id) as uow:
                keys = await uow.mentions.objects_naming(
                    sorted(tokens),
                    prefix=f"turns/{Tier.SHORT.value}/{space_id}/",
                    after=after,
                    limit=MENTION_PAGE,
                )
            index: list[tuple[str, frozenset[bytes]]] = []
            for key in keys:
                found = turn_at(key)
                if found is None or found[0] in own:
                    continue
                conversation, turn_id = found
                try:
                    envelope = await self._open(space_id, turn_id, await self._objects.get(key))
                except NotFound:
                    index.append((key, frozenset()))
                    continue
                record, named = redact_value(envelope["record"], plain)
                if named and conversation in held.conversations:
                    done.held += 1
                    continue
                if named:
                    for blob in (record.get("blobs") or {}).values():
                        if "content" in blob:
                            blob["sha256"], blob["size"] = digest(blob["content"])
                    sealed = await self._seal_envelope(space_id, {**envelope, "record": record})
                    for tier in (Tier.SHORT, Tier.KEPT, Tier.SCENARIO):
                        copy = turn_key(tier, space_id, conversation, turn_id)
                        if tier is Tier.SHORT or await self._exists(copy):
                            await self._objects.put(copy, sealed)
                    done.rows += 1
                index.append((key, await self._record_tokens(space_id, record)))
            async with self._uow(space_id) as uow:
                await uow.mentions.index_objects(index)
            if len(keys) < MENTION_PAGE:
                return done
            after = keys[-1]

    async def _redact_failed(
        self, space_id: UUID, scope: ErasureScope, plain: frozenset[str], held: Held
    ) -> Redacted:
        """Others' turns in the intakes set aside that name the target, sealed again with the target redacted
        out; the target's own leave with the fence, and a held conversation's keep as they are."""
        done = Redacted()
        own = set(scope.conversation_hashes)
        after: UUID | None = None
        while True:
            async with self._uow(space_id) as uow:
                rows = await uow.turns.failed_intake(ERASE_PAGE, after=after)
                for row in rows:
                    body = orjson.loads(
                        await self._keys.decrypt(
                            space_id, row.body_ct, intake_aad(row.intake_id), row.dek_version
                        )
                    )
                    turns, changed = [], False
                    for turn in body["turns"]:
                        conversation = bytes.fromhex(turn["conversation"])
                        if conversation in own:
                            turns.append(turn)
                            continue
                        if turn.get("part") and await self._redact_part(space_id, turn, plain, held, done):
                            turns.append(turn)  # a part waiting beside its turn: redacted where it waits
                            continue
                        frame, named = redact_value(turn["frame"], plain)
                        if not named:
                            turns.append(turn)
                        elif conversation in held.conversations:
                            done.held += 1
                            turns.append(turn)
                        else:
                            turns.append({**turn, "frame": frame})
                            changed = True
                            done.rows += 1
                    if changed:
                        sealed, version = await self._keys.encrypt(
                            space_id, orjson.dumps({**body, "turns": turns}), intake_aad(row.intake_id)
                        )
                        await uow.turns.rewrite_intake(row.intake_id, sealed, version, len(turns))
            if len(rows) < ERASE_PAGE:
                return done
            after = rows[-1].intake_id

    async def _redact_part(
        self, space_id: UUID, turn: dict[str, Any], plain: frozenset[str], held: Held, done: Redacted
    ) -> bool:
        """A part of a turn that waits beside it for a merge, sealed again with the target redacted out: its
        merge then adds nothing of them. False when no part is stored for it (the intake keeps the frame)."""
        conversation, turn_id = bytes.fromhex(turn["conversation"]), turn["frame"]["turn_id"]
        key = part_key(space_id, conversation, turn_id, turn["part"])
        try:
            envelope = await self._open(space_id, turn_id, await self._objects.get(key))
        except NotFound:
            return False
        record, named = redact_value(envelope["record"], plain)
        if named and conversation in held.conversations:
            done.held += 1
        elif named:
            await self._objects.put(key, await self._seal_envelope(space_id, {**envelope, "record": record}))
            done.rows += 1
        return True

    async def _record_tokens(self, space_id: UUID, record: Mapping[str, Any]) -> frozenset[bytes]:
        return await sealed_tokens(self._keys, space_id, body_tokens(orjson.dumps(record)))

    async def _index_recorded(self, space_id: UUID) -> None:
        """Indexes, once, the recordings written before the index of turn objects existed: the one pass
        that lists the space's short tier, by the first erasure."""
        async with self._uow(space_id) as uow:
            if await uow.mentions.ready(OBJECT_MENTIONS_READY):
                return
        keys = [
            k for k in await self._objects.list_keys(f"turns/{Tier.SHORT.value}/{space_id}/") if turn_at(k)
        ]
        for start in range(0, len(keys), MENTION_PAGE):
            entries: list[tuple[str, frozenset[bytes]]] = []
            for key in keys[start : start + MENTION_PAGE]:
                found = turn_at(key)
                assert found is not None
                try:
                    envelope = await self._open(space_id, found[1], await self._objects.get(key))
                except NotFound:
                    continue
                entries.append((key, await self._record_tokens(space_id, envelope["record"])))
            async with self._uow(space_id) as uow:
                await uow.mentions.index_objects(entries)
        async with self._uow(space_id) as uow:
            await uow.mentions.mark_ready(OBJECT_MENTIONS_READY)

    async def _apply_next(self, space_id: UUID, limit: int) -> bool:
        rows: list[TurnIntakeRow] = []
        try:
            async with self._uow(space_id) as uow:
                taken = await uow.turns.take_intake(limit)
                if taken is None or not taken.rows:
                    return False
                rows = taken.rows
                config = self._config.space(space_id)
                applied, turns, merged = await self._add_up(space_id, config, rows, taken.fenced)
                for consumer in self._consumers.all():
                    await consumer.consume(uow, space_id, turns)
                await uow.turns.apply(applied)
                # Merged and counted, or of a conversation an erasure fenced off: the objects go before the
                # commit. No tier expires a turn any more, so a delete that fails must leave the intake to be
                # applied again, never an object behind (a delete of a gone key does nothing).
                for key in merged:
                    await self._objects.delete(key)
        except (NiadraError, ValueError, KeyError, TypeError) as exc:
            if not rows or isinstance(exc, Unavailable):
                raise
            if len(rows) > 1:
                # The group goes one intake at a time, so only the one that fails counts an attempt.
                return await self._apply_next(space_id, 1)
            await self._failed(space_id, rows[0])
            raise
        return True

    async def _add_up(
        self,
        space_id: UUID,
        config: SpaceConfig,
        rows: Sequence[TurnIntakeRow],
        fenced: frozenset[bytes] = frozenset(),
    ) -> tuple[AppliedTurns, list[AppliedTurn], list[str]]:
        """The group's totals and rows, with what each turn's object names a person by, what the consumers
        take, and the objects to remove once it committed. A turn of a conversation an erasure fenced off
        counts for nothing, reaches no consumer, and its objects go: it arrived after, or while, the erasure
        took the rest (24.5.5)."""
        frames: list[tuple[UUID, dict[str, Any]]] = []
        added: list[tuple[UUID, dict[str, Any]]] = []
        applied: list[AppliedTurn] = []
        kept: list[KeptTurn] = []
        merged: list[str] = []
        named: dict[str, set[str]] = {}
        flagged: list[dict[str, Any]] = []
        scans: list[Scan] = []
        scanning = config.recording.pii_model and self._models is not None
        tell = subscribes(config, ("turn.flagged",))
        required = config.recording.required_pins
        kept_for = kept_until_after(config.recording.kept_days)
        for row in rows:
            body = await self._keys.decrypt(space_id, row.body_ct, intake_aad(row.intake_id), row.dek_version)
            for turn in orjson.loads(body)["turns"]:
                frame = turn["frame"]
                conversation = bytes.fromhex(turn["conversation"])
                if conversation in fenced:
                    merged += _fenced_keys(space_id, conversation, frame["turn_id"], turn.get("part"))
                    continue
                written = await self._write_deferred(space_id, row, turn) if turn.get("deferred") else None
                if written is Written.REPEAT:
                    continue
                if turn.get("named"):
                    key = turn_key(Tier.SHORT, space_id, conversation, frame["turn_id"])
                    named.setdefault(key, set()).update(turn["named"])
                if turn.get("part") or written is Written.PART:
                    part = await self._merge(space_id, turn, stored=written is None)
                    if part is None:
                        continue
                    added.append((row.source_id, part))
                    applied.append(
                        AppliedTurn(row.source_id, row.received_at, TurnRecord.model_validate(part))
                    )
                    if written is None:
                        merged.append(
                            part_key(
                                space_id, bytes.fromhex(turn["conversation"]), frame["turn_id"], turn["part"]
                            )
                        )
                    continue
                frames.append((row.source_id, frame))
                applied.append(AppliedTurn(row.source_id, row.received_at, TurnRecord.model_validate(frame)))
                if scanning and (scan := to_scan(frame, bytes.fromhex(turn["conversation"]))) is not None:
                    scans.append(scan)
                if tell and turn["kept_by"] == KeptBy.FLAG.value:
                    flagged.append(_flagged(frame, row, turn["flags"], _until(row.received_at, kept_for)))
                if turn["kept_by"] is not None:
                    kept.append(
                        _kept_row(
                            frame,
                            bytes.fromhex(turn["conversation"]),
                            row.source_id,
                            tuple(turn["flags"]),
                            replayable(frame, required),
                            KeptBy(turn["kept_by"]),
                            row.received_at,
                            _until(row.received_at, kept_for),
                        )
                    )
        return (
            AppliedTurns(
                intake_ids=tuple(r.intake_id for r in rows),
                daily=tuple(daily_totals(frames, added)),
                builds=tuple(builds_seen(frames)),
                kept=tuple(kept),
                flagged=tuple(flagged),
                scans=tuple(
                    {"turns": [{"turn_id": t.turn_id, "conversation": t.conversation} for t in batch]}
                    for batch in batches(scans)
                ),
                mentions=tuple(
                    [
                        (key, await sealed_tokens(self._keys, space_id, plain))
                        for key, plain in sorted(named.items())
                    ]
                ),
            ),
            applied,
            merged,
        )

    async def _write_deferred(self, space_id: UUID, row: TurnIntakeRow, turn: dict[str, Any]) -> Written:
        """The object of a turn the edge took without object storage: REPEAT when it turns out one, PART
        for spans of a turn already recorded. An object found with this intake's id is this worker's own
        earlier attempt, and counts."""
        frame, conversation = turn["frame"], bytes.fromhex(turn["conversation"])
        turn_id = frame["turn_id"]
        kept_by = KeptBy(turn["kept_by"]) if turn["kept_by"] else None
        sealed = await self._seal(space_id, row.source_id, row.received_at, row.intake_id, frame)
        try:
            if await self._write_object(space_id, turn_id, conversation, kept_by, sealed):
                return Written.CREATED
            data = await self._objects.get(turn_key(Tier.SHORT, space_id, conversation, turn_id))
        except (NiadraError, OSError, ValueError, TimeoutError) as exc:
            _stored_or_unavailable(exc)
        found = await self._open(space_id, turn_id, data)
        if found.get("intake_id") == str(row.intake_id):
            return Written.CREATED
        return Written.REPEAT if frame.get("fidelity", "gold") == "gold" else Written.PART

    async def _merge(self, space_id: UUID, turn: dict[str, Any], *, stored: bool) -> dict[str, Any] | None:
        """Merges a part into its turn's object and returns the frame of what it added, or None when it
        added nothing. The part is the object the edge wrote beside the turn when `stored`, or the intake's
        frame for a part the edge deferred. The object remembers each part it took, so a retry of this
        intake counts the same additions again and merges nothing twice."""
        frame, conversation = turn["frame"], bytes.fromhex(turn["conversation"])
        turn_id, digest = frame["turn_id"], turn.get("part") or part_digest(frame)
        main = turn_key(Tier.SHORT, space_id, conversation, turn_id)
        try:
            existing = await self._open(space_id, turn_id, await self._objects.get(main))
            part = frame
            if stored:
                sealed = await self._objects.get(part_key(space_id, conversation, turn_id, digest))
                part = (await self._open(space_id, turn_id, sealed))["record"]
        except NotFound:
            return None  # the turn left its tier, or this part was merged and removed already
        except (NiadraError, OSError, ValueError, TimeoutError) as exc:
            _stored_or_unavailable(exc)
        taken = existing.get("parts") or {}
        if digest in taken:
            calls, interactions = set(taken[digest]["calls"]), taken[digest]["interactions"]
            return part_of(
                existing["record"],
                {
                    **frame_of(frame),
                    "calls": [c for c in frame.get("calls") or () if c["call_id"] in calls],
                    "interactions": [
                        i for n, i in enumerate(frame.get("interactions") or ()) if n in interactions
                    ],
                },
            )
        result = merge_part(existing["record"], part)
        if result is None:
            return None
        record, addition = result
        positions = [n for n, i in enumerate(part.get("interactions") or ()) if i in addition["interactions"]]
        took = {"calls": [c["call_id"] for c in addition["calls"]], "interactions": positions}
        sealed = await self._seal_envelope(
            space_id, {**existing, "record": record, "parts": {**taken, digest: took}}
        )
        kept = turn_key(Tier.KEPT, space_id, conversation, turn_id)
        try:
            await self._objects.put(main, sealed)
            if turn["kept_by"] is not None or await self._exists(kept):
                await self._objects.put(kept, sealed)
        except (NiadraError, OSError, ValueError, TimeoutError) as exc:
            _stored_or_unavailable(exc)
        return frame_of(addition)

    async def _exists(self, key: str) -> bool:
        try:
            await self._objects.get(key)
        except NotFound:
            return False
        return True

    async def _failed(self, space_id: UUID, row: TurnIntakeRow) -> None:
        async with self._uow(space_id) as uow:
            attempts = await uow.turns.fail_intake(row.intake_id)
            if attempts >= MAX_INTAKE_ATTEMPTS:
                # It would hold back the space's later intakes: set aside, sealed as it came, never deleted;
                # its turns stay readable in object storage, and the company hears it as a data issue it can
                # retry.
                await uow.turns.set_aside_intake(row.intake_id, self._clock.now())
                if self._issues is not None:
                    source = self._config.space(space_id).source(row.source_id)
                    await self._issues.open(
                        uow,
                        space_id,
                        "row_failed",
                        field="recorded_turns",
                        source=source.name if source is not None else None,
                    )
        if attempts >= MAX_INTAKE_ATTEMPTS:
            log.error(
                "recorded turns could not be added up and were kept aside for a retry",
                extra={"space_id": str(space_id), "intake_id": str(row.intake_id), "attempts": attempts},
            )

    async def failed(self, space_id: UUID) -> list[FailedIntake]:
        """The intakes set aside after failing every attempt, oldest first, as the Console lists them."""
        async with self._uow(space_id) as uow:
            rows = await uow.turns.failed_intake(MAX_FAILED_LISTED)
        return [
            FailedIntake(r.intake_id, "turns", r.source_id, r.received_at, r.failed_at, r.attempts)
            for r in rows
            if r.failed_at is not None
        ]

    async def kept(self, space_id: UUID) -> int:
        """How many intakes are set aside, every one: a listing shows `MAX_FAILED_LISTED` at most."""
        async with self._uow(space_id) as uow:
            return await uow.turns.count_failed_intake()

    async def retry(self, space_id: UUID, intake_ids: Sequence[UUID] | None) -> int:
        """Puts intakes set aside back in the space's order, all of them when `intake_ids` is None, and has a
        worker add them up; returns how many went back."""
        async with self._uow(space_id) as uow:
            put_back = await uow.turns.retry_intake(intake_ids)
            if put_back:
                await uow.pipeline.enqueue([Task(TaskName.APPLY_TURNS, space_id, {"retry": len(put_back)})])
        return len(put_back)

    async def recorded_for(self, space_id: UUID, scope: ErasureScope) -> list[dict[str, Any]]:
        """The subject's turn records, by the keyed hashes of their conversations, unsealed for their access
        package (5.14): every tier an erasure takes, each turn once, its record as stored."""
        found: dict[str, dict[str, Any]] = {}
        for conversation in scope.conversation_hashes:
            for prefix in conversation_prefixes(space_id, conversation):
                for key in await self._objects.list_keys(prefix):
                    if not key.endswith(".bin"):
                        continue
                    turn_id = key[len(prefix) : -len(".bin")]
                    if turn_id in found:
                        continue
                    try:
                        envelope = await self._open(space_id, turn_id, await self._objects.get(key))
                    except NotFound:
                        continue
                    found[turn_id] = {
                        "turn_id": turn_id,
                        "record": envelope["record"],
                        "source_id": envelope["source_id"],
                        "received_at": envelope["received_at"],
                    }
        return [found[t] for t in sorted(found)]

    async def kept_for(self, space_id: UUID, scope: ErasureScope) -> list[dict[str, Any]]:
        """The subject's turn records in the intakes set aside, by the keyed hashes of their conversations,
        unsealed for their access package (5.14), each marked `kept_aside` with its intake: the same turns an
        erasure takes."""
        wanted = set(scope.conversation_hashes)
        found: list[dict[str, Any]] = []
        if not wanted:
            return found
        after: UUID | None = None
        while True:
            async with self._uow(space_id) as uow:
                rows = await uow.turns.failed_intake(ERASE_PAGE, after=after)
            for row in rows:
                aad = intake_aad(row.intake_id)
                body = orjson.loads(await self._keys.decrypt(space_id, row.body_ct, aad, row.dek_version))
                found += [
                    {
                        "turn": turn["frame"],
                        "source_id": row.source_id,
                        "received_at": row.received_at,
                        "kept_aside": True,
                        "intake_id": row.intake_id,
                        "failed_at": row.failed_at,
                    }
                    for turn in body["turns"]
                    if bytes.fromhex(turn["conversation"]) in wanted
                ]
            if len(rows) < ERASE_PAGE:
                return found
            after = rows[-1].intake_id

    async def _erase_failed(self, space_id: UUID, scope: ErasureScope) -> None:
        """The target's turns leave the intakes set aside; each keeps its others, sealed again, and one left
        with none goes. Their objects are the turns step's, erased by the same conversations (24.5)."""
        fenced = set(scope.conversation_hashes)
        if not fenced:
            return
        after: UUID | None = None
        while True:
            async with self._uow(space_id) as uow:
                rows = await uow.turns.failed_intake(ERASE_PAGE, after=after)
                for row in rows:
                    body = orjson.loads(
                        await self._keys.decrypt(
                            space_id, row.body_ct, intake_aad(row.intake_id), row.dek_version
                        )
                    )
                    kept = [t for t in body["turns"] if bytes.fromhex(t["conversation"]) not in fenced]
                    if body["turns"] and not kept:
                        await uow.turns.drop_intake([row.intake_id])
                        continue
                    # The cursor stays on an intake still there: one this pass deleted would end the read.
                    after = row.intake_id
                    if len(kept) == len(body["turns"]):
                        continue
                    sealed, version = await self._keys.encrypt(
                        space_id, orjson.dumps({**body, "turns": kept}), intake_aad(row.intake_id)
                    )
                    await uow.turns.rewrite_intake(row.intake_id, sealed, version, len(kept))
            if len(rows) < ERASE_PAGE:
                return

    async def scan_arguments(self, task: Task) -> None:
        """The PII model over the text of the stored records' tool arguments (24.3.5), after the rules the
        edge applied: a record where it finds something is written again, in each tier that holds it now,
        with the values replaced. A run again finds placeholders only, and writes nothing."""
        if self._models is None:
            return
        space_id = task.space_id
        opened: list[tuple[str, bytes, dict[str, Any], list[tuple[str, tuple[Any, ...], str]]]] = []
        for item in task.payload["turns"]:
            turn_id, conversation = item["turn_id"], bytes.fromhex(item["conversation"])
            try:
                data = await self._objects.get(turn_key(Tier.SHORT, space_id, conversation, turn_id))
            except NotFound:
                continue
            envelope = await self._open(space_id, turn_id, data)
            opened.append((turn_id, conversation, envelope, argument_leaves(envelope["record"])))
        texts = [text for *_, leaves in opened for _, _, text in leaves]
        if not texts:
            return
        spans = iter(await self._models.pii(texts))
        for turn_id, conversation, envelope, leaves in opened:
            if redacted(envelope["record"], leaves, [next(spans) for _ in leaves]):
                sealed = await self._seal_envelope(space_id, envelope)
                await self._objects.put(turn_key(Tier.SHORT, space_id, conversation, turn_id), sealed)
                kept = turn_key(Tier.KEPT, space_id, conversation, turn_id)
                with contextlib.suppress(NotFound):
                    await self._objects.get(kept)
                    await self._objects.put(kept, sealed)

    async def notify_flagged(self, task: Task) -> None:
        """`turn.flagged` to every endpoint that subscribes, one event per turn kept by a flag: ids only."""
        config = self._config.space(task.space_id)
        async with self._uow(task.space_id) as uow:
            for data in task.payload["turns"]:
                await enqueue_event(uow, config, "turn.flagged", None, data)

    # Legal holds

    async def keep_held(self, space_id: UUID) -> int:
        """A per-space loop step (24.5.1): every turn object of a conversation under a legal hold gets a copy
        under `turns/held/`, which no bucket rule expires, as new turns of it keep arriving; a conversation
        no longer held loses its copies. An erasure leaves them, and the release removes them. Returns the
        objects copied or removed."""
        try:
            enabled = Feature.LEGAL_HOLDS in self._config.space(space_id).features.enabled
        except NotFound:
            return 0
        held = await self._holds.held_conversations(space_id) if enabled and self._holds else frozenset()
        changed = 0
        for conversation in sorted(held):
            prefix = conversation_prefix(Tier.HELD, space_id, conversation)
            present = set(await self._objects.list_keys(prefix))
            for tier in (Tier.SHORT, Tier.KEPT, Tier.SCENARIO):
                for key in await self._objects.list_keys(conversation_prefix(tier, space_id, conversation)):
                    copy = prefix + key.rsplit("/", 1)[1]
                    if key.endswith(".bin") and copy not in present:
                        await self._objects.copy(key, copy)
                        present.add(copy)
                        changed += 1
        for key in await self._objects.list_keys(f"turns/{Tier.HELD.value}/{space_id}/"):
            if bytes.fromhex(key.split("/")[3]) not in held:
                await self._objects.delete(key)
                changed += 1
        return changed

    # Promotion

    async def promote(self, caller: SourcePrincipal, request: PromoteRequest) -> PromoteResponse:
        """Moves turns of the short tier to the kept one, as the complaint arrives days after the turn: the
        object is copied first, then the rows are added in one statement, so a row never lacks its object."""
        space_id = caller.space.space_id
        config = self._config.space(space_id)
        if request.conversation_id is not None:
            conversation = await self._conversation(space_id, request.conversation_id, None)
            prefix = conversation_prefix(Tier.SHORT, space_id, conversation)
            ids = short_turn_ids(space_id, conversation, await self._objects.list_keys(prefix))
            wanted = sorted(ids)
            located = {turn_id: conversation for turn_id in wanted}
            asked = len(wanted)
        else:
            wanted = list(dict.fromkeys(request.turn_ids))
            located = {
                t: c for t, c in zip(wanted, await _gather(self._locate(space_id), wanted), strict=True) if c
            }
            asked = len(wanted)
        now = self._clock.now()
        kept_until = _until(now, kept_until_after(config.recording.kept_days))
        required = config.recording.required_pins

        async def keep(item: tuple[str, bytes]) -> KeptTurn | None:
            turn_id, conversation = item
            short = turn_key(Tier.SHORT, space_id, conversation, turn_id)
            try:
                envelope = await self._open(space_id, turn_id, await self._objects.get(short))
                await self._objects.copy(short, turn_key(Tier.KEPT, space_id, conversation, turn_id))
            except NotFound:
                return None
            frame = frame_of(envelope["record"])
            flags = tuple(sorted(derived_flags(frame)))
            source = UUID(envelope["source_id"])
            return _kept_row(
                frame,
                conversation,
                source,
                flags,
                replayable(frame, required),
                KeptBy.REQUEST,
                now,
                kept_until,
            )

        pending = list(located.items())
        found = added = 0
        for start in range(0, len(pending), PROMOTE_BATCH):
            rows = [
                row for row in await _gather(keep, pending[start : start + PROMOTE_BATCH]) if row is not None
            ]
            async with self._uow(space_id) as uow:
                added += len(await uow.turns.keep(rows))
            found += len(rows)
        return PromoteResponse(promoted=added, already_kept=found - added, not_found=asked - found)

    def _locate(self, space_id: UUID) -> Callable[[str], Awaitable[bytes | None]]:
        async def locate(turn_id: str) -> bytes | None:
            try:
                return bytes.fromhex((await self._objects.get(by_id_key(space_id, turn_id))).decode())
            except (NotFound, ValueError):
                return None

        return locate

    # The viewer

    async def read(self, principal: Principal, turn_id: str) -> TurnView:
        space_id = principal.space.space_id
        loaded = await self.load(principal, turn_id)
        record = TurnRecord.model_validate(loaded.envelope["record"])
        await self._audit.record_read(
            principal,
            kind=ReceiptKind.QUALITY,
            surface=ReceiptSurface.TURN,
            item_ids=(turn_id,),
            conversation_id=record.conversation_id,
            task_id=record.task_id,
        )
        blocked = blockers(
            frame_of(loaded.envelope["record"]), self._config.space(space_id).recording.required_pins
        )
        return TurnView(
            record=record,
            tier="kept" if loaded.kept else "short",
            kept_until=loaded.kept.kept_until if loaded.kept else None,
            replayable=not blocked,
            replay_blockers=[ReplayBlocker.model_validate({"reason": r, "value": v}) for r, v in blocked],
        )

    async def load(self, principal: Principal, turn_id: str) -> "LoadedTurn":
        """A turn the principal may see, from wherever it is kept: a scenario's copy, the kept tier or the
        short one, then a legal hold's copy of the same conversation. NotFound once none holds it."""
        space_id = principal.space.space_id
        async with self._uow(space_id) as uow:
            kept = await uow.turns.kept([turn_id])
        if kept:
            row = kept[0]
            conversation = row.conversation
            tier = Tier.SCENARIO if row.kept_by == KeptBy.SCENARIO else Tier.KEPT
        else:
            located = await self._locate(space_id)(turn_id)
            if located is None:
                raise NotFound("turn not found")
            conversation, tier = located, Tier.SHORT
        for where in (tier, Tier.HELD):
            key = turn_key(where, space_id, conversation, turn_id)
            try:
                envelope = await self._open(space_id, turn_id, await self._objects.get(key))
            except NotFound:
                continue
            if not _visible(principal, UUID(envelope["source_id"])):
                break
            return LoadedTurn(turn_id, conversation, key, envelope, kept[0] if kept else None)
        raise NotFound("turn not found")

    async def conversation_turns(self, principal: Principal, conversation_id: str, limit: int) -> list[str]:
        """The newest turn ids of a conversation still kept in either tier, oldest first."""
        space_id = principal.space.space_id
        conversation = await self._conversation(space_id, conversation_id, None)
        prefix = conversation_prefix(Tier.SHORT, space_id, conversation)
        short = short_turn_ids(space_id, conversation, await self._objects.list_keys(prefix))
        query = TurnQuery(conversation=conversation, limit=limit, sources=_sources(principal))
        async with self._uow(space_id) as uow:
            kept = [row.turn_id for row in await uow.turns.search(query)]
        return sorted({*short, *kept})[-limit:]

    async def index(self, principal: Principal, day: date, cursor: str | None, limit: int) -> TurnIndexPage:
        """The rows the kept tier took on `day`, with the digest each one is a leaf of the day's anchored
        root by (24.5.4); in turn id order, `cursor` the last turn id of the previous page."""
        since = datetime.combine(day, time(), UTC)
        async with self._uow(principal.space.space_id) as uow:
            rows = await uow.turns.kept_between(
                since, since + timedelta(days=1), after=cursor, limit=limit + 1
            )
        page = [r for r in rows[:limit] if _visible(principal, r.source_id)]
        await self._audit.record_read(
            principal,
            kind=ReceiptKind.QUALITY,
            surface=ReceiptSurface.TURN,
            item_ids=tuple(r.turn_id for r in page),
        )
        return TurnIndexPage(
            day=day,
            items=[
                TurnIndexEntry.model_validate({**entry(r), "digest": f"sha256:{entry_digest(r).hex()}"})
                for r in page
            ],
            next_cursor=rows[limit - 1].turn_id if len(rows) > limit else None,
        )

    async def day_index(self, space_id: UUID, day: date) -> tuple[int, bytes] | None:
        """`ports.turns.TurnIndex`: how many rows the kept tier took on `day` and their Merkle root, for the
        day's audit anchor; None in a space that does not record turns."""
        try:
            if Feature.TURNS not in self._config.space(space_id).features.enabled:
                return None
        except NotFound:
            return None
        since = datetime.combine(day, time(), UTC)
        rows: list[KeptTurn] = []
        async with self._uow(space_id) as uow:
            while True:
                after = rows[-1].turn_id if rows else None
                found = await uow.turns.kept_between(
                    since, since + timedelta(days=1), after=after, limit=INDEX_PAGE
                )
                rows += found
                if len(found) < INDEX_PAGE:
                    break
        return len(rows), index_root(rows)

    async def search(self, principal: Principal, request: TurnSearchRequest) -> TurnSearchResponse:
        """The kept tier, newest first. A conversation is looked up by its hash, never by its id."""
        space_id = principal.space.space_id
        conversation = (
            await self._conversation(space_id, request.conversation_id, None)
            if request.conversation_id
            else None
        )
        query = TurnQuery(
            conversation=conversation,
            agent=request.agent,
            since=request.since,
            until=request.until,
            flags=tuple(request.flags),
            before=_cursor(request.cursor),
            limit=request.limit + 1,
            sources=_sources(principal),
        )
        async with self._uow(space_id) as uow:
            rows = await uow.turns.search(query)
        page, more = rows[: request.limit], len(rows) > request.limit
        await self._audit.record_read(
            principal,
            kind=ReceiptKind.QUALITY,
            surface=ReceiptSurface.TURN,
            item_ids=tuple(r.turn_id for r in page),
            query_hash=_query_hash(query),
        )
        return TurnSearchResponse(
            items=[
                TurnSummary(
                    turn_id=r.turn_id,
                    agent=r.agent,
                    kind=r.kind,
                    started_at=r.started_at,
                    tier="kept",
                    fidelity=r.fidelity,
                    completeness=r.completeness,
                    replayable=r.replayable,
                    flags=list(r.flags),
                )
                for r in page
            ],
            next_cursor=_next_cursor(page[-1]) if more and page else None,
        )

    async def _open(self, space_id: UUID, turn_id: str, data: bytes) -> dict[str, Any]:
        version, sealed = unpack_envelope(data)
        envelope: dict[str, Any] = orjson.loads(
            await self._keys.decrypt(space_id, sealed, record_aad(turn_id), version)
        )
        return envelope


@dataclass(frozen=True, slots=True)
class LoadedTurn:
    """A turn as stored: where it was read, the sealed envelope opened (`source_id`, `received_at`,
    `record`), and its kept row when it has one."""

    turn_id: str
    conversation: bytes
    key: str
    envelope: dict[str, Any]
    kept: KeptTurn | None


class _Refused(Exception):
    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail


def _kept_row(
    frame: Mapping[str, Any],
    conversation: bytes,
    source_id: UUID,
    flags: tuple[str, ...],
    replayable_: bool,
    kept_by: KeptBy,
    kept_at: datetime,
    kept_until: datetime | None,
) -> KeptTurn:
    pins = (frame.get("build") or {}).get("pins") or {}
    return KeptTurn(
        turn_id=frame["turn_id"],
        conversation=conversation,
        source_id=source_id,
        agent=frame["agent"]["name"],
        kind=frame.get("kind", "message"),
        started_at=started(frame),
        fidelity=frame.get("fidelity", "gold"),
        completeness=frame.get("completeness", "complete"),
        content_mode=frame["content_mode"],
        replayable=replayable_,
        build_hash=build_hash(pins),
        flags=flags,
        kept_by=kept_by,
        kept_at=kept_at,
        kept_until=kept_until,
    )


async def _gather[T](work: Callable[[T], Awaitable[Any]], items: Sequence[T]) -> list[Any]:
    """`work` over `items`, at most `OBJECT_WRITES` at a time, results in order."""
    limit = asyncio.Semaphore(OBJECT_WRITES)

    async def one(item: T) -> Any:
        async with limit:
            return await work(item)

    return list(await asyncio.gather(*(one(item) for item in items)))


def _fenced_keys(space_id: UUID, conversation: bytes, turn_id: str, part: str | None) -> list[str]:
    """Every object a turn of a fenced conversation may have left at the edge. The by-id entry stays: it
    names only the conversation's keyed hash, and a read of it finds nothing."""
    keys = [turn_key(tier, space_id, conversation, turn_id) for tier in (Tier.SHORT, Tier.KEPT)]
    return [*keys, part_key(space_id, conversation, turn_id, part)] if part else keys


def _until(at: datetime, kept_for: timedelta | None) -> datetime | None:
    """When a turn kept at `at` goes: never, until the customer sets a retention."""
    return at + kept_for if kept_for is not None else None


def _flagged(
    frame: Mapping[str, Any], row: TurnIntakeRow, flags: Sequence[str], kept_until: datetime | None
) -> dict[str, Any]:
    """A `turn.flagged` body: the turn's ids and flags, never what it said or read; a conversation id that
    reads as a phone number or an address stays out (7.5)."""
    return {
        "turn_id": frame["turn_id"],
        "conversation_id": external_reference(frame.get("conversation_id")),
        "task_id": external_reference(frame.get("task_id")),
        "source_id": str(row.source_id),
        "agent": frame["agent"]["name"],
        "flags": list(flags),
        "kept_until": kept_until.isoformat() if kept_until is not None else None,
        "at": frame["started_at"],
    }


def _stored_or_unavailable(exc: Exception) -> NoReturn:
    """A worker's object storage failure retries the group later, without counting an attempt."""
    if isinstance(exc, NiadraError) and not isinstance(exc, (Unavailable, NotFound)):
        raise exc
    raise Unavailable("object storage is unavailable", retry_after_s=5) from None


def _describe(exc: ValidationError) -> str:
    """Where each error is and why, never the value that caused it."""
    return "; ".join(".".join(str(p) for p in e["loc"]) + f": {e['msg']}" for e in exc.errors())[:500]


def _visible(principal: Principal, source_id: UUID) -> bool:
    return (
        not isinstance(principal, PersonPrincipal)
        or principal.allowed_sources is None
        or (source_id in principal.allowed_sources)
    )


def _sources(principal: Principal) -> tuple[UUID, ...] | None:
    if isinstance(principal, PersonPrincipal) and principal.allowed_sources is not None:
        return tuple(sorted(principal.allowed_sources))
    return None


def _cursor(value: str | None) -> tuple[datetime, str] | None:
    if value is None:
        return None
    try:
        at, turn_id = base64.urlsafe_b64decode(value.encode()).decode().split("|", 1)
        return datetime.fromisoformat(at), turn_id
    except (ValueError, UnicodeDecodeError):
        raise InvalidInput("the cursor is not one this search returned") from None


def _next_cursor(row: KeptTurn) -> str:
    return base64.urlsafe_b64encode(f"{row.started_at.isoformat()}|{row.turn_id}".encode()).decode()


def _query_hash(query: TurnQuery) -> bytes:
    fields = {
        "conversation": query.conversation.hex() if query.conversation else None,
        "agent": query.agent,
        "since": query.since.isoformat() if query.since else None,
        "until": query.until.isoformat() if query.until else None,
        "flags": sorted(query.flags),
    }
    return hashlib.sha256(orjson.dumps(fields, option=orjson.OPT_SORT_KEYS)).digest()
