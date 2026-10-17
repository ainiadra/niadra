"""Outcomes and their attribution (24.7).

An object of a type the `measurement` document names in `outcomes` reaches an outcome when its lifecycle
state is one its type calls provisional or final. The objects module calls `consume` inside the transaction
that wrote the objects, which only hands them to the `attribute_outcomes` task (one outbox row). The task
reads the objects, what their subjects were shown and engaged with (`SignalsService.seen`), and the actions
agents recorded on them, and writes every link of each object at once: attributing the same object again
rewrites its links. A line or a whole object is provisional until the object reaches a final state or the
exchange window of the signed definition passes. The objects module's clock moves an object past its
type's deadline to `expired_without_outcome` and calls `consume` again: it is attributed as such, worth
nothing. What an outcome says of the subject's size (bought, kept, returned for not fitting) goes to their
signals.

The `outcomes` loop makes provisional links final at the end of their window and drops links past their
retention (13 months, 24.5). The reports read the daily totals, without a person, and the reconciliation
compares each line's value with the company's BI file.
"""

import csv
import hashlib
import io
import logging
import re
import tempfile
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any
from uuid import UUID

from niadra.app.signals import SignalsService
from niadra.app.space_loops import Pass
from niadra.app.typed_state import Attribution, lifecycle_stage
from niadra.config.measurement import OutcomeDefinition
from niadra.config.models import SpaceConfig
from niadra.config.object_types import ObjectType
from niadra.contracts.signals import (
    MAX_BI_BYTES,
    AttributionReport,
    AttributionRow,
    AttributionTotal,
    BiUpload,
    BiUploadRequest,
    IdentityCalibration,
    InterleavingReport,
    InterleavingRow,
    InterleavingTotal,
    OutcomeLink,
    OutcomePage,
    ReconciledLine,
    ReconcileRequest,
    ReconcileResult,
)
from niadra.domain.common.errors import InvalidInput, NotFound
from niadra.domain.common.ids import uuid7
from niadra.domain.common.space import Principal
from niadra.domain.measure.attribution import (
    EXPIRED,
    FINAL,
    PROVISIONAL,
    Acted,
    BiLine,
    Line,
    Link,
    Outcome,
    Rules,
    attribute,
    carries_token,
    finality_of,
    minor_units,
    reconcile,
    totals,
)
from niadra.domain.measure.revenue import WHOLE_ORDER, Revenue
from niadra.domain.notify.cursor import decode_cursor, encode_cursor
from niadra.domain.pipeline.delay import DelayReason
from niadra.domain.records import BusinessObject, ObjectKey
from niadra.domain.signals.daily import sign_test
from niadra.domain.signals.derive import Entry
from niadra.domain.signals.storage import bi_key
from niadra.domain.state.expr import duration_ms
from niadra.domain.tasks import Task, TaskName
from niadra.domain.vocabulary import Feature
from niadra.ports.infra import Clock, ConfigProvider, ObjectStore
from niadra.ports.repos.outcomes import StoredLink
from niadra.ports.uow import UnitOfWork, UnitOfWorkFactory

log = logging.getLogger(__name__)

LIFECYCLE = "state"
"""The reported field that holds an object's lifecycle state (`app.typed_state`)."""
OUTCOMES_EVERY_S = 30.0
OUTCOMES_IDLE_CAP_S = 60.0
"""An exchange window's end lands within a minute of its time; the pass wakes sooner when one is near."""
FINALIZE_LIMIT = 1000
RETENTION = timedelta(days=395)
"""Attribution by line is kept 13 months; after that only the daily totals, without a person (24.5)."""
RETENTION_EVERY = timedelta(hours=1)
BI_URL_TTL_S = 900
BI_IN_MEMORY = 4 * 1024 * 1024
SIZE_RETURNS = frozenset({"size_small", "size_large"})
CALIBRATION_DAYS = 90
CALIBRATION_MIN_LINES = 20
"""Below this many lines that a token and the identity method both attribute, identity links carry no
confidence."""
CALIBRATION_EVERY = timedelta(hours=1)
WAIT_FOR_MEASUREMENT = timedelta(minutes=2)
"""How long an object that carries an exposure token waits for a configuration that measures its type: a
definition turned on reaches every process within seconds; past this, the space does not measure it."""
RETRY_EVERY = timedelta(seconds=15)
REVISED_LOOKBACK = timedelta(minutes=10)
REVISED_DELAY = timedelta(seconds=15)
REVISED_MAX = 1000
"""A revised definition reaches every process within seconds of its approval, and a process that still held
the old one may attribute an outcome in that window: once the outcomes loop sees the new definition, the
objects attributed in the last minutes are attributed again, a little later, when every worker holds it."""
NOT_YET = ""
_PURCHASE = re.compile(r"\S{1,512}")
"""The finality of an object that has not reached an outcome."""


@dataclass(frozen=True, slots=True)
class _Measured:
    definition: OutcomeDefinition
    declared: ObjectType | None


class OutcomesService:
    def __init__(
        self,
        uow: UnitOfWorkFactory,
        signals: SignalsService,
        clock: Clock,
        config: ConfigProvider,
        objects: ObjectStore,
    ) -> None:
        self._uow = uow
        self._signals = signals
        self._clock = clock
        self._config = config
        self._objects = objects
        self._swept: dict[UUID, datetime] = {}
        self._calibrated: dict[UUID, tuple[datetime, float | None]] = {}
        self._definitions: dict[UUID, str] = {}

    # What comes in.

    async def consume(self, uow: UnitOfWork, space_id: UUID, objects: Sequence[BusinessObject]) -> None:
        """`OutcomeConsumer`: inside the transaction that wrote the objects, the ones an outcome definition
        names go to their task, one outbox row for all of them. An object that carries an exposure token goes
        too, whatever this process's configuration says: it may not know yet that the space measures it."""
        measured = self._measured(space_id)
        keys = [
            [o.key.type, o.key.namespace, o.key.external_id]
            for o in objects
            if o.origin_handle_id is not None and (o.key.type in measured or carries_token(o.state))
        ]
        if keys:
            since = self._clock.now().isoformat()
            await uow.pipeline.enqueue(
                [Task(TaskName.ATTRIBUTE_OUTCOMES, space_id, {"objects": keys, "since": since})]
            )

    async def attribute(self, task: Task) -> None:
        """The `attribute_outcomes` task: every link of each object, rewritten whole. An object this process's
        configuration does not measure yet waits for it, a task a little later, up to two minutes; past that
        the space does not measure it, and the warning says so."""
        space_id = task.space_id
        measured = self._measured(space_id)
        now = self._clock.now()
        wanted = list(task.payload["objects"])
        if measured and task.payload.get("written_since"):
            # The objects attributed since a revised definition was approved, under the definition now held.
            async with self._uow(space_id) as uow:
                since = datetime.fromisoformat(task.payload["written_since"])
                recent = await uow.outcomes.written_since(since, limit=REVISED_MAX)
            wanted += [ref.split(":", 2) for ref in recent if ref.split(":", 1)[0] in measured]
        waiting = [k for k in wanted if k[0] not in measured]
        if waiting:
            await self._wait_for_measurement(task, waiting, now)
        if not measured or len(waiting) == len(wanted):
            return
        config = self._config.space(space_id)
        keys = [ObjectKey(*k) for k in wanted if k[0] in measured]
        async with self._uow(space_id) as uow:
            confidence = await self._identity_confidence(uow, space_id, now)
            found = [o for o in await uow.objects.get_objects(keys) if o.origin_handle_id is not None]
            handles: dict[UUID, tuple[UUID, ...]] = {}
            acted: dict[UUID, tuple[Acted, ...]] = {}
            for obj in found:
                assert obj.origin_handle_id is not None
                if obj.origin_handle_id not in handles:
                    profile = await uow.identity.profile_of(obj.origin_handle_id)
                    handles[obj.origin_handle_id] = (
                        tuple(profile.handle_ids) if profile else (obj.origin_handle_id,)
                    )
                acted[obj.object_id] = await self._acted(uow, config, obj)
        outcomes: list[tuple[Outcome, _Measured]] = []
        for obj in found:
            known = measured[obj.key.type]
            outcome = outcome_of(obj, known.definition, known.declared, config, acted[obj.object_id], now)
            if outcome.finality != NOT_YET:
                outcomes.append((outcome, known))
        links: list[Link] = []
        traits: list[tuple[UUID, Entry]] = []
        seen = {h: await self._signals.seen(space_id, handles[h]) for h in {o.handle_id for o, _ in outcomes}}
        for outcome, known in outcomes:
            shown, touches = seen[outcome.handle_id]
            links += attribute(outcome, shown, touches, rules_of(config, known.definition, confidence))
            traits += size_traits(outcome, known.definition)
        async with self._uow(space_id) as uow:
            days = await uow.outcomes.write([o.object_id for o in found], links, now)
            await uow.outcomes.roll_up(days)
            await self._signals.derived(uow, space_id, traits)

    async def _wait_for_measurement(self, task: Task, waiting: list[list[str]], now: datetime) -> None:
        since = datetime.fromisoformat(task.payload.get("since") or now.isoformat())
        if now - since < WAIT_FOR_MEASUREMENT:
            later = Task(
                TaskName.ATTRIBUTE_OUTCOMES, task.space_id, {"objects": waiting, "since": since.isoformat()}
            )
            async with self._uow(task.space_id) as uow:
                await uow.pipeline.schedule(later, now + RETRY_EVERY, DelayReason.DELAY)
            return
        log.warning(
            "outcomes not attributed: the space does not measure their type",
            extra={
                "space_id": str(task.space_id),
                "objects": len(waiting),
                "types": sorted({k[0] for k in waiting}),
            },
        )

    async def _identity_confidence(self, uow: UnitOfWork, space_id: UUID, now: datetime) -> float | None:
        """The identity method's agreement with the lines' own tokens over 90 days, read at most once an hour
        per space (6.8)."""
        kept = self._calibrated.get(space_id)
        if kept is not None and now - kept[0] < CALIBRATION_EVERY:
            return kept[1]
        if "identity" not in self._config.space(space_id).measurement.attribution.methods:
            rate = None
        else:
            today = now.date()
            agreeing, compared = await uow.outcomes.agreement(today - timedelta(days=CALIBRATION_DAYS), today)
            rate = round(agreeing / compared, 4) if compared >= CALIBRATION_MIN_LINES else None
        self._calibrated[space_id] = (now, rate)
        return rate

    async def _acted(self, uow: UnitOfWork, config: SpaceConfig, obj: BusinessObject) -> tuple[Acted, ...]:
        names = {s.source_id: s.name for s in config.sources}
        return tuple(
            Acted(a.action_id, a.occurred_at, names.get(a.source_id))
            for a in await uow.objects.actions_of_object(obj.object_id)
        )

    # The loop.

    async def step(self, space_id: UUID) -> Pass:
        """The `outcomes` loop: provisional links past their exchange window become final, and links past
        their retention leave; a revised definition has the objects attributed in the last minutes attributed
        again. Nothing, and no statement, where nothing is measured."""
        revised = self._revised(space_id)
        if not self._measured(space_id):
            return Pass(0)
        now = self._clock.now()
        async with self._uow(space_id) as uow:
            if revised:
                payload = {
                    "objects": [],
                    "written_since": (now - REVISED_LOOKBACK).isoformat(),
                    "since": now.isoformat(),
                }
                again = Task(TaskName.ATTRIBUTE_OUTCOMES, space_id, payload)
                await uow.pipeline.schedule(again, now + REVISED_DELAY, DelayReason.DELAY)
            days = await uow.outcomes.finalize(now, limit=FINALIZE_LIMIT)
            if now - self._swept.get(space_id, datetime.min.replace(tzinfo=now.tzinfo)) >= RETENTION_EVERY:
                days = sorted({*days, *await uow.outcomes.expire(now - RETENTION, limit=FINALIZE_LIMIT)})
                self._swept[space_id] = now
            await uow.outcomes.roll_up(days)
            ahead = await uow.outcomes.next_due()
        due_in = max(0.0, (ahead - now).total_seconds()) if ahead is not None else None
        return Pass(len(days), due_in)

    # Reports.

    async def outcomes(self, principal: Principal, cursor: str | None, limit: int) -> OutcomePage:
        space_id = principal.space.space_id
        before = decode_cursor(cursor)
        async with self._uow(space_id) as uow:
            found = await uow.outcomes.links(before=before, limit=limit + 1)
        page = found[:limit]
        more = len(found) > limit
        return OutcomePage(
            items=[link_out(s) for s in page],
            next_cursor=encode_cursor(page[-1].known_at, page[-1].link.link_id) if more else None,
        )

    async def attribution(self, principal: Principal, since: date, until: date) -> AttributionReport:
        _period(since, until)
        async with self._uow(principal.space.space_id) as uow:
            rows = await uow.outcomes.daily(since, until)
            agreeing, compared = await uow.outcomes.agreement(since, until)
        return AttributionReport(
            since=since,
            until=until,
            rows=[AttributionRow.model_validate(r) for r in rows],
            totals=[AttributionTotal.model_validate(t, from_attributes=True) for t in totals(rows)],
            identity=IdentityCalibration(
                agreeing=agreeing, compared=compared, rate=round(agreeing / compared, 4) if compared else None
            ),
        )

    async def interleaving(self, principal: Principal, since: date, until: date) -> InterleavingReport:
        """Which of a tool's two rankings the person's engagement credited, by day and over the period, with
        the sign test on the impressions it decided."""
        _period(since, until)
        async with self._uow(principal.space.space_id) as uow:
            rows = await uow.outcomes.interleaved(since, until)
        summed: dict[tuple[str, str], list[int]] = {}
        for row in rows:
            acc = summed.setdefault((row["experiment"], row["tool"]), [0, 0, 0, 0])
            for i, name in enumerate(("impressions", "wins_a", "wins_b", "ties")):
                acc[i] += int(row[name])
        return InterleavingReport(
            since=since,
            until=until,
            rows=[InterleavingRow.model_validate(r) for r in rows],
            totals=[
                InterleavingTotal(
                    experiment=experiment, tool=tool, impressions=acc[0], wins_a=acc[1], wins_b=acc[2],
                    ties=acc[3], p_value=sign_test(acc[1], acc[2]),
                )
                for (experiment, tool), acc in sorted(summed.items())
            ],
        )  # fmt: skip

    async def bi_upload(self, principal: Principal, request: BiUploadRequest) -> BiUpload:
        """Where the company uploads its BI file, which the storage takes only with this size and digest."""
        upload_id = uuid7()
        key = bi_key(principal.space.space_id, upload_id)
        upload = await self._objects.presign_put(
            key, "text/csv", BI_URL_TTL_S, size_bytes=request.size_bytes, sha256_hex=request.sha256
        )
        expires_at = self._clock.now() + timedelta(seconds=BI_URL_TTL_S)
        return BiUpload(
            upload_id=upload_id,
            upload_url=upload.url,
            upload_headers=dict(upload.headers),
            expires_at=expires_at,
        )

    async def reconcile(self, principal: Principal, request: ReconcileRequest) -> ReconcileResult:
        """`POST /v1/measure/reconcile`: the lines both sides know, compared value by value; the report is
        kept with the tolerance the `measurement` document sets (6.8). The uploaded file is read once and
        deleted."""
        _period(request.since, request.until)
        space_id = principal.space.space_id
        config = self._config.space(space_id)
        key = bi_key(space_id, request.file.upload_id)
        bi = await self._bi_file(key)
        async with self._uow(space_id) as uow:
            values = await uow.outcomes.line_values(request.since, request.until)
        ours: dict[tuple[str, str], int] = {}
        for order, line, value in values:
            ours[(_external_id(order), line)] = ours.get((_external_id(order), line), 0) + value
        tolerance = config.measurement.attribution.reconcile_tolerance
        revenue = config.measurement.revenue
        whole = revenue is not None and revenue.split_orders == WHOLE_ORDER
        found = reconcile(ours, bi, tolerance, whole_orders=whole)
        body: dict[str, Any] = {
            "deviation": found.deviation, "tolerance": tolerance, "within": found.within,
            "matched": found.matched, "only_ours": found.only_ours, "only_bi": found.only_bi,
            "ours_minor": found.ours_minor, "bi_minor": found.bi_minor, "currency": found.currency,
            "worst": [list(w) for w in found.worst],
        }  # fmt: skip
        async with self._uow(space_id) as uow:
            report_id = await uow.outcomes.add_report(
                "reconcile", request.since, request.until, body, self._clock.now()
            )
        await self._objects.delete(key)
        return ReconcileResult(
            report_id=str(report_id),
            deviation=found.deviation,
            tolerance=tolerance,
            within_tolerance=found.within,
            matched=found.matched,
            only_ours=found.only_ours,
            only_bi=found.only_bi,
            ours_minor=found.ours_minor,
            bi_minor=found.bi_minor,
            currency=found.currency,
            worst=[
                ReconciledLine(order=o, line=line, ours_minor=a, bi_minor=b) for o, line, a, b in found.worst
            ],
        )

    async def _bi_file(self, key: str) -> list[BiLine]:
        """The uploaded file, spooled to disk past a few megabytes rather than held whole in memory."""
        with tempfile.SpooledTemporaryFile(max_size=BI_IN_MEMORY) as spool:
            try:
                async for chunk in self._objects.get_stream(key):
                    spool.write(chunk)
                    if spool.tell() > MAX_BI_BYTES:
                        raise InvalidInput("the BI file is larger than 50 MB")
            except NotFound:
                raise InvalidInput("no BI file was uploaded under that `upload_id`") from None
            spool.seek(0)
            return bi_lines(io.TextIOWrapper(spool, encoding="utf-8-sig", newline=""))

    def _revised(self, space_id: UUID) -> bool:
        """Whether the space's outcome definitions, revenue or attribution changed since this process last
        looked; the first look is no change."""
        try:
            measurement = self._config.space(space_id).measurement
        except NotFound:
            return False
        try:
            inferred = "|".join(
                d.model_dump_json() for d in outcome_definitions(self._config.space(space_id))
            )
        except NotFound:
            inferred = ""
        digest = hashlib.sha256(
            (measurement.model_dump_json(include={"revenue", "outcomes", "attribution"}) + inferred).encode()
        ).hexdigest()
        before = self._definitions.get(space_id)
        self._definitions[space_id] = digest
        return before is not None and before != digest

    def _measured(self, space_id: UUID) -> dict[str, _Measured]:
        """The object types an outcome definition names, while the space measures; empty otherwise."""
        try:
            config = self._config.space(space_id)
        except NotFound:
            return {}
        if Feature.MEASUREMENT not in config.features.enabled:
            return {}
        return {
            d.object_type: _Measured(d, config.object_types.get(d.object_type))
            for d in outcome_definitions(config)
        }


def outcome_definitions(config: SpaceConfig) -> tuple[OutcomeDefinition, ...]:
    """What the space measures as outcomes: the definitions it declared, then one for each
    object type whose lifecycle names final states and that no declaration covers. Such a type reaches its
    outcome in any of its final states, the outcome named after the type: with no declaration, Niadra does
    not guess which final state is a success, so "the filing reached `protocolada`" and "the deadline
    reached `cumprido`" are both the type's outcome reached, with the state on the row. A declaration
    splits success from failure and adds revenue."""
    declared = tuple(config.measurement.outcomes)
    covered = {d.object_type for d in declared}
    inferred: list[OutcomeDefinition] = []
    for spec in config.object_types.types:
        outcome = spec.lifecycle.outcome if spec.lifecycle else None
        if spec.type in covered or outcome is None or not outcome.final:
            continue
        name = _outcome_name(spec.type)
        if name is None:
            continue
        inferred.append(
            OutcomeDefinition(name=name, object_type=spec.type, success_states=list(outcome.final)[:10])
        )
    return declared + tuple(inferred)


_OUTCOME_NAME = re.compile(r"^[a-z][a-z0-9_]{0,39}$")


def _outcome_name(object_type: str) -> str | None:
    """The type's name as an outcome's (`measurement.Name`): lowercase, `_` for anything else, at most 40
    characters; None when nothing usable is left."""
    name = re.sub(r"[^a-z0-9_]+", "_", object_type.lower()).strip("_")[:40]
    return name if _OUTCOME_NAME.fullmatch(name) else None


def outcome_of(
    obj: BusinessObject,
    definition: OutcomeDefinition,
    declared: ObjectType | None,
    config: SpaceConfig,
    acted: tuple[Acted, ...],
    now: datetime,
) -> Outcome:
    """The object as an outcome: the lifecycle state the objects module keeps (each system's codes
    translated, the clock's transitions and its expiry included), the finality its type gives that state
    (`NOT_YET` while it is not an outcome), its lines and the marks agents left. Money counts only under the
    signed revenue definition."""
    assert obj.origin_handle_id is not None
    names = {source.source_id: source.name for source in config.sources}
    stage = lifecycle_stage(obj.fields, declared, Attribution(declared, names)) if declared else None
    state = stage.state if stage else _text(obj.state.get(LIFECYCLE)) or ""
    reached = stage.at if stage else obj.as_of
    lifecycle = declared.lifecycle.outcome if declared and declared.lifecycle else None
    if state == EXPIRED:
        finality = EXPIRED
    elif lifecycle is not None:
        finality = finality_of(state, lifecycle.provisional, lifecycle.final) or NOT_YET
    else:
        finality = FINAL if state in definition.success_states else NOT_YET
    window = timedelta(milliseconds=duration_ms(definition.final_after)) if definition.final_after else None
    if finality == PROVISIONAL and window is not None and reached + window <= now:
        finality = FINAL
    currency = config.measurement.revenue.currency if config.measurement.revenue else None

    def money(state: Mapping[str, Any], name: str | None) -> int | None:
        return minor_units(state.get(name), currency) if currency and name else None

    lines: list[Line] = []
    spec = definition.lines
    entries = obj.state.get(spec.field) if spec else None
    if spec is not None and isinstance(entries, list):
        for index, entry in enumerate(e for e in entries if isinstance(e, dict)):
            lines.append(
                Line(
                    line_ref=str(entry.get(spec.line_id) or index + 1),
                    item=_text(entry.get(spec.item)) if spec.item else None,
                    value_minor=money(entry, spec.value),
                    state=_text(entry.get(spec.state)) if spec.state else None,
                    stamp=_text(entry.get(spec.stamp)),
                    size=_text(entry.get(spec.size)) if spec.size else None,
                    return_reason=_text(entry.get(spec.return_reason)) if spec.return_reason else None,
                    discount_minor=money(entry, spec.discount) or 0,
                    tax_minor=money(entry, spec.tax) or 0,
                    replaces=_text(entry.get(spec.replaces)) if spec.replaces else None,
                )
            )
    purchase = _text(obj.state.get(definition.purchase_field)) if definition.purchase_field else None
    if purchase is not None and not _PURCHASE.fullmatch(purchase):
        purchase = None
    return Outcome(
        outcome=definition.name,
        object_id=obj.object_id,
        object_ref=f"{obj.key.type}:{obj.key.namespace}:{obj.key.external_id}",
        handle_id=obj.origin_handle_id,
        state=state,
        finality=finality,
        reached_at=reached,
        success=state in definition.success_states,
        lines=tuple(lines),
        value_minor=money(obj.state, definition.revenue_field),
        currency=currency,
        stamp=_text(obj.state.get(definition.stamp_field)) if definition.stamp_field else None,
        handoff_id=_uuid(obj.state.get(definition.handoff_field)) if definition.handoff_field else None,
        actions=acted,
        discount_minor=money(obj.state, definition.discount_field) or 0,
        coupon_minor=money(obj.state, definition.coupon_field) or 0,
        shipping_minor=money(obj.state, definition.shipping_field) or 0,
        tax_minor=money(obj.state, definition.tax_field) or 0,
        purchase=f"{obj.key.type}:{obj.key.namespace}:{purchase}" if purchase else None,
    )


def rules_of(
    config: SpaceConfig, definition: OutcomeDefinition, identity_confidence: float | None = None
) -> Rules:
    settings = config.measurement.attribution
    return Rules(
        methods=frozenset(settings.methods),
        click_window=timedelta(days=settings.click_window_days),
        view_window=timedelta(days=settings.view_window_days),
        handoff_window=timedelta(days=settings.handoff_window_days),
        final_after=timedelta(milliseconds=duration_ms(definition.final_after))
        if definition.final_after
        else None,
        failed_line_states=frozenset(definition.lines.failed_states) if definition.lines else frozenset(),
        identity_confidence=identity_confidence,
        revenue=Revenue(**config.measurement.revenue.model_dump(exclude={"currency"}))
        if config.measurement.revenue
        else Revenue(),
    )


def size_traits(outcome: Outcome, definition: OutcomeDefinition) -> list[tuple[UUID, Entry]]:
    """A size bought (`acquired`), kept past the window (`kept`), or returned for not fitting
    (`returned_for_size`), as an attribute of the subject; the key makes the same line's fact once."""
    spec = definition.lines
    if spec is None or spec.size_attribute is None or outcome.finality == EXPIRED:
        return []
    out = []
    for line in outcome.lines:
        if not line.size:
            continue
        if line.return_reason in SIZE_RETURNS:
            source = "returned_for_size"
        elif outcome.success and line.state not in spec.failed_states:
            source = "kept" if outcome.finality == FINAL else "acquired"
        else:
            continue
        interaction = {"kind": "attribute", "name": spec.size_attribute, "value": line.size, "source": source}
        key = f"o:{outcome.object_id}:{line.line_ref}:{source}"
        out.append((outcome.handle_id, Entry(key, outcome.reached_at, interaction)))
    return out


def link_out(stored: StoredLink) -> OutcomeLink:
    x = stored.link
    return OutcomeLink(
        link_id=str(x.link_id),
        outcome=x.outcome,
        object=x.object_ref,
        line=x.line_ref,
        state=x.state,
        method=x.method,
        band=x.band,
        finality=x.finality,
        value_minor=x.value_minor,
        currency=x.currency,
        purchase=x.order_ref,
        exposure_id=x.exposure_id,
        position=x.position,
        turn_id=x.turn_id,
        action_id=str(x.action_id) if x.action_id else None,
        handoff_id=str(x.handoff_id) if x.handoff_id else None,
        agent=x.agent,
        confidence=round(x.confidence, 4) if x.confidence is not None else None,
        valid_at=x.valid_at,
        known_at=stored.known_at,
        final_at=x.final_at,
    )


async def exported_outcomes(uow: UnitOfWork, handles: Sequence[UUID]) -> list[Any]:
    """The subject's package: every outcome attributed for them, as the outcomes report shows it."""
    found = await uow.outcomes.links_of(handles)
    return [link_out(s).model_dump(mode="json", exclude_none=True) for s in found]


BI_COLUMNS = frozenset({"order", "value", "currency"})


def bi_lines(content: Iterable[str]) -> list[BiLine]:
    """The BI file's lines; a malformed one refuses the whole file, by its line number, never its content."""
    reader = csv.DictReader(content)
    missing = BI_COLUMNS - set(reader.fieldnames or ())
    if missing:
        raise InvalidInput(f"the BI file lacks the columns {', '.join(sorted(missing))}")
    out = []
    for number, row in enumerate(reader, start=2):
        currency = (row.get("currency") or "").strip().upper()
        value = minor_units((row.get("value") or "").strip(), currency)
        order = (row.get("order") or "").strip()
        if not order or value is None or len(currency) != 3:
            raise InvalidInput(f"line {number} of the BI file has no order, value or currency")
        out.append(BiLine(order, (row.get("line") or "").strip(), value, currency))
    return out


def _period(since: date, until: date) -> None:
    if until < since or (until - since).days > 400:
        raise InvalidInput("the period runs forward and spans at most 400 days")


def _external_id(ref: str) -> str:
    return ref.split(":", 2)[2]


def _text(value: Any) -> str | None:
    return str(value) if isinstance(value, str | int) and not isinstance(value, bool) and str(value) else None


def _uuid(value: Any) -> UUID | None:
    try:
        return UUID(str(value)) if value else None
    except ValueError:
        return None
