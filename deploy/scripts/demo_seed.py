# /// script
# requires-python = ">=3.11"
# dependencies = ["httpx"]
# ///
"""Fills a demo space with synthetic data, so every Console screen has something to show.

    NIADRA_DOMAIN=<domain> uv run scripts/demo_seed.py   (control.api.<domain> and api.<domain>)
    NIADRA_CONTROL_URL=... NIADRA_API_URL=... NIADRA_SECRETS_DIR=<dir> uv run scripts/demo_seed.py
                                                     (a local cell that scripts/local-e2e.sh --keep left running)

What it builds, in Niadra's own tenant (the one `niadra/tenant/bootstrap` names):
- the project "Demonstração: varejo e atendimento" (slug `demo-varejo`), and in its sandbox space every agent
  feature and agent memory turned on through approved configuration diffs (a sandbox's author approves their
  own diff);
- a demo admin, `demo@niadra.com`, whose password and authenticator secret live only in Secrets Manager
  (`niadra/admin/demo-password`); the first run creates it through the bootstrap admin;
- a fictional retailer's two weeks, the way a company's agents and systems send them: 40 customers on WhatsApp,
  voice, e-mail and the app, identity merges, three look-alikes the daily suggester puts up for review; its B2B
  side: three company groups (a holding and two companies each, every one an organization of its own), nine
  companies with three or four people each (some in two companies, some also retail customers), a reseller and a
  marketplace as partners, each with its people, orders and invoices; conversations and turns of four agents (voice
  service, WhatsApp sales, billing, the internal CRM), typed orders, invoices, tickets, quotes and plans, shared
  objects, signals, coordination, agent notes, outcomes, data issues, a legal hold and an erasure.

Every person, company, phone, e-mail and order is invented: phones in the +55 11 90003 range (the look-alikes'
in +55 12, 13 and 14 90003), e-mails under `demo.niadra.com`, which receives no mail (the identity graph refuses to join people by `example.com`). Event and turn times spread over the last 14 days; usage is metered
when the cell receives a call, so the usage screen shows the day of the run.

Re-runnable: idempotency keys, turn ids and object versions are fixed, so a second run adds nothing it already
added. Each run revokes the keys the previous run issued and issues new ones; no key or password is printed.
Reads secrets with the AWS CLI (profile `niadra`, us-east-2; override with NIADRA_PROFILE, NIADRA_REGION).
"""

import base64
import hashlib
import json
import os
import secrets as random
import subprocess
import sys
import time
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx
from sdk_session import Run, bearer, expect, totp

DOMAIN = os.environ.get("NIADRA_DOMAIN", "niadra.com")
CONTROL = os.environ.get("NIADRA_CONTROL_URL", f"https://control.api.{DOMAIN}")
API = os.environ.get("NIADRA_API_URL", f"https://api.{DOMAIN}")
# A local cell (scripts/local-e2e.sh --keep) keeps its secrets in files, one `<name with / as __>.json` each: the
# bootstrap is its bootstrap.json. Never set against production, whose secrets live in Secrets Manager only.
SECRETS_DIR = os.environ.get("NIADRA_SECRETS_DIR")
PROFILE = os.environ.get("NIADRA_PROFILE", "niadra")
REGION = os.environ.get("NIADRA_REGION", "us-east-2")

BOOTSTRAP_SECRET = "niadra/tenant/bootstrap"
DEMO_SECRET = "niadra/admin/demo-password"
DEMO_EMAIL = "demo@niadra.com"
PROJECT = {"name": "Demonstração: varejo e atendimento", "slug": "demo-varejo"}
# A new key, a feature or a document reaches the cell with the control plane's next snapshot.
READY_TIMEOUT_S = 180
STATE_TIMEOUT_S = 180
NOW = datetime.now(UTC).replace(microsecond=0)
# Every id the seed mints derives from this namespace, so a second run sends the same ids.
NS = uuid.UUID("6f1c2d3e-4b5a-4c6d-8e7f-0a1b2c3d4e5f")


def uid(*parts: object) -> str:
    return str(uuid.uuid5(NS, "/".join(map(str, parts))))


def at(days_ago: float, minutes: float = 0) -> str:
    """An instant `days_ago` days before the run, `minutes` later: business hours of a past day."""
    return (NOW - timedelta(days=days_ago) + timedelta(minutes=minutes)).isoformat()


def digest(value: Any) -> str:
    """The turn record's digest: sorted compact JSON is the RFC 8785 form for these values."""
    canonical = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return "sha256:" + hashlib.sha256(canonical.encode()).hexdigest()


def exposure_token(exposure_id: str, position: int) -> str:
    """The exposure token spec's `nx1.<id>.<position>.<verifier>`, as a card carries it to the cart."""
    encoded = base64.urlsafe_b64encode(uuid.UUID(exposure_id).bytes).decode().rstrip("=")
    prefix = f"nx1.{encoded}.{position}"
    verifier = base64.urlsafe_b64encode(hashlib.sha256(prefix.encode()).digest()).decode().rstrip("=")[:4]
    return f"{prefix}.{verifier}"


def b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


# The cast: invented people and companies. Phones in the range the e2e check uses for fiction.

PEOPLE = [
    # name, city, channels they use (w WhatsApp, v voice, e e-mail, a app)
    ("Marina Teixeira", "São Paulo", "wve"),
    ("Rafael Moura", "Campinas", "wa"),
    ("Juliana Prado", "Santos", "we"),
    ("Thiago Albuquerque", "São Paulo", "vw"),
    ("Camila Ventura", "Sorocaba", "wae"),
    ("Bruno Carvalhal", "Ribeirão Preto", "v"),
    ("Larissa Fontes", "São Paulo", "w"),
    ("Diego Sampaio", "Jundiaí", "we"),
    ("Patrícia Lemos", "São José dos Campos", "ve"),
    ("Gustavo Rangel", "São Paulo", "wa"),
    ("Aline Barreto", "Guarulhos", "w"),
    ("Rafael Quintela", "Osasco", "va"),
    ("Renata Siqueira", "São Paulo", "we"),
    ("Eduardo Paiva", "Santo André", "v"),
    ("Bianca Toledo", "Campinas", "wa"),
    ("Leonardo Brandão", "São Paulo", "e"),
    ("Isabela Furtado", "Piracicaba", "wv"),
    ("Mateus Valadares", "Bauru", "w"),
    ("Natália Coimbra", "São Paulo", "wea"),
    ("Rodrigo Esteves", "Mogi das Cruzes", "v"),
    ("Fernanda Arruda", "São Paulo", "w"),
    ("Vinícius Peixoto", "Taubaté", "we"),
    ("Carolina Bastos", "São Paulo", "a"),
    ("André Lacerda", "Limeira", "wv"),
    ("Priscila Nogueira", "São Paulo", "w"),
    ("Henrique Saldanha", "Americana", "e"),
    ("Tatiane Rocha", "Barueri", "wa"),
    ("Lucas Figueiredo", "São Paulo", "v"),
    ("Daniela Mattos", "Indaiatuba", "w"),
    ("Otávio Pimentel", "São Paulo", "we"),
    ("Yasmin Duarte", "Sorocaba", "wa"),
    ("Ricardo Amaral", "São Paulo", "v"),
    ("Sabrina Leal", "Santos", "w"),
    ("Caio Medeiros", "Campinas", "ea"),
    ("Letícia Brito", "São Paulo", "wv"),
    ("Murilo Cardoso", "Itu", "w"),
    ("Gabriela Freitas", "São Paulo", "we"),
    ("Paulo Henrique Sá", "Atibaia", "v"),
    ("Vanessa Guimarães", "São Paulo", "wa"),
    ("Igor Monteiro", "Valinhos", "w"),
]


# The identity graph never joins subjects by a placeholder address (`example.com`, `.example`), so the demo's
# e-mails live under a name of Niadra's own that receives no mail.
MAIL_DOMAIN = "demo.niadra.com"


def slug(name: str) -> str:
    plain = name.lower()
    for a, b in (("á", "a"), ("ã", "a"), ("â", "a"), ("é", "e"), ("ê", "e"), ("í", "i"), ("ó", "o"), ("ô", "o"),
                 ("ú", "u"), ("ç", "c")):  # fmt: skip
        plain = plain.replace(a, b)
    return ".".join(plain.split())


class Customer:
    def __init__(self, n: int, name: str, city: str, channels: str) -> None:
        self.n, self.name, self.city, self.channels = n, name, city, channels
        self.first = name.split()[0]
        self.phone = {"type": "phone_e164", "value": f"+551190003{n:04d}"}
        self.email = {"type": "email", "value": f"{slug(name)}@{MAIL_DOMAIN}"}
        self.app = {"type": "app_user_id", "value": f"app-{4100 + n}", "scope": "app_loja"}
        self.crm = {"type": "system_id", "value": f"CLI-{20500 + n}", "scope": "crm"}

    def handle(self, channel: str) -> dict[str, str]:
        return {"w": self.phone, "v": self.phone, "e": self.email, "a": self.app}[channel]

    @property
    def main(self) -> dict[str, str]:
        return self.handle(self.channels[0])


CUSTOMERS = [Customer(i + 1, *p) for i, p in enumerate(PEOPLE)]
# The customer erased at the end, by their own request.
FORGOTTEN = CUSTOMERS[-1]

# Three company groups, each a holding and two companies, and the people who buy for them at the holding. Each
# company stands alone: Niadra places no organization under another.
GROUPS = [
    {
        "holding": ("Grupo Horizonte Participações", "horizonte.example"),
        "companies": [("Horizonte Hotéis", "hoteis.horizonte.example"), ("Horizonte Coworking", "cowork.horizonte.example")],
        "contacts": [(CUSTOMERS[8], "compras"), (CUSTOMERS[15], "financeiro")],
    },
    {
        "holding": ("Rede Pátio Verde Restaurantes", "patioverde.example"),
        "companies": [("Pátio Verde Campinas", "campinas.patioverde.example"), ("Pátio Verde Santos", "santos.patioverde.example")],
        "contacts": [(CUSTOMERS[25], "compras"), (CUSTOMERS[33], "gerente")],
    },
    {
        "holding": ("Grupo Aurora Educação", "aurora.example"),
        "companies": [("Colégio Aurora Centro", "centro.aurora.example"), ("Colégio Aurora Norte", "norte.aurora.example")],
        "contacts": [(CUSTOMERS[35], "diretoria")],
    },
]  # fmt: skip


def org(domain: str) -> dict[str, str]:
    """A company by its id in the ERP."""
    code = domain.removesuffix(".example").replace(".", "-").upper()
    return {"type": "system_id", "value": f"EMP-{code}", "scope": "erp", "subject_kind": "account"}


def partner(code: str) -> dict[str, str]:
    """A partner company (a reseller, a marketplace) by its id in the ERP."""
    return {"type": "system_id", "value": f"PAR-{code}", "scope": "erp", "subject_kind": "partner"}


class Contact(Customer):
    """Someone who buys or pays for a company: a phone and the company's own e-mail under the demo domain."""

    def __init__(self, n: int, name: str, city: str, host: str) -> None:
        super().__init__(n, name, city, "we")
        self.email = {"type": "email", "value": f"{slug(name)}@{host}.{MAIL_DOMAIN}"}


# The people of the companies and partners, numbered past the retail customers (+55 11 90003 0101 on).
CONTACTS = [Contact(101 + i, *p) for i, p in enumerate([
    ("Adriana Campos", "São Paulo", "hoteis-horizonte"),
    ("Marcelo Tavares", "São Paulo", "hoteis-horizonte"),
    ("Simone Araújo", "São Paulo", "hoteis-horizonte"),
    ("Rogério Lins", "São Paulo", "cowork-horizonte"),
    ("Cláudia Neves", "São Paulo", "cowork-horizonte"),
    ("Fábio Rezende", "Campinas", "patioverde"),
    ("Luciana Prates", "Campinas", "patioverde"),
    ("Márcio Dantas", "Campinas", "patioverde"),
    ("Helena Cunha", "Santos", "patioverde"),
    ("Jorge Batista", "Santos", "patioverde"),
    ("Beatriz Salles", "São Paulo", "aurora"),
    ("Roberto Fonseca", "São Paulo", "aurora"),
    ("Elaine Moraes", "São Paulo", "aurora"),
    ("Vítor Antunes", "São Paulo", "aurora"),
    ("Silvana Teles", "São Paulo", "aurora"),
    ("Alexandre Pires", "São Paulo", "lume"),
    ("Mariana Lopes", "São Paulo", "lume"),
    ("Cristiano Melo", "Osasco", "trigofino"),
    ("Joana Ribeiro", "Osasco", "trigofino"),
    ("Denise Faria", "Santo André", "sorrisopleno"),
    ("Paulo Viana", "Santo André", "sorrisopleno"),
    ("Raquel Moreira", "Santo André", "sorrisopleno"),
    ("Wagner Costa", "São Paulo", "casaviva"),
    ("Fabiana Rocha", "São Paulo", "casaviva"),
    ("Rafaela Mendes", "São Paulo", "vitrine"),
    ("Emerson Lima", "São Paulo", "vitrine"),
])]  # fmt: skip
K = {c.n: c for c in CONTACTS}

# The companies the retailer sells to, each with the people who buy, pay or run it. Some people work for two
# companies, and some retail customers also buy for their employer. A buyer (`compras`, `dono`, `dona`,
# `diretoria`) places the company's orders; every company gets its invoices.
COMPANIES = [
    # (name, domain, members)
    ("Horizonte Hotéis", "hoteis.horizonte.example",
     [(K[101], "compras"), (K[102], "gerente"), (K[103], "financeiro"), (CUSTOMERS[0], "compras")]),
    ("Horizonte Coworking", "cowork.horizonte.example",
     [(K[104], "compras"), (K[105], "gerente"), (CUSTOMERS[21], "recepção")]),
    ("Pátio Verde Campinas", "campinas.patioverde.example",
     [(K[106], "compras"), (K[107], "gerente"), (K[108], "financeiro"), (CUSTOMERS[27], "compras")]),
    ("Pátio Verde Santos", "santos.patioverde.example",
     [(K[109], "compras"), (K[110], "gerente"), (K[108], "financeiro")]),
    ("Colégio Aurora Centro", "centro.aurora.example",
     [(K[112], "compras"), (K[113], "financeiro"), (CUSTOMERS[29], "compras"), (K[111], "diretoria")]),
    ("Colégio Aurora Norte", "norte.aurora.example",
     [(K[114], "compras"), (K[115], "coordenação"), (K[111], "diretoria")]),
    ("Lume Arquitetura", "lume.example",
     [(K[116], "dono"), (K[117], "compras"), (CUSTOMERS[18], "financeiro")]),
    ("Padaria Trigo Fino", "trigofino.example",
     [(K[118], "dono"), (K[119], "compras"), (K[116], "sócio")]),
    ("Clínica Sorriso Pleno", "sorrisopleno.example",
     [(K[120], "dona"), (K[121], "compras"), (K[122], "recepção"), (CUSTOMERS[18], "financeiro")]),
]  # fmt: skip
BUYERS = frozenset({"compras", "dono", "dona", "diretoria"})

# Partners: a reseller that sells the retailer's line in its own stores, and a marketplace that sells it online.
PARTNERS = [
    # (name, code, members)
    ("Revenda Casa Viva", "CASAVIVA", [(K[123], "representante"), (K[124], "gerente de conta"), (CUSTOMERS[30], "revendedora")]),
    ("Marketplace Vitrine", "VITRINE", [(K[125], "gerente de conta"), (K[126], "operações"), (CUSTOMERS[36], "compras")]),
]  # fmt: skip

# Callers who look like a customer, from another area code and another mailbox of the same name: nobody joins
# them, and the daily suggester puts each pair up for review.
LOOK_ALIKES = [(CUSTOMERS[12], "+5512", "loja"), (CUSTOMERS[4], "+5513", "pedidos"), (CUSTOMERS[18], "+5514", "contato")]



# The agents and systems of the space: sources, with the scopes their keys carry.

SOURCES = [
    {"name": "sac-voz", "audience": "customer_agent", "channel": "voice", "vendor": "plataforma-de-voz",
     "purposes": ["support"], "verification_ceiling": "V3",
     "scopes": ["track", "context", "search", "identify", "replay", "coordinate", "agent_memory", "agent_memory:write",
                "agent_state"]},
    {"name": "vendas-whatsapp", "audience": "customer_agent", "channel": "whatsapp", "vendor": "plataforma-whatsapp",
     "purposes": ["sales", "support"], "verification_ceiling": "V2",
     "scopes": ["track", "context", "search", "identify", "replay", "coordinate", "agent_memory", "agent_memory:write",
                "agent_state"]},
    {"name": "cobranca", "audience": "customer_agent", "channel": "email", "vendor": "agente-proprio",
     "purposes": ["billing"], "verification_ceiling": "V2",
     "scopes": ["track", "context", "search", "identify", "replay", "coordinate", "agent_memory", "agent_memory:write"]},
    {"name": "crm-interno", "audience": "internal_agent", "channel": "crm", "vendor": "agente-proprio",
     "purposes": ["support", "sales"], "verification_ceiling": "V2",
     "trusted_action_ops": ["open_ticket", "update_address", "credit"],
     "scopes": ["track", "act", "context", "search", "identify", "coordinate", "agent_memory", "agent_memory:write",
                "state:push"]},
    {"name": "erp", "audience": "internal_agent", "channel": "erp", "vendor": "erp",
     "purposes": ["billing"], "verification_ceiling": "V2", "trusted_action_ops": ["credit"],
     "scopes": ["track", "act", "identify", "state:push"]},
    {"name": "plataforma-loja", "audience": "internal_agent", "channel": "app", "vendor": "plataforma-de-ecommerce",
     "purposes": ["sales"], "verification_ceiling": "V2",
     "scopes": ["track", "identify", "state:push"]},
]  # fmt: skip


# The object types: a retailer's orders, invoices, tickets, plans and quotes, its catalog and price table.

ORDER_TYPE = {
    "type": "order",
    "ownership": "subject",
    "mirror_of": {"system": "erp"},
    "fields": {
        "total": {"type": "money", "role": "order_total", "claim": {"allowed": True, "class": "money", "nature": "observed"}},
        "lines": {"type": "list"},
        "delivery_estimate": {"type": "date", "claim": {"allowed": True, "class": "date", "nature": "observed"},
                              "freshness": {"class": "semi", "max_age": "12h"}},
        "tracking_code": {"type": "string"},
    },
    "sources": {"erp": {"kind": "push", "precedence": 1}},
    "states": ["placed", "paid", "shipped", "delivered", "kept", "returned", "cancelled"],
    "lifecycle": {
        "transitions": [
            {"from": "placed", "to": "paid", "by": ["source"]},
            {"from": "paid", "to": "shipped", "by": ["source"]},
            {"from": "shipped", "to": "delivered", "by": ["source"]},
            {"from": "placed", "to": "delivered", "by": ["source"]},
            {"from": "delivered", "to": "kept", "by": ["source", "clock"]},
            {"from": "delivered", "to": "returned", "by": ["source"]},
            {"from": "*", "to": "cancelled", "by": ["source"]},
        ],
        "outcome": {"provisional": ["delivered"], "final": ["kept", "returned", "cancelled"]},
    },
    "purposes": {"claim": {"prohibitions": ["affirm_delivery_date"]}},
}  # fmt: skip
CATALOG_FINGERPRINT = "sha256:" + hashlib.sha256(b"demo erp invoices catalog, synthetic").hexdigest()
INVOICE_TYPE = {
    "type": "invoice",
    "ownership": "subject",
    "mirror_of": {"system": "postgresql", "derived_by": "introspection", "fingerprint": CATALOG_FINGERPRINT,
                  "drift": "alert"},
    "fields": {
        "amount": {"type": "money", "role": "invoice_amount", "claim": {"allowed": True, "class": "money", "nature": "observed"}},
        "status": {"type": "enum"},
    },
    "sources": {"erp": {"kind": "push", "precedence": 1}},
    "states": ["open", "overdue", "paid", "credited"],
    "values": {"due_date": {"rule": "vencimento@v1"}},
    "timers": {"due_reminder": {"due": "due_date - 48h", "on_fire": ["notify"]}},
}  # fmt: skip
TICKET_TYPE = {
    "type": "ticket",
    "ownership": "subject",
    "mirror_of": {"system": "crm"},
    "fields": {"subject": {"type": "string"}, "priority": {"type": "enum"}, "channel": {"type": "enum"}},
    "sources": {"crm_interno": {"kind": "push", "precedence": 1}},
    "states": ["open", "in_progress", "waiting_customer", "resolved", "closed"],
    "lifecycle": {
        "transitions": [
            {"from": "open", "to": "in_progress", "by": ["source"]},
            {"from": "in_progress", "to": "waiting_customer", "by": ["source"]},
            {"from": "waiting_customer", "to": "in_progress", "by": ["source"]},
            {"from": "in_progress", "to": "resolved", "by": ["source"]},
            {"from": "open", "to": "resolved", "by": ["source"]},
            {"from": "resolved", "to": "closed", "by": ["source", "clock"]},
        ],
        "outcome": {"final": ["resolved", "closed"]},
    },
    "values": {"sla_ends": {"rule": "sla_atendimento@v2"}},
    "timers": {"sla_warning": {"due": "sla_ends - 2h", "on_fire": ["notify"]}},
}  # fmt: skip
PLAN_TYPE = {
    "type": "plan",
    "ownership": "shared",
    "mirror_of": {"system": "plataforma_loja"},
    "working_set": {"enter_on": ["presented", "related"], "leave_after": "30d"},
    "fields": {
        "name": {"type": "string"},
        "price_table": {"type": "money", "role": "price_monthly", "claim": {"allowed": True, "class": "money"},
                        "track_changes": True},
    },
    "values": {"promo_ends": {"rule": "promo@v1"}},
    "sources": {"plataforma_loja": {"kind": "push", "precedence": 1}},
    "timers": {"promo_review": {"due": "promo_ends - 1h", "on_fire": ["notify"]}},
}  # fmt: skip
QUOTE_TYPE = {
    "type": "quote",
    "ownership": "subject",
    "nature": "derived",
    "mirror_of": {"system": "plataforma_loja"},
    "inputs": ["plan.price_table"],
    "fields": {"price_full": {"type": "money", "role": "price_full", "claim": {"allowed": True, "class": "money"}}},
    "sources": {"plataforma_loja": {"kind": "push", "precedence": 1}},
    "purposes": {"claim": {"prohibitions": ["affirm_price"]}},
}  # fmt: skip
ITEM_TYPE = {
    "type": "item_variant",
    "ownership": "shared",
    "mirror_of": {"system": "plataforma_loja"},
    "working_set": {"enter_on": ["presented", "engaged", "related"], "leave_after": "30d"},
    "fields": {
        "name": {"type": "string"},
        "available": {"type": "bool"},
        "color": {"type": "enum"},
        "price_sale": {"type": "money", "role": "price_sale",
                       "claim": {"allowed": True, "class": "money", "nature": "observed"},
                       "freshness": {"class": "price", "max_age": "1d", "claim_max_age": "1h"}, "track_changes": True},
    },
    "sources": {
        "plataforma_loja": {"kind": "push", "precedence": 1},
        "buscar_produtos": {"kind": "tool_observation", "precedence": 2},
    },
}  # fmt: skip
LOOK_TYPE = {
    "type": "look",
    "ownership": "shared",
    "mirror_of": {"system": "plataforma_loja"},
    "working_set": {"enter_on": ["presented"], "leave_after": "30d"},
    "fields": {"name": {"type": "string"}, "pieces": {"type": "list"}},
    "relations": {"pieces": {"type": "item_variant", "via": "pieces[*].id", "many": True, "status_field": "status"}},
    "derived_fields": {
        "all_pieces_available": {"agg": "all", "over": "pieces", "where": "available == yes"},
        "pieces_count": {"agg": "count", "over": "pieces"},
    },
    "sources": {"plataforma_loja": {"kind": "push", "precedence": 1}},
}  # fmt: skip
ASSISTANT_STATE_TYPE = {
    "type": "shopping_assistant_state",
    "ownership": "agent",
    "agent_state": {"scope": "conversation", "write": "merge_by_key"},
    "fields": {"step": {"type": "enum"}, "shortlist": {"type": "list"}, "size_hint": {"type": "string"}},
}  # fmt: skip
TYPES = [ORDER_TYPE, INVOICE_TYPE, TICKET_TYPE, PLAN_TYPE, QUOTE_TYPE, ITEM_TYPE, LOOK_TYPE, ASSISTANT_STATE_TYPE]

# The catalog the WhatsApp sales agent shows: name, color, price.
ITEMS = [
    ("vestido-midi-linho", "Vestido midi de linho", "bege", 289.90),
    ("vestido-midi-linho-azul", "Vestido midi de linho", "azul", 289.90),
    ("camisa-oxford", "Camisa oxford slim", "branco", 159.90),
    ("calca-alfaiataria", "Calça de alfaiataria", "preto", 219.90),
    ("tenis-couro", "Tênis de couro casual", "branco", 349.90),
    ("jaqueta-jeans", "Jaqueta jeans oversized", "azul", 279.90),
    ("jogo-cama-percal", "Jogo de cama percal 200 fios", "cinza", 399.90),
    ("toalha-banho", "Toalha de banho fio penteado", "verde", 89.90),
    ("luminaria-mesa", "Luminária de mesa articulada", "preto", 189.90),
    ("manta-trico", "Manta de tricô", "terracota", 149.90),
    ("bolsa-palha", "Bolsa de palha", "natural", 199.90),
    ("sandalia-rasteira", "Sandália rasteira", "caramelo", 129.90),
]
PLANS = [("clube-essencial", "Clube Mirante Essencial", 29.90), ("clube-plus", "Clube Mirante Plus", 59.90)]
LOOKS = [("look-verao", "Look verão", ["vestido-midi-linho", "bolsa-palha", "sandalia-rasteira"]),
         ("look-trabalho", "Look trabalho", ["camisa-oxford", "calca-alfaiataria", "tenis-couro"])]  # fmt: skip


def ref(type_: str, namespace: str, id_: str) -> dict[str, str]:
    return {"type": type_, "namespace": namespace, "id": id_}


def ref_text(r: dict[str, str]) -> str:
    return f"{r['type']}:{r['namespace']}:{r['id']}"


def item_ref(sku: str) -> dict[str, str]:
    return ref("item_variant", "loja", sku)


def plan_ref(code: str) -> dict[str, str]:
    return ref("plan", "loja", code)


MEASUREMENT = {
    # The revenue definition signed with the retailer: net of discounts, coupons spread by line, no freight or tax.
    "revenue": {"currency": "BRL", "basis": "net_of_discount", "coupons": "prorated_by_line", "shipping": "excluded",
                "taxes": "excluded", "cancellations": "by_line"},
    "outcomes": [{"name": "compra", "object_type": "order", "success_states": ["delivered", "kept"], "lines": {},
                  "final_after": "7d"}],
    # A holdout on the constraints block, and what each agent cost per turn before it had memory.
    "experiments": [{"name": "restricoes_na_busca", "kind": "element", "element": "constraints",
                     "arms": [{"name": "control", "fraction": 0.2}, {"name": "com_restricoes", "fraction": 0.8}],
                     "salt": "demo-restricoes-2026", "starts_at": "2026-09-17T00:00:00Z"}],
    "memory_off_costs": [
        {"agent": "sac-voz", "cost_per_turn_usd": 0.0019, "measured": "Duas semanas de agosto, sem a memória"},
        {"agent": "vendas-whatsapp", "cost_per_turn_usd": 0.0016, "measured": "Duas semanas de agosto, sem a memória"},
    ],
}  # fmt: skip
PURPOSES = {
    "version": "1",
    "purposes": [
        {"name": "support", "legal_basis": "contract", "retention_days": 730, "description": "Atendimento de pedidos, trocas e entregas"},
        {"name": "sales", "legal_basis": "legitimate_interest", "retention_days": 365, "description": "Venda assistida e recomendação"},
        {"name": "billing", "legal_basis": "legal_obligation", "retention_days": 1825, "description": "Faturas, cobrança e créditos"},
        {"name": "marketing", "legal_basis": "consent", "retention_days": 180, "description": "Ofertas do Clube Mirante"},
    ],
    "event_types": {"invoice.*": ["billing"], "order.*": ["support"]},
    "default": ["support"],
}  # fmt: skip
GATEWAY_ID = "disparo_marketing"
GATEWAY_KEY = hashlib.sha256(b"demo gateway key, synthetic").digest()
NETWORK_OPT_OUT = {"purpose": "marketing", "reason": "opt_out"}
HANDOFF_OUTCOMES = [
    {"name": "sem_interesse", "suppress": [NETWORK_OPT_OUT]},
    {"name": "venda_fechada"},
    {"name": "resolvido"},
]
AGENT_NOTES = {
    "cobranca": [
        ("procedure", "Segunda via de boleto", "Antes de gerar a segunda via, confirme se o boleto original já venceu; "
         "boleto vencido há mais de 30 dias é reemitido pelo ERP com juros, nunca pelo agente.", ["boleto", "erp"]),
        ("tool_note", "Crédito na fatura", "Crédito de frete cobrado em duplicidade sai pela operação credit do ERP; "
         "depois de lançar, chame refresh_invoice para a fatura mostrar o valor novo.", ["credito", "fatura"]),
        ("pitfall", "Pix agendado", "Pagamento por Pix agendado só aparece no ERP no dia seguinte: não trate como "
         "atraso antes das 10h do dia útil seguinte.", ["pix"]),
    ],
    "sac-voz": [
        ("procedure", "Troca por tamanho", "Troca por tamanho não precisa de foto; avaria precisa de foto da embalagem "
         "e do produto antes de abrir a coleta.", ["troca"]),
        ("pitfall", "Transportadora no feriado", "Em semana de feriado a coleta atrasa até dois dias úteis: avise no "
         "início da ligação, não no fim.", ["entrega", "feriado"]),
    ],
    "vendas-whatsapp": [
        ("procedure", "Medidas do vestido midi", "O vestido midi de linho veste pequeno: sugira um número acima "
         "quando a pessoa estiver entre dois tamanhos.", ["tamanho", "vestido"]),
        ("pitfall", "Cupom de primeira compra", "O cupom de primeira compra não vale para itens do Clube; diga isso "
         "antes de montar o carrinho.", ["cupom", "clube"]),
    ],
    "crm-interno": [
        ("process_note", "Endereço de entrega", "Mudança de endereço depois do faturamento vira chamado para a "
         "logística; antes do faturamento, update_address resolve.", ["endereco", "chamado"]),
    ],
}  # fmt: skip
TRAIT_RULES = [
    {"rule_id": "faturas_em_atraso", "catalog": "overdue_invoices", "version": "1",
     "params": {"event_type": "invoice.overdue", "min_count": 2, "window_days": 30}},
    {"rule_id": "nao_atende_ligacoes", "catalog": "does_not_answer_calls", "version": "1",
     "params": {"min_attempts": 3, "window_days": 30, "event_types": ["call.unanswered"], "voice_channels": ["voice"]}},
    {"rule_id": "reclamacao_recorrente", "catalog": "recurring_complaint", "version": "1", "params": {}},
    {"rule_id": "pediu_cancelamento", "catalog": "asked_to_cancel", "version": "1", "params": {}},
]  # fmt: skip
ENDPOINT_ID = "erp-avisos"
SIEM_ID = "siem-recibos"


def aws(*args: str, stdin: str | None = None) -> subprocess.CompletedProcess[str]:
    command = ["aws", "--profile", PROFILE, "--region", REGION, *args]
    return subprocess.run(command, input=stdin, capture_output=True, text=True, check=False)


def secret_file(name: str) -> str:
    assert SECRETS_DIR is not None
    return os.path.join(SECRETS_DIR, name.replace("/", "__") + ".json")


def read_secret(name: str) -> dict[str, Any] | None:
    if SECRETS_DIR is not None:
        if not os.path.exists(secret_file(name)):
            return None
        with open(secret_file(name)) as f:
            return dict(json.load(f))
    done = aws("secretsmanager", "get-secret-value", "--secret-id", name, "--query", "SecretString", "--output", "text")
    if done.returncode != 0:
        if "ResourceNotFoundException" in done.stderr:
            return None
        raise RuntimeError(f"reading {name}: {done.stderr.strip()[:200]}")
    return dict(json.loads(done.stdout))


def write_secret(name: str, value: dict[str, Any], description: str) -> None:
    """Values travel on the CLI's standard input, never in its arguments or output."""
    body = json.dumps(value)
    if SECRETS_DIR is not None:
        with open(secret_file(name), "w") as f:
            f.write(body)
        os.chmod(secret_file(name), 0o600)
        return
    if read_secret(name) is None:
        done = aws("secretsmanager", "create-secret", "--name", name, "--description", description,
                   "--secret-string", "file:///dev/stdin", stdin=body)  # fmt: skip
    else:
        done = aws("secretsmanager", "put-secret-value", "--secret-id", name, "--secret-string", "file:///dev/stdin",
                   stdin=body)  # fmt: skip
    if done.returncode != 0:
        raise RuntimeError(f"writing {name}: {done.stderr.strip()[:200]}")


def retried(send: Callable[[], httpx.Response]) -> httpx.Response:
    """A call sent again while the cell or the control plane is busy or restarting (a deploy): 503 and 429 wait
    for their Retry-After, a dropped connection a few seconds."""
    deadline = time.monotonic() + READY_TIMEOUT_S
    while True:
        try:
            response = send()
        except httpx.TransportError:
            if time.monotonic() > deadline:
                raise
            time.sleep(5)
            continue
        if response.status_code not in (502, 503, 504, 429) or time.monotonic() > deadline:
            return response
        time.sleep(min(max(float(response.headers.get("retry-after", "3")), 1.0), 30.0))


class Person:
    """A person of the tenant on the control plane."""

    def __init__(self, email: str, password: str, totp_secret: str | None) -> None:
        self.email, self._password, self._totp = email, password, totp_secret
        self.control = httpx.Client(base_url=CONTROL, timeout=60)
        self.headers: dict[str, str] = {}

    def login(self, space_id: str | None = None) -> httpx.Response:
        body: dict[str, Any] = {"email": self.email, "password": self._password}
        if space_id:
            body["space_id"] = space_id
        if self._totp:
            body["totp"] = totp(self._totp)
        response = retried(lambda: self.control.post("/v1/auth/login", json=body))
        if response.status_code == 401 and self._totp and "second factor" in response.text:
            # The control plane takes each code once: wait for the next one.
            time.sleep(30 - time.time() % 30 + 0.5)
            body["totp"] = totp(self._totp)
            response = retried(lambda: self.control.post("/v1/auth/login", json=body))
        if response.status_code == 200:
            self.headers = bearer(response.json()["access_token"])
        return response

    def to_space(self, space_id: str) -> None:
        sent = retried(lambda: self.control.post("/v1/auth/token", json={"space_id": space_id}, headers=self.headers))
        token = expect(sent, 200)
        self.headers = bearer(token["access_token"])

    def get(self, path: str, **params: Any) -> Any:
        return expect(retried(lambda: self.control.get(path, params=params, headers=self.headers)), 200)

    def post(self, path: str, body: Any, *statuses: int) -> Any:
        sent = retried(lambda: self.control.post(path, json=body, headers=self.headers))
        return expect(sent, *(statuses or (200, 201)))


def demo_admin() -> Person:
    """The demo admin, created through the bootstrap admin when it does not exist or its secret does not open it.
    Its password and authenticator secret go only to Secrets Manager."""
    stored = read_secret(DEMO_SECRET)
    if stored:
        person = Person(stored["email"], stored["password"], stored.get("totp_secret"))
        if person.login().status_code == 200:
            return person
    boot = read_secret(BOOTSTRAP_SECRET)
    if boot is None:
        raise RuntimeError(f"{BOOTSTRAP_SECRET} is missing: the cell's tenant job has not run")
    admin = Person(boot["admin_email"], boot["password"], boot.get("totp_secret"))
    expect(admin.login(boot["space_id"]), 200)
    password = random.token_urlsafe(24)
    existing = next((u for u in admin.get("/v1/users") if u["email"] == DEMO_EMAIL), None)
    if existing is None:
        body = {"email": DEMO_EMAIL, "password": password, "roles": [{"role": "admin"}], "with_totp": True}
        created = admin.post("/v1/users", body, 201)
        secret = parse_qs(urlparse(created["totp_uri"]).query)["secret"][0]
    else:
        # The account exists but the stored secret does not open it: a new password through its own invitation
        # link, and a new authenticator, enrolled at the next sign-in.
        user = existing["user_id"]
        token = admin.post(f"/v1/users/{user}/invitation", {}, 201)["invitation"]["token"]
        expect(admin.control.post("/v1/auth/invitation", json={"token": token, "password": password}), 200)
        if existing.get("second_factor_confirmed_at"):
            admin.post(f"/v1/users/{user}/second-factor/reset", {"reason": "demo: the stored secret was lost"})
        enrolled = admin.control.post("/v1/auth/second-factor", json={"email": DEMO_EMAIL, "password": password})
        secret = expect(enrolled, 200)["secret"]
    write_secret(
        DEMO_SECRET,
        {"email": DEMO_EMAIL, "password": password, "totp_secret": secret},
        "Console login of demo@niadra.com, the demo space's admin (scripts/demo_seed.py)",
    )
    person = Person(DEMO_EMAIL, password, secret)
    expect(person.login(), 200)
    return person


class Demo:
    """The demo space: its admin on the control plane, its cell, the keys of its sources."""

    def __init__(self, person: Person, space_id: str, project: dict[str, Any]) -> None:
        self.person, self.space, self.project = person, space_id, project
        self.api = httpx.Client(base_url=API, timeout=60)
        self.keys: dict[str, dict[str, str]] = {}
        self.source_ids: dict[str, str] = {}

    @property
    def admin(self) -> dict[str, str]:
        return self.person.headers

    # Configuration

    def document(self, kind: str) -> dict[str, Any]:
        return dict(self.person.get(f"/v1/config/{kind}", space_id=self.space)["document"])

    def approve(self, kind: str, document: Any, reason: str) -> None:
        """`document` as the space's `kind`: in a sandbox the author approves their own diff."""
        diff = {"space_id": self.space, "type": kind, "document": document, "reason": f"demo: {reason}"}
        created = self.person.post("/v1/config/diffs", diff, 201)
        self.person.post(f"/v1/config/diffs/{created['diff_id']}/approve", {}, 200)

    def declare(self, kind: str, update: Callable[[Any], Any], reason: str) -> bool:
        """The document with `update` applied, approved unless it already says so."""
        params = {"space_id": self.space}
        current = self.person.get(f"/v1/config/{kind}", **params)["document"]
        wanted = update(json.loads(json.dumps(current)))
        if covers(current, wanted):
            return False
        self.approve(kind, wanted, reason)
        return True

    # The cell

    def until(self, what: str, probe: Callable[[], Any], timeout: float = STATE_TIMEOUT_S) -> Any:
        deadline = time.monotonic() + timeout
        while not (found := probe()):
            if time.monotonic() > deadline:
                raise AssertionError(f"{what} did not happen in {timeout:.0f} s")
            time.sleep(3)
        return found

    def call(self, method: str, path: str, who: str | None, body: Any = None, *statuses: int,
             once: str | None = None, params: dict[str, Any] | None = None) -> Any:  # fmt: skip
        """A call as the source `who`, or as the admin when None; a 503 waits for its Retry-After."""
        headers = dict(self.keys[who] if who else self.admin)
        if once:
            headers["idempotency-key"] = once
        response = retried(lambda: self.api.request(method, path, json=body, params=params, headers=headers))
        return expect(response, *(statuses or (200, 201)))

    def batch(self, who: str, items: list[dict[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {"accepted": 0, "duplicates": 0, "errors": []}
        for start in range(0, len(items), 200):
            body = self.call("POST", "/v1/batch", who, {"items": items[start : start + 200]}, 200, 207)
            result["accepted"] += body["accepted"]
            result["duplicates"] += body["duplicates"]
            result["errors"] += [{**e, "index": e["index"] + start} for e in body["errors"]]
        return result

    def turns(self, who: str, turns: list[dict[str, Any]]) -> dict[str, Any]:
        result = {"accepted": 0, "duplicates": 0, "errors": []}
        for start in range(0, len(turns), 50):
            body = self.call("POST", "/v1/turns", who, {"turns": turns[start : start + 50]}, 200)
            for k in ("accepted", "duplicates"):
                result[k] += body[k]
            result["errors"] += body.get("errors") or []
        return result


def covers(stored: Any, wanted: Any) -> bool:
    """Whether a document as the control plane keeps it, every default filled in, says what `wanted` says."""
    if isinstance(wanted, dict):
        return isinstance(stored, dict) and all(k in stored and covers(stored[k], v) for k, v in wanted.items())
    if isinstance(wanted, list) and all(isinstance(v, str | int | float) for v in wanted):
        return isinstance(stored, list) and sorted(map(str, stored)) == sorted(map(str, wanted))
    if isinstance(wanted, list):
        return isinstance(stored, list) and len(stored) == len(wanted) and all(map(covers, stored, wanted))
    return bool(stored == wanted)


def typed(document: dict[str, Any], *declared: dict[str, Any]) -> dict[str, Any]:
    types = document.get("types") or []
    names = {t["type"] for t in declared}
    return {**document, "types": [*(t for t in types if t.get("type") not in names), *declared]}


# Conversations: what each agent hears and says, by scenario. `{first}`, `{order}`, `{city}` and `{day}` are filled
# per customer. C is the customer, A the agent.

SCENARIOS: dict[str, dict[str, Any]] = {
    "vestido_festa": {
        "agent": "vendas-whatsapp", "channel": "whatsapp", "purpose": "sales",
        "lines": [
            ("C", "Oi! Vou num casamento em {city} daqui a três semanas e queria um vestido midi."),
            ("A", "Oi, {first}! Tenho o vestido midi de linho em bege e em azul, R$ 289,90. Qual tamanho você usa?"),
            ("C", "Uso 40. Prefiro cores claras, nada de preto, por favor."),
            ("A", "Anotado: tons claros e nada de preto. O bege no 40 está disponível. Quer que eu separe?"),
            ("C", "Quero sim, pode fechar no cartão."),
            ("A", "Pedido {order} criado. A entrega em {city} sai em até 5 dias úteis."),
        ],
    },
    "clube_plus": {
        "agent": "vendas-whatsapp", "channel": "whatsapp", "purpose": "sales",
        "lines": [
            ("C", "Quanto custa o Clube Mirante Plus? Vi no Instagram."),
            ("A", "O Plus está R$ 59,90 por mês, com frete grátis e 10% em toda a loja."),
            ("C", "Vou pensar. Me chama de novo só no fim do mês, agora estou sem tempo."),
            ("A", "Combinado, {first}. Te procuro no fim do mês."),
        ],
    },
    "troca_tenis": {
        "agent": "vendas-whatsapp", "channel": "app", "purpose": "support",
        "lines": [
            ("C", "Comprei o tênis de couro no pedido {order} e ficou pequeno. Dá para trocar por um número maior?"),
            ("A", "Dá sim. A troca por tamanho é grátis em até 30 dias. Qual número você quer?"),
            ("C", "O 42. E preciso que chegue antes de sábado."),
            ("A", "Abri a troca do pedido {order} para o 42. A coleta passa amanhã e o novo par chega até sexta."),
        ],
    },
    "pedido_atrasado": {
        "agent": "sac-voz", "channel": "voice", "purpose": "support",
        "lines": [
            ("C", "Meu pedido {order} era para ter chegado anteontem e até agora nada."),
            ("A", "Sinto muito, {first}. Vejo que o pedido saiu da transportadora com atraso por causa do feriado."),
            ("C", "É a terceira vez que isso acontece comigo. Estou bem chateada."),
            ("A", "Entendo. Vou acionar a transportadora e te retorno até sexta com a data certa."),
            ("C", "Tá bom, mas se não chegar até sábado eu quero cancelar."),
        ],
    },
    "luminaria_quebrada": {
        "agent": "sac-voz", "channel": "voice", "purpose": "support",
        "lines": [
            ("C", "A luminária do pedido {order} chegou com a haste quebrada."),
            ("A", "Que chato, {first}. Para a troca por avaria preciso de uma foto da embalagem e do produto."),
            ("C", "Mando pelo WhatsApp hoje à noite."),
            ("A", "Perfeito. Assim que a foto chegar, abro a coleta e a nova luminária sai em até 3 dias úteis."),
        ],
    },
    "cancelar_clube": {
        "agent": "sac-voz", "channel": "voice", "purpose": "support",
        "lines": [
            ("C", "Quero cancelar minha assinatura do Clube Mirante, não estou usando."),
            ("A", "Posso cancelar agora, {first}. Antes, quer saber do plano Essencial, que custa R$ 29,90?"),
            ("C", "Não, obrigado. Pode cancelar mesmo."),
            ("A", "Cancelamento registrado. A cobrança deste mês já não vem."),
        ],
    },
    "segunda_via": {
        "agent": "cobranca", "channel": "email", "purpose": "billing",
        "lines": [
            ("C", "Bom dia. O boleto da fatura {invoice} venceu e eu não consegui pagar. Podem mandar outro?"),
            ("A", "Bom dia, {first}. Envio a segunda via com vencimento para {day}. O valor continua R$ {amount}."),
            ("C", "Obrigada. Pago até {day} sem falta."),
        ],
    },
    "frete_duplicado": {
        "agent": "cobranca", "channel": "email", "purpose": "billing",
        "lines": [
            ("C", "Olá, o frete do pedido {order} foi cobrado duas vezes na fatura {invoice}."),
            ("A", "Olá, {first}. Confirmei a cobrança em duplicidade e lancei o crédito de R$ 24,90 na fatura."),
            ("C", "Ótimo, obrigado pela rapidez."),
        ],
    },
    "prazo_app": {
        "agent": "vendas-whatsapp", "channel": "app", "purpose": "support",
        "lines": [
            ("C", "Qual o prazo de entrega do jogo de cama para {city}?"),
            ("A", "Para {city}, o jogo de cama percal chega em 3 a 4 dias úteis depois do pagamento."),
            ("C", "Ótimo, vou fechar pelo app mesmo."),
        ],
    },
    "endereco_novo": {
        "agent": "vendas-whatsapp", "channel": "whatsapp", "purpose": "support",
        "lines": [
            ("C", "Mudei de endereço, agora moro em {city}. Dá para atualizar antes do pedido {order} sair?"),
            ("A", "Dá sim, {first}. Passei para o time atualizar o endereço; o pedido ainda não foi faturado."),
            ("C", "Valeu! E prefiro receber à tarde, de manhã não tem ninguém em casa."),
            ("A", "Anotado: entregas à tarde."),
        ],
    },
}  # fmt: skip

# Who talks about what: each customer's conversations, by scenario and days before the run.
ASSIGNMENT: list[tuple[int, str, float]] = []
_plan = {
    "w": ["vestido_festa", "clube_plus", "endereco_novo"],
    "v": ["pedido_atrasado", "luminaria_quebrada", "cancelar_clube"],
    "e": ["segunda_via", "frete_duplicado"],
    "a": ["troca_tenis", "prazo_app"],
}
for _c in CUSTOMERS:
    for _k, _channel in enumerate(_c.channels):
        _options = _plan[_channel]
        ASSIGNMENT.append((_c.n, _options[(_c.n + _k) % len(_options)], 13.5 - ((_c.n * 7 + _k * 5) % 14)))


def order_id(n: int, k: int = 0) -> str:
    return f"PED-{70000 + n * 10 + k}"


def invoice_id(n: int, k: int = 0) -> str:
    return f"FAT-{88000 + n * 10 + k}"


def conversation_id(n: int, scenario: str) -> str:
    thread = "-email" if SCENARIOS[scenario]["channel"] == "email" else ""
    return f"demo-{scenario.replace('_', '-')}-{n:02d}{thread}"


MODEL = "gpt-4.1-mini"


def conversation_items(c: Customer, scenario: str, days_ago: float) -> list[dict[str, Any]]:
    """The messages of one conversation, as the agent's SDK sends them, and its end."""
    spec = SCENARIOS[scenario]
    conv = conversation_id(c.n, scenario)
    handle = c.email if spec["channel"] == "email" else c.app if spec["channel"] == "app" else c.phone
    due = (NOW + timedelta(days=3)).date().strftime("%d/%m")
    fill = {"first": c.first, "city": c.city, "order": order_id(c.n), "invoice": invoice_id(c.n), "day": due,
            "amount": f"{189.9 + c.n * 7:.2f}".replace(".", ",")}  # fmt: skip
    items: list[dict[str, Any]] = []
    for i, (who, text) in enumerate(spec["lines"]):
        agent = who == "A"
        item: dict[str, Any] = {
            "type": "event",
            "idempotency_key": f"{conv}-{i + 1}",
            "channel": spec["channel"],
            "conversation_id": conv,
            "handles": [handle],
            "speaker": {"role": "ai_agent", "id": spec["agent"]} if agent else {"role": "customer"},
            "direction": "outbound" if agent else "inbound",
            "content": {"type": "text", "text": text.format(**fill)},
            "occurred_at": at(days_ago, i * 2),
        }
        if agent:
            prompt = 2400 + 37 * i + c.n
            item["usage"] = {"provider": "openai", "model": MODEL, "prompt_tokens": prompt,
                             "cached_tokens": prompt * (2 + i % 3) // 5}  # fmt: skip
        if spec["channel"] == "voice":
            item["voice"] = {"ani": c.phone["value"], "network_attestation": "A", "turn_offset_ms": i * 9000}
        items.append(item)
    items.append({"type": "conversation.ended", "idempotency_key": f"{conv}-end", "conversation_id": conv,
                  "occurred_at": at(days_ago, len(spec["lines"]) * 2 + 1)})  # fmt: skip
    return items


def span(text: str, part: str) -> list[int]:
    start = text.index(part)
    return [start, start + len(part)]


def blob(value: Any) -> dict[str, Any]:
    return {"sha256": digest(value), "content": value}


def scenario_turns(c: Customer, scenario: str, days_ago: float) -> list[dict[str, Any]]:
    """One turn per agent message of the conversation: the tool it called, the model call, what it showed and
    claimed. Claims carry the verdicts the SDK's checker gave them; most are backed, a few flagged, some hedged."""
    spec = SCENARIOS[scenario]
    conv = conversation_id(c.n, scenario)
    items = conversation_items(c, scenario, days_ago)
    said = [i for i in items if i.get("speaker", {}).get("role") == "ai_agent"]
    agent = spec["agent"]
    turns = []
    for k, message in enumerate(said):
        text = message["content"]["text"]
        index = int(message["idempotency_key"].rsplit("-", 1)[1])
        turn: dict[str, Any] = {
            "turn_id": f"{conv}-t{index}",
            "conversation_id": conv,
            "agent": {"name": agent, "role": "specialist"},
            "started_at": at(days_ago, (index - 1) * 2 + 0.5),
            "ended_at": at(days_ago, (index - 1) * 2 + 0.9),
            "content_mode": "stored",
            "build": {"pins": {"prompts": {"core": "v14" if days_ago > 7 else "v15"}, "model": MODEL},
                      "sdk": "niadra-python/0.9.0"},
            "reads": [{"surface": "pack", "etag": f"W/\"{uid('pack', conv, k)[:12]}\""}],
            "calls": [{"call_id": "m1", "kind": "model", "name": MODEL, "latency_ms": 900 + 37 * c.n,
                       "tokens": {"in": 2400 + 37 * index + c.n, "out": 80 + len(text) // 4,
                                  "cached": (2400 + 37 * index + c.n) // 2}}],
            "output": {"event_keys": [message["idempotency_key"]]},
            "latency_ms": 1400 + 41 * c.n,
            "cost": {"usd": round(0.0011 + 0.00003 * index, 5)},
        }  # fmt: skip
        tool = TOOLS.get((scenario, k))
        if tool:
            name, args, result, extra = tool(c)
            call = {"call_id": "k1", "kind": "tool", "name": name, "args_hash": digest(args), "args": "b:1",
                    "result_model": "b:2", "latency_ms": 300 + 11 * c.n, **extra}  # fmt: skip
            turn["calls"].insert(0, call)
            turn["blobs"] = {"b:1": blob(args), "b:2": blob(result)}
        claims, interactions, flags = CLAIMS.get((scenario, k), lambda _c, _t: ([], [], []))(c, text)
        if claims:
            turn["claims"] = claims
        if interactions:
            turn["interactions"] = interactions
        if flags:
            turn["flags"] = flags
        turns.append(turn)
    return turns


def exposure_of(c: Customer, scenario: str) -> str:
    return uid("exposure", c.n, scenario)


def _search(c: Customer) -> tuple[str, dict[str, Any], dict[str, Any], dict[str, Any]]:
    shown = ITEMS[:2]
    seen = at(0)
    result = {"items": [{"sku": sku, "nome": name, "cor": color, "preco": price} for sku, name, color, price in shown]}
    observations = [{"ref": ref_text(item_ref(sku)), "fields": {"price_sale": price, "color": color, "available": True},
                     "provenance": {"source": "live", "source_observed_at": seen}} for sku, _n, color, price in shown]  # fmt: skip
    return "buscar_produtos", {"consulta": "vestido midi", "tamanho": "40", "excluir_cor": "preto"}, result, {
        "observations": observations}


def _plans(c: Customer) -> tuple[str, dict[str, Any], dict[str, Any], dict[str, Any]]:
    return "consultar_planos", {"cliente": c.crm["value"]}, {"planos": [{"codigo": p, "preco": v} for p, _n, v in PLANS]}, {}


def _order(c: Customer) -> tuple[str, dict[str, Any], dict[str, Any], dict[str, Any]]:
    return "consultar_pedido", {"pedido": order_id(c.n)}, {"pedido": order_id(c.n), "status": "enviado",
                                                           "previsao": (NOW - timedelta(days=2)).date().isoformat()}, {}


def _exchange(c: Customer) -> tuple[str, dict[str, Any], dict[str, Any], dict[str, Any]]:
    key = f"troca:{order_id(c.n)}"
    return "abrir_troca", {"pedido": order_id(c.n), "motivo": "avaria"}, {"troca": f"TR-{c.n:04d}", "prazo_dias": 3}, {
        "effect_key": key}


def _cancel(c: Customer) -> tuple[str, dict[str, Any], dict[str, Any], dict[str, Any]]:
    return "cancelar_assinatura", {"cliente": c.crm["value"]}, {"cancelado": True}, {"effect_key": f"cancelar:{c.crm['value']}"}


def _invoice(c: Customer) -> tuple[str, dict[str, Any], dict[str, Any], dict[str, Any]]:
    amount = round(189.9 + c.n * 7, 2)
    return "gerar_segunda_via", {"fatura": invoice_id(c.n)}, {"fatura": invoice_id(c.n), "valor": amount,
                                                              "vencimento": (NOW + timedelta(days=3)).date().isoformat()}, {}


def _credit(c: Customer) -> tuple[str, dict[str, Any], dict[str, Any], dict[str, Any]]:
    return "lancar_credito", {"fatura": invoice_id(c.n), "valor": 24.9}, {"credito": 24.9, "status": "lancado"}, {
        "effect_key": f"credito:{invoice_id(c.n)}"}


def _freight(c: Customer) -> tuple[str, dict[str, Any], dict[str, Any], dict[str, Any]]:
    # The freight tool also returns a gift card the space never declared: its observation opens `type_undeclared`.
    seen = at(0)
    observation = {"ref": f"vale_presente:loja:VP-{c.n:04d}", "fields": {"saldo": 50.0},
                   "provenance": {"source": "live", "source_observed_at": seen}}  # fmt: skip
    return "calcular_frete", {"cidade": c.city, "sku": "jogo-cama-percal"}, {"dias_uteis": [3, 4], "frete": 0}, {
        "observations": [observation]}


TOOLS: dict[tuple[str, int], Callable[[Customer], tuple[str, dict[str, Any], dict[str, Any], dict[str, Any]]]] = {
    ("vestido_festa", 0): _search,
    ("clube_plus", 0): _plans,
    ("troca_tenis", 1): _order,
    ("pedido_atrasado", 0): _order,
    ("luminaria_quebrada", 1): _exchange,
    ("cancelar_clube", 1): _cancel,
    ("segunda_via", 0): _invoice,
    ("frete_duplicado", 0): _credit,
    ("prazo_app", 0): _freight,
}


def money(text: str, written: str, amount: str, verdict: str, *, evidence: dict[str, Any] | None = None,
          role: str = "price_sale", nature: str = "computed", action: str = "none") -> dict[str, Any]:  # fmt: skip
    claim = {"category": "price", "class": "money", "nature": nature, "role": role, "span": span(text, written),
             "value": {"amount": amount, "unit": "BRL"}, "verdict": verdict, "action": action}  # fmt: skip
    if evidence:
        claim["evidence"] = evidence
    return claim


def _dress_offer(c: Customer, text: str) -> tuple[list[Any], list[Any], list[Any]]:
    exposure = exposure_of(c, "vestido_festa")
    items = [{"pos": i + 1, "ref": ref_text(item_ref(sku)), "shown": {"price_sale": price, "color": color}}
             for i, (sku, _n, color, price) in enumerate(ITEMS[:2])]  # fmt: skip
    looks = [{"pos": 1, "ref": ref_text(ref("look", "loja", LOOKS[0][0]))}]
    shown = at(13)
    return (
        [money(text, "R$ 289,90", "289.90", "matched", evidence={"call_id": "k1", "field": "price_sale"})],
        [{"kind": "presented", "exposure_id": exposure, "list_id": "busca", "items": items, "visible_k": 2,
          "delivered_at": shown},
         {"kind": "presented", "exposure_id": uid("look", c.n), "list_id": "looks", "items": looks, "visible_k": 1,
          "delivered_at": shown}],
        [],
    )  # fmt: skip


def _dress_preference(c: Customer, text: str) -> tuple[list[Any], list[Any], list[Any]]:
    return (
        [],
        [{"kind": "engaged", "exposure_id": exposure_of(c, "vestido_festa"), "ref": ref_text(item_ref(ITEMS[0][0])),
          "how": "detail"},
         {"kind": "preference", "attr": "item_variant.color", "op": "not_in", "values": ["preto"], "strength": "must_not",
          "scope": "persistent", "source": "stated"},
         {"kind": "preference", "attr": "item_variant.color", "op": "in", "values": ["bege", "off-white", "azul claro"],
          "strength": "prefer", "scope": "persistent", "source": "stated"},
         {"kind": "attribute", "name": "size.dress", "system": "BR", "value": "40", "source": "stated"}],
        [],
    )  # fmt: skip


def _club_offer(c: Customer, text: str) -> tuple[list[Any], list[Any], list[Any]]:
    plans = [{"pos": i + 1, "ref": ref_text(plan_ref(code)), "shown": {"price_table": price}}
             for i, (code, _n, price) in enumerate(PLANS)]  # fmt: skip
    flagged = {"category": "discount", "class": "percent", "nature": "model", "span": span(text, "10%"),
               "value": {"amount": "10", "unit": "%"}, "verdict": "unsupported",
               "action": "block" if c.n % 2 else "warn"}  # fmt: skip
    price = money(text, "R$ 59,90", "59.90", "matched", role="price_monthly",
                  evidence={"call_id": "k1", "field": "preco"})  # fmt: skip
    return (
        [price, flagged],
        [{"kind": "presented", "exposure_id": exposure_of(c, "clube_plus"), "list_id": "planos", "items": plans,
          "visible_k": 2, "delivered_at": at(10)}],
        ["guard_acted"] if c.n % 2 else [],
    )  # fmt: skip


def _hedged_date(category: str, written: str) -> Callable[[Customer, str], tuple[list[Any], list[Any], list[Any]]]:
    """A delivery date the agent's data was too old to state: the SDK rewrote it as a hedge ("até sexta" stands for
    "vou confirmar"), and the turn records the rewrite."""

    def claims(c: Customer, text: str) -> tuple[list[Any], list[Any], list[Any]]:
        if written not in text:
            return [], [], []
        claim = {"category": category, "class": "date", "nature": "model", "span": span(text, written),
                 "verdict": "stale" if c.n % 3 else "no_evidence", "action": "rewrite" if c.n % 3 else "warn"}  # fmt: skip
        return [claim], [], ["negative_feedback"] if category == "promise" else []

    return claims


def _duration(written: str, low: str, high: str) -> Callable[[Customer, str], tuple[list[Any], list[Any], list[Any]]]:
    def claims(c: Customer, text: str) -> tuple[list[Any], list[Any], list[Any]]:
        claim = {"category": "delivery_time", "class": "duration", "nature": "computed", "span": span(text, written),
                 "value": {"min": low, "max": high, "unit": "d"}, "verdict": "matched", "action": "none",
                 "evidence": {"call_id": "k1", "field": "dias_uteis" if low != high else "prazo_dias"}}  # fmt: skip
        return [claim], [], []

    return claims


def _cancel_offer(c: Customer, text: str) -> tuple[list[Any], list[Any], list[Any]]:
    return [money(text, "R$ 29,90", "29.90", "matched", role="price_monthly",
                  evidence={"ref": ref_text(plan_ref("clube-essencial")), "field": "price_table"})], [], []  # fmt: skip


def _invoice_amount(c: Customer, text: str) -> tuple[list[Any], list[Any], list[Any]]:
    written = f"R$ {189.9 + c.n * 7:.2f}".replace(".", ",")
    return [money(text, written, f"{189.9 + c.n * 7:.2f}", "matched", role="invoice_amount",
                  evidence={"call_id": "k1", "field": "valor"})], [], []  # fmt: skip


def _credit_amount(c: Customer, text: str) -> tuple[list[Any], list[Any], list[Any]]:
    return [money(text, "R$ 24,90", "24.90", "matched", role="credit_amount",
                  evidence={"call_id": "k1", "field": "credito"})], [], []  # fmt: skip


def _afternoons(c: Customer, text: str) -> tuple[list[Any], list[Any], list[Any]]:
    pref = {"kind": "preference", "attr": "delivery.window", "op": "in", "values": ["tarde"], "strength": "prefer",
            "scope": "persistent", "source": "stated"}  # fmt: skip
    return [], [pref], ["handoff"]


CLAIMS: dict[tuple[str, int], Callable[[Customer, str], tuple[list[Any], list[Any], list[Any]]]] = {
    ("vestido_festa", 0): _dress_offer,
    ("vestido_festa", 1): _dress_preference,
    ("clube_plus", 0): _club_offer,
    ("troca_tenis", 1): _hedged_date("delivery_date", "até sexta"),
    ("pedido_atrasado", 1): _hedged_date("promise", "até sexta"),
    ("luminaria_quebrada", 1): _duration("3 dias úteis", "3", "3"),
    ("cancelar_clube", 0): _cancel_offer,
    ("segunda_via", 0): _invoice_amount,
    ("frete_duplicado", 0): _credit_amount,
    ("prazo_app", 0): _duration("3 a 4 dias úteis", "3", "4"),
    ("endereco_novo", 1): _afternoons,
}


class Seeder:
    """The steps, in the order a company would set the space up and use it."""

    def __init__(self, demo: Demo) -> None:
        self.d = demo

    # Setup: agents, configuration, warnings

    def sources(self) -> str:
        d = self.d
        listed = {s["name"]: s for s in d.person.get("/v1/sources", space_id=d.space) if not s["revoked_at"]}
        made = 0
        for spec in SOURCES:
            if spec["name"] not in listed:
                body = {k: v for k, v in spec.items() if k != "scopes"} | {"space_id": d.space}
                listed[spec["name"]] = d.person.post("/v1/sources", body, 201)
                made += 1
            source_id = listed[spec["name"]]["source_id"]
            d.source_ids[spec["name"]] = source_id
            # The previous run's keys go; this run's are the only ones that work.
            for key in d.person.get(f"/v1/sources/{source_id}/keys"):
                if key["status"] == "active":
                    path = f"/v1/sources/{source_id}/keys/{key['key_id']}/revoke"
                    d.person.post(path, {"reason": "demo: the seed issues new keys each run"}, 200)
            issued = d.person.post(f"/v1/sources/{source_id}/keys", {"scopes": spec["scopes"]}, 201)
            d.keys[spec["name"]] = bearer(issued["secret"])
        return f"{len(SOURCES)} sources ({made} new), one fresh key each"

    def configuration(self) -> str:
        d = self.d
        changed = []
        features = ["turns", "state", "agent_state", "signals", "claims", "coordination", "measurement",
                    "notifications", "legal_holds"]  # fmt: skip
        if d.declare("features", lambda doc: {**doc, "enabled": sorted(set(doc.get("enabled") or ()) | set(features))},
                     "every agent feature on for the demo"):  # fmt: skip
            changed.append("features")
        if d.declare("object-types", lambda doc: typed(doc, *TYPES), "the retailer's types"):
            changed.append("object-types")
        if d.declare("measurement", lambda doc: {**doc, **MEASUREMENT}, "purchases measured on orders"):
            changed.append("measurement")
        sampling = {"base_rate": 0.1, "boosted_rate": 1.0, "daily_cap": 500}
        if d.declare("review-sampling", lambda doc: {**doc, **sampling}, "a larger review sample for the demo"):
            changed.append("review-sampling")
        binding = {"tool": "buscar_produtos",
                   "args": [{"attr": "item_variant.color", "param": "cor", "negation": {"param": "excluir_cor"}}]}  # fmt: skip
        if d.declare("tool-bindings", lambda doc: {**doc, "bindings": [binding]}, "the product search tool"):
            changed.append("tool-bindings")
        if d.declare("purposes", lambda doc: {**doc, **PURPOSES}, "purposes and their legal bases"):
            changed.append("purposes")
        if d.declare("trait-rules", lambda _: TRAIT_RULES, "overdue invoices, unanswered calls, complaints"):
            changed.append("trait-rules")
        return f"approved: {', '.join(changed) or 'nothing new'}"

    def coordination(self) -> str:
        d = self.d
        vault = {"value": b64url(GATEWAY_KEY)}
        d.call("PUT", f"/v1/secrets/gateway/{GATEWAY_ID}", None, vault, 200)
        panel = {
            "name": "painel_atendimento",
            "via": "mapping",
            "max_validity_s": 3600,
            "levels": ["human_active"],
            "source_id": d.source_ids["crm-interno"],
            "rules": [
                {"canonical_type": "panel.session_opened", "level": "human_active", "holder": "human:painel"},
                {"canonical_type": "panel.session_closed"},
            ],
        }
        document = {
            "purposes": [
                {"name": "transactional", "fail": "open"},
                {"name": "service", "fail": "open"},
                {"name": "marketing", "fail": "closed", "token": True, "budget": {"limit": 1, "per_hours": 168}},
                {"name": "retention", "fail": "closed", "token": True},
                {"name": "collection", "fail": "closed", "token": True},
            ],
            "gateways": [{"gateway_id": GATEWAY_ID, "secret_ref": f"spaces/{d.space}/gateway/{GATEWAY_ID}",
                          "channels": ["whatsapp"]}],
            "ownership": {"sources": [panel]},
            "handoffs": {"outcomes": HANDOFF_OUTCOMES},
        }
        approved = d.declare("coordination", lambda doc: {**doc, **document}, "ownership, contact tokens, handoffs")
        on = {"enabled": True, "writes": "agent"}
        memory = d.declare("agent-memory", lambda doc: {**doc, **on}, "agent memory, written by the agents")
        return f"coordination {'approved' if approved else 'as it was'}, agent memory {'on' if memory else 'as it was'}"

    def warnings(self) -> str:
        d = self.d
        # The ERP's endpoint for warnings and a SIEM stream of receipts. The receiving systems are fictional
        # (`.example` never resolves), so the warnings' deliveries fail and wait to be sent again, as the Alerts
        # screen shows; the SIEM stream is declared turned off, or every batch of receipts would fail too.
        endpoints = []
        for endpoint, owner, url, extra in (
            (ENDPOINT_ID, "erp", "https://avisos.erp.mirante.example/niadra", {"event_types": [
                "trigger.fired", "object.timer_due", "data_issue.opened", "type.drift", "erasure.completed",
                "agent_memory.note_created"]}),
            (SIEM_ID, "crm-interno", "https://siem.mirante.example/coletor", {"event_types": ["*"], "format": "ocsf",
                                                                                 "enabled": False}),
        ):  # fmt: skip
            secret = "whsec_" + base64.b64encode(hashlib.sha256(f"demo {endpoint}".encode()).digest()).decode()
            written = d.call("PUT", f"/v1/secrets/webhook/{endpoint}", None, {"value": secret}, 200)
            ref = f"spaces/{d.space}/webhook/{endpoint}"
            version = int(written.get("version") or 1)
            existing = {r["ref"] for r in d.person.get("/v1/secret-refs", space_id=d.space)}
            if ref not in existing:
                body = {"space_id": d.space, "ref": ref, "kind": "webhook", "version": version}
                d.person.post("/v1/secret-refs", body, 201)
            endpoints.append({"endpoint_id": endpoint, "url": url, "source_id": d.source_ids[owner], "secret_refs": [ref],
                              **extra})
        d.declare("webhook-endpoints", lambda _: endpoints, "the ERP's warnings and the SIEM stream")
        rules = [
            {"rule_id": "fatura_vencendo", "condition": "object.timer_due", "version": "1", "endpoint_id": ENDPOINT_ID,
             "params": {}},
            {"rule_id": "cliente_com_atrasos", "condition": "trait.present", "version": "1", "endpoint_id": ENDPOINT_ID,
             "params": {"name": "faturas_em_atraso"}},
            {"rule_id": "promessa_vencida", "condition": "promise.overdue", "version": "1", "endpoint_id": ENDPOINT_ID,
             "params": {}},
            {"rule_id": "agente_em_silencio", "condition": "source.silent", "version": "1", "endpoint_id": ENDPOINT_ID,
             "params": {"minutes": 720}},
        ]  # fmt: skip
        d.declare("trigger-rules", lambda _: rules, "warnings to the ERP")
        return f"{len(endpoints)} endpoints, {len(rules)} warning rules"

    def keys_ready(self) -> str:
        d = self.d

        def ready() -> bool:
            for name in d.keys:
                response = retried(lambda n=name: d.api.get("/v1/sources/me", headers=d.keys[n]))
                if response.status_code != 200:
                    return False
            profile = retried(lambda: d.api.get("/v1/sdk/profile", headers=d.keys["vendas-whatsapp"]))
            names = {t["type"] for t in profile.json().get("types", [])} if profile.status_code == 200 else set()
            return {"order", "plan", "look"} <= names

        d.until("the keys and the types reaching the cell", ready, READY_TIMEOUT_S)
        return "every key and every type served by the cell"

    # Customers: who they are across channels, and the companies some of them buy for

    def identities(self) -> str:
        d = self.d
        items = []
        methods = ["system_import", "explicit_identify"]
        for c in CUSTOMERS:
            handles = [c.crm, *{json.dumps(c.handle(ch), sort_keys=True): c.handle(ch) for ch in c.channels}.values()]
            items.append({"type": "identify", "idempotency_key": f"demo-identify-{c.n}-{digest(handles)[7:15]}",
                          "handles": handles,
                          "method": methods[c.n % len(methods)], "occurred_at": at(14 - c.n % 3)})  # fmt: skip
        body = d.batch("crm-interno", items)
        assert not body["errors"], body["errors"][:3]
        for group in GROUPS:
            _holding_name, holding_domain = group["holding"]
            for person, role in group["contacts"]:
                link = {"person": person.phone, "organization": org(holding_domain), "role": role,
                        "can_see_contacts": role == "compras", "method": "declared"}  # fmt: skip
                d.call("POST", "/v1/identity/links", None, link, 200, 201, 409, once=uid("link", person.n, holding_domain))
        # The ERP bills each company of the groups: their accounts get memory of their own.
        bills = []
        for g, group in enumerate(GROUPS):
            for k, (name, domain) in enumerate([group["holding"], *group["companies"]]):
                invoice = ref("invoice", "erp", f"FAT-B2B-{g}{k}")
                bills.append({
                    "type": "event", "kind": "system_event", "idempotency_key": f"demo-b2b-{g}{k}", "channel": "erp",
                    "canonical_type": "invoice.issued", "handles": [org(domain)], "object_refs": [invoice],
                    "speaker": {"role": "system"}, "occurred_at": at(9 - k),
                    "fields": {"amount": f"{4890 + 1270 * k:.2f}", "status": "open", "state": "open",
                               "due_date": (NOW + timedelta(days=12 + k)).date().isoformat(),
                               "description": f"Enxoval e decoração, {name}"},
                })  # fmt: skip
        body = d.batch("erp", bills)
        assert not body["errors"], body["errors"][:3]
        return f"{len(CUSTOMERS)} customers joined across channels; {len(GROUPS)} company groups with their buyers; B2B invoices"

    def companies(self) -> str:
        """The B2B side: the people of the companies and partners, who works where and in what role (some in
        two companies), a short WhatsApp exchange with each company's buyer, and the orders and invoices of each
        company and partner, so every one of them has objects and people around it in the relationship graph."""
        d = self.d
        identify = [{"type": "identify", "idempotency_key": f"demo-identify-{c.n}-{digest([c.crm, c.phone, c.email])[7:15]}",
                     "handles": [c.crm, c.phone, c.email], "method": "system_import", "occurred_at": at(14 - c.n % 3)}
                    for c in CONTACTS]  # fmt: skip
        body = d.batch("crm-interno", identify)
        assert not body["errors"], body["errors"][:3]
        members = [(org(domain), domain, person, role) for _name, domain, people in COMPANIES for person, role in people]
        members += [(partner(code), code, person, role) for _name, code, people in PARTNERS for person, role in people]
        for organization, key, person, role in members:
            link = {"person": person.phone, "organization": organization, "role": role, "method": "declared",
                    "can_see_contacts": role in BUYERS}  # fmt: skip
            d.call("POST", "/v1/identity/links", None, link, 200, 201, 409, once=uid("link", person.n, key))
        talks = 0
        for name, domain, people in COMPANIES:
            buyer = next(p for p, role in people if role in BUYERS)
            conv = f"demo-b2b-{slug(name).replace('.', '-')}"
            _sku, item_name, _color, _price = ITEMS[len(name) % len(ITEMS)]
            lines = [
                ("C", f"Oi, aqui é {buyer.first}, da {name}. Preciso repor {item_name.lower()} para a equipe, umas 20 unidades."),
                ("A", f"Oi, {buyer.first}! Para a {name} consigo o preço de atacado. Mando o pedido no CNPJ da empresa?"),
                ("C", "Isso, no CNPJ. E a fatura vai para o nosso financeiro, como sempre."),
            ]  # fmt: skip
            items = [{"type": "event", "idempotency_key": f"{conv}-{i + 1}", "channel": "whatsapp",
                      "conversation_id": conv, "handles": [buyer.phone],
                      "speaker": {"role": "ai_agent", "id": "vendas-whatsapp"} if who == "A" else {"role": "customer"},
                      "direction": "outbound" if who == "A" else "inbound",
                      "content": {"type": "text", "text": text}, "occurred_at": at(5 + len(name) % 4, i * 2)}
                     for i, (who, text) in enumerate(lines)]  # fmt: skip
            items.append({"type": "conversation.ended", "idempotency_key": f"{conv}-end", "conversation_id": conv,
                          "occurred_at": at(5 + len(name) % 4, 8)})  # fmt: skip
            d.batch("vendas-whatsapp", items)
            talks += 1
        events: list[dict[str, Any]] = []
        buyers = [(org(domain), domain.removesuffix(".example").replace(".", "-").upper(), person)
                  for _name, domain, people in COMPANIES for person, role in people if role in BUYERS]
        buyers += [(partner(code), code, people[0][0]) for _name, code, people in PARTNERS]
        for j, (organization, code, person) in enumerate(buyers):
            for k in range(2):
                order = ref("order", "erp", f"PED-B2B-{code}-{person.n}-{k}")
                sku, _name, _color, price = ITEMS[(j + k) % len(ITEMS)]
                quantity = 12 + 4 * ((j + k) % 3)
                lines = [{"line_id": "1", "ref": ref_text(item_ref(sku)), "amount": f"{price:.2f}", "quantity": quantity}]
                path = ["placed", "paid", "shipped", "delivered"][: 2 + (j + k) % 3]
                for step, state in enumerate(path):
                    events.append({
                        "type": "event", "kind": "system_event", "idempotency_key": f"demo-{order['id']}-{state}",
                        "channel": "erp", "canonical_type": f"order.{state}", "handles": [person.phone, organization],
                        "object_refs": [order], "speaker": {"role": "system"},
                        "fields": {"state": state, "total": f"{price * quantity:.2f}", "lines": lines},
                        "occurred_at": at(max(11 - j % 8 - 2 * k - step * 1.5, 0.3)),
                    })  # fmt: skip
        billed = [(org(domain), domain.removesuffix(".example").replace(".", "-").upper(), name)
                  for name, domain, _people in COMPANIES]
        billed += [(partner(code), code, name) for name, code, _people in PARTNERS]
        for j, (organization, code, name) in enumerate(billed):
            for k, state in enumerate(("paid", "open")):
                invoice = ref("invoice", "erp", f"FAT-B2B-{code}-{k}")
                events.append({
                    "type": "event", "kind": "system_event", "idempotency_key": f"demo-{invoice['id']}-{state}",
                    "channel": "erp", "canonical_type": f"invoice.{'paid' if state == 'paid' else 'issued'}",
                    "handles": [organization], "object_refs": [invoice], "speaker": {"role": "system"},
                    "occurred_at": at(10 - 6 * k - j % 3),
                    "fields": {"amount": f"{2390 + 610 * j + 450 * k:.2f}", "status": state, "state": state,
                               "due_date": (NOW + timedelta(days=-8 + 20 * k)).date().isoformat(),
                               "description": f"Enxoval e utilidades, {name}"},
                })  # fmt: skip
        body = d.batch("erp", events)
        assert not body["errors"], body["errors"][:3]
        two = {person.n for _o, _k, person, _r in members}
        twice = sum(1 for n in two if sum(1 for _o, _k, p, _r in members if p.n == n) > 1)
        return (f"{len(CONTACTS)} company people; {len(COMPANIES)} companies and {len(PARTNERS)} partners with "
                f"{len(members)} links ({twice} people in two); {talks} buyer conversations; {len(events)} B2B "
                "order and invoice events")

    def conversations(self) -> str:
        d = self.d
        by_agent: dict[str, list[dict[str, Any]]] = {}
        proofs: dict[str, list[dict[str, Any]]] = {}
        method = {"voice": ("network_attestation", "V1"), "whatsapp": ("otp_whatsapp", "V1"), "app": ("login", "V2"),
                  "email": ("kba", "V1")}  # fmt: skip
        for n, scenario, days_ago in ASSIGNMENT:
            c = CUSTOMERS[n - 1]
            spec = SCENARIOS[scenario]
            conv = conversation_id(n, scenario)
            how, level = method[spec["channel"]]
            handle = c.email if spec["channel"] == "email" else c.app if spec["channel"] == "app" else c.phone
            proofs.setdefault(spec["agent"], []).append(
                {"type": "verify", "idempotency_key": f"{conv}-proof", "method": how, "level": level,
                 "conversation_id": conv, "handle": handle, "occurred_at": at(days_ago, 1)})  # fmt: skip
            by_agent.setdefault(spec["agent"], []).extend(conversation_items(c, scenario, days_ago))
        accepted = duplicates = 0
        for agent, items in by_agent.items():
            body = d.batch(agent, proofs[agent] + items)
            assert not body["errors"], (agent, body["errors"][:3])
            accepted, duplicates = accepted + body["accepted"], duplicates + body["duplicates"]
        return f"{len(ASSIGNMENT)} conversations of 3 agents: {accepted} items accepted, {duplicates} already there"

    # Business objects from the ERP and the CRM

    def orders(self) -> str:
        d = self.d
        paths = [
            ["placed", "paid", "shipped", "delivered", "kept"],
            ["placed", "paid", "shipped", "delivered"],
            ["placed", "paid", "shipped"],
            ["placed", "paid"],
            ["placed", "paid", "shipped", "delivered", "returned"],
            ["placed", "cancelled"],
            ["placed"],
        ]
        items = []
        for c in CUSTOMERS:
            for k in range(1 + c.n % 2):
                path = paths[(c.n + k) % len(paths)]
                order = ref("order", "erp", order_id(c.n, k))
                sku, _name, _color, price = ITEMS[(c.n + k) % len(ITEMS)]
                lines = [{"line_id": "1", "ref": ref_text(item_ref(sku)), "amount": f"{price:.2f}", "quantity": 1}]
                start = 13 - (c.n + 3 * k) % 12
                for step, state in enumerate(path):
                    fields: dict[str, Any] = {"state": state, "total": f"{price + 19.9:.2f}", "lines": lines}
                    if state == "shipped":
                        fields["tracking_code"] = f"BR{c.n:03d}{k}4471SP"
                        fields["delivery_estimate"] = (NOW - timedelta(days=start - step - 4)).date().isoformat()
                    items.append({
                        "type": "event", "kind": "system_event", "idempotency_key": f"demo-{order['id']}-{state}",
                        "channel": "erp", "canonical_type": f"order.{state}", "handles": [c.main, c.crm],
                        "object_refs": [order], "speaker": {"role": "system"}, "fields": fields,
                        "occurred_at": at(max(start - step * 1.5, 0.2)),
                    })  # fmt: skip
        body = d.batch("erp", items)
        assert not body["errors"], body["errors"][:3]
        return f"{len(items)} order events across 7 lifecycles"

    def invoices(self) -> str:
        d = self.d
        items = []
        for c in CUSTOMERS:
            invoice = ref("invoice", "erp", invoice_id(c.n))
            amount = f"{189.9 + c.n * 7:.2f}"
            due = NOW + timedelta(days=(c.n % 5) - 1)
            fields = {"amount": amount, "status": "open", "state": "open", "due_date": due.date().isoformat()}
            items.append({
                "type": "event", "kind": "system_event", "idempotency_key": f"demo-{invoice['id']}-issued",
                "channel": "erp", "canonical_type": "invoice.issued", "handles": [c.main, c.crm],
                "object_refs": [invoice], "speaker": {"role": "system"}, "fields": fields, "occurred_at": at(12),
            })  # fmt: skip
            if c.n % 4 == 0:
                # Two overdue invoices in the month: the `faturas_em_atraso` pattern.
                for k, days_ago in ((1, 9), (2, 3)):
                    late = ref("invoice", "erp", invoice_id(c.n, k))
                    items.append({
                        "type": "event", "kind": "system_event", "idempotency_key": f"demo-{late['id']}-overdue",
                        "channel": "erp", "canonical_type": "invoice.overdue", "handles": [c.main, c.crm],
                        "object_refs": [late], "speaker": {"role": "system"}, "occurred_at": at(days_ago),
                        "fields": {"amount": f"{99.9 + k * 30:.2f}", "status": "overdue", "state": "overdue",
                                   "due_date": (NOW - timedelta(days=days_ago + 2)).date().isoformat()},
                    })  # fmt: skip
            elif c.n % 4 == 1:
                items.append({
                    "type": "event", "kind": "system_event", "idempotency_key": f"demo-{invoice['id']}-paid",
                    "channel": "erp", "canonical_type": "invoice.paid", "handles": [c.main, c.crm],
                    "object_refs": [invoice], "speaker": {"role": "system"}, "occurred_at": at(4),
                    "fields": {"status": "paid", "state": "paid"},
                })  # fmt: skip
        # The billing agent's credit for a freight charged twice, as an action that names the invoice.
        for n, scenario, days_ago in ASSIGNMENT:
            if scenario == "frete_duplicado":
                c = CUSTOMERS[n - 1]
                invoice = ref("invoice", "erp", invoice_id(n))
                items.append({
                    "type": "event", "kind": "action", "idempotency_key": f"demo-{invoice['id']}-credit",
                    "channel": "erp", "handles": [c.main, c.crm], "object_refs": [invoice],
                    "speaker": {"role": "ai_agent", "id": "cobranca"}, "occurred_at": at(days_ago, 5),
                    "action": {"operation": "credit", "result": "crédito de R$ 24,90 lançado", "purpose": "billing"},
                })  # fmt: skip
                if n % 2:
                    # The ERP confirms half of the credits; the others stay as the agent's word against the system's.
                    items.append({
                        "type": "event", "kind": "system_event", "idempotency_key": f"demo-{invoice['id']}-credited",
                        "channel": "erp", "canonical_type": "invoice.credited", "handles": [c.main, c.crm],
                        "object_refs": [invoice], "speaker": {"role": "system"}, "occurred_at": at(days_ago, 9),
                        "fields": {"status": "credited", "state": "credited", "credit": "24.90"},
                    })  # fmt: skip
        body = d.batch("erp", items)
        assert not body["errors"], body["errors"][:3]
        return f"{len(items)} invoice events: open, overdue twice for every fourth customer, paid, credited"

    def tickets(self) -> str:
        d = self.d
        items = []
        states = [["open"], ["open", "in_progress"], ["open", "in_progress", "waiting_customer"],
                  ["open", "in_progress", "resolved"], ["open", "resolved", "closed"]]  # fmt: skip
        subjects = ["Entrega atrasada", "Produto com avaria", "Troca de tamanho", "Cobrança em duplicidade",
                    "Alteração de endereço"]  # fmt: skip
        for c in CUSTOMERS[::3]:
            ticket = ref("ticket", "crm", f"CH-{5100 + c.n}")
            path = states[c.n % len(states)]
            for step, state in enumerate(path):
                fields: dict[str, Any] = {"state": state}
                if step == 0:
                    fields |= {"subject": subjects[c.n % len(subjects)], "priority": ["alta", "media", "baixa"][c.n % 3],
                               "channel": "voz" if "v" in c.channels else "whatsapp",
                               "sla_ends": (NOW + timedelta(hours=3 + c.n % 20)).isoformat()}  # fmt: skip
                items.append({
                    "type": "event", "kind": "system_event", "idempotency_key": f"demo-{ticket['id']}-{state}",
                    "channel": "crm", "canonical_type": f"ticket.{state}", "handles": [c.main, c.crm],
                    "object_refs": [ticket], "speaker": {"role": "system"}, "fields": fields,
                    "occurred_at": at(6 - step, c.n),
                })  # fmt: skip
        # The CRM agent's tasks: an address updated before invoicing, a ticket opened for logistics.
        for n, scenario, days_ago in ASSIGNMENT:
            if scenario == "endereco_novo":
                c = CUSTOMERS[n - 1]
                task = f"demo-task-endereco-{n:02d}"
                items += [
                    {"type": "event", "kind": "action", "idempotency_key": f"{task}-1", "channel": "crm", "task_id": task,
                     "handles": [c.crm], "object_refs": [ref("order", "erp", order_id(n))],
                     "speaker": {"role": "ai_agent", "id": "crm-interno"}, "occurred_at": at(days_ago, 20),
                     "action": {"operation": "update_address", "result": f"endereço de entrega trocado para {c.city}",
                                "purpose": "support"}},
                    {"type": "task.ended", "idempotency_key": f"{task}-end", "task_id": task, "occurred_at": at(days_ago, 22)},
                ]  # fmt: skip
        # Voice calls nobody answered: the `nao_atende_ligacoes` pattern for three customers.
        for c in CUSTOMERS[5:20:5]:
            for k in range(3):
                items.append({
                    "type": "event", "kind": "system_event", "idempotency_key": f"demo-unanswered-{c.n}-{k}",
                    "channel": "voice", "canonical_type": "call.unanswered", "handles": [c.phone],
                    "speaker": {"role": "system"}, "fields": {"attempt": k + 1}, "occurred_at": at(8 - 2 * k),
                })  # fmt: skip
        body = d.batch("crm-interno", items)
        assert not body["errors"], body["errors"][:3]
        return f"{len(items)} CRM events: tickets in 5 states, address tasks, unanswered calls"

    # The agents' turns: what each read, called, showed, claimed and decided

    def turns(self) -> str:
        d = self.d
        by_agent: dict[str, list[dict[str, Any]]] = {}
        # On a second run the erased customer is gone, and their turns must not come back.
        erased = self.profile_of(FORGOTTEN.phone) is None
        for n, scenario, days_ago in ASSIGNMENT:
            c = CUSTOMERS[n - 1]
            if c is FORGOTTEN and erased:
                continue
            spec = SCENARIOS[scenario]
            by_agent.setdefault(spec["agent"], []).extend(scenario_turns(c, scenario, days_ago))
        accepted = duplicates = 0
        for agent, turns in by_agent.items():
            body = d.turns(agent, turns)
            assert not body["errors"], (agent, body["errors"][:3])
            accepted, duplicates = accepted + body["accepted"], duplicates + body["duplicates"]
        # Every demo conversation kept whole, so the Console's list shows them (the list reads the kept tier only).
        promoted = 0
        for n, scenario, _days_ago in ASSIGNMENT:
            if CUSTOMERS[n - 1] is FORGOTTEN and erased:
                continue
            agent = SCENARIOS[scenario]["agent"]
            conv = conversation_id(n, scenario)
            body = {"conversation_id": conv, "reason": "review"}
            answer = d.call("POST", "/v1/turns/promote", agent, body, 200, once=f"promote-{conv}")
            promoted += answer["promoted"] + answer["already_kept"]
        return f"{accepted} turns recorded ({duplicates} already there), {promoted} kept for review"

    # Shared objects: the catalog, the plans and the looks the platform pushes; quotes derived from a plan

    def push(self, objects: list[dict[str, Any]]) -> dict[str, int]:
        """A push from the store's platform; a shared object enters the working set when an agent showed it, so a
        push before that turn reached the cell is `out_of_set`, sent again until it lands."""
        d = self.d
        seen = NOW.isoformat()
        body = {"objects": [o | {"provenance": {"source": "live", "source_observed_at": seen}} for o in objects]}

        def landed() -> dict[str, int] | None:
            answer = d.call("POST", "/v1/objects/push", "plataforma-loja", body, 200)
            return None if answer.get("out_of_set") else answer

        names = ", ".join(o["ref"]["id"] for o in objects)
        return dict(d.until(f"the push of {names} landing in the working set", landed))

    def shared(self) -> str:
        items = [{"ref": item_ref(sku), "version": 7, "fields": {"name": name, "color": color, "available": True,
                                                                  "price_sale": round(price * 0.9, 2)}}
                 for sku, name, color, price in ITEMS[:2]]  # fmt: skip
        pushed = self.push(items)
        plans = [{"ref": plan_ref(code), "version": 1,
                  "fields": {"name": name, "price_table": price, "promo_ends": (NOW + timedelta(minutes=50)).isoformat()}}
                 for code, name, price in PLANS]  # fmt: skip
        self.push(plans)
        pieces = {sku: {"ref": item_ref(sku), "version": 6, "fields": {"name": name, "color": color, "available": True,
                                                                        "price_sale": price}}
                  for sku, name, color, price in ITEMS}  # fmt: skip
        for code, name, skus in LOOKS[:1]:
            self.push([{"ref": ref("look", "loja", code), "version": 7,
                        "fields": {"name": name, "pieces": [{"id": sku, "status": "exact"} for sku in skus]}}])  # fmt: skip
            for sku in skus[1:]:
                self.push([pieces[sku]])
            # The sandal sells out: the look is no longer whole.
            out = {**pieces[skus[-1]], "version": 8, "fields": {**pieces[skus[-1]]["fields"], "available": False}}
            self.push([out])
        return f"catalog prices lowered 10% since shown ({pushed['applied']} applied), 2 plans with a promotion ending, a look with a piece out"

    def quotes(self) -> str:
        d = self.d
        made = 0
        for n, scenario, _days_ago in ASSIGNMENT:
            if scenario != "clube_plus":
                continue
            quote = {"ref": ref("quote", "loja", f"COT-{n:04d}"), "version": 1, "fields": {"price_full": 59.9 * 12},
                     "inputs": {"plan": ref_text(plan_ref("clube-plus"))}}  # fmt: skip
            d.call("POST", "/v1/objects/push", "plataforma-loja",
                   {"objects": [quote | {"provenance": {"source": "live", "source_observed_at": NOW.isoformat()}}]}, 200)  # fmt: skip
            made += 1

        first = next(n for n, s, _ in ASSIGNMENT if s == "clube_plus")

        def bound() -> bool:
            read = {"refs": [ref("quote", "loja", f"COT-{first:04d}")]}
            found = d.call("POST", "/v1/state/read", "vendas-whatsapp", read, 200)
            return bool(found["objects"]) and found["objects"][0].get("derived_status") == "current"

        d.until("the quotes bound to the plan", bound)
        # The plan's price goes up: every quote derived from it expires, naming the input; the promotion is revised.
        later = (NOW + timedelta(minutes=55)).isoformat()
        self.push([{"ref": plan_ref("clube-plus"), "version": 2, "fields": {"price_table": 64.9, "promo_ends": later}}])
        return f"{made} annual quotes derived from the Plus plan, expired by its new price"

    # Signals and coordination

    def coordination_flows(self) -> str:
        d = self.d
        done = []
        # An opt-out: the customer asked for no more promotions; marketing to them is suppressed.
        quiet = [CUSTOMERS[n - 1] for n, s, _ in ASSIGNMENT if s == "clube_plus"][:3]
        for c in quiet:
            body = {"kind": "suppression.added", "subject": c.phone, "agent": "vendas-whatsapp",
                    "detail": {"purpose": "marketing", "reason": "opt_out"}}  # fmt: skip
            d.call("POST", "/v1/coordination/declare", "vendas-whatsapp", body, 200, once=uid("optout", c.n))
        check = {"agent": "vendas-whatsapp", "intent": "promo_clube", "channel": "whatsapp", "direction": "outbound",
                 "purpose": "marketing", "gateway_id": GATEWAY_ID}  # fmt: skip
        denied = d.call("POST", "/v1/coordination/check", "vendas-whatsapp", {**check, "subject": quiet[0].phone}, 200)
        done.append(f"opt-out {denied['decision']} {denied.get('reasons')}")
        # A contact token under a budget of one a week: the first agent gets it, the second is refused.
        target = CUSTOMERS[20]
        first = d.call("POST", "/v1/coordination/check", "vendas-whatsapp", {**check, "subject": target.phone}, 200)
        second = d.call("POST", "/v1/coordination/check", "cobranca",
                        {**check, "agent": "cobranca", "subject": target.phone}, 200)  # fmt: skip
        if first.get("contact_token") and first.get("decision_id"):
            made = {"kind": "contact.made", "subject": target.phone, "agent": "disparo",
                    "detail": {"decision_id": first["decision_id"], "purpose": "marketing", "channel": "whatsapp",
                               "gateway_id": GATEWAY_ID}}  # fmt: skip
            d.call("POST", "/v1/coordination/declare", "vendas-whatsapp", made, 200, once=uid("contact", target.n))
        done.append(f"token {first['decision']}, second {second['decision']} {second.get('reasons')}")
        # An effect done once: the billing reminder goes out, and the second attempt is refused.
        payer = CUSTOMERS[11]
        effect = {"subject": payer.email, "agent": "cobranca", "intent": "lembrete_vencimento", "channel": "email",
                  "direction": "outbound", "purpose": "transactional", "effect_key": f"lembrete:{invoice_id(payer.n)}"}  # fmt: skip
        once = d.call("POST", "/v1/coordination/check", "cobranca", effect, 200)
        sent = {"kind": "effect", "subject": payer.email, "agent": "cobranca",
                "detail": {"effect_key": effect["effect_key"], "state": "done", "attempt": 1}}  # fmt: skip
        d.call("POST", "/v1/coordination/declare", "cobranca", sent, 200, once=uid("effect", payer.n))
        again = d.call("POST", "/v1/coordination/check", "cobranca", effect, 200)
        done.append(f"effect {once['decision']}, then {again['decision']} {again.get('reasons')}")
        # Ownership: the voice agent holds a complaint; the service panel's human takes another customer.
        for c, holder in ((CUSTOMERS[3], "agent:sac-voz"), (CUSTOMERS[16], "agent:sac-voz")):
            lease = {"subject": c.phone, "kind": "owner", "holder": holder, "level": "agent_active", "lease_s": 86_400,
                     "intents": ["reclamacao"]}  # fmt: skip
            d.call("POST", "/v1/coordination/claims", "sac-voz", lease, 201, 409, once=uid("lease", c.n, NOW.date()))
        panel = CUSTOMERS[6]
        opened = {"type": "event", "kind": "system_event", "idempotency_key": f"demo-panel-{panel.n}-{NOW.date()}",
                  "channel": "panel", "canonical_type": "panel.session_opened", "handles": [panel.phone],
                  "speaker": {"role": "system"}, "occurred_at": NOW.isoformat()}  # fmt: skip
        d.batch("crm-interno", [opened])
        # Handoffs: the sales agent passes two customers to the closing queue; one buys, one declines.
        for c, outcome in ((CUSTOMERS[9], "venda_fechada"), (CUSTOMERS[14], "sem_interesse")):
            create = {"subject": c.phone, "agent": "vendas-whatsapp", "target": "queue:fechamento",
                      "reason": "quer fechar hoje", "level": "V1", "conversation_id": conversation_id(c.n, "vestido_festa")}  # fmt: skip
            handoff = d.call("POST", "/v1/handoffs", "vendas-whatsapp", create, 200, 201, once=uid("handoff", c.n))
            path = f"/v1/handoffs/{handoff['handoff_id']}"
            if handoff.get("status") not in ("closed",):
                d.call("POST", f"{path}/accept", "vendas-whatsapp", None, 200, 409, once=uid("accept", c.n))
                d.call("POST", f"{path}/outcome", "vendas-whatsapp", {"outcome": outcome}, 200, 409, once=uid("outcome", c.n))
        done.append("2 handoffs closed")
        # A second attempt whose result nobody knows waits for a decision, and a second agent is refused a customer
        # the voice agent holds: the conflicts the coordination screen counts.
        late = CUSTOMERS[7]
        unknown = {**effect, "subject": late.email, "effect_key": f"lembrete:{invoice_id(late.n)}"}
        d.call("POST", "/v1/coordination/check", "cobranca", unknown, 200)
        lost = {"kind": "effect", "subject": late.email, "agent": "cobranca",
                "detail": {"effect_key": unknown["effect_key"], "state": "unknown_outcome", "attempt": 1}}  # fmt: skip
        d.call("POST", "/v1/coordination/declare", "cobranca", lost, 200, once=uid("effect-unknown", late.n))
        rival = {"subject": CUSTOMERS[3].phone, "kind": "owner", "holder": "agent:vendas-whatsapp",
                 "level": "agent_active", "lease_s": 3600}  # fmt: skip
        d.call("POST", "/v1/coordination/claims", "vendas-whatsapp", rival, 201, 409, once=uid("rival", NOW.date()))
        if not d.call("GET", "/v1/coordination/reports", None, None, 200, params={"limit": 1}).get("items"):
            d.call("POST", "/v1/coordination/shadow", None, {"days": 30}, 200, 201, 202)
        return "; ".join(done)

    # Agent memory

    def agent_notes(self) -> str:
        d = self.d
        made = 0
        for agent, notes in AGENT_NOTES.items():
            source_id = d.source_ids[agent]
            d.call("DELETE", "/v1/agent-memory/notes", None, None, 200, params={"source_id": source_id})

            def ready(a: str = agent) -> bool:
                response = retried(lambda: d.api.get("/v1/agent-memory/block", headers=d.keys[a]))
                return response.status_code == 200 and response.json().get("enabled")

            d.until("agent memory on for the agent's key", ready)
            for kind, title, body, tags in notes:
                note = {"kind": kind, "title": title, "body": body, "tags": tags, "visibility": "space"}
                d.call("POST", "/v1/agent-memory/notes", agent, note, 201)
                made += 1
        # A note the billing agent distilled from a conversation waits for a person's approval.
        pending = d.call("GET", "/v1/agent-memory/proposals", None, None, 200, params={"status": "pending"})
        if not pending.get("items"):
            conv = next(conversation_id(n, s) for n, s, _ in ASSIGNMENT if s == "frete_duplicado")
            body = {"source_id": d.source_ids["cobranca"], "conversation_id": conv}
            d.call("POST", "/v1/agent-memory/distill", None, body, 200, 201)
        return f"{made} notes of 4 agents; a distilled proposal pending approval"

    # Outcomes: orders whose lines carry the token of the card the sales agent showed

    def outcomes(self) -> str:
        d = self.d
        items = []
        dress = [(n, days) for n, s, days in ASSIGNMENT if s == "vestido_festa"]
        for k, (n, days_ago) in enumerate(dress):
            c = CUSTOMERS[n - 1]
            order = ref("order", "erp", f"PED-9{n:04d}")
            pos = 1 + k % 2
            sku, _name, _color, price = ITEMS[pos - 1]
            line = {"line_id": "1", "ref": ref_text(item_ref(sku)), "amount": f"{price:.2f}", "quantity": 1,
                    "discount": "0.00" if k % 3 else "28.99", "exposure_token": exposure_token(exposure_of(c, "vestido_festa"), pos)}  # fmt: skip
            states = ["placed", "paid", "shipped", "delivered"] + (["kept"] if k % 4 == 0 else ["returned"] if k % 4 == 3 else [])
            for step, state in enumerate(states):
                items.append({
                    "type": "event", "kind": "system_event", "idempotency_key": f"demo-{order['id']}-{state}",
                    "channel": "erp", "canonical_type": f"order.{state}", "handles": [c.phone, c.crm],
                    "object_refs": [order], "speaker": {"role": "system"},
                    "fields": {"state": state, "total": f"{price + 19.9:.2f}", "lines": [line]},
                    "occurred_at": at(max(days_ago - 0.2 - step, 0.1)),
                })  # fmt: skip
        body = d.batch("erp", items)
        assert not body["errors"], body["errors"][:3]

        def attributed() -> int:
            page = d.call("GET", "/v1/measure/outcomes", None, None, 200, params={"limit": 200})
            return len(page["items"])

        found = d.until("the orders attributed to the cards", attributed)
        return f"{len(dress)} orders from the sales agent's cards; {found} attributed"

    # Data issues

    def data_issues(self) -> str:
        d = self.d
        same = {"type": "invoice", "fingerprint": CATALOG_FINGERPRINT}
        d.call("POST", "/v1/types/fingerprint", "erp", same, 200)
        changed = {"type": "invoice", "fingerprint": "sha256:" + hashlib.sha256(b"demo erp invoices, new column").hexdigest(),
                   "changes": {"fields_added": 1, "fields_removed": 0, "fields_retyped": 0, "states_added": 1,
                               "states_removed": 0, "relations_added": 0, "relations_removed": 0, "key_changed": False,
                               "fields": ["status"]}}  # fmt: skip
        drift = d.call("POST", "/v1/types/fingerprint", "erp", changed, 200)
        c = CUSTOMERS[2]
        undeclared = {"type": "event", "kind": "system_event", "idempotency_key": "demo-invoice-coupon-field",
                      "channel": "erp", "canonical_type": "invoice.updated", "handles": [c.main],
                      "object_refs": [ref("invoice", "erp", invoice_id(c.n))], "speaker": {"role": "system"},
                      "fields": {"state": "renegotiated", "codigo_desconto": "VOLTA10"}, "occurred_at": at(1)}  # fmt: skip
        d.batch("erp", [undeclared])
        # The platform starts sending a field the catalog type does not declare: drift by observation on a push.
        sku, name, _color, _price = ITEMS[0]
        self.push([{"ref": item_ref(sku), "version": 9, "fields": {"name": name, "material": "linho"}}])
        return f"drift on invoice ({'issue ' + drift['issue_id'][:8] if drift.get('issue_id') else 'none'}), an undeclared state and field"

    def agent_state(self) -> str:
        d = self.d
        written = 0
        for n, scenario, _days in ASSIGNMENT:
            if scenario != "vestido_festa":
                continue
            c = CUSTOMERS[n - 1]
            body = {"agent": "vendas-whatsapp", "scope": {"kind": "conversation", "id": conversation_id(n, scenario)},
                    "subject": c.phone, "mode": "merge_by_key",
                    "body": {"step": "checkout", "shortlist": [ITEMS[0][0], ITEMS[1][0]], "size_hint": "40"}}  # fmt: skip
            d.call("PUT", "/v1/agent-state", "vendas-whatsapp", body, 200)
            written += 1
        return f"{written} working states of the sales agent"

    # Identity by hand, privacy

    def profile_of(self, handle: dict[str, str]) -> str | None:
        found = self.d.call("POST", "/v1/profiles/search", None, {"query": handle["value"], "limit": 5}, 200)["items"]
        return found[0]["profile_id"] if found else None

    def merges(self) -> str:
        d = self.d
        # Thiago called once from an old number; the team merges the two profiles by hand.
        c = CUSTOMERS[3]
        old = {"type": "phone_e164", "value": "+5511900039904"}
        conv = "demo-ligacao-numero-antigo-04"
        call = [
            {"type": "event", "idempotency_key": f"{conv}-1", "channel": "voice", "conversation_id": conv, "handles": [old],
             "speaker": {"role": "customer"}, "direction": "inbound", "occurred_at": at(11),
             "content": {"type": "text", "text": "Oi, é o Thiago. Liguei de outro número, quero saber da minha troca."}},
            {"type": "conversation.ended", "idempotency_key": f"{conv}-end", "conversation_id": conv, "occurred_at": at(11, 3)},
        ]  # fmt: skip
        d.batch("sac-voz", call)
        first = d.until("the old number's profile", lambda: self.profile_of(old))
        main = d.until("the customer's profile", lambda: self.profile_of(c.phone))
        if first != main:
            d.call("POST", "/v1/identity/merge", None, {"profile_ids": [main, first], "force": False,
                   "reason": "Mesmo cliente: ligou de um número antigo"}, 200, 201, 202, 409, once=uid("merge", c.n))  # fmt: skip
        # A person states two of their handles belong together.
        r = CUSTOMERS[12]
        work = {"type": "email", "value": f"{slug(r.name)}@trabalho.{MAIL_DOMAIN}"}
        assertion = {"handles": [r.email, work], "method": "declared", "subject_kind": "person",
                     "reason": "A cliente informou o e-mail do trabalho"}  # fmt: skip
        d.call("POST", "/v1/identity/assertions", None, assertion, 200, 201, 202, 409, once=uid("assert", r.n))
        # People who look like customers call from another area code with another mailbox of the same name:
        # nobody joins them, and the daily suggester puts each pair up for review (the graph's dashed edges).
        for c, area, mailbox in LOOK_ALIKES:
            twin = [{"type": "phone_e164", "value": c.phone["value"].replace("+5511", area, 1)},
                    {"type": "email", "value": f"{slug(c.name)}@{mailbox}.{MAIL_DOMAIN}"}]
            conv = f"demo-ligacao-parecida-{c.n:02d}"
            call = [
                {"type": "event", "idempotency_key": f"{conv}-1", "channel": "voice", "conversation_id": conv,
                 "handles": twin, "speaker": {"role": "customer"}, "direction": "inbound", "occurred_at": at(6),
                 "content": {"type": "text", "text": "Oi, queria saber se o meu pedido já saiu."}},
                {"type": "conversation.ended", "idempotency_key": f"{conv}-end", "conversation_id": conv,
                 "occurred_at": at(6, 2)},
            ]  # fmt: skip
            d.batch("sac-voz", call)
        return f"two profiles merged by hand, one assertion declared, {len(LOOK_ALIKES)} look-alikes for the suggester"

    def privacy(self) -> str:
        d = self.d
        held = conversation_id(CUSTOMERS[4].n, next(s for n, s, _ in ASSIGNMENT if n == CUSTOMERS[4].n))
        hold = {"conversation_id": held, "reason": "Reclamação no Procon em andamento"}
        d.call("POST", "/v1/legal-holds", None, hold, 200, 201, 409, once=uid("hold", held))
        forget = {"target": "handle", "handle": FORGOTTEN.phone}
        erasure = d.call("POST", "/v1/forget", None, forget, 200, 202, once=uid("forget", FORGOTTEN.n))
        # The portable file of one customer's data, as the data subject's right of access asks.
        d.call("POST", "/v1/export", None, {"handle": CUSTOMERS[2].phone}, 200, 201, 202)
        return (f"legal hold on one conversation; erasure {erasure.get('status')} for one customer's own request; "
                "one customer's data file")

    # Today: agents reading context before they answer, which the usage and measurement screens count

    def live(self) -> str:
        d = self.d
        read = 0
        turns = []
        for c in CUSTOMERS[:24]:
            channel = c.channels[0]
            agent = {"w": "vendas-whatsapp", "v": "sac-voz", "e": "cobranca", "a": "vendas-whatsapp"}[channel]
            conv = f"demo-hoje-{c.n:02d}-{NOW.date().isoformat()}"
            handle = c.handle(channel)
            name = {"w": "whatsapp", "v": "voice", "e": "email", "a": "app"}[channel]
            hello = {"type": "event", "idempotency_key": f"{conv}-1", "channel": name, "conversation_id": conv,
                     "handles": [handle], "speaker": {"role": "customer"}, "direction": "inbound",
                     "verification_hint": "V1", "occurred_at": NOW.isoformat(),
                     "content": {"type": "text", "text": "Oi, voltei. Alguma novidade do meu pedido?"}}  # fmt: skip
            d.batch(agent, [hello])
            ask = {"subject": handle, "conversation_id": conv, "verification": "V1", "include": ["constraints", "state"]}
            context = d.call("POST", "/v1/context", agent, ask, 200)
            read += 1
            reply = (f"Oi, {c.first}! O pedido {order_id(c.n)} está a caminho. "
                     "Lembrei que você prefere tons claros e entregas à tarde.")  # fmt: skip
            answer = {"type": "event", "idempotency_key": f"{conv}-2", "channel": name, "conversation_id": conv,
                      "handles": [handle], "speaker": {"role": "ai_agent", "id": agent}, "direction": "outbound",
                      "occurred_at": (NOW + timedelta(seconds=20)).isoformat(), "content": {"type": "text", "text": reply},
                      "context_stamp": {"etag": context.get("etag"), "injected_at": NOW.isoformat()},
                      "usage": {"provider": "openai", "model": MODEL, "prompt_tokens": 3100, "cached_tokens": 1800},
                      "backing": {"checked": 1, "unbacked_values": []}}  # fmt: skip
            end = {"type": "conversation.ended", "idempotency_key": f"{conv}-end", "conversation_id": conv,
                   "occurred_at": (NOW + timedelta(seconds=40)).isoformat()}  # fmt: skip
            d.batch(agent, [answer, end])
            if agent == "vendas-whatsapp":
                # The search ran with the customer's hard constraints: what was sent, checked and violated.
                block = context.get("constraints") or {}
                turns.append({
                    "turn_id": f"{conv}-t2", "conversation_id": conv, "agent": {"name": agent},
                    "started_at": NOW.isoformat(), "content_mode": "hash_only",
                    "build": {"pins": {"prompts": {"core": "v15"}, "model": MODEL}, "sdk": "niadra-python/0.9.0"},
                    "reads": [{"surface": "pack", "etag": context.get("etag") or "none"}],
                    "calls": [{"call_id": "k1", "kind": "tool", "name": "buscar_produtos", "latency_ms": 410,
                               "applied": {"constraints": block.get("version") or "cv_none",
                                           "hard_sent": ["item_variant.color"], "results_checked": 8,
                                           "violations": c.n % 5 == 0, "unverifiable": 1}}],
                    "output": {"event_keys": [f"{conv}-2"]},
                })  # fmt: skip
        if turns:
            d.turns("vendas-whatsapp", turns)
        # The store's platform tells what happened in the app: carts the customers left.
        carts = [{"type": "event", "kind": "system_event", "idempotency_key": f"demo-cart-{c.n}-{NOW.date()}",
                  "channel": "app", "canonical_type": "cart.abandoned", "handles": [c.app], "speaker": {"role": "system"},
                  "fields": {"items": 1 + c.n % 3, "total": f"{129.9 + c.n:.2f}"}, "occurred_at": NOW.isoformat()}
                 for c in CUSTOMERS if "a" in c.channels]  # fmt: skip
        d.batch("plataforma-loja", carts)
        return f"{read} conversations today read their context before answering; {len(carts)} carts from the app"

    def review(self) -> str:
        """A reviewer's verdicts on the sampled conversations, so the agreement and the test cases have numbers."""
        d = self.d
        samples = d.call("GET", "/v1/review/queue", None, None, 200, params={"limit": 10})["items"]
        judged = 0
        for k, sample in enumerate(samples[:6]):
            verdicts = [{"target": "fact", "target_id": f["fact_id"], "verdict": "wrong" if (k + i) % 5 == 4 else "correct"}
                        for i, f in enumerate(sample.get("facts") or [])]
            verdicts += [{"target": "open_item", "target_id": o["item_id"], "verdict": "correct"}
                         for o in sample.get("open_items") or []]
            verdicts += [{"target": "trait", "target_id": t["trait_id"], "verdict": "holds"}
                         for t in sample.get("traits") or []]
            if verdicts:
                d.call("POST", f"/v1/review/{sample['sample_id']}/verdicts", None, {"verdicts": verdicts}, 200, 201)
                judged += len(verdicts)
        return f"{judged} verdicts on {min(len(samples), 6)} samples; the rest of the queue waits for a person"


def main() -> int:
    run = Run()
    person = demo_admin()
    projects = person.get("/v1/projects")
    project = next((p for p in projects if p["slug"] == PROJECT["slug"]), None)
    if project is None:
        region = projects[0]["region"] if projects else REGION
        project = person.post("/v1/projects", {**PROJECT, "region": region}, 201)
    space = next(s for s in project["spaces"] if s["environment"] == "sandbox")
    person.to_space(space["space_id"])
    print(f"PASS  project {project['name']}, sandbox space {space['slug']} ({space['space_id']}), as {person.email}")
    seed = Seeder(Demo(person, space["space_id"], project))
    only = set(sys.argv[1:])
    steps = [
        ("sources and keys", seed.sources),
        ("configuration", seed.configuration),
        ("coordination and agent memory", seed.coordination),
        ("webhooks and warnings", seed.warnings),
        ("keys ready", seed.keys_ready),
        ("identities", seed.identities),
        ("companies", seed.companies),
        ("conversations", seed.conversations),
        ("orders", seed.orders),
        ("invoices", seed.invoices),
        ("tickets", seed.tickets),
        ("turns", seed.turns),
        ("shared objects", seed.shared),
        ("quotes", seed.quotes),
        ("coordination flows", seed.coordination_flows),
        ("agent notes", seed.agent_notes),
        ("outcomes", seed.outcomes),
        ("data issues", seed.data_issues),
        ("agent state", seed.agent_state),
        ("merges", seed.merges),
        ("privacy", seed.privacy),
        ("today", seed.live),
        ("review", seed.review),
    ]
    setup = {"sources and keys", "keys ready"}
    for name, step in steps:
        if only and name not in only and name not in setup:
            continue
        run.step(name, step)
    print(f"\n{run.failed} failed step(s)")
    return run.failed


if __name__ == "__main__":
    sys.exit(main())
