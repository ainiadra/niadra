# niadra-models: the small in-cell models (06 4.5). Build from this directory: `docker build models/`.
# The default image serves the rule backends. `--build-arg EMBED_MODEL=<name>` adds ONNX Runtime and bakes
# that model into /models/<name> at build time (src/niadra_models/onnx/pinned.py pins each name to a
# revision and checksums), so a pod never downloads anything; the deployment points
# NIADRA_MODELS_ONNX_EMBED_DIR at the directory. `--build-arg EXTRAS=--extra=onnx` adds ONNX Runtime alone,
# for model directories mounted from elsewhere (NIADRA_MODELS_ONNX_EMBED_DIR / NIADRA_MODELS_ONNX_NER_DIR).
ARG EMBED_MODEL=""
# `--build-arg OCR=1` adds the document reader (the `ocr` extra: RapidOCR with its detector inside the wheel,
# pypdf, pypdfium2 and the Office readers) and bakes its recognizer, pinned like the embedding model, into
# /models/pp-ocrv6-small-rec-r1, where the settings look for it by default.
ARG OCR=""

# Its own stage, so the download stays cached while the code changes.
FROM python:3.13-slim AS model
ARG EMBED_MODEL
ARG OCR
COPY src/niadra_models/onnx/pinned.py /pinned.py
RUN mkdir /models && if [ -n "$EMBED_MODEL" ]; then python /pinned.py "$EMBED_MODEL" /models; fi
RUN if [ -n "$OCR" ]; then python /pinned.py pp-ocrv6-small-rec-r1 /models; fi

FROM python:3.13-slim AS build
ARG EXTRAS=""
ARG EMBED_MODEL
ARG OCR
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
COPY --from=ghcr.io/astral-sh/uv:0.9 /uv /usr/local/bin/uv
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project $EXTRAS ${EMBED_MODEL:+--extra=onnx} ${OCR:+--extra=ocr}
COPY src ./src
RUN uv sync --frozen --no-dev $EXTRAS ${EMBED_MODEL:+--extra=onnx} ${OCR:+--extra=ocr}

FROM python:3.13-slim
ARG EMBED_MODEL
ARG OCR
RUN useradd --system --uid 10001 --home /app niadra
WORKDIR /app
COPY --from=build --chown=niadra:niadra /app /app
# Owned by root: the service reads the model and cannot change it.
COPY --from=model /models /models
ENV PATH=/app/.venv/bin:$PATH PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 HF_HUB_OFFLINE=1
# A baked model has to load and embed here, so a broken one fails the build and not the pods.
RUN if [ -n "$EMBED_MODEL" ]; then NIADRA_MODELS_ONNX_EMBED_DIR="/models/$EMBED_MODEL" python -c \
      "from niadra_models import registry as r, settings as s; print(r.build(s.Settings.from_env()).embedder.embed(['ok']).shape)"; \
    fi
# The reader has to load here too, for the same reason: an image, a scanned PDF page and the Office readers.
RUN if [ -n "$OCR" ]; then python -m niadra_models.documents; fi
# Numeric, so Kubernetes can check runAsNonRoot without reading /etc/passwd.
USER 10001:10001
EXPOSE 8080
# One worker per container: batching only works when concurrent requests share one process;
# scale with more containers and NIADRA_MODELS_THREADS, not more workers.
HEALTHCHECK --interval=10s --timeout=2s --start-period=20s --retries=3 \
  CMD ["python", "-c", "import urllib.request as u; u.urlopen('http://127.0.0.1:8080/healthz', timeout=1)"]
CMD ["uvicorn", "niadra_models.api:create_app", "--factory", "--host", "0.0.0.0", "--port", "8080", \
     "--workers", "1", "--no-access-log"]
