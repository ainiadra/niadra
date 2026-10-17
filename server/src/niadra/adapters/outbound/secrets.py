"""`SecretStore` on the cell database, encrypted with a key derived from the KEK.

A reference is `spaces/<space>/<kind>/<id>`; every `put` adds a version, so a webhook can sign
with the old and the new secret while a rotation is under way. A Vault KV adapter can replace
this class without touching callers.
"""

import os
from uuid import UUID

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine

from niadra.adapters.outbound.postgres.schema.platform import Secret
from niadra.domain.common.errors import Conflict, NotFound

NONCE_BYTES = 12
PUT_ATTEMPTS = 3


def secrets_key_of(kek: bytes) -> bytes:
    """The secret store's key a local KEK implies; a move to KMS keeps it, so stored secrets still open."""
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=b"niadra-secrets").derive(kek)


class PgSecretStore:
    def __init__(self, engine: AsyncEngine, key: bytes, kek_id: str) -> None:
        self._aead = AESGCM(key)
        self._engine = engine
        self._kek_id = kek_id

    @classmethod
    def from_kek(cls, engine: AsyncEngine, kek: bytes, kek_id: str) -> "PgSecretStore":
        return cls(engine, secrets_key_of(kek), kek_id)

    async def get(self, ref: str) -> str:
        stmt = select(Secret.value_ct).where(Secret.ref == ref).order_by(Secret.version.desc()).limit(1)
        async with self._engine.connect() as conn:
            sealed = await conn.scalar(stmt)
        if sealed is None:
            raise NotFound("secret not found")
        return self._aead.decrypt(sealed[:NONCE_BYTES], sealed[NONCE_BYTES:], ref.encode()).decode()

    async def put(self, ref: str, value: str) -> int:
        nonce = os.urandom(NONCE_BYTES)
        sealed = nonce + self._aead.encrypt(nonce, value.encode(), ref.encode())
        next_version = select(func.coalesce(func.max(Secret.version), 0) + 1).where(Secret.ref == ref)
        stmt = (
            insert(Secret)
            .values(ref=ref, version=next_version.scalar_subquery(), value_ct=sealed, kek_id=self._kek_id)
            .returning(Secret.version)
        )
        # Two writers can compute the same next version; the primary key sends the loser round again.
        for _ in range(PUT_ATTEMPTS):
            try:
                async with self._engine.begin() as conn:
                    version: int = (await conn.execute(stmt)).scalar_one()
                    return version
            except IntegrityError:
                continue
        raise Conflict("the secret is being written concurrently")

    async def destroy_space(self, space_id: UUID) -> int:
        stmt = delete(Secret).where(Secret.ref.startswith(f"spaces/{space_id}/", autoescape=True))
        async with self._engine.begin() as conn:
            return len((await conn.execute(stmt.returning(Secret.ref))).all())
