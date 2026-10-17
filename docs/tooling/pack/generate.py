"""The Context Pack examples of the documentation and of the site, from the server's own compiler.

Runs inside the server's environment (../server), as `npm run sync` does:

    uv run --directory "$NIADRA_SERVER_DIR" python tooling/pack/generate.py > tooling/pack/examples.json

It builds one synthetic customer, Marina, the way a cell holds her memory at 14:07 on 22/09/2026,
the minute her call reaches the voice agent: a call on 12/03 about a technician who never came,
settled with a credit; a WhatsApp message at 14:02 today about the same failure; the billing
agent's credit at 14:06, which the system confirmed; and the visit the company promised to
reschedule. The space's configuration is the starter one a new space gets, with three sources.
The voice agent's read is compiled with the functions the read path uses (`reader_variant`,
`compile_settings`, `compile_pack`), once for a space in English and once for a space in
Portuguese. Nothing here is written by hand into the pack: the pages and the site show the output.
"""

import json
import subprocess
import sys
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

from niadra.app.compile import compile_settings, reader_variant
from niadra.config.models import SourceConfig, SpaceConfig, TenantSettings
from niadra.domain.compile import COMPILER_VERSION, CompileInput, MemorySnapshot, compile_pack
from niadra.domain.compile.tokens import estimate_tokens
from niadra.domain.records import (
    AgentAction,
    Episode,
    Fact,
    OpenItem,
    ProfileAttr,
    ProfileRecord,
)
from niadra.domain.vocabulary import (
    ActionStatus,
    Audience,
    FactStatus,
    OpenItemStatus,
    Sensitivity,
    Speaker,
    SubjectKind,
    Verification,
)


def uid(n: int) -> UUID:
    return UUID(f"0192f5a0-0000-7000-8000-{n:012x}")


SPACE, TENANT = uid(0x1), uid(0x2)
VOICE, WHATSAPP, BILLING = uid(0x11), uid(0x12), uid(0x13)
PROFILE, PHONE, WA, CRM = uid(0x21), uid(0x22), uid(0x23), uid(0x24)
# Marina's record id in the company's customer base (system_id 48213, scope crm), sent on every event as the
# home page's test does (tests/unit/flow/test_voice_first_read.py): her number was silent for more than 180
# days after the March call, so without the record id the WhatsApp of September would be a possibly recycled
# number and March would stay apart (identity, 5.3). With it, the three handles are one profile.
READ_AT = datetime(2026, 9, 22, 17, 7, tzinfo=UTC)  # 14:07 in São Paulo, when Marina calls


@dataclass(frozen=True)
class Words:
    """What a person or a system said, in the space's language: the memory holds it as it was said."""

    locale: str
    timezone: str
    billing: str
    plan: str
    dispute: str
    march: str
    march_solution: str
    today: str
    credit: str
    pending: str


LANGUAGES = {
    "en": Words(
        locale="en-US",
        timezone="America/Sao_Paulo",
        billing="Billing",
        plan="Family plan",
        dispute="The August bill was charged twice",
        march="The technician visit did not happen",
        march_solution="$40 credit on the bill",
        today="The technician visit promised for this morning did not happen",
        credit="$40 credit on the August bill",
        pending="Reschedule the missed technician visit",
    ),
    "pt": Words(
        locale="pt-BR",
        timezone="America/Sao_Paulo",
        billing="Cobrança",
        plan="Plano Família",
        dispute="A fatura de agosto veio cobrada duas vezes",
        march="A visita técnica não aconteceu",
        march_solution="crédito de R$ 40 na fatura",
        today="A visita técnica prometida para hoje de manhã não aconteceu",
        credit="crédito de R$ 40 na fatura de agosto",
        pending="Remarcar a visita técnica que não aconteceu",
    ),
}


def space(words: Words) -> SpaceConfig:
    """A new space: the starter policy and extraction, in the given language, with three sources."""
    return SpaceConfig(
        space_id=SPACE,
        tenant_id=TENANT,
        region="us-east-2",
        environment="production",
        settings=TenantSettings(locale=words.locale, timezone=words.timezone),
        sources=[
            SourceConfig(
                source_id=VOICE,
                name="Voice",
                channel="voice",
                purposes=frozenset({"support"}),
                audience=Audience.CUSTOMER_AGENT,
            ),
            SourceConfig(
                source_id=WHATSAPP,
                name="WhatsApp",
                channel="whatsapp",
                purposes=frozenset({"support"}),
                audience=Audience.CUSTOMER_AGENT,
            ),
            SourceConfig(
                source_id=BILLING,
                name=words.billing,
                channel="erp",
                purposes=frozenset({"billing"}),
                audience=Audience.INTERNAL_AGENT,
                trusted_action_ops=frozenset({"credit"}),
            ),
        ],
    )


def fact(n: int, predicate: str, value: str, category: str, at: datetime, conversation: str | None) -> Fact:
    return Fact(
        fact_id=uid(0x100 + n),
        origin_handle_id=PHONE,
        predicate=predicate,
        value=value,
        category=category,
        sensitivity=Sensitivity.NORMAL,
        speaker=Speaker.CUSTOMER,
        confidence=0.9,
        valid_at=at,
        created_at=at,
        status=FactStatus.ACTIVE,
        last_confirmed_at=at,
        origin_conversation_id=conversation,
        evidence_event_ids=(uid(0x900 + n),),
    )


def episode(
    n: int,
    channel: str,
    source: UUID,
    summary: str,
    outcome: str,
    start: datetime,
    minutes: int,
    resolution: str | None,
) -> Episode:
    return Episode(
        episode_id=uid(0x200 + n),
        version=1,
        origin_handle_id=PHONE if channel == "voice" else WA,
        session_id=uid(0x300 + n),
        channel=channel,
        source_id=source,
        summary=summary,
        intent="complaint",
        category="technical",
        normalized_category="technical",
        outcome=outcome,
        started_at=start,
        ended_at=start + timedelta(minutes=minutes),
        resolution=resolution,
        initiated_by="customer",
        conversation_id=f"conv-{n}",
    )


def marina(words: Words) -> MemorySnapshot:
    march = datetime(2026, 3, 12, 14, 20, tzinfo=UTC)
    whatsapp = datetime(2026, 9, 22, 17, 2, tzinfo=UTC)
    return MemorySnapshot(
        profile=ProfileRecord(PROFILE, SubjectKind.PERSON, "cli_marina", 1, 1, (PHONE, WA, CRM)),
        attrs=(ProfileAttr(PHONE, "preferred_name", "Marina", VOICE, march, 3),),
        # Predicates of the starter extraction schema. Billing needs V2, but the dispute is Marina's own words
        # (a customer turn is its evidence), so the starter policy's mirror rule releases it at V1. Since
        # compiler-1.15 a complaint fact no pinned conversation says waits in the reserve for a turn about its
        # matter, so the voice skeleton below does not print it; it is still hers at V1, and a turn about the
        # bill brings it as a slot.
        facts=(
            fact(1, "product_or_service_used", words.plan, "relationship", march, "conv-1"),
            fact(2, "preferred_channel", "whatsapp", "preferences", whatsapp, "conv-2"),
            fact(3, "billing_issue", words.dispute, "billing", datetime(2026, 9, 20, 13, 0, tzinfo=UTC), None),
        ),
        # The WhatsApp agent ended its conversation at 14:03, and the memory of it was ready by 14:07.
        episodes=(
            episode(1, "voice", VOICE, words.march, "resolved", march, 9, words.march_solution),
            episode(2, "whatsapp", WHATSAPP, words.today, "unresolved", whatsapp, 1, None),
        ),
        open_items=(
            OpenItem(
                item_id=uid(0x400),
                origin_handle_id=WA,
                description=words.pending,
                owner="company",
                status=OpenItemStatus.OPEN,
                created_at=whatsapp,
                origin={"event_ids": [str(uid(0x950))]},
                due_at=datetime(2026, 9, 23, 15, 0, tzinfo=UTC),
            ),
        ),
        actions=(
            AgentAction(
                uid(0x500),
                uid(0x960),
                BILLING,
                "credit",
                ActionStatus.CONFIRMED,
                datetime(2026, 9, 22, 17, 6, tzinfo=UTC),
                result=words.credit,
                purpose="billing",
            ),
        ),
    )


def voice_read(lang: str) -> dict[str, object]:
    words = LANGUAGES[lang]
    config = space(words)
    variant = reader_variant(
        config,
        source_id=VOICE,
        audience=Audience.CUSTOMER_AGENT,
        purposes=frozenset({"support"}),
        view="voice",
        level=Verification.V1,
    )
    pack = compile_pack(CompileInput(marina(words), variant, compile_settings(config)))
    return {
        "view": "voice",
        "verification": "V1",
        "text": pack.text,
        "tokens": estimate_tokens(pack.text),
        "withheld": pack.withheld,
        "as_of": pack.as_of.isoformat() if pack.as_of else None,
        "etag": pack.etag,
        "manifest_hash": pack.manifest_hash,
        "variables": pack.variables,
    }


def server_commit() -> str:
    try:
        out = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, check=True)
        return out.stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def main() -> None:
    examples = {
        "generator": str(Path("tooling/pack/generate.py")),
        "server_commit": server_commit(),
        "compiler_version": COMPILER_VERSION,
        "read_at": READ_AT.isoformat(),
        "packs": {lang: {"voice": voice_read(lang)} for lang in sorted(LANGUAGES)},
    }
    json.dump(examples, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
