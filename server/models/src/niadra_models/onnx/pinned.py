"""The ONNX models an image can bake in, pinned by Hugging Face revision and SHA-256.

The Dockerfile runs this file with the standard library alone while it builds the image, so a pod never
downloads a model when it starts. The same command fetches a model for the quality test:

    uv run python src/niadra_models/onnx/pinned.py paraphrase-multilingual-minilm-l12-v2-r1 ~/.cache/niadra

The name is the model version: it goes into `VERSION`, into every `/v1/embed` response and into the
`embedding_model` of each stored vector, which search compares before it uses a vector. Anything that can
change a vector (another file, revision or token limit) needs a new name.

The OCR recognizer is baked the same way when the image carries the reader (`--build-arg OCR=1`):

    uv run python src/niadra_models/onnx/pinned.py pp-ocrv6-small-rec-r1 ~/.cache/niadra
"""

import hashlib
import sys
import urllib.request
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

HUB = "https://huggingface.co"
OCR_REC_MODEL = "pp-ocrv6-small-rec-r1"
_CHUNK = 1 << 20


@dataclass(frozen=True, slots=True)
class PinnedFile:
    path: str  # inside the repository, at the pinned revision
    sha256: str


@dataclass(frozen=True, slots=True)
class PinnedModel:
    repository: str
    revision: str  # a full commit, never a branch
    license: str
    files: Mapping[str, PinnedFile]  # by their name in the model directory
    keys_sha256: str | None = None
    """An OCR recognizer's character list, written to `keys.txt` from its `inference.yml` (one character per
    line, the order of the model's output classes) and checked against this checksum."""


MODELS: dict[str, PinnedModel] = {
    # 384 dimensions, mean pooling, no instruction prefixes, 50+ languages including Portuguese and
    # Spanish. model.onnx is the upstream optimum export with dynamic int8 quantization (QInt8 weights);
    # the repository's arm64 and avx512 files are the same bytes. sentence_bert_config.json carries the
    # 128-token limit the model was trained with, which the loader applies.
    "paraphrase-multilingual-minilm-l12-v2-r1": PinnedModel(
        repository="sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
        revision="e8f8c211226b894fcb81acc59f3b34ba3efd5f42",
        license="Apache-2.0",
        files={
            "model.onnx": PinnedFile(
                "onnx/model_qint8_avx512_vnni.onnx",
                "783fea82d71a58179b830a4dbd2d58447e640609e98eedf9ffa12622d375a672",
            ),
            "tokenizer.json": PinnedFile(
                "tokenizer.json", "2c3387be76557bd40970cec13153b3bbf80407865484b209e655e5e4729076b8"
            ),
            "config.json": PinnedFile(
                "config.json", "6300193cb75e01cf80c96decef7187dfb33094d97cc1490b7ead6ff134476e4e"
            ),
            "sentence_bert_config.json": PinnedFile(
                "sentence_bert_config.json",
                "70f4448f31320443fe3557cacea5abf2dcc4915dda8c80646bec9f3bb0aa5a1f",
            ),
        },
    ),
    # Text recognition for the document reader (niadra_models.ocr): PaddleOCR's PP-OCRv6 small recognizer,
    # the ONNX export PaddlePaddle publishes, 21 MB. It reads Portuguese accents and keeps the spaces between
    # words, which RapidOCR's bundled Chinese and English recognizer loses. The detector and the angle
    # classifier stay the ones inside the RapidOCR wheel.
    OCR_REC_MODEL: PinnedModel(
        repository="PaddlePaddle/PP-OCRv6_small_rec_onnx",
        revision="b8f84f0b80c529de40b4fbb3544b84fa7233a513",
        license="Apache-2.0",
        files={
            "model.onnx": PinnedFile(
                "inference.onnx", "5435fd747c9e0efe15a96d0b378d5bd157e9492ed8fd80edf08f30d02fa24634"
            ),
            "inference.yml": PinnedFile(
                "inference.yml", "ab078671bb49f06228eadccd34f1bb501e157f7a047095ffb943ba81512c77d1"
            ),
        },
        keys_sha256="b5f2bfe2bdd9448429e3e82b51c789775d9b42f2403d082b00662eb77e401c5d",
    ),
}


def fetch(name: str, into: Path, *, models: Mapping[str, PinnedModel] = MODELS, hub: str = HUB) -> Path:
    """Downloads model `name` into `into/name`, checks every file and writes `VERSION`.

    Files already there with the pinned checksum are kept, so a second run needs no network.
    """
    model = models[name]
    target = into / name
    target.mkdir(parents=True, exist_ok=True)
    for filename, pinned in model.files.items():
        dest = target / filename
        if dest.exists() and _sha256(dest) == pinned.sha256:
            continue
        _download(f"{hub}/{model.repository}/resolve/{model.revision}/{pinned.path}", dest, pinned.sha256)
    if model.keys_sha256 is not None:
        _write_keys(target / "inference.yml", target / "keys.txt", model.keys_sha256)
    (target / "VERSION").write_text(name + "\n")
    return target


def character_keys(inference_yml: str) -> list[str]:
    """`PostProcess.character_dict` of a PaddleOCR `inference.yml`, read without a YAML library (this file
    runs on the standard library alone). The list holds one scalar per line, plain or single-quoted; any
    other form fails here, and a wrong reading fails the pinned checksum."""
    lines = inference_yml.split("\n")
    try:
        at = lines.index("  character_dict:", lines.index("PostProcess:"))
    except ValueError:
        raise ValueError("inference.yml has no PostProcess.character_dict") from None
    keys: list[str] = []
    for line in lines[at + 1 :]:
        if not line.startswith("  - "):
            break
        value = line[4:]
        if value.startswith("'"):
            if len(value) < 2 or not value.endswith("'"):
                raise ValueError(f"unexpected entry in character_dict: {value!r}")
            value = value[1:-1].replace("''", "'")
        elif value.startswith(('"', "|", ">", "&", "*", "!")) or value.strip(" \t") != value:
            raise ValueError(f"unexpected entry in character_dict: {value!r}")
        keys.append(value)
    if not keys:
        raise ValueError("inference.yml has an empty character_dict")
    return keys


def _write_keys(source: Path, dest: Path, sha256: str) -> None:
    content = ("\n".join(character_keys(source.read_text(encoding="utf-8"))) + "\n").encode()
    digest = hashlib.sha256(content).hexdigest()
    if digest != sha256:
        raise ValueError(f"{dest.name}: sha256 {digest} is not the pinned {sha256}")
    dest.write_bytes(content)


def _download(url: str, dest: Path, sha256: str) -> None:
    if not url.startswith(("https://", "file://")):
        raise ValueError(f"refusing to download {dest.name} from {url}")
    partial = dest.with_name(f".{dest.name}.partial")
    digest = hashlib.sha256()
    try:
        with urllib.request.urlopen(url, timeout=60) as response, partial.open("wb") as out:  # noqa: S310
            while chunk := response.read(_CHUNK):
                digest.update(chunk)
                out.write(chunk)
        if digest.hexdigest() != sha256:
            raise ValueError(f"{dest.name}: sha256 {digest.hexdigest()} is not the pinned {sha256}")
        partial.replace(dest)
    finally:
        partial.unlink(missing_ok=True)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(_CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 2 or args[0] not in MODELS:
        print(f"usage: pinned.py <model> <directory>, where <model> is one of: {', '.join(MODELS)}")
        return 2
    print(fetch(args[0], Path(args[1]).expanduser()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
