"""Text in images and documents (10, D.1), read in a child process of its own, one file at a time.

Reading a file takes a few hundred MiB for a moment, and the pod also holds the embedding model that
every search question needs within 100 ms. The child is a new process per file, so what it took goes
back when it exits, and it asks the kernel to be the first to go if the pod runs out of memory: a file
then fails alone, and the embeddings, the PII layer and the guard keep serving.

Recognition uses PaddleOCR's PP-OCRv6 small recognizer (pinned in `onnx/pinned.py`, baked into the image)
inside RapidOCR, whose detector and angle classifier stay the ones in its wheel.
"""

import asyncio
import contextlib
import io
import multiprocessing
from collections.abc import Callable, Sequence
from concurrent.futures import ProcessPoolExecutor
from concurrent.futures.process import BrokenProcessPool
from dataclasses import dataclass
from importlib.util import find_spec
from pathlib import Path
from typing import Any

from niadra_models.onnx.pinned import OCR_REC_MODEL

VERSION = f"rapidocr-onnx-1.4.4+{OCR_REC_MODEL}"
MAX_PIXELS = 40_000_000
"""A decompression bomb is refused before it is decoded; a PDF page is rendered at most this large."""


class NotAnImageError(Exception):
    pass


class TooLargeError(Exception):
    pass


class UnreadableError(Exception):
    """The child could not finish: most likely the pod's memory ran out while it read."""


@dataclass(frozen=True, slots=True)
class OcrOptions:
    max_side: int
    det_side: int
    threads: int
    rec_dir: Path


def available(rec_dir: Path) -> bool:
    """The engine is the `ocr` extra and the recognizer is baked into the image; the main process never
    imports the engine."""
    return (
        find_spec("rapidocr_onnxruntime") is not None
        and (rec_dir / "model.onnx").is_file()
        and (rec_dir / "keys.txt").is_file()
    )


def engine(options: OcrOptions) -> Any:
    from rapidocr_onnxruntime import RapidOCR  # type: ignore[import-not-found,unused-ignore]

    return RapidOCR(
        intra_op_num_threads=options.threads,
        inter_op_num_threads=1,
        max_side_len=options.max_side,
        det_limit_type="max",
        det_limit_side_len=options.det_side,
        rec_model_path=str(options.rec_dir / "model.onnx"),
        rec_keys_path=str(options.rec_dir / "keys.txt"),
    )


def rows(result: Sequence[Any] | None) -> list[str]:
    """RapidOCR's boxes as visual rows: a box joins the row whose first box has its vertical centre within
    half the taller height, and a row reads left to right, cells two spaces apart. A table row then stays
    one line of text."""
    items: list[tuple[float, float, float, str]] = []
    for box, text, _score in result or []:
        ys = [float(point[1]) for point in box]
        xs = [float(point[0]) for point in box]
        items.append(((min(ys) + max(ys)) / 2, max(ys) - min(ys), min(xs), str(text)))
    items.sort()
    grouped: list[tuple[float, float, list[tuple[float, str]]]] = []
    for centre, height, left, text in items:
        if grouped and abs(centre - grouped[-1][0]) < 0.5 * max(height, grouped[-1][1]):
            grouped[-1][2].append((left, text))
        else:
            grouped.append((centre, height, [(left, text)]))
    return ["  ".join(text for _, text in sorted(cells)) for _, _, cells in grouped]


def image_rows(reader: Any, image: Any, max_side: int) -> list[str]:
    image.thumbnail((max_side, max_side))
    result, _ = reader(image)
    return rows(result)


def decode(data: bytes, max_side: int) -> Any:
    """Decodes at most `max_side` on the longest side (a JPEG decodes straight at the smaller scale)."""
    from PIL import Image, UnidentifiedImageError

    try:
        with Image.open(io.BytesIO(data)) as source:
            if source.width * source.height > MAX_PIXELS:
                raise TooLargeError("the image has too many pixels")
            source.draft("RGB", (max_side, max_side))
            return source.convert("RGB")
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
        raise NotAnImageError("the file is not an image this server reads") from exc


def read(data: bytes, options: OcrOptions) -> list[str]:
    """The rows of an image in reading order. Runs in the child."""
    image = decode(data, options.max_side)
    return image_rows(engine(options), image, options.max_side)


def _child() -> None:
    # Raising one's own OOM score needs no privilege; the kernel then picks this process first.
    with contextlib.suppress(OSError):
        Path("/proc/self/oom_score_adj").write_text("1000")


class OcrReader:
    """Runs one read at a time, each in a new child process."""

    def __init__(self, options: OcrOptions) -> None:
        self.options = options
        self._pool: ProcessPoolExecutor | None = None

    async def read(self, data: bytes) -> list[str]:
        return await self.run(read, data, self.options)

    async def run[R](self, fn: Callable[..., R], *args: Any) -> R:
        if self._pool is None:
            self._pool = ProcessPoolExecutor(
                max_workers=1,
                mp_context=multiprocessing.get_context("spawn"),
                initializer=_child,
                max_tasks_per_child=1,
            )
        try:
            return await asyncio.get_running_loop().run_in_executor(self._pool, fn, *args)
        except BrokenProcessPool as exc:
            self._pool = None
            raise UnreadableError("the file could not be read with the memory this server has") from exc

    def close(self, wait: bool = False) -> None:
        if self._pool is not None:
            self._pool.shutdown(wait=wait, cancel_futures=True)
            self._pool = None
