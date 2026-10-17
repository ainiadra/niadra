import os
from collections.abc import Callable
from pathlib import Path

import numpy as np
import pytest

from niadra_models.onnx.pinned import OCR_REC_MODEL
from niadra_models.settings import Settings

TinyModel = Callable[..., Path]


def ocr_rec_dir() -> Path:
    """Where the tests find the pinned OCR recognizer: NIADRA_MODELS_OCR_REC_DIR, the cache `pinned.py`
    fetches into (`uv run python src/niadra_models/onnx/pinned.py pp-ocrv6-small-rec-r1 ~/.cache/niadra`)
    or, as in the image, the default."""
    if os.environ.get("NIADRA_MODELS_OCR_REC_DIR"):
        return Path(os.environ["NIADRA_MODELS_OCR_REC_DIR"])
    cached = Path.home() / ".cache" / "niadra" / OCR_REC_MODEL
    return cached if cached.is_dir() else Settings().ocr_rec_dir


@pytest.fixture
def tiny_embed_model(tmp_path: Path) -> TinyModel:
    """Writes a real ONNX embedding model small enough for a unit test: a word-level tokenizer and a
    lookup table from token id to a 4-dimensional state, with the files a Hugging Face export has."""
    tokenizers = pytest.importorskip("tokenizers")
    onnx = pytest.importorskip("onnx")
    pytest.importorskip("onnxruntime")

    def write(max_seq_length: int | None = None, version: str = "tiny-r1") -> Path:
        words = ["[UNK]", "oi", "pedido", "entrega", "senha", "atrasou", "chegou", "trocar"]
        tokenizer = tokenizers.Tokenizer(
            tokenizers.models.WordLevel({w: i for i, w in enumerate(words)}, unk_token="[UNK]")
        )
        tokenizer.pre_tokenizer = tokenizers.pre_tokenizers.Whitespace()
        tokenizer.save(str(tmp_path / "tokenizer.json"))
        table = np.random.default_rng(7).normal(size=(len(words), 4)).astype(np.float32)
        graph = onnx.helper.make_graph(
            [onnx.helper.make_node("Gather", ["table", "input_ids"], ["last_hidden_state"])],
            "tiny",
            [
                onnx.helper.make_tensor_value_info("input_ids", onnx.TensorProto.INT64, ["b", "s"]),
                onnx.helper.make_tensor_value_info("attention_mask", onnx.TensorProto.INT64, ["b", "s"]),
            ],
            [onnx.helper.make_tensor_value_info("last_hidden_state", onnx.TensorProto.FLOAT, ["b", "s", 4])],
            [onnx.numpy_helper.from_array(table, "table")],
        )
        # Pinned to an IR version every supported onnxruntime reads.
        proto = onnx.helper.make_model(graph, ir_version=9, opset_imports=[onnx.helper.make_opsetid("", 17)])
        onnx.save(proto, str(tmp_path / "model.onnx"))
        (tmp_path / "config.json").write_text('{"hidden_size": 4}')
        (tmp_path / "VERSION").write_text(version + "\n")
        if max_seq_length is not None:
            (tmp_path / "sentence_bert_config.json").write_text(f'{{"max_seq_length": {max_seq_length}}}')
        return tmp_path

    return write
