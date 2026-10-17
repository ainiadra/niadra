"""`ModelsClient` over the internal HTTP API of `niadra-models` (5.6, 06 4.5).

Timeouts are short on purpose: every caller has a conservative fallback ("extract", "sensitive",
"suspicious"), so a slow model must turn into a quick `Unavailable`, never into a slow pipeline.

Every request fits the pod's limits (`domain.models_input`): more texts than one request takes go in
several requests at once, and a longer text is read in overlapping windows, its PII spans put back on its
own offsets. A text to embed is cut to the limit instead: the encoder reads only its first few hundred
tokens, so the vector is the same.

A failure (connection, timeout, status, malformed body) raises `Unavailable`. A refused input (413, 422)
raises `ModelsRefused`: sending it again would be refused again, so no caller retries it.
"""

import asyncio
from collections import Counter
from collections.abc import Callable, Coroutine, Sequence
from typing import Any

import httpx

from niadra.domain.common.errors import ModelsRefused, NiadraError, Unavailable
from niadra.domain.models_input import (
    MAX_CHARS,
    MAX_TEXTS,
    OVERLAP,
    groups,
    merge_spans,
    split,
)
from niadra.ports.infra import PiiSpan

# A model spends a few milliseconds of CPU on each text, so every batch call adds this to the timeout per
# text: a batch of 64 items can finish, while a server that hangs still fails in about half a second. PII
# shares the pod's threads with the embedding batches, so an extraction's batch of PII texts waits behind
# them too (release 2026.09.28-1750-da68f12 timed its PII calls out at a flat 0.5 s).
PER_TEXT_S = 0.02
# A search encodes only its question, one short text that takes the model a few milliseconds. It goes in the
# pod's question lane, never behind the write side's batches. The whole call, connection included, has this
# deadline: the read path then ranks by keywords instead of waiting on an encoder the node starves.
QUERY_TIMEOUT_S = 0.1
# The statuses of an input the pod will never take: too large, or not the shape the task reads.
REFUSED = frozenset({413, 422})


class HttpModelsClient:
    def __init__(
        self,
        base_url: str,
        timeout_s: float = 0.5,
        per_text_s: float = PER_TEXT_S,
        query_timeout_s: float = QUERY_TIMEOUT_S,
        *,
        max_texts: int = MAX_TEXTS,
        max_chars: int = MAX_CHARS,
        overlap: int = OVERLAP,
    ) -> None:
        self._http = httpx.AsyncClient(base_url=base_url, timeout=httpx.Timeout(timeout_s))
        self._timeout_s = timeout_s
        self._per_text_s = per_text_s
        self._query_timeout_s = query_timeout_s
        self._max_texts = max_texts
        self._max_chars = max_chars
        self._overlap = overlap

    async def aclose(self) -> None:
        await self._http.aclose()

    async def embed(self, texts: Sequence[str]) -> tuple[list[list[float]], str]:
        if not texts:
            return [], ""
        answers = await self._each([t[: self._max_chars] for t in texts], self._embed)
        versions = {version for _, version in answers}
        if len(versions) != 1:
            raise Unavailable("models: /v1/embed answered with more than one model version")
        return [v for vectors, _ in answers for v in vectors], versions.pop()

    async def _embed(self, texts: Sequence[str]) -> tuple[list[list[float]], str]:
        return _vectors(await self._post("/v1/embed", texts, self._batch_timeout(len(texts))), len(texts))

    def _batch_timeout(self, count: int) -> float:
        return self._timeout_s + self._per_text_s * count

    async def embed_query(self, text: str) -> tuple[list[float], str]:
        try:
            async with asyncio.timeout(self._query_timeout_s):
                body = await self._post(
                    "/v1/embed", [text[: self._max_chars]], self._query_timeout_s, question=True
                )
        except TimeoutError as error:
            raise Unavailable("models: /v1/embed timed out") from error
        [vector], version = _vectors(body, 1)
        return vector, version

    async def pii(self, texts: Sequence[str]) -> list[list[PiiSpan]]:
        if not texts:
            return []
        windows = split(texts, self._max_chars, self._overlap)
        found = await self._each([w.text for w in windows], self._pii)
        spans: list[list[tuple[int, int, str]]] = [[] for _ in texts]
        for window, got in zip(windows, (each for batch in found for each in batch), strict=True):
            spans[window.source] += [(s.start + window.start, s.end + window.start, s.kind) for s in got]
        read = Counter(w.source for w in windows)
        return [
            [PiiSpan(*s) for s in (merge_spans(text) if read[i] > 1 else text)]
            for i, text in enumerate(spans)
        ]

    async def _pii(self, texts: Sequence[str]) -> list[list[PiiSpan]]:
        body = await self._post("/v1/pii", texts, self._batch_timeout(len(texts)))
        try:
            return [
                [PiiSpan(int(s["start"]), int(s["end"]), str(s["kind"])) for s in spans]
                for spans in _field(body, "spans", "/v1/pii", len(texts))
            ]
        except (KeyError, TypeError, ValueError) as error:
            raise Unavailable("models: malformed response from /v1/pii") from error

    async def _each[R](
        self, texts: Sequence[str], call: Callable[[Sequence[str]], Coroutine[Any, Any, R]]
    ) -> list[R]:
        """`call` on the texts in requests the pod takes, sent at once; the answers in the texts' order. One
        request failing fails the call and cancels the others."""
        batches = groups(texts, self._max_texts)
        if len(batches) == 1:
            return [await call(batches[0])]
        try:
            async with asyncio.TaskGroup() as tg:
                tasks: list[asyncio.Task[R]] = [tg.create_task(call(batch)) for batch in batches]
        except BaseExceptionGroup as group:
            raise _first(group) from None
        return [t.result() for t in tasks]

    async def _post(
        self, path: str, texts: Sequence[str], timeout_s: float, *, question: bool = False
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"texts": list(texts)}
        if question:
            payload["query"] = True
        try:
            response = await self._http.post(path, json=payload, timeout=httpx.Timeout(timeout_s))
            response.raise_for_status()
            body = response.json()
        except httpx.HTTPStatusError as error:
            status = error.response.status_code
            if status in REFUSED:
                # Counts only: the texts are the customer's.
                longest = max((len(t) for t in texts), default=0)
                detail = f"{len(texts)} texts, the longest of {longest} characters"
                raise ModelsRefused(f"models: {path} refused {detail} ({status})") from error
            raise Unavailable(f"models: {path} answered {status}") from error
        except httpx.TimeoutException as error:
            raise Unavailable(f"models: {path} timed out") from error
        except (httpx.HTTPError, ValueError) as error:
            raise Unavailable(f"models: {path} failed ({type(error).__name__})") from error
        if not isinstance(body, dict):
            raise Unavailable(f"models: malformed response from {path}")
        return body


def _first(group: BaseExceptionGroup[BaseException]) -> BaseException:
    """The error a failed call of several requests raises: a refusal before an outage, since it is final."""
    errors = list(_leaves(group))
    for kind in (ModelsRefused, NiadraError):
        for error in errors:
            if isinstance(error, kind):
                return error
    return errors[0]


def _leaves(error: BaseException) -> list[BaseException]:
    if isinstance(error, BaseExceptionGroup):
        return [leaf for inner in error.exceptions for leaf in _leaves(inner)]
    return [error]


def _vectors(body: dict[str, Any], expected: int) -> tuple[list[list[float]], str]:
    try:
        vectors = [[float(x) for x in v] for v in _field(body, "vectors", "/v1/embed", expected)]
    except (TypeError, ValueError) as error:
        raise Unavailable("models: malformed response from /v1/embed") from error
    return vectors, _version(body, "/v1/embed")


def _field(body: dict[str, Any], name: str, path: str, expected: int) -> list[Any]:
    """One result per input, or the whole response is unusable: a short list would shift decisions."""
    value = body.get(name)
    if not isinstance(value, list) or len(value) != expected:
        raise Unavailable(f"models: malformed response from {path}")
    return value


def _version(body: dict[str, Any], path: str) -> str:
    version = body.get("model_version")
    if not isinstance(version, str) or not version:
        raise Unavailable(f"models: response from {path} has no model_version")
    return version
