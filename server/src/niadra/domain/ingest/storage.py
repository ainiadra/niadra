"""Object-storage keys of ingestion: raw webhook payloads, media uploads and imported files (5.2).

Raw payloads and media live under `<kind>/<space>/<handle>/`, so `forget` deletes a person's copies
by prefix (5.14.0). Objects without a resolved handle go under `_`: unmapped payloads, which `forget` finds
by what they name in the index of raw content (`domain.privacy.raw_index`), and media uploaded without a
subject, which the events that reference them erase one by one. All of it is kept until an erasure, or the
`raw_content` retention the customer sets, which reads the same index.
"""

import re
from collections.abc import Mapping
from datetime import datetime
from typing import Any
from uuid import UUID

MEDIA_REF_PREFIX = "med_"
NO_HANDLE = "_"
_MEDIA_REF = re.compile(r"^med_(?:([0-9a-f]{32})_)?([0-9a-f]{32})$")


def raw_key(space_id: UUID, handle_id: UUID | None, at: datetime, object_id: UUID) -> str:
    """The raw payload is always kept, so a new mapping version can remap it (5.2)."""
    return f"raw/{space_id}/{handle_id or NO_HANDLE}/{at:%Y-%m-%d}/{object_id}"


def media_ref(handle_id: UUID | None, object_id: UUID) -> str:
    """The reference names the handle, so the event that carries it can find the object's key."""
    owner = f"{handle_id.hex}_" if handle_id else ""
    return f"{MEDIA_REF_PREFIX}{owner}{object_id.hex}"


def media_key(space_id: UUID, ref: str) -> str | None:
    """None for a reference this cell never issued: it points at nothing to store or erase."""
    match = _MEDIA_REF.match(ref)
    if match is None:
        return None
    owner, object_hex = match.groups()
    handle = str(UUID(hex=owner)) if owner else NO_HANDLE
    return f"media/{space_id}/{handle}/{UUID(hex=object_hex)}"


def import_prefix(space_id: UUID, import_id: UUID) -> str:
    """Uploaded files are deleted as soon as their rows are in the log: the log is the copy that `forget`
    reaches, the file is only a staging area."""
    return f"imports/{space_id}/{import_id}/"


def import_part_key(space_id: UUID, import_id: UUID, part: int) -> str:
    return f"{import_prefix(space_id, import_id)}part-{part:05d}"


def event_blob_keys(
    space_id: UUID, raw_ref: str | None, content_meta: Mapping[str, Any] | None
) -> tuple[str, ...]:
    """What `forget` deletes from object storage for one event: its raw payload and its media."""
    ref = (content_meta or {}).get("media_ref")
    media = media_key(space_id, ref) if isinstance(ref, str) else None
    return tuple(k for k in (raw_ref, media) if k)
