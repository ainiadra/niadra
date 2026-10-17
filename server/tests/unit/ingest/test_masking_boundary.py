"""Where ingestion applies the masks (WP-A): every event, its raw payload, the hot layer, text read from
media, and the pass that masks what was stored before the rules existed."""

import hashlib
import itertools
import json
from datetime import timedelta
from typing import Any
from uuid import UUID

import orjson
import pytest

from niadra.adapters.memory.ingest import InMemoryEventRepository
from niadra.app.archive import ArchiveReader, content_aad
from niadra.app.control_config import validate_document
from niadra.app.ingest import IngestService
from niadra.config.models import (
    HandleExpr,
    IngestMaskingSettings,
    Mapping,
    MappingRule,
    WebhookAuth,
)
from niadra.contracts.ingest import validate_items
from niadra.domain.common.errors import InvalidInput
from niadra.domain.common.ids import uuid7
from niadra.domain.ingest.events import NewEvent
from niadra.domain.ingest.hot import hot_aad, unpack_envelope
from niadra.domain.ingest.masking import ALL_TYPES, Held
from niadra.domain.ingest.storage import raw_key
from niadra.domain.privacy.files import json_line
from niadra.domain.records import EventRecord
from niadra.domain.tasks import Task, TaskName
from niadra.domain.vocabulary import EventKind, HandleType, Speaker
from niadra.testing.cell import BILLING_SOURCE, SPACE_ID, WHATSAPP_SOURCE, Cell, principal, sample_config
from tests.unit.ingest.fakes import build, message

_MS = itertools.count()
PAN = "4111111111111111"
ORDER = "4532015112830366"  # passes the card check too: only its word keeps it an order


def repo(cell: Cell) -> InMemoryEventRepository:
    return InMemoryEventRepository(cell.uow.store)


def texts(cell: Cell) -> list[str | None]:
    return [e.record.text for e in sorted(repo(cell).stored, key=lambda e: e.record.event_id)]


def by_key(cell: Cell) -> dict[str, str | None]:
    return {e.idempotency_key: e.record.text for e in repo(cell).stored}


def digits_of(blob: Any) -> str:
    return "".join(ch for ch in repr(blob) if ch.isdigit())


async def hot_texts(cell: Cell) -> list[str]:
    found = []
    for (space_id, _), turns in cell.hot.turns.items():
        for turn in turns.values():
            version, ct = unpack_envelope(turn.envelope_ct)
            doc = json.loads(await cell.keys.decrypt(space_id, ct, hot_aad(space_id, turn.event_id), version))
            found.append(doc.get("text", ""))
    return found


async def send(service: IngestService, *items: dict[str, Any], raw: bytes | None = None) -> Any:
    valid, errors = validate_items(list(items))
    assert not errors
    return await service.ingest_items(principal(), valid, raw_body=raw)


async def test_a_batch_stores_and_serves_only_masked_text(cell: Cell) -> None:
    service, _, _ = build(cell)
    agent = {"speaker": {"role": "ai_agent"}}
    response = await send(
        service,
        message("m1", "Qual o número do cartão?", **agent),
        message("m2", PAN),
        message("m3", "cvv 123, senha: hunter2. Protocolo 45778204, pedido " + ORDER),
    )

    assert response.masked == {"card": 1, "cvv": 1, "password": 1}
    assert by_key(cell) == {
        "m1": "Qual o número do cartão?",
        "m2": "[retido:cartão ****1111]",
        "m3": f"cvv [retido:cvv], senha: [retido:senha]. Protocolo 45778204, pedido {ORDER}",
    }
    metas = {e.idempotency_key: e.record.content_meta or {} for e in repo(cell).stored}
    assert metas["m2"]["masked"] == {"card": 1}
    assert metas["m3"]["masked"] == {"cvv": 1, "password": 1}
    live = await hot_texts(cell)
    assert live
    assert PAN not in digits_of(live)
    assert "hunter2" not in repr(live)


async def test_the_turn_before_is_read_from_the_log_when_it_came_in_an_earlier_batch(cell: Cell) -> None:
    service, _, _ = build(cell)
    await send(service, message("m1", "Me passa o número do cartão, por favor", speaker={"role": "ai_agent"}))
    await send(service, message("m2", PAN))
    await send(service, message("m3", PAN, conversation_id="conv-other"))

    assert by_key(cell)["m2"] == "[retido:cartão ****1111]"
    assert by_key(cell)["m3"] == PAN


async def test_fields_actions_and_the_raw_payload_are_masked_and_the_order_kept(cell: Cell) -> None:
    service, _, _ = build(cell)
    paid = {
        "type": "event",
        "kind": "system_event",
        "idempotency_key": "s1",
        "channel": "erp",
        "canonical_type": "invoice.paid",
        "handles": [{"type": "system_id", "value": "C-1", "scope": "erp"}],
        "speaker": {"role": "system"},
        "fields": {
            "card_number": PAN,
            "description": "pago no cartão 4111 1111 1111 1111",
            "order_id": ORDER,
        },
        "occurred_at": "2026-09-22T16:59:00Z",
    }
    credit = {
        "type": "event",
        "kind": "action",
        "idempotency_key": "a1",
        "channel": "erp",
        "handles": [{"type": "system_id", "value": "C-1", "scope": "erp"}],
        "speaker": {"role": "ai_agent"},
        "action": {"operation": "credit", "result": "estornado no cartão 4111 1111 1111 1111"},
        "occurred_at": "2026-09-22T16:59:00Z",
    }
    body = orjson.dumps({"items": [paid, credit]})
    response = await send(service, paid, credit, raw=body)

    assert response.masked == {"card": 3}
    by_kind = {e.record.kind: e for e in repo(cell).stored}
    event = by_kind[EventKind.SYSTEM_EVENT]
    assert event.record.fields == {
        "card_number": "[retido:cartão ****1111]",
        "description": "pago no cartão [retido:cartão ****1111]",
        "order_id": ORDER,
    }
    assert by_kind[EventKind.ACTION].record.action == {
        "operation": "credit",
        "result": "estornado no cartão [retido:cartão ****1111]",
    }
    assert event.raw_ref is not None
    stored = cell.objects.objects[event.raw_ref]
    assert PAN not in digits_of(orjson.loads(stored)).replace(ORDER, "")
    assert orjson.loads(stored)["items"][0]["fields"]["order_id"] == ORDER


def _billing_mapping() -> Mapping:
    return Mapping(
        source_id=BILLING_SOURCE,
        version="m1",
        auth=WebhookAuth(mode="url_token", secret_ref=f"spaces/{SPACE_ID}/webhook-in/wh"),
        rules=[
            MappingRule(
                match="type == 'paid'",
                canonical_type="invoice.paid",
                idempotency_key="id",
                occurred_at="at",
                channel="erp",
                handles=[HandleExpr(type=HandleType.SYSTEM_ID, value="customer", scope="erp")],
                fields={"card": "card", "order": "order"},
            )
        ],
    )


async def test_webhook_bodies_are_masked_mapped_unmapped_or_not_json(cell: Cell) -> None:
    cell.config.put(sample_config().model_copy(update={"mappings": [_billing_mapping()]}))
    cell.secrets.values[f"spaces/{SPACE_ID}/webhook-in/wh"] = "t0k"
    service, _, _ = build(cell)
    token = {"token": "t0k"}
    mapped = orjson.dumps(
        {
            "type": "paid",
            "id": "e1",
            "at": "2026-09-22T16:00:00Z",
            "customer": "C-1",
            "card": PAN,
            "order": ORDER,
        }
    )
    unmapped = orjson.dumps({"type": "refund", "note": "cartão 4111 1111 1111 1111"})

    await service.ingest_webhook(BILLING_SOURCE, {}, mapped, token)
    await service.ingest_webhook(BILLING_SOURCE, {}, unmapped, token)
    with pytest.raises(InvalidInput):
        await service.ingest_webhook(BILLING_SOURCE, {}, b"cartao 4111 1111 1111 1111 senha: x", token)

    [event] = repo(cell).stored
    assert event.record.fields == {"card": "[retido:cartão ****1111]", "order": ORDER}
    bodies = [v for k, v in cell.objects.objects.items() if k.startswith("raw/")]
    assert len(bodies) == 3
    assert all(PAN not in body.decode(errors="replace") for body in bodies)
    assert any(ORDER in body.decode() for body in bodies)


async def test_text_read_from_a_card_photo_is_masked_before_it_is_stored(cell: Cell) -> None:
    from niadra.config.models import MediaSettings
    from niadra.domain.ingest.storage import media_key, media_ref
    from tests.unit.flow.harness import Flow
    from tests.unit.ingest.test_media_text import FakeReader

    cell.config.put(
        sample_config().model_copy(update={"media": MediaSettings(extract_text_from=["image/png"])})
    )
    flow = Flow.build(cell)
    flow.ingest._media = FakeReader("VISA 4111 1111 1111 1111 VALID THRU 12/29")
    flow.ingest._models = cell.models
    ref = media_ref(None, UUID("0192f5a0-0000-7000-8000-0000000f11e2"))
    key = media_key(SPACE_ID, ref)
    assert key is not None
    await cell.objects.put(key, b"\x89PNG\r\n\x1a\n" + b"0" * 16)
    item = message("img", content={"type": "image", "media_ref": ref})
    assert (await flow.send(WHATSAPP_SOURCE, item)).errors == []
    await flow.drain(only={TaskName.MEDIA_TEXT})

    derived = next(e.record for e in repo(cell).stored if (e.record.content_meta or {}).get("extracted_from"))
    assert derived.text == "VISA [retido:cartão ****1111] VALID THRU 12/29"
    assert derived.content_meta is not None
    assert derived.content_meta["masked"] == {"card": 1}


def _stored(
    text: str,
    *,
    fields: dict[str, Any] | None = None,
    raw_ref: str | None = None,
    content_meta: dict[str, Any] | None = None,
) -> NewEvent:
    """An event as ingestion wrote it before the rules existed: unmasked."""
    now = Cell().clock.now()
    # Received a while before the pass starts, one millisecond apart so the ids keep their order.
    record = EventRecord(
        event_id=uuid7(int(now.timestamp() * 1000) - 60_000 + next(_MS)),
        kind=EventKind.MESSAGE,
        source_id=WHATSAPP_SOURCE,
        channel="whatsapp",
        speaker=Speaker.CUSTOMER,
        occurred_at=now,
        received_at=now,
        handle_ids=(UUID(int=7),),
        text=text,
        conversation_id="old-1",
        fields=fields or {},
        content_meta=content_meta,
    )
    return NewEvent(SPACE_ID, record, f"k-{record.event_id}", raw_ref=raw_ref)


async def _plant(cell: Cell, service: IngestService, *events: NewEvent) -> None:
    async with cell.uow(SPACE_ID) as uow:
        await uow.ingest.insert_events(list(events))
    await service._add_hot(SPACE_ID, [e.record for e in events])


def _pending(cell: Cell) -> list[Task]:
    return [t for t in cell.uow.store.data["pipeline"]["outbox"] if t.name is TaskName.MASK_EVENTS]


async def test_the_pass_masks_what_was_stored_before_the_rules(cell: Cell) -> None:
    service, _, _ = build(cell)
    key = raw_key(SPACE_ID, UUID(int=7), cell.clock.now(), uuid7())
    await cell.objects.put(key, orjson.dumps({"text": f"cartão {PAN}", "order": ORDER}))
    await _plant(
        cell,
        service,
        _stored("Qual o número do cartão?"),
        _stored(PAN, raw_ref=key),
        _stored(f"protocolo 45778204, pedido {ORDER}"),
        _stored("ok", fields={"password": "abc"}),
    )
    before = await repo(cell).export_rows("events", after_seq=0, upto_seq=10**6, limit=10)

    assert await service.watch_masking(SPACE_ID) == 1
    assert await service.watch_masking(SPACE_ID) == 0  # one pass per change of the rules
    [task] = _pending(cell)
    await service.mask_events(task)

    assert texts(cell) == [
        "Qual o número do cartão?",
        "[retido:cartão ****1111]",
        f"protocolo 45778204, pedido {ORDER}",
        "ok",
    ]
    fields = [e.record.fields for e in sorted(repo(cell).stored, key=lambda e: e.record.event_id)]
    assert fields[3] == {"password": "[retido:senha]"}
    assert orjson.loads(cell.objects.objects[key]) == {
        "text": "cartão [retido:cartão ****1111]",
        "order": ORDER,
    }
    live = await hot_texts(cell)
    assert len(live) == 4
    assert PAN not in digits_of(live)
    # The continuous export sends the masked rows again: only they moved.
    after = await repo(cell).export_rows(
        "events", after_seq=max(r["change_seq"] for r in before), upto_seq=10**6, limit=10
    )
    assert sorted(r["text"] or "" for r in after) == ["[retido:cartão ****1111]", "ok"]
    # A second run of the same page changes nothing.
    await service.mask_events(task)
    assert (
        await repo(cell).export_rows(
            "events", after_seq=max(r["change_seq"] for r in after), upto_seq=10**6, limit=10
        )
    ) == []


SPLIT_CARD = "Cartão de crédito\n4111 1111\n1111 1111\nSenha:\nabc123"
DERIVED = {"extracted_from": "pdf_ocr", "derived_from_event": str(UUID(int=9))}


async def test_the_pass_reads_the_line_breaks_of_text_read_from_a_file_as_spaces(cell: Cell) -> None:
    # A scan's lines were stored on one line before, where these values were held; they still are. A
    # message someone typed over several lines keeps the rules it always had.
    service, _, _ = build(cell)
    await _plant(cell, service, _stored(SPLIT_CARD, content_meta=DERIVED), _stored(SPLIT_CARD))
    await service.watch_masking(SPACE_ID)
    [task] = _pending(cell)
    await service.mask_events(task)
    derived, typed = texts(cell)
    assert derived == "Cartão de crédito\n[retido:cartão ****1111]\nSenha:\n[retido:senha]"
    assert typed == SPLIT_CARD


async def test_the_pass_pages_and_stops_when_the_rules_change_again(
    cell: Cell, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("niadra.app.ingest.MASK_PAGE", 2)
    service, _, _ = build(cell)
    await _plant(cell, service, *[_stored(f"cartão {PAN}") for _ in range(5)])
    await service.watch_masking(SPACE_ID)
    [first] = _pending(cell)

    await service.mask_events(first)

    [(following, _, reason)] = cell.uow.store.data["pipeline"]["scheduled"]
    assert reason == "delay"
    assert following.payload["after"]
    assert following.payload["masked"] == 2
    await service.mask_events(following)
    assert texts(cell).count("cartão [retido:cartão ****1111]") == 4

    # The security team drops a type: a new pass starts, and the old one's pages stop.
    settings = IngestMaskingSettings(types=[Held.CARD, Held.CVV, Held.PASSWORD])
    cell.config.put(sample_config().model_copy(update={"ingest_masking": settings}))
    assert await service.watch_masking(SPACE_ID) == 1
    await service.mask_events(Task(TaskName.MASK_EVENTS, SPACE_ID, {**following.payload, "after": None}))
    assert texts(cell).count(f"cartão {PAN}") == 1


async def test_archived_rows_are_masked_when_read(cell: Cell) -> None:
    record = _stored("meu cartão é 4111 1111 1111 1111").record
    content, version = await cell.keys.encrypt(
        SPACE_ID, json_line(record).encode(), content_aad(record.event_id)
    )
    row = {"event_id": str(record.event_id), "dek_version": version, "content_ct": content}

    read = await ArchiveReader(cell.objects, cell.keys, cell.config).record(SPACE_ID, row)
    plain = await ArchiveReader(cell.objects, cell.keys).record(SPACE_ID, row)

    assert read.text == "meu cartão é [retido:cartão ****1111]"
    assert read.content_meta == {"masked": {"card": 1}}
    assert plain.text == read.text  # without a configuration every rule applies


async def test_archived_text_read_from_a_file_is_masked_across_its_lines(cell: Cell) -> None:
    record = _stored(SPLIT_CARD, content_meta=DERIVED).record
    content, version = await cell.keys.encrypt(
        SPACE_ID, json_line(record).encode(), content_aad(record.event_id)
    )
    row = {"event_id": str(record.event_id), "dek_version": version, "content_ct": content}
    read = await ArchiveReader(cell.objects, cell.keys, cell.config).record(SPACE_ID, row)
    assert read.text == "Cartão de crédito\n[retido:cartão ****1111]\nSenha:\n[retido:senha]"


def test_the_settings_default_to_every_type_and_the_regulado_floor_holds() -> None:
    config = sample_config()
    assert config.masking_types() == ALL_TYPES
    fewer = config.model_copy(update={"ingest_masking": IngestMaskingSettings(types=[Held.SECRET])})
    assert fewer.masking_types() == {Held.SECRET}
    regulated = fewer.model_copy(update={"tier": "t2"})
    assert regulated.masking_types() == {Held.CARD, Held.CVV, Held.PASSWORD, Held.SECRET}
    assert validate_document("ingest-masking", {"types": ["cvv", "card", "card"]}) == {
        "types": ["card", "cvv"]
    }
    with pytest.raises(InvalidInput):
        validate_document("ingest-masking", {"types": ["cpf"]})


async def test_the_status_counts_what_was_held_by_type_never_the_values(cell: Cell) -> None:
    from niadra.contracts.ingest import IngestStatusRequest
    from tests.unit.flow.harness import Flow

    flow = Flow.build(cell)
    at = cell.clock.now().isoformat()
    item = message("m1", f"cartão {PAN} e senha: hunter2", conversation_id="wa-9", occurred_at=at)
    assert (await flow.send(WHATSAPP_SOURCE, item)).masked == {"card": 1, "password": 1}

    status = await flow.extract.status(principal(), IngestStatusRequest(conversation_id="wa-9"))

    assert status.state == "open"
    assert status.masked == {"card": 1, "password": 1}
    other = await flow.extract.status(principal(), IngestStatusRequest(conversation_id="wa-10"))
    assert other.masked is None


async def test_the_pass_keeps_watching_the_end_of_the_log_while_a_deploy_rolls(cell: Cell) -> None:
    service, _, _ = build(cell)
    await service.watch_masking(SPACE_ID)
    [task] = _pending(cell)
    scheduled = cell.uow.store.data["pipeline"]["scheduled"]

    await service.mask_events(task)
    [(tail, due, _)] = scheduled
    assert due == cell.clock.now() + timedelta(minutes=1)
    # A process that has not read the new rules stores one more unmasked turn after the pass started.
    await _plant(cell, service, _stored(f"cartão {PAN}"))
    cell.clock.advance(minutes=16)
    scheduled.clear()
    await service.mask_events(tail)

    assert texts(cell) == ["cartão [retido:cartão ****1111]"]
    assert scheduled == []  # past the window: the pass is done


async def test_a_jsonl_upload_waits_masked_and_keeps_its_rows(cell: Cell) -> None:
    from niadra.domain.ingest.files import FileFormat
    from tests.unit.ingest.test_files import chunks

    service, _, _ = build(cell)
    line = orjson.dumps(message("f1", f"cartão {PAN}", occurred_at="2026-09-22T16:00:00Z"))
    body = line + b"\n\nnot json, cartao 4111 1111 1111 1111\n" + orjson.dumps(message("f2", "ok")) + b"\n"

    record = await service.upload_file(principal(), FileFormat.JSONL, chunks(body, 64))

    parts = [v for k, v in cell.objects.objects.items() if k.startswith("imports/")]
    staged = b"".join(parts)
    assert PAN not in staged.decode()
    assert staged.count(b"\n") == body.count(b"\n")
    assert record.sha256 == hashlib.sha256(body).hexdigest()  # the file as sent
    await service.import_file(Task(TaskName.IMPORT_FILE, SPACE_ID, {"import_id": str(record.import_id)}))
    assert by_key(cell)["f1"] == "cartão [retido:cartão ****1111]"
