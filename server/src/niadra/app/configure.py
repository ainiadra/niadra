"""The configuration assistant (22.4): masked samples in, a versioned configuration diff out.

It reads production samples, so it runs inside the cell: samples are masked before the prompt,
the proposal is checked against the raw samples here, and only the configuration document goes
to the control plane, where a person approves it.
"""

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ValidationError

from niadra.app.metering import record_usage_in
from niadra.config.models import (
    ExtractionSchema,
    Policy,
    SpaceConfig,
    TraitRuleConfig,
    TriggerRuleConfig,
)
from niadra.config.models import Mapping as MappingDoc
from niadra.contracts.common import Page
from niadra.contracts.configure import AssistRequest, AssistRunOut
from niadra.domain.common.errors import InvalidInput, NotFound, RateLimited
from niadra.domain.common.ids import uuid7
from niadra.domain.common.space import Principal, SourcePrincipal
from niadra.domain.configure.mapping_check import RuleExpressions, check_mapping
from niadra.domain.configure.masking import (
    Masked,
    contains_removed,
    looks_personal,
    mask_example,
    mask_payloads,
    mask_text,
    mask_texts,
)
from niadra.domain.configure.model import (
    MAPPING_SAMPLES,
    MONTHLY_RUNS,
    TRANSCRIPT_SAMPLES,
    AssistRun,
    AssistStatus,
    AssistTask,
)
from niadra.domain.configure.policy_check import CategoryAccess, loosened
from niadra.domain.metering import Metric
from niadra.domain.records import EventRecord
from niadra.domain.tasks import Task, TaskName
from niadra.domain.vocabulary import Sensitivity, Verification
from niadra.ports.infra import Clock, ConfigProvider, LlmClient, ObjectStore, Reasoning
from niadra.ports.proposals import ConfigProposal, ConfigProposals
from niadra.ports.uow import UnitOfWork, UnitOfWorkFactory

PROMPT_SAMPLES = 20
REASONING: Reasoning = "low"
"""The extraction model's effort for a configuration proposal, measured on 27/09: at
`none` and `low` every proposal of the four tasks passed the checks, but `none` renamed the source's event
types and turned one-off sample values into predicates; `medium` cost 1.8 times `low` and wrote an invalid
expression."""
_ANSWER_SCHEMA = {
    "type": "object",
    "properties": {"document": {"type": "object"}, "kind": {"type": "string", "enum": ["trigger", "trait"]}},
    "required": ["document"],
}
_SENSITIVITY_RANK = {Sensitivity.NORMAL: 0, Sensitivity.RESTRICTED: 1, Sensitivity.SENSITIVE: 2}
_SYSTEM = (
    "You propose configuration for Niadra, a customer memory, for a person to review and approve. "
    'Answer with JSON only, shaped as {{"document": <the document>}}{kind}. Placeholders such as '
    "<string:token>, <number> or <name> stand for masked customer data: never invent or copy values. "
    "The document must validate against this JSON Schema: {schema}"
)


@dataclass
class _Samples:
    ids: tuple[str, ...] = ()
    raw: list[dict[str, Any]] = field(default_factory=list)
    masked: Masked = field(default_factory=lambda: Masked([]))


@dataclass
class _Sources:
    raw_refs: list[tuple[UUID, str]] = field(default_factory=list)
    messages: list[EventRecord] = field(default_factory=list)


@dataclass
class _Checked:
    config_type: str
    piece: Any
    document: Any
    checks: dict[str, Any] = field(default_factory=dict)
    problems: list[str] = field(default_factory=list)


class AssistService:
    def __init__(
        self,
        uow: UnitOfWorkFactory,
        llm: LlmClient,
        clock: Clock,
        config: ConfigProvider,
        control_proposals: ConfigProposals,
        *,
        objects: ObjectStore | None = None,
    ) -> None:
        self._uow = uow
        self._llm = llm
        self._clock = clock
        self._config = config
        self._proposals = control_proposals
        # Raw webhook bodies stay in object storage (5.2); without it the mapping task has no samples.
        self._objects = objects

    async def request(self, principal: Principal, body: AssistRequest) -> AssistRunOut:
        space_id = principal.space.space_id
        if body.source_id is not None and self._config.space(space_id).source(body.source_id) is None:
            raise NotFound("source not found")
        now = self._clock.now()
        month = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        requested_by = principal.source_id if isinstance(principal, SourcePrincipal) else principal.user_id
        request = {
            "source_id": str(body.source_id) if body.source_id else None,
            "description": mask_text(body.description) if body.description else None,
            # Masked here, before it is stored or read by anything.
            "example": mask_example(body.example_payload).samples[0] if body.example_payload else None,
        }
        run = AssistRun(
            uuid7(), body.task, AssistStatus.QUEUED, requested_by, now, proposal={"request": request}
        )
        async with self._uow(space_id) as uow:
            if await uow.configure.count_since(month) >= MONTHLY_RUNS:
                raise RateLimited(_seconds_to_next_month(now), "the monthly assistant runs are used up")
            await uow.configure.add_run(run)
            await uow.pipeline.enqueue([Task(TaskName.ASSIST, space_id, {"run_id": str(run.run_id)})])
        return _out(run)

    async def get(self, principal: Principal, run_id: UUID) -> AssistRunOut:
        async with self._uow(principal.space.space_id) as uow:
            run = await uow.configure.get_run(run_id)
        if run is None:
            raise NotFound("assistant run not found")
        return _out(run)

    async def runs(self, principal: Principal, *, cursor: str | None, limit: int) -> Page[AssistRunOut]:
        """The space's runs, newest first; `cursor` is the last `run_id` of the previous page."""
        before = _run_cursor(cursor)
        async with self._uow(principal.space.space_id) as uow:
            runs = await uow.configure.list_runs(before_id=before, limit=limit + 1)
        page = runs[:limit]
        more = len(runs) > limit
        return Page[AssistRunOut](
            items=[_out(r) for r in page], next_cursor=str(page[-1].run_id) if more else None
        )

    async def assist(self, task: Task) -> None:
        run_id = UUID(task.payload["run_id"])
        config = self._config.space(task.space_id)
        async with self._uow(task.space_id) as uow:
            run = await uow.configure.get_run(run_id)
            if run is None or run.status not in (AssistStatus.QUEUED, AssistStatus.PROPOSED):
                return
            sources = await _sample_sources(uow, run) if run.status is AssistStatus.QUEUED else _Sources()
        samples = await self._samples(sources, run.proposal["request"].get("example"))
        if run.status is AssistStatus.QUEUED:
            # The LLM call runs outside any transaction: it may take seconds (L3).
            run = await self._propose(run, config, samples)
            async with self._uow(task.space_id) as uow:
                await uow.configure.save_run(run)
                if usage := run.proposal.get("usage"):
                    # What the assistant's call cost, counted once per run (5.15).
                    tokens = int(usage.get("input_tokens", 0)) + int(usage.get("output_tokens", 0))
                    await record_usage_in(
                        uow,
                        task.space_id,
                        Metric.ASSISTANT_TOKENS,
                        tokens,
                        ("assist", run.run_id),
                        int(usage.get("cost_micros", 0)),
                        self._clock.now(),
                    )
        if run.status is AssistStatus.PROPOSED:
            await self._deliver(task.space_id, run)

    async def _samples(self, sources: _Sources, example: dict[str, Any] | None = None) -> _Samples:
        if not sources.raw_refs and not sources.messages and example:
            # A source that never sent an event: the person's example, already masked, is the sample.
            return _Samples(("example",), [example], Masked([example]))
        if sources.raw_refs:
            raw = await self._raw_bodies(sources.raw_refs)
            return _Samples(
                tuple(str(i) for i, _ in raw), [p for _, p in raw], mask_payloads([p for _, p in raw])
            )
        messages = [m for m in sources.messages if m.text]
        if messages:
            lines = [f"{m.speaker.value}: {m.text}" for m in messages]
            return _Samples(tuple(str(m.event_id) for m in messages), [], mask_texts(lines))
        return _Samples()

    async def _raw_bodies(self, refs: Sequence[tuple[UUID, str]]) -> list[tuple[UUID, dict[str, Any]]]:
        """Read outside the transaction. A body erased since, or one that is not a JSON object (the
        webhook stores it before parsing), is skipped rather than failing the run."""
        if self._objects is None:
            return []
        bodies = []
        for event_id, key in refs:
            try:
                body = json.loads(await self._objects.get(key))
            except (NotFound, ValueError):
                continue
            if isinstance(body, dict):
                bodies.append((event_id, body))
        return bodies

    async def _deliver(self, space_id: UUID, run: AssistRun) -> None:
        """Only the document leaves the cell; `Unavailable` from the port retries the task."""
        proposal = ConfigProposal(
            space_id=space_id,
            run_id=run.run_id,
            config_type=run.proposal["config_type"],
            document=run.proposal["document"],
            reason=f"configuration assistant run {run.run_id} ({run.task.value})",
        )
        try:
            diff_id = await self._proposals.submit(proposal)
            delivered = replace(
                run, status=AssistStatus.DELIVERED, proposal={**run.proposal, "diff_id": diff_id}
            )
        except InvalidInput:
            delivered = replace(
                run,
                status=AssistStatus.FAILED,
                proposal={**run.proposal, "problems": ["refused_by_control_plane"]},
            )
        async with self._uow(space_id) as uow:
            await uow.configure.save_run(delivered)

    async def _propose(self, run: AssistRun, config: SpaceConfig, samples: _Samples) -> AssistRun:
        request = run.proposal["request"]
        source_id = UUID(request["source_id"]) if request.get("source_id") else None
        schema, current = _target(run.task, config, source_id)
        kind = ', plus "kind": "trigger" or "trait"' if run.task is AssistTask.TRIGGER_OR_TRAIT_RULE else ""
        user = {
            "task": run.task.value,
            "source": _source_brief(config, source_id),
            "current": current,
            "description": request.get("description"),
            "samples": samples.masked.samples[:PROMPT_SAMPLES],
        }
        result = await self._llm.complete_json(
            system=_SYSTEM.format(kind=kind, schema=json.dumps(schema, separators=(",", ":"))),
            user=json.dumps(user, ensure_ascii=False, default=str),
            json_schema=_ANSWER_SCHEMA,
            reasoning=REASONING,
            max_output_tokens=4000,
            space_id=config.space_id,
            purpose="assistant",
        )
        usage = {
            "input_tokens": result.usage.input_tokens,
            "output_tokens": result.usage.output_tokens,
            "cost_micros": result.usage.cost_micros,
            "model": result.model,
        }
        proposal = {**run.proposal, "usage": usage}
        try:
            answer = json.loads(result.content)
            document = answer["document"]
        except (ValueError, KeyError, TypeError):
            return replace(
                run, status=AssistStatus.FAILED, proposal={**proposal, "problems": ["invalid_answer"]}
            )
        checked = _check(run, answer, document, config, source_id, samples)
        piece = json.dumps(checked.piece, sort_keys=True, ensure_ascii=False)
        if contains_removed(piece, samples.masked.removed) or looks_personal(piece):
            checked.problems.append("diff_contains_personal_data")
        status = AssistStatus.REJECTED if checked.problems else AssistStatus.PROPOSED
        canonical = json.dumps(checked.document, sort_keys=True, separators=(",", ":")).encode()
        return replace(
            run,
            status=status,
            sample_ids=samples.ids,
            diff_hash=hashlib.sha256(canonical).hexdigest(),
            proposal={
                **proposal,
                "config_type": checked.config_type,
                "document": checked.document,
                "checks": checked.checks,
                "problems": checked.problems,
            },
        )


async def _sample_sources(uow: UnitOfWork, run: AssistRun) -> "_Sources":
    source = run.proposal["request"].get("source_id")
    if run.task is AssistTask.WEBHOOK_MAPPING and source:
        return _Sources(raw_refs=await uow.ingest.raw_refs(UUID(source), limit=MAPPING_SAMPLES))
    if run.task is AssistTask.EXTRACTION_SCHEMA and source:
        return _Sources(messages=await uow.ingest.recent_messages(UUID(source), limit=TRANSCRIPT_SAMPLES))
    return _Sources()


def _check(
    run: AssistRun,
    answer: Mapping[str, Any],
    document: Any,
    config: SpaceConfig,
    source_id: UUID | None,
    samples: _Samples,
) -> _Checked:
    try:
        match run.task:
            case AssistTask.WEBHOOK_MAPPING:
                assert source_id is not None
                return _check_mapping(run, document, config, source_id, samples)
            case AssistTask.EXTRACTION_SCHEMA:
                schema = ExtractionSchema.model_validate(document)
                known = {p.name for p in config.extraction.predicates}
                new = sorted(p.name for p in schema.predicates if p.name not in known)
                return _Checked("schemas", _dump(schema), _dump(schema), {"new_predicates": new})
            case AssistTask.SOURCE_POLICY:
                policy = Policy.model_validate(document)
                problems = loosened(
                    _access(config.policy), _access(policy), _denies(config.policy), _denies(policy)
                )
                return _Checked("policies", _dump(policy), _dump(policy), problems=problems)
            case AssistTask.TRIGGER_OR_TRAIT_RULE:
                return _check_rule(answer.get("kind"), document, config)
    except (ValidationError, TypeError) as exc:
        detail = _fields(exc) if isinstance(exc, ValidationError) else "document"
        return _Checked("", None, None, problems=["invalid_document:" + detail])
    raise AssertionError(run.task)


def _check_mapping(
    run: AssistRun, document: Any, config: SpaceConfig, source_id: UUID, samples: _Samples
) -> _Checked:
    doc = {**document, "source_id": str(source_id)}
    doc.setdefault("version", f"assist-{run.run_id.hex[:12]}")
    mapping = MappingDoc.model_validate(doc)
    rules = [
        RuleExpressions(
            match=r.match,
            required=(r.idempotency_key, r.occurred_at),
            optional=tuple(
                e
                for e in (
                    r.conversation_id,
                    r.text,
                    r.canonical_type_expr,
                    *r.fields.values(),
                    *(h.value for h in r.handles),
                    *(o.id for o in r.object_refs),
                )
                if e
            ),
        )
        for r in mapping.rules
    ]
    report = check_mapping(rules, samples.raw)
    problems = ["invalid_expression"] if report.invalid_expressions else []
    if samples.raw and report.mapped == 0:
        problems.append("maps_no_sample")
    others = [_dump(m) for m in config.mappings if m.source_id != source_id]
    return _Checked("mappings", _dump(mapping), [*others, _dump(mapping)], report.to_json(), problems)


def _check_rule(kind: object, document: Any, config: SpaceConfig) -> _Checked:
    rule: TriggerRuleConfig | TraitRuleConfig
    current: Sequence[TriggerRuleConfig | TraitRuleConfig]
    if kind == "trigger":
        rule, current = TriggerRuleConfig.model_validate(document), config.trigger_rules
    else:
        rule, current = TraitRuleConfig.model_validate(document), config.trait_rules
    rules = [_dump(r) for r in current if r.rule_id != rule.rule_id]
    # The dry run belongs to the triggers and traits modules; the Console runs it on the proposal.
    checks = {"kind": "trigger" if kind == "trigger" else "trait", "dry_run": "not_run"}
    return _Checked(
        "trigger-rules" if kind == "trigger" else "trait-rules", _dump(rule), [*rules, _dump(rule)], checks
    )


def _target(task: AssistTask, config: SpaceConfig, source_id: UUID | None) -> tuple[dict[str, Any], Any]:
    match task:
        case AssistTask.WEBHOOK_MAPPING:
            mapping = config.mapping_for(source_id) if source_id else None
            return MappingDoc.model_json_schema(), _dump(mapping) if mapping else None
        case AssistTask.EXTRACTION_SCHEMA:
            return ExtractionSchema.model_json_schema(), _dump(config.extraction)
        case AssistTask.SOURCE_POLICY:
            return Policy.model_json_schema(), _dump(config.policy)
        case AssistTask.TRIGGER_OR_TRAIT_RULE:
            schema = {
                "trigger": TriggerRuleConfig.model_json_schema(),
                "trait": TraitRuleConfig.model_json_schema(),
            }
            current = {
                "trigger_rules": [_dump(r) for r in config.trigger_rules],
                "trait_rules": [_dump(r) for r in config.trait_rules],
            }
            return schema, current


def _source_brief(config: SpaceConfig, source_id: UUID | None) -> dict[str, Any] | None:
    source = config.source(source_id) if source_id else None
    if source is None:
        return None
    return {
        "name": source.name,
        "vendor": source.vendor,
        "channel": source.channel,
        "purposes": sorted(source.purposes),
        "audience": source.audience.value,
    }


def _access(policy: Policy) -> dict[str, CategoryAccess]:
    return {
        c.name: CategoryAccess(
            Verification(c.min_verification).rank, _SENSITIVITY_RANK[c.sensitivity], c.purposes
        )
        for c in policy.categories
    }


def _denies(policy: Policy) -> frozenset[str]:
    return frozenset(json.dumps(_dump(r.when), sort_keys=True) for r in policy.rules if r.effect == "deny")


def _dump(model: BaseModel) -> Any:
    return model.model_dump(mode="json")


def _fields(exc: ValidationError) -> str:
    # Field paths only: echoing values could leak what the LLM copied.
    return ",".join(".".join(str(p) for p in e["loc"]) for e in exc.errors())[:500]


def _out(run: AssistRun) -> AssistRunOut:
    p = run.proposal
    return AssistRunOut(
        run_id=run.run_id,
        task=run.task,
        status=run.status,
        created_at=run.created_at,
        config_type=p.get("config_type"),
        document=p.get("document"),
        diff_hash=run.diff_hash,
        checks=p.get("checks") or {},
        problems=p.get("problems") or [],
        diff_id=p.get("diff_id"),
    )


def _run_cursor(cursor: str | None) -> UUID | None:
    if not cursor:
        return None
    try:
        return UUID(cursor)
    except ValueError:
        raise InvalidInput("malformed cursor") from None


def _seconds_to_next_month(now: datetime) -> int:
    year, month = (now.year + 1, 1) if now.month == 12 else (now.year, now.month + 1)
    start = now.replace(year=year, month=month, day=1, hour=0, minute=0, second=0, microsecond=0)
    return max(1, int((start - now).total_seconds()))
