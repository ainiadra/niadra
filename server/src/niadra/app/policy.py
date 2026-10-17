"""The policy simulator (5.12): "what would agent X see of profile Y at V1?", for compliance teams.

It runs the real context compiler for a hypothetical reader, so the answer is exactly what that
reader would receive, with every exclusion and its reason.
"""

from collections import Counter
from typing import Protocol
from uuid import UUID

from niadra.app.compile import compile_settings, load_snapshot, trim_for
from niadra.app.serve import LiveScreen
from niadra.config.models import SpaceConfig
from niadra.contracts.policy import (
    SimulatedItem,
    SimulatedLive,
    SimulatedLiveTurn,
    SimulateRequest,
    SimulateResponse,
)
from niadra.domain.common.errors import NotFound
from niadra.domain.common.space import Principal
from niadra.domain.compile import CompiledPack, CompileInput, Variant, compile_pack
from niadra.domain.identity.provenance import reader_view
from niadra.domain.policy import effective_verification
from niadra.domain.records import ProfileRecord
from niadra.domain.vocabulary import Audience, EventKind, Verification
from niadra.ports.infra import ConfigProvider
from niadra.ports.uow import UnitOfWorkFactory


class LiveSource(Protocol):
    """The read path's `live` screening (`ServeService.screen_live`), so the simulation shows what a read
    gets of the customer's recent turns with the same function."""

    async def screen_live(
        self,
        space_id: UUID,
        profile: ProfileRecord,
        variant: Variant,
        config: SpaceConfig,
        *,
        pack: CompiledPack | None,
        thread: str | None = None,
    ) -> LiveScreen: ...


class PolicyService:
    def __init__(
        self, uow: UnitOfWorkFactory, config: ConfigProvider, *, live: LiveSource | None = None
    ) -> None:
        self._uow = uow
        self._config = config
        self._live = live

    async def simulate(self, principal: Principal, request: SimulateRequest) -> SimulateResponse:
        space_id = principal.space.space_id
        config = self._config.space(space_id)
        source = config.source(request.source_id) if request.source_id else None
        if request.source_id and source is None:
            raise NotFound("unknown source")
        audience = request.audience or (source.audience if source else Audience.CUSTOMER_AGENT)
        purposes = frozenset(request.purposes) or (source.purposes if source else frozenset())
        # Same admission rule as a real read: `no_customer` only for internal agents (2.2).
        level = effective_verification(
            request.verification, ceiling=Verification.V4, proven=request.verification, audience=audience
        ).effective
        async with self._uow(space_id) as uow:
            found = await uow.identity.get_profile(request.profile_id)
            if found is None:
                raise NotFound("profile not found")
            # As an agent's read by profile id: without the rows of unconfirmed links (5.3).
            profile = reader_view(found)
            snapshot = await load_snapshot(uow, profile, timezone=config.settings.timezone)
            trims = await uow.measure.trims() if request.source_id else []
        competing = request.source_id is not None and str(request.source_id) in config.policy.competes_with
        variant = Variant(
            view=request.view,
            verification=level,
            audience=audience,
            purposes=purposes,
            source_id=request.source_id if competing else None,
            # As a read of that source would: what it never used stays out (15.8).
            trim=trim_for(config, request.source_id, request.view, trims)
            if request.source_id
            else frozenset(),
        )
        pack = compile_pack(CompileInput(snapshot, variant, compile_settings(config)))
        manifest = pack.manifest
        items = [
            # `rule` says when the mirror rule released it: what the customer said, back to them from V1.
            SimulatedItem(
                id=i["id"],
                kind=i["kind"],
                category=i["category"],
                effect=i["effect"],
                rule=i.get("rule") or None,
                delivered=True,
            )
            for i in manifest["items"]
        ]
        for excluded in manifest["excluded"]:
            # Left out by the budget or for lack of use: the policy allowed it.
            allowed = excluded["reason"] in ("budget", "unused")
            items.append(
                SimulatedItem(
                    id=excluded["id"],
                    kind=excluded["kind"],
                    category=excluded.get("category", ""),
                    effect="allow" if allowed else "deny",
                    reason=str(excluded["reason"]),
                    rule=excluded.get("rule") or None,
                    delivered=False,
                )
            )
        return SimulateResponse(
            policy_version=config.policy.version,
            verification=level,
            audience=audience,
            items=sorted(items, key=lambda i: i.id),
            withheld={str(k): int(v) for k, v in manifest["withheld"].items()},
            text=pack.text,
            live=await self._simulate_live(space_id, profile, variant, config, pack),
        )

    async def _simulate_live(
        self,
        space_id: UUID,
        profile: ProfileRecord,
        variant: Variant,
        config: SpaceConfig,
        pack: CompiledPack,
    ) -> SimulatedLive:
        """The `live` block the same read would carry: every recent turn of the customer's conversations,
        through the pack's policy for this reader; a withheld turn keeps its reason and never its text."""
        if self._live is None:
            return SimulatedLive(complete=False)
        screen = await self._live.screen_live(space_id, profile, variant, config, pack=pack)
        turns = []
        withheld: Counter[str] = Counter()
        for line, decision, turn in screen.lines:
            doc = line.doc
            reason = decision.reason.value if decision.reason else None
            if not decision.included:
                withheld[reason or "policy"] += 1
            turns.append(
                SimulatedLiveTurn(
                    at=doc["occurred_at"],
                    channel=doc["channel"],
                    kind=EventKind(doc["kind"]),
                    speaker=doc["speaker"],
                    source_id=doc["source_id"],
                    delivered=turn is not None,
                    effect=decision.effect.value,
                    reason=reason,
                    rule=decision.rule or None,
                    text=turn.text if turn is not None else None,
                )
            )
        return SimulatedLive(
            turns=turns,
            delivered=sum(1 for t in turns if t.delivered),
            withheld=dict(sorted(withheld.items())),
            complete=screen.complete,
        )
