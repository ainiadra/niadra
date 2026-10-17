"""Field access and source licences (24.2.11, 24.2.4; object-type spec 3.4 and 3.6): who reads which field of
an object, for which purpose, and which source's values a purpose may not use.

Pure, and the same decision on every path that serves a field to an agent: the state read, the subject's view,
the pack's state block and the SDK's mask of a tool's output, so the paths never disagree. A field's rules are
read in order and the first whose readers name the reader and whose purposes include one of the reader's (or
that names none) decides; a field that declares rules and matches none is denied, as the context firewall
denies what no rule releases. A field with no rules is read by anyone who reads its object.
"""

import re
from collections.abc import Collection, Iterable
from dataclasses import dataclass
from typing import Protocol

from niadra.domain.policy.rules import Effect

PUBLIC = "public"
_NOT_NAME = re.compile(r"[^a-z0-9]+")


class AccessRuleSpec(Protocol):
    @property
    def readers(self) -> Collection[str]: ...
    @property
    def purposes(self) -> Collection[str]: ...
    @property
    def effect(self) -> str: ...


class LicenceSpec(Protocol):
    @property
    def commercial_use(self) -> str: ...
    @property
    def exclude_from_purposes(self) -> Collection[str]: ...


@dataclass(frozen=True, slots=True)
class FieldReader:
    """An agent or a system reading objects: the names a rule may call it by, and its purposes."""

    names: frozenset[str]
    purposes: frozenset[str]

    def reading(self, purpose: str | None) -> "FieldReader":
        """The same reader for one read: the read's own purpose (`display`, `claim`, `decide`) counts too."""
        return FieldReader(self.names, self.purposes | {purpose}) if purpose else self


def reader_of(source_name: str, purposes: Iterable[str]) -> FieldReader:
    """A source key's reader: `public`, and `agent:<name>` and `source:<name>`, the source's name lowercased
    with each run of other characters as one `_`."""
    name = _NOT_NAME.sub("_", source_name.lower()).strip("_")
    names = {PUBLIC} | ({f"agent:{name}", f"source:{name}"} if name else set())
    return FieldReader(frozenset(names), frozenset(purposes))


def field_effect(rules: Iterable[AccessRuleSpec], reader: FieldReader) -> Effect:
    listed = False
    for rule in rules:
        listed = True
        if reader.names.isdisjoint(rule.readers):
            continue
        if rule.purposes and reader.purposes.isdisjoint(rule.purposes):
            continue
        return Effect(rule.effect)
    return Effect.DENY if listed else Effect.ALLOW


def licence_allows(
    licence: LicenceSpec | None, reader: FieldReader, commercial_purposes: Collection[str]
) -> bool:
    """Whether a value from a source under `licence` may reach this reader: not when the reader's purposes
    include one the licence excludes, nor, when the licence forbids commercial use, one the registry marks
    commercial."""
    if licence is None:
        return True
    if not reader.purposes.isdisjoint(licence.exclude_from_purposes):
        return False
    return licence.commercial_use != "forbidden" or reader.purposes.isdisjoint(commercial_purposes)
