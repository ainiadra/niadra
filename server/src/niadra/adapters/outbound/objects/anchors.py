"""The locked anchor store (5.13): the daily roots of the receipt chains in a bucket created with Object
Lock. Each copy carries a compliance-mode retention and is written only if absent, so not even the
cell's own role can delete or replace a root it wrote."""

from datetime import datetime

from niadra.adapters.outbound.objects.s3 import S3ObjectStore

ANCHOR_TYPE = "application/json"


class LockedAnchors:
    def __init__(self, store: S3ObjectStore) -> None:
        self._store = store

    @property
    def locked(self) -> bool:
        return True

    async def put_anchor(self, key: str, data: bytes, retain_until: datetime) -> None:
        await self._store.put_retained(key, data, ANCHOR_TYPE, retain_until)

    async def get_anchor(self, key: str) -> bytes:
        return await self._store.get(key)
