"""One vocabulary for operations (5.8, 18.4): what an agent says it did, what a system event confirms
and what an open item waits for.

Agents record `credit`, the ERP emits `invoice.credited` and the extraction may name either, so every
side is normalized to the canonical operation before two of them are compared. Without it the credit
that settles a contested invoice never closes the open item the conversation opened.
"""

import hashlib
import re
import unicodedata
from collections.abc import Iterable, Mapping

MAX_OPERATIONS = 200
"""The one cap of every list of operations a space keeps: the names its
systems confirm (`domain.objects.infer`), the ones its agents record and the ones extraction offers
(`domain.extract.observed`). Past it the least used are not offered, and the Console says how many."""

# Product catalog of canonical system events that confirm an operation (5.8); tenant mappings may
# instead put the operation in the event's `fields.operation`.
CANONICAL_OPERATIONS: Mapping[str, str] = {
    "invoice.credited": "credit",
    "invoice.refunded": "refund",
    "invoice.contested": "contest",
    "invoice.paid": "pay",
    "order.cancelled": "cancel",
    "order.delivery_changed": "change_delivery",
    "subscription.cancelled": "cancel",
    "ticket.reopened": "reopen",
    "ticket.closed": "close",
    "visit.rescheduled": "reschedule",
    "payment.refunded": "refund",
}

# The past tense of each catalog verb, so `credited` or `fatura.credited` still read as `credit`.
_PAST_TENSE: Mapping[str, str] = {
    **{event.split(".", 1)[1]: operation for event, operation in CANONICAL_OPERATIONS.items()},
    "canceled": "cancel",
}


SAFE_NAME = re.compile(r"[a-z0-9_.-]{1,64}")
"""What an operation name may be once normalized: a name comes from the customer's
agents and systems and reaches the extraction model's instructions, so it is a label, never text that could
instruct. Any other name goes by `hashed_operation`."""


def canonical_operation(name: str | None) -> str | None:
    """`invoice.credited`, `Credited` and `credit` are all `credit`; an unknown name stays as written,
    lowercased and without accents, so a tenant's own operation still matches itself (`notificação` is
    `notificacao`). A name outside `SAFE_NAME` once normalized becomes its `hashed_operation`, the same on
    every side, so it still matches itself and never carries words of its own."""
    cleaned = _clean(name)
    if cleaned is None:
        return None
    named = _recognized(cleaned) or cleaned
    return named if SAFE_NAME.fullmatch(named) else hashed_operation(named)


def hashed_operation(name: str) -> str:
    """A neutral, stable name for an operation whose own name is not a safe label."""
    return "op_" + hashlib.sha256(name.encode()).hexdigest()[:10]


def operation_of_event_type(event_type: str | None) -> str | None:
    """The operation a system event type confirms, only when the type is one the catalog knows."""
    cleaned = _clean(event_type)
    return _recognized(cleaned) if cleaned else None


def _clean(name: str | None) -> str | None:
    if name is None:
        return None
    folded = "".join(c for c in unicodedata.normalize("NFKD", name) if not unicodedata.combining(c))
    return "_".join(folded.strip().casefold().split()) or None


def _recognized(cleaned: str) -> str | None:
    if cleaned in CANONICAL_OPERATIONS:
        return CANONICAL_OPERATIONS[cleaned]
    verb = cleaned.rsplit(".", 1)[-1]
    if verb in _PAST_TENSE:
        return _PAST_TENSE[verb]
    if verb in catalog_operations():
        return verb
    return None


def canonical_operations(names: Iterable[str]) -> frozenset[str]:
    return frozenset(op for op in (canonical_operation(n) for n in names) if op)


def catalog_operations() -> frozenset[str]:
    return frozenset(CANONICAL_OPERATIONS.values())


def trusts(trusted_ops: Iterable[str], operation: str | None, *, checked: bool = True) -> bool:
    """Whether a source's declared action may close items for `operation`. A declared list is a closed one.
    A source that declared none trusts every operation the closing can check (`checked`): an
    internal agent's, by the words and the decision model, and a customer-facing agent's only where the
    delivery gate reads its reply in the conversation. Outside one there is no reply to read, and the
    attendant's declared "aviso da intimação" with no conversation closed the lawyer's promise on its words
    alone."""
    allowed = canonical_operations(trusted_ops)
    if not allowed:
        return checked
    return canonical_operation(operation) in allowed


def same_operation(a: str | None, b: str | None) -> bool:
    left, right = canonical_operation(a), canonical_operation(b)
    return left is not None and left == right


def acts_on_same(a: str | None, b: str | None) -> bool:
    """Two operations done to the same kind of thing: `attach_document` and `deliver_document`. With no
    configuration the operation an item waits for is the one extraction knew when it was said, so the
    lawyer's "enviar ao cliente a cópia da decisão" waited for `attach_document`, the only document operation
    observed then, and the attendant's retelling of it for `deliver_document` (juridico-zero, 07/10/2026).
    What decides whether one tells the other is the decision model, never this."""
    left, right = canonical_operation(a), canonical_operation(b)
    if left is None or right is None or "_" not in left or "_" not in right:
        return False
    return left.split("_", 1)[1] == right.split("_", 1)[1]
