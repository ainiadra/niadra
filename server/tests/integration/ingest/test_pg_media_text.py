"""Text read from an Office file, on PostgreSQL: the derived event keeps its lines, the card split over
two of them is held as it was when that text was stored on one line, and the original is marked."""

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from niadra.adapters.memory.fakes import (
    DeterministicModels,
    DictSecrets,
    FixedClock,
    InMemoryHotLayer,
    InMemoryObjectStore,
    StaticConfig,
)
from niadra.app.ingest import IngestService
from niadra.app.sessions import SessionService
from niadra.config.models import MediaSettings
from niadra.contracts.ingest import validate_items
from niadra.domain.ingest.media_text import XLSX
from niadra.domain.ingest.storage import media_key, media_ref
from niadra.domain.tasks import Task, TaskName
from niadra.testing.cell import principal, sample_config
from tests.integration.conftest import CloningUnitOfWork
from tests.unit.ingest.fakes import FakeIdentity
from tests.unit.ingest.test_media_text import MAIN, FakeReader, office_file

NOW = datetime(2026, 9, 27, 10, 0, tzinfo=UTC)
SINCE, UNTIL = NOW - timedelta(minutes=1), NOW + timedelta(minutes=1)
SHEET = (
    "Extrato de conta, Banco Horizonte Digital\n"
    "Data | Descrição | Valor (R$)\n"
    "08/09/2026 | Boleto Energia Clara | -312,47\n"
    "Cartão\n4111 1111\n1111 1111"
)


async def test_an_xlsx_becomes_a_masked_derived_turn_that_keeps_its_rows(pg_uow: CloningUnitOfWork) -> None:
    space = uuid4()
    clock = FixedClock(NOW)
    config = sample_config(space).model_copy(update={"media": MediaSettings(extract_text_from=[XLSX])})
    objects = InMemoryObjectStore()
    service = IngestService(
        pg_uow,  # type: ignore[arg-type]
        FakeIdentity(),  # type: ignore[arg-type]
        SessionService(pg_uow, clock, StaticConfig(config)),  # type: ignore[arg-type]
        pg_uow._keys,
        InMemoryHotLayer(),
        clock,
        StaticConfig(config),
        objects,
        DictSecrets(),  # type: ignore[arg-type]
        media=FakeReader(None, office_text=SHEET),
        models=DeterministicModels(),
    )
    ref = media_ref(None, UUID("0192f5a0-0000-7000-8000-0000000f11e3"))
    key = media_key(space, ref)
    assert key is not None
    await objects.put(key, office_file(MAIN.format("spreadsheetml.sheet")))
    item: dict[str, Any] = {
        "type": "event",
        "idempotency_key": "sheet-1",
        "channel": "whatsapp",
        "conversation_id": "wa-sheet",
        "handles": [{"type": "phone_e164", "value": "+5511944443333"}],
        "speaker": {"role": "customer"},
        "content": {"type": "file", "media_ref": ref},
        "occurred_at": NOW.isoformat(),
    }
    items, errors = validate_items([item])
    assert not errors
    await service.ingest_items(principal(config=config), items)
    async with pg_uow(space) as uow:
        [original] = await uow.ingest.events_between(conversation_id="wa-sheet", since=SINCE, until=UNTIL)

    await service.media_text(Task(TaskName.MEDIA_TEXT, space, {"event_id": str(original.event_id)}))

    async with pg_uow(space) as uow:
        stored = await uow.ingest.events_between(conversation_id="wa-sheet", since=SINCE, until=UNTIL)
    derived = next(e for e in stored if e.event_id != original.event_id)
    marked = next(e for e in stored if e.event_id == original.event_id)
    assert derived.text is not None
    assert derived.text.splitlines() == [
        "Extrato de conta, Banco Horizonte Digital",
        "Data | Descrição | Valor (R$)",
        "08/09/2026 | Boleto Energia Clara | -312,47",
        "Cartão",
        "[retido:cartão ****1111]",
    ]
    assert derived.content_meta == {
        "extracted_from": "office",
        "derived_from_event": str(original.event_id),
        "masked": {"card": 1},
    }
    assert (derived.conversation_id, derived.handle_ids) == (original.conversation_id, original.handle_ids)
    assert marked.content_meta is not None
    assert marked.content_meta["media_text"] == "office"
    assert marked.content_meta["media_text_event"] == str(derived.event_id)
