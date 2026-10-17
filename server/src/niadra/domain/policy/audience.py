"""Who a conversation is with decides its audience, and only ever narrows the key's.

A key needs no declared audience to be safe: the same assistant may talk with the company's staff in the
morning and with a customer at night. A conversation whose subject is staff (a person the company's own
systems name, `SubjectKind.STAFF`) is internal. A conversation with anyone else, or with no known subject,
is customer-facing. A task is internal, as the key is. A key declared customer-facing stays customer-facing
whoever it talks with: a declaration narrows, the subject never widens it (P3).

Pure, and the same rule on every path: the read of a conversation's context (`app.serve`), the packs warmed
for a conversation that just opened (`app.compile`), and what memory keeps of the conversation
(`app.memory`, `app.extract`), so the staff view never reaches a customer and a customer's words never pass
for the company's.
"""

from dataclasses import replace

from niadra.domain.common.space import SourcePrincipal
from niadra.domain.ingest.rules import default_subject_kind
from niadra.domain.vocabulary import Audience, HandleType, SubjectKind


def conversation_audience(
    declared: Audience | None, subject: SubjectKind | None, *, conversation: bool
) -> Audience | None:
    """The audience a key reads and writes with in one conversation (`conversation`) or task.

    An unproven subject is a customer: `None` (no profile yet), a person, an account or a partner all narrow
    an internal key to customer-facing. Only staff keeps it internal."""
    if declared is not Audience.INTERNAL_AGENT or not conversation:
        return declared
    return Audience.INTERNAL_AGENT if subject is SubjectKind.STAFF else Audience.CUSTOMER_AGENT


def in_conversation(
    caller: SourcePrincipal, subject: SubjectKind | None, conversation_id: str | None
) -> SourcePrincipal:
    """The caller as it reads in one conversation: narrowed to customer-facing unless its subject is staff.
    A read outside a conversation (a task, a lookup) keeps the key's audience."""
    audience = conversation_audience(caller.audience, subject, conversation=conversation_id is not None)
    return caller if audience is caller.audience or audience is None else replace(caller, audience=audience)


def is_task(view: str | None, task_id: str | None) -> bool:
    """A read for a task: a `task:` view or a task id. A task is internal, as its key is, whatever
    conversation thread it names, since no customer takes part in it."""
    return task_id is not None or (view or "").startswith("task:")


def claimed_kind(type: HandleType, given: SubjectKind | None) -> SubjectKind:
    """What a read's subject handle says it names, for `in_conversation`: staff only through a system id,
    since a customer's own identifier never makes its conversation internal, whatever kind it claims."""
    if given is SubjectKind.STAFF and type is not HandleType.SYSTEM_ID:
        return SubjectKind.PERSON
    return default_subject_kind(type, given)
