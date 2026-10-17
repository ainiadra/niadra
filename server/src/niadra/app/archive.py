"""Reading the cold archive of events (5.14.0): monthly Parquet parts, each row's content encrypted.

Privacy writes the archive; this reader serves privacy's exports and the reads that must reach past the
hot window, like an object's timeline. Nothing here writes.

A month archived before the space masked a kind of value (a card, a security code, a password; WP-A)
still holds it, and rewriting a month's parts is the archive's own job: every row read here is masked on
the way out, so an export or a timeline never serves one.
"""

import json
from collections.abc import AsyncIterator, Collection
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from niadra.domain.common.errors import NotFound
from niadra.domain.common.ids import uuid7_ms
from niadra.domain.ingest.events import record_from_json
from niadra.domain.ingest.masking import ALL_TYPES, Hold, added, language, mask_fields, mask_text
from niadra.domain.ingest.media_text import is_derived
from niadra.domain.privacy.files import from_parquet
from niadra.domain.privacy.retention import month_key
from niadra.domain.records import EventRecord
from niadra.ports.infra import ConfigProvider, KeyVault, ObjectStore


def index_key(space_id: UUID) -> str:
    return f"archive/events/{space_id}/index.json"


def content_aad(event_id: object) -> bytes:
    return f"archive_events:{event_id}:content".encode()


class ArchiveReader:
    def __init__(self, objects: ObjectStore, keys: KeyVault, config: ConfigProvider | None = None) -> None:
        self._objects = objects
        self._keys = keys
        self._config = config

    async def index(self, space_id: UUID) -> dict[str, Any]:
        try:
            index: dict[str, Any] = json.loads(await self._objects.get(index_key(space_id)))
        except NotFound:
            return {"months": {}}
        return index

    async def parts(
        self, index: dict[str, Any], months: Collection[str] | None = None
    ) -> AsyncIterator[tuple[str, list[dict[str, Any]]]]:
        """Each Parquet part, oldest month first, one in memory at a time; only `months` when given."""
        for month in sorted(index["months"]):
            if months is not None and month not in months:
                continue
            for part in index["months"][month]:
                try:
                    data = await self._objects.get(part["key"])
                except NotFound:
                    continue
                yield month, from_parquet(data)

    async def content(self, space_id: UUID, row: dict[str, Any]) -> dict[str, Any]:
        plain = await self._keys.decrypt(
            space_id, row["content_ct"], content_aad(row["event_id"]), int(row["dek_version"])
        )
        decoded: dict[str, Any] = json.loads(plain)
        return self._masked(space_id, decoded)

    def _masked(self, space_id: UUID, row: dict[str, Any]) -> dict[str, Any]:
        lang, types = "pt", ALL_TYPES
        if self._config is not None:
            try:
                config = self._config.space(space_id)
                lang, types = language(config.settings.locale), config.masking_types()
            except NotFound:
                pass
        held: list[Hold] = []
        if isinstance(row.get("text"), str):
            meta_found = row.get("content_meta")
            derived = is_derived(meta_found if isinstance(meta_found, dict) else None)
            masked = mask_text(row["text"], lang, types, lines_as_spaces=derived)
            row["text"], held = masked.text, [*masked.held]
        if isinstance(row.get("fields"), dict):
            row["fields"], found = mask_fields(row["fields"], lang, types)
            held += found
        action = row.get("action")
        if isinstance(action, dict):
            for key in ("result", "purpose"):
                if isinstance(action.get(key), str):
                    masked = mask_text(action[key], lang, types)
                    action[key] = masked.text
                    held += masked.held
        if held:
            found_meta = row.get("content_meta")
            meta: dict[str, Any] = found_meta if isinstance(found_meta, dict) else {}
            row["content_meta"] = {**meta, "masked": added(meta.get("masked"), held)}
        return row

    async def record(self, space_id: UUID, row: dict[str, Any]) -> EventRecord:
        return record_from_json(await self.content(space_id, row))

    async def events(self, space_id: UUID, event_ids: Collection[UUID]) -> list[EventRecord]:
        """Archived events by id. An event's id is minted when it is received, so its month names the
        part that holds it: only those months are read."""
        wanted = set(event_ids)
        if not wanted:
            return []
        index = await self.index(space_id)
        months = {month_key(datetime.fromtimestamp(uuid7_ms(e) / 1000, UTC).date()) for e in wanted}
        found: list[EventRecord] = []
        async for _, rows in self.parts(index, months):
            for row in rows:
                event_id = UUID(row["event_id"])
                if event_id in wanted:
                    found.append(await self.record(space_id, row))
                    wanted.discard(event_id)
            if not wanted:
                break
        return found
