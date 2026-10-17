"""Data issues (24.3.5, 24.6.6): the `DataIssues` port other modules open issues through, and the operator's
list and acknowledgement (`GET /v1/data-issues`, `POST /v1/data-issues/{id}/ack`).

An issue carries a class, a type, a field, a source and object references, all system names and ids: no value
and no person. Opening one spends one statement of the finder's transaction; announcing it
(`data_issue.opened`) happens after that transaction commits, from the task the same statement added.
Acknowledging one leaves a new token in the cache, so a finder that throttles what it reports forgets it and
the next occurrence opens a new issue at once.
"""

import base64
import logging
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from uuid import UUID

from niadra.app.admin_audit import AdminAudit
from niadra.app.notify import enqueue_event, subscribes
from niadra.config.models import SpaceConfig
from niadra.contracts.turns import DataIssue, DataIssuePage
from niadra.domain.common.errors import InvalidInput, NotFound
from niadra.domain.common.ids import uuid7
from niadra.domain.common.space import Principal, SourcePrincipal
from niadra.domain.tasks import Task
from niadra.domain.turns.issues import (
    DATA_ISSUE_KINDS,
    DataIssueKind,
    DataIssueRow,
    IssueQuery,
    TypeRules,
    new_issue,
)
from niadra.ports.infra import Clock, ConfigProvider, KeyValueCache
from niadra.ports.uow import UnitOfWork, UnitOfWorkFactory

log = logging.getLogger(__name__)

OPENED = "data_issue.opened"
ACKNOWLEDGED_FOR = timedelta(days=1)
"""How long the cache keeps a space's acknowledgement token: longer than any finder throttles a finding."""
_STATUS = {"open": True, "acknowledged": False, "all": None}
_CACHE_ERRORS = (ConnectionError, TimeoutError, OSError)


class DataIssuesService:
    def __init__(
        self,
        uow: UnitOfWorkFactory,
        clock: Clock,
        config: ConfigProvider,
        audit: AdminAudit,
        cache: KeyValueCache,
    ) -> None:
        self._uow = uow
        self._clock = clock
        self._config = config
        self._audit = audit
        self._cache = cache

    async def open(
        self,
        uow: UnitOfWork,
        space_id: UUID,
        kind: DataIssueKind,
        *,
        object_type: str | None = None,
        field: str | None = None,
        source: str | None = None,
        refs: Sequence[str] = (),
    ) -> UUID:
        issue = new_issue(
            uuid7(),
            kind,
            self._clock.now(),
            object_type=object_type,
            field=field,
            source=source,
            refs=refs,
        )
        return await uow.turns.open_issue(issue, tell=self._tells(space_id))

    async def acknowledged(self, space_id: UUID) -> bytes | None:
        return await self._cache.get(_acknowledged_key(space_id))

    def _tells(self, space_id: UUID) -> bool:
        try:
            return subscribes(self._config.space(space_id), (OPENED,))
        except NotFound:
            return False

    async def notify_opened(self, task: Task) -> None:
        """`data_issue.opened` to every endpoint that subscribes: the issue's names, count and references."""
        config = self._config.space(task.space_id)
        async with self._uow(task.space_id) as uow:
            row = await uow.turns.issue(UUID(task.payload["issue_id"]))
            if row is not None:
                await enqueue_event(uow, config, "data_issue.opened", None, _body(row))
        if row is not None:
            lag = (datetime.now(UTC) - row.opened_at).total_seconds()
            log.info("data issue announced", extra={"kind": row.kind, "lag_s": round(lag, 1)})

    async def list(
        self, principal: Principal, *, status: str, kind: str | None, cursor: str | None, limit: int
    ) -> DataIssuePage:
        if kind is not None and kind not in DATA_ISSUE_KINDS:
            raise InvalidInput(f"`kind` is one of {', '.join(sorted(DATA_ISSUE_KINDS))}")
        query = IssueQuery(open=_STATUS[status], kind=kind, before=_decode(cursor), limit=limit + 1)
        async with self._uow(principal.space.space_id) as uow:
            rows = await uow.turns.issues(query)
        page = rows[:limit]
        return DataIssuePage(
            items=[_view(r) for r in page],
            next_cursor=_encode(page[-1]) if len(rows) > limit and page else None,
        )

    async def acknowledge(self, principal: Principal, issue_id: str) -> DataIssue:
        """Closes the issue; acknowledging it again answers it as it is. The next occurrence opens another."""
        wanted = _uuid(issue_id)
        async with self._uow(principal.space.space_id) as uow:
            row = await uow.turns.acknowledge_issue(wanted, _actor(principal), self._clock.now())
        if row is None:
            raise NotFound("data issue not found")
        await self._audit.record(principal, action="data_issue.acknowledged", target=f"data_issue:{wanted}")
        try:
            await self._cache.set(
                _acknowledged_key(principal.space.space_id),
                uuid7().bytes,
                int(ACKNOWLEDGED_FOR.total_seconds()),
            )
        except _CACHE_ERRORS:
            log.warning("the acknowledgement is not in the cache; a new occurrence may wait up to an hour")
        return _view(row)


def type_rules(space: SpaceConfig) -> dict[str, TypeRules]:
    """What each declared type says about its values, for attributing a failure to data (24.6.6)."""
    rules = {}
    for declared in space.object_types.types:
        outcome = declared.lifecycle.outcome if declared.lifecycle else None
        codes = {code for v in (outcome.vocabularies if outcome else ()) for code in v.map}
        states = frozenset(declared.states) | codes if declared.states else frozenset()
        rules[declared.type] = TypeRules({n: f.max_age_ms() for n, f in declared.fields.items()}, states)
    return rules


def _view(row: DataIssueRow) -> DataIssue:
    return DataIssue.model_validate(
        {
            "issue_id": str(row.issue_id),
            "kind": row.kind,
            "object_type": row.object_type,
            "field": row.field,
            "source": row.source,
            "refs": list(row.refs),
            "occurrences": row.occurrences,
            "opened_at": row.opened_at,
            "last_seen_at": row.last_seen_at,
            "acknowledged_at": row.acknowledged_at,
        }
    )


def _body(row: DataIssueRow) -> dict[str, object]:
    """Names and system ids only (7.5): an object reference is `type:namespace:id`, never a value."""
    return {
        "issue_id": str(row.issue_id),
        "kind": row.kind,
        "object_type": row.object_type,
        "field": row.field,
        "source": row.source,
        "refs": list(row.refs),
        "occurrences": row.occurrences,
        "opened_at": row.opened_at.isoformat(),
    }


def _acknowledged_key(space_id: UUID) -> str:
    return f"data_issues_acknowledged:{space_id}"


def _actor(principal: Principal) -> str:
    if isinstance(principal, SourcePrincipal):
        return f"source:{principal.source_id}"
    return f"user:{principal.user_id}"


def _uuid(value: str) -> UUID:
    try:
        return UUID(value)
    except ValueError:
        raise NotFound("data issue not found") from None


def _encode(row: DataIssueRow) -> str:
    return base64.urlsafe_b64encode(f"{row.opened_at.isoformat()}|{row.issue_id}".encode()).decode()


def _decode(value: str | None) -> tuple[datetime, UUID] | None:
    if value is None:
        return None
    try:
        at, issue_id = base64.urlsafe_b64decode(value.encode()).decode().split("|", 1)
        return datetime.fromisoformat(at), UUID(issue_id)
    except (ValueError, UnicodeDecodeError):
        raise InvalidInput("the cursor is not one this list returned") from None
