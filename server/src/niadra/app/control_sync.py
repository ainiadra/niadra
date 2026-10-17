"""The cell's side of distribution (4.7): pull the signed snapshot by long poll, and the signed
revocation feed every 2 s, and hand them to the config and edge services; push the hourly usage of
its spaces every 15 minutes.

A failure keeps the last good state: the cell never stops serving because the control plane is away.
Keys rotated with a grace period carry `expires_at`; the snapshot does not change when it passes, so
this loop revokes them locally at that moment.
"""

import asyncio
import base64
import contextlib
import logging
from collections.abc import Awaitable, Callable, Sequence
from datetime import datetime, timedelta
from typing import Any, Protocol
from uuid import UUID

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from niadra.config.validation import AccessFeed, RevocationFeed
from niadra.domain.common.errors import InvalidInput, Unavailable
from niadra.domain.config import AppliedSnapshot
from niadra.domain.config.signing import SignedSnapshot, canonical_json, verify_signed
from niadra.domain.edge.keys import SourceKeyRow
from niadra.domain.metering import UsageRow, hour_floor
from niadra.domain.vocabulary import Scope
from niadra.ports.control import ControlPlaneClient
from niadra.ports.infra import Clock

log = logging.getLogger(__name__)

MAX_BACKOFF_S = 60.0
USAGE_EVERY_S = 900.0
USAGE_LOOKBACK = timedelta(hours=48)
"""Hours sent again at each push: an hour the cell rolled up late, or one sent while the control plane
was away, still reaches it."""
USAGE_BATCH = 5000
ACCESS_EVERY_S = 15.0


class SnapshotApplier(Protocol):
    """What `ConfigService` offers."""

    async def apply(self, snapshot: SignedSnapshot) -> AppliedSnapshot: ...

    async def active_version(self) -> int | None: ...

    def verify_revocations(self, body: bytes, signature: bytes) -> RevocationFeed: ...


class KeyMirror(Protocol):
    """What `EdgeService` offers."""

    async def mirror_keys(self, space_id: UUID, rows: Sequence[SourceKeyRow]) -> list[SourceKeyRow]: ...

    async def revoke_local(self, key_id: str) -> None: ...


class AccessFeedReader(Protocol):
    """What `ConfigService` offers for the access feed."""

    def verify_access_feed(self, body: bytes, signature: bytes) -> AccessFeed: ...

    async def access_cursor(self) -> datetime | None: ...

    async def save_access_cursor(self, at: datetime) -> None: ...


class UsageSource(Protocol):
    """What `MeteringService` offers: a space's hourly usage, summed over its sources."""

    async def usage(self, space_id: UUID, since: datetime, until: datetime) -> list[UsageRow]: ...


class ControlSync:
    def __init__(
        self,
        client: ControlPlaneClient,
        config: SnapshotApplier,
        edge: KeyMirror,
        signing_keys: Sequence[Ed25519PublicKey],
        clock: Clock,
        cell_id: str,
        *,
        on_applied: Callable[[], Awaitable[None]] | None = None,
        usage: UsageSource | None = None,
        spaces: Callable[[], Sequence[UUID]] = list,
        audit: Callable[[UUID, str, str], Awaitable[None]] | None = None,
        access: AccessFeedReader | None = None,
        space_tenants: Callable[[], Sequence[tuple[UUID, UUID]]] = list,
    ) -> None:
        self._audit = audit
        self._access = access
        self._space_tenants = space_tenants
        self._client = client
        self._config = config
        self._edge = edge
        self._keys = list(signing_keys)
        self._clock = clock
        self._cell_id = cell_id
        self._usage = usage
        self._spaces = spaces
        # Run after each applied snapshot: `loops` reconciles the space databases at once.
        self.on_applied = on_applied
        self._revocation_seq = 0
        self._expiring: dict[str, datetime] = {}

    async def pull_snapshot(self, wait_s: float = 25.0) -> int | None:
        """Applies the next snapshot if there is one; returns its version, or None when nothing changed."""
        active = await self._config.active_version()
        body = await self._client.snapshot(self._cell_id, active, wait_s)
        if body is None or (active is not None and body["manifest"]["version"] <= active):
            return None
        keys = verify_signed(canonical_json(body["keys"]), _unb64(body["keys_signature"]), self._keys)
        applied = await self._config.apply(
            SignedSnapshot(
                manifest=canonical_json(body["manifest"]),
                signature=_unb64(body["signature"]),
                documents={digest: canonical_json(doc) for digest, doc in body["documents"].items()},
            )
        )
        await self._mirror_keys(keys)
        if self._audit is not None:
            # What the control plane changed reaches each space's own receipt chain; the
            # control plane keeps who proposed and approved it, by snapshot version.
            for space_id, digest in applied.changed:
                await self._audit(space_id, "config.applied", f"snapshot:{applied.version}:{digest[:16]}")
        if self.on_applied is not None:
            await self.on_applied()
        return applied.version

    async def pull_revocations(self) -> int:
        """Applies revocations newer than the last seen; returns how many keys were cut."""
        body = await self._client.revocations(self._cell_id, self._revocation_seq)
        feed = self._config.verify_revocations(canonical_json(body["body"]), _unb64(body["signature"]))
        for key in feed.keys:
            await self._edge.revoke_local(key.key_id)
        self._revocation_seq = max(self._revocation_seq, feed.seq)
        now = self._clock.now()
        expired = [key_id for key_id, at in self._expiring.items() if at <= now]
        for key_id in expired:
            await self._edge.revoke_local(key_id)
            del self._expiring[key_id]
        return len(feed.keys) + len(expired)

    async def pull_access_changes(self) -> int:
        """Changes to people's roles and to the tenant's SSO reach the receipt chain of each of the tenant's
        spaces here, once each: the cursor is saved in the cell database after they are written.
        Returns how many changes were recorded."""
        if self._access is None or self._audit is None:
            return 0
        after = await self._access.access_cursor()
        body = await self._client.access_changes(self._cell_id, after)
        feed = self._access.verify_access_feed(canonical_json(body["body"]), _unb64(body["signature"]))
        spaces: dict[UUID, list[UUID]] = {}
        for space_id, tenant_id in self._space_tenants():
            spaces.setdefault(tenant_id, []).append(space_id)
        for change in feed.changes:
            target = f"access_change:{change.change_id}"
            if change.user_id is not None:
                target += f":user:{change.user_id}"
            for space_id in spaces.get(change.tenant_id, []):
                await self._audit(space_id, f"access.{change.kind}", target)
        if after is None or feed.after > after:
            await self._access.save_access_cursor(feed.after)
        return len(feed.changes)

    async def push_usage(self) -> int:
        """Sends the hourly usage of every space this cell serves, over the last `USAGE_LOOKBACK`, so the
        control plane holds the tenant's totals (5.15); an hour sent again replaces the previous count.
        Returns how many rows the control plane accepted."""
        if self._usage is None:
            return 0
        now = self._clock.now()
        since, until = hour_floor(now - USAGE_LOOKBACK), hour_floor(now) + timedelta(hours=1)
        items: list[dict[str, Any]] = []
        for space_id in self._spaces():
            try:
                rows = await self._usage.usage(space_id, since, until)
            except Unavailable:
                continue  # its database is being created or restored: the next push takes it
            items += [
                {
                    "space_id": str(space_id),
                    "period_start": row.hour.isoformat(),
                    "metric": row.metric,
                    "quantity": row.quantity,
                    "cost_micros": row.cost_micros,
                }
                for row in rows
            ]
        accepted = 0
        for start in range(0, len(items), USAGE_BATCH):
            accepted += await self._client.push_usage(self._cell_id, items[start : start + USAGE_BATCH])
        return accepted

    async def run(
        self,
        stop: asyncio.Event,
        revocation_interval_s: float = 2.0,
        snapshot_wait_s: float = 25.0,
        usage_interval_s: float = USAGE_EVERY_S,
    ) -> None:
        await asyncio.gather(
            self._loop(stop, self.pull_revocations, revocation_interval_s),
            self._loop(stop, lambda: self.pull_snapshot(snapshot_wait_s), 1.0),
            self._loop(stop, self.push_usage, usage_interval_s),
            self._loop(stop, self.pull_access_changes, ACCESS_EVERY_S),
        )

    async def _loop(self, stop: asyncio.Event, step: Any, pause_s: float) -> None:
        failures = 0
        while not stop.is_set():
            try:
                await step()
                failures = 0
            except Unavailable as exc:
                # The control plane is away or does not know this cell yet: expected, so no traceback.
                failures += 1
                log.warning("control plane unavailable", extra={"detail": str(exc), "failures": failures})
            except Exception:  # keep the last good state and try again (4.7)
                failures += 1
                log.exception("control sync step failed")
            # Back off while it fails, so a long outage costs one line a minute, not one a second.
            wait = min(pause_s * 2 ** min(failures, 6), MAX_BACKOFF_S) if failures else pause_s
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), wait)

    async def _mirror_keys(self, keys: Any) -> None:
        if not isinstance(keys, dict):
            raise InvalidInput("malformed key list")
        for space_id, entries in keys.items():
            rows = [_row(UUID(space_id), e) for e in entries]
            changed = await self._edge.mirror_keys(UUID(space_id), rows)
            if self._audit is not None:
                for row in changed:
                    action = (
                        "key.revoked" if row.revoked else "key.issued" if row.version == 1 else "key.updated"
                    )
                    # The key's source goes with it, as a person does in an access change: a key id
                    # alone tells the reader nothing about whose access changed.
                    await self._audit(UUID(space_id), action, f"key:{row.key_id}:source:{row.source_id}")
            for entry in entries:
                if entry["status"] == "active" and entry.get("expires_at"):
                    self._expiring[entry["key_id"]] = datetime.fromisoformat(entry["expires_at"])
                else:
                    self._expiring.pop(entry["key_id"], None)


def _row(space_id: UUID, entry: dict[str, Any]) -> SourceKeyRow:
    return SourceKeyRow(
        space_id=space_id,
        key_id=entry["key_id"],
        source_id=UUID(entry["source_id"]),
        fast_hash=bytes.fromhex(entry["fast_hash"]),
        slow_hash=entry["slow_hash"],
        scopes=frozenset(Scope(s) for s in entry["scopes"]),
        status=entry["status"],
        version=int(entry["version"]),
    )


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))
