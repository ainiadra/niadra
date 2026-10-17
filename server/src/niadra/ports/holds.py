"""What a legal hold offers the modules that purge (24.5.1; LGPD, art. 16): what is held right now. The turn
record's kept tier and the retention sweep skip it; an erasure leaves it for the hold's release."""

from typing import Protocol
from uuid import UUID

from niadra.domain.privacy.holds import Held


class LegalHolds(Protocol):
    async def held(self, space_id: UUID) -> Held:
        """The space's active holds by what they hold, a held handle's conversations among them."""
        ...

    async def held_conversations(self, space_id: UUID) -> frozenset[bytes]:
        """The keyed hashes of the conversations under an active hold, by their own or through a handle."""
        ...
