"""A field a company's system reports on a declared type that the type does not declare (24.2.1). Pure.

Niadra serves it by default, as it serves a declared field: the company sends its data and its agents read
it, with no configuration first. Declaring the field stays possible, to give it a label, mark it personal
or sensitive, rule its access or track its changes, and a declared field always goes by its declaration.

Until it is declared, an undeclared field reaches no agent when it may be personal data or a third party's
free text (`held`):
- personal: its value has the shape of a person's contact or document (an e-mail, a phone, a CPF), the
  same detectors that keep personal data out of an agent's working notes, whatever its name
  ("contato_principal": "maria@exemplo.com"); or its name says it holds a person's contact, document,
  address or birth date ("telefone_cliente", "endereco") and its value is a text; or its name says it holds
  a name and either says whose (a person: "cliente_nome", "customer_full_name") or its value reads as a
  person's full name ("name": "Maria da Silva"), so a product's name ("product_name": "Dipirona 500 mg") is
  served. A name the field says is of the company's own professional on the matter, in a working role and
  with no word of the customer's side ("responsible_lawyer_name", "account_manager_name"), is the company's
  word on who handles it, which the customer is told: it is served. An employee's, a driver's, a seller's or
  a doctor's name is not taken as such (the person may be the record's subject, a third party, or say
  something about the customer's health), and a contact or document value is held whatever the name. A
  name that reads like a person's in a field that does not say whose ("plan_name": "Plano Ouro") is held too:
  holding one more field until it is declared costs less than serving a person's name. A name alone never
  holds a number or a date. An organization's registry (CNPJ) is not a person's.
- content: a text longer than `CONTENT_CHARS`, of several lines, or that reads as prose (`reads_as_prose`:
  several words and a sentence's punctuation, "06/10/2026: COMUNICACAO: Fica a parte requerida intimada
  para…"), is a third party's words (a court's order, a ticket's body). Such words never go bare on a line:
  the state block says them inside the envelope, as a declared content field's, when they are short, hold no
  contact or document and the deterministic scan finds nothing in them (`fenced`); every other path leaves
  them out. A status, a code, a case number or a title ("Especificação de provas") is not prose.
A number is read as its digits: a phone or a CPF a system sends as a number ("telefone_cliente":
5511912345678) is held as its text would be, and a name that says contact, document, address or birth holds
any number. A nested value (an object or a list) is held when any of its parts is, each part read under its
own name too ("contato": {"email": ...}), and as content when it is long. A name that says whose and reads as
a person's full name is held even with no word for "name" in the field ("cliente": "Maria da Silva",
"titular", "paciente"). The same rule holds on every path an agent reads (the state and pack lines, history
rows and vectors, the typed and plain reads), and a type nobody declared holds every field by it.
Both stay in the object's state and go back to the company as a data issue (`field_held`) that says which
field and why, never its value.
"""

import json
import re
from collections.abc import Collection, Mapping
from datetime import date, datetime
from typing import Any, Literal

from niadra.domain.identity.documents import is_valid_cpf
from niadra.domain.ingest.scan import ScanState, scan
from niadra.domain.privacy.personal import local_personal_kinds

Held = Literal["personal", "content"]

PROSE_WORDS = 5
PROSE_ALONE = 12
"""Words that make a text prose: with a sentence's punctuation from `PROSE_WORDS`, without from this many."""
_SENTENCE = re.compile(r"[.:;!?](\s|$)")
_CPF_DIGITS = re.compile(r"\b\d{3}\.?\d{3}\.?\d{3}-?\d{2}\b")

NESTED_DEPTH = 4
"""How deep a nested value is read for personal data; deeper, it is held as content."""

CONTENT_CHARS = 200
"""Longer than this, a text is a third party's words rather than a value a line says ("Especificação de
provas", a court, a status are far shorter)."""

_ORGANIZATION = frozenset({"cnpj"})
"""What the shared detectors find that is an organization's, not a person's."""

_NAME_WORDS = frozenset({"name", "nome", "nombre", "apelido", "surname", "sobrenome", "apellido"})
"""Words that say a field holds a name: a person's when the name says whose, or the value reads as one."""

_PERSON_WORDS = frozenset(
    {
        "client", "cliente", "customer", "contact", "contato", "contacto", "person", "pessoa", "persona",
        "user", "usuario", "owner", "titular", "holder", "patient", "paciente", "employee", "funcionario",
        "empleado", "spouse", "conjuge", "father", "pai", "mother", "mae", "parent", "guardian",
        "beneficiary", "beneficiario", "driver", "motorista", "seller", "vendedor", "buyer", "comprador",
        "tenant", "inquilino", "doctor", "medico", "first", "last", "full", "social",
    }
)  # fmt: skip
"""Words that say a name is a person's ("cliente_nome", "full_name"): held."""

_STAFF_WORDS = frozenset(
    {
        "lawyer", "advogado", "abogado", "attorney", "responsible", "responsavel", "assigned", "atribuido",
        "agent", "atendente", "manager", "gerente", "consultant", "consultor", "technician", "tecnico",
        "analyst", "analista", "broker", "corretor", "representative", "representante",
    }
)  # fmt: skip
"""Words that say a name is of the company's own professional on the matter ("responsible_lawyer_name"):
served when no word of `_PERSON_WORDS` is in the name too."""

_PERSONAL_WORDS = frozenset(
    {
        # contact
        "email", "mail", "phone", "telefone", "telefono", "celular", "whatsapp", "mobile",
        # document
        "cpf", "rg", "cnh", "ssn", "passport", "passaporte", "pasaporte", "dni", "nif", "nie",
        # address
        "address", "endereco", "direccion", "domicilio", "logradouro", "cep", "zip",
        # birth
        "birth", "birthday", "nascimento", "nacimiento", "dob",
    }
)  # fmt: skip
"""Words a field's name is made of that say it holds a person's contact, document, address or birth date,
in PT, EN and ES; ASCII, as the contract writes field names."""

_WHOSE = _PERSON_WORDS - {"first", "last", "full", "social"}
"""The words of `_PERSON_WORDS` that say whose with no word for "name" beside them: "last_status" and
"full_text" are not a person's."""

_LOGIN_WORDS = frozenset({"login", "username", "handle", "nickname", "usuario", "user"})
"""Words that say a field holds how a person signs in or is called by a system."""

_PARTICLES = frozenset({"de", "da", "do", "das", "dos", "e", "del", "la", "y", "van", "von"})
_NAME_WORD = re.compile(r"[A-ZÀ-Ý][a-zà-ÿ'’-]+")

_WORD = re.compile(r"[a-z]+")
_E164_BR = re.compile(r"55[1-9]{2}9?\d{8}")
_DIGITS = re.compile(r"\D")
_ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
_ISO_INSTANT = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}")


def held(name: str, value: Any, _depth: int = 0) -> Held | None:
    """Why an undeclared field reaches no agent before it is declared; None when it does (module doc)."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, Mapping | list | tuple):
        # Deeper than a record's own parts is a document, not a value a line says: held, never walked.
        return "content" if _depth >= NESTED_DEPTH else _held_nested(name, value, _depth + 1)
    if isinstance(value, int | float):
        return _held_number(name, value)
    if not isinstance(value, str):
        return "content"
    text = value.strip()
    if not text:
        return None
    if len(text) > CONTENT_CHARS or "\n" in text:
        return "content"
    if _personal_value(text) or _personal_name(name, text):
        return "personal"
    if reads_as_prose(text):
        return "content"
    return None


def reads_as_prose(text: str) -> bool:
    """Several words with a sentence's punctuation, or many words: someone's sentence, not a value."""
    words = text.split()
    return (len(words) >= PROSE_WORDS and bool(_SENTENCE.search(text))) or len(words) >= PROSE_ALONE


def fenced(name: str, value: Any) -> str | None:
    """The text of an undeclared field held as content that the state block may still say inside the
    envelope: short, with no contact or document in it, and nothing the deterministic scan flags."""
    if not isinstance(value, str) or held(name, value) != "content":
        return None
    text = value.strip()
    # A longer text waits for a declaration as content, whose scan and mode the company decides.
    if len(text) > CONTENT_CHARS or "\n" in text or local_personal_kinds([text]) or _CPF_DIGITS.search(text):
        return None
    return text if scan(text, required=False).state is ScanState.CLEAN else None


def _held_nested(
    name: str, value: Mapping[Any, Any] | list[Any] | tuple[Any, ...], depth: int
) -> Held | None:
    parts = (
        [held(f"{name}_{key}", part, depth) for key, part in value.items()]
        if isinstance(value, Mapping)
        else [held(name, part, depth) for part in value]
    )
    if "personal" in parts:
        return "personal"
    if "content" in parts or len(json.dumps(value, default=str, ensure_ascii=False)) > CONTENT_CHARS:
        return "content"
    return None


def _held_number(name: str, value: int | float) -> Held | None:
    if set(_WORD.findall(name.lower())) & _PERSONAL_WORDS:
        return "personal"
    if isinstance(value, float) and not value.is_integer():
        return None
    return "personal" if _personal_value(str(int(value))) else None


def shown(name: str, value: Any, ruled: Collection[str]) -> bool:
    """Whether a field of an object reaches an agent, on every path that serves one: a name a person declared
    on its type (`ruled`, `ObjectType.ruled`) by its declaration, any other unless `held`. A type nobody
    declared rules nothing."""
    return name in ruled or held(name, value) is None


def says_person(name: str) -> bool:
    """Whether a field's name says it holds something of a person: a name, a contact, a document, an
    address, a login, or a word of whose it is ("cliente", "titular")."""
    return bool(set(_WORD.findall(name.lower())) & (_NAME_WORDS | _WHOSE | _PERSONAL_WORDS | _LOGIN_WORDS))


def humanized(name: str) -> str:
    """What a line calls an undeclared field: its name made readable (`held_on`, "Held on"). Field names are
    English ASCII by the contract, so this guesses no translation."""
    words = name.replace("-", "_").split("_")
    said = " ".join(w for w in words if w)
    return said[:1].upper() + said[1:] if said else name


def as_value(value: Any) -> Any:
    """An undeclared field's value as a line writes it: an ISO date or instant as a date or datetime, so it
    reads in the space's date format; anything else as it is."""
    if isinstance(value, str):
        text = value.strip()
        try:
            if _ISO_INSTANT.match(text):
                return datetime.fromisoformat(text)
            if _ISO_DATE.fullmatch(text):
                return date.fromisoformat(text)
        except ValueError:
            return value
    return value


def is_dated(value: Any) -> bool:
    """Whether an undeclared field's value is a date or an instant (`as_value`)."""
    return isinstance(as_value(value), date)


def _personal_value(text: str) -> bool:
    if [k for k in local_personal_kinds([text]) if k not in _ORGANIZATION]:
        return True
    digits = _DIGITS.sub("", text)
    # A Brazilian phone in E.164 written with no "+", as systems often store it: country, area, number.
    if _E164_BR.fullmatch(text.strip()):
        return True
    # A CPF written bare or formatted: exactly its eleven digits, and they pass the check.
    return len(digits) == 11 and len(text) <= 14 and is_valid_cpf(digits)


def _personal_name(name: str, text: str) -> bool:
    words = set(_WORD.findall(name.lower()))
    if words & _PERSONAL_WORDS:
        return True
    if not words & _NAME_WORDS:
        # A field that says whose, holding what reads as a full name: "cliente": "Maria da Silva".
        # A word of the customer's side wins over a working role: "responsible_customer" says whose.
        return bool(words & _WHOSE) and _reads_as_full_name(text)
    if words & _PERSON_WORDS:
        return True
    return not words & _STAFF_WORDS and _reads_as_full_name(text)


def _reads_as_full_name(text: str) -> bool:
    """Two to six words, each a capitalised word of letters or a particle ("Maria da Silva")."""
    words = text.split()
    named = [w for w in words if w not in _PARTICLES]
    return 2 <= len(words) <= 6 and len(named) >= 2 and all(_NAME_WORD.fullmatch(w) for w in named)
