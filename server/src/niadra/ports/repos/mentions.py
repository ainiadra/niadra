"""The index of what derived memory and turn recordings name a person by (`domain.privacy.mentions`)."""

from collections.abc import Collection, Sequence
from typing import Protocol

from niadra.domain.privacy.mentions import MentionKey


class MentionRepository(Protocol):
    async def index(
        self, entries: Sequence[tuple[MentionKey, frozenset[bytes]]], *, replace: bool = False
    ) -> None:
        """Each row's keyed-hashed tokens: added to what it named, or instead of it with `replace`; a row
        that names no one any more leaves the index then."""
        ...

    async def drop(self, keys: Sequence[MentionKey]) -> None: ...

    async def naming(
        self,
        tokens: Sequence[bytes],
        plain: frozenset[str],
        *,
        stores: Collection[str],
        after: MentionKey | None,
        limit: int,
    ) -> list[MentionKey]:
        """The rows of `stores` whose text names any of the target's tokens, in key order after `after`: one
        statement whatever the size of the space. `plain` is the same tokens unhashed, for an index kept in
        memory, which reads its rows in the clear."""
        ...

    async def index_objects(
        self, entries: Sequence[tuple[str, frozenset[bytes]]], *, replace: bool = True
    ) -> None:
        """Each object's tokens: instead of what it named (one that names no one leaves the index), or added
        to it without `replace`."""
        ...

    async def drop_objects(self, keys: Sequence[str]) -> None: ...

    async def objects_naming(
        self, tokens: Sequence[bytes], *, prefix: str, after: str | None, limit: int
    ) -> list[str]:
        """The objects under `prefix` that name any of `tokens`, in key order after `after`: one statement."""
        ...

    async def ready(self, key: str) -> bool:
        """Whether what was written before the index existed is in it (`TEXT_MENTIONS_READY`, ...)."""
        ...

    async def mark_ready(self, key: str) -> None: ...
