"""The agents' working state (24.3.9; the agent-state spec): written by an agent's code, never by a model.

A write is decided in the state store, atomically (the version, the declared fields, the cap), and answers
only once the new version is there, its copy for the reads is replaced (a copy is only ever replaced by a
higher version) and the broker confirmed its durable message. The batch writer in `loops` stores up to 500 of
them in one statement per space. A read takes the copy from the cache store, else the state store, else the
space's database, which it writes back to both: warm, a read runs no statement (read your writes, agent-state
6.2).

A state is kept whole, whatever its size (memory never cuts): a write is bounded only by its
request's own size limit, refused there. Fields the type marks as personal data are masked by the space's
rules before they are sealed, and a field whose own retention passed leaves the state.
"""

import dataclasses
import hashlib
import logging
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import orjson

from niadra.app.profile_view import load_profile
from niadra.app.raw_index import sealed_tokens
from niadra.config.models import SpaceConfig
from niadra.config.object_types import AgentStatePolicy, ObjectType
from niadra.contracts.common import Handle
from niadra.contracts.state import (
    AgentState,
    AgentStateMeta,
    AgentStateMetaPage,
    AgentStateReadRequest,
    AgentStateScope,
    AgentStateWrite,
)
from niadra.contracts.state import AgentStateWriteResult as WriteAnswer
from niadra.domain.common.errors import AgentStateConflict, InvalidInput, NotFound, Unavailable
from niadra.domain.common.space import Principal, SourcePrincipal
from niadra.domain.identity.handles import normalize_handle
from niadra.domain.identity.provenance import governance_view
from niadra.domain.ingest.masking import language, mask_value
from niadra.domain.privacy.erasure import ErasureScope, TargetKind
from niadra.domain.privacy.fences import erased_before
from niadra.domain.privacy.holds import Held
from niadra.domain.privacy.mentions import (
    AGENT_STATE_MENTIONS_READY,
    MENTION_PAGE,
    MentionKey,
    MentionStore,
    Redacted,
)
from niadra.domain.privacy.raw_index import body_tokens, handle_tokens, redact_value
from niadra.domain.serve.cache_keys import CacheEpoch
from niadra.domain.state.agent_state import (
    SCOPE_PURPOSE,
    WORKING_SET_KEPT,
    DurableState,
    fragment_size,
    is_removal,
    scope_text,
    state_size,
)
from niadra.domain.state.expr import duration_ms
from niadra.domain.vocabulary import HandleType
from niadra.ports.erasure import ErasureMarks
from niadra.ports.infra import Clock, ConfigProvider, KeyValueCache, KeyVault
from niadra.ports.state import AgentStateLog, AgentStateStore, HeldField, HeldState, WriteResult
from niadra.ports.uow import UnitOfWorkFactory

log = logging.getLogger(__name__)

STORE_TTL_S = 3600
"""How long the state store keeps a state after its last write: it holds the states in transit (06, 15.3;
its memory is small and never evicts), the copy in the cache store and the database keep them longer, and the
next write or read brings back one the store let go."""
COPY_TTL_S = 24 * 3600
PUBLISH_TIMEOUT_S = 0.2
BATCH = 500
"""States per batch of the writer: one statement per space per batch (06, 15.6)."""
FENCE_TTL_S = 3600
"""How long the writer skips what an erasure reached: far past the queue's linger and its redeliveries, which
last as long as the database is down, and the erasure itself needs the database."""
_CACHE_ERRORS = (ConnectionError, TimeoutError, OSError)
_DEFAULT_POLICY = AgentStatePolicy()
_SUBJECT_TYPES = {"phone": HandleType.PHONE_E164, "email": HandleType.EMAIL}


@dataclass(frozen=True, slots=True)
class Names:
    """Where one state lives: the keyed digest of its scope, its key in the state store and its copy's."""

    scope_key: bytes
    store: str
    copy: str
    copy_version: str


class AgentStateService:
    def __init__(
        self,
        uow: UnitOfWorkFactory,
        store: AgentStateStore,
        cache: KeyValueCache,
        durable: AgentStateLog,
        keys: KeyVault,
        clock: Clock,
        config: ConfigProvider,
        *,
        marks: ErasureMarks,
        epoch: CacheEpoch | None = None,
    ) -> None:
        """`marks` keeps what erasures reached and when, shared with the other modules' fences
        (`ports.erasure`)."""
        self._uow = uow
        self._store = store
        self._erasures = marks
        self._cache = cache
        self._durable = durable
        self._keys = keys
        self._clock = clock
        self._config = config
        self._epoch = epoch or CacheEpoch()
        self._marks: dict[UUID, tuple[bytes, ...]] = {}
        """What the fence of each erasure in this process marked, for its purge: the handles may be gone."""

    # Reads and writes (7.7)

    async def read(self, caller: SourcePrincipal, request: AgentStateReadRequest) -> AgentState:
        space_id = caller.space.space_id
        space = self._config.space(space_id)
        declared = agent_type(space, request.agent)
        names = await self._names(space_id, request.scope, caller.source_id, request.agent)
        state = await self._held(space_id, names, caller.source_id, request.agent)
        fields = _current(state, declared, self._clock.now())
        return AgentState(
            body=await self._opened(space_id, names, fields),
            version=state.version,
            updated_at=_instant(state.updated_ms) if state.version else None,
        )

    async def write(self, caller: SourcePrincipal, request: AgentStateWrite) -> WriteAnswer:
        space_id = caller.space.space_id
        space = self._config.space(space_id)
        declared = agent_type(space, request.agent)
        policy = declared.agent_state if declared and declared.agent_state else _DEFAULT_POLICY
        if policy.write not in ("both", request.mode):
            raise InvalidInput(f"this agent's state is written by {policy.write} only")
        names = await self._names(space_id, request.scope, caller.source_id, request.agent)
        kind, source, agent = request.scope.kind, caller.source_id, request.agent
        undeclared = [name for name in request.body if declared is None or name not in declared.fields]
        if undeclared:
            # The version is checked first (agent-state 5.3), then the declared fields.
            current = await self._held(space_id, names, source, agent)
            if request.if_version is not None and request.if_version != current.version:
                raise AgentStateConflict("the state moved since the version it read")
            return WriteAnswer(stored=False, version=current.version, reason="not_declared_field")
        now = self._clock.now()
        now_ms = _ms(now)
        changes = await self._sealed(space_id, space, names, declared, request.body, now_ms)
        if request.mode == "merge_by_key" and declared is not None:
            # A field whose own retention passed leaves the state with this write.
            held = await self._held(space_id, names, source, agent)
            for name in set(held.fields) - set(_current(held, declared, now)):
                changes.setdefault(name, None)
        outcome = await self._store.write(
            names.store,
            changes,
            replace=request.mode == "cas",
            if_version=request.if_version,
            now_ms=now_ms,
            ttl_s=STORE_TTL_S,
        )
        if outcome.result is WriteResult.MISSING:
            await self._restore(space_id, names, source, agent)
            outcome = await self._store.write(
                names.store,
                changes,
                replace=request.mode == "cas",
                if_version=request.if_version,
                now_ms=now_ms,
                ttl_s=STORE_TTL_S,
            )
        if outcome.result is WriteResult.CONFLICT:
            raise AgentStateConflict("the state moved since the version it read")
        if outcome.result is not WriteResult.STORED or outcome.state is None:
            # The store lost the state between its restore and the write: the caller writes again.
            raise Unavailable("the state store did not keep the state while it was written")
        await self._copy(names, outcome.state)
        subject = await self._subject_key(space_id, request.scope, request.subject)
        named = await self._persist(
            space_id, names, kind, source, agent, declared, outcome.state, now, subject
        )
        await self._track(space_id, names, subject, named)
        return WriteAnswer(stored=True, version=outcome.version)

    # The batch writer (loops)

    async def run_writer(self) -> None:
        """Drains the confirmed queue into the spaces' databases: one statement per space per batch."""
        async for batch in self._durable.batches(BATCH):
            try:
                await self._write_batch(batch.states)
            except Exception:
                log.exception("agent states not stored; the batch goes back to the queue")
                await batch.retry_later()
            else:
                await batch.ack()

    async def _write_batch(self, states: list[DurableState]) -> None:
        by_space: dict[UUID, list[DurableState]] = {}
        for state in states:
            by_space.setdefault(state.space_id, []).append(state)
        for space_id, own in by_space.items():
            kept = await self._unfenced(space_id, own)
            if kept:
                async with self._uow(space_id) as uow:
                    await uow.agent_state.upsert(kept)

    async def _unfenced(self, space_id: UUID, states: list[DurableState]) -> list[DurableState]:
        """The states no erasure reached: one that ran while a state waited in the queue marked its customer
        or its scope, and a state written before it is dropped instead of bringing the erased data back (5.5).
        A state written after it is new activity. Another customer's state written before an erasure, that
        names the erased person, is stored with them redacted out, as `redact_named` leaves the others."""
        marks = [mark for s in states for mark in (s.scope_key, s.subject_key, *s.named) if mark is not None]
        fenced = await self._erasures.fenced(space_id, marks)
        kept = [
            s
            for s in states
            if not erased_before(s.updated_at, s.scope_key, fenced)
            and not erased_before(s.updated_at, s.subject_key, fenced)
        ]
        return [await self._without_erased(space_id, s, fenced) for s in kept]

    async def _without_erased(
        self, space_id: UUID, state: DurableState, fenced: Mapping[bytes, datetime]
    ) -> DurableState:
        erased = {t for t in state.named if erased_before(state.updated_at, t, fenced)}
        if not erased:
            return state
        aad = f"agent_state:{state.row}:body_ct".encode()
        document = orjson.loads(await self._keys.decrypt(space_id, state.body_ct, aad, state.dek_version))
        plain = set()
        for token in body_tokens(orjson.dumps(document["fields"])):
            if await sealed_tokens(self._keys, space_id, frozenset([token])) & erased:
                plain.add(token)
        return await self._redacted(space_id, state, document, frozenset(plain)) or state

    async def _redacted(
        self, space_id: UUID, state: DurableState, document: dict[str, Any], plain: frozenset[str]
    ) -> DurableState | None:
        """The state sealed again with `plain` redacted out of its fields; None when it names none of them."""
        fields, named = redact_value(document["fields"], plain)
        if not named:
            return None
        body = orjson.dumps({**document, "fields": fields}, option=orjson.OPT_SORT_KEYS)
        aad = f"agent_state:{state.row}:body_ct".encode()
        sealed, dek = await self._keys.encrypt(space_id, body, aad)
        tokens = await sealed_tokens(self._keys, space_id, body_tokens(orjson.dumps(fields)))
        return dataclasses.replace(state, body_ct=sealed, dek_version=dek, named=tokens)

    # Erasure (ports.erasure)

    async def fence(self, space_id: UUID, scope: ErasureScope) -> None:
        """Before the cascade: the writer skips the target's states from now on, and the copies of the states
        the database holds and of the ones on their way leave the stores."""
        marks = await self._marks_of(space_id, scope)
        self._marks[scope.request_id] = marks
        # What the person is named by in others' states, for the writer's states still on their way.
        named = await self._named_marks(space_id, scope)
        await self._erasures.fence(space_id, [*marks, *named], self._clock.now(), FENCE_TTL_S)
        async with self._uow(space_id) as uow:
            stored = await uow.agent_state.for_handles(list(scope.handle_ids)) if scope.handle_ids else []
            if scope.kind is TargetKind.CONVERSATION:
                stored += await uow.agent_state.for_scopes(marks)
        tags = {self._tag(space_id, s.scope_key, s.source_id, s.agent) for s in stored}
        await self._drop(tags | set(await self._store.tracked(space_id, marks)))

    async def purge(self, space_id: UUID, scope: ErasureScope) -> None:
        """After the cascade: what was stored while it ran, by the writer that checked before the fence or by
        a write the store still holds, leaves too."""
        marks = self._marks.pop(scope.request_id, None) or await self._marks_of(space_id, scope)
        await self._drop(set(await self._store.tracked(space_id, marks)))
        while True:
            async with self._uow(space_id) as uow:
                gone = await uow.agent_state.erase_marked(marks, limit=BATCH)
            if gone < BATCH:
                return

    async def _marks_of(self, space_id: UUID, scope: ErasureScope) -> tuple[bytes, ...]:
        """A conversation's keyed scope, or the keyed handles of the customer: what the target's states
        carry."""
        if scope.kind is TargetKind.CONVERSATION:
            if scope.conversation_id is None:
                return ()
            text = scope_text("conversation", scope.conversation_id).encode()
            return (await self._keys.hmac(space_id, SCOPE_PURPOSE, text),)
        async with self._uow(space_id) as uow:
            handles = await uow.identity.get_handles(list(scope.handle_ids))
        return tuple(
            [
                await self._keys.hmac(space_id, f"handle:{h.type.value}", h.value.encode())
                for h in handles
                if h.value
            ]
        )

    async def _named_marks(self, space_id: UUID, scope: ErasureScope) -> list[bytes]:
        if scope.kind is TargetKind.CONVERSATION or not scope.handle_ids:
            return []
        async with self._uow(space_id) as uow:
            handles = await uow.identity.get_handles(list(scope.handle_ids))
        return sorted(
            await sealed_tokens(self._keys, space_id, handle_tokens((h.type, h.value) for h in handles))
        )

    async def redact_named(
        self, space_id: UUID, scope: ErasureScope, plain: frozenset[str], tokens: frozenset[bytes], held: Held
    ) -> Redacted:
        """`NamedInText`: others' working states that name the target keep, with the target's identifiers
        replaced by `ERASED`, found by the index of mentions a page at a time; their copies in the state and
        cache stores go, so the next read takes them from the database. A held customer's state keeps as it
        is, and the target's own go with the cascade. States stored before the index existed are read into it
        once."""
        await self._index_stored(space_id)
        # States written lately, by their identifiers: the stores may hold a version the database does not
        # have yet, so their copies are redacted in place instead of let go (a read would take the older row).
        live = set(await self._store.tracked(space_id, sorted(tokens)))
        kept_as_is: set[str] = set()
        done = Redacted()
        after: MentionKey | None = None
        while True:
            tags: set[str] = set()
            async with self._uow(space_id) as uow:
                keys = await uow.mentions.naming(
                    sorted(tokens), plain, stores=[MentionStore.STATE], after=after, limit=MENTION_PAGE
                )
                index: list[tuple[MentionKey, frozenset[bytes]]] = []
                for key in keys:
                    scope_hex, source, agent = key.row.split("/", 2)
                    state = await uow.agent_state.get(bytes.fromhex(scope_hex), UUID(source), agent)
                    if state is None:
                        index.append((key, frozenset()))
                        continue
                    owner = state.origin_handle_id
                    if owner is not None and owner in scope.handle_ids:
                        continue
                    aad = f"agent_state:{state.row}:body_ct".encode()
                    document = orjson.loads(
                        await self._keys.decrypt(space_id, state.body_ct, aad, state.dek_version)
                    )
                    fixed = await self._redacted(space_id, state, document, plain)
                    if fixed is None:
                        continue
                    tag = self._tag(space_id, state.scope_key, state.source_id, state.agent)
                    if owner is not None and owner in held.handles:
                        done.held += 1
                        kept_as_is.add(tag)
                        continue
                    if await uow.agent_state.replace_body(fixed):
                        tags.add(tag)
                        index.append((key, fixed.named))
                        done.rows += 1
                        if owner is not None:
                            done.handles.add(owner)
                await uow.mentions.index(index, replace=True)
            await self._drop(tags - live)
            if len(keys) < MENTION_PAGE:
                break
            after = keys[-1]
        for tag in sorted(live - kept_as_is):
            await self._redact_copies(space_id, tag, plain)
        return done

    async def _redact_copies(self, space_id: UUID, tag: str, plain: frozenset[str]) -> None:
        """The state store's and the cache's copies of one state with `plain` redacted out, at the version
        they hold: the next read serves no erased identifier, and the writer stores the queued version
        redacted too (`_unfenced`). A state that keeps moving while it is redacted lets its copies go."""
        scope_hex = tag.split("{", 1)[1].split("}", 1)[0]
        epoch = self._epoch.value
        names = Names(bytes.fromhex(scope_hex), f"ast:{tag}", f"asc:{epoch}:{tag}", f"ascv:{epoch}:{tag}")
        for _ in range(3):
            held = await self._store.load(names.store)
            if held is None:
                break
            fixed = await self._redacted_held(space_id, names, held, plain)
            if fixed is None or await self._store.rewrite(names.store, fixed, STORE_TTL_S):
                break
        else:
            await self._drop({tag})
            return
        try:
            raw = await self._cache.get(names.copy)
        except _CACHE_ERRORS:
            raw = None
        if raw is not None and held is None:
            # The state store let it go: the copy, redacted, becomes its state again.
            copy = _from_copy(raw)
            await self._store.restore(
                names.store, await self._redacted_held(space_id, names, copy, plain) or copy, STORE_TTL_S
            )
        if raw is not None:
            try:
                await self._cache.delete([names.copy, names.copy_version])
            except _CACHE_ERRORS:
                log.warning("an erased identifier's agent state copy not removed; it expires with its TTL")

    async def _redacted_held(
        self, space_id: UUID, names: Names, state: HeldState, plain: frozenset[str]
    ) -> HeldState | None:
        """`state` with `plain` redacted out of its fields, each changed field sealed again; None when it
        names none of them."""
        opened = await self._opened(space_id, names, state.fields)
        fields, named = redact_value(opened, plain)
        if not named:
            return None
        sealed_fields: dict[str, HeldField] = {}
        for name, value in fields.items():
            if value == opened[name]:
                sealed_fields[name] = state.fields[name]
                continue
            sealed, dek = await self._keys.encrypt(space_id, orjson.dumps(value), _aad(names, name))
            sealed_fields[name] = HeldField(
                dek.to_bytes(4, "big") + sealed, fragment_size(name, value), state.fields[name].written_ms
            )
        return HeldState(state.version, state.updated_ms, sealed_fields)

    async def _index_stored(self, space_id: UUID) -> None:
        """Reads, once, the states stored before the index existed into it, by the first erasure."""
        async with self._uow(space_id) as uow:
            if await uow.mentions.ready(AGENT_STATE_MENTIONS_READY):
                return
        after: tuple[bytes, UUID, str] | None = None
        while True:
            async with self._uow(space_id) as uow:
                page = await uow.agent_state.states_page(after, BATCH)
                entries = []
                for state in page:
                    aad = f"agent_state:{state.row}:body_ct".encode()
                    document = orjson.loads(
                        await self._keys.decrypt(space_id, state.body_ct, aad, state.dek_version)
                    )
                    named = await sealed_tokens(
                        self._keys, space_id, body_tokens(orjson.dumps(document["fields"]))
                    )
                    entries.append((MentionKey(MentionStore.STATE, state.row), named))
                await uow.mentions.index([(k, t) for k, t in entries if t])
            if len(page) < BATCH:
                break
            after = (page[-1].scope_key, page[-1].source_id, page[-1].agent)
        async with self._uow(space_id) as uow:
            await uow.mentions.mark_ready(AGENT_STATE_MENTIONS_READY)

    async def _drop(self, tags: set[str]) -> None:
        if not tags:
            return
        epoch = self._epoch.value
        await self._store.forget([f"ast:{tag}" for tag in sorted(tags)])
        copies = [key for tag in sorted(tags) for key in (f"asc:{epoch}:{tag}", f"ascv:{epoch}:{tag}")]
        try:
            await self._cache.delete(copies)
        except _CACHE_ERRORS:
            log.warning(
                "erased agent states' copies not removed; reads refuse them once the database has none"
            )

    async def _track(
        self, space_id: UUID, names: Names, subject: bytes | None, named: frozenset[bytes]
    ) -> None:
        """Remembers the state under its scope, its customer and every identifier it writes: an erasure finds
        by them the states and copies the database does not hold yet."""
        marks = [names.scope_key, *([subject] if subject else []), *sorted(named)]
        try:
            await self._store.track(space_id, marks, names.store.removeprefix("ast:"), COPY_TTL_S)
        except _CACHE_ERRORS:
            log.warning("agent state not tracked for erasure; an erasure finds it once it is stored")

    async def profile_states(self, principal: Principal, profile_id: UUID) -> AgentStateMetaPage:
        """The Console's view of a customer's working states (24.3.9): size, version and dates, never the
        content, newest first."""
        async with self._uow(principal.space.space_id) as uow:
            profile = await load_profile(uow, profile_id)
            states = await uow.agent_state.for_handles(list(governance_view(principal, profile).handle_ids))
        return AgentStateMetaPage(
            items=[
                AgentStateMeta(
                    agent=s.agent,
                    scope_kind=s.scope_kind,
                    source_id=str(s.source_id),
                    bytes=s.body_bytes,
                    version=s.version,
                    updated_at=s.updated_at,
                    valid_until=s.valid_until,
                    schema_version=s.schema_version,
                )
                for s in states
            ]
        )

    async def sweep(self, space_id: UUID, held: Held | None = None) -> int:
        """A per-space loop step: states past their retention leave the database, a state finds the handle of
        its customer once the handle exists, and a field whose own retention passed leaves the state it
        belongs to (agent-state 7.1). The states of a customer under a legal hold stay whole (24.5.1). The
        state store and the copies let them go by their own expiry, and a read never serves a field past
        its retention."""
        try:
            space = self._config.space(space_id)
        except NotFound:
            return 0
        now = self._clock.now()
        keep = frozenset(held.handles) if held else frozenset()
        async with self._uow(space_id) as uow:
            removed = await uow.agent_state.expire(now, limit=BATCH, keep=keep)
            # A state written before its customer's handle existed finds its owner once the handle does, so
            # erasing the customer and their package reach it.
            owned = await uow.agent_state.resolve_owners(limit=BATCH)
        shortest = _shortest_field_retention(space)
        if shortest is None:
            return removed + owned
        async with self._uow(space_id) as uow:
            page = await uow.agent_state.older_than(now - shortest, limit=BATCH, keep=keep)
        rewritten = [await self._without_expired(space, state, now) for state in page]
        changed = [state for state in rewritten if state is not None]
        if changed:
            async with self._uow(space_id) as uow:
                for state in changed:
                    await uow.agent_state.replace_body(state)
        return removed + owned + len(changed)

    # Where a state lives, and its copies

    async def _names(self, space_id: UUID, scope: AgentStateScope, source_id: UUID, agent: str) -> Names:
        scope_key = await self._keys.hmac(space_id, SCOPE_PURPOSE, scope_text(scope.kind, scope.id).encode())
        tag = self._tag(space_id, scope_key, source_id, agent)
        epoch = self._epoch.value
        return Names(scope_key, f"ast:{tag}", f"asc:{epoch}:{tag}", f"ascv:{epoch}:{tag}")

    @staticmethod
    def _tag(space_id: UUID, scope_key: bytes, source_id: UUID, agent: str) -> str:
        return (
            f"{space_id}:{{{scope_key.hex()}}}:{source_id}:{hashlib.sha256(agent.encode()).hexdigest()[:16]}"
        )

    async def _held(self, space_id: UUID, names: Names, source_id: UUID, agent: str) -> HeldState:
        """The state as the reads serve it: the cache's copy, the state store's, or the database's written
        back to both. The absence of a state is kept too, as version 0."""
        try:
            raw = await self._cache.get(names.copy)
        except _CACHE_ERRORS:
            raw = None
        if raw is not None:
            return _from_copy(raw)
        held = await self._store.load(names.store)
        if held is None:
            async with self._uow(space_id) as uow:
                stored = await uow.agent_state.get(names.scope_key, source_id, agent)
            held = await self._from_durable(space_id, names, stored) if stored else HeldState(0, 0)
            await self._store.restore(names.store, held, STORE_TTL_S)
        await self._copy(names, held)
        return held

    async def _restore(self, space_id: UUID, names: Names, source_id: UUID, agent: str) -> None:
        """Puts back in the state store a state it let go: from the copy the reads use, else the database."""
        try:
            raw = await self._cache.get(names.copy)
        except _CACHE_ERRORS:
            raw = None
        if raw is not None:
            held = _from_copy(raw)
        else:
            async with self._uow(space_id) as uow:
                stored = await uow.agent_state.get(names.scope_key, source_id, agent)
            held = await self._from_durable(space_id, names, stored) if stored else HeldState(0, 0)
        await self._store.restore(names.store, held, STORE_TTL_S)

    async def _copy(self, names: Names, state: HeldState) -> None:
        try:
            await self._cache.bump_generation(names.copy_version, state.version, COPY_TTL_S)
            await self._cache.put_if_generation(
                names.copy, names.copy_version, _to_copy(state), state.version, COPY_TTL_S
            )
        except _CACHE_ERRORS:
            # The copy that stays is older than the state store's: reads go past it once it expires.
            log.warning("agent state copy not written")

    # Sealing

    async def _sealed(
        self,
        space_id: UUID,
        space: SpaceConfig,
        names: Names,
        declared: ObjectType | None,
        body: Mapping[str, Any],
        now_ms: int,
    ) -> dict[str, HeldField | None]:
        lang, types = language(space.settings.locale), space.masking_types()
        changes: dict[str, HeldField | None] = {}
        for name, value in body.items():
            if is_removal(value):
                changes[name] = None
                continue
            spec = declared.fields.get(name) if declared else None
            if spec is not None and spec.pii:
                value, _ = mask_value(name, value, lang, types)
            sealed, dek = await self._keys.encrypt(space_id, orjson.dumps(value), _aad(names, name))
            changes[name] = HeldField(dek.to_bytes(4, "big") + sealed, fragment_size(name, value), now_ms)
        return changes

    async def _opened(self, space_id: UUID, names: Names, fields: Mapping[str, HeldField]) -> dict[str, Any]:
        opened: dict[str, Any] = {}
        for name, held in sorted(fields.items()):
            dek, sealed = int.from_bytes(held.sealed[:4], "big"), held.sealed[4:]
            opened[name] = orjson.loads(await self._keys.decrypt(space_id, sealed, _aad(names, name), dek))
        return opened

    async def _persist(
        self,
        space_id: UUID,
        names: Names,
        kind: str,
        source_id: UUID,
        agent: str,
        declared: ObjectType | None,
        state: HeldState,
        now: datetime,
        subject_key: bytes | None,
    ) -> frozenset[bytes]:
        """Queues the state for the writer; the keyed tokens of the identifiers its fields write, by which an
        erasure finds it before the database holds it."""
        body = await self._opened(space_id, names, state.fields)
        written = {name: f.written_ms for name, f in state.fields.items()}
        document = orjson.dumps({"fields": body, "written": written}, option=orjson.OPT_SORT_KEYS)
        row = _row(names, source_id, agent)
        sealed, dek = await self._keys.encrypt(space_id, document, f"agent_state:{row}:body_ct".encode())
        named = await sealed_tokens(self._keys, space_id, body_tokens(orjson.dumps(body)))
        durable = DurableState(
            space_id=space_id,
            scope_key=names.scope_key,
            scope_kind=kind,
            source_id=source_id,
            agent=agent,
            version=state.version,
            body_ct=sealed,
            dek_version=dek,
            body_bytes=state_size(f.size for f in state.fields.values()),
            updated_at=now,
            valid_until=None if (kept := _kept(declared)) is None else now + kept,
            schema_version=declared.version if declared else None,
            subject_key=subject_key,
            named=named,
        )
        if await self._durable.publish([durable], PUBLISH_TIMEOUT_S):
            return named
        # The queue did not confirm in time: the write is stored now, in one statement.
        log.warning("agent state queue unconfirmed; the state is written to the database directly")
        if await self._unfenced(space_id, [durable]):
            async with self._uow(space_id) as uow:
                await uow.agent_state.upsert([durable])
        return named

    async def _from_durable(self, space_id: UUID, names: Names, stored: DurableState) -> HeldState:
        """A state the stores lost, from the database, sealed field by field again for them."""
        row = _row(names, stored.source_id, stored.agent)
        document = orjson.loads(
            await self._keys.decrypt(
                space_id, stored.body_ct, f"agent_state:{row}:body_ct".encode(), stored.dek_version
            )
        )
        fields: dict[str, HeldField] = {}
        for name, value in document["fields"].items():
            sealed, dek = await self._keys.encrypt(space_id, orjson.dumps(value), _aad(names, name))
            written = int(document["written"].get(name, _ms(stored.updated_at)))
            fields[name] = HeldField(dek.to_bytes(4, "big") + sealed, fragment_size(name, value), written)
        return HeldState(stored.version, _ms(stored.updated_at), fields)

    async def _without_expired(
        self, space: SpaceConfig, state: DurableState, now: datetime
    ) -> DurableState | None:
        declared = agent_type(space, state.agent)
        if declared is None:
            return None
        row = f"{state.scope_key.hex()}/{state.source_id}/{state.agent}"
        aad = f"agent_state:{row}:body_ct".encode()
        document = orjson.loads(
            await self._keys.decrypt(space.space_id, state.body_ct, aad, state.dek_version)
        )
        written = {name: _instant(ms) for name, ms in document["written"].items()}
        gone = _expired(declared, written, now)
        if not gone:
            return None
        fields = {k: v for k, v in document["fields"].items() if k not in gone}
        kept = {k: v for k, v in document["written"].items() if k not in gone}
        sealed, dek = await self._keys.encrypt(
            space.space_id,
            orjson.dumps({"fields": fields, "written": kept}, option=orjson.OPT_SORT_KEYS),
            aad,
        )
        size = state_size(fragment_size(k, v) for k, v in fields.items())
        return DurableState(
            space_id=state.space_id,
            scope_key=state.scope_key,
            scope_kind=state.scope_kind,
            source_id=state.source_id,
            agent=state.agent,
            version=state.version,
            body_ct=sealed,
            dek_version=dek,
            body_bytes=size,
            updated_at=state.updated_at,
            valid_until=state.valid_until,
        )

    async def _subject_key(
        self, space_id: UUID, scope: AgentStateScope, subject: Handle | None
    ) -> bytes | None:
        """The customer the state is about, as the space's keyed digest of their handle (the one `handles`
        keeps): named by the write, or by a scope of kind `subject`."""
        if subject is None and scope.kind == "subject":
            prefix, _, value = scope.id.partition(":")
            if prefix in _SUBJECT_TYPES and value:
                subject = Handle(type=_SUBJECT_TYPES[prefix], value=value)
        if subject is None:
            return None
        try:
            key = normalize_handle(subject.type, subject.value, subject.scope, subject.subject_kind).key
        except InvalidInput:
            return None
        return await self._keys.hmac(space_id, f"handle:{key.type.value}", key.value.encode())


def agent_type(space: SpaceConfig, agent: str) -> ObjectType | None:
    """The declared schema of an agent's state: the agent type named as the agent, or the space's only one."""
    types = [t for t in space.object_types.types if t.ownership == "agent"]
    named = next((t for t in types if t.type == agent), None)
    return named or (types[0] if len(types) == 1 else None)


def _current(state: HeldState, declared: ObjectType | None, now: datetime) -> dict[str, HeldField]:
    if declared is None:
        return dict(state.fields)
    gone = _expired(declared, {name: _instant(f.written_ms) for name, f in state.fields.items()}, now)
    return {name: f for name, f in state.fields.items() if name not in gone}


def _expired(declared: ObjectType, written: Mapping[str, datetime], now: datetime) -> set[str]:
    kept = {
        name: timedelta(milliseconds=duration_ms(value))
        for name, value in declared.retention.items()
        if name in declared.fields and value != "while_in_working_set"
    }
    return {name for name, at in written.items() if name in kept and at + kept[name] <= now}


def _kept(declared: ObjectType | None) -> timedelta | None:
    """How long a state stays after its last write, as its type names it; None when it names nothing."""
    value = declared.retention.get("state") if declared else None
    if value is None:
        return None
    if value == "while_in_working_set":
        return WORKING_SET_KEPT
    return timedelta(milliseconds=duration_ms(value))


def _shortest_field_retention(space: SpaceConfig) -> timedelta | None:
    found = [
        timedelta(milliseconds=duration_ms(value))
        for declared in space.object_types.types
        if declared.ownership == "agent"
        for name, value in declared.retention.items()
        if name in declared.fields and value != "while_in_working_set"
    ]
    return min(found) if found else None


def _aad(names: Names, field: str) -> bytes:
    return f"{names.store}:{field}".encode()


def _row(names: Names, source_id: UUID, agent: str) -> str:
    return f"{names.scope_key.hex()}/{source_id}/{agent}"


def _ms(moment: datetime) -> int:
    return int(moment.timestamp() * 1000)


def _instant(ms: int) -> datetime:
    return datetime.fromtimestamp(ms / 1000, tz=UTC)


def _to_copy(state: HeldState) -> bytes:
    fields = {name: [f.sealed.hex(), f.size, f.written_ms] for name, f in state.fields.items()}
    return orjson.dumps({"v": state.version, "t": state.updated_ms, "f": fields})


def _from_copy(raw: bytes) -> HeldState:
    body = orjson.loads(raw)
    fields = {
        name: HeldField(bytes.fromhex(sealed), int(size), int(written))
        for name, (sealed, size, written) in body["f"].items()
    }
    return HeldState(int(body["v"]), int(body["t"]), fields)
