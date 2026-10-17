"""What the turns module offers a module that finds a problem in the company's data (24.3.5, 24.6.6): an issue
returned to the data's owner, with no personal data.

`open` runs inside the finder's own transaction and spends one statement there: the open issue of the same
class, type, field and source counts one more occurrence and takes the new object references, up to 50;
without one, a new issue opens, and `data_issue.opened` goes out to whoever subscribes. It is safe to call
again for the same occurrence, as a retried group does: the issue stays one, with one more count.

A finder that throttles what it reports, to spend fewer statements, asks `acknowledged` before it holds a
finding back: when the token changed, an issue of the space was acknowledged, and the finder reports again.
"""

from collections.abc import Sequence
from typing import Protocol
from uuid import UUID

from niadra.domain.turns.issues import DataIssueKind
from niadra.ports.uow import UnitOfWork


class DataIssues(Protocol):
    async def open(
        self,
        uow: UnitOfWork,
        space_id: UUID,
        kind: DataIssueKind,
        *,
        object_type: str | None = None,
        field: str | None = None,
        source: str | None = None,
        refs: Sequence[str] = (),
    ) -> UUID:
        """The id of the open issue the occurrence was counted in. `refs` are objects as
        `type:namespace:id`, system ids only; `source` names where the data came from (a tool, a system of
        record), never a person."""
        ...

    async def acknowledged(self, space_id: UUID) -> bytes | None:
        """A token that changes each time an issue of the space is acknowledged, None before the first one.
        Raises `ConnectionError`, `TimeoutError` or `OSError` when the cache does not answer."""
        ...
