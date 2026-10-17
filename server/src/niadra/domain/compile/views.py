"""Budgets per view (P6): which sections a view carries, and each section's floor and ceiling.

Budgets count only B1 to B3 (15.3, rule 2); B0 is shared by the tenant and never competes with
the customer's own content.
"""

import re
import string
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace

from niadra.domain.common.errors import InvalidInput
from niadra.domain.compile.model import Section, TaskViewSpec, slot_budget


@dataclass(frozen=True, slots=True)
class SectionLimit:
    floor: int = 0
    ceiling: int | None = None


@dataclass(frozen=True, slots=True)
class ViewSpec:
    name: str
    budget: int
    sections: Mapping[Section, SectionLimit]
    voice: bool = False
    max_traits: int = 6
    object_types: tuple[str, ...] = ()
    purposes: frozenset[str] = field(default_factory=frozenset)
    template: str | None = None
    """The space's own layout of the customer block: section names in braces, nothing else."""

    def carries(self, section: Section) -> bool:
        return section in self.sections


_CHAT = {
    Section.ACCOUNT: SectionLimit(0, 300),
    Section.CUSTOMER: SectionLimit(20, 150),
    Section.FACTS: SectionLimit(0, 400),
    Section.STABLE_PATTERNS: SectionLimit(0, 120),
    Section.META: SectionLimit(),
    Section.HISTORY: SectionLimit(),
    Section.SUMMARY: SectionLimit(0, 200),
    Section.EPISODES: SectionLimit(0, 400),
    Section.VOLATILE_PATTERNS: SectionLimit(0, 120),
    Section.ACTIONS: SectionLimit(0, 200),
    Section.OBJECTS: SectionLimit(0, 200),
    Section.PENDING: SectionLimit(60, 400),
}

# Voice: short sentences and at most 4 traits in 60 tokens (5.10), split between the two layers.
_VOICE = {
    Section.ACCOUNT: SectionLimit(0, 100),
    Section.CUSTOMER: SectionLimit(20, 80),
    Section.FACTS: SectionLimit(0, 120),
    Section.STABLE_PATTERNS: SectionLimit(0, 30),
    Section.META: SectionLimit(),
    Section.HISTORY: SectionLimit(),
    Section.EPISODES: SectionLimit(0, 120),
    Section.VOLATILE_PATTERNS: SectionLimit(0, 30),
    Section.ACTIONS: SectionLimit(0, 80),
    Section.OBJECTS: SectionLimit(0, 60),
    Section.PENDING: SectionLimit(40, 150),
}

_BRIEF = {
    Section.CUSTOMER: SectionLimit(10, 50),
    Section.STABLE_PATTERNS: SectionLimit(0, 40),
    Section.VOLATILE_PATTERNS: SectionLimit(0, 40),
    Section.PENDING: SectionLimit(0, 80),
}

_FULL = {section: SectionLimit() for section in Section}

VIEWS: dict[str, ViewSpec] = {
    "chat": ViewSpec("chat", 1500, _CHAT),
    "custom": ViewSpec("custom", 1500, _CHAT),
    "voice": ViewSpec("voice", 600, _VOICE, voice=True, max_traits=4),
    "brief": ViewSpec("brief", 150, _BRIEF, max_traits=1),
    "full": ViewSpec("full", 4000, _FULL, max_traits=20),
    "account": ViewSpec("account", 1500, _CHAT),
    "partner": ViewSpec("partner", 1500, _CHAT),
}


SKELETON_VIEWS = frozenset({"chat", "voice"})
"""The views whose pinned body is cut to a skeleton (11, 3.3). The other views keep their
body (a task view carries its objects, an account its contacts, `brief` and `full` are what they are) and
only gain slots from what their budget left out."""

# Skeleton budgets, like every budget without B0 and the tags: the customer line, facts, stable patterns,
# open items and, when it is not recent, the latest conversation. What just happened has a budget of its own
# (`RECENT_BUDGETS`). A floor of one token makes the knapsack keep the latest conversation whenever it fits
# its ceiling.
_SKELETON = {
    "voice": (
        110,
        {
            Section.EPISODES: SectionLimit(1, 100),
            Section.ACTIONS: SectionLimit(1, 60),
            Section.PENDING: SectionLimit(30, 60),
        },
    ),
    "chat": (
        180,
        {
            Section.EPISODES: SectionLimit(1, 130),
            Section.ACTIONS: SectionLimit(1, 100),
            Section.PENDING: SectionLimit(40, 100),
        },
    ),
}

RECENT_BUDGETS = {"voice": 150, "chat": 240}
"""Tokens a skeleton adds for what just happened around the customer's latest activity: the conversations of
that day and their open matter, other agents' actions and system records of the last hours, and the line
that says the matter happened before. They sit on top of the skeleton's own budget, so the
customer line, facts and open items never give way to them, and pinned body plus slots stay within the
view's budget (voice 110 + 150 + 60 of 600, chat 180 + 240 + 120 of 1,500)."""

LEARNED_SECTIONS: Mapping[str, Section] = {
    "fact": Section.FACTS,
    "episode": Section.EPISODES,
    "action": Section.ACTIONS,
}
"""The skeleton sections whose ceilings are learned per agent and model (13, 6.3), by the item kind the
measurement judges: only kinds whose use it sees. Identity, patterns, open items and the fixed lines keep
the view's own limits; objects and the tail summary wait in the reserve anyway."""
SLOTS = "slots"
"""The per-turn slots' budget, learned beside the sections."""


def learned_ceilings(view: str, factors: Mapping[str, float]) -> tuple[tuple[str, int], ...]:
    """The section ceilings a skeleton view compiles with under learned `factors` (kind -> times the
    default), as the variant key names them: only those that differ from the view's, never below a
    section's floor. Empty for a view without a skeleton."""
    if view not in _SKELETON:
        return ()
    spec = skeleton_spec(VIEWS[view])
    found = []
    for kind, section in LEARNED_SECTIONS.items():
        limit = spec.sections.get(section)
        factor = factors.get(kind)
        if factor is None or limit is None or limit.ceiling is None:
            continue
        tokens = max(limit.floor, int(limit.ceiling * factor))
        if tokens != limit.ceiling:
            found.append((section.value, tokens))
    return tuple(sorted(found))


def default_ceilings(view: str) -> dict[str, int]:
    """The ceilings of the learned sections as the view has them, by kind; empty without a skeleton."""
    if view not in _SKELETON:
        return {}
    spec = skeleton_spec(VIEWS[view])
    found = {}
    for kind, section in LEARNED_SECTIONS.items():
        limit = spec.sections.get(section)
        if limit is not None and limit.ceiling is not None:
            found[kind] = limit.ceiling
    return found


def with_ceilings(spec: ViewSpec, ceilings: Sequence[tuple[str, int]]) -> ViewSpec:
    """The spec with learned ceilings in place of its own; floors never move."""
    if not ceilings:
        return spec
    sections = dict(spec.sections)
    for name, tokens in ceilings:
        section = Section(name)
        if (limit := sections.get(section)) is not None:
            sections[section] = SectionLimit(limit.floor, max(limit.floor, tokens))
    return replace(spec, sections=sections)


def learned_slot_budget(view: str, factor: float | None, *, question: bool = False) -> int:
    """The slots' budget of a view under a learned factor; the view's own without one. `question`: the
    turn asks for a value, a count or a day (`QUESTION_SLOT_BUDGETS`)."""
    budget = slot_budget(view, question=question)
    return budget if factor is None or budget <= 0 else max(1, int(budget * factor))


def skeleton_spec(spec: ViewSpec) -> ViewSpec:
    """The view's spec for a skeleton: a smaller budget and tighter ceilings on what stays pinned."""
    if spec.name not in _SKELETON:
        return spec
    budget, limits = _SKELETON[spec.name]
    return replace(spec, budget=budget, sections={**spec.sections, **limits})


CUSTOM = "custom"
DEFAULT_TASK_VIEW_BUDGET = 1500
"""The budget of a task view the space never declared."""
TEMPLATE_MAX_CHARS = 2000
TEMPLATE_FIELDS = frozenset(s.value for s in Section if s is not Section.ACCOUNT)
"""What a template may name: every section of the customer block. The account block, the opening line and
the tags around the block stay where the product puts them."""


def view_spec(name: str, task_views: Sequence[TaskViewSpec] = ()) -> ViewSpec:
    if name.startswith("task:"):
        sections = {**_CHAT, Section.OBJECTS: SectionLimit(0, 500), Section.PENDING: SectionLimit(60, 500)}
        task = next((t for t in task_views if t.name == name), None)
        if task is None:
            # A task view nobody declared (zero touch): every object of the task's subject, no
            # focus, the default budget. A declaration names types, purposes or a template.
            return ViewSpec(name, DEFAULT_TASK_VIEW_BUDGET, sections)
        return ViewSpec(
            name,
            task.budget_tokens,
            sections,
            object_types=tuple(task.object_types),
            purposes=task.purposes,
            template=task.template,
        )
    if name == CUSTOM and (own := next((t for t in task_views if t.name == CUSTOM), None)) is not None:
        # The space's `custom` view: its budget and, with a template, only the sections the template names.
        named = template_fields(own.template) if own.template else None
        sections = {
            s: lim for s, lim in _CHAT.items() if named is None or s.value in named or s is Section.ACCOUNT
        }
        return ViewSpec(
            CUSTOM,
            own.budget_tokens,
            sections,
            object_types=tuple(own.object_types),
            purposes=own.purposes,
            template=own.template,
        )
    try:
        return VIEWS[name]
    except KeyError:
        raise InvalidInput("unknown view") from None


def template_fields(template: str) -> frozenset[str]:
    """The sections a template names, as `{facts}` or `{pending}`; `{{` and `}}` write a brace. Anything
    else, a format spec, a field of a field, an unknown name, is refused: a template holds no logic."""
    if len(template) > TEMPLATE_MAX_CHARS:
        raise InvalidInput(f"a view template has at most {TEMPLATE_MAX_CHARS} characters")
    try:
        parsed = list(string.Formatter().parse(template))
    except ValueError:
        raise InvalidInput("a view template has an unmatched brace") from None
    names: set[str] = set()
    for _, name, spec, conversion in parsed:
        if name is None:
            continue
        if spec or conversion or name not in TEMPLATE_FIELDS:
            raise InvalidInput("a view template names only sections: " + ", ".join(sorted(TEMPLATE_FIELDS)))
        names.add(name)
    return frozenset(names)


def fill_template(template: str, texts: Mapping[str, str]) -> str:
    """The customer block laid out by the template; a section with nothing to say leaves no empty lines."""
    body = template.format_map({name: texts.get(name, "") for name in TEMPLATE_FIELDS})
    return re.sub(r"\n{3,}", "\n\n", body).strip()
