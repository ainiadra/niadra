"""What one read bounds says it goes on: the whole text is in the history and the JSON view."""

from dataclasses import replace
from datetime import UTC, datetime
from uuid import uuid4

from niadra.app.serve import _live_turn
from niadra.domain.compile.state_view import CONTENT_CHARS, ObjectLine, render
from niadra.domain.compile.templates import Formatter
from niadra.domain.ingest.hot import MAX_HOT_TEXT, turn_document
from niadra.domain.policy import Decision
from niadra.domain.policy.rules import Effect
from niadra.domain.records import EventRecord
from niadra.domain.serve.live import LiveLine
from niadra.domain.vocabulary import EventKind, Speaker

T0 = datetime(2026, 10, 8, 12, 0, tzinfo=UTC)


def test_a_content_quote_longer_than_a_line_says_it_continues_in_the_history() -> None:
    movement = "Sentença. JULGO IMPROCEDENTE o pedido, por ausência de prova da cobrança indevida. " * 10
    assert len(movement) > CONTENT_CHARS
    text = render(
        [ObjectLine(ref="legal_case court:1", content=[("Última movimentação", movement)])], "pt-BR", "UTC"
    )
    assert text is not None
    assert "(continua no histórico)" in text
    short = render([ObjectLine(ref="legal_case court:1", content=[("Nota", "Curta.")])], "pt-BR", "UTC")
    assert short is not None
    assert "continua" not in short


def test_a_live_turn_the_cache_keeps_only_the_start_of_says_it_continues() -> None:
    long = "Segue o relatório completo. " * 400
    assert len(long) > MAX_HOT_TEXT
    record = EventRecord(
        event_id=uuid4(),
        kind=EventKind.MESSAGE,
        source_id=uuid4(),
        channel="email",
        speaker=Speaker.CUSTOMER,
        occurred_at=T0,
        received_at=T0,
        handle_ids=(uuid4(),),
        text=long,
    )
    doc = turn_document(record)
    assert doc["text_cut"] == len(long)
    turn = _live_turn(
        LiveLine(record.event_id, doc, record.handle_ids), Decision(Effect.ALLOW), Formatter("UTC", "pt-BR")
    )
    assert turn.text.endswith("(continua no histórico)")
    assert "text_cut" not in turn_document(replace(record, text="Oi"))
