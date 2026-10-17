"""Column encryption for the PostgreSQL repositories (5.14.1).

The additional authenticated data binds a ciphertext to its place (`table:row:column`) so a
value cannot be copied to another row, and deliberately excludes the profile id: merges and
unmerges never force re-encryption.
"""

from uuid import UUID

from niadra.domain.privacy.mentions import INDEXED_TABLES, MentionKey
from niadra.domain.privacy.raw_index import RAW_INDEX_PURPOSE, TOKEN_BYTES, body_tokens
from niadra.ports.infra import KeyVault

VECTOR_COLUMN = "embedding_ct"


class Sealer:
    """Seals the columns of one space transaction, and notes what each indexed row's text names a person by
    (`domain.privacy.mentions`): the unit of work writes it to `text_mentions` before it commits, with the
    row, so no row of derived memory is stored without its index entry."""

    def __init__(self, keys: KeyVault, space_id: UUID) -> None:
        self._keys = keys
        self._space_id = space_id
        self.written: dict[MentionKey, set[str]] = {}

    async def seal(self, value: str | bytes, *, table: str, row: object, column: str) -> tuple[bytes, int]:
        data = value.encode() if isinstance(value, str) else value
        self.note(table, row, column, data)
        return await self._keys.encrypt(self._space_id, data, _aad(table, row, column))

    def note(self, table: str, row: object, column: str, data: bytes) -> None:
        """What a text column of an indexed table names a person by, under its row; a vector names no one."""
        store = INDEXED_TABLES.get(table)
        if store is None or column == VECTOR_COLUMN or not data:
            return
        self.written.setdefault(MentionKey(store, str(row)), set()).update(body_tokens(data))

    async def take_mentions(self) -> list[tuple[MentionKey, frozenset[bytes]]]:
        """What the rows sealed so far name a person by, keyed-hashed as handles are, and no longer held: the
        caller writes it to the index, in its own statement or with the commit."""
        written, self.written = self.written, {}
        hashed: dict[str, bytes] = {}
        for token in {t for plain in written.values() for t in plain}:
            digest = await self._keys.hmac(self._space_id, RAW_INDEX_PURPOSE, token.encode())
            hashed[token] = digest[:TOKEN_BYTES]
        return [(key, frozenset(hashed[t] for t in plain)) for key, plain in written.items() if plain]

    async def open_text(
        self, ciphertext: bytes, dek_version: int, *, table: str, row: object, column: str
    ) -> str:
        return (await self.open_bytes(ciphertext, dek_version, table=table, row=row, column=column)).decode()

    async def open_bytes(
        self, ciphertext: bytes, dek_version: int, *, table: str, row: object, column: str
    ) -> bytes:
        return await self._keys.decrypt(self._space_id, ciphertext, _aad(table, row, column), dek_version)

    async def hmac(self, purpose: str, value: str) -> bytes:
        return await self._keys.hmac(self._space_id, purpose, value.encode())


def _aad(table: str, row: object, column: str) -> bytes:
    return f"{table}:{row}:{column}".encode()
