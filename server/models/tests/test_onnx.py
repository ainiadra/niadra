"""The ONNX backends against a fake session and tokenizer: no model files, no onnxruntime needed."""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from niadra_models.onnx.embeddings import OnnxEmbedder
from niadra_models.onnx.ner import OnnxNer, entity_kind, id2label
from niadra_models.onnx.runtime import feeds


@dataclass
class FakeEncoding:
    ids: list[int]
    attention_mask: list[int]
    type_ids: list[int]
    offsets: list[tuple[int, int]]
    overflowing: list["FakeEncoding"] = field(default_factory=list)


class WhitespaceTokenizer:
    """[CLS] word... [SEP], with character offsets like `tokenizers` returns them."""

    def __init__(self, max_words: int = 64) -> None:
        self.max_words = max_words

    def encode_batch(self, texts: list[str]) -> list[FakeEncoding]:
        return [self._encode(t) for t in texts]

    def _encode(self, text: str) -> FakeEncoding:
        offsets, start = [], 0
        for word in text.split():
            start = text.index(word, start)
            offsets.append((start, start + len(word)))
            start += len(word)
        pieces = [offsets[i : i + self.max_words] for i in range(0, max(len(offsets), 1), self.max_words)]
        first, *rest = [self._piece(p) for p in pieces]
        first.overflowing = rest
        return first

    @staticmethod
    def _piece(offsets: list[tuple[int, int]]) -> FakeEncoding:
        full = [(0, 0), *offsets, (0, 0)]
        ids = [101, *(1000 + s for s, _ in offsets), 102]
        return FakeEncoding(ids, [1] * len(ids), [0] * len(ids), full)


@dataclass
class Input:
    name: str


class FakeSession:
    def __init__(self, inputs: list[str], compute: Any) -> None:
        self._inputs = [Input(n) for n in inputs]
        self._compute = compute
        self.feeds: list[dict[str, np.ndarray]] = []

    def get_inputs(self) -> list[Input]:
        return self._inputs

    def run(self, output_names: list[str] | None, input_feed: dict[str, np.ndarray]) -> list[np.ndarray]:
        self.feeds.append(input_feed)
        return [self._compute(input_feed)]


def test_feeds_pad_and_only_pass_inputs_the_graph_declares() -> None:
    session = FakeSession(["input_ids", "attention_mask"], lambda f: None)
    encodings = WhitespaceTokenizer().encode_batch(["a b c", "a"])
    arrays = feeds(session, encodings)
    assert set(arrays) == {"input_ids", "attention_mask"}
    assert arrays["input_ids"].shape == (2, 5)
    assert arrays["attention_mask"].tolist() == [[1, 1, 1, 1, 1], [1, 1, 1, 0, 0]]


def test_embedder_mean_pools_under_the_mask_and_normalizes() -> None:
    def states(f: dict[str, np.ndarray]) -> np.ndarray:
        # Token state = [1, position]; padding positions get a huge value that pooling must ignore.
        batch, length = f["input_ids"].shape
        out = np.zeros((batch, length, 2), dtype=np.float32)
        out[:, :, 0] = 1.0
        out[:, :, 1] = np.arange(length)
        out[f["attention_mask"] == 0] = 1e6
        return out

    session = FakeSession(["input_ids", "attention_mask", "token_type_ids"], states)
    embedder = OnnxEmbedder(session, WhitespaceTokenizer(), dim=2, version="e5-small-r1")
    vectors = embedder.embed(["a b c", "a"])
    expected = np.array([[1.0, 2.0], [1.0, 1.0]])  # mean positions over 5 and 3 real tokens
    expected /= np.linalg.norm(expected, axis=1, keepdims=True)
    np.testing.assert_allclose(vectors, expected, rtol=1e-6)
    assert "token_type_ids" in session.feeds[0]
    assert embedder.version == "onnx:e5-small-r1"


def test_embedder_accepts_graphs_with_pooling_inside() -> None:
    session = FakeSession(["input_ids", "attention_mask"], lambda f: np.full((len(f["input_ids"]), 3), 2.0))
    vectors = OnnxEmbedder(session, WhitespaceTokenizer(), dim=3, version="v").embed(["x"])
    np.testing.assert_allclose(vectors, [[1 / np.sqrt(3)] * 3], rtol=1e-6)
    assert OnnxEmbedder(session, WhitespaceTokenizer(), dim=3, version="v").embed([]).shape == (0, 3)


def test_embedder_runs_chunks_of_similar_length_and_keeps_the_input_order() -> None:
    def states(f: dict[str, np.ndarray]) -> np.ndarray:
        # Token state = [1, token id]; padding gets a huge value that pooling must ignore.
        out = np.stack([np.ones_like(f["input_ids"]), f["input_ids"]], axis=-1).astype(np.float32)
        out[f["attention_mask"] == 0] = 1e6
        return out

    session = FakeSession(["input_ids", "attention_mask"], states)
    embedder = OnnxEmbedder(session, WhitespaceTokenizer(), dim=2, version="v", chunk=2)
    texts = ["a b c d", "e", "f g", "h", "i j k"]
    vectors = embedder.embed(texts)
    shapes = [f["input_ids"].shape for f in session.feeds]
    assert shapes == [(2, 3), (2, 5), (1, 6)]  # 1+1 words, then 2+3, then 4, plus [CLS] and [SEP]
    alone = np.vstack([embedder.embed([t]) for t in texts])
    np.testing.assert_allclose(vectors, alone, rtol=1e-6)


def test_loader_caps_tokens_at_the_length_the_model_was_trained_with(tiny_embed_model: Any) -> None:
    from niadra_models.onnx import runtime

    text = "oi pedido entrega senha atrasou chegou trocar oi"
    uncapped = runtime.load(tiny_embed_model(), max_tokens=6, threads=1)
    assert len(uncapped.tokenizer.encode_batch([text])[0].ids) == 6
    capped = runtime.load(tiny_embed_model(max_seq_length=4), max_tokens=6, threads=1)
    assert len(capped.tokenizer.encode_batch([text])[0].ids) == 4
    assert capped.config == {"hidden_size": 4}
    assert capped.version == "tiny-r1"


LABELS = {0: "O", 1: "B-PER", 2: "I-PER", 3: "B-LOC", 4: "I-LOC", 5: "B-MISC"}


def tagger(tags_by_word: dict[str, int], confidence: float = 8.0) -> Any:
    """Logits that put `confidence` on the tag chosen for each word id (ids are 1000 + char start)."""

    def compute(f: dict[str, np.ndarray]) -> np.ndarray:
        ids = f["input_ids"]
        logits = np.zeros((*ids.shape, len(LABELS)), dtype=np.float32)
        for (row, col), token in np.ndenumerate(ids):
            logits[row, col, tags_by_word.get(int(token), 0)] = confidence
        return logits

    return compute


def test_ner_joins_bio_tags_into_spans_with_original_offsets() -> None:
    text = "Falei com Maria Souza em Belo Horizonte ontem"
    tags = {
        1000 + text.index("Maria"): 1,
        1000 + text.index("Souza"): 2,
        1000 + text.index("Belo"): 3,
        1000 + text.index("Horizonte"): 4,
    }
    ner = OnnxNer(
        FakeSession(["input_ids", "attention_mask"], tagger(tags)),
        WhitespaceTokenizer(),
        LABELS,
        "ner-r1",
        0.6,
    )
    (spans,) = ner.detect([text])
    assert [(text[s.start : s.end], s.kind) for s in spans] == [
        ("Maria Souza", "person"),
        ("Belo Horizonte", "location"),
    ]


def test_ner_drops_low_confidence_and_misc() -> None:
    text = "Maria comprou Coca"
    tags = {1000: 1, 1000 + text.index("Coca"): 5}
    session = FakeSession(["input_ids", "attention_mask"], tagger(tags, confidence=0.5))
    assert OnnxNer(session, WhitespaceTokenizer(), LABELS, "v", min_score=0.6).detect([text]) == [[]]


def test_ner_reads_every_overflowing_piece_of_a_long_text() -> None:
    text = "a b c d Maria e f g h Joana"
    tags = {1000 + text.index("Maria"): 1, 1000 + text.index("Joana"): 1}
    session = FakeSession(["input_ids", "attention_mask"], tagger(tags))
    ner = OnnxNer(session, WhitespaceTokenizer(max_words=5), LABELS, "v", 0.6)
    (spans,) = ner.detect([text])
    assert [text[s.start : s.end] for s in spans] == ["Maria", "Joana"]
    assert session.feeds[0]["input_ids"].shape[0] == 2  # both pieces in one session call


def test_entity_names_and_config() -> None:
    assert entity_kind("PER") == "person"
    assert entity_kind("GPE") == "location"
    assert entity_kind("MISC") is None
    assert entity_kind("EMAIL") == "email"
    assert id2label({"id2label": {"0": "O", "1": "B-PER"}}) == {0: "O", 1: "B-PER"}


def test_loader_reads_a_real_tokenizer_and_version(tmp_path: Path) -> None:
    tokenizers = pytest.importorskip("tokenizers")
    onnx = pytest.importorskip("onnx")
    from niadra_models.onnx import runtime

    model = tokenizers.Tokenizer(tokenizers.models.WordLevel({"[UNK]": 0, "oi": 1}, unk_token="[UNK]"))
    model.pre_tokenizer = tokenizers.pre_tokenizers.Whitespace()
    model.save(str(tmp_path / "tokenizer.json"))
    graph = onnx.helper.make_graph(
        [onnx.helper.make_node("Identity", ["input_ids"], ["out"])],
        "g",
        [onnx.helper.make_tensor_value_info("input_ids", onnx.TensorProto.INT64, ["b", "s"])],
        [onnx.helper.make_tensor_value_info("out", onnx.TensorProto.INT64, ["b", "s"])],
    )
    # Pinned to an IR version every supported onnxruntime reads.
    proto = onnx.helper.make_model(graph, ir_version=9, opset_imports=[onnx.helper.make_opsetid("", 17)])
    onnx.save(proto, str(tmp_path / "model.onnx"))
    (tmp_path / "VERSION").write_text("tiny-r1\n")

    loaded = runtime.load(tmp_path, max_tokens=8, threads=1)
    encodings = loaded.tokenizer.encode_batch(["oi oi x"])
    assert encodings[0].ids == [1, 1, 0]
    assert loaded.version == "tiny-r1"
    assert loaded.session.run(None, feeds(loaded.session, encodings))[0].tolist() == [[1, 1, 0]]
