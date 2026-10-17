"""The index of raw content (5.14.0): payloads as they came, media and raw interactions, by object key.

Each object is indexed when it is written: under the handle segment of its key (a handle id, or `_`) and the
time it was written, so the `raw_content` retention reads only what expired, and an erasure drops a handle's
rows with its prefix. Every payload is also indexed by what it names a person
by, read from the body as it came and keyed-hashed with the space's key, and so is the text of every event
(`mention_body`): an erasure looks the target's handles up instead of reading the space, a document identity
keeps only as a keyed hash and an id of four characters included. What it finds under no handle goes; what it
finds kept for someone else keeps, with the target's identifiers in it replaced by `ERASED` (`redact_body`,
`redact_value`).
"""

import dataclasses
import json
import re
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from niadra.domain.identity.phones import line_forms
from niadra.domain.records import EventRecord
from niadra.domain.vocabulary import HandleType

RAW_INDEX_PURPOSE = "raw_index"
"""The keyed hash of a token is the space's, under its own purpose (5.14.1)."""
TOKEN_BYTES = 16
"""A token's keyed hash, cut to 128 bits: a body names a few thousand at most, and a lookup needs no more."""
RAW_INDEX_READY = "raw_index_ready"
"""The `space_state` key set once the objects written before the index existed are in it."""
RAW_INDEX_PAGE = 500
"""Rows the retention and the first indexing write or delete per statement."""

PHONE_DIGITS = 10
"""A phone number names its owner by its last ten digits: a body writes it with or without the country code,
with or without punctuation, and in any form that names the same line (`line_forms`)."""
MIN_ID_CHARS = 6
"""An id this long names its owner wherever the body writes it, free text included."""
MIN_VALUE_CHARS = 4
"""A shorter id names its owner only as a whole string value of the body: inside free text, or as a number,
it would name other people's payloads too. Below this, no id is looked for."""
_PHONE_RUN = re.compile(r"\+?\d[\d ().-]{8,}\d")
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_WORD = re.compile(r"[\w.@-]+")
_DOCUMENT = re.compile(r"[0-9A-Za-z]+(?:[./-][0-9A-Za-z]+)*")
_NOT_ALNUM = re.compile(r"[^0-9A-Za-z]")
_DOCUMENT_CHARS = (4, 24)
_PHONES = frozenset({HandleType.PHONE_E164, HandleType.WA_ID, HandleType.WA_JID})
ERASED = "[erased]"
"""What stands where an erased person's identifier was, in a record kept for someone else (5.14.0): the rest
of the record stays theirs."""
_DOCUMENTS = frozenset({HandleType.GOV_ID_HMAC, HandleType.ORG_REGISTRY_HMAC})


@dataclass(frozen=True, slots=True)
class RawObject:
    """One object of raw content: `owner` is its key's handle segment, `tokens` the keyed hashes of what a
    payload under no handle names a person by."""

    key: str
    owner: str
    written_at: datetime
    tokens: frozenset[bytes] = field(default=frozenset())


def body_tokens(body: bytes) -> frozenset[str]:
    """What a body names a person by, as `handle_tokens` writes a handle: the last ten digits of a phone
    number, an e-mail, a document by its letters and digits (with the leading zeros a spreadsheet drops), a
    whole string value, and a word of at least `MIN_ID_CHARS`."""
    found: set[str] = set()
    for text, whole in _texts(body):
        if whole and len(text) >= MIN_VALUE_CHARS:
            found.add(f"v:{text}")
        for run in _PHONE_RUN.findall(text):
            found.update(_phone_tokens(run))
        found.update(f"e:{e.lower()}" for e in _EMAIL.findall(text))
        found.update(f"w:{w}" for w in _WORD.findall(text) if len(w) >= MIN_ID_CHARS)
        for run in _DOCUMENT.findall(text):
            found.update(f"doc:{d}" for d in _documents(_NOT_ALNUM.sub("", run).upper()))
    return frozenset(found)


def handle_tokens(handles: Iterable[tuple[HandleType, str]]) -> frozenset[str]:
    """The tokens a payload names the target by. A domain names a whole organization's people, and an id
    shorter than `MIN_VALUE_CHARS` anyone's payload, so neither is looked for."""
    found: set[str] = set()
    for type_, value in handles:
        if not value or type_ is HandleType.EMAIL_DOMAIN:
            continue
        if type_ in _PHONES and len(digits := _digits(value.split("@", 1)[0])) >= PHONE_DIGITS:
            found.update(_phone_tokens("+" + digits))
        elif type_ is HandleType.EMAIL:
            found.add(f"e:{value.lower()}")
        elif type_ in _DOCUMENTS:
            found.add(f"doc:{_NOT_ALNUM.sub('', value).upper()}")
        else:
            if len(value) >= MIN_VALUE_CHARS:
                found.add(f"v:{value}")
            if len(value) >= MIN_ID_CHARS:
                found.add(f"w:{value}")
    return frozenset(found)


def mention_body(text: str | None, fields: Any = None, result: Any = None) -> bytes | None:
    """What an event writes in free text and fields, as one body `body_tokens` reads; None when it writes
    nothing."""
    parts = {k: v for k, v in (("text", text), ("fields", fields), ("result", result)) if v}
    return json.dumps(parts, ensure_ascii=False, default=str).encode() if parts else None


def redact_body(body: bytes, targets: frozenset[str]) -> tuple[bytes, int]:
    """The body with every identifier `handle_tokens` gave for the erased person replaced by `ERASED`, and how
    many were: a JSON body keeps its shape, any other is redacted as text. The same bytes when none was."""
    text = body.decode("utf-8", errors="replace")
    try:
        data = json.loads(text)
    except ValueError:
        redacted, found = redact_text(text, targets)
        return (redacted.encode() if found else body), found
    redacted_data, found = redact_value(data, targets)
    return (json.dumps(redacted_data, ensure_ascii=False).encode() if found else body), found


def redact_value(data: Any, targets: frozenset[str]) -> tuple[Any, int]:
    """`redact_body` for a decoded value: strings and numbers are read as `body_tokens` reads them, a whole
    string or number that is the identifier becomes `ERASED`."""
    if isinstance(data, dict):
        found = 0
        out: dict[Any, Any] = {}
        for key, value in data.items():
            out[key], n = redact_value(value, targets)
            found += n
        return out, found
    if isinstance(data, list):
        items = [redact_value(value, targets) for value in data]
        return [v for v, _ in items], sum(n for _, n in items)
    if isinstance(data, str):
        if f"v:{data}" in targets:
            return ERASED, 1
        return redact_text(data, targets)
    if isinstance(data, int | float) and not isinstance(data, bool):
        _, found = redact_text(str(data), targets)
        return (ERASED if found else data), found
    return data, 0


def redact_text(text: str, targets: frozenset[str]) -> tuple[str, int]:
    """Free text with the erased person's phones, e-mails, documents and ids replaced by `ERASED`, matched as
    `body_tokens` reads them, and how many were."""
    spans: list[tuple[int, int]] = []
    for match in _PHONE_RUN.finditer(text):
        if _phone_tokens(match.group()) & targets:
            start, end = match.span()
            # An area code in parentheses opens before the run's first digit.
            spans.append((start - 1 if start and text[start - 1] in "(+" else start, end))
    spans += [m.span() for m in _EMAIL.finditer(text) if f"e:{m.group().lower()}" in targets]
    for match in _DOCUMENT.finditer(text):
        compact = _NOT_ALNUM.sub("", match.group()).upper()
        if any(f"doc:{d}" in targets for d in _documents(compact)):
            spans.append(match.span())
    spans += [
        m.span()
        for m in _WORD.finditer(text)
        if len(m.group()) >= MIN_ID_CHARS and f"w:{m.group()}" in targets
    ]
    if not spans:
        return text, 0
    merged: list[list[int]] = []
    for start, end in sorted(spans):
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    out = text
    for start, end in reversed(merged):
        out = out[:start] + ERASED + out[end:]
    return out, len(merged)


def _texts(body: bytes) -> Iterator[tuple[str, bool]]:
    """The body's text: each string and number of a JSON body, flagged when it is a whole string value; any
    other body as it came."""
    text = body.decode("utf-8", errors="replace")
    try:
        data = json.loads(text)
    except ValueError:
        yield text, False
        return
    yield from _scalars(data)


def _scalars(data: Any) -> Iterator[tuple[str, bool]]:
    if isinstance(data, dict):
        for value in data.values():
            yield from _scalars(value)
    elif isinstance(data, list):
        for value in data:
            yield from _scalars(value)
    elif isinstance(data, str):
        yield data, True
    elif isinstance(data, int | float) and not isinstance(data, bool):
        yield str(data), False


def _documents(compact: str) -> Iterator[str]:
    low, high = _DOCUMENT_CHARS
    if not low <= len(compact) <= high or not any(c.isdigit() for c in compact):
        return
    yield compact
    if compact.isdigit():
        # A CPF of 11 digits and a CNPJ of 14 lose up to two leading zeros in a spreadsheet.
        yield from (compact.zfill(size) for size in (11, 14) if size - 2 <= len(compact) < size)


def _phone_tokens(run: str) -> set[str]:
    """The tokens a phone number names its line by, in every form it is written with: a handle keeps the
    E.164 form (a Brazilian mobile with its ninth digit) and a summary writes the number as the person said
    it, so either side finds the other, and an entry indexed before by the digits as written still matches."""
    return {f"p:{d[-PHONE_DIGITS:]}" for d in line_forms(run) if len(d) >= PHONE_DIGITS}


def _digits(text: str) -> str:
    return "".join(c for c in text if c.isdigit())


def event_tokens(record: EventRecord) -> frozenset[str]:
    """What an event names a person by, in its text, its fields and its action's result."""
    body = mention_body(record.text, record.fields or None, (record.action or {}).get("result"))
    return body_tokens(body) if body else frozenset()


def redact_event(record: EventRecord, plain: frozenset[str]) -> EventRecord | None:
    """The event with the erased person (`plain`, `handle_tokens`) redacted out of its text, fields and the
    action's result; None when it names them nowhere."""
    text, in_text = redact_value(record.text, plain) if record.text else (record.text, 0)
    fields, in_fields = redact_value(dict(record.fields), plain) if record.fields else (record.fields, 0)
    action = record.action
    in_result = 0
    if action is not None and action.get("result"):
        result, in_result = redact_value(action["result"], plain)
        action = {**action, "result": result}
    if not in_text + in_fields + in_result:
        return None
    return dataclasses.replace(record, text=text, fields=fields, action=action)
