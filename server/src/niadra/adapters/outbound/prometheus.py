"""The `MetricsReader` port on the cell's Prometheus (kube-prometheus-stack, in the cluster)."""

import asyncio
import math
from collections.abc import Mapping

import httpx

TIMEOUT_S = 2.0


class PrometheusReader:
    def __init__(self, url: str, *, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._url = url.rstrip("/")
        self._transport = transport

    async def scalars(self, queries: Mapping[str, str]) -> dict[str, float | None]:
        async with httpx.AsyncClient(timeout=TIMEOUT_S, transport=self._transport) as client:
            values = await asyncio.gather(*(self._one(client, q) for q in queries.values()))
        return dict(zip(queries, values, strict=True))

    async def _one(self, client: httpx.AsyncClient, query: str) -> float | None:
        response = await client.get(f"{self._url}/api/v1/query", params={"query": query})
        response.raise_for_status()
        result = response.json()["data"]["result"]
        if not result:
            return None
        value = float(result[0]["value"][1])
        return None if math.isnan(value) or math.isinf(value) else value
