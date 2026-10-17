"""Profile search for the Console (5.22): by pseudonym, by an exact identifier or by a name, never by
scanning.

A handle value is normalized exactly as ingestion does and then looked up by its keyed hash, so the
search can only find what the caller already knows; there is no partial match to enumerate customers. A
name is looked up the same way, by the keyed hash of the name fact's value (`app.identity`).
"""

import re

from niadra.domain.common.errors import InvalidInput
from niadra.domain.identity.display import NAME_CHARS
from niadra.domain.identity.documents import looks_like_cpf
from niadra.domain.identity.handles import HandleKey, classify, normalize_handle
from niadra.domain.vocabulary import HandleType

_PSEUDONYM = re.compile(r"^p_[a-z2-7]{16}$")


def is_name_query(query: str) -> bool:
    """A value no identifier format claims, with a letter in it, short enough to be a name."""
    text = query.strip()
    return (
        0 < len(text) <= NAME_CHARS
        and any(ch.isalpha() for ch in text)
        and classify(text) is None
        and not looks_like_cpf(text)
        and not is_pseudonym(text)
    )


def is_pseudonym(query: str) -> bool:
    return bool(_PSEUDONYM.match(query.strip()))


def search_keys(query: str, type: HandleType | None = None, scope: str | None = None) -> list[HandleKey]:
    """The keys a value can be stored under, the derived ones included (a JID also finds the phone).

    Contact values are classified by format; opaque ids (system ids, BSUIDs, app ids) need `type`.
    Eleven digits can be a Brazilian mobile or a CPF, so both are tried.
    """
    if type is not None:
        candidates = [type]
    else:
        candidates = [t for t in (classify(query),) if t is not None]
        if looks_like_cpf(query):
            candidates.append(HandleType.GOV_ID_HMAC)
    keys: list[HandleKey] = []
    for candidate in candidates:
        try:
            normalized = normalize_handle(candidate, query, scope)
        except InvalidInput:
            continue
        keys += [normalized.key, *(d.key for d in normalized.derived)]
    if not keys:
        raise InvalidInput("the query is neither a pseudonym nor a recognizable identifier; give its type")
    return list(dict.fromkeys(keys))
