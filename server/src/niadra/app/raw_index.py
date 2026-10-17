"""The rows of the index of raw content (`domain.privacy.raw_index`), for the modules that store it."""

from collections.abc import Iterable
from datetime import datetime
from uuid import UUID

from niadra.domain.ingest.storage import NO_HANDLE
from niadra.domain.privacy.raw_index import RAW_INDEX_PURPOSE, TOKEN_BYTES, RawObject, body_tokens
from niadra.ports.infra import KeyVault


async def raw_object(
    keys: KeyVault,
    space_id: UUID,
    key: str,
    owner: UUID | str | None,
    at: datetime,
    body: bytes | None = None,
) -> RawObject:
    """The row of an object kept under `owner` (no handle when None); `body`, a payload as it came, gives the
    keyed hashes of what it names a person by (media and interactions have none)."""
    tokens = await sealed_tokens(keys, space_id, body_tokens(body)) if body is not None else frozenset()
    return RawObject(key, str(owner) if owner else NO_HANDLE, at, tokens)


async def sealed_tokens(keys: KeyVault, space_id: UUID, tokens: Iterable[str]) -> frozenset[bytes]:
    """Each token keyed-hashed with the space's key, as handles are (5.14.1)."""
    sealed = [await keys.hmac(space_id, RAW_INDEX_PURPOSE, token.encode()) for token in tokens]
    return frozenset(digest[:TOKEN_BYTES] for digest in sealed)
