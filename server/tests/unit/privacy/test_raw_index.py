"""The index of raw content (5.14.0): an erasure finds a payload kept under no handle by what it names, a
document identity keeps only as a keyed hash and a short id too, without listing or reading the space's
payloads; the `raw_content` retention reads the index, never the space's listing."""

import dataclasses
import json
from collections.abc import Awaitable, Callable
from datetime import timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest

from niadra.config.models import Mapping, MappingRule, WebhookAuth
from niadra.contracts.ingest import MediaUploadIn
from niadra.contracts.privacy import ForgetRequest
from niadra.domain.records import HandleRecord
from niadra.domain.tasks import Task, TaskName
from niadra.domain.vocabulary import HandleType
from niadra.testing.cell import BILLING_SOURCE, SPACE_ID, Cell, principal
from tests.unit.ingest.fakes import build

from .world import T0, FakeIdentity, World

SECRET = f"spaces/{SPACE_ID}/webhook-in/wh"
CPF = "01234567890"
SHORT_ID = "A7F2"
UNMAPPED = f"raw/{SPACE_ID}/_/"


class TypedIdentity(FakeIdentity):
    """The world's handles with the types the test gives them; the fake's are all phones."""

    def __init__(self, world: World, types: dict[UUID, HandleType]) -> None:
        super().__init__(world)
        self.types = types

    async def get_handles(self, handle_ids: list[UUID]) -> list[HandleRecord]:
        found = await super().get_handles(handle_ids)
        return [dataclasses.replace(h, type=self.types.get(h.handle_id, h.type)) for h in found]


def customer(world: World, *handles: str) -> tuple[UUID, list[UUID]]:
    profile, ids = uuid4(), [uuid4() for _ in handles]
    world.handles |= dict(zip(ids, handles, strict=True))
    world.profiles[profile] = ids
    world.privacy_version[profile] = 1
    return profile, ids


def webhook(cell: Cell) -> Callable[[dict[str, Any]], Awaitable[None]]:
    """Sends through the ingest service: a type no rule maps is kept under no handle."""
    config = cell.config.space(SPACE_ID)
    auth = WebhookAuth(mode="url_token", secret_ref=SECRET)
    rule = MappingRule(
        match="type == 'paid'", canonical_type="invoice.paid", idempotency_key="id", occurred_at="at"
    )
    mapping = Mapping(source_id=BILLING_SOURCE, version="m1", auth=auth, rules=[rule])
    cell.config.put(config.model_copy(update={"mappings": [mapping]}))
    cell.secrets.values[SECRET] = "t0k"
    ingest, _, _ = build(cell)

    async def send(body: dict[str, Any]) -> None:
        await ingest.ingest_webhook(BILLING_SOURCE, {}, json.dumps(body).encode(), {"token": "t0k"})

    return send


def unmapped(cell: Cell) -> list[str]:
    return sorted(k for k in cell.objects.objects if k.startswith(UNMAPPED))


async def forget(cell: Cell, privacy: Any, profile: UUID, key: str) -> dict[str, Any]:
    target = ForgetRequest(target="profile", profile_id=profile)
    accepted = await privacy.forget(SPACE_ID, target, idempotency_key=key, requested_by="user:u")
    tasks = [t for t in cell.uow.store.module("pipeline")["outbox"] if t.name is TaskName.ERASE]
    await privacy.erase(tasks[-1])
    async with cell.uow(SPACE_ID) as uow:
        request = await uow.privacy.get_erasure(SPACE_ID, UUID(accepted.request_id))
    assert request is not None
    return request.counts


async def test_an_erasure_finds_an_unmapped_payload_by_a_hashed_document_and_by_a_four_character_id(
    cell: Cell, world: World, make_privacy: Any
) -> None:
    # The document is a keyed hash in identity, and a body writes it with punctuation; the short id would name
    # other people's payloads anywhere but as a whole value. Both used to be left out of the erasure.
    profile, (cpf, short) = customer(world, CPF, SHORT_ID)
    types = {cpf: HandleType.GOV_ID_HMAC, short: HandleType.SYSTEM_ID}
    privacy = make_privacy(identity=TypedIdentity(world, types))
    send = webhook(cell)
    await send({"type": "refunded", "customer": {"cpf": "012.345.678-90"}})
    await send({"type": "refunded", "customer_id": SHORT_ID})
    await send({"type": "refunded", "cpf": "987.654.321-00", "note": f"ticket {SHORT_ID} closed"})
    stored = unmapped(cell)
    assert len(stored) == 3

    counts = await forget(cell, privacy, profile, "k-1")

    (left,) = unmapped(cell)
    assert b"ticket" in cell.objects.objects[left], "a short id inside free text names no one"
    assert counts["unmapped_payloads"] == 2


async def test_an_erasure_reads_and_lists_the_same_whatever_the_size_of_the_space(
    cell: Cell, world: World, make_privacy: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    first, _ = customer(world, "cus_100001")
    second, _ = customer(world, "cus_100002")
    earlier, _ = customer(world, "cus_100003")
    privacy = make_privacy()
    send = webhook(cell)
    await send({"type": "refunded", "customer_id": "cus_100001"})
    await send({"type": "refunded", "customer_id": "cus_900000"})
    # What was written before the index existed is indexed once, by the first sweep, and what derived
    # memory, recordings and the archive wrote before theirs, by the first erasure.
    await privacy.retention_sweep(Task(TaskName.RETENTION_SWEEP, SPACE_ID))
    await forget(cell, privacy, earlier, "k-earlier")

    reads: list[str] = []
    transactions: list[str] = []
    get, list_keys, open_ = cell.objects.get, cell.objects.list_keys, cell.uow._open

    async def counted_get(key: str) -> bytes:
        reads.append(f"get {key}")
        return await get(key)

    async def counted_list(prefix: str) -> list[str]:
        reads.append(f"list {prefix}")
        return await list_keys(prefix)

    def counted_open(space_id: UUID | None, kind: str) -> Any:
        transactions.append(kind)
        return open_(space_id, kind)

    async def measured(profile: UUID, key: str) -> tuple[list[str], int]:
        reads.clear()
        transactions.clear()
        with monkeypatch.context() as patch:
            patch.setattr(cell.objects, "get", counted_get)
            patch.setattr(cell.objects, "list_keys", counted_list)
            patch.setattr(cell.uow, "_open", counted_open)
            counts = await forget(cell, privacy, profile, key)
        assert counts["unmapped_payloads"] == 1
        return list(reads), len(transactions)

    small = await measured(first, "k-small")
    for n in range(50):
        await send({"type": "refunded", "customer_id": f"cus_{200000 + n}", "phone": f"+5511980{n:06d}"})
    await send({"type": "refunded", "customer_id": "cus_100002"})
    large = await measured(second, "k-large")

    assert large == small
    assert not [r for r in large[0] if r.split(" ", 1)[1].startswith("raw/")], "no payload read or listed"


async def test_the_retention_reads_the_index_and_never_lists_the_space(
    cell: Cell, make_privacy: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    privacy = make_privacy()
    send = webhook(cell)
    ingest, _, _ = build(cell)
    await privacy.retention_sweep(Task(TaskName.RETENTION_SWEEP, SPACE_ID))
    cell.clock.at = T0 - timedelta(days=40)
    await send({"type": "refunded", "customer_id": "cus_old"})
    upload = await ingest.create_media_upload(
        principal(), MediaUploadIn(content_type="audio/ogg", size_bytes=3, sha256="0" * 64)
    )
    indexed = cell.uow.store.module("privacy")["raw_objects"]
    old_media = next(k for k in indexed if k.startswith("media/"))
    cell.objects.objects[old_media] = b"ogg"
    cell.clock.at = T0
    await send({"type": "refunded", "customer_id": "cus_new"})
    assert upload.media_ref
    config = cell.config.space(SPACE_ID)
    settings = config.settings.model_copy(update={"retention_days": {"raw_content": 30}})
    cell.config.put(config.model_copy(update={"settings": settings}))
    listed: list[str] = []
    list_keys = cell.objects.list_keys

    async def counted(prefix: str) -> list[str]:
        listed.append(prefix)
        return await list_keys(prefix)

    monkeypatch.setattr(cell.objects, "list_keys", counted)
    await privacy.retention_sweep(Task(TaskName.RETENTION_SWEEP, SPACE_ID))

    kept = sorted(k for k in cell.objects.objects if k.startswith((f"raw/{SPACE_ID}/", f"media/{SPACE_ID}/")))
    assert old_media not in kept
    assert [json.loads(cell.objects.objects[k])["customer_id"] for k in kept] == ["cus_new"]
    assert not [p for p in listed if p.startswith(("raw/", "media/", "measure/raw/"))]
    assert sorted(cell.uow.store.module("privacy")["raw_objects"]) == kept


async def test_a_batch_that_rolls_back_takes_the_payload_it_stored(
    cell: Cell, make_privacy: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The index row rolls back with the batch: a payload left behind would be out of every erasure's reach.
    make_privacy()
    send = webhook(cell)
    ingest, _, _ = build(cell)

    async def broken(*_: Any) -> None:
        raise RuntimeError("the commit failed")

    monkeypatch.setattr(ingest, "_persist", broken)
    body = b'{"type": "paid", "id": "i-1", "at": "2026-09-22T16:59:00Z"}'
    with pytest.raises(RuntimeError):
        await ingest.ingest_webhook(BILLING_SOURCE, {}, body, {"token": "t0k"})
    assert send is not None
    assert not [k for k in cell.objects.objects if k.startswith(f"raw/{SPACE_ID}/")]
    assert not cell.uow.store.module("privacy").get("raw_objects")
