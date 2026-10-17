"""Loading ONNX models exported from Hugging Face, and turning tokenizer output into session inputs.

`onnxruntime` and `tokenizers` are imported only inside the loaders: the default install serves the
rule backends without them (the `onnx` extra adds them). Sessions and tokenizers are typed by the
small protocols below, which is also what lets the tests use a fake session.
"""

import json
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import numpy as np
import numpy.typing as npt


class Encoding(Protocol):
    @property
    def ids(self) -> list[int]: ...

    @property
    def attention_mask(self) -> list[int]: ...

    @property
    def type_ids(self) -> list[int]: ...

    @property
    def offsets(self) -> list[tuple[int, int]]: ...

    @property
    def overflowing(self) -> Sequence["Encoding"]: ...


class Tokenizer(Protocol):
    def encode_batch(self, texts: list[str], /) -> Sequence[Encoding]: ...


class InputSpec(Protocol):
    name: str


class Session(Protocol):
    def get_inputs(self) -> list[InputSpec]: ...

    def run(self, output_names: list[str] | None, input_feed: dict[str, Any]) -> list[npt.NDArray[Any]]: ...


def feeds(session: Session, pieces: Sequence[Encoding]) -> dict[str, npt.NDArray[np.int64]]:
    """Right-pads the pieces to one length and names the arrays the way the exported graph expects."""
    length = max((len(p.ids) for p in pieces), default=0)
    arrays = {
        "input_ids": np.zeros((len(pieces), length), dtype=np.int64),
        "attention_mask": np.zeros((len(pieces), length), dtype=np.int64),
        "token_type_ids": np.zeros((len(pieces), length), dtype=np.int64),
    }
    for row, piece in enumerate(pieces):
        n = len(piece.ids)
        arrays["input_ids"][row, :n] = piece.ids
        arrays["attention_mask"][row, :n] = piece.attention_mask
        arrays["token_type_ids"][row, :n] = piece.type_ids
    # BERT-style graphs take token_type_ids; RoBERTa and ModernBERT exports do not.
    return {i.name: arrays[i.name] for i in session.get_inputs() if i.name in arrays}


@dataclass(frozen=True, slots=True)
class LoadedModel:
    session: Session
    tokenizer: Tokenizer
    config: dict[str, Any]
    version: str


def load(model_dir: Path, max_tokens: int, threads: int, stride: int = 0) -> LoadedModel:
    """Reads `model.onnx`, `tokenizer.json`, optional `config.json` and an optional `VERSION` file.

    Without a `VERSION` file the directory name is the version, so a directory per release
    (`e5-small-v2-r3/`) is enough to keep stored vectors traceable (L13). A sentence-transformers
    `sentence_bert_config.json` lowers `max_tokens` to the length the model was trained with, which
    is also the length its published scores were measured at.
    """
    import onnxruntime as ort
    from tokenizers import Tokenizer as HfTokenizer

    options = ort.SessionOptions()
    options.intra_op_num_threads = threads
    # Idle intra-op threads sleep instead of spinning: the machine's few cores serve every pod.
    options.add_session_config_entry("session.intra_op.allow_spinning", "0")
    wanted = ("CUDAExecutionProvider", "CPUExecutionProvider")
    providers = [p for p in wanted if p in ort.get_available_providers()]
    session = ort.InferenceSession(str(model_dir / "model.onnx"), options, providers=providers)

    trained = _json(model_dir / "sentence_bert_config.json").get("max_seq_length")
    tokenizer = HfTokenizer.from_file(str(model_dir / "tokenizer.json"))
    tokenizer.no_padding()
    tokenizer.enable_truncation(max_length=min(max_tokens, int(trained or max_tokens)), stride=stride)

    version_file = model_dir / "VERSION"
    version = version_file.read_text().strip() if version_file.exists() else model_dir.name
    return LoadedModel(session, tokenizer, _json(model_dir / "config.json"), version)


def _json(path: Path) -> dict[str, Any]:
    data: dict[str, Any] = json.loads(path.read_text()) if path.exists() else {}
    return data
