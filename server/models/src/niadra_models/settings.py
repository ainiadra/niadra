import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from niadra_models.onnx.pinned import OCR_REC_MODEL

_PREFIX = "NIADRA_MODELS_"


@dataclass(frozen=True, slots=True)
class Settings:
    """Read from `NIADRA_MODELS_*` environment variables; every field has a working default.

    Without `ONNX_EMBED_DIR` and `ONNX_NER_DIR` the server runs the dependency-free rule backends.
    """

    onnx_embed_dir: Path | None = None
    onnx_ner_dir: Path | None = None
    onnx_threads: int = 1
    ner_min_score: float = 0.6
    max_tokens: int = 256
    batch_wait_ms: float = 5.0
    max_batch_items: int = 64
    threads: int = 4
    max_texts: int = 256
    max_chars: int = 20_000
    ocr_max_side: int = 1600
    """Longest side an image is read at; a larger one is scaled down first, which bounds the memory."""
    ocr_det_side: int = 960
    """Longest side the text detector sees."""
    ocr_rec_dir: Path = Path("/models") / OCR_REC_MODEL
    """The OCR recognizer the Dockerfile bakes into the image with the reader (`--build-arg OCR=1`)."""

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "Settings":
        source = os.environ if env is None else env

        def get(name: str) -> str | None:
            return source.get(_PREFIX + name) or None

        def path(name: str) -> Path | None:
            value = get(name)
            return Path(value) if value else None

        d = cls()
        return cls(
            onnx_embed_dir=path("ONNX_EMBED_DIR"),
            onnx_ner_dir=path("ONNX_NER_DIR"),
            onnx_threads=int(get("ONNX_THREADS") or d.onnx_threads),
            ner_min_score=float(get("NER_MIN_SCORE") or d.ner_min_score),
            max_tokens=int(get("MAX_TOKENS") or d.max_tokens),
            batch_wait_ms=float(get("BATCH_WAIT_MS") or d.batch_wait_ms),
            max_batch_items=int(get("MAX_BATCH_ITEMS") or d.max_batch_items),
            threads=int(get("THREADS") or d.threads),
            max_texts=int(get("MAX_TEXTS") or d.max_texts),
            max_chars=int(get("MAX_CHARS") or d.max_chars),
            ocr_max_side=int(get("OCR_MAX_SIDE") or d.ocr_max_side),
            ocr_det_side=int(get("OCR_DET_SIDE") or d.ocr_det_side),
            ocr_rec_dir=path("OCR_REC_DIR") or d.ocr_rec_dir,
        )
