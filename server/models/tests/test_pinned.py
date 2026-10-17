"""The fetcher against a local copy of a repository: no network."""

import hashlib
import re
from pathlib import Path

import pytest

from niadra_models.onnx import pinned
from niadra_models.onnx.pinned import MODELS, OCR_REC_MODEL, PinnedFile, PinnedModel, fetch


def hub_with(tmp_path: Path, files: dict[str, bytes]) -> tuple[str, PinnedModel]:
    """A directory laid out like `<hub>/<repository>/resolve/<revision>/<path>`, and a model pinned to it."""
    revision = "0" * 40
    for path, content in files.items():
        source = tmp_path / "hub" / "org" / "tiny" / "resolve" / revision / path
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_bytes(content)
    model = PinnedModel(
        repository="org/tiny",
        revision=revision,
        license="MIT",
        files={
            Path(path).name: PinnedFile(path, hashlib.sha256(content).hexdigest())
            for path, content in files.items()
        },
    )
    return (tmp_path / "hub").as_uri(), model


def test_fetch_checks_every_file_and_writes_the_version(tmp_path: Path) -> None:
    hub, model = hub_with(tmp_path, {"onnx/model.onnx": b"graph", "tokenizer.json": b"{}"})
    target = fetch("tiny-r1", tmp_path / "models", models={"tiny-r1": model}, hub=hub)
    assert target == tmp_path / "models" / "tiny-r1"
    assert (target / "model.onnx").read_bytes() == b"graph"
    assert (target / "tokenizer.json").read_bytes() == b"{}"
    assert (target / "VERSION").read_text() == "tiny-r1\n"


def test_files_already_there_are_not_downloaded_again(tmp_path: Path) -> None:
    hub, model = hub_with(tmp_path, {"onnx/model.onnx": b"graph"})
    fetch("tiny-r1", tmp_path / "models", models={"tiny-r1": model}, hub=hub)
    fetch("tiny-r1", tmp_path / "models", models={"tiny-r1": model}, hub=(tmp_path / "gone").as_uri())
    assert (tmp_path / "models" / "tiny-r1" / "model.onnx").read_bytes() == b"graph"


def test_a_checksum_mismatch_fails_and_leaves_nothing_behind(tmp_path: Path) -> None:
    hub, model = hub_with(tmp_path, {"onnx/model.onnx": b"graph"})
    tampered = PinnedModel(
        model.repository,
        model.revision,
        model.license,
        {"model.onnx": PinnedFile("onnx/model.onnx", hashlib.sha256(b"other").hexdigest())},
    )
    with pytest.raises(ValueError, match="sha256"):
        fetch("tiny-r1", tmp_path / "models", models={"tiny-r1": tampered}, hub=hub)
    assert list((tmp_path / "models" / "tiny-r1").iterdir()) == []


def test_only_https_and_local_files_are_fetched(tmp_path: Path) -> None:
    _, model = hub_with(tmp_path, {"onnx/model.onnx": b"graph"})
    with pytest.raises(ValueError, match="refusing"):
        fetch("tiny-r1", tmp_path / "models", models={"tiny-r1": model}, hub="http://example.com")


def test_every_pinned_model_names_a_commit_checksums_and_the_files_the_loader_reads() -> None:
    for name, model in MODELS.items():
        assert re.fullmatch(r"[a-z0-9.-]+-r\d+", name), name
        assert re.fullmatch(r"[0-9a-f]{40}", model.revision), name
        # An OCR recognizer is read by RapidOCR with its character list; an embedder by our loader.
        if model.keys_sha256 is not None:
            assert {"model.onnx", "inference.yml"} <= set(model.files), name
            assert re.fullmatch(r"[0-9a-f]{64}", model.keys_sha256), name
        else:
            assert {"model.onnx", "tokenizer.json", "config.json"} <= set(model.files), name
        assert all(re.fullmatch(r"[0-9a-f]{64}", f.sha256) for f in model.files.values()), name
        assert model.license == "Apache-2.0", name


def test_the_ocr_recognizer_is_pinned_to_its_upstream_export() -> None:
    model = MODELS[OCR_REC_MODEL]
    assert model.repository == "PaddlePaddle/PP-OCRv6_small_rec_onnx"
    assert model.files["model.onnx"] == PinnedFile(
        "inference.onnx", "5435fd747c9e0efe15a96d0b378d5bd157e9492ed8fd80edf08f30d02fa24634"
    )


CHARACTERS = ["!", "'", "a", "\u00e7", "\u3000", " "]
YML = (
    "Global:\n  model_name: tiny_rec\nPostProcess:\n  name: CTCLabelDecode\n  character_dict:\n"
    "  - '!'\n  - ''''\n  - a\n  - \u00e7\n  - \u3000\n  - ' '\n"
)


def test_the_character_list_is_read_from_inference_yml_in_order() -> None:
    assert pinned.character_keys(YML) == CHARACTERS


@pytest.mark.parametrize("entry", ['"\\u0041"', "|", " a", "'open"])
def test_a_character_list_in_a_form_it_does_not_know_fails(entry: str) -> None:
    with pytest.raises(ValueError, match="unexpected entry"):
        pinned.character_keys(YML + f"  - {entry}\n")


def test_a_file_without_a_character_list_fails() -> None:
    with pytest.raises(ValueError, match="character_dict"):
        pinned.character_keys("PostProcess:\n  name: CTCLabelDecode\n")


def test_fetch_writes_the_keys_and_checks_their_checksum(tmp_path: Path) -> None:
    hub, model = hub_with(tmp_path, {"inference.onnx": b"graph", "inference.yml": YML.encode()})
    keys = ("\n".join(CHARACTERS) + "\n").encode()
    files = {"model.onnx": model.files["inference.onnx"], "inference.yml": model.files["inference.yml"]}
    rec = PinnedModel(model.repository, model.revision, "Apache-2.0", files, hashlib.sha256(keys).hexdigest())
    target = fetch("rec-r1", tmp_path / "models", models={"rec-r1": rec}, hub=hub)
    assert (target / "keys.txt").read_bytes() == keys
    wrong = PinnedModel(model.repository, model.revision, "Apache-2.0", files, "0" * 64)
    with pytest.raises(ValueError, match=r"keys.txt: sha256"):
        fetch("rec-r2", tmp_path / "models", models={"rec-r2": wrong}, hub=hub)


def test_the_command_line_names_the_models_it_knows(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert pinned.main(["no-such-model", str(tmp_path)]) == 2
    assert "paraphrase-multilingual-minilm-l12-v2-r1" in capsys.readouterr().out
