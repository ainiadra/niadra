"""Applying the signed configuration snapshot and serving it from memory (4.7).

An invalid snapshot never reaches the database: the cell keeps serving the last good one and
the caller reports the error to the control plane. The snapshot and the documents live in the cell
database; each space's schedules live in its own database and follow in a second step.
"""

import asyncio
import contextlib
import logging
import time
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from pydantic import ValidationError

from niadra.app.pipeline import replace_space_schedules, specs_for
from niadra.config.models import SnapshotManifest, SpaceConfig
from niadra.config.observed import observed_operation_names, observed_types, observed_words, with_observed
from niadra.config.validation import AccessFeed, RevocationFeed, parse_space
from niadra.domain.common.errors import Conflict, InvalidInput, NotFound, Unavailable
from niadra.domain.config import OBSERVED_DOC, OBSERVED_VERSION, SPACE_DOC, AppliedSnapshot, StoredDocument
from niadra.domain.config.signing import (
    SignedSnapshot,
    content_hash,
    parse_json,
    verify_signed,
    version_tuple,
)
from niadra.domain.objects.words import Words
from niadra.ports.turns import ConfigHistory
from niadra.ports.uow import UnitOfWorkFactory

ACCESS_CURSOR = "access_feed_us"
"""Where the access feed stopped (`cell_state`), so a restart never writes a change twice."""
MISS_REFRESH_S = 1.0
"""How often a process reads its configuration again for a key that names a space it does not know, or for a
feature route of a space whose copy has the feature off."""

log = logging.getLogger(__name__)


class ConfigService:
    def __init__(
        self,
        uow: UnitOfWorkFactory,
        signing_keys: Sequence[Ed25519PublicKey],
        cell_id: str,
        cell_version: str,
    ) -> None:
        self._uow = uow
        self._keys = list(signing_keys)
        self._cell_id = cell_id
        self._cell_version = version_tuple(cell_version)
        # The turns module's change log takes each changed space's sections (`ports.turns.ConfigHistory`).
        self.history: ConfigHistory | None = None

    async def apply(self, snapshot: SignedSnapshot) -> AppliedSnapshot:
        """Verifies, validates every space and applies atomically; raises without touching anything."""
        raw_manifest = verify_signed(snapshot.manifest, snapshot.signature, self._keys)
        manifest = self._manifest(raw_manifest)
        parsed = [
            self._document(manifest, space, digest, snapshot.documents)
            for space, digest in manifest.spaces.items()
        ]
        documents = [document for document, _ in parsed]
        async with self._uow.cell() as uow:
            active = await uow.config.active_version(self._cell_id, lock=True)
            if active is not None and manifest.version <= active:
                raise Conflict(f"snapshot {manifest.version} is not newer than the active {active}")
            before = (await uow.config.active_manifest(self._cell_id) or {}).get("spaces") or {}
            await uow.config.save_snapshot(self._cell_id, manifest.version, raw_manifest, snapshot.signature)
            await uow.config.replace_documents(SPACE_DOC, documents)
            serving = {row.space_id for row in await uow.directory.entries() if row.serving}
        # Then each serving space's schedules, in its own database. A space still being created gets
        # them from the provisioner; one that fails here keeps its previous schedules until the next
        # snapshot, which is why the loop goes on.
        for _, config in parsed:
            if config.space_id in serving:
                await self.install_schedules(config, manifest.created_at)
                if active is not None and before.get(str(config.space_id)) != manifest.spaces.get(
                    str(config.space_id)
                ):
                    await self._record_history(config, manifest.version, manifest.created_at)
        changed = tuple(
            (UUID(space), digest)
            for space, digest in sorted(manifest.spaces.items())
            # The first snapshot a cell applies is its starting point, not a change anyone made.
            if active is not None and before.get(space) != digest
        )
        return AppliedSnapshot(self._cell_id, manifest.version, len(documents), changed)

    async def install_schedules(self, config: SpaceConfig, after: datetime) -> bool:
        try:
            async with self._uow(config.space_id) as uow:
                await replace_space_schedules(uow, specs_for(config, after))
        except Unavailable:
            return False
        except Exception:
            log.exception("schedules of a space not updated", extra={"space_id": str(config.space_id)})
            return False
        return True

    async def _record_history(self, config: SpaceConfig, version: int, at: datetime) -> None:
        # Like the schedules: a space that fails here misses one entry of its change log, never the snapshot.
        if self.history is None:
            return
        try:
            await self.history.record_config(config, version, at)
        except Unavailable:
            return
        except Exception:
            log.exception(
                "configuration history of a space not recorded", extra={"space_id": str(config.space_id)}
            )

    async def active_version(self) -> int | None:
        async with self._uow.cell() as uow:
            return await uow.config.active_version(self._cell_id)

    def verify_access_feed(self, body: bytes, signature: bytes) -> AccessFeed:
        """Signed like the revocation feed; its changes become receipts in each space."""
        try:
            feed = AccessFeed.model_validate(verify_signed(body, signature, self._keys))
        except ValidationError:
            raise InvalidInput("malformed access feed") from None
        if feed.cell_id != self._cell_id:
            raise InvalidInput("the access feed is for another cell")
        return feed

    async def access_cursor(self) -> datetime | None:
        async with self._uow.cell() as uow:
            micros = await uow.config.state(ACCESS_CURSOR)
        return None if micros is None else datetime.fromtimestamp(micros / 1_000_000, UTC)

    async def save_access_cursor(self, at: datetime) -> None:
        async with self._uow.cell() as uow:
            await uow.config.set_state(ACCESS_CURSOR, int(at.timestamp() * 1_000_000))

    def verify_revocations(self, body: bytes, signature: bytes) -> RevocationFeed:
        """The revocation feed is signed like the snapshot; the edge applies what it returns (4.7)."""
        try:
            feed = RevocationFeed.model_validate(verify_signed(body, signature, self._keys))
        except ValidationError:
            raise InvalidInput("malformed revocation feed") from None
        if feed.cell_id != self._cell_id:
            raise InvalidInput("the revocation feed is for another cell")
        return feed

    async def cache_epoch(self) -> int:
        async with self._uow.cell() as uow:
            return await uow.config.cache_epoch()

    async def bump_cache_epoch(self) -> int:
        """Called by cell automation after a cache failover (06, 3) and after a restore."""
        async with self._uow.cell() as uow:
            return await uow.config.bump_cache_epoch()

    def _manifest(self, raw: Any) -> SnapshotManifest:
        try:
            manifest = SnapshotManifest.model_validate(raw)
        except ValidationError:
            raise InvalidInput("malformed snapshot manifest") from None
        if manifest.cell_id != self._cell_id:
            raise InvalidInput("the snapshot is for another cell")
        if manifest.min_cell_version and version_tuple(manifest.min_cell_version) > self._cell_version:
            raise InvalidInput("the snapshot needs a newer cell version")
        return manifest

    @staticmethod
    def _document(
        manifest: SnapshotManifest, space: str, digest: str, blobs: dict[str, bytes]
    ) -> tuple[StoredDocument, SpaceConfig]:
        if digest not in blobs:
            raise InvalidInput(f"document {digest} is missing")
        document = parse_json(blobs[digest], "a space document")
        if content_hash(document) != digest:
            raise InvalidInput(f"document {digest} does not match its hash")
        try:
            space_id = UUID(space)
        except ValueError:
            raise InvalidInput("bad space id in the manifest") from None
        config = parse_space(document, space_id)
        # Stored as received, so `content_hash` keeps addressing exactly these bytes.
        return StoredDocument(space_id, SPACE_DOC, digest, manifest.version, document), config


class CachedConfigProvider:
    """`ConfigProvider` from an in-process copy, refreshed on a timer.

    Each refresh reads the active version, the observed version and the cache epoch; the space documents are
    reloaded only when the active version changed, the observed types only when either changed. A document
    that no longer parses keeps its previous copy. A space serves its declared configuration with its observed
    types merged beneath (`with_observed`).
    """

    def __init__(
        self, uow: UnitOfWorkFactory, cell_id: str, monotonic: Callable[[], float] = time.monotonic
    ) -> None:
        self._uow = uow
        self._cell_id = cell_id
        self._monotonic = monotonic
        self._read_from = float("-inf")
        """When the last completed read of the cell's copy began."""
        self._miss = asyncio.Lock()
        self._version: int | None = None
        self._observed_version: int | None = None
        self._declared: dict[UUID, SpaceConfig] = {}
        self._observed: dict[UUID, list[dict[str, Any]]] = {}
        self._observed_ops: dict[UUID, list[str]] = {}
        self._observed_words: dict[UUID, Words] = {}
        self._spaces: dict[UUID, SpaceConfig] = {}
        self._removed: dict[UUID, datetime] = {}
        self._moved: dict[UUID, str] = {}
        self._epoch = 0

    @property
    def version(self) -> int | None:
        return self._version

    @property
    def cache_epoch(self) -> int:
        """Enters every derived-state cache key; read tasks see a bump within one refresh (06, 3)."""
        return self._epoch

    def space(self, space_id: UUID) -> SpaceConfig:
        try:
            return self._spaces[space_id]
        except KeyError:
            raise NotFound("unknown space") from None

    def spaces(self) -> Sequence[SpaceConfig]:
        return list(self._spaces.values())

    def removed(self) -> Mapping[UUID, datetime]:
        return dict(self._removed)

    def moved(self) -> Mapping[UUID, str]:
        return self._moved

    async def refresh_on_miss(self) -> bool:
        asked = self._monotonic()
        async with self._miss:
            if self._read_from >= asked:
                return True
            if asked - self._read_from < MISS_REFRESH_S:
                return False
            await self.refresh()
            return True

    async def refresh(self) -> None:
        began = self._monotonic()
        await self._read()
        self._read_from = max(self._read_from, began)

    def observe(
        self,
        space_id: UUID,
        types: Sequence[Mapping[str, Any]],
        operations: Sequence[str] = (),
        words: Words | None = None,
    ) -> None:
        """What this process just inferred for a space, served at once; the others read it from the cell."""
        self._observed[space_id] = [dict(t) for t in types]
        self._observed_ops[space_id] = list(operations)
        self._observed_words[space_id] = words or Words()
        if space_id in self._declared:
            self._spaces[space_id] = with_observed(
                self._declared[space_id],
                self._observed[space_id],
                self._observed_ops[space_id],
                self._observed_words[space_id],
            )

    async def _read(self) -> None:
        async with self._uow.cell() as uow:
            self._epoch = await uow.config.cache_epoch()
            version = await uow.config.active_version(self._cell_id)
            observed_version = await uow.config.state(OBSERVED_VERSION)
            if version == self._version and observed_version == self._observed_version:
                return
            documents = await uow.config.documents(SPACE_DOC) if version != self._version else None
            manifest = await uow.config.active_manifest(self._cell_id) if version != self._version else None
            observed = await uow.config.documents(OBSERVED_DOC)
        if documents is not None:
            self._declared = self._parse(documents)
            self._removed = _removed(manifest)
            self._moved = _moved(manifest)
            self._version = version
        self._observed = {doc.space_id: observed_types(doc.document) for doc in observed}
        self._observed_ops = {doc.space_id: observed_operation_names(doc.document) for doc in observed}
        self._observed_words = {doc.space_id: observed_words(doc.document) for doc in observed}
        self._observed_version = observed_version
        self._spaces = {
            space_id: with_observed(
                config,
                self._observed.get(space_id, []),
                self._observed_ops.get(space_id, []),
                self._observed_words.get(space_id),
            )
            for space_id, config in self._declared.items()
        }

    async def run(self, stop: asyncio.Event, interval_s: float = 5.0) -> None:
        while not stop.is_set():
            try:
                await self.refresh()
            except Exception:
                # Serving the last good copy is the point of the cache (11).
                log.exception("configuration refresh failed")
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), interval_s)

    def _parse(self, documents: Sequence[StoredDocument]) -> dict[UUID, SpaceConfig]:
        spaces: dict[UUID, SpaceConfig] = {}
        for doc in documents:
            try:
                spaces[doc.space_id] = SpaceConfig.model_validate(doc.document)
            except ValidationError:
                log.error(
                    "stored space configuration no longer parses", extra={"space_id": str(doc.space_id)}
                )
                if doc.space_id in self._declared:
                    spaces[doc.space_id] = self._declared[doc.space_id]
        return spaces


def _moved(manifest: dict[str, Any] | None) -> dict[UUID, str]:
    moved: dict[UUID, str] = {}
    for space, cell in (manifest["moved"] if manifest else {}).items():
        try:
            moved[UUID(space)] = str(cell)
        except (TypeError, ValueError):
            log.error("unreadable moved space in the active manifest")
    return moved


def _removed(manifest: dict[str, Any] | None) -> dict[UUID, datetime]:
    removed: dict[UUID, datetime] = {}
    for space, at in (manifest["removed"] if manifest else {}).items():
        try:
            removed[UUID(space)] = datetime.fromisoformat(at)
        except (TypeError, ValueError):
            log.error("unreadable removed space in the active manifest")
    return removed
