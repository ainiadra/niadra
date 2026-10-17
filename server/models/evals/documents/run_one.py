"""Reads one document the way the models server does, in a new child process, and prints one JSON line
with the text, the time and the child's peak resident memory. `bench.sh` runs it inside the models image.

    python run_one.py <file>

One file per process: the peak it reports is the largest child this process ever had.
"""

import asyncio
import json
import resource
import sys
import time
from pathlib import Path

from niadra_models import documents
from niadra_models import ocr as ocr_engine
from niadra_models.settings import Settings


async def one(path: Path) -> dict[str, object]:
    settings = Settings.from_env()
    options = ocr_engine.OcrOptions(
        settings.ocr_max_side, settings.ocr_det_side, settings.onnx_threads, settings.ocr_rec_dir
    )
    data = path.read_bytes()
    mime = documents.sniff(data)
    reader = ocr_engine.OcrReader(options)
    started = time.perf_counter()
    text, source, error = None, None, None
    try:
        if mime is None:
            raise documents.UnreadableDocumentError("not a type the reader takes")
        found = await reader.run(documents.read, data, mime, options)
        text, source = found.text, found.source
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"[:400]
    finally:
        reader.close(wait=True)
    elapsed = time.perf_counter() - started
    try:
        cgroup_peak: int | None = int(Path("/sys/fs/cgroup/memory.peak").read_text())
    except OSError:
        cgroup_peak = None
    # ru_maxrss of the children is the largest single child's peak, in KiB on Linux.
    child_peak = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss * 1024
    return {
        "engine": "widened",
        "file": path.name,
        "mime": mime,
        "source": source,
        "text": text,
        "error": error,
        "t_cold": elapsed,
        "child_peak_rss": child_peak,
        "cgroup_peak": cgroup_peak,
        "model_version": ocr_engine.VERSION,
    }


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("usage: run_one.py <file>")
    print("@@RESULT@@" + json.dumps(asyncio.run(one(Path(sys.argv[1]))), ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
