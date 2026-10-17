"""The models pod's HTTP API as the cell's client meets it, under respx: the pod's limits (413 for more than
`max_texts` texts or a text longer than `max_chars`, as `niadra_models.api._check`), e-mails and cards found
by rules, and every request recorded by its size only."""

import json
import re
from dataclasses import dataclass, field

import httpx
import respx

BASE = "http://models.cell.internal:8080"

_PII = {
    "email": re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+"),
    "card": re.compile(r"\b(?:\d{4}[ -]?){3}\d{4}\b"),
}


@dataclass(frozen=True, slots=True)
class Request:
    path: str
    texts: int
    longest: int


@dataclass
class EmulatedPod:
    max_texts: int = 256
    max_chars: int = 20_000
    requests: list[Request] = field(default_factory=list)

    def install(self, router: respx.MockRouter | None = None) -> "EmulatedPod":
        (router or respx).post(url__regex=rf"^{re.escape(BASE)}/v1/.*").mock(side_effect=self.answer)
        return self

    def refused(self) -> list[Request]:
        return [r for r in self.requests if r.texts > self.max_texts or r.longest > self.max_chars]

    def answer(self, request: httpx.Request) -> httpx.Response:
        texts: list[str] = json.loads(request.content)["texts"]
        path = request.url.path
        self.requests.append(Request(path, len(texts), max((len(t) for t in texts), default=0)))
        if len(texts) > self.max_texts:
            return httpx.Response(413, json={"detail": f"at most {self.max_texts} texts per request"})
        if any(len(t) > self.max_chars for t in texts):
            return httpx.Response(413, json={"detail": f"at most {self.max_chars} characters per text"})
        if path == "/v1/pii":
            spans = [
                sorted(
                    (
                        {"start": m.start(), "end": m.end(), "kind": kind}
                        for kind, rx in _PII.items()
                        for m in rx.finditer(t)
                    ),
                    key=lambda s: (s["start"], s["end"]),
                )
                for t in texts
            ]
            return httpx.Response(200, json={"spans": spans, "model_version": "rules-test"})
        if path == "/v1/embed":
            vectors = [[float(len(t) % 7), 1.0] for t in texts]
            return httpx.Response(200, json={"vectors": vectors, "dim": 2, "model_version": "embed-test"})
        return httpx.Response(404, json={"detail": "unknown"})
