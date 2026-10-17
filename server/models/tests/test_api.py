import asyncio
import io
import threading
from collections.abc import AsyncIterator, Sequence
from dataclasses import replace

import httpx
import pytest
from fastapi import FastAPI

from conftest import ocr_rec_dir
from niadra_models import ocr as ocr_engine
from niadra_models.api import create_app
from niadra_models.capabilities import Span, Vectors
from niadra_models.registry import build
from niadra_models.rules.embeddings import HashingEmbedder
from niadra_models.settings import Settings

SETTINGS = Settings(max_texts=8, max_chars=500, batch_wait_ms=20, ocr_rec_dir=ocr_rec_dir())
HAS_OCR = ocr_engine.available(SETTINGS.ocr_rec_dir)
NEEDS_OCR = pytest.mark.skipif(not HAS_OCR, reason="the reader is the `ocr` extra and the pinned recognizer")


@pytest.fixture
async def app() -> AsyncIterator[FastAPI]:
    app = create_app(SETTINGS)
    async with app.router.lifespan_context(app):
        yield app


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://models") as c:
        yield c


async def test_embed(client: httpx.AsyncClient) -> None:
    r = await client.post("/v1/embed", json={"texts": ["meu pedido atrasou", "outra coisa"]})
    assert r.status_code == 200
    body = r.json()
    assert body["dim"] == 384
    assert body["model_version"] == "hash-char35-384@1"
    assert [len(v) for v in body["vectors"]] == [384, 384]


async def test_the_classifiers_are_gone(client: httpx.AsyncClient) -> None:
    # Every decision is the decision model's (the server's); the last pair classifier,
    # `action_fulfils_item`, went on 05/10/2026.
    for task in ("action_fulfils_item", "worth_extracting", "question_type"):
        assert (await client.post(f"/v1/classify/{task}", json={"texts": ["a [SEP] b"]})).status_code == 404


async def test_pii(client: httpx.AsyncClient) -> None:
    r = await client.post("/v1/pii", json={"texts": ["CPF 529.982.247-25", "sem dados"]})
    assert r.json() == {
        "spans": [[{"start": 4, "end": 18, "kind": "cpf"}], []],
        "model_version": "pii-rules@3",
    }


async def test_the_injection_guard_is_gone(client: httpx.AsyncClient) -> None:
    # Injection is the decision model's (the server's).
    assert (await client.post("/v1/guard", json={"texts": ["x"]})).status_code == 404


async def test_limits(client: httpx.AsyncClient) -> None:
    assert (await client.post("/v1/pii", json={"texts": ["x"] * 9})).status_code == 413
    assert (await client.post("/v1/pii", json={"texts": ["x" * 501]})).status_code == 413
    assert (await client.post("/v1/embed", json={"texts": "not a list"})).status_code == 422


async def test_healthz_names_every_model(client: httpx.AsyncClient) -> None:
    body = (await client.get("/healthz")).json()
    assert body["status"] == "ok"
    expected = {"embed", "pii"} | ({"ocr"} if HAS_OCR else set())
    assert set(body["model_version"]) == expected


async def test_metrics_count_requests_per_route_template(client: httpx.AsyncClient) -> None:
    await client.post("/v1/pii", json={"texts": ["a"]})
    await client.post("/v1/pii", json={"texts": ["b"]})
    await client.post("/v1/nope/42", json={"texts": ["x"]})
    text = (await client.get("/metrics")).text
    assert 'niadra_models_requests_total{endpoint="/v1/pii",status="200"} 2' in text
    assert 'niadra_models_requests_total{endpoint="unmatched",status="404"} 1' in text
    assert 'niadra_models_request_duration_seconds_bucket{endpoint="/v1/pii",le="+Inf"} 2' in text
    assert 'niadra_models_queue_depth{model="pii"} 0' in text


class CountingDetector:
    version = "counting"

    def __init__(self) -> None:
        self.calls = 0

    def detect(self, texts: Sequence[str]) -> list[list[Span]]:
        self.calls += 1
        return [[] for _ in texts]


async def test_concurrent_http_requests_share_one_model_call() -> None:
    counter = CountingDetector()
    app = create_app(SETTINGS, replace(build(SETTINGS), pii=counter))
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://models") as client,
    ):
        responses = await asyncio.gather(
            *(client.post("/v1/pii", json={"texts": [f"t{i}"]}) for i in range(6))
        )
    assert all(r.status_code == 200 for r in responses)
    assert counter.calls == 1


class HeldEmbedder:
    """Encodes as the hashing backend, but a batch of item texts waits until the test lets it finish."""

    def __init__(self) -> None:
        self._inner = HashingEmbedder()
        self.version = self._inner.version
        self.dim = self._inner.dim
        self.batch_started = threading.Event()
        self.release = threading.Event()

    def embed(self, texts: Sequence[str]) -> Vectors:
        if len(texts) > 1:
            self.batch_started.set()
            assert self.release.wait(5), "the test never released the batch"
        return self._inner.embed(texts)


async def test_a_question_is_not_queued_behind_a_batch_of_item_texts() -> None:
    # A search waits on its question: behind a batch it waited for the whole batch (p95 0.45 s on 09/10).
    held = HeldEmbedder()
    app = create_app(SETTINGS, replace(build(SETTINGS), embedder=held))
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://models") as client,
    ):
        batch = asyncio.create_task(client.post("/v1/embed", json={"texts": [f"item {i}" for i in range(8)]}))
        assert await asyncio.to_thread(held.batch_started.wait, 5)
        question = await asyncio.wait_for(
            client.post("/v1/embed", json={"texts": ["meu pedido atrasou"], "query": True}), 2
        )
        assert question.status_code == 200
        assert not batch.done()
        held.release.set()
        assert (await batch).status_code == 200
        # The same vector as the question encoded in the items' lane.
        alone = await client.post("/v1/embed", json={"texts": ["meu pedido atrasou"]})
    assert question.json() == alone.json()


async def test_the_question_lane_reports_its_queue_depth(client: httpx.AsyncClient) -> None:
    r = await client.post("/v1/embed", json={"texts": ["meu pedido atrasou"]})
    assert r.status_code == 200
    text = (await client.get("/metrics")).text
    assert 'niadra_models_queue_depth{model="embed:question"} 0' in text


async def test_the_reader_answers_501_without_an_engine() -> None:
    # The image carries no reader by default: the cell records that the file was not read.
    settings = replace(SETTINGS, ocr_rec_dir=SETTINGS.ocr_rec_dir / "absent")
    app = create_app(settings)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://models") as client,
    ):
        r = await client.post("/v1/ocr", content=b"\x89PNG\r\n\x1a\n" + b"0" * 32)
        assert r.status_code == 501
        r = await client.post("/v1/document-text", params={"type": "application/pdf"}, content=b"%PDF-1.4")
        assert r.status_code == 501
        assert "ocr" not in (await client.get("/healthz")).json()["model_version"]


def _receipt(size: tuple[int, int], fmt: str = "PNG") -> bytes:
    from PIL import Image, ImageDraw, ImageFont

    image = Image.new("RGB", size, "white")
    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default(size=max(size) // 30)
    for i, line in enumerate(("Fatura 0823", "Valor total R$ 189,90", "Pedido 48213")):
        draw.text((size[0] // 20, size[1] // 10 + i * size[1] // 6), line, fill="black", font=font)
    out = io.BytesIO()
    image.save(out, fmt)
    return out.getvalue()


@NEEDS_OCR
async def test_ocr_reads_the_lines_of_an_image_and_scales_a_large_photo_down(
    client: httpx.AsyncClient,
) -> None:
    small = await client.post("/v1/ocr", content=_receipt((1200, 900)))
    large = await client.post("/v1/ocr", content=_receipt((4000, 3000), "JPEG"))
    for r in (small, large):
        assert r.status_code == 200
        text = " ".join(r.json()["lines"])
        assert "0823" in text
        assert "48213" in text
        assert r.json()["model_version"] == ocr_engine.VERSION


@NEEDS_OCR
async def test_ocr_refuses_what_is_not_an_image_and_a_decompression_bomb(client: httpx.AsyncClient) -> None:
    from PIL import Image

    assert (await client.post("/v1/ocr", content=b"%PDF-1.7 not an image")).status_code == 422
    out = io.BytesIO()
    Image.new("1", (9000, 9000)).save(out, "PNG")
    assert (await client.post("/v1/ocr", content=out.getvalue())).status_code == 413


async def test_a_child_that_dies_fails_only_its_image() -> None:
    # The kernel kills the OCR child first when the pod runs out of memory: that image answers 422 and
    # the next one gets a new child.
    from concurrent.futures import Future
    from concurrent.futures.process import BrokenProcessPool

    from niadra_models.ocr import OcrOptions, OcrReader, UnreadableError

    class DeadPool:
        def submit(self, *_: object) -> Future[list[str]]:
            future: Future[list[str]] = Future()
            future.set_exception(BrokenProcessPool("killed"))
            return future

        def shutdown(self, **_: object) -> None:
            return None

    reader = OcrReader(OcrOptions(1600, 960, 1, ocr_rec_dir()))
    reader._pool = DeadPool()  # type: ignore[assignment]
    with pytest.raises(UnreadableError):
        await reader.read(b"image")
    assert reader._pool is None


@NEEDS_OCR
async def test_document_text_reads_a_scanned_pdf_in_its_own_child(client: httpx.AsyncClient) -> None:
    from PIL import Image

    page = Image.open(io.BytesIO(_receipt((1654, 2339))))
    scanned = io.BytesIO()
    page.save(scanned, "PDF", resolution=200)
    r = await client.post("/v1/document-text", params={"type": "application/pdf"}, content=scanned.getvalue())
    assert r.status_code == 200
    body = r.json()
    assert (body["source"], body["pages"], body["ocr_pages"]) == ("pdf_ocr", 1, 1)
    assert "Pedido 48213" in body["text"].splitlines()
    assert body["model_version"] == ocr_engine.VERSION
    assert (await client.get("/healthz")).json()["model_version"]["ocr"] == ocr_engine.VERSION


@NEEDS_OCR
async def test_document_text_trusts_the_bytes_not_the_declared_type(client: httpx.AsyncClient) -> None:
    png = _receipt((300, 200))
    r = await client.post("/v1/document-text", params={"type": "application/pdf"}, content=png)
    assert r.status_code == 422
    r = await client.post("/v1/document-text", params={"type": "image/gif"}, content=b"GIF89a")
    assert r.status_code == 422
    r = await client.post("/v1/document-text", params={"type": "application/pdf"}, content=b"")
    assert r.status_code == 413
    # A PDF neither pypdf nor pdfium can open is a damaged file, not a server error.
    r = await client.post("/v1/document-text", params={"type": "application/pdf"}, content=b"%PDF-1.7 junk")
    assert r.status_code == 422
