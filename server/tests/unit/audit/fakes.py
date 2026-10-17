"""A stand-in for `AdminAudit` in route tests: keeps what the route recorded."""

from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from niadra.domain.common.space import Principal


@dataclass
class RecordingAdminAudit:
    records: list[dict[str, Any]] = field(default_factory=list)

    async def record(
        self,
        principal: Principal,
        *,
        action: str,
        profile_id: UUID | None = None,
        target: str | None = None,
    ) -> None:
        self.records.append({"action": action, "profile_id": profile_id, "target": target})

    async def record_read(self, principal: Principal, **fields: Any) -> None:
        self.records.append({"action": "read", **fields})

    @property
    def actions(self) -> list[str]:
        return [r["action"] for r in self.records]
