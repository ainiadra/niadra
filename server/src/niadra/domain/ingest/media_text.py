"""Text from files and images, read inside the cell (10, D.1). Only the types the space allows, only by
local extraction on the in-cell models server (a PDF's text layer, OCR of images and scanned pages, the
Office readers), never a third party's vision model.

The text becomes a derived event beside the one carrying the media: the log stays append-only, the
derived event has the same speaker, conversation and handles, so extraction, erasure and export treat it
like any turn, and it names where it came from (`extracted_from`, `derived_from_event`). It keeps its line
breaks, so a table row stays one line; the masking rules and the identity check read the breaks as spaces,
exactly as they read the one-line text before.
"""

import io
import re
import zipfile
from collections.abc import Collection, Mapping
from dataclasses import dataclass
from typing import Any

PDF = "application/pdf"
JPEG = "image/jpeg"
PNG = "image/png"
DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
PPTX = "application/vnd.openxmlformats-officedocument.presentationml.presentation"
EXTRACTABLE = frozenset({PDF, JPEG, PNG, DOCX, XLSX, PPTX})

MAX_BYTES = 20 * 1024 * 1024
MAX_PAGES = 50
"""Pages, slides or sheets one request to the reader reads; the next window starts at its `next_page`, so a
document of any length is read whole."""
MAX_CHARS = 100_000
"""Characters of one derived turn, line breaks counted. A longer text is never cut: it is stored as several
derived turns in order, each saying which `part` of how many `parts` it is."""
MIN_CHARS = 3
"""Below this a photo or a blank page gave nothing worth a turn."""
ZIP_MAX_ENTRIES = 2_000
ZIP_MAX_UNCOMPRESSED = 100 * 1024 * 1024
"""An Office file past either limit is not read: a zip bomb is refused before anything inflates. Python's
zipfile never inflates an entry past its declared size, so the declared sizes bound the readers."""
_CONTENT_TYPES_MAX = 1024 * 1024
_MAIN_PARTS = {
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml": DOCX,
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml": XLSX,
    "application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml": PPTX,
}
_CONTENT_TYPE = re.compile(rb"""ContentType\s*=\s*["']([^"']+)["']""")


@dataclass(frozen=True, slots=True)
class ReadText:
    text: str
    source: str
    """`pdf_text`, `pdf_ocr` (a page had no text layer and was read by OCR), `ocr` or `office`: the
    derived event's `extracted_from` and the mark on the original."""
    next_page: int | None = None
    """Where the reader's next window of `MAX_PAGES` starts; None when this one reached the end."""


# What the PII detectors call an identity document. A scan whose text carries one is never stored: the
# memory keeps no copy of a person's ID, whatever the space allows (10, D.1).
IDENTITY_KINDS = frozenset(
    {"cpf", "rg", "cnh", "passport", "gov_id", "national_id", "document", "ssn", "nif", "dni"}
)


def sniff(data: bytes) -> str | None:
    """The type by the file's own bytes: the declared type of an upload is the sender's word."""
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
    """DOCX, XLSX or PPTX by the main part its `[Content_Types].xml` declares. None for any other ZIP, one
    past the limits, or one that carries macros: .docm, .xlsm and .pptm declare a macro-enabled main part,
    and a VBA project under an Office content type is refused all the same."""
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            entries = archive.infolist()
            if len(entries) > ZIP_MAX_ENTRIES or sum(e.file_size for e in entries) > ZIP_MAX_UNCOMPRESSED:
                return None
            if any(e.filename.lower().endswith("vbaproject.bin") for e in entries):
                return None
            listing = next((e for e in entries if e.filename == "[Content_Types].xml"), None)
            if listing is None or listing.file_size > _CONTENT_TYPES_MAX:
                return None
            declared = archive.read(listing)
    except (zipfile.BadZipFile, zipfile.LargeZipFile, OSError, ValueError, NotImplementedError, EOFError):
        return None
    lowered = declared.lower()
    if b"macroenabled" in lowered or b"vbaproject" in lowered:
        return None
    found = {_MAIN_PARTS.get(t.decode(errors="replace")) for t in _CONTENT_TYPE.findall(declared)} - {None}
    return found.pop() if len(found) == 1 else None


def allowed(mime: str | None, space_allows: Collection[str]) -> bool:
    return mime is not None and mime in EXTRACTABLE and mime in space_allows


def is_identity_document(kinds: Collection[str]) -> bool:
    return any(k in IDENTITY_KINDS for k in kinds)


def trimmed(text: str) -> str:
    """Spaces collapse within each line and blank lines go; the line breaks stay, so a table row read as
    one line stays one line."""
    lines = (" ".join(line.split()) for line in text.splitlines())
    return "\n".join(line for line in lines if line)


def parts(text: str) -> list[str]:
    """The text in pieces of at most `MAX_CHARS`, each ending at its last line break, or at the limit for a
    line longer than it: joined by line breaks where they were cut at one, they are the whole text."""
    out: list[str] = []
    rest = text
    while len(rest) > MAX_CHARS:
        cut = rest.rfind("\n", 0, MAX_CHARS + 1)
        if cut > 0:
            out.append(rest[:cut])
            rest = rest[cut + 1 :]
        else:
            out.append(rest[:MAX_CHARS])
            rest = rest[MAX_CHARS:]
    return [*out, rest] if rest else out


def one_line(text: str) -> str:
    """What the PII detectors see of a derived text: its breaks as spaces."""
    return text.replace("\n", " ")


def is_derived(content_meta: Mapping[str, Any] | None) -> bool:
    """An event whose text was read from a file or an image."""
    return bool(content_meta and content_meta.get("extracted_from"))
