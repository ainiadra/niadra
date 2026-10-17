"""The sensitive-category barrier (20.4).

No trait is ever derived from a sensitive category, and the leak can hide in a rule parameter:
"recurring complaint about exam authorization" reveals health without any personal data. So
every category and event type a rule names must pass the tenant allow-list, and never be sensitive.
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from niadra.domain.common.errors import InvalidInput
from niadra.domain.policy.sensitive import PRODUCT_SENSITIVE_CATEGORIES, is_product_sensitive

_CATEGORY_PARAMS = ("category", "categories")
_EVENT_TYPE_PARAMS = ("event_type", "event_types")


@dataclass(frozen=True, slots=True)
class Barrier:
    sensitive: frozenset[str]
    allowed: frozenset[str] | None
    """Tenant allow-list of categories and event types; None allows every non-sensitive one."""
    declared: frozenset[str] | None = None
    """Without an allow-list, the categories the space declares are the default one: a category
    nobody declared, a typo or a topic the word lists do not know, never becomes a pattern. None when the
    space declares no episode categories, so extraction's own names stand."""

    @classmethod
    def build(
        cls,
        tenant_sensitive: Iterable[str],
        allowed: Iterable[str] | None,
        declared: Iterable[str] | None = None,
    ) -> "Barrier":
        # The product's list (domain/policy/sensitive.py) plus the categories the tenant marks sensitive.
        return cls(
            sensitive=PRODUCT_SENSITIVE_CATEGORIES | frozenset(tenant_sensitive),
            allowed=None if allowed is None else frozenset(allowed),
            declared=None if declared is None else frozenset(declared),
        )

    def is_sensitive(self, name: str | None) -> bool:
        if not name:
            return False
        return is_product_sensitive(name) or name in self.sensitive or name.split(".", 1)[0] in self.sensitive

    def permits(self, name: str | None) -> bool:
        """Whether a rule may name `name` (an event type) as a parameter."""
        if not name or self.is_sensitive(name):
            return False
        return self.allowed is None or name in self.allowed

    def permits_category(self, name: str | None) -> bool:
        """Whether a rule may name the category `name` or turn it into a trait value."""
        if not self.permits(name):
            return False
        return self.allowed is not None or self.declared is None or name in self.declared

    def check_params(self, params: Mapping[str, Any]) -> None:
        """Rejects a rule whose parameters name a sensitive or non-allowed category or event type."""
        for key in (*_CATEGORY_PARAMS, *_EVENT_TYPE_PARAMS):
            allows = self.permits_category if key in _CATEGORY_PARAMS else self.permits
            for value in _as_list(params.get(key)):
                if not allows(value):
                    raise InvalidInput(
                        f"rule parameter `{key}` names a category that is sensitive or not allowed"
                    )


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    return [str(v) for v in value]
