"""A profile's variants as one stored value (5.10): what goes in comes out, whatever a pack's body holds."""

import os
from datetime import UTC, datetime

import pytest

from niadra.domain.common.ids import uuid7
from niadra.domain.compile import StoredPack, pack_key, profile_target
from niadra.domain.compile.bundle import bundle_bytes, bundle_packs, read_marks

AS_OF = datetime(2026, 9, 28, 12, tzinfo=UTC)


def stored(profile, variant: str, body: bytes) -> StoredPack:
    return StoredPack(
        pack_key(profile, variant),
        profile_target(profile),
        4,
        "c-1",
        2,
        body,
        f"p-{variant}",
        f"m-{variant}",
        AS_OF,
    )


def test_every_variant_round_trips_with_its_metadata_and_any_body() -> None:
    profile = uuid7()
    packs = [
        stored(profile, "voice", os.urandom(300)),
        stored(profile, "chat", b'{"text": "Marina"}'),
        stored(profile, "empty", b""),
    ]
    back = bundle_packs(bundle_bytes(packs))
    assert back == sorted(packs, key=lambda p: p.pack_key)


def test_the_variants_of_a_profile_compress_together() -> None:
    profile = uuid7()
    text = ("Pedido 700123 entregue com atraso; a cliente pediu reembolso do frete. " * 40).encode()
    packs = [stored(profile, f"view-{i}", text + str(i).encode()) for i in range(8)]
    assert len(bundle_bytes(packs)) < sum(len(p.body) for p in packs) / 10


def test_last_reads_come_back_by_variant_and_marks_group_by_profile() -> None:
    profile, other = uuid7(), uuid7()
    packs = [stored(profile, "chat", b"a"), stored(profile, "voice", b"b")]
    ms = int(AS_OF.timestamp() * 1000)
    back = {p.pack_key: p.last_read_at for p in bundle_packs(bundle_bytes(packs), {"chat": ms})}
    assert back == {pack_key(profile, "chat"): AS_OF, pack_key(profile, "voice"): None}
    marks = read_marks({pack_key(profile, "chat:V1"): AS_OF, pack_key(other, "voice"): AS_OF})
    assert marks == {profile_target(profile): {"chat:V1": ms}, profile_target(other): {"voice": ms}}


def test_bytes_that_are_not_a_bundle_are_refused() -> None:
    import zlib

    with pytest.raises(ValueError, match="not a pack bundle"):
        bundle_packs(zlib.compress(b"something else"))
