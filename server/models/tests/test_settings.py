from pathlib import Path

from niadra_models.settings import Settings


def test_defaults_serve_the_rule_backends() -> None:
    settings = Settings.from_env({})
    assert settings == Settings()
    assert settings.onnx_embed_dir is None
    assert settings.onnx_ner_dir is None


def test_reads_prefixed_variables() -> None:
    settings = Settings.from_env(
        {
            "NIADRA_MODELS_ONNX_EMBED_DIR": "/models/e5-small-r1",
            "NIADRA_MODELS_BATCH_WAIT_MS": "2.5",
            "NIADRA_MODELS_MAX_TEXTS": "32",
            "NIADRA_MODELS_ONNX_NER_DIR": "",
        }
    )
    assert settings.onnx_embed_dir == Path("/models/e5-small-r1")
    assert settings.onnx_ner_dir is None
    assert settings.batch_wait_ms == 2.5
    assert settings.max_texts == 32
