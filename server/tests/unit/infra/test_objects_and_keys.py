"""S3 object store over a stand-in client, and the KEK loader."""

# The stand-in mirrors boto keyword names (Bucket, Key, ...).
# ruff: noqa: N803

import base64
import hashlib
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from botocore.exceptions import ClientError

from niadra.adapters.outbound.keys.kek import load_kek
from niadra.adapters.outbound.objects.anchors import LockedAnchors
from niadra.adapters.outbound.objects.s3 import S3ObjectStore
from niadra.domain.common.errors import Conflict, NotFound


class Body:
    def __init__(self, data: bytes) -> None:
        self.data = data

    async def __aenter__(self) -> "Body":
        return self

    async def __aexit__(self, *exc: object) -> None: ...

    async def read(self) -> bytes:
        return self.data


class Paginator:
    def __init__(self, objects: dict[str, bytes]) -> None:
        self.objects = objects

    async def paginate(self, Bucket: str, Prefix: str):  # type: ignore[no-untyped-def]
        keys = sorted(k for k in self.objects if k.startswith(Prefix))
        for i in range(0, len(keys), 1500):
            yield {"Contents": [{"Key": k} for k in keys[i : i + 1500]]}


class FakeS3:
    def __init__(self, *, buckets: set[str] | None = None, create_error: str | None = None) -> None:
        self.objects: dict[str, bytes] = {}
        self.delete_calls: list[int] = []
        self.buckets = buckets if buckets is not None else set()
        self.create_error = create_error
        self.created: list[dict[str, Any]] = []
        self.presigned: list[dict[str, Any]] = []
        self.lifecycle: list[dict[str, Any]] = []

    async def head_bucket(self, Bucket: str) -> None:
        if Bucket not in self.buckets:
            raise ClientError({"Error": {"Code": "404"}}, "HeadBucket")

    async def create_bucket(self, **params: Any) -> None:
        if self.create_error:
            raise ClientError({"Error": {"Code": self.create_error}}, "CreateBucket")
        self.created.append(params)
        self.buckets.add(params["Bucket"])

    async def put_object(self, Bucket: str, Key: str, Body: bytes, ContentType: str, **extra: Any) -> None:
        if extra.get("IfNoneMatch") == "*" and Key in self.objects:
            raise ClientError({"Error": {"Code": "PreconditionFailed"}}, "PutObject")
        self.objects[Key] = Body
        self.put_params = {"Bucket": Bucket, "Key": Key, **extra}

    async def get_object(self, Bucket: str, Key: str) -> dict[str, Any]:
        if Key not in self.objects:
            raise ClientError({"Error": {"Code": "NoSuchKey"}}, "GetObject")
        return {"Body": Body(self.objects[Key])}

    async def copy_object(self, Bucket: str, Key: str, CopySource: dict[str, str]) -> None:
        if CopySource["Key"] not in self.objects:
            raise ClientError({"Error": {"Code": "NoSuchKey"}}, "CopyObject")
        self.objects[Key] = self.objects[CopySource["Key"]]

    def get_paginator(self, name: str) -> Paginator:
        return Paginator(self.objects)

    async def delete_object(self, Bucket: str, Key: str) -> None:
        self.objects.pop(Key, None)

    async def delete_objects(self, Bucket: str, Delete: dict[str, Any]) -> None:
        self.delete_calls.append(len(Delete["Objects"]))
        for item in Delete["Objects"]:
            self.objects.pop(item["Key"], None)

    async def generate_presigned_url(self, op: str, Params: dict[str, Any], ExpiresIn: int) -> str:
        self.presigned.append(Params)
        return f"https://minio.local/{Params['Bucket']}/{Params['Key']}?op={op}&exp={ExpiresIn}"

    async def put_bucket_lifecycle_configuration(
        self, Bucket: str, LifecycleConfiguration: dict[str, Any]
    ) -> None:
        self.lifecycle = LifecycleConfiguration["Rules"]


async def test_put_get_and_missing_objects() -> None:
    store = S3ObjectStore(FakeS3(), "cell-1")
    await store.put("space/a/raw/1.json", b"{}", "application/json")
    assert await store.get("space/a/raw/1.json") == b"{}"
    with pytest.raises(NotFound):
        await store.get("space/a/raw/2.json")


async def test_a_conditional_write_never_replaces_and_a_copy_stays_in_the_store() -> None:
    s3 = FakeS3()
    store = S3ObjectStore(s3, "b")
    assert await store.put_if_absent("turns/short/s/by-id/t1", b"first")
    assert not await store.put_if_absent("turns/short/s/by-id/t1", b"second")
    assert await store.get("turns/short/s/by-id/t1") == b"first"
    await store.copy("turns/short/s/by-id/t1", "turns/kept/s/t1")
    assert await store.get("turns/kept/s/t1") == b"first"
    with pytest.raises(NotFound):
        await store.copy("turns/short/s/missing", "turns/kept/s/missing")
    await store.put("turns/short/s/by-id/t12", b"other")
    await store.delete("turns/short/s/by-id/t1")
    await store.delete("turns/short/s/by-id/t1")
    assert await store.list_keys("turns/short/s/by-id/") == ["turns/short/s/by-id/t12"]


async def test_delete_prefix_batches_within_the_s3_limit() -> None:
    s3 = FakeS3()
    s3.objects = {f"space/a/{i:05}": b"x" for i in range(2500)} | {"space/b/keep": b"x"}
    assert await S3ObjectStore(s3, "cell-1").delete_prefix("space/a/") == 2500
    assert max(s3.delete_calls) <= 1000
    assert list(s3.objects) == ["space/b/keep"]


async def test_presigned_put_names_the_bucket_and_expiry() -> None:
    upload = await S3ObjectStore(FakeS3(), "cell-1").presign_put("imports/x.csv", "text/csv", 900)
    assert upload.url == "https://minio.local/cell-1/imports/x.csv?op=put_object&exp=900"
    assert upload.headers == {"Content-Type": "text/csv"}


async def test_presigned_put_locks_size_and_digest() -> None:
    s3 = FakeS3()
    digest = hashlib.sha256(b"audio").hexdigest()
    upload = await S3ObjectStore(s3, "cell-1").presign_put(
        "media/a.ogg", "audio/ogg", 900, size_bytes=5, sha256_hex=digest
    )
    expected = base64.b64encode(hashlib.sha256(b"audio").digest()).decode()
    assert s3.presigned[-1]["ContentLength"] == 5
    assert s3.presigned[-1]["ChecksumSHA256"] == expected
    assert upload.headers["x-amz-checksum-sha256"] == expected


async def test_links_are_signed_for_the_public_address() -> None:
    internal, public = FakeS3(), FakeS3()
    await S3ObjectStore(internal, "cell-1", presigner=public).presign_get("exports/r.zip", 60)
    assert public.presigned
    assert not internal.presigned


async def test_lifecycle_rules_expire_by_prefix() -> None:
    s3 = FakeS3()
    await S3ObjectStore(s3, "cell-1").ensure_lifecycle({"imports/": 2, "raw/": 60})
    assert [(r["Filter"]["Prefix"], r["Expiration"]["Days"]) for r in s3.lifecycle] == [
        ("imports/", 2),
        ("raw/", 60),
    ]


async def test_presigned_get_names_the_bucket_and_expiry() -> None:
    url = await S3ObjectStore(FakeS3(), "cell-1").presign_get("exports/s/r.zip", 600)
    assert url == "https://minio.local/cell-1/exports/s/r.zip?op=get_object&exp=600"


async def test_ensure_bucket_creates_a_missing_bucket_once() -> None:
    s3 = FakeS3()
    store = S3ObjectStore(s3, "cell-1", "sa-east-1")
    await store.ensure_bucket()
    await store.ensure_bucket()
    assert s3.created == [
        {"Bucket": "cell-1", "CreateBucketConfiguration": {"LocationConstraint": "sa-east-1"}}
    ]


async def test_ensure_bucket_omits_the_location_in_the_default_region() -> None:
    s3 = FakeS3()
    await S3ObjectStore(s3, "cell-1", "us-east-1").ensure_bucket()
    assert s3.created == [{"Bucket": "cell-1"}]


async def test_ensure_bucket_tolerates_a_concurrent_create() -> None:
    await S3ObjectStore(FakeS3(create_error="BucketAlreadyOwnedByYou"), "cell-1").ensure_bucket()
    with pytest.raises(ClientError):
        await S3ObjectStore(FakeS3(create_error="AccessDenied"), "cell-1").ensure_bucket()


@pytest.mark.parametrize(
    "encode", [lambda k: k, lambda k: k.hex().encode(), base64.b64encode], ids=["raw", "hex", "b64"]
)
def test_kek_loads_from_a_file_in_any_encoding(tmp_path: Path, encode: Any) -> None:
    kek = bytes(range(32))
    path = tmp_path / "kek"
    path.write_bytes(encode(kek) + (b"" if encode(kek) == kek else b"\n"))
    assert load_kek(str(path)) == kek


def test_kek_loads_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NIADRA_KEK", base64.b64encode(b"k" * 32).decode())
    assert load_kek("env:NIADRA_KEK") == b"k" * 32
    monkeypatch.setenv("NIADRA_KEK", "short")
    with pytest.raises(ValueError, match="32 bytes"):
        load_kek("env:NIADRA_KEK")
    with pytest.raises(ValueError, match="32 bytes"):
        load_kek("env:NIADRA_MISSING")


async def test_a_locked_write_carries_compliance_retention_and_never_replaces() -> None:
    # The daily root is written once, retained in compliance mode, with the integrity header.
    s3 = FakeS3()
    anchors = LockedAnchors(S3ObjectStore(s3, "cell-1-audit"))
    until = datetime(2031, 9, 22, tzinfo=UTC)
    await anchors.put_anchor("audit/s/2026-09-22.json", b'{"root":"ab"}', until)
    assert s3.put_params == {
        "Bucket": "cell-1-audit",
        "Key": "audit/s/2026-09-22.json",
        "ContentMD5": base64.b64encode(
            hashlib.md5(b'{"root":"ab"}', usedforsecurity=False).digest()
        ).decode(),
        "ObjectLockMode": "COMPLIANCE",
        "ObjectLockRetainUntilDate": until,
        "IfNoneMatch": "*",
    }
    with pytest.raises(Conflict):
        await anchors.put_anchor("audit/s/2026-09-22.json", b'{"root":"cd"}', until)
    assert await anchors.get_anchor("audit/s/2026-09-22.json") == b'{"root":"ab"}'
    assert anchors.locked
