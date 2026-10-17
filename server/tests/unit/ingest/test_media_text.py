"""Text from files and images, read inside the cell (10, D.1): only the types the space allows, told by
their bytes, read on the in-cell models server, never an identity document, and always as a derived turn
that keeps its lines."""

import io
import zipfile
from uuid import UUID

import httpx
import pytest
from hypothesis import given
from hypothesis import strategies as st

from niadra.adapters.outbound.media_text import LocalMediaText, _pdf_text
from niadra.config.models import MediaSettings
from niadra.domain.ingest import media_text
from niadra.domain.ingest.masking import mask_text
from niadra.domain.ingest.media_text import (
    DOCX,
    JPEG,
    MAX_CHARS,
    PDF,
    PNG,
    PPTX,
    XLSX,
    ReadText,
    is_derived,
    office_type,
    one_line,
    parts,
    sniff,
    trimmed,
)
from niadra.domain.ingest.storage import media_key, media_ref
from niadra.domain.tasks import Task, TaskName
from niadra.testing.cell import SPACE_ID, WHATSAPP_SOURCE, Cell, sample_config
from tests.unit.flow.harness import Flow

PHONE = {"type": "phone_e164", "value": "+5511944443333"}


def pdf(text: str) -> bytes:
    """A one-page PDF with a text layer, written by hand so the test needs no PDF library."""
    stream = f"BT /F1 12 Tf 72 712 Td ({text}) Tj ET".encode()
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
        b"/Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out, offsets = b"%PDF-1.4\n", []
    for i, body in enumerate(objects, 1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    out += b"".join(f"{off:010d} 00000 n \n".encode() for off in offsets)
    out += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return out


class FakeReader:
    """The local reader with the models server replaced: a PDF's text layer is read for real, images and
    Office files give the text the test chose (None: this cell has no reader)."""

    def __init__(self, image_text: str | None, office_text: str | None = None) -> None:
        self.image = image_text
        self.office = office_text
        self.asked: list[str] = []
        self.windows: list[str] = []
        """Office text by window of pages (`first_page`), when the test gives more than one."""

    async def document_text(self, data: bytes, mime: str, first_page: int = 0) -> ReadText | None:
        self.asked.append(mime)
        if mime == PDF:
            return ReadText(_pdf_text(data), "pdf_text")
        if self.windows and mime not in (JPEG, PNG):
            following = first_page + 1 if first_page + 1 < len(self.windows) else None
            return ReadText(self.windows[first_page], "office", next_page=following)
        text = self.image if mime in (JPEG, PNG) else self.office
        if text is None:
            return None
        return ReadText(text, "ocr" if mime in (JPEG, PNG) else "office")


def _flow(cell: Cell, allowed: list[str], ocr: str | None = None, office: str | None = None) -> Flow:
    cell.config.put(sample_config().model_copy(update={"media": MediaSettings(extract_text_from=allowed)}))
    flow = Flow.build(cell)
    flow.ingest._media = FakeReader(ocr, office)
    flow.ingest._models = cell.models
    return flow


async def _send_media(flow: Flow, data: bytes, kind: str = "file") -> str:
    ref = media_ref(None, UUID("0192f5a0-0000-7000-8000-0000000f11e1"))
    key = media_key(SPACE_ID, ref)
    assert key is not None
    await flow.cell.objects.put(key, data)
    item = {
        "type": "event",
        "idempotency_key": f"m-{kind}",
        "channel": "whatsapp",
        "conversation_id": "wa-media",
        "handles": [PHONE],
        "speaker": {"role": "customer"},
        "content": {"type": kind, "media_ref": ref},
        "occurred_at": flow.cell.clock.now().isoformat(),
    }
    assert (await flow.send(WHATSAPP_SOURCE, item)).errors == []
    return ref


def _events(cell: Cell) -> list:  # type: ignore[type-arg]
    return [e.record for e in cell.uow.store.module("ingest")["events"].values()]


async def test_a_pdf_the_space_allows_becomes_a_derived_turn(cell: Cell) -> None:
    flow = _flow(cell, ["application/pdf"])
    await _send_media(flow, pdf("Segue a nota fiscal da compra de fibra"))
    await flow.drain(only={TaskName.MEDIA_TEXT})
    original, derived = sorted(_events(cell), key=lambda e: e.received_at)
    assert derived.text == "Segue a nota fiscal da compra de fibra"
    assert derived.content_meta == {
        "extracted_from": "pdf_text",
        "derived_from_event": str(original.event_id),
    }
    assert (derived.conversation_id, derived.speaker, derived.handle_ids) == (
        original.conversation_id,
        original.speaker,
        original.handle_ids,
    )
    [original] = [e for e in _events(cell) if e.event_id == original.event_id]
    assert original.content_meta["media_text"] == "pdf_text"
    # A retried task finds the mark and writes nothing twice.
    await flow.ingest.media_text(Task(TaskName.MEDIA_TEXT, SPACE_ID, {"event_id": str(original.event_id)}))
    assert len(_events(cell)) == 2


async def test_a_document_of_any_length_is_read_and_stored_whole(
    cell: Cell, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Past 50 pages or 100,000 characters the rest of a document was left out. Every window
    # of pages is read now, and a text past the limit of one turn is stored as several, in order.
    monkeypatch.setattr(media_text, "MAX_CHARS", 120)
    windows = [
        "\n".join(f"Cláusula {w}.{n}: o fornecedor entrega em 30 dias" for n in range(3)) for w in range(3)
    ]
    flow = _flow(cell, [DOCX], office="")
    flow.ingest._media.windows = windows  # type: ignore[union-attr]
    await _send_media(flow, office_file(MAIN.format("wordprocessingml.document")))
    await flow.drain(only={TaskName.MEDIA_TEXT})
    derived = sorted(
        (e for e in _events(cell) if (e.content_meta or {}).get("derived_from_event")),
        key=lambda e: e.content_meta["part"],
    )
    assert len(derived) > 1
    assert [e.content_meta["parts"] for e in derived] == [len(derived)] * len(derived)
    assert "\n".join(e.text for e in derived) == "\n".join(windows)
    assert all(len(e.text) <= 120 for e in derived)
    [original] = [e for e in _events(cell) if (e.content_meta or {}).get("media_text")]
    assert original.content_meta["media_text_parts"] == len(derived)
    assert original.content_meta["media_text_event"] == str(derived[0].event_id)


@pytest.mark.parametrize(
    ("allowed", "data", "kind", "ocr", "outcome"),
    [
        ([], b"%PDF-1.4", "file", None, None),  # off by default: no task at all
        (["image/png"], pdf("texto"), "file", None, "type_not_allowed"),
        (["image/png"], b"\x89PNG\r\n\x1a\n" + b"0" * 16, "image", None, "ocr_unavailable"),
        (
            ["image/png"],
            b"\x89PNG\r\n\x1a\n" + b"0" * 16,
            "image",
            "RG 12.345.678-9 CPF 529.982.247-25",
            "identity_document",
        ),
    ],
)
async def test_what_is_not_read_says_why(
    cell: Cell, allowed: list[str], data: bytes, kind: str, ocr: str | None, outcome: str | None
) -> None:
    flow = _flow(cell, allowed, ocr)
    await _send_media(flow, data, kind)
    ran = await flow.drain(only={TaskName.MEDIA_TEXT})
    [original] = _events(cell)
    assert (original.content_meta or {}).get("media_text") == outcome
    assert bool(ran) == bool(allowed)


async def test_an_image_the_space_allows_is_read_by_ocr(cell: Cell) -> None:
    flow = _flow(cell, ["image/jpeg"], ocr="Protocolo da visita: manhã de terça")
    await _send_media(flow, b"\xff\xd8\xff" + b"0" * 16, "image")
    await flow.drain(only={TaskName.MEDIA_TEXT})
    derived = next(e for e in _events(cell) if e.content_meta and e.content_meta.get("extracted_from"))
    assert (derived.text, derived.content_meta["extracted_from"]) == (
        "Protocolo da visita: manhã de terça",
        "ocr",
    )


def office_file(main: str, extra: dict[str, bytes] | None = None) -> bytes:
    """A minimal Office package: its content types and a main part, zipped by hand."""
    types = (
        '<?xml version="1.0" encoding="UTF-8"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/'
        f'content-types"><Override PartName="/main.xml" ContentType="{main}"/></Types>'
    )
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", types)
        archive.writestr("main.xml", "<document/>")
        for name, data in (extra or {}).items():
            archive.writestr(name, data)
    return out.getvalue()


MAIN = "application/vnd.openxmlformats-officedocument.{}.main+xml"


def test_types_are_read_from_the_bytes_not_the_upload() -> None:
    assert sniff(pdf("x")) == "application/pdf"
    assert sniff(b"\xff\xd8\xff\xe0") == "image/jpeg"
    assert sniff(b"GIF89a") is None
    assert sniff(office_file(MAIN.format("wordprocessingml.document"))) == DOCX
    assert sniff(office_file(MAIN.format("spreadsheetml.sheet"))) == XLSX
    assert sniff(office_file(MAIN.format("presentationml.presentation"))) == PPTX
    # A ZIP is not an Office file by being a ZIP, nor by its name.
    assert sniff(office_file("application/xml")) is None
    assert sniff(b"PK\x03\x04" + b"0" * 64) is None


def test_macro_enabled_office_files_are_never_read() -> None:
    assert sniff(office_file("application/vnd.ms-word.document.macroEnabled.main+xml")) is None
    assert sniff(office_file("application/vnd.ms-excel.sheet.macroEnabled.main+xml")) is None
    assert sniff(office_file("application/vnd.ms-powerpoint.presentation.macroEnabled.main+xml")) is None
    vba = {"xl/vbaProject.bin": b"\xd0\xcf\x11\xe0"}
    assert sniff(office_file(MAIN.format("spreadsheetml.sheet"), vba)) is None


def test_a_zip_bomb_is_refused_by_its_sizes_and_entries(monkeypatch: pytest.MonkeyPatch) -> None:
    # 101 MiB of zeros deflate to about 100 KiB: the declared size gives it away before anything inflates.
    bomb = office_file(MAIN.format("wordprocessingml.document"), {"word/big.xml": b"\0" * (101 << 20)})
    assert len(bomb) < 1 << 20
    assert office_type(bomb) is None
    many = office_file(MAIN.format("wordprocessingml.document"), {f"p{i}.xml": b"x" for i in range(10)})
    assert office_type(many) == DOCX
    monkeypatch.setattr(media_text, "ZIP_MAX_ENTRIES", 5)
    assert office_type(many) is None


def test_trimmed_keeps_the_lines_and_collapses_spaces_within_each() -> None:
    read = "Nota fiscal  2026-004817\r\n\n   Roteador  Wi-Fi\t1  R$ 489,90  \n\n\x0cTotal: R$ 843,30"
    assert trimmed(read) == "Nota fiscal 2026-004817\nRoteador Wi-Fi 1 R$ 489,90\nTotal: R$ 843,30"
    assert trimmed("x" * (MAX_CHARS + 10)) == "x" * (MAX_CHARS + 10)


def test_parts_are_the_whole_text_in_pieces_of_at_most_the_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(media_text, "MAX_CHARS", 10)
    assert parts("abc\ndefgh\nijklmnopqrstu\nv") == ["abc\ndefgh", "ijklmnopqr", "stu\nv"]
    assert parts("") == []
    assert parts("short") == ["short"]


@given(st.text(alphabet=st.sampled_from("ab1 \t\n\r\x0b\x0c\x1c\u2028\u3000ç"), max_size=300))
def test_the_lines_joined_are_the_one_line_text_of_before(text: str) -> None:
    assert one_line(trimmed(text)) == " ".join(text.split())


def test_masking_reads_a_derived_texts_breaks_as_spaces() -> None:
    read = "Cartão\n4111 1111\n1111 1111\nSenha:\nabc123\nPedido 45778204"
    assert mask_text(read, "pt", lines_as_spaces=True).text == (
        "Cartão\n[retido:cartão ****1111]\nSenha:\n[retido:senha]\nPedido 45778204"
    )
    assert mask_text(read, "pt").text == read  # a typed message keeps the rules it had
    # Exactly what the one-line text of before gave, with the breaks back in place.
    assert one_line(mask_text(read, "pt", lines_as_spaces=True).text) == mask_text(one_line(read), "pt").text


def test_a_derived_event_is_told_by_its_metadata() -> None:
    assert is_derived({"extracted_from": "office", "derived_from_event": "e"})
    assert not is_derived({"media_text": "office"})
    assert not is_derived(None)


async def test_an_office_file_the_space_allows_becomes_a_derived_turn_with_its_lines(cell: Cell) -> None:
    lines = "Case reference: CS-2026-78412\nItem | Amount\nAquaPure 300 filter | $249.00"
    flow = _flow(cell, [DOCX], office=lines + "\n\n")
    await _send_media(flow, office_file(MAIN.format("wordprocessingml.document")))
    await flow.drain(only={TaskName.MEDIA_TEXT})
    original, derived = sorted(_events(cell), key=lambda e: e.received_at)
    assert derived.text == lines
    assert derived.content_meta == {"extracted_from": "office", "derived_from_event": str(original.event_id)}
    [original] = [e for e in _events(cell) if e.event_id == original.event_id]
    assert original.content_meta["media_text"] == "office"


async def test_an_office_type_the_space_does_not_list_is_not_read(cell: Cell) -> None:
    flow = _flow(cell, ["application/pdf", XLSX], office="Planilha")
    await _send_media(flow, office_file(MAIN.format("wordprocessingml.document")))
    await flow.drain(only={TaskName.MEDIA_TEXT})
    [original] = _events(cell)
    assert original.content_meta["media_text"] == "type_not_allowed"
    assert flow.ingest._media.asked == []  # type: ignore[union-attr]


async def test_an_identity_document_split_over_lines_is_still_never_stored(cell: Cell) -> None:
    flow = _flow(cell, [PNG], ocr="REPUBLICA FEDERATIVA\nRG\n12.345.678-9\nCPF\n529.982.247-25")
    await _send_media(flow, b"\x89PNG\r\n\x1a\n" + b"0" * 16, "image")
    await flow.drain(only={TaskName.MEDIA_TEXT})
    [original] = _events(cell)
    assert original.content_meta["media_text"] == "identity_document"


def reader_with(handler: object) -> LocalMediaText:
    reader = LocalMediaText("http://models")
    reader._http = httpx.AsyncClient(
        base_url="http://models",
        transport=httpx.MockTransport(handler),  # type: ignore[arg-type]
    )
    return reader


async def test_the_local_reader_asks_the_models_server_for_the_document() -> None:
    seen: list[httpx.Request] = []

    def answer(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"text": "a\nb", "source": "pdf_ocr", "pages": 1, "ocr_pages": 1})

    reader = reader_with(answer)
    assert await reader.document_text(pdf("x"), PDF) == ReadText("a\nb", "pdf_ocr")
    assert seen[0].url.path == "/v1/document-text"
    assert seen[0].url.params["type"] == PDF
    # A PDF may be long and scanned (3 s a page on top of 20 s), and a photo may wait behind one.
    assert seen[0].extensions["timeout"]["read"] == 170.0
    await reader.document_text(b"\x89PNG", PNG)
    assert seen[1].extensions["timeout"]["read"] == 170.0
    await reader.aclose()


@pytest.mark.parametrize("status", [413, 422])
async def test_a_file_the_server_cannot_read_has_no_text(status: int) -> None:
    reader = reader_with(lambda r: httpx.Response(status))
    assert await reader.document_text(b"PK", DOCX) == ReadText("", "office")
    assert await reader.document_text(b"\x89PNG", PNG) == ReadText("", "ocr")
    await reader.aclose()


async def test_without_the_reader_a_pdf_is_read_here_and_an_image_by_ocr() -> None:
    def answer(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/ocr":
            return httpx.Response(200, json={"lines": ["a", "b"]})
        return httpx.Response(501)

    reader = reader_with(answer)
    assert await reader.document_text(pdf("ok"), PDF) == ReadText("ok", "pdf_text")
    assert await reader.document_text(b"\x89PNG", PNG) == ReadText("a\nb", "ocr")
    assert await reader.document_text(b"PK", DOCX) is None  # nothing here reads Office files
    await reader.aclose()


async def test_no_engine_at_all_is_no_reader() -> None:
    reader = reader_with(lambda r: httpx.Response(501))
    assert await reader.document_text(b"\x89PNG", PNG) is None
    assert await reader.document_text(pdf("ok"), PDF) == ReadText("ok", "pdf_text")
    await reader.aclose()
