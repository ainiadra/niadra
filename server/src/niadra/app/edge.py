"""Edge use cases: who is calling, how fast, and replaying governance POSTs (5.1, 7.0).

The key cache is derived state: it can be lost, stale by up to its TTL, or down. Revocation is
the exception, so a revocation marker in the cache is checked before the positive entry, and the
database row is revoked as well.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable, Mapping, Sequence
from datetime import datetime
from typing import Any
from uuid import UUID

import orjson

from niadra.config.models import SourceConfig, SpaceConfig
from niadra.domain.common.errors import NotFound, RateLimited, Unauthenticated, Unavailable, WrongCell
from niadra.domain.common.space import PersonPrincipal, SourcePrincipal, SpaceRef
from niadra.domain.edge.keys import ParsedKey, SourceKeyRow, fast_hash, parse_source_key, verify_slow_hash
from niadra.domain.edge.limits import IDEMPOTENCY_TTL, IdempotencyRecord, rate_window_key, replayable
from niadra.domain.edge.tokens import verify_person_token
from niadra.domain.vocabulary import Environment, Scope
from niadra.ports.infra import Clock, ConfigProvider, KeyValueCache, SpaceDatabases
from niadra.ports.uow import UnitOfWorkFactory

log = logging.getLogger(__name__)

POSITIVE_TTL_S = 300
NEGATIVE_TTL_S = 30
REVOKED_TTL_S = 24 * 3600
DEFAULT_RATE_PER_S = 200

Compute = Callable[[], Awaitable[tuple[int, dict[str, Any]]]]


def space_ref(config: SpaceConfig) -> SpaceRef:
    return SpaceRef(config.space_id, config.tenant_id, config.region, Environment(config.environment))


def source_principal(
    config: SpaceConfig, source: SourceConfig, *, key_id: str, scopes: frozenset[Scope]
) -> SourcePrincipal:
    return SourcePrincipal(
        space=space_ref(config),
        source_id=source.source_id,
        key_id=key_id,
        scopes=scopes,
        audience=source.audience,
        verification_ceiling=source.verification_ceiling,
        purposes=source.purposes,
        vendor=source.vendor,
        confirms_before_acting=source.confirms_before_acting,
        trusted_action_ops=source.trusted_action_ops,
    )


class EdgeService:
    def __init__(
        self,
        uow: UnitOfWorkFactory,
        cache: KeyValueCache,
        config: ConfigProvider,
        clock: Clock,
        jwks: Mapping[str, bytes],
        key_pepper: bytes,
        *,
        databases: SpaceDatabases,
        rate_per_s: int = DEFAULT_RATE_PER_S,
    ) -> None:
        self._uow = uow
        self._cache = cache
        self._config = config
        self._clock = clock
        self._jwks = jwks
        self._pepper = key_pepper
        self._databases = databases
        self._rate_per_s = rate_per_s

    async def authenticate_key(self, token: str) -> SourcePrincipal:
        # Bad format is refused before any lookup, which is cheaper than a negative-cache read.
        parsed = parse_source_key(token)
        digest = fast_hash(self._pepper, parsed.key_id, parsed.secret)
        positive, negative, revoked = _cache_keys(digest, parsed.key_id)
        cached_row, unknown, is_revoked = await self._cache_get([positive, negative, revoked])
        if is_revoked is not None:
            raise Unauthenticated("revoked source key")
        if unknown is not None:
            raise Unauthenticated("unknown source key")
        row = _decode_row(cached_row) if cached_row is not None else await self._load(parsed, digest)
        if row.space_id in self._config.moved():
            # The space lives in another cell now; the SDKs retry at once on a fresh connection.
            raise WrongCell("the space moved to another cell")
        principal = await self._principal(row, parsed)
        # A space created seconds ago answers 503 with Retry-After until its database is ready; the
        # SDKs wait and send again, so an agent never sees the difference.
        await self._databases.require_serving(row.space_id)
        return principal

    async def authenticate_person(self, token: str) -> PersonPrincipal:
        claims = verify_person_token(token, self._jwks, self._clock.now())
        if claims.space_id in self._config.moved():
            raise WrongCell("the space moved to another cell")
        config = await self._known(claims.space_id)
        return PersonPrincipal(
            space=space_ref(config),
            user_id=claims.user_id,
            roles=claims.roles,
            allowed_sources=claims.sources,
            label=claims.label,
        )

    async def check_rate(self, principal: SourcePrincipal, route: str) -> None:
        await self._check_window(principal.key_id, route, "source key over its rate limit")

    async def check_webhook_rate(self, source_id: UUID, route: str) -> None:
        """Inbound webhooks carry no source key: they are counted per source, before authentication, so
        a flood is refused before any secret is fetched. The sender retries on 429 (5.1)."""
        await self._check_window(f"webhook:{source_id}", route, "source over its webhook rate limit")

    async def _check_window(self, counter: str, route: str, detail: str) -> None:
        key = rate_window_key(counter, route, self._clock.now())
        try:
            count = await self._cache.incr_window(key, 1)
        except Exception:  # fail-open: a broken limiter must not stop agents (5.1)
            log.warning("rate limiter unavailable; letting the request through")
            return
        if count > self._rate_per_s:
            raise RateLimited(retry_after_s=1, detail=detail)

    async def revoke_local(self, key_id: str) -> None:
        """The fast path of a revocation (4.7): the cache marker wins at once, the row for good."""
        await self._cache_set(_revoked_key(key_id), b"1", REVOKED_TTL_S)
        async with self._uow.cell() as uow:
            await uow.keys.revoke(key_id)

    async def revoke_source(self, space_id: UUID, source_id: UUID) -> tuple[list[str], datetime]:
        """The instant local cut of a whole source (4.7): the cache markers win at once on every
        instance, the rows keep the keys out after the markers expire, and the control plane's
        revocation makes it permanent through the snapshot."""
        async with self._uow.cell() as uow:
            key_ids = await uow.keys.source_key_ids(space_id, source_id)
        for key_id in key_ids:
            await self._cache_set(_revoked_key(key_id), b"1", REVOKED_TTL_S)
        async with self._uow.cell() as uow:
            for key_id in key_ids:
                await uow.keys.revoke(key_id)
        return key_ids, self._clock.now()

    async def mirror_keys(self, space_id: UUID, rows: Sequence[SourceKeyRow]) -> list[SourceKeyRow]:
        """Writes the snapshot's keys and returns the ones that changed here: issued, rotated or revoked
        since this cell last saw them, which the admin receipts record."""
        async with self._uow.cell() as uow:
            known = await uow.keys.key_versions(space_id, [r.key_id for r in rows])
            changed = [r for r in rows if known.get(r.key_id, 0) < r.version]
            await uow.keys.upsert_keys(space_id, rows)
        for row in rows:
            if row.revoked:
                await self._cache_set(_revoked_key(row.key_id), b"1", REVOKED_TTL_S)
        return changed

    async def idempotent(
        self, space_id: UUID, key: str, body_hash: bytes, compute: Compute
    ) -> tuple[int, dict[str, Any]]:
        """Runs `compute` once per Idempotency-Key for 24 h and replays its answer (7.0).

        Two concurrent first calls may both compute; governance operations are idempotent by their
        own `op_id`, and the loser replays the winner's stored answer.
        """
        now = self._clock.now()
        async with self._uow(space_id) as uow:
            record = await uow.edge.get_idempotency(key)
        if replayable(record, body_hash, now):
            assert record is not None
            return record.status, record.response
        status, response = await compute()
        fresh = IdempotencyRecord(key, body_hash, status, response, now)
        async with self._uow(space_id) as uow:
            if await uow.edge.save_idempotency(space_id, fresh, now - IDEMPOTENCY_TTL):
                return status, response
            winner = await uow.edge.get_idempotency(key)
        if replayable(winner, body_hash, now):
            assert winner is not None
            return winner.status, winner.response
        return status, response

    async def _load(self, parsed: ParsedKey, digest: bytes) -> SourceKeyRow:
        positive, negative, _ = _cache_keys(digest, parsed.key_id)
        async with self._uow.cell() as uow:
            row = await uow.keys.find_by_fast_hash(digest)
            known_id = row is not None or await uow.keys.key_id_exists(parsed.key_id)
        if row is None:
            if not known_id:  # only unknown ids are cached negative, never a wrong secret (5.1)
                await self._cache_set(negative, b"1", NEGATIVE_TTL_S)
            raise Unauthenticated("unknown source key")
        if row.revoked or row.key_id != parsed.key_id:
            raise Unauthenticated("revoked source key")
        # scrypt costs tens of milliseconds of CPU; keep it off the event loop.
        if not await asyncio.to_thread(verify_slow_hash, row.slow_hash, parsed.key_id, parsed.secret):
            raise Unauthenticated("unknown source key")
        await self._cache_set(positive, _encode_row(row), POSITIVE_TTL_S)
        return row

    async def _principal(self, row: SourceKeyRow, parsed: ParsedKey) -> SourcePrincipal:
        config = await self._known(row.space_id, row.source_id)
        source = config.source(row.source_id)
        assert source is not None
        if parsed.environment.value != config.environment or parsed.region != config.region:
            raise Unauthenticated("the key belongs to another environment or region")
        return source_principal(config, source, key_id=row.key_id, scopes=row.scopes)

    async def _known(self, space_id: UUID, source_id: UUID | None = None) -> SpaceConfig:
        """The space's configuration, with the source when one is named. A snapshot writes the configuration
        before the keys, but each process reads its copy on a timer: a key that works in one process may name
        a space another has not read yet. Such a miss reads the copy at once; when that read is rate limited,
        the caller waits a second (503) instead of being told the key is unknown."""
        config = self._configured(space_id, source_id)
        if config is None and space_id not in self._config.removed():
            if not await self._config.refresh_on_miss():
                raise Unavailable("the space's configuration is on its way", retry_after_s=1)
            config = self._configured(space_id, source_id)
        if config is not None:
            return config
        try:
            self._config.space(space_id)
        except NotFound:
            raise Unauthenticated("unknown space") from None
        raise Unauthenticated("the key's source is not configured")

    def _configured(self, space_id: UUID, source_id: UUID | None) -> SpaceConfig | None:
        try:
            config = self._config.space(space_id)
        except NotFound:
            return None
        return config if source_id is None or config.source(source_id) is not None else None

    async def _cache_get(self, keys: list[str]) -> list[bytes | None]:
        try:
            return await self._cache.get_many(keys)
        except Exception:  # cache down: authenticate through PostgreSQL (5.1)
            log.warning("key cache unavailable; falling back to the database")
            return [None] * len(keys)

    async def _cache_set(self, key: str, value: bytes, ttl_s: int) -> None:
        try:
            await self._cache.set(key, value, ttl_s)
        except Exception:
            log.warning("key cache unavailable; entry not stored")


def _cache_keys(digest: bytes, key_id: str) -> tuple[str, str, str]:
    return f"srckey:{digest.hex()}", f"srckey:unknown:{key_id}", _revoked_key(key_id)


def _revoked_key(key_id: str) -> str:
    return f"revoked:{key_id}"


def _encode_row(row: SourceKeyRow) -> bytes:
    return orjson.dumps(
        {
            "space_id": str(row.space_id),
            "key_id": row.key_id,
            "source_id": str(row.source_id),
            "scopes": sorted(row.scopes),
            "version": row.version,
        }
    )


def _decode_row(data: bytes) -> SourceKeyRow:
    doc = orjson.loads(data)
    return SourceKeyRow(
        space_id=UUID(doc["space_id"]),
        key_id=doc["key_id"],
        source_id=UUID(doc["source_id"]),
        fast_hash=b"",
        slow_hash="",
        scopes=frozenset(Scope(s) for s in doc["scopes"]),
        version=int(doc["version"]),
    )
