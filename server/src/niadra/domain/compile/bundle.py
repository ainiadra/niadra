"""A profile's stored packs as one value: every variant of one generation, framed and compressed (5.10).

The bundle is one row per profile, rewritten once per generation, so a change costs one write, one dead
tuple and one TOAST value, where a row and a manifest per variant would cost six to ten of each. The
framing knows nothing of what a pack holds: each variant is its metadata and its body as bytes, so the
pack's own format can change without touching it. The value is compressed before it is sealed (a sealed
value does not compress), and the variants of one profile share most of their text.
"""

import json
import struct
import zlib
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from uuid import UUID

from niadra.domain.compile.model import StoredPack, profile_target

MAGIC = b"NPB1"
_LENGTH = struct.Struct(">I")
_LEVEL = 6


def bundle_bytes(packs: Sequence[StoredPack]) -> bytes:
    """The packs of one generation as bytes, in `pack_key` order; `last_read_at` stays out (it moves on
    every read and lives next to the value, not in it)."""
    parts = [MAGIC, _LENGTH.pack(len(packs))]
    for pack in sorted(packs, key=lambda p: p.pack_key):
        head = json.dumps(
            {
                "pack_key": pack.pack_key,
                "target": pack.target,
                "generation": pack.generation,
                "compiler_version": pack.compiler_version,
                "privacy_version": pack.privacy_version,
                "pack_hash": pack.pack_hash,
                "manifest_hash": pack.manifest_hash,
                "as_of": pack.as_of.isoformat(),
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        parts += [_LENGTH.pack(len(head)), head, _LENGTH.pack(len(pack.body)), pack.body]
    return zlib.compress(b"".join(parts), _LEVEL)


def bundle_packs(data: bytes, last_read_ms: Mapping[str, int] | None = None) -> list[StoredPack]:
    """The packs back, with the time each variant was last read (`last_read_ms`, by variant key)."""
    raw = zlib.decompress(data)
    if raw[:4] != MAGIC:
        raise ValueError("not a pack bundle")
    reads = last_read_ms or {}
    (count,) = _LENGTH.unpack_from(raw, 4)
    at = 8
    packs = []
    for _ in range(count):
        head, at = _chunk(raw, at)
        body, at = _chunk(raw, at)
        meta = json.loads(head)
        variant = meta["pack_key"].split("/", 1)[1]
        read = reads.get(variant)
        packs.append(
            StoredPack(
                pack_key=meta["pack_key"],
                target=meta["target"],
                generation=int(meta["generation"]),
                compiler_version=meta["compiler_version"],
                privacy_version=int(meta["privacy_version"]),
                body=body,
                pack_hash=meta["pack_hash"],
                manifest_hash=meta["manifest_hash"],
                as_of=datetime.fromisoformat(meta["as_of"]),
                last_read_at=datetime.fromtimestamp(read / 1000, UTC) if read is not None else None,
            )
        )
    return packs


def read_marks(reads: Mapping[str, datetime]) -> dict[str, dict[str, int]]:
    """`mark_read`'s input (by `pack_key`) grouped by the profile's target, as milliseconds by variant; a key
    that names no profile's pack is left out."""
    grouped: dict[str, dict[str, int]] = {}
    for key, at in reads.items():
        target = target_of(key)
        if target is not None:
            grouped.setdefault(target, {})[key.partition("/")[2]] = int(at.timestamp() * 1000)
    return grouped


def target_of(pack_key: str) -> str | None:
    """The profile target a `pack_key` (`<profile id>/<variant>`) belongs to; None for any other string."""
    profile, sep, variant = pack_key.partition("/")
    try:
        return profile_target(UUID(profile)) if sep and variant else None
    except ValueError:
        return None


def _chunk(raw: bytes, at: int) -> tuple[bytes, int]:
    (length,) = _LENGTH.unpack_from(raw, at)
    start = at + _LENGTH.size
    return raw[start : start + length], start + length
