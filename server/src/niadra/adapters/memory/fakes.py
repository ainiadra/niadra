"""Deterministic stand-ins for the infrastructure ports, for tests."""

import hashlib
import json
import math
import re
from collections import defaultdict
from collections.abc import AsyncIterator, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from niadra.config.models import SpaceConfig
from niadra.config.observed import with_observed
from niadra.domain.common.errors import Conflict, NotFound, Unavailable
from niadra.domain.decide import Triage
from niadra.domain.objects.words import Words
from niadra.domain.policy.sensitive import sensitive_topics
from niadra.domain.spaces.directory import RETRY_AFTER_S, DbStatus
from niadra.domain.tasks import Task
from niadra.domain.vocabulary import Verification
from niadra.ports.infra import (
    HotLayerState,
    HotRead,
    HotTurn,
    HttpResponse,
    Label,
    LlmResult,
    LlmUsage,
    PiiSpan,
    PresignedUpload,
)


class FixedClock:
    def __init__(self, at: datetime | None = None) -> None:
        self.at = at or datetime(2026, 9, 22, 17, 0, tzinfo=UTC)

    def now(self) -> datetime:
        return self.at

    def advance(self, **delta: float) -> datetime:
        self.at += timedelta(**delta)
        return self.at


class StaticConfig:
    def __init__(self, *configs: SpaceConfig) -> None:
        self._by_id = {c.space_id: c for c in configs}
        self._declared = dict(self._by_id)
        self._observed: dict[UUID, list[dict[str, Any]]] = {}
        self._observed_ops: dict[UUID, list[str]] = {}
        self._observed_words: dict[UUID, Words] = {}
        self._removed: dict[UUID, datetime] = {}
        self._moved: dict[UUID, str] = {}

    def put(self, config: SpaceConfig) -> None:
        """What a snapshot does: the declared configuration, with the space's observed types beneath."""
        self._declared[config.space_id] = config
        self._by_id[config.space_id] = with_observed(
            config,
            self._observed.get(config.space_id, []),
            self._observed_ops.get(config.space_id, []),
            self._observed_words.get(config.space_id),
        )

    def observe(
        self,
        space_id: UUID,
        types: Sequence[Mapping[str, Any]],
        operations: Sequence[str] = (),
        words: Words | None = None,
    ) -> None:
        """What the cell inferred for a space (`app.observed_config`), merged under its declaration."""
        self._observed[space_id] = [dict(t) for t in types]
        self._observed_ops[space_id] = list(operations)
        self._observed_words[space_id] = words or Words()
        if space_id in self._declared:
            self._by_id[space_id] = with_observed(
                self._declared[space_id],
                self._observed[space_id],
                self._observed_ops[space_id],
                self._observed_words[space_id],
            )

    def remove(self, space_id: UUID, at: datetime) -> None:
        """What a snapshot does when the control plane deleted the space."""
        self._by_id.pop(space_id, None)
        self._removed[space_id] = at

    def drop(self, space_id: UUID) -> None:
        """A space missing from a truncated snapshot, not listed as removed."""
        self._by_id.pop(space_id, None)

    def moved(self) -> Mapping[UUID, str]:
        return self._moved

    def move(self, space_id: UUID, to_cell: str) -> None:
        """What a snapshot says after the space moved to another cell."""
        self._by_id.pop(space_id, None)
        self._moved[space_id] = to_cell

    def removed(self) -> Mapping[UUID, datetime]:
        return dict(self._removed)

    async def refresh_on_miss(self) -> bool:
        """The copy is the configuration itself: nothing to read again."""
        return True

    def space(self, space_id: UUID) -> SpaceConfig:
        try:
            return self._by_id[space_id]
        except KeyError:
            raise NotFound("unknown space") from None

    def spaces(self) -> Sequence[SpaceConfig]:
        return list(self._by_id.values())


class DictSecrets:
    def __init__(self, values: Mapping[str, str] | None = None) -> None:
        self.values = dict(values or {})

    async def get(self, ref: str) -> str:
        try:
            return self.values[ref]
        except KeyError:
            raise NotFound("unknown secret") from None

    async def put(self, ref: str, value: str) -> int:
        self.values[ref] = value
        return 1

    async def destroy_space(self, space_id: UUID) -> int:
        doomed = [ref for ref in self.values if ref.startswith(f"spaces/{space_id}/")]
        for ref in doomed:
            del self.values[ref]
        return len(doomed)


@dataclass
class RecordedDelivery:
    task: Task
    attempt: int = 1
    acked: bool = False
    retried: list[str] = field(default_factory=list)

    async def ack(self) -> None:
        self.acked = True

    async def retry_later(self, reason: str) -> None:
        self.retried.append(reason)


class InMemoryBus:
    def __init__(self) -> None:
        self.published: list[Task] = []

    async def publish(self, tasks: Sequence[Task]) -> None:
        self.published.extend(tasks)

    async def consume(self, queues: Sequence[str], prefetch: int) -> AsyncIterator[RecordedDelivery]:
        while self.published:
            task = self.published.pop(0)
            if task.queue in queues:
                yield RecordedDelivery(task)

    def take(self, name: str | None = None) -> list[Task]:
        taken = [t for t in self.published if name is None or t.name == name]
        self.published = [t for t in self.published if t not in taken]
        return taken


class InMemoryHotLayer:
    """Starts sealed, as a store the cell has used before; `wipe()` plays a restart without persistence.
    The marker's lease does not expire here: a test drops it with `wipe()` or `marker = None`."""

    def __init__(self) -> None:
        self.turns: dict[tuple[UUID, UUID], dict[UUID, HotTurn]] = defaultdict(dict)
        self.ttl_ms: dict[tuple[UUID, UUID], int] = {}
        self.fail = False
        self.marker: str | None = "whole"
        self.proofs: dict[str, dict[UUID, Verification]] = defaultdict(dict)
        self.proof_ttl_ms: dict[str, int] = {}

    def wipe(self) -> None:
        self.turns.clear()
        self.ttl_ms.clear()
        self.proofs.clear()
        self.marker = None

    async def add(self, space_id: UUID, handle_id: UUID, turns: Sequence[HotTurn], cap_ms: int) -> None:
        if self.fail:
            raise ConnectionError("hot layer unavailable")
        bucket = self.turns[(space_id, handle_id)]
        for turn in turns:
            bucket[turn.event_id] = turn
        self.ttl_ms[(space_id, handle_id)] = cap_ms

    async def read(
        self, space_id: UUID, handle_ids: Sequence[UUID], since_ms: int, *, newest: int | None = None
    ) -> HotRead:
        if self.fail:
            raise ConnectionError("hot layer unavailable")
        found = []
        by_handle = {}
        for h in handle_ids:
            own = sorted(
                (t for t in self.turns.get((space_id, h), {}).values() if t.occurred_at_ms >= since_ms),
                key=lambda t: (t.occurred_at_ms, str(t.event_id)),
            )
            by_handle[h] = own[-newest:] if newest is not None else own
            found += by_handle[h]
        ordered = sorted(
            {t.event_id: t for t in found}.values(), key=lambda t: (t.occurred_at_ms, str(t.event_id))
        )
        return HotRead(ordered, whole=self.marker == "whole", by_handle=by_handle)

    async def state(self) -> HotLayerState:
        if self.fail:
            raise ConnectionError("hot layer unavailable")
        if self.marker is None:
            return HotLayerState.LOST
        return HotLayerState.WHOLE if self.marker == "whole" else HotLayerState.REBUILDING

    async def claim_rebuild(self, token: str, lease_ms: int) -> bool:
        if self.marker is not None:
            return False
        self.marker = f"rebuilding:{token}"
        return True

    async def extend_rebuild(self, token: str, lease_ms: int) -> bool:
        return self.marker == f"rebuilding:{token}"

    async def seal(self, token: str) -> bool:
        if self.marker != f"rebuilding:{token}":
            return False
        self.marker = "whole"
        return True

    async def shorten(self, space_id: UUID, handle_id: UUID, covered_until_ms: int, keep_ms: int) -> None:
        bucket = self.turns.get((space_id, handle_id), {})
        if bucket and max(t.occurred_at_ms for t in bucket.values()) <= covered_until_ms:
            self.ttl_ms[(space_id, handle_id)] = keep_ms

    async def purge(self, space_id: UUID, handle_ids: Sequence[UUID]) -> None:
        for h in handle_ids:
            self.turns.pop((space_id, h), None)

    async def retract(self, space_id: UUID, handle_id: UUID, event_id: UUID, occurred_at_ms: int) -> None:
        if self.fail:
            raise ConnectionError("hot layer unavailable")
        bucket = self.turns.get((space_id, handle_id), {})
        if event_id in bucket and bucket[event_id].occurred_at_ms == occurred_at_ms:
            del bucket[event_id]

    async def hold_proofs(self, held: Sequence[tuple[str, UUID, Verification]], ttl_ms: int) -> None:
        if self.fail:
            raise ConnectionError("hot layer unavailable")
        for name, event_id, level in held:
            self.proofs[name][event_id] = level
            self.proof_ttl_ms[name] = ttl_ms

    async def held_proof(self, name: str) -> Verification | None:
        if self.fail:
            raise ConnectionError("hot layer unavailable")
        return max(self.proofs.get(name, {}).values(), key=lambda level: level.rank, default=None)

    async def release_proofs(self, held: Sequence[tuple[str, UUID]]) -> None:
        if self.fail:
            raise ConnectionError("hot layer unavailable")
        for name, event_id in held:
            fields = self.proofs.get(name, {})
            fields.pop(event_id, None)
            if not fields:
                self.proofs.pop(name, None)


class InMemoryCache:
    def __init__(self) -> None:
        self.values: dict[str, bytes] = {}
        self.counters: dict[str, int] = defaultdict(int)

    async def get(self, key: str) -> bytes | None:
        return self.values.get(key)

    async def get_many(self, keys: Sequence[str]) -> list[bytes | None]:
        return [self.values.get(k) for k in keys]

    async def set(self, key: str, value: bytes, ttl_s: int | None = None) -> None:
        self.values[key] = value

    async def delete(self, keys: Sequence[str]) -> None:
        for k in keys:
            self.values.pop(k, None)

    async def put_and_get_many(
        self, key: str, value: bytes | None, ttl_s: int, keys: Sequence[str]
    ) -> tuple[bool, list[bytes | None]]:
        stored = key in self.values if value is None else True
        if value is not None:
            self.values[key] = value
        return stored, [self.values.get(k) for k in keys]

    async def put_if_generation(
        self, pack_key: str, gen_key: str, value: bytes, generation: int, ttl_s: int
    ) -> bool:
        current = self.values.get(gen_key)
        if current is None or generation < int(current):
            return False
        self.values[pack_key] = value
        return True

    async def bump_generation(self, gen_key: str, generation: int, ttl_s: int) -> None:
        current = int(self.values.get(gen_key, b"0"))
        self.values[gen_key] = str(max(current, generation)).encode()

    async def incr_window(self, key: str, window_s: int) -> int:
        self.counters[key] += 1
        return self.counters[key]

    async def purge_profile(self, space_id: UUID, profile_id: UUID) -> int:
        doomed = [k for k in self.values if f":{space_id}:{{{profile_id}}}" in k]
        for k in doomed:
            del self.values[k]
        return len(doomed)

    async def purge_space(self, space_id: UUID) -> int:
        doomed = [k for k in self.values if f":{space_id}:" in k]
        for k in doomed:
            del self.values[k]
        return len(doomed)


class InMemoryObjectStore:
    def __init__(self, now: Callable[[], datetime] | None = None) -> None:
        self.objects: dict[str, bytes] = {}
        # When each object was last written, for a retention by age; `now` is the test's clock.
        self.written: dict[str, datetime] = {}
        self.now = now or (lambda: datetime.now(UTC))

    def _wrote(self, key: str) -> None:
        self.written[key] = self.now()

    async def put(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> None:
        self.objects[key] = data
        self._wrote(key)

    async def put_stream(
        self, key: str, chunks: AsyncIterator[bytes], content_type: str = "application/octet-stream"
    ) -> int:
        self.objects[key] = b"".join([chunk async for chunk in chunks])
        self._wrote(key)
        return len(self.objects[key])

    async def put_if_absent(
        self, key: str, data: bytes, content_type: str = "application/octet-stream"
    ) -> bool:
        if key in self.objects:
            return False
        self.objects[key] = data
        self._wrote(key)
        return True

    async def copy(self, source: str, target: str) -> None:
        self.objects[target] = await self.get(source)
        self._wrote(target)

    async def get(self, key: str) -> bytes:
        try:
            return self.objects[key]
        except KeyError:
            raise NotFound("object not found") from None

    async def get_stream(self, key: str) -> AsyncIterator[bytes]:
        data = await self.get(key)
        for i in range(0, len(data), 1 << 16):
            yield data[i : i + (1 << 16)]

    async def list_keys(self, prefix: str) -> list[str]:
        return sorted(k for k in self.objects if k.startswith(prefix))

    async def list_keys_before(self, prefix: str, before: datetime) -> list[str]:
        return sorted(
            k for k in self.objects if k.startswith(prefix) and self.written.get(k, before) < before
        )

    async def delete(self, key: str) -> None:
        self.objects.pop(key, None)
        self.written.pop(key, None)

    async def delete_prefix(self, prefix: str) -> int:
        doomed = [k for k in self.objects if k.startswith(prefix)]
        for k in doomed:
            del self.objects[k]
            self.written.pop(k, None)
        return len(doomed)

    async def presign_put(
        self,
        key: str,
        content_type: str,
        expires_s: int,
        *,
        size_bytes: int | None = None,
        sha256_hex: str | None = None,
    ) -> PresignedUpload:
        headers = {"Content-Type": content_type}
        if sha256_hex:
            headers["x-amz-checksum-sha256"] = sha256_hex
        return PresignedUpload(f"https://objects.test/{key}?expires={expires_s}", headers)

    async def presign_get(self, key: str, expires_s: int) -> str:
        return f"https://objects.test/{key}?download=1&expires={expires_s}"


@dataclass
class InMemoryAnchorStore:
    """Object Lock in compliance mode as tests see it: written once, never replaced or deleted."""

    locked: bool = True
    objects: dict[str, bytes] = field(default_factory=dict)
    retained_until: dict[str, datetime] = field(default_factory=dict)

    async def put_anchor(self, key: str, data: bytes, retain_until: datetime) -> None:
        if key in self.objects:
            raise Conflict("the object exists and is not replaced")
        self.objects[key] = data
        self.retained_until[key] = retain_until

    async def get_anchor(self, key: str) -> bytes:
        try:
            return self.objects[key]
        except KeyError:
            raise NotFound("object not found") from None


class ScriptedLlm:
    """Returns queued answers in order; records every prompt for assertions.

    An answer is JSON text, sent as it is, or a mapping written the way a test reads one session's
    extraction (`episode`, `facts` with `evidence`, `open_items`, `agent_questions`, ...), with turn ids as
    the session alone would number them (`e1` is its first turn). Such mappings are encoded into the
    extraction's compact answer (`domain/extract/prompt.py`), one queued mapping per session of the call's
    batch, with each session's turn ids moved to where the batch numbers them."""

    def __init__(self, *answers: Mapping[str, Any] | str) -> None:
        self.answers: list[Mapping[str, Any] | str] = list(answers)
        self.calls: list[dict[str, Any]] = []

    async def complete_json(self, **kwargs: Any) -> LlmResult:
        self.calls.append(kwargs)
        if not self.answers:
            raise RuntimeError("ScriptedLlm has no more answers")
        first = self.answers.pop(0)
        if isinstance(first, str):
            content = first
        elif "episode" not in first:
            content = json.dumps(first)
        else:
            blocks = _session_turns(str(kwargs.get("user", "")))
            given = [first]
            while len(given) < max(len(blocks), 1):
                if not self.answers or isinstance(self.answers[0], str):
                    raise RuntimeError("ScriptedLlm has fewer answers than the batch has sessions")
                given.append(self.answers.pop(0))  # type: ignore[arg-type]
            content = json.dumps(wire_answer(given, blocks))
        usage = LlmUsage(input_tokens=len(kwargs["user"]) // 4, output_tokens=len(content) // 4)
        return LlmResult(content=content, model="scripted", provider="test", usage=usage)


_SESSION_LINE = re.compile(r"^\[(s\d+)[|\]]")
_TURN_LINE = re.compile(r"^\[(e\d+)[|\]]")


def _session_turns(user: str) -> list[tuple[str, list[str]]]:
    """The batch's sessions in the prompt, each with its turn ids in order."""
    blocks: list[tuple[str, list[str]]] = []
    for line in user.splitlines():
        if m := _SESSION_LINE.match(line):
            blocks.append((m.group(1), []))
        elif (m := _TURN_LINE.match(line)) and blocks:
            blocks[-1][1].append(m.group(1))
    return blocks


def session_turns(user: str) -> list[tuple[str, list[str]]]:
    """The batch's sessions in an extraction prompt's user message, each with its turn ids in order."""
    return _session_turns(user)


def wire_answer(
    answers: Sequence[Mapping[str, Any]], blocks: Sequence[tuple[str, list[str]]]
) -> dict[str, Any]:
    """Readable per-session answers (`ScriptedLlm`) as the extraction's compact answer."""
    out: dict[str, list[Any]] = {
        k: []
        for k in (
            "matters",
            "sessions",
            "facts",
            "items",
            "resolve",
            "instructions",
            "hours",
            "slots",
            "asks",
            "claims",
            "values",
        )
    }
    for k, answer in enumerate(answers):
        alias, turns = blocks[k] if k < len(blocks) else (f"s{k + 1}", [])

        def turn(ref: Any, turns: list[str] = turns) -> Any:
            m = re.fullmatch(r"\[?e(\d+)\]?", str(ref)) if ref is not None else None
            if m is None or not turns:
                return ref
            i = int(m.group(1)) - 1
            return turns[i] if 0 <= i < len(turns) else f"e{10_000 + i}"

        def refs(*parts: Any) -> list[Any]:
            found: list[Any] = []
            for part in parts:
                for ref in part if isinstance(part, list) else [part]:
                    if ref is not None:
                        found.append(turn(ref))
            return found

        ep = answer["episode"]
        if ep.get("matter"):
            same = next((m for m in out["matters"] if m["key"] == ep["matter"]), None)
            if same is None:
                out["matters"].append(
                    {"key": ep["matter"], "label": ep.get("matter_label") or "", "sessions": []}
                )
                same = out["matters"][-1]
            same["sessions"].append(alias)
        out["sessions"].append(
            {
                "id": alias,
                "summary": ep.get("summary"),
                "intent": ep.get("intent"),
                "category": ep.get("category"),
                "outcome": ep.get("outcome"),
                "resolution": ep.get("resolution"),
                "sentiment": ep.get("sentiment"),
            }
        )
        for f in answer.get("facts", []):
            out["facts"].append(
                {
                    "op": f.get("op"),
                    "predicate": f.get("predicate"),
                    "value": f.get("value", ""),
                    "refs": refs(
                        f.get("evidence"),
                        f.get("target"),
                        f.get("contradicts"),
                        f.get("object"),
                        f.get("about"),
                    ),
                    "since": f.get("valid_at"),
                    "certainty": f.get("certainty", 1.0),
                }
            )
        for i in answer.get("open_items", []):
            out["items"].append(
                {
                    "text": i.get("description"),
                    "kind": i.get("kind", "promise"),
                    "owner": i.get("owner"),
                    "due": i.get("due_at"),
                    "operation": i.get("expected_operation"),
                    "refs": refs(i.get("evidence"), i.get("object"), i.get("about")),
                }
            )
        out["resolve"] += list(answer.get("resolve", []))
        for h in answer.get("hours", []):
            out["hours"].append(
                {
                    "channel": h.get("channel", "any"),
                    "not_before": h.get("not_before"),
                    "not_after": h.get("not_after"),
                    "days": list(h.get("days") or []),
                    "lift": bool(h.get("lift")),
                    "refs": refs(h.get("evidence")),
                }
            )
        for i in answer.get("instructions", []):
            out["instructions"].append(
                {
                    "text": i.get("text"),
                    "strength": i.get("strength"),
                    "attr": i.get("attr"),
                    "only_with": i.get("only_with"),
                    "contact": bool(i.get("contact")),
                    "refs": refs(i.get("evidence"), i.get("object")),
                }
            )
        for slot in answer.get("profile", []):
            out["slots"].append(
                {"slot": slot.get("slot"), "value": slot.get("value"), "refs": refs(slot.get("evidence"))}
            )
        for q in answer.get("agent_questions", []):
            out["asks"].append(
                {"predicate": q.get("predicate"), "refs": refs(q.get("event"), q.get("object"))}
            )
        for c in answer.get("agent_claims", []):
            out["claims"].append(
                {
                    "predicate": c.get("predicate"),
                    "value": c.get("value"),
                    "refs": refs(c.get("event"), c.get("object"), c.get("contradicts")),
                }
            )
        out["values"] += list(answer.get("values", []))
    return out


_PII_PATTERNS = {
    "email": re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+"),
    "phone": re.compile(r"\+?\d[\d\s().-]{8,}\d"),
    "document": re.compile(r"\b\d{3}\.?\d{3}\.?\d{3}-?\d{2}\b"),
}


class DeterministicModels:
    """Hash embeddings and regex PII: enough to exercise every code path."""

    dim = 64

    async def embed(self, texts: Sequence[str]) -> tuple[list[list[float]], str]:
        return [self._embed(t) for t in texts], "hash-64"

    async def embed_query(self, text: str) -> tuple[list[float], str]:
        return self._embed(text), "hash-64"

    async def pii(self, texts: Sequence[str]) -> list[list[PiiSpan]]:
        return [
            sorted(
                (
                    PiiSpan(m.start(), m.end(), kind)
                    for kind, rx in _PII_PATTERNS.items()
                    for m in rx.finditer(t)
                ),
                key=lambda s: s.start,
            )
            for t in texts
        ]

    def _embed(self, text: str) -> list[float]:
        vec = [0.0] * self.dim
        for token in re.findall(r"\w+", text.lower()):
            h = int.from_bytes(hashlib.blake2b(token.encode(), digest_size=4).digest(), "big")
            vec[h % self.dim] += 1.0
        norm = math.sqrt(sum(v * v for v in vec)) or 1.0
        return [v / norm for v in vec]


_INJECTION_MARKERS = ("ignore previous", "ignore as instruções", "system prompt", "you are now")
_QUESTION_RULES = (
    ("verifies", re.compile(r"segurança|security|código|code|nascimento|birth", re.IGNORECASE)),
    ("confirms", re.compile(r"confirm|correto|certo\?|right\?|correct\?", re.IGNORECASE)),
    ("asks_data", re.compile(r"\?")),
)
_SENSITIVE_LABELS = {
    "health": "health",
    "religion": "religion",
    "philosophical_belief": "religion",
    "political_opinion": "politics",
    "union_membership": "union",
    "sexual_orientation": "sexual_orientation",
    "sexual_life": "sexual_orientation",
    "racial_or_ethnic_origin": "ethnicity",
    "genetic": "genetic_biometric",
    "biometric": "genetic_biometric",
    "criminal_record": "criminal_record",
}


class DeterministicDecider:
    """Stands in for the decision model in tests: keyword answers per task, in the adapter's shape (a
    yes-or-no task answers its non-conservative label with that label's probability). `answers` fixes a task's
    answer, and None there plays a text the model did not answer. Every call is recorded."""

    def __init__(
        self, answers: Mapping[str, Label | None] | None = None, triage: Triage | None = None
    ) -> None:
        self.answers = dict(answers or {})
        self.fixed_triage = triage
        self.calls: list[tuple[str, list[str]]] = []

    async def triage(
        self, space_id: UUID, transcripts: Sequence[str], categories: Sequence[str]
    ) -> Triage | None:
        """A short transcript was answered on the spot; the markers of `decide` flag injections and the
        product's word lists special categories; the first category offered."""
        self.calls.append(("triage", list(transcripts)))
        if self.fixed_triage is not None:
            return self.fixed_triage
        lowered = [t.lower() for t in transcripts]
        return Triage(
            answered=tuple(0.9 if len(t) <= 20 else 0.1 for t in transcripts),
            categories=tuple(categories[0] if categories else None for _ in transcripts),
            injection=0.95 if any(m in t for t in lowered for m in _INJECTION_MARKERS) else 0.05,
            sensitive=0.95 if any(sensitive_topics(t) for t in transcripts) else 0.05,
            model_version="decider-test",
        )

    async def decide(self, space_id: UUID, task: str, texts: Sequence[str]) -> list[Label | None]:
        self.calls.append((task, list(texts)))
        if task in self.answers:
            return [self.answers[task]] * len(texts)
        return [self._one(task, t) for t in texts]

    @staticmethod
    def _one(task: str, text: str) -> Label:
        lowered = text.lower()
        if task == "injection":
            return Label(
                "safe", 0.05 if any(m in lowered for m in _INJECTION_MARKERS) else 0.97, "decider-test"
            )
        if task == "contradiction":
            return Label("compatible", 0.05, "decider-test")
        if task == "sensitive_category":
            topics = sensitive_topics(text)
            return Label(_SENSITIVE_LABELS[topics[0]] if topics else "none", 0.95, "decider-test")
        if task == "question_type":
            label = next((name for name, rx in _QUESTION_RULES if rx.search(text)), "ambiguous")
            return Label(label, 0.9, "decider-test")
        if task == "action_fulfils_item":
            return Label("fulfils", 0.05, "decider-test")  # a test that needs a "yes" sets `answers`
        if task == "item_restates_item":
            return Label("restates", 0.05, "decider-test")  # a test that needs a "yes" sets `answers`
        if task == "turn_delivers_item":
            return Label("delivers", 0.05, "decider-test")  # a test that needs a "yes" sets `answers`
        if task == "turn_asks_item":
            return Label("asks", 0.05, "decider-test")  # a test that needs a "yes" sets `answers`
        raise ValueError(f"no decision for {task}")


@dataclass
class RecordingEgress:
    status: int = 200
    sent: list[tuple[str, bytes, dict[str, str]]] = field(default_factory=list)

    async def post(self, url: str, body: bytes, headers: Mapping[str, str], timeout_s: float) -> HttpResponse:
        self.sent.append((url, body, dict(headers)))
        return HttpResponse(status=self.status)


class ServingSpaces:
    """`SpaceDatabases` where every space is served, unless a test says otherwise."""

    def __init__(self) -> None:
        self.statuses: dict[UUID, DbStatus] = {}
        self.disposed: list[UUID] = []

    async def require_serving(self, space_id: UUID) -> None:
        status = self.statuses.get(space_id, DbStatus.READY)
        if status is not DbStatus.READY:
            raise Unavailable(f"the space's database is {status.value}", retry_after_s=RETRY_AFTER_S)

    async def status(self, space_id: UUID) -> DbStatus | None:
        return self.statuses.get(space_id, DbStatus.READY)

    def active_spaces(self) -> list[UUID]:
        return sorted(space for space, status in self.statuses.items() if status is DbStatus.READY)

    def serving_spaces(self) -> list[UUID]:
        return self.active_spaces()

    async def refresh(self) -> None:
        return None

    async def dispose(self, space_id: UUID) -> None:
        self.disposed.append(space_id)
