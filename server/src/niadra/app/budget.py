"""The context budget: `include: ["budget"]` shows the agent what its context costs.

Three numbers, none from the database: what the served pack costs per section (the tokens its manifest
counts), what this agent's recorded turns already spent in the conversation and in the case (model and tool
calls, tokens, cost), and what the measurement of context use says this agent leaves unused (the cut the
pack may already apply). The turn counters come from the applied turns into the state store, one hash per
conversation and per case, keyed by the keyed digest turn records use, so an erasure finds them by the same
digest. Niadra does not enforce any of it: the agent's loop decides.
"""

import logging
from collections.abc import Mapping, Sequence
from uuid import UUID

from niadra.app.serve import BlockAsk, BlockRead, PackCost
from niadra.app.serve_reads import Target
from niadra.contracts.signals import BudgetBlock, BudgetCut, BudgetPack, BudgetUse
from niadra.domain.common.errors import NotFound
from niadra.domain.common.space import SourcePrincipal
from niadra.domain.privacy.erasure import ErasureScope
from niadra.domain.tasks import Task
from niadra.domain.turns.storage import HMAC_PURPOSE
from niadra.domain.vocabulary import Feature, ReceiptSurface
from niadra.ports.infra import CacheUnavailable, ConfigProvider, KeyVault
from niadra.ports.signals import TurnCounterStore
from niadra.ports.turns import AppliedTurn, TurnConsumer
from niadra.ports.uow import UnitOfWork, UnitOfWorkFactory

log = logging.getLogger(__name__)

COUNTERS_TTL_S = 6 * 3600
"""A conversation's counters live six hours past its last applied turn."""
_FIELDS = ("model_calls", "tool_calls", "tokens_in", "tokens_cached", "tokens_out", "cost_micros")


def counts_of(turn: AppliedTurn) -> str:
    """One turn's counts as its field's value: calls, tokens and cost in millionths of a dollar."""
    record = turn.record
    models = [c for c in record.calls if c.kind == "model"]
    tokens = [c.tokens for c in models if c.tokens is not None]
    values = (
        len(models),
        sum(1 for c in record.calls if c.kind == "tool"),
        sum(t.input for t in tokens),
        sum(t.cached for t in tokens),
        sum(t.output for t in tokens),
        round(record.cost.usd * 1_000_000) if record.cost else 0,
    )
    return ",".join(str(v) for v in values)


def added_up(fields: Mapping[str, str], source_id: UUID) -> tuple[BudgetUse, int]:
    """This source's turns added up, and how many turns the other sources recorded."""
    mine = [0] * len(_FIELDS)
    turns = others = 0
    prefix = f"{source_id}:"
    for field, value in fields.items():
        if not field.startswith(prefix):
            others += 1
            continue
        turns += 1
        for i, part in enumerate(value.split(",")[: len(_FIELDS)]):
            mine[i] += int(part)
    use = BudgetUse(
        turns=turns, model_calls=mine[0], tool_calls=mine[1], tokens_in=mine[2], tokens_cached=mine[3],
        tokens_out=mine[4], cost_usd=round(mine[5] / 1_000_000, 6),
    )  # fmt: skip
    return use, others


class BudgetService:
    """Takes the applied turns' counts (the `interactions` slot of `TurnConsumers`, beside the signals) and
    reads them back for the block; no statement either way."""

    def __init__(
        self, uow: UnitOfWorkFactory, store: TurnCounterStore, keys: KeyVault, config: ConfigProvider
    ) -> None:
        self._uow = uow
        self._store = store
        self._keys = keys
        self._config = config

    async def consume(self, uow: UnitOfWork, space_id: UUID, turns: Sequence[AppliedTurn]) -> None:  # noqa: ARG002
        if not turns or not self._on(space_id):
            return
        counts: dict[str, dict[str, str]] = {}
        for turn in turns:
            field = f"{turn.source_id}:{turn.record.turn_id}"
            value = counts_of(turn)
            for key in await self._digests(space_id, turn.record.conversation_id, turn.record.task_id):
                counts.setdefault(key, {})[field] = value
        try:
            await self._store.add(space_id, counts, COUNTERS_TTL_S)
        except CacheUnavailable:
            # The turns still apply; the budget says it could not count them.
            log.warning("the context budget's turn counters were not written")

    async def use(
        self, space_id: UUID, source_id: UUID, conversation_id: str | None, task_id: str | None
    ) -> tuple[BudgetUse | None, BudgetUse | None, int] | None:
        """This source's use in the conversation and in the case, and the other agents' turns; None when
        the state store could not be read."""
        keys = [
            await self._digest(space_id, name) if name else None
            for name in (conversation_id, f"task:{task_id}" if task_id else None)
        ]
        wanted = [k for k in keys if k is not None]
        try:
            found = dict(zip(wanted, await self._store.read(space_id, wanted), strict=True)) if wanted else {}
        except CacheUnavailable:
            return None
        conversation = case = None
        others = 0
        if keys[0] is not None:
            conversation, others = added_up(found[keys[0]], source_id)
        if keys[1] is not None:
            case, _ = added_up(found[keys[1]], source_id)
        return conversation, case, others

    async def purge(self, task: Task) -> None:
        """After an erasure: the counters of the target's conversations, by the digests the erasure found
        (those a legal hold keeps are not among them). A case's counters, when the case had a conversation,
        expire with their hours."""
        space_id = task.space_id
        async with self._uow(space_id) as uow:
            request = await uow.privacy.get_erasure(space_id, UUID(task.payload["request_id"]))
        if request is None or "scope" not in request.counts:
            return
        hashes = ErasureScope.from_json(request.counts["scope"]).conversation_hashes
        if hashes:
            await self._store.forget(space_id, [h.hex() for h in hashes])

    async def _digests(self, space_id: UUID, conversation_id: str | None, task_id: str | None) -> list[str]:
        names = [n for n in (conversation_id, f"task:{task_id}" if task_id else None) if n]
        return [await self._digest(space_id, name) for name in names]

    async def _digest(self, space_id: UUID, name: str) -> str:
        return (await self._keys.hmac(space_id, HMAC_PURPOSE, name.encode())).hex()

    def _on(self, space_id: UUID) -> bool:
        try:
            return Feature.TURNS in self._config.space(space_id).features.enabled
        except NotFound:
            return False


class BudgetReadBlock:
    """The budget block as a read's `include` names it (24.2.10)."""

    name = "budget"
    feature = Feature.TURNS
    surface = ReceiptSurface.BUDGET

    def __init__(self, budget: BudgetService) -> None:
        self._budget = budget

    async def read(self, caller: SourcePrincipal, target: Target | None, ask: BlockAsk) -> BlockRead:  # noqa: ARG002
        space_id = caller.space.space_id
        found = await self._budget.use(space_id, caller.source_id, ask.conversation_id, ask.task_id)
        conversation, case, others = found if found is not None else (None, None, 0)
        block = BudgetBlock(
            pack=_pack(ask.pack),
            conversation=conversation,
            case=case,
            other_agents_turns=others,
            cut=_cut(ask.pack),
            counted=found is not None,
        )
        detail = {"counted": "1" if found is not None else "0"}
        if block.pack is not None:
            detail["pack_tokens"] = str(block.pack.total)
        if conversation is not None:
            detail["turns"] = str(conversation.turns)
        return BlockRead(block, detail=detail)


def _pack(cost: PackCost | None) -> BudgetPack | None:
    return BudgetPack(total=cost.total, sections=dict(cost.sections)) if cost is not None else None


def _cut(cost: PackCost | None) -> BudgetCut | None:
    if cost is None or not cost.cut:
        return None
    return BudgetCut(
        units=list(cost.cut),
        applied=cost.applied,
        window_days=cost.window_days,
        min_deliveries=cost.min_deliveries,
    )


class TurnsTaken:
    """Several consumers in one slot of `TurnConsumers`, in order."""

    def __init__(self, *consumers: TurnConsumer) -> None:
        self._consumers = consumers

    async def consume(self, uow: UnitOfWork, space_id: UUID, turns: Sequence[AppliedTurn]) -> None:
        for consumer in self._consumers:
            await consumer.consume(uow, space_id, turns)
