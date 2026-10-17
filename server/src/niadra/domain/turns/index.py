"""The kept tier's index as the company's audit chain anchors it (24.5.4): no second chain, the company's own.

Each kept row has a digest, SHA-256 over the RFC 8785 form of the entry `entry` builds, and a day's rows,
the ones kept that day in turn id order, fold into a Merkle root with the prefixes of RFC 6962: 0x00 before
a leaf, 0x01 before a pair, and an odd node carried up. The daily `audit.root` carries that root next to the
receipts' own, and the company recomputes it from `GET /v1/turns/index/{day}`. A row that expires or is
erased later leaves the anchored root as it was: the anchor says what the index held that day. Pure.
"""

import hashlib
from collections.abc import Sequence
from typing import Any

from niadra.domain.turns.digest import canonical_json
from niadra.domain.turns.records import KeptTurn


def entry(row: KeptTurn) -> dict[str, Any]:
    """What a kept row says about its turn, never what the turn said: ids, times, the build and the mode."""
    return {
        "turn_id": row.turn_id,
        "source_id": str(row.source_id),
        "agent": row.agent,
        "kind": row.kind,
        "started_at": row.started_at.isoformat(),
        "content_mode": row.content_mode,
        "build_hash": row.build_hash.hex() if row.build_hash is not None else None,
        "kept_at": row.kept_at.isoformat(),
    }


def entry_digest(row: KeptTurn) -> bytes:
    return hashlib.sha256(canonical_json(entry(row))).digest()


def index_root(rows: Sequence[KeptTurn]) -> bytes:
    level = [
        hashlib.sha256(b"\x00" + entry_digest(r)).digest() for r in sorted(rows, key=lambda r: r.turn_id)
    ]
    if not level:
        return hashlib.sha256(b"").digest()
    while len(level) > 1:
        paired = [
            hashlib.sha256(b"\x01" + a + b).digest() for a, b in zip(level[::2], level[1::2], strict=False)
        ]
        if len(level) % 2:
            paired.append(level[-1])
        level = paired
    return level[0]
