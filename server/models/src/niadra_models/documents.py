"""The text of a document (10, D.1): PDF, JPEG, PNG, DOCX, XLSX and PPTX, read locally in the OCR child
(`niadra_models.ocr`), one child per document.

- PDF: each page's text layer with pypdf; a page with a ruled table is read in pypdf's layout mode, so a
  table row stays one line; a page without a text layer is rendered with pypdfium2 and read by OCR.
- JPEG and PNG: OCR, its boxes grouped into visual rows.
- DOCX, XLSX, PPTX: python-docx, openpyxl (read-only, cached values, never a formula or a macro) and
  python-pptx, one line per paragraph and one line per table row, cells joined by " | ".

The type comes from the bytes, never from what the sender declared. An Office file is a ZIP, opened only
within an entry count and a total uncompressed size; macro-enabled files are not read.
"""

import io
import math
import re
import zipfile
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date, datetime, time
from typing import Any

from niadra_models import ocr

PDF = "application/pdf"
JPEG = "image/jpeg"
PNG = "image/png"
DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
PPTX = "application/vnd.openxmlformats-officedocument.presentationml.presentation"
TYPES = frozenset({PDF, JPEG, PNG, DOCX, XLSX, PPTX})

MAX_BYTES = 20 * 1024 * 1024
MAX_PAGES = 50
"""PDF pages, slides or sheets one request reads: the answer's `next_page` is where the next window starts,
so the cell reads a document of any length whole, a window at a time."""
MIN_PAGE_CHARS = 3
"""A PDF page with less text than this has no text layer worth the name: it is read by OCR."""
RENDER_DPI = 200
ZIP_MAX_ENTRIES = 2_000
ZIP_MAX_UNCOMPRESSED = 100 * 1024 * 1024
"""Python's zipfile never inflates an entry past its declared size, so a sum of declared sizes bounds
what the readers can inflate; overlapping entries count once per entry."""
_CONTENT_TYPES_MAX = 1024 * 1024
_MAIN_PARTS = {
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml": DOCX,
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml": XLSX,
    "application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml": PPTX,
}
_CONTENT_TYPE = re.compile(rb"""ContentType\s*=\s*["']([^"']+)["']""")
_MAX_GROUP_DEPTH = 8


class UnreadableDocumentError(Exception):
    """The document is damaged, encrypted or not what its bytes first said."""


@dataclass(frozen=True, slots=True)
class DocumentText:
    text: str
    source: str
    """`pdf_text` (the text layer alone), `pdf_ocr` (at least one page read by OCR), `ocr` or `office`."""
    pages: int
    """Pages, slides or sheets this window read."""
    ocr_pages: int = 0
    next_page: int | None = None
    """Where the next window of `MAX_PAGES` starts; None when this one reached the end."""


def sniff(data: bytes) -> str | None:
    if data.startswith(b"%PDF-"):
        return PDF
    if data.startswith(b"\xff\xd8\xff"):
        return JPEG
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return PNG
    if data.startswith(b"PK\x03\x04"):
        return office_type(data)
    return None


def office_type(data: bytes) -> str | None:
    """DOCX, XLSX or PPTX by the main part `[Content_Types].xml` declares; None for any other ZIP, one past
    the limits, or one that carries macros (.docm, .xlsm, .pptm or a VBA project under another name)."""
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            entries = archive.infolist()
            if len(entries) > ZIP_MAX_ENTRIES:
                return None
            if sum(e.file_size for e in entries) > ZIP_MAX_UNCOMPRESSED:
                return None
            if any(e.filename.lower().endswith("vbaproject.bin") for e in entries):
                return None
            try:
                listing = archive.getinfo("[Content_Types].xml")
            except KeyError:
                return None
            if listing.file_size > _CONTENT_TYPES_MAX:
                return None
            declared = archive.read(listing)
    except (zipfile.BadZipFile, zipfile.LargeZipFile, OSError, ValueError, NotImplementedError, EOFError):
        return None
    if b"macroenabled" in declared.lower() or b"vbaproject" in declared.lower():
        return None
    found = {_MAIN_PARTS[t.decode()] for t in _CONTENT_TYPE.findall(declared) if t.decode() in _MAIN_PARTS}
    return found.pop() if len(found) == 1 else None


def read(data: bytes, mime: str, options: ocr.OcrOptions, first: int = 0) -> DocumentText:
    """Runs in the child. The window of `MAX_PAGES` pages, slides or sheets from `first`; an image and a
    Word document are one window."""
    try:
        if mime == PDF:
            return _pdf(data, options, first)
        if mime in (JPEG, PNG):
            return DocumentText("\n".join(ocr.read(data, options)), "ocr", 1, 1)
        if mime == DOCX:
            return _docx(data)
        if mime == XLSX:
            return _xlsx(data, first)
        if mime == PPTX:
            return _pptx(data, first)
    except (ocr.TooLargeError, ocr.NotAnImageError, UnreadableDocumentError):
        raise
    except Exception as exc:
        # Whatever a damaged file makes a parser raise; the class alone crosses back to the parent.
        raise UnreadableDocumentError(f"the document could not be read ({type(exc).__name__})") from None
    raise UnreadableDocumentError("not a type this server reads")


def _next(first: int, read: int, total: int) -> int | None:
    return first + read if first + read < total else None


def _pdf(data: bytes, options: ocr.OcrOptions, first: int = 0) -> DocumentText:
    from pypdf import PdfReader

    pages: list[Any] = []
    total = 0
    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            return DocumentText("", "pdf_text", 0)
        total = len(reader.pages)
        pages = list(reader.pages[first : first + MAX_PAGES])
    except Exception:  # noqa: S110
        # pypdf could not open it; pdfium is more forgiving, and every page goes to OCR.
        pass
    rendered: Any = None
    reader_ocr: Any = None
    lines: list[str] = []
    count = len(pages)
    ocr_pages = 0
    if not pages:
        rendered = _pdfium(data)
        total = len(rendered)
        count = max(0, min(total - first, MAX_PAGES))
    try:
        for i in range(count):
            text = _page_text(pages[i]) if pages else ""
            if len(text.strip()) < MIN_PAGE_CHARS:
                if rendered is None:
                    rendered = _pdfium(data)
                if first + i >= len(rendered):
                    continue
                reader_ocr = reader_ocr or ocr.engine(options)
                text = "\n".join(ocr.image_rows(reader_ocr, _render(rendered, first + i), options.max_side))
                ocr_pages += 1
            lines.append(text)
    finally:
        if rendered is not None:
            rendered.close()
    return DocumentText(
        "\n".join(lines),
        "pdf_ocr" if ocr_pages else "pdf_text",
        count,
        ocr_pages,
        next_page=_next(first, count, total),
    )


def _page_text(page: Any) -> str:
    try:
        text: str = page.extract_text() or ""
    except Exception:
        return ""
    if "\n" in text and _ruled(page):
        try:
            return page.extract_text(extraction_mode="layout") or text
        except Exception:
            return text
    return text


def _ruled(page: Any) -> bool:
    """Several rectangles in the page's drawing: a table with borders, which layout mode keeps in rows."""
    try:
        contents = page.get_contents()
        return contents is not None and contents.get_data().count(b" re") >= 6
    except Exception:
        return False


def _pdfium(data: bytes) -> Any:
    import pypdfium2 as pdfium  # type: ignore[import-not-found,unused-ignore]

    try:
        return pdfium.PdfDocument(data)
    except pdfium.PdfiumError as exc:
        raise UnreadableDocumentError("the PDF could not be opened") from exc


def _render(document: Any, index: int) -> Any:
    page = document[index]
    try:
        width, height = page.get_size()
        scale = RENDER_DPI / 72
        area = max(width * height, 1.0)
        if area * scale * scale > ocr.MAX_PIXELS:
            scale = math.sqrt(ocr.MAX_PIXELS / area)
        bitmap = page.render(scale=scale)
        try:
            return bitmap.to_pil().convert("RGB")
        finally:
            bitmap.close()
    finally:
        page.close()


def _docx(data: bytes) -> DocumentText:
    from docx import Document
    from docx.table import Table

    document = Document(io.BytesIO(data))
    lines: list[str] = []
    for block in document.iter_inner_content():
        if isinstance(block, Table):
            lines.extend(_table_rows(block))
        else:
            lines.append(block.text)
    return DocumentText("\n".join(lines), "office", 1)


def _table_rows(table: Any) -> Iterator[str]:
    for row in table.rows:
        cells: list[str] = []
        seen: list[Any] = []
        for cell in row.cells:
            # A merged cell comes back once per grid column it spans (python-docx), or as spanned cells
            # (python-pptx); its text counts once.
            if getattr(cell, "is_spanned", False) or any(cell._tc is tc for tc in seen):
                continue
            seen.append(cell._tc)
            cells.append(" ".join(cell.text.split()))
        yield " | ".join(cells)


def _xlsx(data: bytes, first: int = 0) -> DocumentText:
    from openpyxl import load_workbook

    workbook = load_workbook(io.BytesIO(data), read_only=True, data_only=True, keep_links=False)
    lines: list[str] = []
    try:
        everything = workbook.worksheets
        sheets = everything[first : first + MAX_PAGES]
        for sheet in sheets:
            if len(everything) > 1:
                lines.append(sheet.title)
            for row in sheet.iter_rows(values_only=True):
                values = [_cell(v) for v in row]
                while values and not values[-1]:
                    values.pop()
                if values:
                    lines.append(" | ".join(values))
    finally:
        workbook.close()
    return DocumentText(
        "\n".join(lines), "office", len(sheets), next_page=_next(first, len(sheets), len(everything))
    )


def _cell(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.date().isoformat() if value.time() == time(0) else value.isoformat(sep=" ")
    if isinstance(value, date | time):
        return value.isoformat()
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return " ".join(str(value).split())


def _pptx(data: bytes, first: int = 0) -> DocumentText:
    from pptx import Presentation

    everything = list(Presentation(io.BytesIO(data)).slides)
    slides = everything[first : first + MAX_PAGES]
    lines = [line for slide in slides for line in _shape_lines(slide.shapes, 0)]
    return DocumentText(
        "\n".join(lines), "office", len(slides), next_page=_next(first, len(slides), len(everything))
    )


def _shape_lines(shapes: Any, depth: int) -> Iterator[str]:
    for shape in shapes:
        if hasattr(shape, "shapes"):
            if depth < _MAX_GROUP_DEPTH:
                yield from _shape_lines(shape.shapes, depth + 1)
            continue
        if getattr(shape, "has_text_frame", False) and shape.has_text_frame:
            yield shape.text_frame.text
        if getattr(shape, "has_table", False) and shape.has_table:
            yield from _table_rows(shape.table)


def selfcheck(options: ocr.OcrOptions) -> list[str]:
    """Reads a blank image, a blank scanned page and one small file of each Office type, in this process:
    the image build runs it so a reader that cannot load fails the build and not the pods."""
    import pypdfium2 as pdfium  # type: ignore[import-not-found,unused-ignore]
    from docx import Document
    from openpyxl import Workbook
    from PIL import Image
    from pptx import Presentation

    if not ocr.available(options.rec_dir):
        raise RuntimeError(f"no OCR engine or recognizer in {options.rec_dir}")
    files: list[bytes] = []
    image = io.BytesIO()
    Image.new("RGB", (64, 32), "white").save(image, "PNG")
    files.append(image.getvalue())
    blank = pdfium.PdfDocument.new()
    blank.new_page(200, 100)
    scanned = io.BytesIO()
    blank.save(scanned)
    files.append(scanned.getvalue())
    for make in (Document, Workbook, Presentation):
        out = io.BytesIO()
        make().save(out)
        files.append(out.getvalue())
    found = []
    for data in files:
        mime = sniff(data)
        if mime is None:
            raise RuntimeError("a sample file was not recognized")
        found.append(f"{mime}: {read(data, mime, options).source}")
    return found


if __name__ == "__main__":
    from niadra_models.settings import Settings

    s = Settings.from_env()
    print("\n".join(selfcheck(ocr.OcrOptions(s.ocr_max_side, s.ocr_det_side, 1, s.ocr_rec_dir))))
