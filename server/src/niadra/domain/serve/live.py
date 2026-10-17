"""The `live` block (B3) passes the same policy function as the pack (5.12).

A raw turn has no category yet: extraction files its conversation only after it ends. Until then an
action is an action, a turn about a business object (its `object_refs`) is filed under the object's
type, as the object is in the pack, and any other turn is conversation history. Each turn also carries
every category the law calls sensitive that any turn of its conversation touches, read by the
product's word lists, the way an item about a conversation carries that conversation's category. So a
read that proved nothing gets no turn, the billing agent never gets the lines of a conversation about
health, and a task without the customer (`no_customer`) reads only turns whose categories the policy
releases for it, like the pack's items.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from niadra.domain.policy import Decision, ItemAttrs, PolicyContext, PolicySpec, Reader, evaluate
from niadra.domain.policy.sensitive import sensitive_topics
from niadra.domain.vocabulary import EventKind, SubjectKind, Visibility

LIVE_KIND = "live"
HISTORY_CATEGORY = "history"
ACTION_CATEGORY = "action"


@dataclass(frozen=True, slots=True)
class LiveLine:
    """One turn of the hot layer, decrypted, with every handle of the customer it was read under."""

    event_id: UUID
    doc: Mapping[str, Any]
    handle_ids: tuple[UUID, ...]

    @property
    def thread(self) -> str:
        return str(self.doc.get("conversation_id") or self.doc.get("task_id") or f"event:{self.event_id}")

    @property
    def words(self) -> str:
        """What the word lists read: the text, or the names of the event or operation."""
        parts = (self.doc.get("text"), self.doc.get("canonical_type"), self.doc.get("operation"))
        return " ".join(str(p).replace(".", " ").replace("_", " ") for p in parts if p)


def live_attrs(line: LiveLine, subject_kind: SubjectKind, topics: Sequence[str] = ()) -> ItemAttrs:
    doc = line.doc
    types = list(dict.fromkeys(str(r["type"]) for r in doc.get("object_refs") or [] if r.get("type")))
    if doc.get("kind") == EventKind.ACTION.value:
        category = ACTION_CATEGORY
    elif types:
        category = types[0]
    else:
        category = HISTORY_CATEGORY
    return ItemAttrs(
        kind=LIVE_KIND,
        category=category,
        visibility=Visibility(doc["visibility"]),
        subject_kind=subject_kind,
        handle_ids=line.handle_ids,
        # A turn about two objects is about both types, like a conversation is about its category.
        topics=tuple(sorted({*types, *topics} - {category})),
    )


def screen_live(
    lines: Sequence[LiveLine],
    policy: PolicySpec,
    reader: Reader,
    context: PolicyContext,
    subject_kind: SubjectKind,
) -> list[tuple[LiveLine, Decision]]:
    """Every turn with the decision the pack's items would get; the caller drops what is not included."""
    topics: dict[str, set[str]] = {}
    for line in lines:
        topics.setdefault(line.thread, set()).update(sensitive_topics(line.words))
    return [
        (line, evaluate(policy, reader, live_attrs(line, subject_kind, sorted(topics[line.thread])), context))
        for line in lines
    ]
