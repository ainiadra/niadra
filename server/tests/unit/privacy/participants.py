"""Erasure and retention scenarios for the governance modules' own tables: trigger firings, review samples
and webhook deliveries. Run against the in-memory repositories (unit) and PostgreSQL (integration), so both
implementations answer the same questions the same way.
"""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from niadra.domain.common.ids import uuid7
from niadra.domain.extract.result import EpisodeDraft, ExtractionResult, ExtractionRun, RunStatus
from niadra.domain.notify.webhooks import (
    Delivery,
    DeliveryStatus,
    WebhookSubject,
    body_hash,
    build_body,
    encode_body,
)
from niadra.domain.privacy.erasure import ErasureScope, TargetKind
from niadra.domain.records import SessionRecord
from niadra.domain.review.sampling import ReviewSampleRecord
from niadra.domain.traits.evidence import EvidenceRef
from niadra.domain.triggers.firing import Firing

NOW = datetime(2026, 9, 22, 18, tzinfo=UTC)
TENANT = UUID("0192f5a0-0000-7000-8000-00000000b001")


@dataclass(frozen=True)
class People:
    """Ana has two handles; Bruno one. The conversation `c-ana` is Ana's."""

    ana: UUID
    ana_phone: UUID
    ana_mail: UUID
    bruno: UUID
    bruno_phone: UUID

    @classmethod
    def make(cls) -> "People":
        return cls(uuid7(), uuid7(), uuid7(), uuid7(), uuid7())

    def scope(self, kind: TargetKind, **changes: Any) -> ErasureScope:
        base: dict[str, Any] = {
            TargetKind.PROFILE: {"handle_ids": (self.ana_phone, self.ana_mail), "profile_id": self.ana},
            TargetKind.HANDLE: {"handle_ids": (self.ana_mail,), "profile_id": self.ana},
            TargetKind.CONVERSATION: {
                "handle_ids": (self.ana_phone,),
                "profile_id": self.ana,
                "conversation_id": "c-ana",
            },
        }[kind]
        values = {"conversation_id": None, "pseudonym": "pz-ana", **base, **changes}
        return ErasureScope(uuid7(), kind, requested_at=NOW, **values)


def firing(subject: UUID, *evidence: EvidenceRef, days_ago: float = 0) -> Firing:
    return Firing(
        uuid7(),
        "rule",
        "v1",
        subject,
        f"w-{uuid7()}",
        b"\x01" * 32,
        evidence,
        1,
        NOW - timedelta(days=days_ago),
    )


def sample(profile: UUID, conversation: str, *, days_ago: float = 0) -> ReviewSampleRecord:
    return ReviewSampleRecord(
        uuid7(),
        uuid7(),
        "base",
        "support",
        NOW - timedelta(days=days_ago),
        profile_id=profile,
        conversation_id=conversation,
    )


def delivery(subject: str | None, data: dict[str, Any], *, days_ago: float = 0) -> Delivery:
    delivery_id = uuid7()
    body = build_body(
        delivery_id=delivery_id,
        event_type="trait.added",
        created_at=NOW - timedelta(days=days_ago),
        tenant_id=TENANT,
        subject=WebhookSubject(subject, "person") if subject else None,
        data=data,
        links=None,
    )
    return Delivery(
        delivery_id,
        "crm",
        "trait.added",
        body,
        body_hash(encode_body(body)),
        DeliveryStatus.DELIVERED,
        NOW - timedelta(days=days_ago),
    )


async def triggers_scenario(uow_factory: Any, space: UUID) -> None:
    p = People.make()
    erased_event = uuid7()
    own = firing(p.ana, EvidenceRef("open_item", uuid7(), p.ana_phone))
    by_mail = firing(p.bruno, EvidenceRef("handle", uuid7(), p.ana_mail))
    by_event = firing(p.bruno, EvidenceRef("event", erased_event, p.bruno_phone))
    brunos = firing(p.bruno, EvidenceRef("event", uuid7(), p.bruno_phone))
    async with uow_factory(space) as uow:
        for f in (own, by_mail, by_event, brunos):
            assert await uow.triggers.add_firing(f)

    async with uow_factory(space) as uow:
        evidence = p.scope(TargetKind.CONVERSATION).with_events((erased_event,))
        assert _ids(await uow.triggers.erase_for(evidence, limit=10), "firing_id") == {by_event.firing_id}
        assert await uow.triggers.erase_for(p.scope(TargetKind.CONVERSATION), limit=10) == []
        first = await uow.triggers.erase_for(p.scope(TargetKind.PROFILE), limit=1)
        rest = await uow.triggers.erase_for(p.scope(TargetKind.PROFILE), limit=10)
        assert {r.table for r in first + rest} == {"trigger_firings"}
        assert _ids(first + rest, "firing_id") == {own.firing_id, by_mail.firing_id}
        assert await uow.triggers.erase_for(p.scope(TargetKind.PROFILE), limit=10) == []
        assert [f.firing_id for f in await uow.triggers.list_firings()] == [brunos.firing_id]


async def triggers_retention(uow_factory: Any, space: UUID) -> None:
    p = People.make()
    old = firing(p.ana, EvidenceRef("open_item", uuid7(), p.ana_phone), days_ago=400)
    recent = firing(p.ana, EvidenceRef("open_item", uuid7(), p.ana_phone), days_ago=10)
    async with uow_factory(space) as uow:
        await uow.triggers.add_firing(old)
        await uow.triggers.add_firing(recent)
    async with uow_factory(space) as uow:
        assert await uow.triggers.purge_before(NOW - timedelta(days=395)) == 1
        assert [f.firing_id for f in await uow.triggers.list_firings()] == [recent.firing_id]


async def review_scenario(uow_factory: Any, space: UUID) -> None:
    p = People.make()
    in_conversation, elsewhere, brunos = sample(p.ana, "c-ana"), sample(p.ana, "c-2"), sample(p.bruno, "c-3")
    async with uow_factory(space) as uow:
        for s in (in_conversation, elsewhere, brunos):
            await uow.review.add_sample(s)
    async with uow_factory(space) as uow:
        stored = await uow.review.get_sample(in_conversation.sample_id)
        assert (stored.profile_id, stored.conversation_id) == (p.ana, "c-ana")
        conversation = await uow.review.erase_for(p.scope(TargetKind.CONVERSATION), limit=10)
        assert _ids(conversation, "sample_id") == {in_conversation.sample_id}
        handle = await uow.review.erase_for(p.scope(TargetKind.HANDLE), limit=10)
        assert _ids(handle, "sample_id") == {elsewhere.sample_id}
        assert await uow.review.erase_for(p.scope(TargetKind.PROFILE), limit=10) == []
        assert await uow.review.get_sample(brunos.sample_id) == brunos


async def review_retention(uow_factory: Any, space: UUID) -> None:
    p = People.make()
    old, recent = sample(p.ana, "c-1", days_ago=366), sample(p.ana, "c-2", days_ago=1)
    async with uow_factory(space) as uow:
        await uow.review.add_sample(old)
        await uow.review.add_sample(recent)
    async with uow_factory(space) as uow:
        assert await uow.review.purge_before(NOW - timedelta(days=365)) == 1
        assert await uow.review.get_sample(old.sample_id) is None
        assert await uow.review.get_sample(recent.sample_id) is not None


async def notify_scenario(uow_factory: Any, space: UUID) -> None:
    p = People.make()
    about_ana = delivery("pz-ana", {"trait": "vip"})
    names_mail = delivery("pz-other", {"evidence": [{"origin_handle_id": str(p.ana_mail)}]})
    brunos = delivery("pz-bruno", {"trait": "vip"})
    async with uow_factory(space) as uow:
        for d in (about_ana, names_mail, brunos):
            await uow.notify.add_delivery(d)
    async with uow_factory(space) as uow:
        assert await uow.notify.erase_for(p.scope(TargetKind.CONVERSATION), limit=10) == []
        handle = await uow.notify.erase_for(p.scope(TargetKind.HANDLE), limit=10)
        assert _ids(handle, "delivery_id") == {names_mail.delivery_id}
        profile = await uow.notify.erase_for(p.scope(TargetKind.PROFILE), limit=10)
        assert _ids(profile, "delivery_id") == {about_ana.delivery_id}
        assert [d.delivery_id for d in await uow.notify.list_deliveries()] == [brunos.delivery_id]


async def notify_retention(uow_factory: Any, space: UUID) -> None:
    old, recent = delivery(None, {"n": 1}, days_ago=31), delivery(None, {"n": 2}, days_ago=1)
    async with uow_factory(space) as uow:
        await uow.notify.add_delivery(old)
        await uow.notify.add_delivery(recent)
    async with uow_factory(space) as uow:
        assert await uow.notify.purge_before(NOW - timedelta(days=30)) == 1
        assert [d.delivery_id for d in await uow.notify.list_deliveries()] == [recent.delivery_id]


async def extraction_scenario(uow_factory: Any, space: UUID) -> None:
    p = People.make()
    result = ExtractionResult(EpisodeDraft("Pediu segunda via", "request", "invoice", "open"))

    def session(profile: UUID, conversation: str) -> SessionRecord:
        return SessionRecord(
            uuid7(), "conversation", uuid7(), "whatsapp", NOW, NOW, conversation, None, profile
        )

    anas = [session(p.ana, "c-ana"), session(p.ana, "c-2")]
    brunos = session(p.bruno, "c-3")
    async with uow_factory(space) as uow:
        for s in (*anas, brunos):
            await uow.sessions.create_session(space, s)
            await uow.extract.save_run(ExtractionRun(s.session_id, "v1", RunStatus.OK, NOW, result=result))
    async with uow_factory(space) as uow:
        in_conversation = await uow.sessions.session_ids_for(p.scope(TargetKind.CONVERSATION))
        assert in_conversation == [anas[0].session_id]
        # A handle's erasure reaches every session of its profile: sessions do not record who spoke.
        heard = await uow.sessions.session_ids_for(p.scope(TargetKind.HANDLE))
        assert set(heard) == {s.session_id for s in anas}
        scope = p.scope(TargetKind.HANDLE).with_sessions(tuple(heard))
        first = await uow.extract.erase_for(scope, limit=1)
        rest = await uow.extract.erase_for(scope, limit=10)
        assert {r.key["session_id"] for r in first + rest} == {str(s.session_id) for s in anas}
        assert await uow.extract.erase_for(scope, limit=10) == []
        assert await uow.extract.erase_for(p.scope(TargetKind.PROFILE), limit=10) == [], "no sessions given"
    async with uow_factory(space) as uow:
        cleared = await uow.extract.get_run(anas[0].session_id, "v1")
        assert cleared is not None
        assert cleared.result is None
        kept = await uow.extract.get_run(brunos.session_id, "v1")
        assert kept is not None
        assert kept.result == result


SCENARIOS = (
    triggers_scenario,
    triggers_retention,
    review_scenario,
    review_retention,
    notify_scenario,
    notify_retention,
    extraction_scenario,
)


def _ids(rows: list[Any], key: str) -> set[UUID]:
    return {UUID(r.key[key]) for r in rows}
