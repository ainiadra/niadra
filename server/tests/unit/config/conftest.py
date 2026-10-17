import json
from collections.abc import Callable
from typing import Any

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from niadra.config.models import SpaceConfig
from niadra.domain.config.signing import SignedSnapshot, content_hash, sign

CELL_ID = "sa-east-1-c1"

SnapshotFactory = Callable[..., SignedSnapshot]


@pytest.fixture
def control_key() -> Ed25519PrivateKey:
    return Ed25519PrivateKey.generate()


@pytest.fixture
def make_snapshot(control_key: Ed25519PrivateKey) -> SnapshotFactory:
    def build(
        version: int,
        *configs: SpaceConfig | dict[str, Any],
        cell_id: str = CELL_ID,
        min_cell_version: str | None = None,
        key: Ed25519PrivateKey | None = None,
        moved: dict[str, str] | None = None,
    ) -> SignedSnapshot:
        documents = [c.model_dump(mode="json") if isinstance(c, SpaceConfig) else c for c in configs]
        blobs = {content_hash(d): json.dumps(d, indent=2).encode() for d in documents}
        manifest = {
            "cell_id": cell_id,
            "version": version,
            "min_cell_version": min_cell_version,
            "created_at": "2026-09-22T17:00:00+00:00",
            "spaces": {str(d["space_id"]): content_hash(d) for d in documents},
            "removed": {},
            "moved": moved or {},
        }
        # Pretty-printed on the wire on purpose: the signature covers the canonical form.
        return SignedSnapshot(
            json.dumps(manifest, indent=1).encode(), sign(manifest, key or control_key), blobs
        )

    return build
