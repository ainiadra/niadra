"""Personal data a text holds, found with no model: contacts and Brazilian documents. Pure.

It never waits for the models server, so a path that must decide at once uses it: what may stay in an
agent's working note (`domain.agent_memory.rules`), and which undeclared field of an object reaches no agent
before it is declared (`domain.objects.undeclared`). Phone numbers need a leading `+` or a Brazilian shape,
so dates, versions, error codes and case numbers stay allowed.
"""

import re
from collections.abc import Sequence

_LOCAL = (
    ("email", re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)*\.[a-z]{2,}", re.I)),
    ("cpf", re.compile(r"\b\d{3}\.\d{3}\.\d{3}-\d{2}\b")),
    ("cnpj", re.compile(r"\b\d{2}\.\d{3}\.\d{3}/\d{4}-\d{2}\b")),
    ("phone", re.compile(r"\+\d{1,3}[\s.-]?\(?\d{1,4}\)?(?:[\s.-]?\d{2,5}){2,4}")),
    ("phone", re.compile(r"\(?\b\d{2}\)?\s?9\d{4}-?\d{4}\b")),
)


def local_personal_kinds(texts: Sequence[str]) -> list[str]:
    """The kinds of personal data the texts hold (`email`, `cpf`, `cnpj`, `phone`), sorted."""
    return sorted({kind for text in texts for kind, rx in _LOCAL if rx.search(text)})
