"""`ObjectStore` over the S3 protocol: AWS S3, or any compatible store (MinIO, Ceph RGW...)."""

import base64
import contextlib
import hashlib
from collections.abc import AsyncIterator, Mapping
from contextlib import AsyncExitStack, asynccontextmanager
from datetime import datetime
from typing import Any

from aiobotocore.config import AioConfig
from aiobotocore.session import get_session
from botocore.exceptions import ClientError  # type: ignore[import-untyped]

from niadra.domain.common.errors import Conflict, NotFound
from niadra.ports.infra import PresignedUpload

DELETE_BATCH = 1000  # the S3 DeleteObjects limit
MAX_CONNECTIONS = 64
# Multipart parts: S3 wants at least 5 MiB for every part but the last; 8 MiB keeps memory flat.
PART_BYTES = 8 * 1024 * 1024
READ_CHUNK = 1024 * 1024
MISSING = {"NoSuchKey", "404", "NotFound"}
# A conditional write (`If-None-Match: *`) that found the key.
EXISTS = {"PreconditionFailed", "412", "ConditionalRequestConflict", "409"}
NO_BUCKET = {"NoSuchBucket", "404", "NotFound"}
# Two first boots racing on the same bucket: the loser's create fails with one of these.
ALREADY_CREATED = {"BucketAlreadyOwnedByYou", "BucketAlreadyExists"}
# The one region S3 refuses as an explicit LocationConstraint.
DEFAULT_REGION = "us-east-1"


class S3ObjectStore:
    def __init__(
        self, client: Any, bucket: str, region: str = DEFAULT_REGION, presigner: Any | None = None
    ) -> None:
        self._s3 = client
        self._bucket = bucket
        self._region = region
        # Links for outside callers are signed for the public address: the signature covers the host.
        self._presigner = presigner or client

    @classmethod
    @asynccontextmanager
    async def connect(
        cls,
        *,
        bucket: str,
        region: str,
        endpoint_url: str | None = None,
        public_endpoint_url: str | None = None,
        access_key_id: str | None = None,
        secret_access_key: str | None = None,
        timeout_s: float = 10.0,
        checksums: bool = True,
    ) -> AsyncIterator["S3ObjectStore"]:
        """Without `endpoint_url` it talks to AWS S3; without keys, the default credential chain
        (an instance or pod role) signs the requests."""
        config = AioConfig(
            # Self-hosted stores rarely have wildcard DNS for virtual-host buckets; AWS picks its own.
            s3={"addressing_style": "path" if endpoint_url else "auto"},
            signature_version="s3v4",
            connect_timeout=timeout_s,
            read_timeout=timeout_s,
            retries={"max_attempts": 3, "mode": "standard"},
            # botocore opens 10 by default: a request of 50 turn records writes 100 objects, which then
            # queue for the pool in ten rounds (29/09/2026, 190 to 240 ms locally for 50 turns).
            max_pool_connections=MAX_CONNECTIONS,
            # Stores that speak S3 but not its newer checksums (Google Cloud Storage) take them only
            # when an operation needs them.
            request_checksum_calculation="when_supported" if checksums else "when_required",
            response_checksum_validation="when_supported" if checksums else "when_required",
        )
        async with AsyncExitStack() as stack:

            def create(endpoint: str | None) -> Any:
                return get_session().create_client(
                    "s3",
                    endpoint_url=endpoint,
                    region_name=region,
                    aws_access_key_id=access_key_id,
                    aws_secret_access_key=secret_access_key,
                    config=config,
                )

            client = await stack.enter_async_context(create(endpoint_url))
            presigner = (
                await stack.enter_async_context(create(public_endpoint_url)) if public_endpoint_url else None
            )
            yield cls(client, bucket, region, presigner)

    async def ensure_lifecycle(self, expire_days: Mapping[str, int]) -> None:
        """Expiry by key prefix, replacing the bucket's rules; what nothing else deletes ages out."""
        rules = [
            {
                "ID": f"expire-{prefix.strip('/').replace('/', '-') or 'all'}",
                "Status": "Enabled",
                "Filter": {"Prefix": prefix},
                "Expiration": {"Days": days},
            }
            for prefix, days in sorted(expire_days.items())
        ]
        await self._s3.put_bucket_lifecycle_configuration(
            Bucket=self._bucket, LifecycleConfiguration={"Rules": rules}
        )

    async def ensure_bucket(self) -> None:
        """Creates the bucket on first boot; a no-op when it already exists."""
        try:
            await self._s3.head_bucket(Bucket=self._bucket)
            return
        except ClientError as exc:
            if _code(exc) not in NO_BUCKET:
                raise
        params: dict[str, Any] = {"Bucket": self._bucket}
        if self._region != DEFAULT_REGION:
            params["CreateBucketConfiguration"] = {"LocationConstraint": self._region}
        try:
            await self._s3.create_bucket(**params)
        except ClientError as exc:
            if _code(exc) not in ALREADY_CREATED:
                raise

    async def put(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> None:
        await self._s3.put_object(Bucket=self._bucket, Key=key, Body=data, ContentType=content_type)

    async def put_if_absent(
        self, key: str, data: bytes, content_type: str = "application/octet-stream"
    ) -> bool:
        try:
            await self._s3.put_object(
                Bucket=self._bucket, Key=key, Body=data, ContentType=content_type, IfNoneMatch="*"
            )
        except ClientError as exc:
            if _code(exc) in EXISTS:
                return False
            raise
        return True

    async def copy(self, source: str, target: str) -> None:
        try:
            await self._s3.copy_object(
                Bucket=self._bucket, Key=target, CopySource={"Bucket": self._bucket, "Key": source}
            )
        except ClientError as exc:
            if _code(exc) in MISSING:
                raise NotFound("object not found") from None
            raise

    async def put_retained(self, key: str, data: bytes, content_type: str, retain_until: datetime) -> None:
        """Object Lock in compliance mode: until `retain_until` no one, root included, deletes this
        version; `If-None-Match` refuses a second version under the key, so the copy is never replaced.
        S3 requires an integrity header on locked writes. Raises `Conflict` when the key exists."""
        try:
            await self._s3.put_object(
                Bucket=self._bucket,
                Key=key,
                Body=data,
                ContentType=content_type,
                ContentMD5=base64.b64encode(hashlib.md5(data, usedforsecurity=False).digest()).decode(),
                ObjectLockMode="COMPLIANCE",
                ObjectLockRetainUntilDate=retain_until,
                IfNoneMatch="*",
            )
        except ClientError as exc:
            if _code(exc) in EXISTS:
                raise Conflict("the object exists and is not replaced") from None
            raise

    async def put_stream(
        self, key: str, chunks: AsyncIterator[bytes], content_type: str = "application/octet-stream"
    ) -> int:
        """Multipart upload in 8 MiB parts, so a dump of any size passes through a small container; an
        object smaller than one part is a single PUT. A failed upload is aborted, never left half-made."""
        buffer = bytearray()
        upload_id: str | None = None
        parts: list[dict[str, Any]] = []
        size = 0
        try:
            async for chunk in chunks:
                buffer.extend(chunk)
                size += len(chunk)
                while len(buffer) >= PART_BYTES:
                    if upload_id is None:
                        created = await self._s3.create_multipart_upload(
                            Bucket=self._bucket, Key=key, ContentType=content_type
                        )
                        upload_id = str(created["UploadId"])
                    part, buffer = bytes(buffer[:PART_BYTES]), buffer[PART_BYTES:]
                    parts.append(await self._upload_part(key, upload_id, len(parts) + 1, part))
            if upload_id is None:
                await self.put(key, bytes(buffer), content_type)
                return size
            if buffer:
                parts.append(await self._upload_part(key, upload_id, len(parts) + 1, bytes(buffer)))
            await self._s3.complete_multipart_upload(
                Bucket=self._bucket, Key=key, UploadId=upload_id, MultipartUpload={"Parts": parts}
            )
        except BaseException:
            if upload_id is not None:
                with contextlib.suppress(Exception):
                    await self._s3.abort_multipart_upload(Bucket=self._bucket, Key=key, UploadId=upload_id)
            raise
        return size

    async def _upload_part(self, key: str, upload_id: str, number: int, body: bytes) -> dict[str, Any]:
        response = await self._s3.upload_part(
            Bucket=self._bucket, Key=key, UploadId=upload_id, PartNumber=number, Body=body
        )
        return {"PartNumber": number, "ETag": response["ETag"]}

    async def get_stream(self, key: str) -> AsyncIterator[bytes]:
        try:
            response = await self._s3.get_object(Bucket=self._bucket, Key=key)
        except ClientError as exc:
            if _code(exc) in MISSING:
                raise NotFound("object not found") from None
            raise
        async with response["Body"] as body:
            while chunk := await body.read(READ_CHUNK):
                yield chunk

    async def get(self, key: str) -> bytes:
        try:
            response = await self._s3.get_object(Bucket=self._bucket, Key=key)
        except ClientError as exc:
            if _code(exc) in MISSING:
                raise NotFound("object not found") from None
            raise
        async with response["Body"] as body:
            data: bytes = await body.read()
        return data

    async def list_keys(self, prefix: str) -> list[str]:
        keys: list[str] = []
        paginator = self._s3.get_paginator("list_objects_v2")
        async for page in paginator.paginate(Bucket=self._bucket, Prefix=prefix):
            keys += [item["Key"] for item in page.get("Contents", [])]
        return sorted(keys)

    async def list_keys_before(self, prefix: str, before: datetime) -> list[str]:
        keys: list[str] = []
        paginator = self._s3.get_paginator("list_objects_v2")
        async for page in paginator.paginate(Bucket=self._bucket, Prefix=prefix):
            keys += [item["Key"] for item in page.get("Contents", []) if item["LastModified"] < before]
        return sorted(keys)

    async def can_list(self, prefix: str) -> bool:
        """Whether this credential may list the prefix: a write-only one is refused."""
        try:
            await self._s3.list_objects_v2(Bucket=self._bucket, Prefix=prefix, MaxKeys=1)
        except ClientError as exc:
            if _code(exc) in ("AccessDenied", "403", "Forbidden"):
                return False
            raise
        return True

    async def delete(self, key: str) -> None:
        await self._s3.delete_object(Bucket=self._bucket, Key=key)

    async def delete_prefix(self, prefix: str) -> int:
        deleted = 0
        paginator = self._s3.get_paginator("list_objects_v2")
        async for page in paginator.paginate(Bucket=self._bucket, Prefix=prefix):
            keys = [{"Key": item["Key"]} for item in page.get("Contents", [])]
            for i in range(0, len(keys), DELETE_BATCH):
                chunk = keys[i : i + DELETE_BATCH]
                await self._s3.delete_objects(Bucket=self._bucket, Delete={"Objects": chunk, "Quiet": True})
                deleted += len(chunk)
        return deleted

    async def presign_put(
        self,
        key: str,
        content_type: str,
        expires_s: int,
        *,
        size_bytes: int | None = None,
        sha256_hex: str | None = None,
    ) -> PresignedUpload:
        params: dict[str, Any] = {"Bucket": self._bucket, "Key": key, "ContentType": content_type}
        headers = {"Content-Type": content_type}
        if size_bytes is not None:
            params["ContentLength"] = size_bytes
        if sha256_hex is not None:
            # The store recomputes the digest and refuses a body that does not match.
            digest = base64.b64encode(bytes.fromhex(sha256_hex)).decode()
            params["ChecksumSHA256"] = digest
            headers["x-amz-checksum-sha256"] = digest
        url: str = await self._presigner.generate_presigned_url(
            "put_object", Params=params, ExpiresIn=expires_s
        )
        return PresignedUpload(url, headers)

    async def presign_get(self, key: str, expires_s: int) -> str:
        url: str = await self._presigner.generate_presigned_url(
            "get_object", Params={"Bucket": self._bucket, "Key": key}, ExpiresIn=expires_s
        )
        return url


def _code(exc: ClientError) -> str:
    return str(exc.response.get("Error", {}).get("Code", ""))
