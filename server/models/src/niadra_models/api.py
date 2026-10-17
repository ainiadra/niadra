"""The internal HTTP API of `niadra-models` (5.6). Text comes in, a label or a vector goes out; nothing
is stored and no text is logged. Every response names the `model_version` that produced it.

Run with `uvicorn niadra_models.api:create_app --factory`.
"""

import time
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

import numpy as np
from fastapi import APIRouter, FastAPI, HTTPException, Query, Request, Response
from pydantic import BaseModel

from niadra_models import documents
from niadra_models import ocr as ocr_engine
from niadra_models.batching import MicroBatcher
from niadra_models.capabilities import Span
from niadra_models.metrics import Metrics
from niadra_models.registry import Models, build
from niadra_models.settings import Settings


class TextsIn(BaseModel):
    texts: list[str]


class EmbedIn(TextsIn):
    query: bool = False
    """A search's question: encoded in a lane of its own, never queued behind the write side's batches."""


class EmbedOut(BaseModel):
    vectors: list[list[float]]
    dim: int
    model_version: str


class SpanOut(BaseModel):
    start: int
    end: int
    kind: str


class PiiOut(BaseModel):
    spans: list[list[SpanOut]]
    model_version: str


class HealthOut(BaseModel):
    status: str
    model_version: dict[str, str]


@dataclass(slots=True)
class Runtime:
    settings: Settings
    models: Models
    metrics: Metrics
    embed: MicroBatcher[str, list[float]]
    question: MicroBatcher[str, list[float]]
    pii: MicroBatcher[str, list[Span]]
    ocr: ocr_engine.OcrReader | None = None

    def batchers(self) -> list[MicroBatcher[Any, Any]]:
        return [self.embed, self.question, self.pii]


def _runtime(settings: Settings, models: Models, executor: ThreadPoolExecutor) -> Runtime:
    metrics = Metrics()

    def batcher[T, R](name: str, run: Callable[[list[T]], Sequence[R]]) -> MicroBatcher[T, R]:
        return MicroBatcher(
            name, run, executor, metrics, settings.batch_wait_ms / 1000, settings.max_batch_items
        )

    # Rounded and converted in the worker thread, so the loop only serializes plain floats.
    def embed(texts: list[str]) -> list[list[float]]:
        vectors: list[list[float]] = np.round(models.embedder.embed(texts), 6).tolist()
        return vectors

    runtime = Runtime(
        settings=settings,
        models=models,
        metrics=metrics,
        embed=batcher("embed", embed),
        # A search waits on its question's vector: one that came while a batch of item texts was encoded
        # waited for the whole batch (p95 0.45 s at 10 questions/s beside 64 texts every 2 s, 09/10/2026).
        # In its own lane it runs beside the batch, on another thread of the pool, and takes the questions
        # already waiting without waiting for more.
        question=MicroBatcher("embed:question", embed, executor, metrics, 0.0, settings.max_batch_items),
        pii=batcher("pii", models.pii.detect),
        ocr=ocr_engine.OcrReader(
            ocr_engine.OcrOptions(
                settings.ocr_max_side, settings.ocr_det_side, settings.onnx_threads, settings.ocr_rec_dir
            )
        )
        if ocr_engine.available(settings.ocr_rec_dir)
        else None,
    )
    metrics.gauge("niadra_models_queue_depth", lambda: {b.name: b.depth for b in runtime.batchers()})
    return runtime


router = APIRouter()


def _rt(request: Request) -> Runtime:
    runtime: Runtime = request.app.state.runtime
    return runtime


def _check(settings: Settings, texts: Sequence[str]) -> None:
    if len(texts) > settings.max_texts:
        raise HTTPException(413, f"at most {settings.max_texts} texts per request")
    if any(len(t) > settings.max_chars for t in texts):
        raise HTTPException(413, f"at most {settings.max_chars} characters per text")


@router.post("/v1/embed")
async def embed(body: EmbedIn, request: Request) -> EmbedOut:
    rt = _rt(request)
    _check(rt.settings, body.texts)
    vectors = await (rt.question if body.query else rt.embed).submit(body.texts)
    return EmbedOut(vectors=vectors, dim=rt.models.embedder.dim, model_version=rt.models.embedder.version)


@router.post("/v1/pii")
async def pii(body: TextsIn, request: Request) -> PiiOut:
    rt = _rt(request)
    _check(rt.settings, body.texts)
    spans = await rt.pii.submit(body.texts)
    return PiiOut(
        spans=[[SpanOut(start=s.start, end=s.end, kind=s.kind) for s in text] for text in spans],
        model_version=rt.models.pii.version,
    )


class OcrOut(BaseModel):
    lines: list[str]
    model_version: str


async def _body(request: Request) -> bytes:
    data = await request.body()
    if not data or len(data) > documents.MAX_BYTES:
        raise HTTPException(413, "the file is empty or larger than 20 MiB")
    return data


@router.post("/v1/ocr")
async def ocr(request: Request) -> OcrOut:
    """Text in an image (10, D.1), one line per visual row, only when the image carries the reader (the
    `ocr` extra and the baked recognizer). Without one it answers 501 and the cell records that the image
    was not read. The image is read in a child process, one at a time (see `niadra_models.ocr`)."""
    rt = _rt(request)
    if rt.ocr is None:
        raise HTTPException(501, "no OCR engine in this image")
    data = await _body(request)
    try:
        lines = await rt.ocr.read(data)
    except ocr_engine.TooLargeError as exc:
        raise HTTPException(413, str(exc)) from exc
    except (ocr_engine.NotAnImageError, ocr_engine.UnreadableError) as exc:
        raise HTTPException(422, str(exc)) from exc
    return OcrOut(lines=lines, model_version=ocr_engine.VERSION)


class DocumentOut(BaseModel):
    text: str
    source: str
    pages: int
    ocr_pages: int
    next_page: int | None = None
    """Where the next window of pages, slides or sheets starts (`first_page`); null at the end."""
    model_version: str


@router.post("/v1/document-text")
async def document_text(
    request: Request, mime: str = Query(alias="type"), first_page: int = Query(default=0, ge=0)
) -> DocumentOut:
    """The text of a PDF, an image or an Office file (10, D.1), read in a child process of its own, one
    document at a time. `type` is what the cell sniffed; this server sniffs the bytes again and refuses a
    mismatch with 422, as it refuses a damaged file. 413: too large, 501: no reader in this image. One
    request reads `documents.MAX_PAGES` pages, slides or sheets from `first_page`; `next_page` says where the
    next starts, so a document of any length is read whole."""
    rt = _rt(request)
    if rt.ocr is None:
        raise HTTPException(501, "no document reader in this image")
    data = await _body(request)
    if mime not in documents.TYPES or documents.sniff(data) != mime:
        raise HTTPException(422, "the bytes are not a document of this type")
    try:
        found = await rt.ocr.run(documents.read, data, mime, rt.ocr.options, first_page)
    except ocr_engine.TooLargeError as exc:
        raise HTTPException(413, str(exc)) from exc
    except (
        ocr_engine.NotAnImageError,
        ocr_engine.UnreadableError,
        documents.UnreadableDocumentError,
    ) as exc:
        raise HTTPException(422, str(exc)) from exc
    return DocumentOut(
        text=found.text,
        source=found.source,
        pages=found.pages,
        ocr_pages=found.ocr_pages,
        next_page=found.next_page,
        model_version=ocr_engine.VERSION,
    )


@router.get("/healthz")
async def healthz(request: Request) -> HealthOut:
    rt = _rt(request)
    m = rt.models
    versions = {"embed": m.embedder.version, "pii": m.pii.version}
    if rt.ocr is not None:
        versions["ocr"] = ocr_engine.VERSION
    return HealthOut(status="ok", model_version=versions)


@router.get("/metrics")
async def metrics(request: Request) -> Response:
    return Response(_rt(request).metrics.render(), media_type="text/plain; version=0.0.4")


def _endpoint(request: Request) -> str:
    """The route template, so a path with a parameter is one series and unknown paths add no series."""
    route = request.scope.get("route")
    return str(getattr(route, "path", "unmatched"))


def create_app(settings: Settings | None = None, models: Models | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    models = models or build(settings)
    executor = ThreadPoolExecutor(max_workers=settings.threads, thread_name_prefix="models")
    runtime = _runtime(settings, models, executor)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        yield
        for b in runtime.batchers():
            await b.close()
        if runtime.ocr is not None:
            runtime.ocr.close()
        executor.shutdown(wait=False, cancel_futures=True)

    app = FastAPI(title="niadra-models", lifespan=lifespan, docs_url=None, redoc_url=None)
    app.state.runtime = runtime
    app.include_router(router)

    @app.middleware("http")
    async def observe(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        started = time.perf_counter()
        response = await call_next(request)
        runtime.metrics.request(_endpoint(request), response.status_code, time.perf_counter() - started)
        return response

    return app
