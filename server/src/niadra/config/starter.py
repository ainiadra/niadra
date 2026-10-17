"""The configuration a new space starts with, before its own schema exists (5.7, 22.4).

Neutral on purpose: facts any business with customers keeps. The configuration assistant proposes
the tenant's own predicates from its transcripts; once approved they replace these, never merge.
Every predicate category has a policy category, because the policy denies what it never declared.

Access is released by purpose from the first day (5.12): billing to billing and support agents,
technical matters to support, and health, sensitive by law, only to care agents at V3. No starter
predicate collects health data; a conversation about it is still filed under `health`, so its
episode, the promises made in it and the summaries that quote it stay away from everyone else.

The mirror rule is on for every category but health: from V1 the customer hears back what they told the
company themselves (the dispute they wrote about, the postal code they gave), while what the company holds
about them (a balance it stated, a record from its systems) keeps the category's level. A space turns it
off per category (`mirror: false`), for instance a bank that wants V2 even for what the customer said.
"""

from niadra.config.claims import ClaimContract
from niadra.config.models import (
    CategoryRule,
    EpisodeCategory,
    EventAnchor,
    ExtractionSchema,
    Policy,
    PolicyCondition,
    PolicyRule,
    PredicateSpec,
    PurposeSettings,
    PurposeSpec,
    SynonymGroup,
)
from niadra.contracts.claims import (
    Actions,
    ClaimCategory,
    ClaimEvidence,
    Detect,
    NegativeCorpus,
    ValueEvidence,
)
from niadra.domain.privacy.purposes import LegalBasis
from niadra.domain.vocabulary import Audience, Sensitivity, Verification

STARTER_VERSION = "starter-2"
STARTER_POLICY_VERSION = "starter-3"
"""The starter policy's own version: the mirror rule changed what it releases, not the extraction schema,
so the extraction keeps `STARTER_VERSION` and no conversation is extracted again for it."""

STARTER_POLICY = Policy(
    version=STARTER_POLICY_VERSION,
    categories=[
        CategoryRule(name="contact", min_verification=Verification.V1, mirror=True),
        CategoryRule(name="preferences", min_verification=Verification.V1, mirror=True),
        CategoryRule(name="relationship", min_verification=Verification.V1, mirror=True),
        CategoryRule(name="orders", min_verification=Verification.V1, mirror=True),
        CategoryRule(name="personal", min_verification=Verification.V2, mirror=True),
        CategoryRule(
            name="billing",
            min_verification=Verification.V2,
            purposes=frozenset({"billing", "support"}),
            no_customer_allowed=True,
            mirror=True,
        ),
        CategoryRule(
            name="technical",
            min_verification=Verification.V1,
            purposes=frozenset({"support", "technical"}),
            mirror=True,
        ),
        CategoryRule(
            name="health",
            sensitivity=Sensitivity.SENSITIVE,
            min_verification=Verification.V3,
            purposes=frozenset({"care"}),
        ),
    ],
    rules=[
        PolicyRule(
            effect="allow",
            when=PolicyCondition(category="health", purpose="care", min_verification=Verification.V3),
        ),
        # People reading the base for analysis never see health, whatever purposes the space declares.
        PolicyRule(effect="deny", when=PolicyCondition(category="health", audience=Audience.ANALYST)),
    ],
)

STARTER_EXTRACTION = ExtractionSchema(
    version=STARTER_VERSION,
    categories=[
        CategoryRule(name=c.name, sensitivity=c.sensitivity, min_verification=c.min_verification)
        for c in STARTER_POLICY.categories
    ],
    episode_categories=[
        EpisodeCategory(name=name, normalized_as=name)
        for name in ("orders", "billing", "technical", "account", "health", "other")
    ],
    predicates=[
        PredicateSpec(name="preferred_name", category="contact", single_valued=True),
        PredicateSpec(
            name="preferred_channel",
            category="preferences",
            single_valued=True,
            enum=["whatsapp", "voice", "email", "sms", "chat"],
        ),
        PredicateSpec(name="preferred_contact_time", category="preferences", single_valued=True),
        PredicateSpec(name="preferred_resolution", category="preferences"),
        PredicateSpec(name="product_or_service_used", category="relationship"),
        PredicateSpec(name="order_problem", category="orders"),
        PredicateSpec(name="billing_issue", category="billing"),
        PredicateSpec(name="technical_issue", category="technical"),
        PredicateSpec(name="important_date", category="personal"),
    ],
)

# The system events a customer names in a question, by the canonical types spaces and the documentation use
# for them. A space renames or adds its own in `event_anchors`, or on the mapping rule of the event.
STARTER_ANCHORS = [
    EventAnchor(
        name="inspection",
        canonical_types=["inspection.completed", "inspection.done", "vehicle_inspection.completed"],
        terms={
            "pt": ["vistoria", "inspeção", "perícia"],
            "en": ["inspection", "vehicle inspection", "survey"],
            "es": ["inspección", "peritaje", "peritación"],
        },
    ),
    EventAnchor(
        name="delivery",
        canonical_types=[
            "order.delivered",
            "shipment.delivered",
            "delivery.completed",
            "package.delivered",
            "parcel.delivered",
        ],
        terms={
            "pt": ["entrega", "chegada do pedido", "recebimento do pedido", "pedido chegar", "pedido chegou"],
            "en": ["delivery", "order was delivered", "order arrived", "package arrived", "parcel arrived"],
            "es": ["entrega", "llegada del pedido", "recepción del pedido", "pedido llegó"],
        },
    ),
    EventAnchor(
        name="pickup",
        canonical_types=["pickup.completed", "shipment.picked_up", "collection.completed", "pickup.done"],
        terms={
            "pt": ["coleta", "retirada"],
            "en": ["pickup", "pick-up", "collection"],
            "es": ["recogida", "recolección", "retiro"],
        },
    ),
    EventAnchor(
        name="card_replacement",
        canonical_types=["card.replaced", "card.reissued", "card.issued"],
        terms={
            "pt": [
                "troca do cartão",
                "cartão novo",
                "novo cartão",
                "emissão do cartão",
                "reemissão do cartão",
                "segunda via do cartão",
                "substituição do cartão",
            ],
            "en": [
                "new card",
                "replacement card",
                "card replacement",
                "card was replaced",
                "card was reissued",
                "card reissue",
            ],
            "es": [
                "tarjeta nueva",
                "nueva tarjeta",
                "cambio de tarjeta",
                "reposición de la tarjeta",
                "emisión de la tarjeta",
                "reemisión de la tarjeta",
            ],
        },
    ),
    EventAnchor(
        name="technician_visit",
        canonical_types=["field_visit.completed", "technician_visit.completed", "site_visit.completed"],
        terms={
            "pt": ["visita técnica", "visita do técnico", "técnico", "visita"],
            "en": [
                "technician visit",
                "technical visit",
                "site visit",
                "engineer visit",
                "technician",
                "visit",
            ],
            "es": ["visita técnica", "visita del técnico", "técnico", "visita"],
        },
    ),
    EventAnchor(
        name="installation",
        canonical_types=["installation.completed", "install.completed", "service.installed"],
        terms={
            "pt": ["instalação"],
            "en": ["installation", "install"],
            "es": ["instalación"],
        },
    ),
    EventAnchor(
        name="invoice",
        canonical_types=[
            "invoice.issued",
            "invoice.created",
            "bill.issued",
            "charge.created",
            "invoice.charged",
        ],
        terms={
            "pt": ["fatura", "cobrança", "boleto"],
            "en": ["invoice", "bill", "charge", "billing"],
            "es": ["factura", "cobro", "recibo"],
        },
    ),
    EventAnchor(
        name="payment",
        canonical_types=[
            "payment.received",
            "payment.completed",
            "payment.confirmed",
            "invoice.paid",
            "bill.paid",
        ],
        terms={
            "pt": ["pagamento", "paguei"],
            "en": ["payment", "paid", "paying"],
            "es": ["pago", "pagué"],
        },
    ),
]


def _group(name: str, pt: str, en: str, es: str, categories: tuple[str, ...] = ()) -> SynonymGroup:
    """One group from its terms by language, each list comma separated."""
    return SynonymGroup(
        name=name,
        terms={
            lang: [t.strip() for t in terms.split(",")]
            for lang, terms in (("pt", pt), ("en", en), ("es", es))
        },
        categories=list(categories),
    )


# Words customers of the five starter domains (telecom, insurance, banking, retail, logistics) use for
# the same thing, so a question in other words finds what they said (the lexical slot channel and the
# count line). Inflections the stemmer does not join are listed. A space edits these in `synonyms`.
STARTER_SYNONYMS = [
    _group(
        "identifier",
        "código, identificação, identificador, número de série, serial, referência, id",
        "code, identifier, identification, serial, serial number, reference, id",
        "código, identificación, identificador, número de serie, referencia",
    ),
    _group(
        "access_code",
        "senha, código de acesso, código de liberação, código, pin, chave de acesso",
        "password, passcode, access code, entry code, gate code, door code, code, pin",
        "contraseña, clave, código de acceso, código, pin",
    ),
    _group(
        "last_digits",
        "final, terminação, termina em, terminado em, últimos dígitos, últimos números",
        "ends in, ending, ending in, last digits, last four, final digits, last numbers",
        "terminación, termina en, terminada en, últimos dígitos, final",
    ),
    _group(
        "price",
        "preço, valor, custo, custou, custa, quanto, orçamento, cotação",
        "price, cost, costs, amount, how much, quote, quoted, estimate",
        "precio, costo, coste, costó, cuánto, monto, presupuesto, cotización",
    ),
    _group(
        "printed",
        "impresso, impressa, aparece, escrito, escrita, consta, mostra",
        "printed, written, shown, shows, says, on it",
        "impreso, impresa, aparece, escrito, figura",
    ),
    _group(
        "label",
        "etiqueta, rótulo, adesivo",
        "label, sticker, tag",
        "etiqueta, rótulo, pegatina, adhesivo",
    ),
    _group(
        "seal",
        "lacre, selo, selo de segurança, etiqueta de segurança",
        "seal, security tag, tamper seal, security seal, tag",
        "precinto, sello, marchamo, precinto de seguridad",
    ),
    _group(
        "package",
        "caixa, embalagem, pacote, volume, encomenda, pacotinho",
        "box, package, parcel, packaging, carton",
        "caja, paquete, embalaje, bulto, encomienda",
        ("orders",),
    ),
    _group(
        "cargo",
        "carga, contêiner, container, conteiner, mercadoria, lote",
        "load, cargo, container, freight load, goods",
        "carga, contenedor, mercancía, lote",
        ("orders",),
    ),
    _group(
        "delivery",
        "entrega, entregar, entregou, entregue, entregaram, entregador, entregadora, envio",
        "delivery, deliver, delivered, deliveries, courier, drop-off, shipment",
        "entrega, entregar, entregado, entregaron, repartidor, mensajero, envío",
        ("orders",),
    ),
    _group(
        "order",
        "pedido, compra, encomenda",
        "order, purchase",
        "pedido, orden, compra",
        ("orders",),
    ),
    _group(
        "item",
        "item, itens, produto, peça, artigo, mercadoria",
        "item, product, part, article, goods",
        "artículo, producto, pieza, mercancía",
        ("orders",),
    ),
    _group(
        "missing",
        "faltando, faltou, falta, faltar, faltaram, incompleto, incompleta, sem um dos, não veio",
        "missing, incomplete, lacking, left out, without one, short one",
        "faltante, falta, faltó, faltaba, incompleto, incompleta, sin uno de",
        ("orders",),
    ),
    _group(
        "failed",
        (
            "falhou, falha, falhar, não foi feita, não foi feito, não realizada, não realizado, "
            "não conseguiu, sem ninguém vir, ninguém veio, ninguém apareceu, não apareceu"
        ),
        (
            "failed, fail, failure, not made, was not made, could not deliver, nobody came, no one came, did "
            "not show, no show, not completed"
        ),
        "falló, fallida, fallido, no se realizó, no pudo, nadie vino, no apareció",
    ),
    _group(
        "attempt",
        "tentativa, tentaram, tentou",
        "attempt, attempted, tried",
        "intento, intentaron, intentó",
    ),
    _group(
        "damaged",
        (
            "defeito, defeituoso, defeituosa, quebrado, quebrada, quebrou, estragado, estragada, danificado, "
            "danificada, avariado, avariada, amassado, amassada, rasgado, rasgada, dá problema, com problema"
        ),
        "defective, faulty, broken, damaged, crushed, torn, malfunctioning, not working",
        "defectuoso, defectuosa, roto, rota, dañado, dañada, averiado, estropeado",
    ),
    _group(
        "exchange",
        "troca, trocar, devolução, devolver",
        "exchange, return, swap",
        "cambio, cambiar, devolución, devolver",
        ("orders",),
    ),
    _group(
        "tracking",
        "rastreio, rastreamento, código de rastreio",
        "tracking, tracking number",
        "seguimiento, rastreo, número de seguimiento",
        ("orders",),
    ),
    _group(
        "freight",
        "frete, transporte, remessa",
        "shipping, freight, transport, carriage, shipping fee",
        "flete, transporte, porte",
        ("orders",),
    ),
    _group(
        "assembly",
        "montagem, montar, montou, instalar, instalação, instalou",
        "assembly, assemble, install, installation, set up",
        "montaje, montar, instalación, instalar",
    ),
    _group(
        "furniture",
        "guarda-roupa, guarda roupa, armário, roupeiro, móvel",
        "wardrobe, closet, cabinet, cupboard, furniture",
        "armario, ropero, guardarropa, mueble",
    ),
    _group(
        "store",
        "loja, filial",
        "store, shop, branch",
        "tienda, sucursal",
    ),
    _group(
        "coupon",
        "cupom, desconto, promoção",
        "coupon, discount, promo, voucher",
        "cupón, descuento, promoción",
    ),
    _group(
        "bill",
        "boleto, fatura, conta de cobrança, guia de pagamento, nota fiscal",
        "bill, invoice, payment slip, slip, statement",
        "factura, boleta, recibo, cuenta de cobro",
        ("billing",),
    ),
    _group(
        "paid",
        "pago, paga, paguei, pagou, pagar, pagamento, quitado, quitada, quitei, quitar",
        "paid, pay, payment, settled, settle",
        "pagado, pagada, pagué, pagar, pago, liquidado, saldado",
        ("billing",),
    ),
    _group(
        "duplicate",
        "duplicidade, duplicado, duplicada, em dobro, dobro, duas vezes, repetido, repetida",
        "duplicate, duplicated, twice, double, second time, two times",
        "duplicado, duplicada, dos veces, doble, repetido",
        ("billing",),
    ),
    _group(
        "charge",
        "cobrança, cobraram, cobrado, cobrada, cobrar, tarifa, taxa",
        "charge, charged, fee",
        "cobro, cobraron, cobrado, cargo, tarifa, comisión",
        ("billing",),
    ),
    _group(
        "improper",
        "indevido, indevida, errado, errada, incorreto, incorreta, a mais, não contratei",
        "wrong, improper, incorrect, extra amount, never ordered, overcharged",
        "indebido, indebida, incorrecto, erróneo, de más",
        ("billing",),
    ),
    _group(
        "expense",
        "compra, gasto, despesa, transação, lançamento",
        "purchase, expense, transaction, spending",
        "compra, gasto, transacción, movimiento",
        ("billing",),
    ),
    _group(
        "unrecognized",
        "não reconheço, desconhecido, desconhecida, estranho, estranha, não fiz, não reconhecida",
        "unknown, don't recognize, do not recognize, unrecognized, unfamiliar, not mine",
        "desconocido, desconocida, no reconozco, extraño, no hice",
        ("billing",),
    ),
    _group(
        "card",
        "cartão, cartões, cartão de crédito, cartão de débito",
        "card, credit card, debit card",
        "tarjeta, tarjeta de crédito, tarjeta de débito",
        ("billing",),
    ),
    _group(
        "declined",
        "recusado, recusada, recusou, negado, negada, não passou, não aprovado, não aprovada",
        "declined, denied, rejected, refused, did not go through, didn't go through, not approved",
        "rechazado, rechazada, denegado, denegada, declinado, no pasó",
        ("billing",),
    ),
    _group(
        "refund",
        "reembolso, estorno, ressarcimento, devolução do dinheiro",
        "refund, reimbursement, chargeback, money back",
        "reembolso, reintegro, devolución del dinero",
        ("billing",),
    ),
    _group(
        "transfer",
        "transferência, pix, ted",
        "transfer, wire",
        "transferencia",
        ("billing",),
    ),
    _group(
        "due_date",
        "vencimento, data de vencimento",
        "due date",
        "vencimiento, fecha de vencimiento",
        ("billing",),
    ),
    _group(
        "connectivity",
        "internet, conexão, sinal, wi-fi, wifi, rede, fibra, banda larga",
        "internet, connection, signal, wi-fi, wifi, network, broadband, fiber, fibre",
        "internet, conexión, señal, red, fibra",
        ("technical",),
    ),
    _group(
        "outage",
        (
            "caiu, cair, caindo, cai, caída, queda, fora do ar, sem internet, sem sinal, sem conexão, fiquei "
            "sem, desconectando, instável, oscilando"
        ),
        (
            "dropped, drop, dropping, went down, is down, was down, outage, offline, lost internet, lost "
            "connection, lost signal, loses signal, cut out, disconnected, unstable"
        ),
        "cayó, caída, cae, cayendo, sin internet, sin señal, sin conexión, se corta, inestable",
        ("technical",),
    ),
    _group(
        "slow",
        "lenta, lento, lentidão, velocidade",
        "slow, speed, sluggish",
        "lenta, lento, lentitud, velocidad",
        ("technical",),
    ),
    _group(
        "device",
        "aparelho, equipamento, dispositivo, decodificador, modem, roteador, receptor",
        "device, equipment, unit, set-top box, decoder, modem, router, receiver",
        "aparato, equipo, dispositivo, decodificador, módem, enrutador, receptor",
        ("technical",),
    ),
    _group(
        "app",
        "aplicativo, app",
        "app, application",
        "aplicación, app",
        ("technical",),
    ),
    _group(
        "crash",
        "travou, trava, travando, fechou sozinho, fechar sozinho, congelou, não abre",
        "froze, frozen, freezes, crash, crashed, crashes, kept closing, won't open",
        "se congeló, se colgó, se cierra, se cerró, no abre",
        ("technical",),
    ),
    _group(
        "login",
        "entrar, login, logar, acessar, acesso",
        "log in, login, sign in, access",
        "iniciar sesión, entrar, acceder, acceso",
        ("account",),
    ),
    _group(
        "technician",
        "técnico, instalador, reparador, montador",
        "technician, installer, tech, engineer, repairman, fitter",
        "técnico, instalador, reparador",
        ("technical",),
    ),
    _group(
        "doorman",
        "portaria, porteiro, recepção, guarita, zelador",
        "doorman, front desk, concierge, reception, lobby, porter",
        "portería, portero, conserje, recepción",
    ),
    _group(
        "plan",
        "plano, pacote de dados, franquia",
        "plan, data plan, allowance",
        "plan, paquete de datos",
    ),
    _group(
        "vehicle",
        "carro, automóvel, veículo, auto, moto",
        "car, vehicle, automobile, auto",
        "coche, carro, vehículo, automóvil, auto",
    ),
    _group(
        "rental",
        "aluguel, locação, locadora, alugado, alugar",
        "rental, rent, rented, hire, hired",
        "alquiler, alquilar, alquilado, arrendamiento",
    ),
    _group(
        "replacement",
        "reserva, substituto, substituta, substituição, provisório, temporário",
        "replacement, loaner, courtesy car, temporary, substitute, spare",
        "sustituto, reemplazo, provisional, de cortesía",
    ),
    _group(
        "repair",
        "conserto, consertar, consertou, reparo, reparar, arrumar, arrumou",
        "repair, repaired, fix, fixed, mend",
        "reparación, reparar, arreglar, arreglo",
    ),
    _group(
        "garage",
        "oficina, mecânico, funilaria",
        "garage, workshop, mechanic, body shop, repair shop",
        "taller, mecánico",
    ),
    _group(
        "tow",
        "guincho, reboque",
        "tow, tow truck, towing, wrecker",
        "grúa, remolque",
    ),
    _group(
        "late",
        (
            "atrasado, atrasada, atrasou, atraso, atrasar, demorou, demora, demorando, "
            "não chegou no horário, fora do prazo, adiou, adiado, adiada"
        ),
        (
            "late, delay, delayed, took too long, too long, did not arrive on time, not on time, behind "
            "schedule, pushed back, postponed"
        ),
        "atrasado, atrasada, retraso, retrasó, demoró, tardó, llegó tarde, pospuso",
    ),
    _group(
        "claim",
        "sinistro, acionamento",
        "claim",
        "siniestro",
    ),
    _group(
        "insurance_policy",
        "apólice, seguro, cobertura",
        "policy, insurance, coverage",
        "póliza, seguro, cobertura",
    ),
    _group(
        "protocol",
        "protocolo, número de atendimento, número do atendimento",
        "protocol, case number, reference number, ticket number",
        "protocolo, número de caso",
    ),
    _group(
        "ticket",
        "chamado, ocorrência, incidente, solicitação",
        "ticket, incident",
        "ticket, incidencia, caso, solicitud",
    ),
    _group(
        "cancel",
        "cancelar, cancelamento, cancelei, encerrar, encerramento",
        "cancel, cancellation, cancelled, canceled, terminate",
        "cancelar, cancelación, dar de baja, baja",
    ),
    _group(
        "address",
        "endereço, cep, rua, logradouro",
        "address, zip, zip code, street, postcode",
        "dirección, código postal, domicilio",
    ),
    _group(
        "phone",
        "telefone, celular, número de telefone",
        "phone, mobile, cell phone, phone number",
        "teléfono, celular, móvil",
    ),
    _group(
        "visit",
        "visita, agendamento, horário marcado",
        "visit, appointment, scheduled visit",
        "visita, cita",
    ),
]


# Zero touch: what a space nobody configured runs with. Every document below is a default a
# declaration replaces, never a task the customer must do before the memory works.

DEFAULT_PURPOSE = "service"
STARTER_PURPOSES_VERSION = "starter-1"
STARTER_PURPOSES = PurposeSettings(
    version=STARTER_PURPOSES_VERSION,
    purposes=[
        PurposeSpec(
            name=DEFAULT_PURPOSE,
            legal_basis=LegalBasis.CONTRACT,
            retention_days=730,
            description="Serving the company's own customers under the contract with them: the default "
            "until the company declares its purposes.",
        )
    ],
    default=[DEFAULT_PURPOSE],
)
"""The purposes a space starts with: serving its customers, on the contract basis, kept two years. The
legal basis stays the company's decision: declaring its own purposes replaces this document."""

DEFAULT_SOURCE_PURPOSES = frozenset({DEFAULT_PURPOSE, "support"})
"""What a source that declares no purposes reads for: serving the customer (the default purpose) and
support, so the starter policy's billing and technical categories reach it. Health stays `care` only."""

STARTER_CLAIM_CONTRACT = ClaimContract(
    version="starter-1",
    languages=["pt", "en", "es"],
    categories=[
        ClaimCategory(
            id="date",
            detect=Detect(classes=["date"]),
            evidence=ClaimEvidence(value=ValueEvidence()),
            actions=Actions(default="count"),
        ),
        ClaimCategory(
            id="money",
            detect=Detect(classes=["money"]),
            evidence=ClaimEvidence(value=ValueEvidence()),
            actions=Actions(default="count"),
        ),
    ],
    negative_corpus=NegativeCorpus(
        version="starter-1",
        phrases=[
            "Posso ajudar em mais alguma coisa?",
            "Obrigado pelo contato, tenha um bom dia.",
            "How can I help you today?",
            "Thank you for reaching out.",
            "¿En qué más puedo ayudarte?",
            "Gracias por tu mensaje.",
        ],
    ),
)
"""A generic contract: a date or an amount an agent states is checked against the values in the turn
record and counted, never blocked. A company that wants a claim blocked declares its own categories."""
