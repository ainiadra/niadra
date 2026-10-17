"""The hot-layer turn: a small document per event, encrypted before it reaches the cache (5.4, 06 6.4).

Envelope bytes are the data-key version (4 bytes, big endian) followed by the ciphertext, so a
reader can decrypt without another lookup. The AAD binds the envelope to its space and event.
"""

from typing import Any
from uuid import UUID

from niadra.domain.identity.handles import HandleKey
from niadra.domain.records import EventRecord

MAX_HOT_TEXT = 4000
"""What the cache keeps of a turn's text for the `live` block: a longer turn says it goes on (`text_cut`), and
is whole in the event log, the history and what extraction reads."""
_VERSION_BYTES = 4
HANDLE_CACHE_PURPOSE = "handle_cache"
"""The keyed hash that names a handle in the handle cache: the acknowledgement writes a turn under the id
the cache knows for each of its handles, and a reader of the same handles finds them the same way."""


def handle_cache_input(key: HandleKey) -> bytes:
    return f"{key.type.value}\x1f{key.scope}\x1f{key.value}".encode()


def handle_cache_name(space_id: UUID, digest: bytes) -> str:
    # A keyed hash: the cache never holds a phone number or an e-mail, not even as a plain digest.
    return f"ih:{space_id}:{digest.hex()}"


def cached_handle(value: bytes) -> tuple[UUID, bool]:
    """The handle id the cache holds, and whether it was learned from the handle itself (evidence)."""
    return UUID(bytes=value[:16]), value[16:] == b"\x01"


def hot_aad(space_id: UUID, event_id: UUID) -> bytes:
    return f"hot:{space_id}:{event_id}".encode()


def pack_envelope(dek_version: int, ciphertext: bytes) -> bytes:
    return dek_version.to_bytes(_VERSION_BYTES, "big") + ciphertext


def unpack_envelope(envelope: bytes) -> tuple[int, bytes]:
    return int.from_bytes(envelope[:_VERSION_BYTES], "big"), envelope[_VERSION_BYTES:]


def turn_document(record: EventRecord) -> dict[str, Any]:
    """What the `live` block needs to render one line, without another database read."""
    doc: dict[str, Any] = {
        "event_id": str(record.event_id),
        "kind": record.kind.value,
        "source_id": str(record.source_id),
        "channel": record.channel,
        "speaker": record.speaker.value,
        "occurred_at": record.occurred_at.isoformat(),
        "visibility": record.visibility.value,
    }
    optional = {
        "text": record.text[:MAX_HOT_TEXT] if record.text else None,
        "text_cut": len(record.text) if record.text and len(record.text) > MAX_HOT_TEXT else None,
        "conversation_id": record.conversation_id,
        "task_id": record.task_id,
        "canonical_type": record.canonical_type,
        "operation": (record.action or {}).get("operation"),
        "object_refs": [
            {"type": o.type, "namespace": o.namespace, "id": o.external_id} for o in record.object_refs
        ]
        or None,
    }
    doc.update({k: v for k, v in optional.items() if v is not None})
    return doc


def occurred_ms(record: EventRecord) -> int:
    return int(record.occurred_at.timestamp() * 1000)
