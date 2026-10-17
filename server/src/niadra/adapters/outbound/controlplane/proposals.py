"""Delivers the assistant's diff and what the observed layer inferred to the control plane: `POST
/v1/config/proposals` and `POST /v1/config/observed` (7.4, 22.4)."""

from typing import Any
from uuid import UUID

import httpx

from niadra.domain.common.errors import InvalidInput, Unavailable
from niadra.ports.proposals import ConfigProposal

CELL_TOKEN_HEADER = "x-niadra-cell-token"  # noqa: S105 - a header name, not a secret


class HttpConfigProposals:
    """`client` carries the control plane's base URL; the cell authenticates with its own token."""

    def __init__(
        self, client: httpx.AsyncClient, cell_id: str, cell_token: str, timeout_s: float = 10.0
    ) -> None:
        self._client = client
        self._cell_id = cell_id
        self._token = cell_token
        self._timeout = timeout_s

    async def submit(self, proposal: ConfigProposal) -> str:
        body = {
            "cell_id": self._cell_id,
            "space_id": str(proposal.space_id),
            "type": proposal.config_type,
            "document": proposal.document,
            "reason": proposal.reason,
        }
        try:
            response = await self._client.post(
                "/v1/config/proposals",
                json=body,
                headers={CELL_TOKEN_HEADER: self._token},
                timeout=self._timeout,
            )
        except httpx.HTTPError as exc:
            raise Unavailable("control plane unreachable") from exc
        if response.status_code >= 500 or response.status_code == 429:
            raise Unavailable(f"control plane answered {response.status_code}")
        if response.status_code >= 400:
            # A refused document does not improve with retries: the run is marked failed.
            raise InvalidInput(f"control plane refused the proposal with {response.status_code}")
        return str(response.json()["diff_id"])

    async def record(self, space_id: UUID, config_type: str, document: Any, reason: str) -> str:
        body = {
            "cell_id": self._cell_id,
            "space_id": str(space_id),
            "type": config_type,
            "document": document,
            "reason": reason,
        }
        try:
            response = await self._client.post(
                "/v1/config/observed",
                json=body,
                headers={CELL_TOKEN_HEADER: self._token},
                timeout=self._timeout,
            )
        except httpx.HTTPError as exc:
            raise Unavailable("control plane unreachable") from exc
        if response.status_code >= 500 or response.status_code == 429:
            raise Unavailable(f"control plane answered {response.status_code}")
        if response.status_code >= 400:
            raise InvalidInput(f"control plane refused the observed record with {response.status_code}")
        return str(response.json()["diff_id"])
