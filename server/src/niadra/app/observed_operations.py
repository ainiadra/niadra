"""The operations a space's agents record, offered to the extraction with no declared vocabulary
(`domain.extract.observed`).

`current` reads, in one statement, how often each operation was recorded in the last 30 days, merges the
names into their canonical (safe) form and keeps the most used. The answer is cached for
`NAMES_TTL_S`, so a burst of extractions reads the table once. A name with at least three recorded result
texts gets a meaning: the texts are masked by the models pod, the generation model writes one sentence, and
the meaning is cached for a week under the digest of the examples it was written from. Writing meanings is
bounded per call and never fails the extraction: a spent budget, a pod that does not answer or a model
error leave the name without a meaning, and the next batch tries again.

This is the read side of the observed layer for operations. The observed configuration (WP1,
`observe_config`) records the same names and meanings as an observed `schemas` diff through `snapshot`, so
the Console shows them as inferred.
"""

import json
import logging
from dataclasses import dataclass
from datetime import timedelta
from uuid import UUID

from niadra.app.decide import DecideService
from niadra.domain.extract.errors import BudgetExceeded
from niadra.domain.extract.observed import (
    MAX_EXAMPLES,
    MEANING_EXAMPLES,
    MEANING_SCHEMA,
    MEANING_SYSTEM,
    MEANING_TTL,
    OBSERVED_WINDOW,
    ObservedOperation,
    ObservedOperations,
    clean_meaning,
    examples_digest,
    meaning_request,
    ranked,
)
from niadra.domain.extract.redaction import Redactor
from niadra.domain.objects.operations import MAX_OPERATIONS, canonical_operation
from niadra.ports.infra import Clock, KeyValueCache, LlmClient
from niadra.ports.uow import UnitOfWorkFactory

log = logging.getLogger(__name__)

NAMES_TTL_S = 600
MEANINGS_PER_CALL = 3
"""New meanings written per extraction batch at most: the first batches of a new space pay a few short
calls, never one per operation at once."""
PURPOSE = "extraction"


@dataclass(frozen=True, slots=True)
class _Counted:
    name: str
    uses: int
    written: tuple[str, ...]
    """The names as the actions recorded them, which the stored rows are found by."""


class ObservedOperationsService:
    def __init__(
        self,
        uow: UnitOfWorkFactory,
        cache: KeyValueCache,
        llm: LlmClient | None,
        decide: DecideService | None,
        clock: Clock,
    ) -> None:
        self._uow = uow
        self._cache = cache
        self._llm = llm
        self._decide = decide
        self._clock = clock

    async def current(self, space_id: UUID, *, describe: bool = True) -> ObservedOperations:
        """The observed names, most used first, with the meanings already written; with `describe`, up to
        `MEANINGS_PER_CALL` missing meanings are written first."""
        counted = await self._counted(space_id)
        if not counted:
            return ObservedOperations()
        meanings = await self._meanings(space_id, counted, describe=describe)
        return ObservedOperations(
            tuple(ObservedOperation(c.name, c.uses, meanings.get(c.name)) for c in counted)
        )

    async def snapshot(self, space_id: UUID) -> ObservedOperations:
        """What the observed configuration records: the same as `current`, without writing meanings."""
        return await self.current(space_id, describe=False)

    async def _counted(self, space_id: UUID) -> tuple[_Counted, ...]:
        key = f"opsobs:{space_id}:names"
        cached = await self._cache.get(key)
        if cached is not None:
            rows = json.loads(cached)
            return tuple(_Counted(r[0], int(r[1]), tuple(r[2])) for r in rows)
        since = self._clock.now() - OBSERVED_WINDOW
        async with self._uow(space_id) as uow:
            raw = await uow.objects.operation_counts(since=since, limit=MAX_OPERATIONS * 2)
        uses: dict[str, int] = {}
        written: dict[str, list[str]] = {}
        for name, n in raw:
            canonical = canonical_operation(name)
            if canonical is None:
                continue
            uses[canonical] = uses.get(canonical, 0) + n
            written.setdefault(canonical, []).append(name)
        counted = tuple(
            _Counted(name, n, tuple(sorted(written[name]))) for name, n in ranked(list(uses.items()))
        )
        await self._cache.set(
            key, json.dumps([[c.name, c.uses, list(c.written)] for c in counted]).encode(), ttl_s=NAMES_TTL_S
        )
        return counted

    async def _meanings(
        self, space_id: UUID, counted: tuple[_Counted, ...], *, describe: bool
    ) -> dict[str, str]:
        keys = [f"opsmean:{space_id}:{c.name}" for c in counted]
        stored = await self._cache.get_many(keys)
        meanings: dict[str, str] = {}
        for c, raw in zip(counted, stored, strict=True):
            if raw is not None:
                meanings[c.name] = json.loads(raw)["meaning"]
        if not describe or self._llm is None or self._decide is None:
            return meanings
        written = 0
        for c in counted:
            if c.name in meanings or written >= MEANINGS_PER_CALL:
                continue
            meaning = await self._describe(space_id, c)
            if meaning is not None:
                meanings[c.name] = meaning
                written += 1
        return meanings

    async def _describe(self, space_id: UUID, counted: _Counted) -> str | None:
        assert self._llm is not None
        assert self._decide is not None
        since = self._clock.now() - OBSERVED_WINDOW
        async with self._uow(space_id) as uow:
            texts = await uow.objects.operation_results(counted.written, since=since, limit=MAX_EXAMPLES)
        if len(texts) < MEANING_EXAMPLES:
            return None
        try:
            spans = await self._decide.pii_spans(space_id, texts)
            redactor = Redactor()
            masked = [redactor.redact(t, s) for t, s in zip(texts, spans, strict=True)]
            result = await self._llm.complete_json(
                system=MEANING_SYSTEM,
                user=meaning_request(counted.name, masked),
                json_schema=MEANING_SCHEMA,
                reasoning="minimal",
                max_output_tokens=120,
                space_id=space_id,
                purpose=PURPOSE,
            )
            answer = json.loads(result.content)
        except BudgetExceeded:
            return None
        except Exception:
            log.warning("operation meaning not written", extra={"space_id": str(space_id)}, exc_info=True)
            return None
        meaning = clean_meaning(answer.get("meaning") if isinstance(answer, dict) else None)
        if meaning is None:
            return None
        ttl = int(MEANING_TTL / timedelta(seconds=1))
        value = {"meaning": meaning, "examples": examples_digest(masked)}
        await self._cache.set(f"opsmean:{space_id}:{counted.name}", json.dumps(value).encode(), ttl_s=ttl)
        return meaning
