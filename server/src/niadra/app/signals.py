"""Subject signals (24.3.2): interactions in, the constraints block out.

Interactions arrive from turn records, through `apply_turns` (`consume`, inside its transaction), or as
standalone batch items, through `apply_interactions`. Either way each is sealed once and written twice: to
`signal_intake`, the durable copy, and to the session overlay in the state store, so the next turn of any
agent sees it before it is derived. The `signals` loop derives a subject once its oldest pending interaction
waited five minutes or its session went quiet for a minute: two statements per subject per window (24.6).

The block is read from the cache and the state store, never from the database on a warm read (24.2.10):
the cache holds each handle's sealed rows, filled by the derivation and by a cold read that misses.

An objection to `personalization` erases the subject's signals and leaves a marker that turns the
derivation off for them (24.5.3). An inference the subject deleted leaves every body of theirs and a tombstone
in `inference_suppressions`; the body carries it, so the warm read honors it without a statement, and the
derivation reads the tombstones with the rest, so a body written meanwhile gets it again.
"""

import base64
import dataclasses
import hashlib
import json
import logging
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from niadra.app.collaborators import IdentityAttacher
from niadra.app.raw_index import sealed_tokens
from niadra.app.serve import BlockAsk, BlockGate, BlockRead
from niadra.app.serve_reads import Target
from niadra.config.measurement import MIN_DISTINCT_PEOPLE
from niadra.config.models import SpaceConfig
from niadra.config.tool_bindings import families
from niadra.contracts.common import Handle
from niadra.contracts.events import InteractionItem
from niadra.contracts.signals import ConstraintsBlock, Rendered
from niadra.contracts.turns import TurnRecord
from niadra.domain.common.errors import InvalidInput, NiadraError, NotFound, Unavailable
from niadra.domain.common.ids import uuid7
from niadra.domain.common.space import SourcePrincipal
from niadra.domain.compile.constraints import build, empty, gated, with_instructions
from niadra.domain.coordinate.window import CONTACT_WINDOW_TRAIT
from niadra.domain.ingest.hot import pack_envelope, unpack_envelope
from niadra.domain.ingest.masking import language
from niadra.domain.ingest.rules import default_subject_kind
from niadra.domain.measure.attribution import Shown, Touch
from niadra.domain.memory.bound import BoundRow
from niadra.domain.privacy.erasure import ErasureScope, TargetKind
from niadra.domain.privacy.files import from_parquet, to_parquet
from niadra.domain.privacy.holds import Held
from niadra.domain.privacy.mentions import (
    INTAKE_MENTIONS_READY,
    MENTION_PAGE,
    SIGNALS_MENTIONS_READY,
    MentionKey,
    MentionStore,
    Redacted,
    signals_key,
)
from niadra.domain.privacy.raw_index import body_tokens, redact_value
from niadra.domain.signals.body import BODY_VERSION, Body, Stated, Trait, decode, encode, from_json, to_json
from niadra.domain.signals.daily import (
    PRIOR_DAYS,
    PRIOR_SEGMENT,
    DailyRow,
    Prior,
    aggregate,
    end_of,
    interleaved,
    priors,
)
from niadra.domain.signals.demand import KIND as DEMAND_ENTRY
from niadra.domain.signals.demand import DemandRow, Weekly, asked_of, demand_entry, week_of, weekly
from niadra.domain.signals.demand import daily as demand_daily
from niadra.domain.signals.derive import (
    Catalog,
    Entry,
    Quotable,
    fold,
    holds,
    interest_token,
    merge,
    shown_fields,
    suppress,
    value_token,
)
from niadra.domain.signals.inferences import Inferred, inferred
from niadra.domain.signals.records import (
    GIFT,
    INFERENCE_PURPOSE,
    SELF,
    SESSION_PURPOSE,
    BeneficiaryRow,
    PendingRow,
    Segment,
    SignalRow,
    SignalWrite,
    Snapshot,
    SuppressionRow,
    beneficiary_id,
    body_aad,
    inference_input,
    label_aad,
    pending_aad,
    relation_of,
    session_digest,
)
from niadra.domain.signals.render import Call, render
from niadra.domain.signals.storage import (
    cache_key,
    daily_key,
    day_marker,
    day_markers,
    demand_key,
    intake_body,
    intake_key,
    object_aad,
    priors_key,
    raw_key,
    raw_of_marker,
    raw_prefix,
)
from niadra.domain.signals.text import block_text
from niadra.domain.tasks import Task, TaskName
from niadra.domain.vocabulary import AssertionMethod, Feature, ReceiptSurface, Verification
from niadra.ports.infra import CacheUnavailable, Clock, ConfigProvider, KeyValueCache, KeyVault, ObjectStore
from niadra.ports.signals import HeldInstruction, SaidHours, SaidInstruction, SignalOverlay
from niadra.ports.state import SeenInterest, WorkingSet
from niadra.ports.turns import AppliedTurn
from niadra.ports.uow import UnitOfWork, UnitOfWorkFactory

log = logging.getLogger(__name__)

WINDOW = timedelta(minutes=5)
QUIET = timedelta(minutes=1)
SIGNALS_EVERY_S = 15.0
SIGNALS_IDLE_CAP_S = 300.0
"""The `signals` loop's pace: every 15 s while subjects are due, backing off to 5 minutes when none are."""
DUE_LIMIT = 50
"""Subjects one pass of the loop derives; the rest wait for the next pass."""
RESOLVE_LIMIT = 200
"""Interactions without a subject one pass looks at; the rest wait for the next pass."""
OVERLAY_TTL_S = 86_400
FOLDED_TTL_S = 86_400
CACHE_TTL_S = 86_400
NUMBERS = frozenset({"money", "percent", "number"})
"""Field types whose values an item shows as numbers a claim can quote."""
REBUILD_PAGE = 500
REBUILD_LEASE_MS = 60_000
DAILY_KIND = "daily"
MARKER_DAYS = 3
"""A day's raw markers live until the pass after it: an erasure looks this many days back for them."""
DEMAND_KIND = "demand_daily"
DAILY_VERSION = "1"
DAILY_KEPT = timedelta(days=395)
"""The daily aggregate by pseudonym is kept 13 months; after that, only aggregates without a person (24.5)."""
PSEUDONYM_PURPOSE = "measure_pseudonym"
PRIORS_TTL_S = 3 * 86_400
PRIOR_SEP = "\u0000"
NIGHT_HOUR = 4
"""The nightly pass runs after 04:00 in the space's time zone, past the memory's own nightly work."""
INACTIVE = timedelta(days=395)
"""13 months without activity and what was derived about a subject decays (24.5.1): their affinity,
interests and what they were shown leave the body; what they said, the inferences they deleted and what
they turned down stay until an erasure or a retention the customer set (`app.privacy`,
`domain.signals.body.told`)."""


@dataclass(frozen=True, slots=True)
class Cached:
    """What the cache keeps of one handle: its sealed rows, and whether the subject objected."""

    rows: tuple[SignalRow, ...]
    opted_out: bool

    def to_bytes(self) -> bytes:
        return json.dumps(
            {
                "o": self.opted_out,
                "r": [
                    [
                        str(r.beneficiary_id),
                        base64.b64encode(r.body_ct).decode(),
                        r.dek_version,
                        r.body_version,
                    ]
                    for r in self.rows
                ],
            },
            separators=(",", ":"),
        ).encode()

    @classmethod
    def from_bytes(cls, handle_id: UUID, raw: bytes, now: datetime) -> "Cached":
        data = json.loads(raw)
        rows = tuple(
            SignalRow(handle_id, UUID(b), base64.b64decode(body), dek, version, 0, now, now)
            for b, body, dek, version in data["r"]
        )
        return cls(rows, bool(data["o"]))

    @classmethod
    def of(cls, rows: Iterable[SignalRow]) -> "Cached":
        kept = tuple(rows)
        return cls(tuple(r for r in kept if r.opted_out_at is None), any(r.opted_out_at for r in kept))


def catalog_of(config: SpaceConfig) -> Catalog:
    families: dict[str, str] = {}
    hidden: set[str] = set()
    sensitive: set[str] = set()
    tracked: dict[str, frozenset[str]] = {}
    quotable: dict[str, Quotable] = {}
    for declared in config.object_types.types:
        for name, spec in declared.fields.items():
            attr = f"{declared.type}.{name}"
            if spec.attribute is not None:
                families[attr] = spec.attribute.family
            if spec.sensitivity is not None or spec.pii:
                hidden.add(attr)
            elif spec.type in NUMBERS:
                quotable[attr] = Quotable(spec.role, spec.claim_max_age_ms() if spec.claimable else None)
            if spec.sensitivity is not None:
                sensitive.add(declared.type)
        tracked[declared.type] = frozenset(n for n, spec in declared.fields.items() if spec.track_changes)
    return Catalog(families, frozenset(hidden), frozenset(sensitive), tracked, quotable)


def entry_json(entry: Entry, conversation: str | None = None) -> bytes:
    """An interaction as it is sealed: the conversation travels only while it names no subject yet."""
    data: dict[str, Any] = {
        "k": entry.key,
        "at": entry.at.isoformat(),
        "i": dict(entry.interaction),
        "ss": entry.session,
        "t": entry.turn_id,
        "e": entry.event_key,
    }
    if entry.agent is not None:
        data["a"] = entry.agent
    if entry.source is not None:
        data["s"] = entry.source
    if conversation is not None:
        data["c"] = conversation
    return json.dumps(data, separators=(",", ":"), ensure_ascii=False).encode()


def entry_of(raw: bytes) -> tuple[Entry, str | None]:
    data = json.loads(raw)
    at = datetime.fromisoformat(data["at"])
    entry = Entry(data["k"], at, data["i"], data["ss"], data["t"], data["e"], data.get("a"), data.get("s"))
    return entry, data.get("c")


def interaction_json(interaction: Any) -> dict[str, Any]:
    dumped: dict[str, Any] = interaction.model_dump(mode="json", by_alias=True, exclude_none=True)
    return dumped


def demand_of(
    record: TurnRecord, interactions: Sequence[Mapping[str, Any]], catalog: Catalog
) -> list[tuple[str, dict[str, Any]]]:
    """The tool calls of a turn that returned nothing or little, as `demand` entries by call: what the
    producer checked of each call's results, and what the turn's tool arguments asked (6.6)."""
    asked = asked_of(interactions, catalog)
    out = []
    for call in record.calls:
        if call.kind != "tool" or call.status != "ok" or call.applied is None or not call.name:
            continue
        found = demand_entry(call.name, call.applied.results_checked, asked)
        if found is not None:
            out.append((call.call_id, found))
    return out


def _moment(interaction: Mapping[str, Any], record: TurnRecord) -> datetime:
    delivered = interaction.get("delivered_at")
    if delivered:
        return datetime.fromisoformat(str(delivered))
    return record.ended_at or record.started_at


class SignalsService:
    def __init__(
        self,
        uow: UnitOfWorkFactory,
        keys: KeyVault,
        cache: KeyValueCache,
        overlay: SignalOverlay,
        objects: ObjectStore,
        clock: Clock,
        config: ConfigProvider,
        *,
        identity: IdentityAttacher | None = None,
        working_set: WorkingSet | None = None,
    ) -> None:
        """`identity` resolves the handles of standalone interaction items, as ingestion does for events;
        `working_set` takes the shared objects they presented, engaged or watched, as a turn's do."""
        self._uow = uow
        self._keys = keys
        self._cache = cache
        self._overlay = overlay
        self._objects = objects
        self._clock = clock
        self._config = config
        self._identity = identity
        self._working_set = working_set
        self._catalogs: dict[UUID, tuple[object, Catalog]] = {}
        self._priors: dict[UUID, tuple[bytes, dict[tuple[str, str, str], float]]] = {}

    # What comes in.

    async def consume(self, uow: UnitOfWork, space_id: UUID, turns: Sequence[AppliedTurn]) -> None:
        """The interactions of a group of applied turns, inside its transaction: one statement to find whom
        each conversation is about, one to keep them pending (24.6). A turn applied again adds nothing."""
        if not self._on(space_id):
            return
        catalog = self._catalog(space_id)
        found: list[tuple[AppliedTurn, str, dict[str, Any]]] = []
        for t in turns:
            if not t.record.conversation_id:
                continue
            said = [interaction_json(x) for x in t.record.interactions]
            found += [(t, str(i), x) for i, x in enumerate(said)]
            found += [(t, f"d:{key}", x) for key, x in demand_of(t.record, said, catalog)]
        if not found:
            return
        conversations = sorted({t.record.conversation_id for t, _, _ in found if t.record.conversation_id})
        handles = await uow.ingest.primary_handles(conversations)
        sessions = {c: await self._session(space_id, c) for c in conversations}
        rows: list[PendingRow] = []
        overlay: dict[UUID, dict[str, bytes]] = defaultdict(dict)
        for turn, index, data in found:
            conversation = turn.record.conversation_id
            assert conversation is not None
            entry = Entry(
                f"t:{turn.record.turn_id}:{index}",
                _moment(data, turn.record),
                data,
                sessions[conversation],
                turn.record.turn_id,
                agent=turn.record.agent.name,
                source=str(turn.source_id),
            )
            handle = handles.get(conversation)
            row = await self._pending(space_id, entry, handle, turn.received_at, conversation)
            rows.append(row)
            if handle is not None and entry.kind != DEMAND_ENTRY:
                overlay[handle][entry.key] = pack_envelope(row.dek_version, row.body_ct)
        await uow.signals.add_pending(rows)
        await self._show(space_id, overlay)

    async def record(self, space_id: UUID, source_id: UUID, items: Sequence[InteractionItem]) -> None:
        """`InteractionSink`: interactions another module learned outside any turn record, taken as a
        standalone batch is (sealed in object storage, one outbox row), so its one statement is the outbox
        row."""
        if not items or not self._on(space_id):
            return
        key = intake_key(space_id, uuid7())
        dumped = [item.model_dump(mode="json", by_alias=True, exclude_none=True) for item in items]
        body = intake_body(source_id, self._clock.now(), dumped)
        sealed, version = await self._keys.encrypt(space_id, body, object_aad(key))
        await self._objects.put(key, pack_envelope(version, sealed))
        async with self._uow(space_id) as uow:
            await uow.pipeline.enqueue([Task(TaskName.APPLY_INTERACTIONS, space_id, {"key": key})])

    async def apply_interactions(self, task: Task) -> None:
        """A standalone interaction batch, kept in object storage by the request: duplicates by their key
        leave, handles resolve as an event's do, and the rest go pending like a turn's."""
        space_id, key = task.space_id, str(task.payload["key"])
        try:
            raw = await self._objects.get(key)
        except NotFound:
            return  # taken already
        version, sealed = unpack_envelope(raw)
        body = json.loads(await self._keys.decrypt(space_id, sealed, object_aad(key), version))
        received_at = datetime.fromisoformat(body["received_at"])
        overlay: dict[UUID, dict[str, bytes]] = defaultdict(dict)
        enter: dict[str, list[str]] = {}
        async with self._uow(space_id) as uow:
            items = list({item["idempotency_key"]: item for item in body["items"]}.values())
            _, known = await uow.ingest.claim_keys(
                space_id, [(i["idempotency_key"], uuid7()) for i in items], received_at
            )
            rows: list[PendingRow] = []
            for item in items:
                if item["idempotency_key"] in known:
                    continue
                try:
                    handle_id = await self._handle(uow, Handle.model_validate(item["handle"]), received_at)
                except NiadraError:
                    handle_id = None  # a handle identity refuses (a malformed number) names nobody
                if handle_id is None:
                    continue
                conversation = item.get("conversation_id")
                entry = Entry(
                    f"b:{item['idempotency_key']}",
                    datetime.fromisoformat(item["occurred_at"]),
                    item["interaction"],
                    await self._session(space_id, conversation) if conversation else None,
                    None,
                    item["idempotency_key"],
                    item.get("agent"),
                    body["source_id"],
                )
                row = await self._pending(space_id, entry, handle_id, received_at, None)
                rows.append(row)
                overlay[handle_id][entry.key] = pack_envelope(row.dek_version, row.body_ct)
                for reason, ref in entering(entry):
                    enter.setdefault(reason, []).append(ref)
            await uow.signals.add_pending(rows)
        await self._show(space_id, overlay)
        if self._working_set is not None:
            # One statement per reason: what a turn's interactions bring through the objects module.
            for reason, refs in sorted(enter.items()):
                await self._working_set.enter_working_set(space_id, sorted(set(refs)), reason)
        await self._objects.delete(key)

    async def _handle(self, uow: UnitOfWork, handle: Handle, at: datetime) -> UUID | None:
        assert self._identity is not None, "standalone interactions need the identity attacher"
        result = await self._identity.attach(
            uow,
            [handle],
            subject_kind=default_subject_kind(handle.type, handle.subject_kind),
            method=AssertionMethod.DECLARED,
            origin_event_id=None,
            level=Verification.V0,
            at=at,
        )
        return result.handle_ids[0] if result.handle_ids and result.profile_id is not None else None

    async def _pending(
        self,
        space_id: UUID,
        entry: Entry,
        handle: UUID | None,
        received_at: datetime,
        conversation: str | None,
    ) -> PendingRow:
        plain = entry_json(entry, conversation if handle is None else None)
        sealed, version = await self._keys.encrypt(space_id, plain, pending_aad(entry.key))
        named = await sealed_tokens(self._keys, space_id, body_tokens(plain))
        return PendingRow(entry.key, handle, received_at, sealed, version, entry.session, named=named)

    async def _show(self, space_id: UUID, overlay: Mapping[UUID, Mapping[str, bytes]]) -> None:
        if not overlay:
            return
        try:
            await self._overlay.add(space_id, overlay, OVERLAY_TTL_S)
        except CacheUnavailable:
            # The derivation takes them from the database within minutes; only the next turn misses them.
            log.warning("the session overlay is unavailable; interactions wait for their derivation")

    async def watch_overlay(self) -> bool:
        """Run by the cell's loops process every few seconds: once the state store came back empty, every
        space's pending interactions go back into the overlay from the database, and the store is sealed
        whole. True when it sealed it. Until then a read builds the block from what was derived."""
        try:
            if await self._overlay.whole():
                return False
            token = uuid4().hex
            if not await self._overlay.claim_rebuild(token, REBUILD_LEASE_MS):
                return False
        except CacheUnavailable:
            return False
        log.warning("the state store lost the session overlay; rebuilding it from the pending interactions")
        for config in self._config.spaces():
            if Feature.SIGNALS not in config.features.enabled:
                continue
            try:
                await self._reload_overlay(config.space_id)
            except (Unavailable, NotFound):
                continue
        return await self._overlay.seal(token)

    async def _reload_overlay(self, space_id: UUID) -> None:
        after: str | None = None
        while True:
            async with self._uow(space_id) as uow:
                page = await uow.signals.pending_page(after, REBUILD_PAGE)
            overlay: dict[UUID, dict[str, bytes]] = defaultdict(dict)
            for row in page:
                assert row.handle_id is not None
                overlay[row.handle_id][row.entry_key] = pack_envelope(row.dek_version, row.body_ct)
            await self._overlay.add(space_id, overlay, OVERLAY_TTL_S)
            if len(page) < REBUILD_PAGE:
                return
            after = page[-1].entry_key

    async def _session(self, space_id: UUID, conversation_id: str) -> str:
        return session_digest(await self._keys.hmac(space_id, SESSION_PURPOSE, conversation_id.encode()))

    # Instructions about an object, for the memory module and the compiler.

    async def instruct(self, uow: UnitOfWork, space_id: UUID, said: Sequence[SaidInstruction]) -> int:
        """`ObjectInstructions`: each instruction pending like a turn's preference, under the handle of who
        said it, with `scope: object`; the same instruction said again keeps its key."""
        if not said or not self._on(space_id):
            return 0
        rows: list[PendingRow] = []
        overlay: dict[UUID, dict[str, bytes]] = defaultdict(dict)
        now = self._clock.now()
        for s in said:
            interaction = (
                {
                    "kind": "preference",
                    "attr": s.attr or "",
                    "op": "eq",
                    "values": [True] if s.attr else [],
                    "strength": s.strength,
                    "scope": "object" if s.object else "about",
                    "source": "stated",
                    "object": s.object,
                    "about": s.about,
                    "text": s.text,
                    "audience": s.audience,
                }
                | ({"speaker": s.speaker} if s.speaker else {})
                | ({"only_with": s.only_with} if s.only_with else {})
                | ({"contact": True} if s.contact else {})
            )
            entry = Entry(f"x:{s.key}", s.at, interaction, event_key=s.event_key)
            row = await self._pending(space_id, entry, s.handle_id, now, None)
            rows.append(row)
            overlay[s.handle_id][entry.key] = pack_envelope(row.dek_version, row.body_ct)
        await uow.signals.add_pending(rows)
        await self._show(space_id, overlay)
        return len(rows)

    async def hours(self, uow: UnitOfWork, space_id: UUID, said: Sequence[SaidHours]) -> int:
        """`ObjectInstructions`: each person's contact window as a trait they said, `contact_window.<channel
        or any>`, pending like a turn's attribute: the latest said on a channel replaces the one before, and
        an empty one lifts it. The constraints block shows it in words (`contact_windows`)."""
        if not said or not self._on(space_id):
            return 0
        rows: list[PendingRow] = []
        overlay: dict[UUID, dict[str, bytes]] = defaultdict(dict)
        now = self._clock.now()
        for s in said:
            interaction = {
                "kind": "attribute",
                "name": f"{CONTACT_WINDOW_TRAIT}{s.channel or 'any'}",
                "value": s.window,
                "source": "stated",
            }
            entry = Entry(f"x:{s.key}", s.at, interaction, event_key=s.event_key)
            row = await self._pending(space_id, entry, s.handle_id, now, None)
            rows.append(row)
            overlay[s.handle_id][entry.key] = pack_envelope(row.dek_version, row.body_ct)
        await uow.signals.add_pending(rows)
        await self._show(space_id, overlay)
        return len(rows)

    async def held(
        self,
        uow: UnitOfWork,
        space_id: UUID,
        handle_ids: Sequence[UUID],
        objects: Sequence[str],
        abouts: Sequence[str] = (),
    ) -> list[HeldInstruction]:
        """`ObjectInstructions`: what the handles' own signals hold about `objects` or the organizations of
        `abouts`, the pending entries of the overlay over the derived ones, by key."""
        if not handle_ids or not (objects or abouts) or not self._on(space_id):
            return []
        now = self._clock.now()
        wanted, organizations = set(objects), set(abouts)

        def about_these(said: Stated) -> bool:
            return said.scope in ("object", "about") and holds(
                said, now, None, objects=wanted, abouts=organizations
            )

        rows = await uow.signals.rows_for(handle_ids)
        # A subject who objected to personalization keeps nothing here (24.5.3).
        out = {r.handle_id for r in rows if r.opted_out_at is not None}
        found: dict[tuple[UUID, str], Stated] = {}
        for row in rows:
            if row.beneficiary_id != SELF or row.handle_id in out:
                continue
            for said in (await self._open(space_id, row)).stated:
                if about_these(said):
                    found[(row.handle_id, said.key)] = said
        try:
            pending = await self._overlay.read(space_id, list(handle_ids))
        except CacheUnavailable:
            pending = {}
        for handle_id, waiting in pending.items():
            if handle_id in out:
                continue
            entries = []
            for key, envelope in waiting.items():
                if not key.startswith("x:"):
                    continue
                version, sealed = unpack_envelope(envelope)
                entries.append(
                    entry_of(await self._keys.decrypt(space_id, sealed, pending_aad(key), version))[0]
                )
            for said in fold(None, entries, now, self._catalog(space_id)).stated if entries else ():
                if about_these(said):
                    found[(handle_id, said.key)] = said
        return [
            HeldInstruction(
                handle_id=handle_id,
                key=said.key,
                object=said.object,
                about=said.about,
                text=said.text or said.attr,
                strength=said.strength,
                said_at=said.said_at,
                audience=said.audience,
                attr=said.attr or None,
                expires_at=said.expires_at,
                speaker=said.speaker,
                only_with=said.only_with,
                contact=said.contact,
            )
            for (handle_id, _), said in sorted(found.items(), key=lambda kv: (str(kv[0][0]), kv[0][1]))
        ]

    # Derivation.

    async def derive_due(self, space_id: UUID) -> int:
        """One pass of the `signals` loop over a space, deriving every subject whose window closed: one
        statement reads their rows and pending interactions, one writes the new bodies and takes the pending
        ones away (24.6), however many subjects the pass holds. Interactions whose conversation has a subject
        by now are resolved first."""
        if not self._on(space_id):
            return 0
        now = self._clock.now()
        catalog = self._catalog(space_id)
        async with self._uow(space_id) as uow:
            due = await uow.signals.due_snapshots(now, window=WINDOW, quiet=QUIET, limit=DUE_LIMIT)
        work = await self._resolve(space_id, now) if due.unresolved else 0
        if not due.snapshots:
            return work
        writes: list[SignalWrite] = []
        kept: dict[UUID, dict[UUID, SignalRow]] = {}
        raw: dict[UUID, list[Entry]] = {}
        for handle_id, snapshot in due.snapshots.items():
            write, rows, raw[handle_id], bodies = await self._derived(
                space_id, handle_id, snapshot, catalog, now
            )
            named = tuple([(b, await self._body_tokens(space_id, body)) for b, body in bodies.items()])
            writes.append(dataclasses.replace(write, named=named))
            kept[handle_id] = rows
        objects = await self._keep_raw(space_id, raw, catalog, now)
        writes = [dataclasses.replace(w, raw_key=objects.get(w.handle_id, (None,))[0]) for w in writes]
        try:
            async with self._uow(space_id) as uow:
                stored = await uow.signals.store_many(writes)
        except Exception:
            # Indexed in the statement that rolled back: what was written for it goes too.
            for key in (k for pair in objects.values() for k in pair):
                await self._objects.delete(key)
            raise
        for write in writes:
            before = {r.beneficiary_id: r for r in due.snapshots[write.handle_id].rows}
            written = kept[write.handle_id] if write.handle_id in stored else {}
            await self._fill(space_id, write.handle_id, Cached.of([*{**before, **written}.values()]))
            await self._forget_overlay(space_id, write.handle_id, write.folded)
        return work + len(writes)

    async def _derived(
        self, space_id: UUID, handle_id: UUID, snapshot: Snapshot, catalog: Catalog, now: datetime
    ) -> tuple[SignalWrite, dict[UUID, SignalRow], list[Entry], dict[UUID, Body]]:
        """The write of one subject: each `for` folded into its own body, and the raw interactions to keep
        for measurement, with the bodies as sealed, for their index entries. A subject who objected gets
        nothing but the removal of what was pending."""
        folded = tuple(p.entry_key for p in snapshot.pending)
        if snapshot.opted_out:
            return SignalWrite(handle_id, now, (), (), folded), {}, [], {}
        entries = [
            entry_of(await self._keys.decrypt(space_id, p.body_ct, pending_aad(p.entry_key), p.dek_version))[
                0
            ]
            for p in snapshot.pending
        ]
        before = {r.beneficiary_id: r for r in snapshot.rows}
        by_for: dict[str, list[Entry]] = defaultdict(list)
        for entry in entries:
            by_for[entry.for_].append(entry)
        rows: dict[UUID, SignalRow] = {}
        bodies: dict[UUID, Body] = {}
        beneficiaries: list[BeneficiaryRow] = []
        for for_, group in sorted(by_for.items()):
            ben = beneficiary_id(space_id, for_)
            stored = await self._open(space_id, before[ben]) if ben in before else None
            if stored is not None:
                stored = await self._honor(space_id, ben, stored, snapshot.suppressed)
            # Once more after the fold: a tombstone of something only the pending interactions held.
            body = await self._honor(space_id, ben, fold(stored, group, now, catalog), snapshot.suppressed)
            sealed, version = await self._keys.encrypt(space_id, encode(body), body_aad(handle_id, ben))
            bodies[ben] = body
            activity = body.last_activity_at or now
            rows[ben] = SignalRow(handle_id, ben, sealed, version, BODY_VERSION, len(sealed), now, activity)
            if ben != SELF:
                label = for_.removeprefix("beneficiary:").encode()
                label_ct, label_dek = await self._keys.encrypt(space_id, label, label_aad(handle_id, ben))
                beneficiaries.append(
                    BeneficiaryRow(handle_id, ben, relation_of(for_), label_ct, label_dek, "active", now, now)
                )
        write = SignalWrite(handle_id, now, tuple(rows.values()), tuple(beneficiaries), folded)
        return write, rows, entries, bodies

    async def _resolve(self, space_id: UUID, now: datetime) -> int:
        """Interactions of conversations that named no subject when their turn was applied: those whose
        conversation has one by now go to it; the rest are kept, stamped and looked at again later
        (`recheck_due`), never dropped. An erasure of their conversation, or of the person it names once it
        does, takes them (`PrivacyService.erase`)."""
        async with self._uow(space_id) as uow:
            waiting = await uow.signals.unresolved(now, RESOLVE_LIMIT)
        opened: list[tuple[PendingRow, str | None]] = []
        for row in waiting:
            plain = await self._keys.decrypt(
                space_id, row.body_ct, pending_aad(row.entry_key), row.dek_version
            )
            opened.append((row, entry_of(plain)[1]))
        overlay: dict[UUID, dict[str, bytes]] = defaultdict(dict)
        async with self._uow(space_id) as uow:
            handles = await uow.ingest.primary_handles(sorted({c for _, c in opened if c}))
            found = {r.entry_key: handles[c] for r, c in opened if c in handles}
            await uow.signals.resolve(found)
            await uow.signals.checked([r.entry_key for r, _ in opened if r.entry_key not in found], now)
        for row, _ in opened:
            if row.entry_key in found:
                overlay[found[row.entry_key]][row.entry_key] = pack_envelope(row.dek_version, row.body_ct)
        await self._show(space_id, overlay)
        return len(opened)

    async def _keep_raw(
        self, space_id: UUID, entries: Mapping[UUID, Sequence[Entry]], catalog: Catalog, now: datetime
    ) -> dict[UUID, tuple[str, str]]:
        """The raw interactions of each subject, for measurement (24.5.1), kept until an erasure or the
        `raw_content` retention the customer sets: of an object of a sensitive type, only its type and
        position stay (24.5.2). The keys of each subject's object and day marker: the derivation's write
        indexes the object (5.14.0)."""
        day = now.astimezone(UTC).date()
        kept: dict[UUID, tuple[str, str]] = {}
        for handle_id, found in entries.items():
            if not found:
                continue
            raw = [
                {"key": e.key, "at": e.at.isoformat(), "session": e.session, "interaction": _raw(e, catalog)}
                | ({"agent": e.agent} if e.agent else {})
                | ({"source": e.source} if e.source else {})
                for e in found
            ]
            object_id = uuid7()
            key = raw_key(space_id, handle_id, day, object_id)
            sealed, version = await self._keys.encrypt(space_id, json.dumps(raw).encode(), object_aad(key))
            marker = day_marker(space_id, day, handle_id, object_id)
            await self._objects.put(key, pack_envelope(version, sealed))
            await self._objects.put(marker, b"")
            kept[handle_id] = (key, marker)
        return kept

    async def _forget_overlay(self, space_id: UUID, handle_id: UUID, folded: Sequence[str]) -> None:
        try:
            await self._overlay.drop(space_id, handle_id, folded, FOLDED_TTL_S)
        except CacheUnavailable:
            log.warning("folded interactions stay in the overlay until it expires")

    async def _fill(self, space_id: UUID, handle_id: UUID, cached: Cached) -> None:
        try:
            await self._cache.set(cache_key(space_id, handle_id), cached.to_bytes(), CACHE_TTL_S)
        except CacheUnavailable:
            try:
                await self._cache.delete([cache_key(space_id, handle_id)])
            except CacheUnavailable:
                log.warning("the signals cache keeps an old copy until it expires")

    async def _open(self, space_id: UUID, row: SignalRow) -> Body:
        plain = await self._keys.decrypt(
            space_id, row.body_ct, body_aad(row.handle_id, row.beneficiary_id), row.dek_version
        )
        return decode(plain)

    # For the objects module's `now` view (24.3.13).

    async def interests(self, space_id: UUID, handle_ids: Sequence[UUID]) -> list[SeenInterest]:
        """`SubjectInterests`: the objects the subject engaged with, gave feedback on, watched or was shown,
        with the values they saw of the fields each type tracks; from the cache and the session overlay, with
        no statement once warm. Nothing for a subject who objected to personalization."""
        if not self._on(space_id) or not handle_ids:
            return []
        now = self._clock.now()
        catalog = self._catalog(space_id)
        handles = list(dict.fromkeys(handle_ids))
        cached, _ = await self._cached(space_id, handles, now)
        if any(c.opted_out for c in cached.values()):
            return []
        try:
            overlay = await self._overlay.read(space_id, handles)
        except CacheUnavailable:
            overlay = {}
        bodies = [await self._open(space_id, row) for found in cached.values() for row in found.rows]
        entries: list[Entry] = []
        for pending in overlay.values():
            for key, envelope in pending.items():
                version, sealed = unpack_envelope(envelope)
                entries.append(
                    entry_of(await self._keys.decrypt(space_id, sealed, pending_aad(key), version))[0]
                )
        body = merge(bodies, now, catalog)
        if entries:
            body = fold(body, entries, now, catalog)
        return seen_interests(body, catalog) if body is not None else []

    # The nightly pass (24.3.2, 24.7).

    async def nightly(self, space_id: UUID) -> int:
        """Once a night: yesterday's raw interactions become their daily aggregate by pseudonym (13 months,
        indexed in `measure_segments`), the interleaved lists their wins and ties, and the last 90 days of
        aggregates the priors a read smooths affinity toward. Aggregates past their 13 months leave."""
        if not self._on(space_id):
            return 0
        config = self._config.space(space_id)
        now = self._clock.now()
        day = (now - timedelta(days=1)).astimezone(UTC).date()
        by_handle = await self._raw_of_day(space_id, day)
        by_pseudonym = {await self._pseudonym(space_id, h): entries for h, entries in by_handle.items()}
        rows = aggregate(
            by_pseudonym, day, self._catalog(space_id), by_source=config.measurement.priors_by_source
        )
        segment = await self._keep_daily(space_id, day, rows)
        demand = await self._keep_demand(space_id, day, demand_daily(by_pseudonym))
        tools = interleaving_tools(config, day) if Feature.MEASUREMENT in config.features.enabled else {}
        counts = interleaved([e for entries in by_handle.values() for e in entries], tools) if tools else {}
        since = end_of(day) - timedelta(days=PRIOR_DAYS)
        async with self._uow(space_id) as uow:
            earlier = [
                s for s in await uow.signals.segments(DAILY_KIND, since) if s.starts_at != segment.starts_at
            ]
        history = [*rows]
        for older in earlier:
            history += await self._daily_rows(space_id, older)
        found = priors(history, MIN_DISTINCT_PEOPLE)
        async with self._uow(space_id) as uow:
            await uow.signals.put_segment(segment, now)
            await uow.signals.put_segment(demand, now)
            await uow.signals.replace_priors(found, now)
            if counts:
                await uow.outcomes.put_interleaved(day, counts)
            expired = await uow.signals.expire_segments(now)
        for key in expired:
            await self._objects.delete(key)
        # The day is read; its markers are not needed again. The next day's stay for tomorrow's pass.
        await self._objects.delete_prefix(day_markers(space_id, day))
        await self._put_priors(space_id, found)
        return len(rows) + len(counts)

    async def keep_priors(self, space_id: UUID) -> int:
        """The priors back in the cache after it lost them, from the database: one statement, only then."""
        if not self._on(space_id):
            return 0
        try:
            if await self._cache.get(priors_key(space_id)) is not None:
                return 0
        except CacheUnavailable:
            return 0
        async with self._uow(space_id) as uow:
            found = await uow.signals.priors()
        await self._put_priors(space_id, found)
        return 1

    async def _put_priors(self, space_id: UUID, found: Sequence[Prior]) -> None:
        body = json.dumps(
            {PRIOR_SEP.join((p.segment, p.attr, p.value)): p.prior for p in found}, separators=(",", ":")
        )
        try:
            await self._cache.set(priors_key(space_id), body.encode(), PRIORS_TTL_S)
        except CacheUnavailable:
            log.warning("the priors are not in the cache; reads smooth toward none until the next pass")

    def _priors_of(self, space_id: UUID, raw: bytes | None) -> dict[tuple[str, str, str], float]:
        """The priors a read found in the cache by segment, attribute and value, parsed once per version."""
        if raw is None:
            return {}
        known = self._priors.get(space_id)
        if known is None or known[0] != raw:
            parsed: dict[tuple[str, str, str], float] = {}
            for key, prior in json.loads(raw).items():
                segment, attr, value = key.split(PRIOR_SEP, 2)
                parsed[(segment, attr, value)] = float(prior)
            known = (raw, parsed)
            self._priors[space_id] = known
        return known[1]

    async def _raw_of_day(self, space_id: UUID, day: date) -> dict[UUID, list[Entry]]:
        """The day's raw interactions by handle: from the objects of that day and the next, derived late,
        found by the days' markers. A marker whose object an erasure took names nothing."""
        out: dict[UUID, list[Entry]] = defaultdict(list)
        for kept_on in (day, day + timedelta(days=1)):
            for marker in sorted(await self._objects.list_keys(day_markers(space_id, kept_on))):
                handle, key = raw_of_marker(space_id, kept_on, marker)
                try:
                    stored = await self._objects.get(key)
                except NotFound:
                    continue
                await self._read_raw(space_id, key, stored, day, out[handle])
        return {h: entries for h, entries in out.items() if entries}

    async def _read_raw(self, space_id: UUID, key: str, stored: bytes, day: date, into: list[Entry]) -> None:
        version, sealed = unpack_envelope(stored)
        for item in json.loads(await self._keys.decrypt(space_id, sealed, object_aad(key), version)):
            entry = Entry(
                item["key"], datetime.fromisoformat(item["at"]), item["interaction"], item.get("session"),
                _turn_of(item["key"]), agent=item.get("agent"), source=item.get("source"),
            )  # fmt: skip
            if entry.at.astimezone(UTC).date() == day:
                into.append(entry)

    async def _pseudonym(self, space_id: UUID, handle_id: UUID) -> str:
        return (await self._keys.hmac(space_id, PSEUDONYM_PURPOSE, handle_id.bytes)).hex()[:32]

    async def _keep_daily(self, space_id: UUID, day: date, rows: Sequence[DailyRow]) -> Segment:
        return await self._keep(space_id, DAILY_KIND, daily_key(space_id, day), day, rows)

    async def _keep_demand(self, space_id: UUID, day: date, rows: Sequence[DemandRow]) -> Segment:
        return await self._keep(space_id, DEMAND_KIND, demand_key(space_id, day), day, rows)

    async def _keep(self, space_id: UUID, kind: str, key: str, day: date, rows: Sequence[Any]) -> Segment:
        """A day's aggregate by pseudonym, sealed in object storage and indexed by the caller (13 months)."""
        data = to_parquet([dataclasses.asdict(r) for r in rows], {"niadra.schema_version": DAILY_VERSION})
        sealed, version = await self._keys.encrypt(space_id, data, object_aad(key))
        stored = pack_envelope(version, sealed)
        await self._objects.put(key, stored)
        starts = end_of(day) - timedelta(days=1)
        return Segment(
            uuid7(), kind, starts, key, len(rows), len(stored), hashlib.sha256(stored).digest(),
            starts + DAILY_KEPT,
        )  # fmt: skip

    async def _daily_rows(self, space_id: UUID, segment: Segment) -> list[DailyRow]:
        return [DailyRow(**row) for row in await self._segment_rows(space_id, segment)]

    async def _segment_rows(self, space_id: UUID, segment: Segment) -> list[dict[str, Any]]:
        try:
            version, sealed = unpack_envelope(await self._objects.get(segment.object_key))
        except NotFound:
            return []
        data = await self._keys.decrypt(space_id, sealed, object_aad(segment.object_key), version)
        return list(from_parquet(data))

    async def _forget_daily(self, space_id: UUID, handles: Sequence[UUID]) -> None:
        """The erased handles' rows leave every daily aggregate and every day of unmet demand that holds them,
        which is written again."""
        gone = {await self._pseudonym(space_id, h) for h in handles}
        async with self._uow(space_id) as uow:
            segments = await uow.signals.segments(DAILY_KIND, datetime.min.replace(tzinfo=UTC))
            segments += await uow.signals.segments(DEMAND_KIND, datetime.min.replace(tzinfo=UTC))
        for segment in segments:
            rows = await self._segment_rows(space_id, segment)
            kept = [r for r in rows if r["pseudonym"] not in gone]
            if len(kept) == len(rows):
                continue
            data = to_parquet(kept, {"niadra.schema_version": DAILY_VERSION})
            sealed, version = await self._keys.encrypt(space_id, data, object_aad(segment.object_key))
            stored = pack_envelope(version, sealed)
            await self._objects.put(segment.object_key, stored)
            rewritten = dataclasses.replace(
                segment, rows=len(kept), size_bytes=len(stored), sha256=hashlib.sha256(stored).digest()
            )
            async with self._uow(space_id) as uow:
                await uow.signals.rewrite_segment(rewritten, self._clock.now())

    def min_people(self, space_id: UUID) -> int:
        return self._config.space(space_id).measurement.unmet_demand_min_people

    async def unmet_demand(
        self, space_id: UUID, since: date, until: date, *, tool: str | None = None
    ) -> list[Weekly]:
        """What the tools did not return, by week and combination of what was asked, only where the space's
        minimum of distinct people asked it (6.6): the days of unmet demand of those weeks, read from object
        storage."""
        start = datetime.combine(week_of(since), datetime.min.time(), UTC)
        async with self._uow(space_id) as uow:
            segments = await uow.signals.segments(DEMAND_KIND, start)
        days: list[tuple[date, list[DemandRow]]] = []
        for segment in segments:
            day = segment.starts_at.astimezone(UTC).date()
            if day > until:
                continue
            rows = [DemandRow(**r) for r in await self._segment_rows(space_id, segment)]
            days.append((day, [r for r in rows if tool is None or r.tool == tool]))
        return weekly(days, self.min_people(space_id))

    # For the measurement of outcomes (24.7).

    async def seen(self, space_id: UUID, handle_ids: Sequence[UUID]) -> tuple[list[Shown], list[Touch]]:
        """What these handles were shown and engaged with, from the raw interactions kept for measurement (45
        days) and the ones still waiting for their derivation: one statement, and the object store."""
        async with self._uow(space_id) as uow:
            pending = await uow.signals.pending_of(handle_ids)
        entries: list[Entry] = []
        for row in pending:
            plain = await self._keys.decrypt(
                space_id, row.body_ct, pending_aad(row.entry_key), row.dek_version
            )
            entries.append(entry_of(plain)[0])
        for handle_id in handle_ids:
            for key in sorted(await self._objects.list_keys(raw_prefix(space_id, handle_id))):
                version, sealed = unpack_envelope(await self._objects.get(key))
                for item in json.loads(await self._keys.decrypt(space_id, sealed, object_aad(key), version)):
                    entries.append(
                        Entry(
                            item["key"], datetime.fromisoformat(item["at"]), item["interaction"],
                            item.get("session"), _turn_of(item["key"]), agent=item.get("agent"),
                        )
                    )  # fmt: skip
        return exposures_and_touches(entries)

    async def derived(self, uow: UnitOfWork, space_id: UUID, traits: Sequence[tuple[UUID, Entry]]) -> None:
        """What an outcome says the subject is (a size kept, or returned for not fitting), pending for the
        next derivation like any interaction: one statement, inside the caller's transaction."""
        if not traits or not self._on(space_id):
            return
        rows = [await self._pending(space_id, entry, handle, entry.at, None) for handle, entry in traits]
        await uow.signals.add_pending(rows)

    async def _body_tokens(self, space_id: UUID, body: Body) -> frozenset[bytes]:
        """What a body names a person by: what was said, the traits, the values seen."""
        return await sealed_tokens(self._keys, space_id, body_tokens(_body_json(body)))

    async def redact_named(
        self, space_id: UUID, scope: ErasureScope, plain: frozenset[str], tokens: frozenset[bytes], held: Held
    ) -> Redacted:
        """`NamedInText`: the signals of others whose body names the target (an instruction, a contact
        preference, a trait) keep, with the target's identifiers replaced by `ERASED`, found by the index of
        mentions a page at a time; the cache drops the subjects' copies. A held handle's body keeps as it
        is. Bodies sealed before the index existed are read into it once. The interactions still waiting to
        be folded are redacted too (`_redact_pending`), so the next fold does not bring the target back."""
        await self._index_written_before(space_id)
        done = await self._redact_pending(space_id, scope, plain, tokens, held)
        after: MentionKey | None = None
        while True:
            async with self._uow(space_id) as uow:
                keys = await uow.mentions.naming(
                    sorted(tokens), plain, stores=[MentionStore.SIGNALS], after=after, limit=MENTION_PAGE
                )
                wanted = {(UUID(k.row[:36]), UUID(k.row[37:])) for k in keys}
                found = {
                    (r.handle_id, r.beneficiary_id): r
                    for r in await uow.signals.rows_for(sorted({h for h, _ in wanted}, key=str))
                    if (r.handle_id, r.beneficiary_id) in wanted
                }
                now = self._clock.now()
                writes: list[SignalWrite] = []
                index: list[tuple[MentionKey, frozenset[bytes]]] = []
                for handle_id, ben in sorted(wanted, key=str):
                    key, row = signals_key(handle_id, ben), found.get((handle_id, ben))
                    if row is None:
                        index.append((key, frozenset()))
                        continue
                    if handle_id in scope.handle_ids:
                        continue
                    body = await self._open(space_id, row)
                    redacted, named = redact_value(to_json(body), plain)
                    if not named:
                        index.append((key, await self._body_tokens(space_id, body)))
                        continue
                    if handle_id in held.handles:
                        done.held += 1
                        continue
                    fixed = from_json(redacted)
                    sealed, version = await self._keys.encrypt(
                        space_id, encode(fixed), body_aad(handle_id, ben)
                    )
                    written = SignalRow(
                        handle_id, ben, sealed, version, BODY_VERSION, len(sealed), now, row.last_activity_at,
                        row.opted_out_at,
                    )  # fmt: skip
                    writes.append(SignalWrite(handle_id, now, (written,), (), ()))
                    index.append((key, await self._body_tokens(space_id, fixed)))
                    done.rows += 1
                    done.handles.add(handle_id)
                await uow.signals.store_many(writes)
                await uow.mentions.index(index, replace=True)
            if writes:
                await self._clear_cached(space_id, [w.handle_id for w in writes])
            if len(keys) < MENTION_PAGE:
                return done
            after = keys[-1]

    async def _redact_pending(
        self, space_id: UUID, scope: ErasureScope, plain: frozenset[str], tokens: frozenset[bytes], held: Held
    ) -> Redacted:
        """The interactions of others still waiting to be folded that name the target, sealed again with the
        target redacted out, in the database and in the session overlay; the target's own go with the
        cascade, and a held handle's keep as they are."""
        await self._index_pending(space_id)
        done = Redacted()
        after: MentionKey | None = None
        while True:
            overlay: dict[UUID, dict[str, bytes]] = defaultdict(dict)
            async with self._uow(space_id) as uow:
                keys = await uow.mentions.naming(
                    sorted(tokens), plain, stores=[MentionStore.INTAKE], after=after, limit=MENTION_PAGE
                )
                found = {r.entry_key: r for r in await uow.signals.pending_by_keys([k.row for k in keys])}
                rewritten: list[PendingRow] = []
                index: list[tuple[MentionKey, frozenset[bytes]]] = []
                for key in keys:
                    row = found.get(key.row)
                    if row is None:
                        index.append((key, frozenset()))  # folded since
                        continue
                    if row.handle_id is not None and row.handle_id in scope.handle_ids:
                        continue
                    opened = await self._keys.decrypt(
                        space_id, row.body_ct, pending_aad(row.entry_key), row.dek_version
                    )
                    redacted, named = redact_value(json.loads(opened), plain)
                    if not named:
                        continue
                    if row.handle_id is not None and row.handle_id in held.handles:
                        done.held += 1
                        continue
                    body = json.dumps(redacted).encode()
                    sealed, version = await self._keys.encrypt(space_id, body, pending_aad(row.entry_key))
                    rewritten.append(dataclasses.replace(row, body_ct=sealed, dek_version=version))
                    index.append((key, await sealed_tokens(self._keys, space_id, body_tokens(body))))
                    if row.handle_id is not None:
                        overlay[row.handle_id][row.entry_key] = pack_envelope(version, sealed)
                        done.handles.add(row.handle_id)
                await uow.signals.rewrite_pending(rewritten)
                await uow.mentions.index(index, replace=True)
            done.rows += len(rewritten)
            await self._show(space_id, overlay)
            if len(keys) < MENTION_PAGE:
                return done
            after = keys[-1]

    async def _index_pending(self, space_id: UUID) -> None:
        """Reads, once, the interactions sealed before the index existed into it, by the first erasure."""
        async with self._uow(space_id) as uow:
            if await uow.mentions.ready(INTAKE_MENTIONS_READY):
                return
        after: str | None = None
        while True:
            async with self._uow(space_id) as uow:
                rows = await uow.signals.intake_page(after, MENTION_PAGE)
                entries = []
                for r in rows:
                    opened = await self._keys.decrypt(
                        space_id, r.body_ct, pending_aad(r.entry_key), r.dek_version
                    )
                    named = await sealed_tokens(self._keys, space_id, body_tokens(opened))
                    entries.append((MentionKey(MentionStore.INTAKE, r.entry_key), named))
                await uow.mentions.index([(k, t) for k, t in entries if t])
            if len(rows) < MENTION_PAGE:
                break
            after = rows[-1].entry_key
        async with self._uow(space_id) as uow:
            await uow.mentions.mark_ready(INTAKE_MENTIONS_READY)

    async def _clear_cached(self, space_id: UUID, handles: Sequence[UUID]) -> None:
        try:
            await self._cache.delete([cache_key(space_id, h) for h in handles])
        except CacheUnavailable:
            log.warning("the signals cache keeps an old copy until it expires")

    async def _index_written_before(self, space_id: UUID) -> None:
        """Reads, once, the bodies sealed before the index existed into it, by the first erasure."""
        async with self._uow(space_id) as uow:
            if await uow.mentions.ready(SIGNALS_MENTIONS_READY):
                return
        after: tuple[UUID, UUID] | None = None
        while True:
            async with self._uow(space_id) as uow:
                rows = await uow.signals.rows_page(after, MENTION_PAGE)
                await uow.mentions.index(
                    [
                        (
                            signals_key(r.handle_id, r.beneficiary_id),
                            await self._body_tokens(space_id, await self._open(space_id, r)),
                        )
                        for r in rows
                    ]
                )
            if len(rows) < MENTION_PAGE:
                break
            after = (rows[-1].handle_id, rows[-1].beneficiary_id)
        async with self._uow(space_id) as uow:
            await uow.mentions.mark_ready(SIGNALS_MENTIONS_READY)

    # Objection and erasure.

    async def withdraw(self, task: Task) -> None:
        """An objection to personalization (24.5.3): the handles' signals go, and a marker stops their
        derivation for good."""
        space_id = task.space_id
        handles = [UUID(h) for h in task.payload["handles"]]
        now = self._clock.now()
        markers = []
        for handle_id in handles:
            sealed, version = await self._keys.encrypt(
                space_id, encode(Body(at=now)), body_aad(handle_id, SELF)
            )
            markers.append(
                SignalRow(handle_id, SELF, sealed, version, BODY_VERSION, len(sealed), now, now, now)
            )
        async with self._uow(space_id) as uow:
            await uow.signals.withdraw(handles, markers)
        await self._clear(space_id, handles, Cached((), True))

    async def purge(self, task: Task) -> None:
        """After an erasure: what signals keep outside the database. For a subject or a handle, the overlay,
        the cache and the raw interactions go; for a conversation, what was said in it leaves the bodies,
        the overlay and the raw objects (the accumulators it fed only decay)."""
        space_id = task.space_id
        async with self._uow(space_id) as uow:
            request = await uow.privacy.get_erasure(space_id, UUID(task.payload["request_id"]))
        if request is None:
            return
        scope = request.scope
        if scope.kind is not TargetKind.CONVERSATION:
            await self._clear(space_id, list(scope.handle_ids), None)
            today = self._clock.now().astimezone(UTC).date()
            for handle_id in scope.handle_ids:
                await self._objects.delete_prefix(raw_prefix(space_id, handle_id))
                for back in range(MARKER_DAYS):
                    await self._objects.delete_prefix(
                        day_markers(space_id, today - timedelta(days=back), handle_id)
                    )
            await self._forget_daily(space_id, scope.handle_ids)
            return
        await self._forget_conversation(space_id, scope)

    async def _clear(self, space_id: UUID, handles: Sequence[UUID], cached: Cached | None) -> None:
        await self._overlay.purge(space_id, handles)
        if cached is None:
            await self._cache.delete([cache_key(space_id, h) for h in handles])
        else:
            for handle_id in handles:
                await self._cache.set(cache_key(space_id, handle_id), cached.to_bytes(), CACHE_TTL_S)

    async def _forget_conversation(self, space_id: UUID, scope: ErasureScope) -> None:
        if scope.conversation_id is None:
            return
        session = await self._session(space_id, scope.conversation_id)
        now = self._clock.now()
        for handle_id in scope.handle_ids:
            async with self._uow(space_id) as uow:
                snapshot = await uow.signals.snapshot(handle_id)
            rows = []
            for row in snapshot.rows:
                if row.opted_out_at is not None:
                    continue
                body = await self._open(space_id, row)
                kept = [s for s in body.stated if s.session != session]
                if len(kept) == len(body.stated):
                    continue
                body.stated = kept
                sealed, version = await self._keys.encrypt(
                    space_id, encode(body), body_aad(handle_id, row.beneficiary_id)
                )
                rows.append(
                    SignalRow(
                        handle_id, row.beneficiary_id, sealed, version, BODY_VERSION, len(sealed), now,
                        row.last_activity_at,
                    )
                )  # fmt: skip
            if rows:
                async with self._uow(space_id) as uow:
                    await uow.signals.store(SignalWrite(handle_id, now, tuple(rows), (), ()))
            overlay = (await self._overlay.read(space_id, [handle_id])).get(handle_id, {})
            said = []
            for key, envelope in overlay.items():
                version, sealed = unpack_envelope(envelope)
                entry, _ = entry_of(await self._keys.decrypt(space_id, sealed, pending_aad(key), version))
                if entry.session == session:
                    said.append(key)
            await self._overlay.drop(space_id, handle_id, said, FOLDED_TTL_S)
            await self._cache.delete([cache_key(space_id, handle_id)])
            await self._forget_raw(space_id, handle_id, session)

    async def _forget_raw(self, space_id: UUID, handle_id: UUID, session: str) -> None:
        for key in await self._objects.list_keys(raw_prefix(space_id, handle_id)):
            version, sealed = unpack_envelope(await self._objects.get(key))
            kept = json.loads(await self._keys.decrypt(space_id, sealed, object_aad(key), version))
            left = [e for e in kept if e.get("session") != session]
            if len(left) == len(kept):
                continue
            await self._objects.delete(key)
            if left:
                sealed, version = await self._keys.encrypt(
                    space_id, json.dumps(left).encode(), object_aad(key)
                )
                await self._objects.put(key, pack_envelope(version, sealed))

    async def _honor(
        self, space_id: UUID, beneficiary: UUID, body: Body, tombstones: Sequence[SuppressionRow]
    ) -> Body:
        """The body with every tombstone of its beneficiary applied: a deletion that raced this derivation's
        read is not undone by its write."""
        wanted = [t for t in tombstones if t.beneficiary_id == beneficiary]
        if not wanted:
            return body
        tokens = [
            *body.suppressed,
            *(value_token(a, v) for a, v in body.values),
            *map(interest_token, body.interests),
        ]
        named = {await self._inference_hmac(space_id, beneficiary, token): token for token in tokens}
        for tombstone in wanted:
            token = named.get(tombstone.key_hmac)
            held = body.suppressed.get(token) if token else None
            if token is not None and (held is None or held < tombstone.evidence_until):
                suppress(body, token, tombstone.evidence_until)
        return body

    async def _inference_hmac(self, space_id: UUID, beneficiary: UUID, token: str) -> bytes:
        return await self._keys.hmac(space_id, INFERENCE_PURPOSE, inference_input(beneficiary, token))

    # The reviewable profile (24.5.3).

    async def views(
        self, space_id: UUID, handles: Sequence[UUID]
    ) -> tuple[dict[UUID, Body], dict[UUID, str]]:
        """Each beneficiary's body across the subject's handles, what is still pending folded in, and whom
        each is for: what the block would be built from. A governance read, from the database and the
        overlay; nothing for a subject who objected to personalization."""
        now = self._clock.now()
        catalog = self._catalog(space_id)
        async with self._uow(space_id) as uow:
            rows = await uow.signals.rows_for(handles)
            found = await uow.signals.beneficiaries_for(handles)
        if any(r.opted_out_at is not None for r in rows):
            return {}, {}
        stored: dict[UUID, list[Body]] = defaultdict(list)
        for row in rows:
            stored[row.beneficiary_id].append(await self._open(space_id, row))
        try:
            overlay = await self._overlay.read(space_id, list(handles))
        except CacheUnavailable:
            overlay = {}
        fors = {SELF: "self"}
        pending: dict[UUID, list[Entry]] = defaultdict(list)
        for waiting in overlay.values():
            for key, envelope in waiting.items():
                version, sealed = unpack_envelope(envelope)
                entry, _ = entry_of(await self._keys.decrypt(space_id, sealed, pending_aad(key), version))
                ben = beneficiary_id(space_id, entry.for_)
                fors[ben] = entry.for_
                pending[ben].append(entry)
        for b in found:
            if b.beneficiary_id in fors or b.label_ct is None or b.dek_version is None:
                continue
            aad = label_aad(b.handle_id, b.beneficiary_id)
            label = (await self._keys.decrypt(space_id, b.label_ct, aad, b.dek_version)).decode()
            fors[b.beneficiary_id] = GIFT if b.relation == GIFT else f"beneficiary:{label}"
        bodies: dict[UUID, Body] = {}
        for ben in (set(stored) | set(pending)) & set(fors):
            body = merge(stored.get(ben, []), now, catalog)
            if pending.get(ben):
                body = fold(body, pending[ben], now, catalog)
            if body is not None:
                bodies[ben] = body
        return bodies, fors

    async def inferences(self, space_id: UUID, handles: Sequence[UUID]) -> list[tuple[UUID, str, Inferred]]:
        """Every inference about the subject, with whose body it is in and whom that is for."""
        bodies, fors = await self.views(space_id, handles)
        catalog = self._catalog(space_id)
        return [(ben, fors[ben], i) for ben, body in bodies.items() for i in inferred(body, catalog)]

    async def inference_key(self, space_id: UUID, beneficiary: UUID, token: str) -> bytes:
        """The keyed hash an inference is named by (in a URL and in its tombstone), never its value."""
        return await self._inference_hmac(space_id, beneficiary, token)

    async def suppress(
        self, space_id: UUID, handles: Sequence[UUID], beneficiary: UUID, token: str, kind: str
    ) -> bool:
        """Deletes an inference (24.5.3): it leaves the beneficiary's body on every handle of the subject, a
        body the handle lacks is started for the tombstone alone, and the tombstones are written in the same
        transaction. The cache takes the new rows. False for a subject who objected: nothing of theirs is
        inferred."""
        now = self._clock.now()
        async with self._uow(space_id) as uow:
            rows = await uow.signals.rows_for(handles)
        if any(r.opted_out_at is not None for r in rows):
            return False
        mine = {r.handle_id: r for r in rows if r.beneficiary_id == beneficiary}
        written: dict[UUID, SignalRow] = {}
        for handle_id in handles:
            row = mine.get(handle_id)
            body = await self._open(space_id, row) if row is not None else Body(at=now)
            sealed, version = await self._keys.encrypt(
                space_id, encode(suppress(body, token, now)), body_aad(handle_id, beneficiary)
            )
            activity = row.last_activity_at if row is not None else now
            written[handle_id] = SignalRow(
                handle_id, beneficiary, sealed, version, BODY_VERSION, len(sealed), now, activity
            )
        key = await self._inference_hmac(space_id, beneficiary, token)
        async with self._uow(space_id) as uow:
            await uow.signals.store_many([SignalWrite(h, now, (r,), (), ()) for h, r in written.items()])
            await uow.signals.add_suppressions(
                [SuppressionRow(h, beneficiary, key, kind, now, now) for h in written]
            )
        for handle_id, row in written.items():
            others = [r for r in rows if r.handle_id == handle_id and r.beneficiary_id != beneficiary]
            await self._fill(space_id, handle_id, Cached.of([*others, row]))
        return True

    async def said(self, space_id: UUID, handle_id: UUID, interaction: Mapping[str, Any]) -> None:
        """What the subject said through a review of their profile (a correction), pending like a turn's
        interaction: in `signal_intake` and in the overlay, so the next read has it."""
        now = self._clock.now()
        entry = Entry(f"c:{uuid7()}", now, dict(interaction))
        row = await self._pending(space_id, entry, handle_id, now, None)
        async with self._uow(space_id) as uow:
            await uow.signals.add_pending([row])
        await self._show(space_id, {handle_id: {entry.key: pack_envelope(row.dek_version, row.body_ct)}})

    # The block.

    async def block(
        self,
        space_id: UUID,
        target: Target | None,
        *,
        for_: str = "self",
        conversation_id: str | None = None,
        withheld: frozenset[str] = frozenset(),
        source: UUID | None = None,
        gate: BlockGate | None = None,
        instructions: Sequence[tuple[str, BoundRow]] = (),
        accounts: Sequence[tuple[UUID, BoundRow]] = (),
        carried: frozenset[str] = frozenset(),
        hours_said: bool = False,
    ) -> ConstraintsBlock:
        """What the subject wants, refuses and is, for this turn's tools, with the session's pending
        interactions folded in. The cache and the state store only, once the handle is warm. Affinity smooths
        toward the reading source's own priors where it has them, and the space's elsewhere. Beside a pack
        (`gate`), an entry goes only where the pack's policy lets an item of the type it names go."""
        if target is None:
            return ConstraintsBlock.model_validate({**empty(for_), "text": ""})
        now = self._clock.now()
        catalog = self._catalog(space_id)
        handles = list(dict.fromkeys(target.profile.handle_ids))
        cached, raw_priors = await self._cached(space_id, handles, now)
        try:
            overlay = await self._overlay.read(space_id, handles)
        except CacheUnavailable:
            log.warning("the session overlay is unavailable; the block holds what was derived")
            overlay = {}
        wanted = beneficiary_id(space_id, for_)
        bodies: list[Body] = []
        entries: list[Entry] = []
        others = False
        for handle_id in handles:
            found = cached.get(handle_id)
            if found is not None and found.opted_out:
                continue
            for row in found.rows if found else ():
                others = others or row.beneficiary_id != SELF
                if row.beneficiary_id == wanted:
                    bodies.append(await self._open(space_id, row))
            for key, envelope in overlay.get(handle_id, {}).items():
                version, sealed = unpack_envelope(envelope)
                entry, _ = entry_of(await self._keys.decrypt(space_id, sealed, pending_aad(key), version))
                others = others or entry.for_ != "self"
                if entry.for_ == for_:
                    entries.append(entry)
        body = merge(bodies, now, catalog)
        if entries:
            body = fold(body, entries, now, catalog)
        session = await self._session(space_id, conversation_id) if conversation_id else None
        known = self._priors_of(space_id, raw_priors)
        built = build(
            body,
            for_=for_,
            session=session,
            now=now,
            catalog=catalog,
            prior=lambda attr, value: known.get(
                (str(source), attr, value), known.get((PRIOR_SEGMENT, attr, value), 0.0)
            ),
            others=others,
            withheld=withheld,
        )
        if gate is not None:
            built = gated(built, lambda path: gate.admits_field(path, target))
        # The instructions about the objects the read is about, as the pack beside it let them through.
        said = with_instructions(built, instructions, accounts)
        # An account's instruction the served pack already has a line for stays in the entries, for a program,
        # and is not said again in the text: twice, beside a contact preference, it left an attendant refusing
        # Marcos his own claim amount (juridico-sandbox, 06/10/2026). Those said after the pin stay in both.
        told = [(p, r) for p, r in accounts if f"instruction:{r.row_id}" not in carried]
        shown = said if len(told) == len(accounts) else with_instructions(built, instructions, told)
        built = said
        space = self._config.space(space_id)
        text = block_text(
            shown,
            space.object_types.labels(),
            language(space.settings.locale),
            shown_fields(body),
            hours_said=hours_said,
        )
        return ConstraintsBlock.model_validate({**built, "text": text or ""})

    def for_tool(self, caller: SourcePrincipal, block: ConstraintsBlock, tool: str) -> ConstraintsBlock:
        """The block rendered through the binding of `tool` for the caller's source, in advisory mode: the
        server sees no call, so it suggests what the tool's arguments can carry and names the residual
        (`constraints.v0`, rendering). The configuration only: no statement."""
        space = self._config.space(caller.space.space_id)
        bound = space.tool_bindings.get(tool, caller.source_id)
        if bound is None:
            raise InvalidInput("no binding is declared for this tool")
        call = Call({}, for_=block.subject.for_)
        rendering = render(block, bound.binding(families(space.object_types)), call, "advisory")
        rendered = Rendered(
            tool=tool,
            mode="advisory",
            args=dict(rendering.suggested),
            residual=list(rendering.residual),
            post_filter=list(rendering.post_filter),
        )
        return block.model_copy(update={"rendered": rendered})

    async def _cached(
        self, space_id: UUID, handles: Sequence[UUID], now: datetime
    ) -> tuple[dict[UUID, Cached], bytes | None]:
        """Each handle's rows from the cache, and the space's priors in the same round trip; the rows the
        cache misses from the database, once, and back into the cache (a handle with no signals is cached
        too, so its next read runs no statement). Priors the cache lost wait for `keep_priors`."""
        found: dict[UUID, Cached] = {}
        try:
            *raws, raw_priors = await self._cache.get_many(
                [*(cache_key(space_id, h) for h in handles), priors_key(space_id)]
            )
        except CacheUnavailable:
            raws, raw_priors = [None] * len(handles), None
        for handle_id, raw in zip(handles, raws, strict=True):
            if raw is not None:
                found[handle_id] = Cached.from_bytes(handle_id, raw, now)
        missing = [h for h in handles if h not in found]
        if missing:
            async with self._uow(space_id) as uow:
                rows = await uow.signals.rows_for(missing)
            by_handle: dict[UUID, list[SignalRow]] = defaultdict(list)
            for row in rows:
                by_handle[row.handle_id].append(row)
            for handle_id in missing:
                found[handle_id] = Cached.of(by_handle.get(handle_id, ()))
                await self._fill(space_id, handle_id, found[handle_id])
        return found, raw_priors

    def _catalog(self, space_id: UUID) -> Catalog:
        registry = self._config.space(space_id).object_types
        known = self._catalogs.get(space_id)
        if known is None or known[0] is not registry:
            known = (registry, catalog_of(self._config.space(space_id)))
            self._catalogs[space_id] = known
        return known[1]

    def now(self) -> datetime:
        return self._clock.now()

    def _on(self, space_id: UUID) -> bool:
        try:
            return Feature.SIGNALS in self._config.space(space_id).features.enabled
        except NotFound:
            return False


def interleaving_tools(config: SpaceConfig, day: date) -> dict[str, str]:
    """The tools whose two rankings an interleaving experiment compared on `day`, to its name."""
    return {
        e.tool: e.name
        for e in config.measurement.experiments
        if e.kind == "interleave"
        and e.tool is not None
        and e.starts_at.date() <= day
        and (e.ends_at is None or e.ends_at.date() >= day)
    }


def entering(entry: Entry) -> list[tuple[str, str]]:
    """The working-set reason and object of what an interaction brings: `presented`, `engaged`, `watched`."""
    data = entry.interaction
    if entry.kind == "presented":
        return [("presented", str(item["ref"])) for item in data.get("items", ()) if "ref" in item]
    if entry.kind == "engaged" and "ref" in data:
        return [("engaged", str(data["ref"]))]
    if entry.kind == "watch" and "ref" in data:
        return [("watched", str(data["ref"]))]
    return []


def seen_interests(body: Body, catalog: Catalog) -> list[SeenInterest]:
    """The body's interests, then what it was shown and nothing more, each with the values the person saw of
    its type's tracked fields."""
    out = [
        SeenInterest(ref, i.reason, i.at, dict(i.shown), i.condition)
        for ref, i in sorted(body.interests.items())
    ]
    shown = shown_fields(body)
    for ref, p in sorted(body.presented.items()):
        if ref not in body.interests:
            tracked = catalog.tracked.get(ref.split(":", 1)[0], frozenset())
            values = {k: v for k, v in shown.get(ref, {}).items() if k in tracked}
            out.append(SeenInterest(ref, "presented", p.at, values))
    return out


def _turn_of(key: str) -> str | None:
    """The turn an interaction came in, from its key (`t:<turn>:<index>`)."""
    if not key.startswith("t:"):
        return None
    return key[2:].rsplit(":", 1)[0]


def exposures_and_touches(entries: Iterable[Entry]) -> tuple[list[Shown], list[Touch]]:
    """The lists a subject was shown and the objects they engaged with, as attribution reads them."""
    shown: dict[str, Shown] = {}
    touches: list[Touch] = []
    for entry in sorted(entries, key=lambda e: (e.at, e.key)):
        data = entry.interaction
        if entry.kind == "presented" and "exposure_id" in data:
            exposure_id = str(data["exposure_id"])
            positions = {int(i["pos"]): str(i["ref"]) for i in data["items"] if "ref" in i}
            before = shown.get(exposure_id)
            if before is not None:
                positions = {**before.positions, **positions}
            at = datetime.fromisoformat(str(data["delivered_at"])) if data.get("delivered_at") else entry.at
            method = str(data.get("method") or "bridge")
            shown[exposure_id] = Shown(exposure_id, at, positions, entry.turn_id, entry.agent, method)
        elif entry.kind == "engaged" and "ref" in data:
            exposure = str(data["exposure_id"]) if data.get("exposure_id") else None
            touches.append(Touch(str(data["ref"]), entry.at, exposure, entry.turn_id, entry.agent))
    return list(shown.values()), touches


def _raw(entry: Entry, catalog: Catalog) -> dict[str, Any]:
    data = dict(entry.interaction)
    ref = str(data.get("ref") or "")
    if ref and catalog.sensitive(ref):
        return {"kind": entry.kind, "type": ref.split(":", 1)[0]}
    if entry.kind == "presented":
        data["items"] = [
            {"pos": i["pos"], "type": str(i["ref"]).split(":", 1)[0]}
            if catalog.sensitive(str(i["ref"]))
            else i
            for i in data["items"]
        ]
    return data


class ConstraintsReadBlock:
    """The constraints block as a read's `include` names it (24.2.10)."""

    name = "constraints"
    feature = Feature.SIGNALS
    surface = ReceiptSurface.CONSTRAINTS

    def __init__(self, signals: SignalsService) -> None:
        self._signals = signals

    async def read(self, caller: SourcePrincipal, target: Target | None, ask: BlockAsk) -> BlockRead:
        now = self._signals.now()
        focused = [(m.ref, r) for m in ask.memory if m.focus for r in m.instructions(now)]
        block = await self._signals.block(
            caller.space.space_id,
            target,
            for_=ask.for_,
            conversation_id=ask.conversation_id,
            withheld=ask.withheld,
            source=caller.source_id,
            gate=ask.gate,
            instructions=focused if ask.for_ == "self" else (),
            accounts=[(p, r) for p, r in ask.accounts if r.expires_at is None or r.expires_at > now]
            if ask.for_ == "self"
            else (),
            carried=ask.carried,
            hours_said=ask.hours_said,
        )
        return BlockRead(block, detail=block_detail(block))


def block_detail(block: ConstraintsBlock) -> dict[str, str]:
    """What the receipt keeps of a block: its version and counts, never a value (24.5.4)."""
    return {
        "version": block.version,
        "for": block.subject.for_,
        "hard": str(len(block.hard)),
        "soft": str(len(block.soft)),
        "attributes": str(len(block.attributes)),
    } | ({"instructions": str(len(block.instructions))} if block.instructions else {})


# The subject's package (24.5.3): signals, beneficiaries and raw interactions, readable.


async def exported_signals(
    uow: UnitOfWork, keys: KeyVault, space_id: UUID, handles: Sequence[UUID]
) -> list[Any]:
    out = []
    for row in await uow.signals.signals_for(handles):
        plain = await keys.decrypt(
            space_id, row.body_ct, body_aad(row.handle_id, row.beneficiary_id), row.dek_version
        )
        body = decode(plain)
        out.append(
            {
                "handle_id": str(row.handle_id),
                "beneficiary_id": str(row.beneficiary_id),
                "derived_at": row.derived_at.isoformat(),
                "last_activity_at": row.last_activity_at.isoformat(),
                "opted_out_at": row.opted_out_at.isoformat() if row.opted_out_at else None,
                "preferences": [_said(s) for s in body.stated],
                "attributes": [_trait(t) for t in body.traits],
                "affinity": [
                    {"attr": v.attr, "value": v.value, "exposure": round(v.e, 4), "signal": round(v.s, 4)}
                    | {"evidence": round(v.n, 4)}
                    for v in body.values.values()
                ],
                "interests": [
                    {"ref": i.ref, "reason": i.reason, "times": i.times, "at": i.at.isoformat()}
                    for i in body.interests.values()
                ],
                "presented": [
                    {"ref": p.ref, "times": p.times, "at": p.at.isoformat()} for p in body.presented.values()
                ],
                "excluded": [{"ref": ref, "at": at.isoformat()} for ref, at in body.excluded.items()],
            }
        )
    return out


def _said(s: Stated) -> dict[str, Any]:
    return (
        {
            "attr": s.attr,
            "op": s.op,
            "values": s.values,
            "strength": s.strength,
            "scope": s.scope,
            "category": s.category,
            "source": s.source,
            "said_at": s.said_at.isoformat(),
        }
        | ({"object": s.object, "text": s.text, "audience": s.audience} if s.object else {})
        | ({"about": s.about, "text": s.text, "audience": s.audience} if s.about else {})
    )


def _trait(t: Trait) -> dict[str, Any]:
    return {
        "name": t.name,
        "value": t.value,
        "system": t.system,
        "category": t.category,
        "source": t.source,
        "said_at": t.said_at.isoformat(),
    }


async def exported_beneficiaries(
    uow: UnitOfWork, keys: KeyVault, space_id: UUID, handles: Sequence[UUID]
) -> list[Any]:
    out = []
    for b in await uow.signals.beneficiaries_for(handles):
        label = None
        if b.label_ct is not None and b.dek_version is not None:
            aad = label_aad(b.handle_id, b.beneficiary_id)
            label = (await keys.decrypt(space_id, b.label_ct, aad, b.dek_version)).decode()
        out.append(
            {
                "handle_id": str(b.handle_id),
                "beneficiary_id": str(b.beneficiary_id),
                "relation": b.relation,
                "label": label,
                "status": b.status,
                "created_at": b.created_at.isoformat(),
            }
        )
    return out


async def exported_interactions(
    uow: UnitOfWork, keys: KeyVault, objects: ObjectStore, space_id: UUID, handles: Sequence[UUID]
) -> list[Any]:
    """The raw interactions kept for measurement, and those still waiting for their derivation."""
    out: list[Any] = []
    for handle_id in handles:
        for key in sorted(await objects.list_keys(raw_prefix(space_id, handle_id))):
            version, sealed = unpack_envelope(await objects.get(key))
            for item in json.loads(await keys.decrypt(space_id, sealed, object_aad(key), version)):
                out.append({"handle_id": str(handle_id), "state": "derived", **item})
        for row in (await uow.signals.snapshot(handle_id)).pending:
            plain = await keys.decrypt(space_id, row.body_ct, pending_aad(row.entry_key), row.dek_version)
            entry, _ = entry_of(plain)
            out.append(
                {
                    "handle_id": str(handle_id),
                    "state": "pending",
                    "key": entry.key,
                    "at": entry.at.isoformat(),
                    "interaction": dict(entry.interaction),
                }
            )
    return out


async def exported_suppressions(uow: UnitOfWork, handles: Sequence[UUID]) -> list[Any]:
    """The inferences the subject deleted: when, of which kind, and until when their evidence no longer
    counts; named by the key their review showed, never by their value."""
    return [
        {
            "key": s.key_hmac[:16].hex(),
            "handle_id": str(s.handle_id),
            "beneficiary_id": str(s.beneficiary_id),
            "kind": s.kind,
            "evidence_until": s.evidence_until.isoformat(),
            "created_at": s.created_at.isoformat(),
        }
        for s in await uow.signals.suppressions_for(handles)
    ]


def _body_json(body: Body) -> bytes:
    return json.dumps(to_json(body), ensure_ascii=False, separators=(",", ":")).encode()
