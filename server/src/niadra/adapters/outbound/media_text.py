"""Local text extraction from media (10, D.1): the in-cell models server reads the document in a child
process of its own (`POST /v1/document-text`). No third-party model sees a file.

A models image without the reader answers 501: a PDF is then read for its text layer here, with pypdf in a
thread, and an image through `/v1/ocr`.
"""

import asyncio
import io
import logging

import httpx

from niadra.domain.ingest.media_text import JPEG, MAX_PAGES, PDF, PNG, ReadText

log = logging.getLogger(__name__)

OCR_TIMEOUT_S = 20.0
OCR_PAGE_TIMEOUT_S = 3.0
DOCUMENT_TIMEOUT_S = OCR_TIMEOUT_S + OCR_PAGE_TIMEOUT_S * MAX_PAGES
"""170 s: a window of 50 scanned pages, each rendered and read by OCR. Every type waits as long, because the
server reads one document at a time and a photo may queue behind such a window."""


class LocalMediaText:
    def __init__(self, models_url: str) -> None:
        self._http = httpx.AsyncClient(base_url=models_url, timeout=httpx.Timeout(OCR_TIMEOUT_S))

    async def aclose(self) -> None:
        await self._http.aclose()

    async def document_text(self, data: bytes, mime: str, first_page: int = 0) -> ReadText | None:
        response = await self._http.post(
            "/v1/document-text",
            params={"type": mime, "first_page": first_page},
            content=data,
            headers={"Content-Type": "application/octet-stream"},
            timeout=DOCUMENT_TIMEOUT_S,
        )
        if response.status_code == 501:
            return await self._without_reader(data, mime) if first_page == 0 else ReadText("", _source(mime))
        if response.status_code in (413, 422):
            # Too large, damaged, not what its bytes said, or more memory than the server has: read as
            # no text, so the task finishes instead of trying the same file again.
            return ReadText("", _source(mime))
        response.raise_for_status()
        body = response.json()
        following = body.get("next_page")
        return ReadText(
            str(body.get("text") or ""),
            str(body.get("source") or _source(mime)),
            next_page=following if isinstance(following, int) and following > first_page else None,
        )

    async def _without_reader(self, data: bytes, mime: str) -> ReadText | None:
        if mime == PDF:
            return ReadText(await asyncio.to_thread(_pdf_text, data), "pdf_text")
        if mime not in (JPEG, PNG):
            return None
        response = await self._http.post(
            "/v1/ocr", content=data, headers={"Content-Type": "application/octet-stream"}
        )
        if response.status_code == 501:
            return None  # this cell's models image carries no OCR engine
        if response.status_code in (413, 422):
            return ReadText("", "ocr")
        response.raise_for_status()
        return ReadText("\n".join(str(line) for line in response.json().get("lines", [])), "ocr")


def _source(mime: str) -> str:
    if mime == PDF:
        return "pdf_text"
    return "ocr" if mime in (JPEG, PNG) else "office"


def _pdf_text(data: bytes) -> str:
    """The text layer of every page."""
    from pypdf import PdfReader
    from pypdf.errors import PdfReadError

    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            return ""
        return "\n".join((page.extract_text() or "") for page in reader.pages)
    except (PdfReadError, ValueError, KeyError, TypeError):
        log.warning("a PDF could not be read for its text layer")
        return ""
