"""HTTP client of the control plane, used by a cell to pull its snapshot and revocation feed (4.7)."""

from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any
from uuid import UUID

import httpx

from niadra.domain.common.errors import Unavailable
from niadra.domain.spaces.deletion import receipt_hash


class HttpControlClient:
    def __init__(self, base_url: str, cell_token: str, client: httpx.AsyncClient | None = None) -> None:
        self._client = client or httpx.AsyncClient(base_url=base_url, timeout=httpx.Timeout(10.0))
        self._headers = {"x-niadra-cell-token": cell_token}

    async def snapshot(self, cell_id: str, after: int | None, wait_s: float) -> dict[str, Any] | None:
        params: dict[str, Any] = {} if after is None else {"after": after, "wait": wait_s}
        # The long poll holds the request open; give it room beyond the wait.
        response = await self._get(f"/v1/snapshot/{cell_id}", params, timeout=wait_s + 10.0)
        if response.status_code == 304:
            return None
        body: dict[str, Any] = response.json()
        return body

    async def revocations(self, cell_id: str, after: int) -> dict[str, Any]:
        body: dict[str, Any] = (
            await self._get("/v1/revocations", {"cell_id": cell_id, "after": after})
        ).json()
        return body

    async def access_changes(self, cell_id: str, after: datetime | None) -> dict[str, Any]:
        params: dict[str, Any] = {"cell_id": cell_id}
        if after is not None:
            params["after"] = after.isoformat()
        body: dict[str, Any] = (await self._get("/v1/access-changes", params)).json()
        return body

    async def post_deletion_receipt(self, cell_id: str, space_id: UUID, receipt: dict[str, Any]) -> None:
        body = {"cell_id": cell_id, "receipt": receipt, "receipt_hash": receipt_hash(receipt).hex()}
        try:
            response = await self._client.post(
                f"/v1/spaces/{space_id}/deletion-receipt", json=body, headers=self._headers
            )
        except httpx.HTTPError as exc:
            raise Unavailable("the control plane is unreachable") from exc
        if response.status_code not in (200, 201):
            raise Unavailable(f"the control plane answered {response.status_code}")

    async def push_usage(self, cell_id: str, items: Sequence[Mapping[str, Any]]) -> int:
        try:
            response = await self._client.post(
                "/v1/usage", json={"cell_id": cell_id, "items": list(items)}, headers=self._headers
            )
        except httpx.HTTPError as exc:
            raise Unavailable("the control plane is unreachable") from exc
        if response.status_code != 202:
            raise Unavailable(f"the control plane answered {response.status_code}")
        return int(response.json()["accepted"])

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _get(self, path: str, params: dict[str, Any], timeout: float = 10.0) -> httpx.Response:
        try:
            response = await self._client.get(path, params=params, headers=self._headers, timeout=timeout)
        except httpx.HTTPError as exc:
            raise Unavailable("the control plane is unreachable") from exc
        if response.status_code not in (200, 304):
            raise Unavailable(f"the control plane answered {response.status_code}")
        return response
