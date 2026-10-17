"""Chooses a backend per capability from the settings; the only place that knows both families."""

from dataclasses import dataclass

from niadra_models.capabilities import Embedder, PiiDetector
from niadra_models.onnx import runtime
from niadra_models.onnx.embeddings import OnnxEmbedder
from niadra_models.onnx.ner import OnnxNer, id2label
from niadra_models.rules.embeddings import HashingEmbedder
from niadra_models.rules.pii import LayeredPiiDetector, RulePiiDetector
from niadra_models.settings import Settings


@dataclass(frozen=True, slots=True)
class Models:
    embedder: Embedder
    pii: PiiDetector


def build(settings: Settings) -> Models:
    embedder = _embedder(settings)
    return Models(
        embedder=embedder,
        pii=_pii(settings),
    )


def _embedder(settings: Settings) -> Embedder:
    if settings.onnx_embed_dir is None:
        return HashingEmbedder()
    model = runtime.load(settings.onnx_embed_dir, settings.max_tokens, settings.onnx_threads)
    return OnnxEmbedder(model.session, model.tokenizer, int(model.config["hidden_size"]), model.version)


def _pii(settings: Settings) -> PiiDetector:
    if settings.onnx_ner_dir is None:
        return RulePiiDetector()
    model = runtime.load(settings.onnx_ner_dir, settings.max_tokens, settings.onnx_threads, stride=32)
    ner = OnnxNer(
        model.session, model.tokenizer, id2label(model.config), model.version, settings.ner_min_score
    )
    return LayeredPiiDetector(ner)
