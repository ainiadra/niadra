"""What encoding one short customer turn costs, the way the read path pays it: through the models pod
(11, WP3). Standard library alone, so the same file runs on a laptop and inside the cluster:

    # Open loop at a fixed rate (run it from the read pod, which reaches the pod):
    kubectl -n niadra exec -i deploy/read -- python - --url http://models:8080 \
        --rate 10 --seconds 60 < models/scripts/query_embed_bench.py

    # Same, with the write side's batches running at the same time (items of 64 texts every 2 s):
    ... --url http://models:8080 --rate 25 --seconds 60 --batch-size 64 --batch-every 2

Each run prints one JSON object: latency percentiles in milliseconds, how many calls missed the deadline
(30 ms, the semantic channel's budget) and errors. The texts are made-up customer turns in Portuguese,
English and Spanish; nothing is read from a database.
"""

import argparse
import contextlib
import http.client
import json
import math
import queue
import sys
import threading
import time
import urllib.parse
from collections.abc import Callable, Sequence
from typing import Any

QUERIES = [
    "qual foi o protocolo que me passaram por e-mail?",
    "minha internet caiu de novo hoje de manhã",
    "quantas vezes eu já reclamei do técnico?",
    "o que eu relatei depois da vistoria?",
    "qual o CEP de coleta que vocês têm hoje?",
    "quero a segunda via da fatura de agosto",
    "o reembolso que prometeram já caiu?",
    "vocês mudaram meu plano sem avisar",
    "preciso remarcar a instalação para sábado",
    "o comprovante do pagamento de ontem chegou?",
    "what was the ticket number you emailed me?",
    "my order still has not arrived",
    "how many times did I call about the router?",
    "can you cancel the card I reported stolen?",
    "the refund you promised last week is missing",
    "cuál es el número de pedido que me enviaron?",
    "el técnico no vino otra vez",
    "quiero cambiar la dirección de entrega",
    "sigo sin recibir la factura de septiembre",
    "cuántas veces reclamé por el cobro duplicado?",
]
DEADLINE_MS = 30.0


def percentile(values: Sequence[float], p: float) -> float:
    if not values:
        return math.nan
    ordered = sorted(values)
    rank = max(0, math.ceil(p / 100 * len(ordered)) - 1)
    return ordered[rank]


def summary(
    name: str, latencies: list[float], errors: int, deadline_ms: float, extra: dict[str, Any]
) -> dict[str, Any]:
    return {
        "mode": name,
        "calls": len(latencies) + errors,
        "errors": errors,
        "p50_ms": round(percentile(latencies, 50), 2),
        "p95_ms": round(percentile(latencies, 95), 2),
        "p99_ms": round(percentile(latencies, 99), 2),
        "max_ms": round(max(latencies, default=math.nan), 2),
        "deadline_ms": deadline_ms,
        "over_deadline": sum(1 for v in latencies if v > deadline_ms) + errors,
        "over_deadline_pct": round(
            100 * (sum(1 for v in latencies if v > deadline_ms) + errors) / max(1, len(latencies) + errors), 2
        ),
        **extra,
    }


def open_loop(
    call: Callable[[str], None], rate: float, seconds: float, workers: int
) -> tuple[list[float], int]:
    """Calls start at a fixed rate whether or not earlier ones finished, as reads arrive in production:
    a slow encoder shows up as queueing, which a closed loop would hide. Latency counts from the planned
    start, so time spent waiting for a free worker counts too."""
    planned: queue.Queue[float | None] = queue.Queue()
    latencies: list[float] = []
    errors = 0
    lock = threading.Lock()

    def worker(index: int) -> None:
        nonlocal errors
        n = index
        while (start := planned.get()) is not None:
            text = QUERIES[n % len(QUERIES)]
            n += workers
            try:
                call(text)
                elapsed = (time.perf_counter() - start) * 1000
                with lock:
                    latencies.append(elapsed)
            except Exception:
                with lock:
                    errors += 1

    threads = [threading.Thread(target=worker, args=(i,), daemon=True) for i in range(workers)]
    for t in threads:
        t.start()
    interval = 1.0 / rate
    begin = time.perf_counter()
    total = int(rate * seconds)
    for i in range(total):
        at = begin + i * interval
        if (wait := at - time.perf_counter()) > 0:
            time.sleep(wait)
        planned.put(at)
    for _ in threads:
        planned.put(None)
    for t in threads:
        t.join()
    return latencies, errors


class HttpEmbed:
    """One keep-alive connection per thread, like the read process's connection pool."""

    def __init__(self, url: str, timeout_s: float, *, question: bool = False) -> None:
        parsed = urllib.parse.urlparse(url)
        self._question = question
        self._host = parsed.hostname or "localhost"
        self._port = parsed.port or 80
        self._timeout = timeout_s
        self._local = threading.local()

    def _conn(self) -> http.client.HTTPConnection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = http.client.HTTPConnection(self._host, self._port, timeout=self._timeout)
            self._local.conn = conn
        return conn

    def __call__(self, texts: list[str]) -> list[list[float]]:
        # A question goes in the pod's own lane for them, as the read path's client sends it.
        body = json.dumps({"texts": texts, "query": True} if self._question else {"texts": texts})
        conn = self._conn()
        try:
            conn.request("POST", "/v1/embed", body, {"Content-Type": "application/json"})
            response = conn.getresponse()
            data = response.read()
        except Exception:
            conn.close()
            self._local.conn = None
            raise
        if response.status != 200:
            raise RuntimeError(f"status {response.status}")
        vectors: list[list[float]] = json.loads(data)["vectors"]
        return vectors


def background_batches(embed: HttpEmbed, size: int, every_s: float, stop: threading.Event) -> None:
    """The write side's item embeds: one batch of `size` item-length texts every `every_s` seconds."""
    item = " ".join(QUERIES[:6])  # about the length of an episode summary
    while not stop.wait(every_s):
        # A failed batch only means less background load; the questions' latencies are what is measured.
        with contextlib.suppress(Exception):
            embed([f"{item} {i}" for i in range(size)])


def run_http(args: argparse.Namespace) -> dict[str, Any]:
    HttpEmbed(args.url, timeout_s=60)(["aquecimento"])  # a cold pod may take seconds for its first call
    embed = HttpEmbed(args.url, timeout_s=args.timeout_ms / 1000, question=True)
    stop = threading.Event()
    bg = None
    if args.batch_size:
        bg_embed = HttpEmbed(args.url, timeout_s=30)
        bg = threading.Thread(
            target=background_batches, args=(bg_embed, args.batch_size, args.batch_every, stop), daemon=True
        )
        bg.start()
    latencies, errors = open_loop(lambda t: embed([t]), args.rate, args.seconds, args.workers)
    stop.set()
    return summary(
        "http",
        latencies,
        errors,
        args.deadline_ms,
        {
            "rate": args.rate,
            "url": args.url,
            "batch_size": args.batch_size,
            "batch_every_s": args.batch_every,
        },
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--rate", type=float, default=10.0, help="questions per second")
    parser.add_argument("--seconds", type=float, default=30.0)
    parser.add_argument("--workers", type=int, default=16, help="concurrent callers at most")
    parser.add_argument("--deadline-ms", type=float, default=DEADLINE_MS)
    parser.add_argument("--url", default="http://models:8080")
    parser.add_argument("--timeout-ms", type=float, default=2000.0, help="per call; the read path uses 100")
    parser.add_argument(
        "--batch-size", type=int, default=0, help="background write-side batch size, 0 for none"
    )
    parser.add_argument("--batch-every", type=float, default=2.0, help="seconds between background batches")
    print(json.dumps(run_http(parser.parse_args(argv))))
    return 0


if __name__ == "__main__":
    sys.exit(main())
