"""Categories the law calls sensitive (LGPD art. 5, II and art. 11; GDPR art. 9), fixed by the product.

A category with one of these names is sensitive whatever the tenant's policy says, and a category the
policy never declared is still recognized by its name, in English, Portuguese or Spanish, so a model
that files a conversation under "saude" or "autorizacao_de_exame" cannot turn health data into
ordinary data.

Raw text that no extraction has filed yet (the `live` turns) is read against word lists in the three
languages the product serves. The lists are written unaccented, since matching runs on folded text; a
`*` at the end of a word marks a stem. Words that are ordinary in customer service stay out
("transtorno" is also a hassle, "greve" is the postal strike that delayed an order, "raça" is a dog's
breed, "disabled" is an account), because a sensitive label keeps the line from every agent the policy
does not release it to. So do the products a company sells and where they are served: a health plan, a health
insurance and a network hospital are what a broker quotes, not anyone's health; what the person says of
their own (a diagnosis, a treatment, a stay in hospital) keeps its words.
"""

import re
import unicodedata
from collections.abc import Iterable
from functools import cache

PRODUCT_SENSITIVE_CATEGORIES = frozenset(
    {
        "health",
        "religion",
        "philosophical_belief",
        "political_opinion",
        "union_membership",
        "sexual_life",
        "sexual_orientation",
        "racial_or_ethnic_origin",
        "genetic",
        "biometric",
        "criminal_record",
    }
)

# Other names a tenant or a model may give the same categories, in the three languages the product
# serves. "raca" stays out: in a pet shop it is the dog's breed; "etnia" and "origem_racial" do not.
_ALIASES = frozenset(
    {
        # en
        "medical",
        "clinical",
        "politics",
        "political",
        "trade_union",
        "sex_life",
        "sexuality",
        "ethnicity",
        "ethnic_origin",
        "racial_origin",
        "genetics",
        "biometrics",
        "criminal_history",
        "religious_belief",
        "philosophical_beliefs",
        # pt
        "saude",
        "medico",
        "medica",
        "clinico",
        "clinica",
        "religiao",
        "crenca_religiosa",
        "conviccao_religiosa",
        "conviccao_filosofica",
        "crenca_filosofica",
        "politica",
        "opiniao_politica",
        "filiacao_partidaria",
        "sindicato",
        "filiacao_sindical",
        "vida_sexual",
        "orientacao_sexual",
        "sexualidade",
        "origem_racial",
        "origem_etnica",
        "origem_racial_ou_etnica",
        "etnia",
        "genetica",
        "dados_geneticos",
        "biometria",
        "dados_biometricos",
        "antecedentes_criminais",
        "antecedente_criminal",
        # es
        "salud",
        "creencia_religiosa",
        "conviccion_filosofica",
        "opinion_politica",
        "afiliacion_politica",
        "afiliacion_sindical",
        "orientacion_sexual",
        "origen_racial",
        "origen_etnico",
        "datos_geneticos",
        "datos_biometricos",
        "antecedentes_penales",
        "antecedentes_criminales",
    }
)

# fmt: off
SENSITIVE_TERMS: dict[str, tuple[str, ...]] = {
    "health": (
        # pt
        "saude mental", "problema de saude", "problemas de saude", "estado de saude", "minha saude",
        "diabet*", "hipertens*", "pressao alta", "cancer", "tumor", "tumores", "quimioterapia", "quimio",
        "radioterapia", "hiv", "aids", "soropositiv*", "depressao", "depressiv*", "crise de ansiedade",
        "transtorno de ansiedade", "ansiedade generalizada", "transtorno bipolar", "transtorno mental",
        "transtornos mentais", "sindrome do panico", "esquizofreni*", "bipolar", "autis*", "tdah",
        "epilepsia", "gravida", "gravidez", "gestante", "gestacao", "aborto", "parto", "internad*",
        "internacao", "uti", "cirurgia*", "cirurgic*", "laudo medico", "receita medica", "remedio controlado",
        "remedios controlados", "medicamento*", "antidepressiv*", "insulina", "dialise", "hemodialise",
        "doenca*", "doente*", "pessoa com deficiencia", "deficiencia fisica", "deficiencia visual",
        "deficiencia auditiva", "deficiencia intelectual", "deficiente fisico", "deficiente visual",
        "cadeirante", "psiquiatr*", "psicolog*", "terapia", "terapeuta", "tratamento medico",
        "tratamento de saude", "atestado medico", "covid", "hepatite", "tuberculose", "alzheimer",
        "parkinson", "avc", "infarto", "exame de sangue", "exames de sangue", "exame medico",
        "exames medicos", "exame laboratorial", "exames laboratoriais", "resultado do exame",
        "resultado dos exames", "autorizacao de exame*", "consulta medica", "medico", "medica",
        "hospitalizad*", "pronto socorro", "alergi*", "asma", "obesidade", "bariatric*", "cardiac*",
        "enxaqueca*", "hernia de disco", "hernia discal", "meu diagnostico", "diagnostico medico",
        # es
        "salud mental", "problema de salud", "problemas de salud", "estado de salud", "diabetes",
        "embarazada", "embarazo", "cirugia*", "enfermedad*", "enfermo", "enferma", "discapacidad",
        "diagnosticad*", "tratamiento medico", "receta medica", "depresion", "ataque de panico",
        "ansiedad generalizada", "cita medica", "examen medico", "analisis de sangre", "migrana*",
        "mi diagnostico",
        # en
        "mental health", "health condition", "health problem*", "medical condition", "diabetic",
        "hypertension", "high blood pressure", "chemotherapy", "chemo", "pregnant", "pregnancy", "abortion",
        "miscarriage", "surgery", "diagnosed", "disease", "illness", "disability", "wheelchair", "depression",
        "anxiety disorder", "panic attack*", "schizophrenia", "autism", "autistic", "adhd", "epilepsy",
        "hospitalized", "hospitalised", "prescription", "medication*", "antidepressant*", "therapy",
        "therapist", "blood test", "medical exam", "exam authorization", "doctor", "allerg*", "asthma",
        "heart attack", "migraine*", "herniated disc", "slipped disc", "my diagnosis", "medical diagnosis",
    ),
    "religion": (
        "religiao", "religios*", "evangelic*", "catolic*", "protestante", "espirita", "umbanda",
        "candomble", "judeu", "judia", "judaism*", "judaic*", "muculman*", "islamic*", "islamismo",
        "budista", "budismo", "ateu", "ateia", "ateismo", "agnostic*", "testemunha de jeova",
        "testemunhas de jeova", "igreja", "missa", "sinagoga", "mesquita", "terreiro", "cristao",
        "cristaos", "crista", "dizimo",
        "religion", "cristianismo", "judio", "musulman*", "ateo", "atea", "iglesia", "mezquita", "misa",
        "diezmo",
        "religious", "catholic", "christianity", "jewish", "muslim", "buddhist", "hindu", "atheist",
        "church", "mosque", "synagogue", "jehovah", "tithe",
    ),
    "philosophical_belief": (
        "conviccao filosofica", "conviccoes filosoficas", "crenca filosofica", "crencas filosoficas",
        "maconaria", "macom",
        "conviccion filosofica", "creencia filosofica", "masoneria",
        "philosophical belief*", "freemason*",
    ),
    "political_opinion": (
        "partido politico", "partidos politicos", "filiad* ao partido", "filiad* a partido",
        "filiacao partidaria", "votei no", "votei na", "votei em", "vou votar no", "vou votar na",
        "vou votar em", "esquerdista", "direitista", "bolsonarista",
        "petista", "lulista", "comunista", "socialista", "anarquista", "fascista", "opiniao politica",
        "opinioes politicas", "ideologia politica", "posicionamento politico", "militancia politica",
        "militante politico",
        "afiliad* al partido", "vote por", "votare por", "voy a votar por", "izquierdista", "derechista",
        "opinion politica", "opiniones politicas",
        "political party", "party member", "i voted for", "i will vote for", "i'm voting for",
        "political opinion*", "political views", "left-wing", "right-wing", "communist", "socialist",
        "fascist", "democrat", "republican",
    ),
    "union_membership": (
        "sindicato", "sindicatos", "sindicalizad*", "sindicalista", "filiad* ao sindicato",
        "filiacao sindical", "contribuicao sindical", "imposto sindical", "sindical",
        "afiliad* al sindicato", "afiliacion sindical", "cuota sindical",
        "trade union", "labor union", "labour union", "union member", "union membership", "union dues",
        "unionized", "unionised",
    ),
    "sexual_orientation": (
        "gay", "gays", "lesbica*", "bissexua*", "homossexua*", "heterossexua*", "transgener*",
        "transexua*", "travesti*", "nao binari*", "lgbt*", "orientacao sexual", "assexua*", "queer",
        "pansexua*",
        "homosexua*", "heterosexua*", "bisexua*", "lesbiana*", "orientacion sexual", "no binari*",
        "lesbian*", "transgender", "non-binary", "nonbinary", "sexual orientation",
    ),
    "sexual_life": (
        "vida sexual", "relacao sexual", "relacoes sexuais", "doenca sexualmente transmissivel",
        "infeccao sexualmente transmissivel", "disfuncao eretil", "impotencia sexual",
        "relaciones sexuales", "enfermedad de transmision sexual", "disfuncion erectil",
        "sex life", "sexual relations", "sexually transmitted", "erectile dysfunction",
    ),
    "racial_or_ethnic_origin": (
        "etnia", "etnias", "etnic*", "indigena*", "quilombola*", "afrodescendente*", "cor da pele",
        "origem racial", "origem etnica", "racismo", "racista*", "racial", "raciais", "injuria racial",
        "preconceito racial", "discriminacao racial", "minha raca",
        "afrodescendiente*", "color de piel", "origen racial", "origen etnico", "mi raza",
        "ethnicity", "ethnic", "racism", "racist", "indigenous", "skin color", "skin colour", "my race",
    ),
    "genetic": (
        "genetic*", "genoma", "exame de dna", "teste de dna",
        "prueba de adn", "examen de adn", "test de adn",
        "genome", "dna test",
    ),
    "biometric": (
        "dados biometricos", "dado biometrico",
        "datos biometricos", "dato biometrico",
        "biometric data", "voiceprint",
    ),
    "criminal_record": (
        "antecedentes criminais", "antecedente criminal", "ficha criminal", "certidao de antecedentes",
        "fui preso", "fui presa", "estive preso", "estive presa", "presidiari*",
        "condenad* por", "processo criminal", "cumpri pena", "tornozeleira eletronica",
        "liberdade condicional",
        "antecedentes penales", "antecedentes criminales", "fui arrestad*", "estuve pres*",
        "estuve en la carcel", "carcel", "libertad condicional",
        "criminal record", "criminal history", "i was arrested", "was convicted", "convicted of",
        "felony", "on parole", "in prison", "in jail", "ex-convict",
    ),
}
# fmt: on

_NOT_WORD = re.compile(r"[^a-z0-9]+")
_QUOTES = str.maketrans({"’": "'", "‘": "'", "´": "'"})


def normalized_name(name: str) -> str:
    """Lowercase ASCII with underscores: `Saúde`, `saude` and `saúde ` compare equal."""
    ascii_only = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    return _NOT_WORD.sub("_", ascii_only.casefold()).strip("_")


@cache
def is_product_sensitive(name: str | None) -> bool:
    """`health`, `health.exam`, `Saúde` and `autorizacao_de_exame` are sensitive: the part before a dot
    names the family, and a name the word lists read as a sensitive topic is one, so a shortcut such as
    `exam_authorization` cannot carry health into a category or a rule parameter."""
    if not name:
        return False
    clean = normalized_name(name)
    family = normalized_name(name.split(".", 1)[0])
    if {clean, family} & (PRODUCT_SENSITIVE_CATEGORIES | _ALIASES):
        return True
    return bool(sensitive_topics(clean.replace("_", " ")))


# The topic each other name of a sensitive category stands for (`_ALIASES`), by its family.
_ALIAS_TOPICS: dict[str, str] = {
    **dict.fromkeys(
        ("medical", "clinical", "saude", "medico", "medica", "clinico", "clinica", "salud"), "health"
    ),
    **dict.fromkeys(
        ("religious_belief", "religiao", "crenca_religiosa", "conviccao_religiosa", "creencia_religiosa"),
        "religion",
    ),
    **dict.fromkeys(
        ("philosophical_beliefs", "conviccao_filosofica", "crenca_filosofica", "conviccion_filosofica"),
        "philosophical_belief",
    ),
    **dict.fromkeys(
        (
            "politics",
            "political",
            "politica",
            "opiniao_politica",
            "filiacao_partidaria",
            "opinion_politica",
            "afiliacion_politica",
        ),
        "political_opinion",
    ),
    **dict.fromkeys(
        ("trade_union", "sindicato", "filiacao_sindical", "afiliacion_sindical"), "union_membership"
    ),
    **dict.fromkeys(("sex_life", "vida_sexual"), "sexual_life"),
    **dict.fromkeys(
        ("sexuality", "orientacao_sexual", "sexualidade", "orientacion_sexual"), "sexual_orientation"
    ),
    **dict.fromkeys(
        (
            "ethnicity",
            "ethnic_origin",
            "racial_origin",
            "origem_racial",
            "origem_etnica",
            "origem_racial_ou_etnica",
            "etnia",
            "origen_racial",
            "origen_etnico",
        ),
        "racial_or_ethnic_origin",
    ),
    **dict.fromkeys(("genetics", "genetica", "dados_geneticos", "datos_geneticos"), "genetic"),
    **dict.fromkeys(("biometrics", "biometria", "dados_biometricos", "datos_biometricos"), "biometric"),
    **dict.fromkeys(
        (
            "criminal_history",
            "antecedentes_criminais",
            "antecedente_criminal",
            "antecedentes_penales",
            "antecedentes_criminales",
        ),
        "criminal_record",
    ),
}


def topic_of(name: str | None) -> str | None:
    """The sensitive category a category name stands for: `health`, `health.exam` and `saude` give
    `health`; an ordinary name gives None, and so does a sensitive one whose topic is not plain."""
    if not name or not is_product_sensitive(name):
        return None
    for candidate in (normalized_name(name), normalized_name(name.split(".", 1)[0])):
        if candidate in PRODUCT_SENSITIVE_CATEGORIES:
            return candidate
        if candidate in _ALIAS_TOPICS:
            return _ALIAS_TOPICS[candidate]
    found = sensitive_topics(normalized_name(name).replace("_", " "))
    return found[0] if len(found) == 1 else None


def fold(text: str) -> str:
    """Lowercase without accents and with single spaces, so word lists match however a turn is typed."""
    decomposed = unicodedata.normalize("NFKD", text.translate(_QUOTES))
    plain = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return " ".join(plain.casefold().split())


def _word(word: str) -> str:
    return re.escape(word.removesuffix("*")) + (r"\w*" if word.endswith("*") else "")


def _pattern(terms: Iterable[str]) -> re.Pattern[str]:
    # Any word of a term may be a stem ("filiad* ao partido"), and the words may be split by spaces or
    # hyphens ("pronto-socorro", "non binary").
    parts = [
        r"[\s-]+".join(_word(word) for word in re.split(r"[\s-]+", term))
        for term in sorted(set(terms), key=len, reverse=True)
    ]
    return re.compile(r"(?<!\w)(?:" + "|".join(parts) + r")(?!\w)")


@cache
def _patterns() -> tuple[tuple[str, re.Pattern[str]], ...]:
    return tuple((name, _pattern(terms)) for name, terms in sorted(SENSITIVE_TERMS.items()))


def sensitive_topics(text: str | None) -> tuple[str, ...]:
    """The sensitive categories a text touches, by the word lists, in name order; empty when none."""
    if not text:
        return ()
    folded = fold(text)
    return tuple(name for name, pattern in _patterns() if pattern.search(folded))
