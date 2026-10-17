"""Drift of the declared types (24.2.1; object-type spec 3.2.2 and 8.8), and the types the space never
declared (24.3.5), returned to the company as data issues through the turns module's `DataIssues` port.

A deriving tool checks a type by its fingerprint (`POST /v1/types/fingerprint`): a fingerprint other than the
declaration's is drift, and a type that alerts opens a `drift` issue, or counts one more occurrence of the
open one. The traffic shows drift too, with no tool and no access to the company's database: a report of a
field the type names nowhere, or of a state it does not declare, opens the same issue, from the transaction
that merges the report; on a shared object, whose push is decided with no statement, from the consolidator's
transaction a second later. What a customer-facing agent's tools showed of a type the space never declared,
once the wait for the configuration ended, opens a `type_undeclared` issue. An issue carries names and object
references only, never a value; `type.drift` goes out once per issue, to the endpoints that take it.
"""

import logging
from collections.abc import Mapping, Sequence
from datetime import timedelta
from typing import Any
from uuid import UUID

from niadra.app.notify import enqueue_event, subscribes
from niadra.app.typed_state import Attribution
from niadra.config.models import SpaceConfig
from niadra.config.object_types import ObjectType
from niadra.contracts.state import DriftChanges, TypeFingerprintRequest, TypeFingerprintResponse
from niadra.domain.common.errors import NoFingerprint, NotFound
from niadra.domain.common.space import SourcePrincipal
from niadra.domain.objects.state import BOOKKEEPING, TOOL_PREFIX
from niadra.domain.objects.undeclared import held
from niadra.domain.records import ObjectKey
from niadra.domain.state.drift import Declared, Recently, drifted_fields
from niadra.domain.state.observed import Report
from niadra.domain.vocabulary import Feature
from niadra.ports.data_issues import DataIssues
from niadra.ports.infra import Clock, ConfigProvider, KeyValueCache
from niadra.ports.state import DriftNoticed
from niadra.ports.uow import UnitOfWork, UnitOfWorkFactory

log = logging.getLogger(__name__)

ANNOUNCED_FOR = timedelta(days=90)
"""How long the cache remembers an issue was announced: longer than a drift stays unacknowledged."""
_CACHE_ERRORS = (ConnectionError, TimeoutError, OSError)


class TypeDriftService:
    def __init__(
        self,
        uow: UnitOfWorkFactory,
        clock: Clock,
        config: ConfigProvider,
        issues: DataIssues,
        cache: KeyValueCache,
    ) -> None:
        self._uow = uow
        self._clock = clock
        self._config = config
        self._issues = issues
        self._cache = cache
        self._recently = Recently()
        self._acknowledged: dict[UUID, bytes | None] = {}

    async def check(
        self, caller: SourcePrincipal, request: TypeFingerprintRequest
    ) -> TypeFingerprintResponse:
        """`POST /v1/types/fingerprint` (object-type spec 8.8.3): the same fingerprint is no drift; another
        one opens or counts the type's `drift` issue when the type alerts, naming the field when the changes
        name exactly one."""
        space_id = caller.space.space_id
        space = self._config.space(space_id)
        declared = space.object_types.get(request.type)
        if declared is None:
            raise NotFound("the space declares no object type of this name")
        mirror = declared.mirror_of
        if mirror is None or mirror.fingerprint is None:
            raise NoFingerprint("the type keeps no fingerprint of the schema it mirrors")
        if request.fingerprint == mirror.fingerprint:
            return TypeFingerprintResponse(drift=False)
        if mirror.drift != "alert":
            return TypeFingerprintResponse(drift=True)
        changes = request.changes
        field = changes.fields[0] if changes is not None and len(changes.fields) == 1 else None
        async with self._uow(space_id) as uow:
            issue_id = await self._issues.open(uow, space_id, "drift", object_type=declared.type, field=field)
            await self._announce(uow, space, issue_id, declared.type, field, "fingerprint", changes)
        return TypeFingerprintResponse(drift=True, issue_id=str(issue_id))

    async def observed(
        self,
        uow: UnitOfWork,
        space: SpaceConfig,
        key: ObjectKey,
        report: Report,
        names: Mapping[UUID, str],
    ) -> None:
        """Drift by observation (object-type spec 3.2.2), inside the transaction that merges `report`: each
        field the type names nowhere, and a state it does not declare, is a `drift` issue of the type, the
        field and the source; one statement per new finding, and a finding this process reported within the
        hour costs none while its issue stays open."""
        ref = f"{key.type}:{key.namespace}:{key.external_id}"
        await self.open_noticed(uow, space, await self.noticed(space, key.type, ref, report, names))

    async def noticed(
        self, space: SpaceConfig, type_: str, ref: str, report: Report, names: Mapping[UUID, str]
    ) -> list[DriftNoticed]:
        """`SharedDrift`, and the first half of `observed`: what of the report the type does not declare, less
        what this process reported within the hour while its issue stays open. No statement."""
        declared = space.object_types.get(type_)
        if (
            Feature.STATE not in space.features.enabled
            or declared is None
            or declared.mirror_of is None
            or declared.mirror_of.drift != "alert"
        ):
            return []
        source = Attribution(declared, names)(report.source).name
        found = drifted_fields(report.fields, _declared(declared, source), BOOKKEEPING)
        if not found:
            return []
        await self._forget_acknowledged(space.space_id)
        # A new field is served to agents as it came, and its `drift` issue is a note; one that may be
        # personal data or free text is held from them until declared, and says so (`field_held`).
        kinds = {
            field: "field_held"
            if field in report.fields and held(field, report.fields[field]) is not None
            else "drift"
            for field in found
        }
        fresh = self._recently.fresh(
            ((str(space.space_id), kinds[field], type_, field, source) for field in found), self._clock.now()
        )
        return [
            DriftNoticed(type_, field, source, ref, "field_held" if kind == "field_held" else "drift")
            for _, kind, _, field, _ in fresh
        ]

    async def open_noticed(
        self, uow: UnitOfWork, space: SpaceConfig, noticed: Sequence[DriftNoticed]
    ) -> None:
        """Each finding's `drift` issue, one statement per type, field and source, with the objects it was
        seen on."""
        refs: dict[tuple[str, str, str, str], list[str]] = {}
        for n in noticed:
            seen = refs.setdefault((n.kind, n.type, n.field, n.source), [])
            if n.ref not in seen:
                seen.append(n.ref)
        for (kind, type_, field, source), seen in refs.items():
            issue_id = await self._issues.open(
                uow,
                space.space_id,
                "field_held" if kind == "field_held" else "drift",
                object_type=type_,
                field=field,
                source=source,
                refs=seen,
            )
            await self._announce(uow, space, issue_id, type_, field, "observation", None)

    async def undeclared(self, space_id: UUID, dropped: Sequence[Any]) -> None:
        """What a customer-facing agent's tools showed of objects of a type the space did not declare once
        the wait for the configuration ended: never state, since such an agent never makes a type (an internal
        agent's is kept for the inference, `ObservationsConsumer.kept_undeclared`), and a `type_undeclared`
        issue per type and tool, with the objects' references."""
        by_kind: dict[tuple[str, str], list[str]] = {}
        for observation in dropped:
            ref = str(observation.ref)
            tool = str(observation.source).removeprefix(TOOL_PREFIX)
            by_kind.setdefault((ref.split(":", 1)[0], tool), []).append(ref)
        if not by_kind:
            return
        await self._forget_acknowledged(space_id)
        fresh = self._recently.fresh(
            ((str(space_id), "type_undeclared", kind, tool) for kind, tool in by_kind), self._clock.now()
        )
        if not fresh:
            return
        async with self._uow(space_id) as uow:
            for *_, kind, tool in fresh:
                await self._issues.open(
                    uow,
                    space_id,
                    "type_undeclared",
                    object_type=kind,
                    source=tool,
                    refs=by_kind[(kind, tool)],
                )

    async def _forget_acknowledged(self, space_id: UUID) -> None:
        """Forgets what this process reported in the space once an issue there was acknowledged, so the next
        occurrence opens a new issue now rather than within the hour. Without the cache the throttle holds."""
        try:
            token = await self._issues.acknowledged(space_id)
        except _CACHE_ERRORS:
            return
        if self._acknowledged.get(space_id, token) != token:
            self._recently.forget(str(space_id))
        self._acknowledged[space_id] = token

    async def _announce(
        self,
        uow: UnitOfWork,
        space: SpaceConfig,
        issue_id: UUID,
        object_type: str,
        field: str | None,
        seen_by: str,
        changes: DriftChanges | None,
    ) -> None:
        """`type.drift` once per issue: the cache remembers which issues were announced; without it, the
        announcement goes out again rather than never."""
        if not subscribes(space, ("type.drift",)):
            return
        try:
            first = await self._cache.incr_window(
                f"type_drift:{space.space_id}:{issue_id}", int(ANNOUNCED_FOR.total_seconds())
            )
        except _CACHE_ERRORS:
            log.warning("the drift announcement is not remembered; it may go out again")
            first = 1
        if first != 1:
            return
        data: dict[str, Any] = {
            "issue_id": str(issue_id),
            "type": object_type,
            "field": field,
            "seen_by": seen_by,
        }
        if changes is not None:
            data["changes"] = changes.model_dump(mode="json")
        await enqueue_event(uow, space, "type.drift", None, data)


def _declared(declared: ObjectType, source: str) -> Declared:
    outcome = declared.lifecycle.outcome if declared.lifecycle else None
    vocabulary: dict[str, str] = {}
    for listed in outcome.vocabularies if outcome else ():
        if listed.source == source:
            vocabulary.update(listed.map)
    return Declared(
        names=frozenset(declared.fields)
        | frozenset(declared.values)
        | frozenset(declared.axes)
        | frozenset(declared.derived_fields),
        states=frozenset(declared.states),
        vocabulary=vocabulary,
    )
