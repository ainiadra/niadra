from typing import Any

import numpy as np

from niadra_models.registry import build
from niadra_models.settings import Settings


def test_without_model_directories_every_capability_uses_the_rules() -> None:
    models = build(Settings())
    assert models.embedder.version == "hash-char35-384@1"
    assert models.pii.version == "pii-rules@3"


def test_an_onnx_embedder_serves_embed(tiny_embed_model: Any) -> None:
    models = build(Settings(onnx_embed_dir=tiny_embed_model()))
    vectors = models.embedder.embed(["oi pedido", "entrega atrasou"])
    assert models.embedder.version == "onnx:tiny-r1"
    assert models.embedder.dim == 4
    np.testing.assert_allclose(np.linalg.norm(vectors, axis=1), 1.0, rtol=1e-5)
