"""The decision models (2.7, 5.6): one port, every answer on the record.

The tasks in `MODEL_TASKS` are decided by the decision model on masked texts: nothing reaches it without the
PII spans, which the models pod answers. On a timeout, an error or an answer below the task's threshold the
decision is the task's conservative label, so an outage costs money (extract anyway) or recall (treat as
suspicious), never privacy (L10). A caller that must not act on a
default (`answered`) can wait for the model instead. A spent AI budget is not an outage: `BudgetExceeded`
reaches the caller.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable, Mapping, Sequence
from functools import partial
from uuid import UUID

from niadra.domain.common.errors import ModelsRefused
from niadra.domain.common.ids import uuid7
from niadra.domain.decide import (
    DECISION_MODEL,
    FALLBACK_MODEL,
    INJECTION_SCREEN,
    MODEL_TASKS,
    MOST_WINDOWS,
    RULES,
    SENSITIVE_SCREEN,
    WINDOWED,
    DecisionRecord,
    DecisionTask,
    Triage,
    input_hash,
    pair_windows,
    resolve,
)
from niadra.domain.extract.errors import BudgetExceeded
from niadra.domain.extract.redaction import Redactor
from niadra.domain.models_input import units
from niadra.ports.infra import Clock, DecisionModel, Label, ModelsClient, PiiSpan
from niadra.ports.uow import UnitOfWorkFactory

log = logging.getLogger(__name__)

# An unanswered PII call must redact everything, so the whole text becomes one span.
WHOLE_TEXT = "unknown"


PER_TEXT_S = 0.02
"""Added to the models call's deadline per text the pod reads (a long text is read in windows,
`domain.models_input`): a batch of an extraction's texts waits behind the pod's embedding batches, and a
flat second failed them while a burst of work kept the pod busy."""


class ModelsPendingError(Exception):
    """The models pod did not find the PII spans in time, and the caller asked to wait for them rather than
    mask every text whole (`pii_spans(..., wait=True)`). A pod that refused the texts raises `ModelsRefused`
    instead: waiting would not change its answer."""


def answered(label: Label) -> bool:
    """Whether a model gave the label, rather than the port's conservative default for a model that did not
    answer."""
    return label.model_version != FALLBACK_MODEL


class DecideService:
    def __init__(
        self,
        uow: UnitOfWorkFactory,
        models: ModelsClient,
        clock: Clock,
        *,
        decider: DecisionModel | None = None,
        timeout_s: float = 1.0,
        thresholds: Mapping[DecisionTask, float] | None = None,
    ) -> None:
        self._uow = uow
        self._models = models
        self._clock = clock
        self._decider = decider
        self._timeout_s = timeout_s
        self._thresholds = dict(thresholds or {})

    async def decide(
        self, space_id: UUID, task: DecisionTask, text: str, subject_ref: str | None = None
    ) -> Label:
        return (await self.decide_many(space_id, task, [text], [subject_ref]))[0]

    async def decide_many(
        self,
        space_id: UUID,
        task: DecisionTask,
        texts: Sequence[str],
        subject_refs: Sequence[str | None] | None = None,
    ) -> list[Label]:
        if task is DecisionTask.PII:
            spans = await self.pii_spans(space_id, texts, subject_refs)
            return [Label("sensitive" if s else "clean", 1.0, "pii") for s in spans]
        refs = list(subject_refs) if subject_refs is not None else [None] * len(texts)
        answers = await self._windowed(space_id, task, texts)
        threshold = self._threshold(task)
        labels: list[Label] = []
        records: list[DecisionRecord] = []
        for text, ref, (raw, model) in zip(texts, refs, answers, strict=True):
            final = resolve(task, raw.label, raw.probability, threshold)
            labels.append(Label(final, raw.probability, raw.model_version))
            records.append(self._record(task, text, model, raw, final, threshold, ref))
        await self._save(space_id, records)
        return labels

    async def answered_before(
        self, space_id: UUID, task: DecisionTask, subject_refs: Sequence[str]
    ) -> set[str]:
        """The subjects the decision model already answered for `task`: a pair it judged is not asked
        again."""
        if not subject_refs:
            return set()
        async with self._uow(space_id) as uow:
            return await uow.decide.answered(task, DECISION_MODEL, subject_refs)

    async def _windowed(
        self, space_id: UUID, task: DecisionTask, texts: Sequence[str]
    ) -> list[tuple[Label, str]]:
        """One answer per text. A pair whose texts pass a window (`domain.decide.pair_windows`) is asked
        about every window, and the windows' answers become one (`_combined`): a long action result, reply
        or item is read whole, never cut."""
        if task not in WINDOWED:
            return await self._answers(space_id, task, texts)
        fallback = (Label(RULES[task].conservative, 0.0, FALLBACK_MODEL), FALLBACK_MODEL)
        windows = [pair_windows(text) for text in texts]
        asked = [w for w in windows if len(w) <= MOST_WINDOWS]
        if len(asked) < len(windows):
            log.warning("a pair past %d windows was not asked: it keeps the conservative label", MOST_WINDOWS)
        flat = await self._answers(space_id, task, [part for w in asked for part in w])
        out: list[tuple[Label, str]] = []
        at = 0
        for w in windows:
            if len(w) > MOST_WINDOWS:
                out.append(fallback)
                continue
            out.append(_combined(task, flat[at : at + len(w)]))
            at += len(w)
        return out

    async def _answers(
        self, space_id: UUID, task: DecisionTask, texts: Sequence[str]
    ) -> list[tuple[Label, str]]:
        if not texts:
            return []
        if task not in MODEL_TASKS:
            raise ValueError(f"{task} is not decided one text at a time")
        return await self._ask_decider(space_id, task, texts)

    async def triage(
        self,
        space_id: UUID,
        transcripts: Sequence[str],
        categories: Sequence[str],
        subject_refs: Sequence[str],
    ) -> Triage:
        """The decision model's one answer about a batch of closed sessions, on masked transcripts; the
        conservative triage (every session to the extraction model, every check) when it cannot be asked.
        On the record: each session's answer as `worth_extracting` ("skip" is kept as its own turns), and the
        batch's two screens as `injection` and `sensitive_category`, on the batch's last session."""
        found = await self._triage(space_id, transcripts, categories)
        triage = found if found is not None else Triage.conservative(len(transcripts))
        model = DECISION_MODEL if found is not None else FALLBACK_MODEL
        whole = "\n\n".join(transcripts)
        last = subject_refs[-1] if subject_refs else None
        records = [
            self._record(
                DecisionTask.WORTH_EXTRACTING,
                text,
                model,
                Label("skip", p, triage.model_version),
                "skip" if triage.light(i) else "extract",
                RULES[DecisionTask.WORTH_EXTRACTING].threshold,
                ref,
            )
            for i, (text, p, ref) in enumerate(zip(transcripts, triage.answered, subject_refs, strict=True))
        ]
        records.append(
            self._record(
                DecisionTask.INJECTION,
                whole,
                model,
                Label("injection", triage.injection, triage.model_version),
                "injection" if triage.injection >= INJECTION_SCREEN else "safe",
                INJECTION_SCREEN,
                last,
            )
        )
        records.append(
            self._record(
                DecisionTask.SENSITIVE_CATEGORY,
                whole,
                model,
                Label("unclassified", triage.sensitive, triage.model_version),
                "unclassified" if triage.sensitive >= SENSITIVE_SCREEN else "none",
                SENSITIVE_SCREEN,
                last,
            )
        )
        await self._save(space_id, records)
        return triage

    async def _triage(
        self, space_id: UUID, transcripts: Sequence[str], categories: Sequence[str]
    ) -> Triage | None:
        if self._decider is None or not transcripts:
            return None
        spans = await self._guarded(partial(self._models.pii, transcripts), transcripts)
        if spans is None:
            return None  # nothing leaves unmasked (5.14)
        redactor = Redactor()
        masked = [redactor.redact(text, found) for text, found in zip(transcripts, spans, strict=True)]
        try:
            return await self._decider.triage(space_id, masked, categories)
        except BudgetExceeded:
            raise
        except Exception as exc:
            log.warning("decision model triage failed, using the conservative triage: %s", type(exc).__name__)
            return None

    async def _ask_decider(
        self, space_id: UUID, task: DecisionTask, texts: Sequence[str]
    ) -> list[tuple[Label, str]]:
        """The decision model's answers on the masked texts; the conservative label for a text it did not
        answer. Nothing leaves unmasked: without the PII spans nothing is asked (5.14)."""
        fallback = (Label(RULES[task].conservative, 0.0, FALLBACK_MODEL), FALLBACK_MODEL)
        if self._decider is None:
            return [fallback] * len(texts)
        spans = await self._guarded(partial(self._models.pii, texts), texts)
        if spans is None:
            return [fallback] * len(texts)
        redactor = Redactor()
        masked = [redactor.redact(text, found) for text, found in zip(texts, spans, strict=True)]
        try:
            # Each text is its own request under the adapter's timeout; a text it could not answer is None.
            answer = await self._decider.decide(space_id, task.value, masked)
        except BudgetExceeded:
            raise  # the space's AI budget is spent: the caller waits for it, or takes its own default
        except Exception as exc:
            log.warning("decision model failed, using the conservative default: %s", type(exc).__name__)
            return [fallback] * len(texts)
        if len(answer) != len(texts):
            log.warning("decision model returned %d answers for %d inputs", len(answer), len(texts))
            return [fallback] * len(texts)
        return [(label, DECISION_MODEL) if label is not None else fallback for label in answer]

    async def pii_spans(
        self,
        space_id: UUID,
        texts: Sequence[str],
        subject_refs: Sequence[str | None] | None = None,
        *,
        wait: bool = False,
    ) -> list[list[PiiSpan]]:
        """The PII spans of each text, from the models pod. When it does not answer, every text is masked
        whole (nothing leaves unmasked, 5.14); with `wait`, `ModelsPendingError` instead, or `ModelsRefused`
        when the pod refused the texts, and nothing is recorded: an extraction retries later, or settles,
        rather than send the extraction model only masks."""
        refs = list(subject_refs) if subject_refs is not None else [None] * len(texts)
        found = await self._guarded(partial(self._models.pii, texts), texts, refused=wait)
        if found is None and wait:
            raise ModelsPendingError
        if found is None:
            spans = [[PiiSpan(0, len(t), WHOLE_TEXT)] if t else [] for t in texts]
            model, version = FALLBACK_MODEL, FALLBACK_MODEL
        else:
            spans, model, version = found, "pii", "pii"
        threshold = self._threshold(DecisionTask.PII)
        records = [
            self._record(
                DecisionTask.PII,
                text,
                model,
                Label("sensitive" if s else "clean", 1.0, version),
                "sensitive" if s else "clean",
                threshold,
                ref,
            )
            for text, s, ref in zip(texts, spans, refs, strict=True)
        ]
        await self._save(space_id, records)
        return spans

    async def _guarded[T](
        self, call: Callable[[], Awaitable[list[T]]], texts: Sequence[str], *, refused: bool = False
    ) -> list[T] | None:
        """The pod's answer about `texts`, or None for the conservative default. A refusal of the input is
        final, so it is logged as an error; with `refused` it reaches the caller."""
        expected = len(texts)
        try:
            async with asyncio.timeout(self._timeout_s + PER_TEXT_S * units(texts)):
                answer = await call()
        except ModelsRefused as exc:
            log.error("models refused the input, using the conservative default: %s", exc.detail)
            if refused:
                raise
            return None
        except Exception as exc:
            log.warning("models call failed, using the conservative default: %s", type(exc).__name__)
            return None
        if len(answer) != expected:
            log.warning("models returned %d answers for %d inputs", len(answer), expected)
            return None
        return answer

    def _threshold(self, task: DecisionTask) -> float:
        return self._thresholds.get(task, RULES[task].threshold)

    def _record(
        self,
        task: DecisionTask,
        text: str,
        model: str,
        raw: Label,
        final: str,
        threshold: float,
        ref: str | None,
    ) -> DecisionRecord:
        return DecisionRecord(
            decision_id=uuid7(),
            task=task,
            input_hash=input_hash(text),
            model=model,
            model_version=raw.model_version,
            label=final,
            probability=raw.probability,
            threshold=threshold,
            created_at=self._clock.now(),
            subject_ref=ref,
        )

    async def _save(self, space_id: UUID, records: list[DecisionRecord]) -> None:
        async with self._uow(space_id) as uow:
            await uow.decide.record(records)


def _combined(task: DecisionTask, answers: Sequence[tuple[Label, str]]) -> tuple[Label, str]:
    """The windows' answers about one pair as one answer. A window that gives the task's windowed label
    (`domain.decide.WINDOWED`: an action that says the thing was done, a conflicting statement) decides it,
    at its highest probability. Otherwise a window the model did not answer leaves the pair unanswered, since
    the unread window may hold it. Otherwise the other label at its lowest probability: it must hold in every
    window."""
    if len(answers) == 1:
        return answers[0]
    found = [a for a in answers if a[1] != FALLBACK_MODEL and a[0].label == WINDOWED[task]]
    if found:
        return max(found, key=lambda a: a[0].probability)
    missing = [a for a in answers if a[1] == FALLBACK_MODEL]
    if missing:
        return missing[0]
    return min(answers, key=lambda a: a[0].probability)
