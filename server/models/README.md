# niadra-models

The small in-cell models (06 4.5): embeddings, PII spans and the document reader (the text of PDFs, images
and Office files). None of them is a language model. The extraction gate, the sensitive category, the
question type, the contradiction check, the injection guard and whether an action does an open item are
decided by the decision model outside the cell (the server's `src/niadra/adapters/outbound/llm/`). One pod per
cell, stateless, behind the internal HTTP API in `src/niadra_models/api.py`.
The image and the pinned models are described in the `Dockerfile` and in `src/niadra_models/onnx/pinned.py`.

## Encoding a customer's turn on the read path

The read path ranks a customer's rows for each turn through exact, lexical, temporal and semantic channels.
The semantic channel needs the turn encoded by the same
model that encoded the stored rows, inside a hard deadline of 30 ms; past it the channel is marked
`skipped` and the turn goes out without it. This section is the measurement that decides whether that
channel is on by default.

### What was measured (25/09/2026)

`scripts/query_embed_bench.py` sends made-up customer turns (PT, EN, ES; 6 to 10 words) at a fixed rate,
open loop, and reports latency percentiles and the share past 30 ms. Measured on a MacBook (Apple M4, arm64) whose other workloads kept the
load average between 10 and 40, in Linux containers limited to the CPUs shown. The cell node is an
m7i-flex.large (2 x86 vCPUs, AVX-512 VNNI, which the pinned int8 export targets), so the absolute numbers
there will differ; the shape will not. The coordinator reruns the same script in the cluster (below).

Through the models pod (`POST /v1/embed`, the pod's 5 ms micro-batching, ONNX on 2 threads):

| Load | p50 | p95 | Past 30 ms |
|---|---|---|---|
| 10 turns/s, pod idle otherwise, 2 CPUs | 20 to 22 ms | 43 to 49 ms | 14 to 15% |
| 25 turns/s, pod idle otherwise, 2 CPUs | 19 to 20 ms | 27 to 42 ms | 2 to 11% |
| 10 turns/s, plus the write side's batches (64 texts every 2 s) | 22 to 26 ms | 0.74 to 1.08 s | 32 to 44% |
| 25 turns/s, plus the same batches | 264 ms | 1.7 s | 70% (3% failed at 2 s) |
| 5 turns/s, pod held to 0.1 CPU (its share of a saturated node at a 50m request) | 1.1 s | 1.7 s | 100% |

The pod ran one embedding batch at a time: a turn that arrived while a batch of item texts was being
encoded waited for the whole batch. That, not the model, was the tail. A question encoded in a batch is also
not bit-for-bit the question encoded alone (dynamic int8 quantization scales over the whole batch; cosine
0.993 to 0.999 between the two). Since 09/10/2026 a search's question has a lane of its own (below).

The rest of the channel is small: the cosine over a customer's rows costs 0.5 ms for 50 rows, 1.3 ms for
200 and 9 ms for 1,000 (p50, Linux, 2 CPUs, standard library over half-precision vectors).

### The per-customer cache, in memory

Measured with `tracemalloc` (the unit test `test_the_estimate_follows_what_python_allocates` keeps the
cache's own estimate within 25% of it), per customer: the half-precision vectors (768 bytes each) and
their map. The value indexes and the temporal anchors come on top, and are small next to the vectors.

| Rows per customer | Per customer | Per 1,000 active customers |
|---|---|---|
| 20 | 20 KiB | 19 MiB |
| 50 | 44 KiB | 43 MiB |
| 200 | 174 KiB | 170 MiB |
| 500 | 439 KiB | 428 MiB |

Until 26/09/2026 an entry also kept the rows' texts and a BM25 index over them, which no channel read
(the lexical slot channel scores the turn's own pool): the same customers took about 2.4 times as much.
The table below was measured then, so a full cache now holds more customers than it says.

Resident memory of the read process's code alone is about 180 to 190 MiB. With the cache full, under 25
concurrent readers doing lookups, BM25, the semantic channel and churn for 30 s (Linux):

| Cache cap | Customers held | Process RSS |
|---|---|---|
| 128 MiB (default), 50 rows each | 1,288 | 319 MiB |
| 1 GiB, 200 rows each | 2,682 | 1,221 MiB |

Loading one customer from PostgreSQL (generation, snapshot, vectors, through the production
repositories): 11 to 30 ms median for 50 rows and 26 to 54 ms for 200, with the laptop at different loads
(`tests/integration/serve/test_retrieval_cache.py`). A load runs off the turn: the first read of a conversation
starts it, and the slots never wait for one.

Since 27/09/2026 a read process without the semantic channel (the default) opens no rows and no vectors: its entry is the customer's settled values and system event
anchors, what the values channel, the guards and the time words read. For the same 200-row customer that is
2.6 KiB loaded in 1.8 ms instead of 172 KiB in 18 ms, so the 128 MiB cap holds tens of thousands of customers.
Only a read inside a conversation, in a view with slots, starts a load; a read outside one (a status check, the
benchmark's settle pass) starts none.

### Decision: the semantic channel is off by default

- Through the pod it cannot meet a p95 of 30 ms whenever the write side is busy (p95 0.7 to 1.7 s) or the
  node is saturated (every turn late). The deadline keeps reads on time, but a channel that is skipped for a
  third of the turns makes the same question fill different slots depending on the load.
- Nothing measures yet what it buys: the benchmark set has no paraphrased questions,
  and the per-channel use measurement (WP5) has no data. The exact and lexical channels answer the
  benchmark's questions without it.

So `NIADRA_SEMANTIC_CHANNEL` defaults to `off` and the retrieval cache to 128 MiB
(`NIADRA_RETRIEVAL_CACHE_MIB`), which fits the read pod's current 512 MiB limit with room. `models` turns
the channel on through the pod; keep it on only if WP5 shows the semantic slots used and the cluster numbers
below hold.

### A search's question in its own lane (09/10/2026)

A history search waits on its question's vector (`POST /v1/history/search`), with a deadline of 100 ms past
which it answers by keywords alone and says `degraded`. On the region benchmark of 09/10 the search's p95 at
25 per second went from 43.5 ms (02/10, a run with nothing else on the cell) to 109 to 154 ms, with 4 to 7% of
the answers degraded; a minute before the history metric the cell's realtime queue held 390 tasks from the
earlier metrics. The search's own code kept its cost (the same repository calls per search, and CPU per call
within 8%, at the commits of 30/09, 02/10 and 09/10, measured in process). Degraded means the question's
encoding passed 100 ms: here it waited behind the write side's batches.

The read path's client now sends a question with `"query": true`, and the pod encodes it in its own
micro-batcher (`embed:question`): it never waits for a batch of item texts, takes the questions already
waiting without waiting for more, and runs on another thread of the pool beside the batch. Measured with
`scripts/query_embed_bench.py` (open loop, 20 s, 2 runs each) against the pod in a Linux container with the
pinned encoder, the share past the read path's 100 ms is what a search answers degraded:

| Pod CPUs, questions, write side | Before: p95, past 100 ms | After: p95, past 100 ms |
|---|---|---|
| 2, 10/s, none | 32 ms, 0% | 20 to 25 ms, 0% |
| 2, 25/s, none | 27 to 28 ms, 0% | 18 to 24 ms, 0% |
| 2, 10/s, 16 texts every 1 s | 177 to 203 ms, 12.5 to 13% | 21 to 22 ms, 0% |
| 2, 25/s, 16 texts every 1 s | 197 to 221 ms, 13.4 to 14.4% | 18 to 19 ms, 0% |
| 2, 10/s, 64 texts every 2 s | 775 to 830 ms, 27.5 to 28% | 19 to 20 ms, 0% |
| 2, 25/s, 64 texts every 2 s | 832 to 1,255 ms, 29.8 to 41.4% | 19 to 21 ms, 0% |
| 1, 10/s, 16 texts every 1 s | 251 ms, 17.5% | 46 ms, 0.5% |
| 1, 25/s, 16 texts every 1 s | 237 ms, 15.2% | 19 ms, 0% |

A question alone also no longer waits the 5 ms the batcher gives other requests to join: its p50 went from
25 to 15 ms at 10 per second on 2 CPUs.

### Rerunning it in the cluster

```bash
# Through the models pod, from the read pod (standard library only):
kubectl -n niadra exec -i deploy/read -- python - --url http://models:8080 \
    --rate 10 --seconds 60 < models/scripts/query_embed_bench.py
kubectl -n niadra exec -i deploy/read -- python - --url http://models:8080 \
    --rate 25 --seconds 60 --batch-size 64 --batch-every 2 < models/scripts/query_embed_bench.py
```

Run each while the benchmark's load runs and once without it. Each prints one JSON line; the decision
above changes if the pod's p95 stays under 30 ms with the write side busy.

## Reading documents

`POST /v1/document-text?type=<mime>` reads a PDF, JPEG, PNG, DOCX, XLSX or PPTX in a new child process per
document (`src/niadra_models/documents.py`, `ocr.py`), the first one the kernel kills if the pod runs short
of memory. A PDF page with a text layer goes through pypdf (layout mode when the page draws a ruled table);
a page without one is rendered at 200 dpi with pypdfium2 and read by RapidOCR with the PP-OCRv6 small
recognizer, its boxes grouped into rows. Office files are opened only within 2,000 entries and 100 MiB
uncompressed, never macro-enabled, openpyxl read-only with cached values. The image carries all of it when
built with `--build-arg OCR=1`, which also bakes the recognizer.

`evals/documents/` reruns the document reader's measurement against this code: eight synthetic
documents with their ground truth, each read in its own container of the models image (2 CPUs, no
network). Measured 27/09/2026 on an Apple M4, natively (linux/arm64) and emulated (linux/amd64, the node's
architecture; memory is representative there, time is not):

| | Before (main) | This reader, arm64 | This reader, amd64 |
|---|---|---|---|
| Key values found | 33 of 76 | 76 of 76 | |
| Table rows on one line | 0 of 20 | 18 of 20 | |
| Child peak RSS, one photo | 424 MB arm64, 499 MB amd64 | 426 MB | 517 MB |
| Child peak RSS, one scanned page | not read | 453 MB | 521 MB |
| Child peak RSS, 36 scanned pages (18.8 MB) | not read | 563 MB, 30 s | 729 MB |
| Child peak RSS, Office file or native PDF | | 39 to 52 MB | 70 MB |

With the pod at 520 to 655 MiB between reads, the 36-page scan peaks near 1.35 GiB, under the 1.5 GiB the
OCR of large photos already reached and inside the 1,792 MiB limit. The two table rows missed are pairs of
the tilted receipt photo, whose prices sit half a line lower than their items; a PDF in two columns written
line by line across both still reads out of order.

```bash
cd models
uv run --no-project --with reportlab --with numpy --with pypdfium2 --with python-docx --with openpyxl \
    --with python-pptx --with pillow python evals/documents/generate.py --stress
evals/documents/bench.sh
uv run python evals/documents/score.py --require-keys 76 --require-rows 18
```
