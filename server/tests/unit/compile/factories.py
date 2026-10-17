"""Memory records for the read-path tests: Marina's history from the architecture examples (7.2, 8.1)."""

from datetime import UTC, datetime, timedelta
from uuid import UUID

from niadra.domain.compile import CompileSettings, MemorySnapshot
from niadra.domain.records import (
    AgentAction,
    BusinessObject,
    Episode,
    Fact,
    ObjectKey,
    OpenItem,
    ProfileAttr,
    ProfileRecord,
    Trait,
)
from niadra.domain.vocabulary import (
    ActionStatus,
    FactStatus,
    OpenItemStatus,
    Sensitivity,
    Speaker,
    SubjectKind,
)
from niadra.testing.cell import BILLING_SOURCE, WHATSAPP_SOURCE, sample_config

T0 = datetime(2026, 9, 22, 17, 0, tzinfo=UTC)
PROFILE = UUID("0192f5a0-0000-7000-8000-0000000d0001")
PHONE = UUID("0192f5a0-0000-7000-8000-0000000e0001")
WA = UUID("0192f5a0-0000-7000-8000-0000000e0002")


def uid(n: int) -> UUID:
    return UUID(f"0192f5a0-0000-7000-8000-{n:012x}")


def profile(
    privacy_version: int = 1,
    kind: SubjectKind = SubjectKind.PERSON,
    pid: UUID = PROFILE,
    handles: tuple[UUID, ...] = (PHONE, WA),
) -> ProfileRecord:
    return ProfileRecord(pid, kind, "cli_marina", 1, privacy_version, handles)


def fact(
    n: int,
    predicate: str,
    value: str,
    category: str,
    *,
    sensitivity: Sensitivity = Sensitivity.NORMAL,
    days_ago: int = 10,
    handle: UUID = PHONE,
) -> Fact:
    at = T0 - timedelta(days=days_ago)
    return Fact(
        fact_id=uid(0x100 + n),
        origin_handle_id=handle,
        predicate=predicate,
        value=value,
        category=category,
        sensitivity=sensitivity,
        speaker=Speaker.CUSTOMER,
        confidence=0.9,
        valid_at=at,
        created_at=at,
        status=FactStatus.ACTIVE,
        last_confirmed_at=at,
        evidence_event_ids=(uid(0x900 + n),),
    )


def episode(
    n: int,
    category: str,
    *,
    days_ago: float,
    outcome: str = "resolved",
    resolution: str | None = None,
    channel: str = "whatsapp",
    summary: str = "cliente relatou problema",
    handle: UUID = PHONE,
) -> Episode:
    start = T0 - timedelta(days=days_ago)
    return Episode(
        episode_id=uid(0x200 + n),
        version=1,
        origin_handle_id=handle,
        session_id=uid(0x300 + n),
        channel=channel,
        source_id=WHATSAPP_SOURCE,
        summary=summary,
        intent="complaint",
        category=category,
        normalized_category=category,
        outcome=outcome,
        started_at=start,
        ended_at=start + timedelta(minutes=9),
        resolution=resolution,
        conversation_id=f"conv-{n}",
    )


def open_item(
    n: int,
    description: str,
    *,
    due_days: int = 1,
    owner: str = "company",
    status: OpenItemStatus = OpenItemStatus.OPEN,
) -> OpenItem:
    return OpenItem(
        item_id=uid(0x400 + n),
        origin_handle_id=PHONE,
        description=description,
        owner=owner,
        status=status,
        created_at=T0 - timedelta(hours=3),
        origin={"event_ids": [str(uid(0x950 + n))]},
        due_at=T0 + timedelta(days=due_days),
    )


def marina(privacy_version: int = 1) -> MemorySnapshot:
    return MemorySnapshot(
        profile=profile(privacy_version),
        attrs=(
            ProfileAttr(PHONE, "preferred_name", "Marina", WHATSAPP_SOURCE, T0 - timedelta(days=300), 4),
            ProfileAttr(WA, "preferred_channel", "WhatsApp", WHATSAPP_SOURCE, T0 - timedelta(days=30), 6),
        ),
        facts=(
            fact(1, "disputed_invoice", "fatura de agosto", "billing"),
            fact(2, "plan", "fibra 500 mega", "contract"),
            fact(3, "condition", "gestante", "health", sensitivity=Sensitivity.SENSITIVE),
        ),
        episodes=(
            episode(1, "technical_visit", days_ago=0.2, outcome="unresolved"),
            episode(2, "technical_visit", days_ago=20, resolution="visita remarcada"),
            episode(3, "technical_visit", days_ago=60),
            episode(4, "billing", days_ago=190, resolution="crédito na fatura"),
        ),
        open_items=(open_item(1, "Visita técnica remarcada para 23/09, manhã"),),
        actions=(
            AgentAction(
                uid(0x500),
                uid(0x960),
                BILLING_SOURCE,
                "credit",
                ActionStatus.CONFIRMED,
                T0 - timedelta(minutes=54),
                result="Crédito de R$ 40 na fatura de agosto",
            ),
        ),
        traits=(
            Trait(
                uid(0x600),
                PROFILE,
                1,
                "recurring_complaint",
                "technical_visit",
                "volatile",
                "r1",
                T0 - timedelta(days=60),
                T0 - timedelta(hours=4),
                T0 + timedelta(days=30),
                0.9,
            ),
        ),
        objects=(
            BusinessObject(
                uid(0x700),
                ObjectKey("invoice", "erp", "0823"),
                {"status": "open", "amount": 129.9},
                T0 - timedelta(days=2),
                BILLING_SOURCE,
                PHONE,
            ),
        ),
        summaries=(("short", "Cliente desde 2021, fibra, reclamações de visita técnica."),),
    )


def settings(**overrides: object) -> CompileSettings:
    config = sample_config()
    values: dict[str, object] = {
        "policy": config.policy,
        "timezone": config.settings.timezone,
        "locale": config.settings.locale,
        "b0_text": config.settings.b0_text,
        "source_names": {BILLING_SOURCE: "Cobrança"},
        "salt_seed": str(config.space_id),
    } | overrides
    return CompileSettings(**values)  # type: ignore[arg-type]
