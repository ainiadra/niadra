"""The context compiler (2.1, 5.10): a profile's memory in, a context pack and its manifest out.

Passes: P0 snapshot (done by the caller) -> P1 policy -> P2 scoring -> P3 knapsack -> P4 templates
-> P5 ordering -> P6 rendering -> P6.5 cache floor -> P7 manifest. Same input, same bytes (L5):
no wall clock, canonical ordering everywhere, ties broken by id.
"""

import hashlib
import re
from collections import Counter
from collections.abc import Callable, Collection, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from functools import partial
from typing import Any
from uuid import UUID

from niadra.domain.compile.bound_text import WORDS as BOUND_WORDS
from niadra.domain.compile.bound_text import Dates, more_text, row_text
from niadra.domain.compile.cache_floor import decide_cache, top_up
from niadra.domain.compile.candidates import CandidateBuilder, memory_year, repeats
from niadra.domain.compile.knapsack import Choice, GroupLimit, select
from niadra.domain.compile.model import (
    COMPILER_VERSION,
    SPEC_VERSION,
    TEMPLATE_VERSION,
    Candidate,
    CompiledPack,
    CompileInput,
    EpisodeRef,
    ItemKind,
    Layer,
    PackLine,
    ReserveItem,
    Section,
    Variant,
    canonical_json,
    public_item_id,
)
from niadra.domain.compile.scoring import MIN_VALUE, Focus, score
from niadra.domain.compile.templates import Formatter
from niadra.domain.compile.tokens import ESTIMATOR, estimate_tokens
from niadra.domain.compile.trim import RECENT_ACTIONS, kept_actions, trims
from niadra.domain.compile.views import (
    RECENT_BUDGETS,
    SKELETON_VIEWS,
    TEMPLATE_FIELDS,
    ViewSpec,
    fill_template,
    skeleton_spec,
    view_spec,
    with_ceilings,
)
from niadra.domain.coordinate.window import CONTACT_TIME_FACT
from niadra.domain.extract.matter import signature
from niadra.domain.identity.provenance import hidden_marker
from niadra.domain.memory.bound import BoundRow, ObjectMemory, for_reader, in_text, reads
from niadra.domain.policy import MIRROR_RULE, Decision, Effect, ItemAttrs, PolicyContext, Reader, evaluate
from niadra.domain.records import Episode
from niadra.domain.serve.atoms import without_reporting
from niadra.domain.serve.holdout import overdue_company_items
from niadra.domain.serve.lexicon import fold_words, stem
from niadra.domain.serve.recurrence import (
    RECURRENCE_INTENT,
    RECURRENCE_WINDOW_DAYS,
    count_by_category,
    recurrence,
)
from niadra.domain.serve.relative_time import SystemMark, marks_from_objects
from niadra.domain.serve.turn import analyze
from niadra.domain.vocabulary import ExclusionReason, Sensitivity, Verification

HISTORY_TOKENS = 80
"""The "From history" section stays within about this many tokens (docs/concepts/context)."""
RECENT_WINDOW = timedelta(hours=24)
"""What counts as just happened in a skeleton: conversations and system records up to this long before the
latest thing the memory holds (`as_of`), never the wall clock, so the same memory pins the same bytes."""
MATTER_MIN = 30
"""Tokens a skeleton keeps for each line of the open matter (the newest conversation, the recurrence line)
when other agents' recent actions take their place first: a compacted line still says date, channel, the
start of what was said and the outcome."""
VOICE_CONVERSATION = 36
"""Tokens of the estimate a voice skeleton's conversation line takes at most: its headline (tpl-12), about 25
real tokens."""
RECURRENCE_LINE = 45
"""Tokens of the estimate the recurrence line takes at most (tpl-12): its date, channel, the start of what
happened and the outcome say the matter came back; the count by matter, when the turn asks, is a slot."""
RECURRENCE_LOOKBACK = timedelta(days=365)
"""How far back the recent matter finds the older one it repeats, for the skeleton's recurrence line."""
COMPLAINT_FACTS = frozenset({"technical_issue", "order_problem", "billing_issue"})
"""Facts that restate what a conversation was about ("technical issue: the internet drops at night"): a
skeleton leaves out those a conversation says in its own words (tpl-12), body and reserve, since the
conversation carries the matter with its date and outcome, and a turn about it finds that conversation. The
others wait in the reserve for a turn about their matter."""
RESERVE_MAX = 200
"""Reserve items a slotted pack carries at most, the most valuable; a customer has a few hundred at most."""


@dataclass(frozen=True, slots=True)
class _Rendered:
    text: str
    lines: tuple[PackLine, ...]
    variables: dict[str, str]
    b0_end: int
    account_end: int
    b1_end: int


class _Compilation:
    def __init__(self, inp: CompileInput) -> None:
        self.inp = inp
        self.variant = inp.variant
        self.settings = inp.settings
        self.spec: ViewSpec = view_spec(self.variant.view, self.settings.task_views)
        self.fmt = Formatter(self.settings.timezone, self.settings.locale, memory_year(inp))
        self._dates = Dates(
            due=self.fmt.date, kept=self.fmt.date, said=self.fmt.date, day_of=self.fmt.local_date
        )
        self.voice = self.spec.voice
        self.reader, self.context = variant_reader(self.variant, self.spec)
        # A reader's chat and voice pin a skeleton (11, 3.3); what it leaves out waits for the slots.
        self.skeleton = self.variant.slotted and self.spec.name in SKELETON_VIEWS
        if self.skeleton:
            self.spec = skeleton_spec(self.spec)
        # What the reading agent and model were learned to use (13, 6.3); nothing for every other pack.
        self.own_spec = self.spec
        self.spec = with_ceilings(self.spec, self.variant.budgets)
        self._preferences: set[str] = set()
        """The instruction lines that are contact preferences: they open the block after the restrictions."""
        self._pinned_facts: set[str] = set()
        """Facts said whatever the budget, as instructions are: the subject's contact time."""

    def run(self) -> CompiledPack:
        candidates = CandidateBuilder(self.inp, self.fmt).build()
        memory = self._object_memory()
        at = max((c.at for c in candidates), default=None)
        objects = {c.object_id: c for c in candidates if c.kind is ItemKind.OBJECT and c.object_id}
        candidates = self._with_memory(candidates, memory, at)
        told = self._account_instructions()
        candidates = [
            *(self._contact_time(c) for c in candidates),
            *self._object_instructions(memory, objects, at),
            *(self._instruction_line(profile, row, section) for profile, row, section in told),
        ]
        decisions = self._policy(candidates)
        for c in candidates:
            if c.kind is ItemKind.INSTRUCTION and c.object_id is not None:
                # What was said about an object goes only where its line may go (2.6), and never masked.
                of_object = decisions.get(f"object:{c.object_id}")
                if not _object_included(of_object):
                    decisions[c.item_id] = Decision(Effect.DENY, ExclusionReason.POLICY, "object_line")
        told_out = tuple(
            (profile, row)
            for profile, row, _ in told
            if (d := decisions.get(f"instruction:{row.row_id}")) is not None and d.included
        )
        # What was said about an object goes only where its line may go (2.6).
        memory = tuple(m for m in memory if _object_included(decisions.get(f"object:{m.object_id}")))
        visible = [c for c in candidates if decisions[c.item_id].included and self.spec.carries(c.section)]
        as_of = max((c.at for c in candidates if c.kind is not ItemKind.SUMMARY), default=None)
        focus = Focus(as_of, self.spec.object_types, self.variant.focus_object_id)
        # A masked line that would say only its section and date takes no room: it tells the agent nothing.
        values = {
            c.item_id: 0
            if c.masked_is_header and decisions[c.item_id].effect is Effect.MASK
            else score(c, focus)
            for c in visible
        }
        # The same promise extracted twice (two conversations that both said it) is one line: the more
        # valuable stays, the other leaves the body and the reserve.
        kept = {f"open_item:{i.item_id}" for i in self.inp.subject.kept_items}
        duplicates = _duplicate_items(visible, values, kept)
        visible = [c for c in visible if c.item_id not in duplicates]
        bodies = {
            c.item_id: c.masked_text if decisions[c.item_id].effect is Effect.MASK else c.text
            for c in visible
        }
        # The same line many times (eleven contacts' conversations masked to "outro contato · outros assuntos
        # · 05/10" in juridico-sandbox, 05/10/2026) is one line that says how many.
        repeated, counted = _repeated(visible, bodies, values)
        visible = [c for c in visible if c.item_id not in repeated]
        duplicates = duplicates | repeated
        bodies = {
            i: self.fmt.word("repeated", text=text, count=counted[i]) if i in counted else text
            for i, text in bodies.items()
            if i not in repeated
        }
        withheld = sum(1 for d in decisions.values() if not d.included)
        for_level = sum(1 for d in decisions.values() if d.reason is ExclusionReason.VERIFICATION)

        fixed = self._fixed_lines(visible, as_of, withheld, for_level)
        # What must or must not be done is never traded for another line (2.7, 2.8): an instruction is said
        # whatever the budget, the skeleton or the use, its room taken before the knapsack's. A case's "não
        # propor acordo" left the pack once eight cases' lines filled the view (08/10/2026).
        pinned = [c for c in visible if self._pinned(c)]
        pool = [c for c in self._pool(visible, values, bodies) if not self._pinned(c)]
        held: frozenset[str] = frozenset()
        recent: list[Candidate] = []
        restated: frozenset[str] = frozenset()
        if self.skeleton:
            # A complaint fact that a conversation says in its own words restates it: the conversation,
            # in the body or the reserve, carries the matter with its date and outcome (tpl-12). It leaves
            # both, as a duplicate; one that says more stays.
            said = [bodies[c.item_id] for c in visible if _conversation(c)]
            restated = frozenset(
                c.item_id
                for c in pool
                if c.kind is ItemKind.FACT
                and c.predicate in COMPLAINT_FACTS
                and any(repeats(text, _fact_value(bodies[c.item_id])) for text in said)
            )
            pool = [c for c in pool if c.item_id not in restated]
            duplicates = duplicates | restated
            pool, recent, held = self._skeleton(pool, as_of)
        whole = bodies
        # A voice skeleton pins each conversation as its headline, and prices it so (tpl-12): the knapsack
        # never trades a conversation for another line on the length of text it will not print.
        headlines = self._headlines((*pool, *recent), bodies) if self.skeleton and self.voice else {}
        bodies = {**bodies, **{i: c.text for i, c in headlines.items()}}
        fixed_tokens = sum(estimate_tokens(text + "\n") for _, text in fixed)
        fixed_tokens += sum(self._cost(c, bodies[c.item_id]) for c in pinned)
        if any(c.section is Section.CUSTOMER for c in (*pool, *pinned)):
            fixed_tokens += estimate_tokens(self.fmt.line(Section.CUSTOMER, "", voice=self.voice) + "\n")
        limits = {s.value: GroupLimit(lim.floor, lim.ceiling) for s, lim in self.spec.sections.items()}
        costs = {c.item_id: self._cost(c, bodies[c.item_id]) for c in (*pool, *recent)}
        choices = [Choice(c.item_id, c.section.value, costs[c.item_id], values[c.item_id]) for c in pool]
        room = max(self.spec.budget - fixed_tokens, 0)
        if self.variant.budgets:
            selected = self._learned_selection(choices, room, limits)
        else:
            selected = select(choices, room, limits)

        by_id = {c.item_id: c for c in visible}
        # What just happened: its own budget on top of the skeleton's, and the line that says the
        # matter happened before, which takes the older conversation's place in the body.
        placed: dict[str, Candidate] = {i: c for i, c in headlines.items() if i in selected}
        if recent:
            chosen, found = self._recent(recent, visible, bodies, costs, as_of)
            selected = selected | chosen
            placed |= {i: c for i, c in headlines.items() if i in chosen} | found
        for item_id, candidate in placed.items():
            by_id[item_id] = candidate
            bodies = {**bodies, item_id: candidate.text}
            costs[item_id] = self._cost(candidate, candidate.text)
        # A line the body pins shorter than its item stays whole in the reserve, where a turn that asks
        # for what it left out finds it (tpl-12).
        shortened = frozenset(i for i in placed if i in selected and whole[i] not in bodies[i])
        # 15.8: what this source never used leaves its pack, and the room it frees stays empty, so the
        # pack is smaller by exactly those lines. Another agent's recent action never leaves.
        standing = (
            kept_actions(self.inp.subject, as_of, self.variant.source_id)
            if self.variant.trim
            else frozenset()
        )
        trimmed = frozenset(
            i
            for i in selected
            if trims(by_id[i], self.variant.trim, self.settings.policy, self.reader, standing)
        )
        selected = selected - trimmed
        # A fact the pinned conversation already says in its own words is one line too many (17, 6.10); it
        # leaves the body and the reserve, and the room it frees stays empty.
        echoed = _echoed(selected, by_id, bodies) if self.skeleton else frozenset()
        selected = (selected - echoed) | {c.item_id for c in pinned}
        duplicates = duplicates | echoed
        rendered = self._render(selected, by_id, bodies, values, fixed)
        stable_tokens = estimate_tokens(rendered.text[: rendered.b1_end])
        spare = [
            (c.item_id, costs[c.item_id])
            for c in sorted(pool, key=lambda c: (-values[c.item_id], c.item_id))
            if c.item_id not in selected
            and c.item_id not in trimmed
            and c.item_id not in echoed
            and c.section.layer is Layer.B1
        ]
        floor_added: tuple[str, ...] = ()
        if self.spec.template is None:
            # A template places the lines itself: more B1 items would not grow a stable prefix.
            floor_added = top_up(self.settings.floors, self.variant, self.spec.name, stable_tokens, spare)
        if floor_added:
            selected = selected | frozenset(floor_added)
            rendered = self._render(selected, by_id, bodies, values, fixed)
            stable_tokens = estimate_tokens(rendered.text[: rendered.b1_end])

        breakpoint_end = rendered.account_end if rendered.account_end > rendered.b0_end else rendered.b0_end
        cache = decide_cache(
            self.settings.floors,
            self.variant,
            self.spec.name,
            stable_tokens,
            breakpoints=(breakpoint_end, rendered.b1_end),
            added=floor_added,
            salt_seed=self.settings.salt_seed,
        )
        pack_hash = hashlib.sha256(rendered.text.encode()).hexdigest()
        manifest = self._manifest(
            candidates,
            decisions,
            visible,
            selected,
            trimmed,
            values,
            rendered,
            as_of,
            pack_hash,
            cache.stable_tokens,
            held,
            placed,
            duplicates,
        )
        reserve: tuple[ReserveItem, ...] = ()
        index: tuple[EpisodeRef, ...] = ()
        marks: tuple[SystemMark, ...] = ()
        until: datetime | None = None
        if self.variant.slotted:
            until = max((e.ended_at for e in self.inp.subject.episodes), default=None)
            reserve, index = self._reserve(
                visible, (selected - shortened) | trimmed | echoed | restated, decisions, values, whole
            )
            seen = {c.object_id for c in visible if c.kind is ItemKind.OBJECT}
            marks = tuple(
                sorted(
                    marks_from_objects(o for o in self.inp.subject.objects if o.object_id in seen),
                    key=lambda m: (m.at, m.canonical_type, str(m.object_id)),
                )
            )
            manifest["slots"] = {"skeleton": self.skeleton, "reserve": len(reserve)}
        account = self.inp.account
        unread = self.inp.subject.objects_unread + (account.objects_unread if account is not None else 0)
        if unread:
            # The objects past the runaway guard (`domain.memory.bound.OBJECTS_CEILING`): never left out in
            # silence; the receipt says how many.
            manifest["objects_unread"] = unread
        if hidden := hidden_marker(self.inp.subject.profile):
            # Handles of unconfirmed links whose rows this reader may not see (5.3): the read path serves
            # the pack only to a reader whose view hides the same ones.
            manifest["provenance"] = {"hidden_handles": hidden}
        if self.variant.trim:
            manifest["trim"] = {
                "units": sorted(self.variant.trim),
                "items": len(trimmed),
                "tokens_saved": sum(costs[i] for i in trimmed),
            }
        manifest["cache"] = {
            "provider": cache.provider,
            "model": cache.model,
            "floor": cache.floor,
            "stable_tokens": cache.stable_tokens,
            "cacheable": cache.cacheable,
            "ttl_seconds": cache.ttl_seconds,
            "added_for_floor": list(cache.added_for_floor),
            "breakpoints": list(cache.breakpoints),
        }
        return CompiledPack(
            variant=self.variant.key,
            text=rendered.text,
            variables=rendered.variables,
            lines=rendered.lines,
            manifest=manifest,
            manifest_hash=hashlib.sha256(canonical_json(manifest)).hexdigest(),
            pack_hash=pack_hash,
            as_of=as_of,
            privacy_version=self.inp.subject.profile.privacy_version,
            policy_version=self.settings.policy.version,
            withheld=withheld,
            cache=cache,
            reserve=reserve,
            episode_index=index,
            marks=marks,
            conversations_until=until,
            object_memory=memory,
            account_instructions=told_out,
        )

    def _object_memory(self) -> tuple[ObjectMemory, ...]:
        """What this variant's reader may read of each object's memory: the audience, then
        the policy, which only takes away. An object the read is about, or one of the types a task view
        names, is in focus: its instructions go to the constraints block (2.7)."""
        snapshots = [self.inp.subject, *([self.inp.account] if self.inp.account is not None else [])]
        found: dict[UUID, ObjectMemory] = {}
        for snapshot in snapshots:
            for memory in snapshot.object_memory:
                found.setdefault(memory.object_id, memory)
        task_types = set(self.spec.object_types) if self.variant.view.startswith("task:") else set()
        readable = for_reader(
            sorted(found.values(), key=lambda m: m.ref), self.variant.audience, admits=self._admits
        )
        return tuple(
            replace(m, focus=m.object_id == self.variant.focus_object_id or m.key.type in task_types)
            for m in readable
            if m.rows
        )

    def _account_instructions(self) -> list[tuple[UUID, BoundRow, Section]]:
        """What the contacts of the organizations of the read said must or must not be done with them, naming
        no object, as this reader may read it: the subject's own when it is one, the `about` one's."""
        found: list[tuple[UUID, BoundRow, Section]] = []
        for snapshot, section in (
            (self.inp.subject, Section.CUSTOMER),
            *([(self.inp.account, Section.ACCOUNT)] if self.inp.account is not None else []),
        ):
            for row in snapshot.instructions:
                if (
                    reads(row.audience, self.variant.audience)
                    and row.sensitivity is not Sensitivity.SENSITIVE
                ):
                    found.append((snapshot.profile.profile_id, row, section))
        return found

    def _contact_time(self, c: Candidate) -> Candidate:
        """The subject's own contact time fact (`preferred_contact_time`), pinned as a contact preference:
        said whatever the budget, after the restrictions, never traded for another line nor left out because
        the conversation that said it is pinned too (juridico-zero-sandbox, 10/10/2026: "não me liguem
        antes das 9h" held a call at 07:00, and a plain read had no line about it). It stays a fact for
        lineage."""
        if c.kind is not ItemKind.FACT or c.predicate != CONTACT_TIME_FACT or c.section is not Section.FACTS:
            return c
        self._preferences.add(c.item_id)
        self._pinned_facts.add(c.item_id)
        return replace(c, section=self._said_in(Section.CUSTOMER), weight=1.5, stable=True)

    def _pinned(self, c: Candidate) -> bool:
        """A line said whatever the budget: an instruction, or the subject's contact time."""
        return c.kind is ItemKind.INSTRUCTION or c.item_id in self._pinned_facts

    def _instruction_line(self, profile_id: UUID, row: BoundRow, section: Section) -> Candidate:
        subject = self.inp.account if section is Section.ACCOUNT and self.inp.account else self.inp.subject
        if row.contact:
            self._preferences.add(f"instruction:{row.row_id}")
        return Candidate(
            item_id=f"instruction:{row.row_id}",
            kind=ItemKind.INSTRUCTION,
            section=self._said_in(section),
            attrs=ItemAttrs(
                kind="instruction",
                category="open_item",
                subject_kind=subject.profile.kind,
                handle_ids=(row.origin_handle_id,),
                profile_id=profile_id,
            ),
            at=row.said_at,
            text=self._bound_text(row, account=True),
            masked_text=self._masked_instruction(),
            weight=1.5,
            stable=section is not Section.ACCOUNT,
        )

    def _object_instructions(
        self, memory: Sequence[ObjectMemory], objects: Mapping[UUID, Candidate], at: datetime | None
    ) -> list[Candidate]:
        """Each instruction in force about an object of the read, as a line of its own that goes by the
        object's name ("lawsuit [Número CNJ 1012345-...] · instrução: Não propor acordo neste processo
        (equipe)"), with the object's policy attributes: it goes wherever the object's line may go, and the
        knapsack never weighs it against another line (`run`)."""
        out = []
        for found in memory:
            line = objects.get(found.object_id)
            if line is None:
                continue
            head = line.text.split(" · ")[1] if " · " in line.text else line.text
            for row in found.instructions(at) if at is not None else found.of("instruction"):
                out.append(
                    Candidate(
                        item_id=f"instruction:{row.row_id}",
                        kind=ItemKind.INSTRUCTION,
                        section=self._said_in(Section.OBJECTS),
                        attrs=line.attrs,
                        at=row.said_at,
                        text=f"{head} · {self._bound_text(row)}",
                        masked_text=self._masked_instruction(),
                        weight=1.5,
                        stable=True,
                        object_type=line.object_type,
                        object_id=found.object_id,
                        object_ref=found.key,
                        bound=(("instruction", row.row_id),),
                    )
                )
        return out

    def _said_in(self, section: Section) -> Section:
        """The section an instruction goes in: its own when the view carries it, else the first the view
        carries of the pending items and the customer's line (`brief` carries neither objects nor the
        account)."""
        for candidate in (section, Section.PENDING, Section.CUSTOMER):
            if self.spec.carries(candidate):
                return candidate
        return section

    def _masked_instruction(self) -> str:
        return f"{BOUND_WORDS[self.fmt.lang]['instruction']}: {self.fmt.word('masked')}"

    def _admits(self, row: BoundRow) -> bool:
        """A row about an object passes the policy as its own kind would: an observation as a fact of its
        category, an item as an open item; an instruction goes where its object's line goes."""
        if row.kind == "instruction":
            return True
        attrs = ItemAttrs(
            kind="fact" if row.kind == "observation" else "open_item",
            category=row.category if row.kind == "observation" else "open_item",
            sensitivity=row.sensitivity,
            subject_kind=self.inp.subject.profile.kind,
            handle_ids=(row.origin_handle_id,),
            profile_id=self.inp.subject.profile.profile_id,
        )
        return evaluate(self.settings.policy, self.reader, attrs, self.context).effect is Effect.ALLOW

    def _with_memory(
        self, candidates: Sequence[Candidate], memory: Sequence[ObjectMemory], at: datetime | None
    ) -> list[Candidate]:
        """Each object's line with the items and observations said about it (2.7), the reader's own aside:
        they are lines of the pack already. Its instructions are lines of their own (`_object_instructions`),
        which no budget leaves out."""
        by_object = {m.object_id: m for m in memory}
        own = frozenset(self.inp.subject.profile.handle_ids)
        out = []
        for c in candidates:
            found = by_object.get(c.object_id) if c.kind is ItemKind.OBJECT and c.object_id else None
            told = in_text(found, at, own) if found is not None else None
            rows = [r for r in told.rows if r.kind != "instruction"] if told is not None else []
            if told is None or not rows:
                out.append(c)
                continue
            more = [more_text(group, count, self.fmt.lang) for group, count in sorted(told.more.items())]
            text = " · ".join([c.text, *(self._bound_text(r) for r in rows), *more])
            out.append(replace(c, text=text, bound=tuple((r.kind, r.row_id) for r in rows)))
        return out

    def _bound_text(self, row: BoundRow, *, account: bool = False) -> str:
        """A row as its line says it, as the state block says it (`domain.compile.bound_text`)."""
        return row_text(row, self.fmt.lang, self._dates, account=account)

    def _learned_selection(
        self, choices: Sequence[Choice], room: int, limits: Mapping[str, GroupLimit]
    ) -> frozenset[str]:
        """A learned skeleton (13, 6.3) is the view's own selection with each learned section at its
        ceiling: a lower one lets go of the section's least valuable lines until it fits, and the room it
        frees stays empty, so the pack is smaller by exactly those lines; a higher one takes the section's
        next lines while they fit it and the view's room. No other section moves."""
        own = {s.value: GroupLimit(lim.floor, lim.ceiling) for s, lim in self.own_spec.sections.items()}
        keep = set(select(choices, room, own))
        by_group: dict[str, list[Choice]] = {}
        for choice in choices:
            by_group.setdefault(choice.group, []).append(choice)
        for group, members in sorted(by_group.items()):
            ceiling, before = limits[group].ceiling, own[group].ceiling if group in own else None
            if ceiling is None or before is None or ceiling == before:
                continue
            held = sum(c.tokens for c in members if c.item_id in keep)
            if ceiling < before:
                for c in sorted(
                    (c for c in members if c.item_id in keep), key=lambda c: (c.value, c.item_id)
                ):
                    if held <= ceiling:
                        break
                    keep.discard(c.item_id)
                    held -= c.tokens
                continue
            used = sum(c.tokens for c in choices if c.item_id in keep)
            for c in sorted(
                (c for c in members if c.item_id not in keep), key=lambda c: (-c.value, c.item_id)
            ):
                if held + c.tokens <= ceiling and used + c.tokens <= room:
                    keep.add(c.item_id)
                    held += c.tokens
                    used += c.tokens
        return frozenset(keep)

    def _skeleton(
        self, pool: Sequence[Candidate], as_of: datetime | None
    ) -> tuple[list[Candidate], list[Candidate], frozenset[str]]:
        """The pinned body of a skeleton (11, 3.3): every section that is always small, the latest
        conversation, and apart, what just happened: the conversations and system records of the
        last `RECENT_WINDOW` and other agents' actions of the last 72 hours, before `as_of`. Older
        conversations and records, older actions and the tail summary are held for the slots."""
        conversations = [c for c in pool if _conversation(c)]
        latest = max(conversations, key=lambda c: (c.at, c.item_id), default=None)
        recent_since = as_of - RECENT_WINDOW if as_of is not None else None
        actions_since = as_of - RECENT_ACTIONS if as_of is not None else None
        keep: list[Candidate] = []
        recent: list[Candidate] = []
        held: set[str] = set()
        for c in pool:
            if (
                recent_since is not None
                and actions_since is not None
                and (
                    ((_conversation(c) or c.section is Section.OBJECTS) and c.at >= recent_since)
                    or (c.section is Section.ACTIONS and c.at >= actions_since)
                )
            ):
                recent.append(c)
            elif (
                (_conversation(c) and c is not latest)
                # An object others said something about stays on the body: its line carries it (2.7).
                or (c.section is Section.OBJECTS and not c.bound)
                or c.section in (Section.SUMMARY, Section.ACTIONS)
                # A complaint fact no conversation says waits for a turn about its matter (tpl-12).
                or (c.kind is ItemKind.FACT and c.predicate in COMPLAINT_FACTS)
            ):
                held.add(c.item_id)
            else:
                keep.append(c)
        return keep, recent, frozenset(held)

    def _recent(
        self,
        recent: Sequence[Candidate],
        visible: Sequence[Candidate],
        bodies: Mapping[str, str],
        costs: Mapping[str, int],
        as_of: datetime | None,
    ) -> tuple[frozenset[str], dict[str, Candidate]]:
        """What just happened, within the view's `RECENT_BUDGETS`, in three passes:

        1. Other agents' actions of the last `RECENT_WINDOW`, newest first: what another agent already did
           is what the agent must never deny or redo, so they take their tokens before anything else,
           leaving `MATTER_MIN` for each line of the open matter.
        2. The open matter: the newest conversation, then the line that says it happened before, each
           compacted to what is left when it does not fit whole (the summary's first sentence, then fewer
           words; date, channel, outcome and the start of the solution stay).
        3. The rest, whole, by relevance to the matter and then recency: older actions and conversations
           of the same reason first. A system record an action already names is left to the action.

        Returns the chosen ids and the items placed with another text or section than their own."""
        episodes = {f"episode:{e.episode_id}": e for e in self.inp.subject.episodes}
        conversations = [c for c in recent if _conversation(c)]
        newest = max(conversations, key=lambda c: (c.at, c.item_id), default=None)
        matter = episodes.get(newest.item_id) if newest is not None else None
        found = self._repeat(matter, {c.item_id for c in conversations}, visible, episodes)
        # A learned view (13, 6.3) that cut a section's ceiling below the view's cuts it here too: the agent
        # and model were measured not using those lines.
        caps = {
            Section(name): tokens
            for name, tokens in self.variant.budgets
            if (own := self.own_spec.sections.get(Section(name))) is not None
            and own.ceiling is not None
            and tokens < own.ceiling
        }
        room = RECENT_BUDGETS.get(self.spec.name, 0)
        state = {"used": 0}
        per_section: Counter[Section] = Counter()
        chosen: set[str] = set()
        placed: dict[str, Candidate] = {}
        acted_on: set[UUID] = set()

        def take(c: Candidate, cost: int, limit: int) -> bool:
            capped = c.section in caps and per_section[c.section] + cost > caps[c.section]
            if c.item_id in chosen or capped or state["used"] + cost > limit:
                return False
            chosen.add(c.item_id)
            state["used"] += cost
            per_section[c.section] += cost
            if c.section is Section.ACTIONS and c.object_id is not None:
                acted_on.add(c.object_id)
            return True

        # 1. What other agents did in the last hours.
        held_for_matter = MATTER_MIN * ((newest is not None) + (found is not None))
        since = as_of - RECENT_WINDOW if as_of is not None else None
        now_actions = [
            c for c in recent if c.section is Section.ACTIONS and since is not None and c.at >= since
        ]
        for c in sorted(now_actions, key=lambda c: (-c.at.timestamp(), c.item_id)):
            take(c, costs[c.item_id], room - held_for_matter)

        # 2. The open matter, compacted to fit.
        if newest is not None and matter is not None:
            left = room - state["used"]
            whole_repeat = self._cost(found[0], found[0].text) if found is not None else 0
            target = max(left - whole_repeat, (left + 1) // 2) if found is not None else left
            text = self._fit(bodies[newest.item_id], matter, lambda t: self._cost(newest, t) <= target)
            compacted = text is not None and text != bodies[newest.item_id]
            if text is not None and take(newest, self._cost(newest, text), room) and compacted:
                placed[newest.item_id] = replace(newest, text=text, masked_text=text)
        if found is not None:
            repeat, before, episode = found
            left = room - state["used"]

            def line(body: str) -> str:
                return self.fmt.word("recurs", before=body)

            cap = min(left, RECURRENCE_LINE)
            text = self._fit(bodies[before.item_id], episode, lambda t: self._cost(repeat, line(t)) <= cap)
            if text is not None and take(repeat, self._cost(repeat, line(text)), room):
                placed[repeat.item_id] = replace(repeat, text=line(text), masked_text=line(text))

        # 3. The rest, whole.
        def tier(c: Candidate) -> int:
            same = (
                matter is not None
                and (e := episodes.get(c.item_id)) is not None
                and e.normalized_category == matter.normalized_category
            )
            return 0 if c.section is Section.ACTIONS or same else 1

        for c in sorted(recent, key=lambda c: (tier(c), -c.at.timestamp(), c.item_id)):
            if c.section is Section.OBJECTS and c.object_id in acted_on:
                continue
            take(c, costs[c.item_id], room)
        return frozenset(chosen), placed

    def _headlines(self, candidates: Sequence[Candidate], bodies: Mapping[str, str]) -> dict[str, Candidate]:
        """A voice skeleton pins each conversation as its headline (tpl-12): date, channel, what it was
        about and the outcome, within `VOICE_CONVERSATION`. What the turn asks of it (a number, a day, an
        amount) the slots bring as an atom from the whole line, which stays in the reserve; a question
        the rules cannot read still has the matter, and the agent can ask or search."""
        episodes = {f"episode:{e.episode_id}": e for e in self.inp.subject.episodes}
        found: dict[str, Candidate] = {}
        for c in candidates:
            episode = episodes.get(c.item_id)
            if not _conversation(c) or episode is None:
                continue
            text = self._fit(bodies[c.item_id], episode, partial(self._fits_headline, c))
            if text is not None and len(text) < len(bodies[c.item_id]):
                found[c.item_id] = replace(c, text=text, masked_text=text)
        return found

    def _fits_headline(self, candidate: Candidate, text: str) -> bool:
        return self._cost(candidate, text) <= VOICE_CONVERSATION

    def _fit(self, body: str, episode: Episode, fits: Callable[[str], bool]) -> str | None:
        """A conversation line that `fits`: whole when it does, else with the summary cut to its first
        sentence without its reporting clause ("Marina relatou que"), then word by word to six words, the
        solution to four, then without the solution, then the summary to three words (tpl-12). None when
        not even that fits, or when the line is masked and has no summary to cut."""
        if fits(body):
            return body
        summary, resolution = episode.summary, episode.resolution or ""
        if summary not in body:
            return None
        sentence = _FIRST_SENTENCE.split(summary.strip(), maxsplit=1)[0]
        core = without_reporting(sentence)
        said, solved = core.split(), resolution.split()
        whole = len(said) if sentence == summary.strip() else len(said) + 1
        solution = f" · {self.fmt.word('solution')}: {resolution}"
        while True:
            text = body.replace(summary, _cut(said, whole), 1)
            if resolution and solution in text:
                if solved:
                    head, _, tail = text.rpartition(resolution)
                    text = head + _cut(solved, len(resolution.split())) + tail
                else:
                    text = text.replace(solution, "", 1)
            if fits(text):
                return text
            if len(said) > 6:
                said.pop()
            elif len(solved) > 4:
                solved.pop()
            elif solved:
                solved = []
            elif len(said) > 3:
                said.pop()
            else:
                return None

    def _repeat(
        self,
        matter: Episode | None,
        recent: Collection[str],
        visible: Sequence[Candidate],
        episodes: Mapping[str, Episode],
    ) -> tuple[Candidate, Candidate, Episode] | None:
        """When the newest recent conversation is a complaint that repeats an older one (the same reason,
        within `RECURRENCE_LOOKBACK`, of the same matter when the extraction keyed both, and at least one
        word of what happened in common: a reason is coarse, and "the internet dropped" does not repeat "the
        technician registered the visit"), the older one with the most words in common, the latest on a
        tie, as the recurrence line, with its date, outcome and solution. Words any report of any matter
        says ("the customer reported", "the agent logged it under protocol") are not in common: the local
        A/B of 27/09 printed "the same reason came up before" with a broken pot lid's protocol beside a sofa
        in the wrong color, sharing only "chegou", and an agent gave that protocol for the sofa (pt-149,
        en-151, en-155, en-161). No count: the per-turn slots count by the matter the customer names, and a
        count by reason here could disagree with it. Only conversations this reader may see are considered,
        and the line carries the older conversation's item, so the read-time recheck and lineage see it."""
        if matter is None or matter.intent != RECURRENCE_INTENT:
            return None
        since = matter.started_at - RECURRENCE_LOOKBACK
        same = [
            (c, e)
            for c in visible
            if _conversation(c)
            and (e := episodes.get(c.item_id)) is not None
            and e.intent == RECURRENCE_INTENT
            and e.normalized_category == matter.normalized_category
            and since <= e.started_at <= matter.started_at
            and _same_matter(matter, e)
        ]
        words = _happened_stems(matter.summary)
        older = [
            (c, e, shared)
            for c, e in same
            if c.item_id not in recent and (shared := len(words & _happened_stems(e.summary)))
        ]
        if not older:
            return None
        before, episode, _ = max(older, key=lambda p: (p[2], p[1].started_at, str(p[1].episode_id)))
        return replace(before, section=Section.HISTORY), before, episode

    def _reserve(
        self,
        visible: Sequence[Candidate],
        taken: Collection[str],
        decisions: Mapping[str, Decision],
        values: Mapping[str, int],
        bodies: Mapping[str, str],
    ) -> tuple[tuple[ReserveItem, ...], tuple[EpisodeRef, ...]]:
        """What the body left out, rendered as the pack would print it, and every visible episode's category
        and intent for the count by topic. Trimmed items stay out: the source never used them."""
        episodes = {f"episode:{e.episode_id}": e for e in self.inp.subject.episodes}
        left = sorted(
            (
                c
                for c in visible
                if c.item_id not in taken and c.section not in (Section.META, Section.HISTORY)
            ),
            key=lambda c: (-values[c.item_id], c.item_id),
        )[:RESERVE_MAX]
        reserve = []
        for c in sorted(left, key=lambda c: c.item_id):
            line = self.fmt.line(c.section, bodies[c.item_id], voice=self.voice)
            episode = episodes.get(c.item_id)
            reserve.append(
                ReserveItem(
                    c.item_id,
                    c.section,
                    line,
                    estimate_tokens(line + "\n"),
                    None if c.kind is ItemKind.SUMMARY else c.at,
                    _entry(c, decisions[c.item_id]),
                    episode.channel if episode else None,
                )
            )
        index = tuple(
            EpisodeRef(
                c.item_id, e.normalized_category, e.intent, e.started_at, e.channel, e.matter, e.matter_label
            )
            for c in visible
            if c.kind is ItemKind.EPISODE and (e := episodes.get(c.item_id)) is not None
        )
        return tuple(reserve), index

    def _policy(self, candidates: Sequence[Candidate]) -> dict[str, Decision]:
        return {
            c.item_id: evaluate(self.settings.policy, self.reader, c.attrs, self.context) for c in candidates
        }

    def _pool(
        self, visible: Sequence[Candidate], values: Mapping[str, int], bodies: Mapping[str, str]
    ) -> list[Candidate]:
        """Knapsack candidates: fixed sections out, traits capped per view, one tail summary at most."""
        pool = [
            c
            for c in visible
            if c.section not in (Section.META, Section.HISTORY) and values[c.item_id] >= MIN_VALUE
        ]
        by_value = sorted(pool, key=lambda c: (-values[c.item_id], c.item_id))
        ranked_traits = [
            c.item_id for c in by_value if c.kind is ItemKind.TRAIT and c.section is not Section.ACCOUNT
        ]
        traits, keep_traits = set(ranked_traits), set(ranked_traits[: self.spec.max_traits])
        # The longest tail summary that fits its section wins (5.10, P3).
        ceiling = self.spec.sections.get(Section.SUMMARY)
        summaries = sorted(
            (
                c
                for c in pool
                if c.kind is ItemKind.SUMMARY
                and (
                    ceiling is None
                    or ceiling.ceiling is None
                    or self._cost(c, bodies[c.item_id]) <= ceiling.ceiling
                )
            ),
            key=lambda c: (-len(bodies[c.item_id]), c.item_id),
        )
        keep_summary = {summaries[0].item_id} if summaries else set()
        return [
            c
            for c in pool
            if (c.item_id not in traits or c.item_id in keep_traits)
            and (c.kind is not ItemKind.SUMMARY or c.item_id in keep_summary)
        ]

    def _cost(self, candidate: Candidate, body: str) -> int:
        if candidate.section is Section.CUSTOMER:
            return estimate_tokens(" · " + body)
        return estimate_tokens(self.fmt.line(candidate.section, body, voice=self.voice) + "\n")

    def _fixed_lines(
        self,
        visible: Sequence[Candidate],
        as_of: datetime | None,
        withheld: int,
        for_level: int,
    ) -> list[tuple[Section, str]]:
        meta = f"{self.fmt.word('withheld')}: {withheld}" if withheld else ""
        if self.skeleton:
            # An agent that reads "withheld: 7" tells the customer it has nothing; one that reads why asks
            # for the verification instead (11, 1.2). At V0 nobody knows who is speaking, so the line tells
            # the agent to ask for it. At V1 it says nothing: there most of what the
            # customer asks is in the pack, and an agent told to verify first withheld that too (the local
            # A/B of 27/09 lost 12 answers so); a kind of value asked and withheld is said by the turn's
            # slots, and a bare count ("withheld: 2") told the agent nothing it acted on (tpl-12). It counts
            # only what the verification would release (`for_level`): an item kept for a purpose stays kept.
            unproven = self.variant.verification is Verification.V0
            meta = (
                self.fmt.word("withheld_ask_verification", count=for_level) if for_level and unproven else ""
            )
        if as_of is not None and not self.skeleton:
            # A skeleton leaves out when memory was last updated (17, 6.10; chat too since tpl-12): every
            # line it pins is dated, and the agent speaks from them, not from the timestamp of the pack.
            updated = f"{self.fmt.word('updated')} {self.fmt.timestamp(as_of)}"
            meta = f"{updated} · {meta}" if meta else updated
        lines = [(Section.META, self.fmt.line(Section.META, meta, voice=self.voice))] if meta else []
        # A skeleton leaves the history index out: the count comes as a slot when the turn asks for one.
        if self.spec.carries(Section.HISTORY) and as_of is not None and not self.skeleton:
            shown = {
                c.item_id for c in visible if c.kind is ItemKind.EPISODE and c.section is Section.EPISODES
            }
            episodes = [e for e in self.inp.subject.episodes if f"episode:{e.episode_id}" in shown]
            lines += [
                (Section.HISTORY, self.fmt.line(Section.HISTORY, t, voice=self.voice))
                for t in history_lines(episodes, self.fmt, as_of)
            ]
        return lines

    def _render(
        self,
        selected: Collection[str],
        by_id: Mapping[str, Candidate],
        bodies: Mapping[str, str],
        values: Mapping[str, int],
        fixed: Sequence[tuple[Section, str]],
    ) -> _Rendered:
        chosen = [by_id[i] for i in sorted(selected)]
        per_section: dict[Section, list[Candidate]] = {}
        for c in chosen:
            per_section.setdefault(c.section, []).append(c)
        fixed_by_section: dict[Section, list[str]] = {}
        for section, text in fixed:
            fixed_by_section.setdefault(section, []).append(text)

        out: list[str] = []
        lines: list[PackLine] = []
        variables: dict[str, str] = {}

        def emit(section: Section, text: str, item_id: str | None = None) -> None:
            out.append(text + "\n")
            lines.append(PackLine(section, text, item_id))
            variables[section.value] = (
                f"{variables[section.value]}\n{text}" if section.value in variables else text
            )

        # The year the dates below leave out, once, in a view that carries the tail summary (tpl-12): an agent
        # orders "05/09" against a summary's "2026-04-26" only when it knows which year "05/09" is. Every
        # other date a pack prints is written here, with its year when it is not this one.
        year = self.fmt.current_year if self.spec.carries(Section.SUMMARY) else None
        opening = self.fmt.word("tag_year", year=year) if year is not None else self.fmt.word("tag")
        out.append(f"<{opening}>\n{self.settings.b0_text}\n")
        b0_end = len("".join(out))
        # What must or must not be done opens the block, before any data, the account's first (2.7). At the
        # end of the account's lines, "não falar de valores com Clara" left an attendant telling Clara a
        # case's amount 10 times of 10 on the block served on 08/10/2026; first, 0 of 10, and Marcos still
        # got his amount 10 of 10.
        first = sorted(
            (c for c in chosen if self._pinned(c)),
            key=lambda c: (
                c.item_id in self._preferences,
                _INSTRUCTIONS_FIRST.index(c.section),
                -values.get(c.item_id, 0),
                c.item_id,
            ),
        )
        for c in first:
            emit(c.section, self.fmt.line(c.section, bodies[c.item_id], voice=self.voice), c.item_id)
        lead = dict(variables)
        per_section = {s: [c for c in cs if not self._pinned(c)] for s, cs in per_section.items()}
        for c in _edges(per_section.get(Section.ACCOUNT, []), values):
            emit(
                Section.ACCOUNT,
                self.fmt.line(Section.ACCOUNT, bodies[c.item_id], voice=self.voice),
                c.item_id,
            )
        account_end = len("".join(out))
        body_start = len(out)
        if customer := per_section.get(Section.CUSTOMER):
            # One composite line; the fragments were priced one by one in the knapsack.
            body = " · ".join(bodies[c.item_id] for c in customer)
            emit(Section.CUSTOMER, self.fmt.line(Section.CUSTOMER, body, voice=self.voice))
            lines[-1:] = [PackLine(Section.CUSTOMER, lines[-1].text, c.item_id) for c in customer]
        for section in (Section.FACTS, Section.STABLE_PATTERNS):
            # B1 keeps a canonical order, not a value order, so it stays byte-stable across compilations.
            for c in per_section.get(section, []):
                emit(section, self.fmt.line(section, bodies[c.item_id], voice=self.voice), c.item_id)
        b1_end = len("".join(out))
        for section in (Section.META, Section.HISTORY):
            for text in fixed_by_section.get(section, []):
                emit(section, text)
        for c in per_section.get(Section.HISTORY, []):
            # A skeleton's recurrence line: an older conversation, said as what the recent matter repeats.
            emit(
                Section.HISTORY,
                self.fmt.line(Section.HISTORY, bodies[c.item_id], voice=self.voice),
                c.item_id,
            )
        for section in (
            Section.SUMMARY,
            Section.EPISODES,
            Section.VOLATILE_PATTERNS,
            Section.ACTIONS,
            Section.OBJECTS,
            Section.PENDING,
        ):
            for c in _edges(per_section.get(section, []), values):
                emit(section, self.fmt.line(section, bodies[c.item_id], voice=self.voice), c.item_id)
        if self.spec.template is not None:
            # The space's layout of the customer block: the same lines, placed by the template. Only
            # the opening survives the layout byte for byte, so it alone counts as the stable prefix.
            texts = {
                name: _after(text, lead.get(name))
                for name, text in variables.items()
                if name in TEMPLATE_FIELDS
            }
            body = fill_template(self.spec.template, texts)
            out[body_start:] = [body + "\n"] if body else []
            b1_end = len("".join(out))
        out.append(f"</{self.fmt.word('tag')}>")
        variables |= {
            "b0": self.settings.b0_text,
            "view": self.variant.view,
            "verification": self.variant.verification.value,
        }
        return _Rendered("".join(out), tuple(lines), variables, b0_end, account_end, b1_end)

    def _manifest(
        self,
        candidates: Sequence[Candidate],
        decisions: Mapping[str, Decision],
        visible: Sequence[Candidate],
        selected: Collection[str],
        trimmed: Collection[str],
        values: Mapping[str, int],
        rendered: _Rendered,
        as_of: datetime | None,
        pack_hash: str,
        stable_tokens: int,
        held: Collection[str] = frozenset(),
        placed: Mapping[str, Candidate] | None = None,
        duplicates: Collection[str] = frozenset(),
    ) -> dict[str, Any]:
        visible_ids = {c.item_id for c in visible}
        items = []
        exclusions = []
        for c in candidates:
            decision = decisions[c.item_id]
            if c.item_id in selected:
                # An item the body placed in another section (the recurrence line) is entered where it is.
                items.append(_entry((placed or {}).get(c.item_id, c), decision))
            elif not decision.included:
                exclusions.append(
                    {
                        **_refs(c),
                        "category": c.attrs.category,
                        "reason": decision.reason.value if decision.reason else None,
                        "rule": decision.rule,
                    }
                )
            elif c.item_id in trimmed:
                # The source did not use this unit across enough deliveries (15.8, 19.2).
                exclusions.append(
                    {**_refs(c), "category": c.attrs.category, "reason": "unused", "rule": "context_use"}
                )
            else:
                rule = "budget" if values.get(c.item_id, 0) >= MIN_VALUE else "low_value"
                if c.item_id in held:
                    rule = "skeleton"  # the skeleton holds it for the per-turn slots (11, 3.3)
                if c.item_id not in visible_ids:
                    rule = "view"  # the view does not carry this section
                if c.item_id in duplicates:
                    rule = "duplicate"  # another open item says the same
                exclusions.append(
                    {
                        **_refs(c),
                        "category": c.attrs.category,
                        "reason": ExclusionReason.BUDGET.value,
                        "rule": rule,
                    }
                )
        section_tokens = Counter[str]()
        for line in rendered.lines:
            section_tokens[line.section.value] += estimate_tokens(line.text + "\n")
        text = rendered.text
        withheld = Counter(str(d.reason) for d in decisions.values() if not d.included)
        return {
            "spec": SPEC_VERSION,
            "compiler_version": COMPILER_VERSION,
            "template_version": TEMPLATE_VERSION,
            "policy_version": self.settings.policy.version,
            "extractor_version": self.settings.extractor_version,
            "token_estimator": ESTIMATOR,
            "variant": self.variant.key,
            "view": self.variant.view,
            "profile_id": str(self.inp.subject.profile.profile_id),
            "membership_version": self.inp.subject.profile.membership_version,
            "privacy_version": self.inp.subject.profile.privacy_version,
            "as_of": self.fmt.iso(as_of) if as_of else None,
            "items": items,
            "excluded": exclusions,
            "withheld": dict(sorted(withheld.items())),
            "tokens": {
                "b0": estimate_tokens(text[: rendered.b0_end]),
                "account": estimate_tokens(text[rendered.b0_end : rendered.account_end]),
                "b1": estimate_tokens(text[rendered.account_end : rendered.b1_end]),
                "b2": estimate_tokens(text[rendered.b1_end :]),
                "stable": stable_tokens,
                "total": estimate_tokens(text),
                "sections": dict(sorted(section_tokens.items())),
            },
            "flags": {
                "overdue_company_items": overdue_company_items(self.inp.subject.open_items),
                "sensitive_items": sum(1 for i in items if i["sensitivity"] == "sensitive"),
            },
            "pack_hash": pack_hash,
        }


def _echoed(
    selected: Collection[str], by_id: Mapping[str, Candidate], bodies: Mapping[str, str]
) -> frozenset[str]:
    """Facts of the body whose value a conversation the body pins already says (`repeats`): "order problem:
    A caixa chegou amassada" beside "O cliente relatou que a caixa chegou amassada". A masked fact says
    nothing a conversation can repeat, so it stays."""
    said = " ".join(bodies[i] for i in sorted(selected) if by_id[i].kind is ItemKind.EPISODE)
    if not said:
        return frozenset()
    found = set()
    for i in selected:
        if by_id[i].kind is not ItemKind.FACT or by_id[i].section is not Section.FACTS:
            continue
        value = _fact_value(bodies[i])
        if analyze(value).topic and repeats(said, value):
            found.add(i)
    return frozenset(found)


def _fact_value(body: str) -> str:
    """The value of a skeleton's fact line, "<label>: <value>"."""
    return body.partition(": ")[2]


def _repeated(
    visible: Sequence[Candidate], bodies: Mapping[str, str], values: Mapping[str, int]
) -> tuple[frozenset[str], dict[str, int]]:
    """Lines of a section that say the same bytes: the most valuable stays (ties by id) and says how many
    there were; the others leave the body and the reserve, as duplicates."""
    groups: dict[tuple[Section, str], list[Candidate]] = {}
    for c in visible:
        groups.setdefault((c.section, bodies[c.item_id]), []).append(c)
    gone: set[str] = set()
    counted: dict[str, int] = {}
    for same in groups.values():
        if len(same) < 2:
            continue
        keep, *rest = sorted(same, key=lambda c: (-values[c.item_id], c.item_id))
        counted[keep.item_id] = len(same)
        gone.update(c.item_id for c in rest)
    return frozenset(gone), counted


def _duplicate_items(
    visible: Sequence[Candidate], values: Mapping[str, int], kept: Collection[str] = frozenset()
) -> frozenset[str]:
    """Open items whose description another visible one of the same section and state (open, or kept)
    already says (`repeats`: every number of it, and most of its words, as "Concluir o estorno da compra
    cancelada" beside "Estorno da compra cancelada aberto; não há confirmação de conclusão"); the most
    valuable stays, ties by id. Two promises that name different numbers are never one."""
    seen: dict[tuple[Section, bool], list[str]] = {}
    found: set[str] = set()
    for c in sorted(visible, key=lambda c: (-values[c.item_id], c.item_id)):
        if c.kind is not ItemKind.OPEN_ITEM:
            continue
        said = c.text.partition(" · ")[0].rstrip(".")
        group = seen.setdefault((c.section, c.item_id in kept), [])
        if any(repeats(other, said) for other in group):
            found.add(c.item_id)
        else:
            group.append(said)
    return frozenset(found)


_FIRST_SENTENCE = re.compile(r"(?<=[.!?])\s+")


_NEGATIONS = frozenset({"não", "nao", "nem", "sem", "nunca", "not", "no", "never", "without", "nor"})
"""Words a cut line never ends on: "the visit did not..." reads as the opposite of what happened."""


def _cut(words: Sequence[str], whole: int) -> str:
    """The words kept, marked as cut when some were left out, never ending on a word of two letters or on
    a negation, whose verb the cut would leave out."""
    if len(words) >= whole:
        return " ".join(words)
    kept = list(words)
    while len(kept) > 1 and (
        len(kept[-1].strip(",;:.")) <= 2 or kept[-1].strip(",;:.").lower() in _NEGATIONS
    ):
        kept.pop()
    return " ".join(kept).rstrip(",;:.") + "..."


def _content_stems(text: str) -> frozenset[str]:
    """The stems of a summary's content words, without stop words, time words or channel names."""
    return frozenset(stem(word) for word in analyze(text).topic)


_REPORT_TEXT = """
    cliente clientes customer customers client clients agente agentes agent agents atendente atendentes
    empresa company equipe team atendimento suporte support caso case mensagem message relata relatou
    relatam reporta reportou reported reports report reportar informa informou informed informs inform
    registra registrou registrado registrada registro logged log logs registered registrar protocolo
    protocol chamado ticket numero number disse said pediu pede asked asks solicitou requested request
    escreveu wrote writes escreve confirmou confirmed recebeu received analisar analyze investigar
    investigate verificar check look
"""
_REPORT_STEMS = frozenset(stem(w) for w in fold_words(_REPORT_TEXT))
"""What a summary of any complaint says of how it was reported and handled, never what happened."""


def _happened_stems(text: str) -> frozenset[str]:
    """A summary's content stems without the words of any report (`_REPORT_STEMS`) or its numbers."""
    return frozenset(s for s in _content_stems(text) if s not in _REPORT_STEMS and not s.isdigit())


def _same_matter(one: Episode, other: Episode) -> bool:
    """Whether two conversations may be of one matter: the extraction keyed them the same way (two
    spellings of a key meet), or it keyed at most one of them."""
    if one.matter is None or other.matter is None:
        return True
    return signature(one.matter) == signature(other.matter)


def _conversation(c: Candidate) -> bool:
    return c.kind is ItemKind.EPISODE and c.section is Section.EPISODES


_INSTRUCTIONS_FIRST = (
    Section.ACCOUNT,
    Section.CUSTOMER,
    Section.OBJECTS,
    Section.PENDING,
)
"""The sections an instruction is said in (`_said_in`), in the order their instructions open the block: the
restrictions first, then the contact preferences, which only say how the team reaches someone."""


def _after(text: str, lead: str | None) -> str:
    """A section's lines without the instructions that open the block: a template places only the rest."""
    return text[len(lead) :].lstrip("\n") if lead and text.startswith(lead) else text


def _edges(candidates: Sequence[Candidate], values: Mapping[str, int]) -> list[Candidate]:
    """Highest values at the two ends of a block, lowest in the middle (15.5, rule 3)."""
    ranked = sorted(candidates, key=lambda c: (-values[c.item_id], c.item_id))
    return ranked[0::2] + ranked[1::2][::-1]


def history_lines(episodes: Sequence[Episode], fmt: Formatter, as_of: datetime) -> list[str]:
    """The "From history" bridge (16.1): an index line and, when it recurs, the last reason's count,
    together within about `HISTORY_TOKENS`; the last solution, free text, is what gets shortened."""
    if not episodes:
        return []
    counts = count_by_category(episodes)
    first = min(e.started_at for e in episodes)
    index = [f"{len(episodes)} {fmt.word('conversations_since')} {fmt.year(first)}"]
    index += [f"{fmt.value(c.category)} ({c.occurrences})" for c in counts[:3]]
    tail: list[str] = []
    latest = max(episodes, key=lambda e: (e.started_at, str(e.episode_id)))
    repeat = recurrence(episodes, latest.normalized_category, now=as_of)
    if repeat is not None and repeat.occurrences >= 2:
        times = fmt.word(
            "times_in_days",
            count=repeat.occurrences,
            days=RECURRENCE_WINDOW_DAYS,
            date=fmt.date(repeat.last_at),
        )
        tail.append(f"{fmt.value(repeat.category)}: {times}")
    solved = [e for e in episodes if e.resolution]
    if solved:
        last = max(solved, key=lambda e: (e.started_at, str(e.episode_id)))
        room = HISTORY_TOKENS - estimate_tokens(" · ".join(index)) - sum(estimate_tokens(t) for t in tail)
        solution = _solution(fmt, last.resolution or "", fmt.date(last.started_at), room)
        if solution:
            index.append(solution)
    while len(index) > 2 and estimate_tokens(" · ".join(index)) + sum(estimate_tokens(t) for t in tail) > (
        HISTORY_TOKENS
    ):
        index.pop()  # long category names: the least frequent goes first
    return [" · ".join(index), *tail]


def _solution(fmt: Formatter, resolution: str, date: str, room: int) -> str | None:
    """`last solution: <text> (<date>)` in at most `room` tokens, cut at a word; None when not even a
    few words fit."""
    words = resolution.split()
    while words:
        text = " ".join(words)
        cut = text if len(words) == len(resolution.split()) else f"{text}..."
        line = f"{fmt.word('last_solution')}: {cut} ({date})"
        if estimate_tokens(" · " + line) <= room:
            return line
        words.pop()
    return None


def compile_pack(inp: CompileInput) -> CompiledPack:
    return _Compilation(inp).run()


def variant_reader(variant: Variant, spec: ViewSpec) -> tuple[Reader, PolicyContext]:
    """Who reads a variant and under which level: the pack and the `live` turns beside it both pass the
    policy with these, so the two can never disagree. A task view narrows the reader's purposes."""
    purposes = variant.purposes & spec.purposes if spec.purposes else variant.purposes
    reader = Reader(variant.audience, purposes, variant.source_id)
    context = PolicyContext(
        variant.verification, variant.link_role, variant.can_see_contacts, variant.deny_sensitive
    )
    return reader, context


def _entry(c: Candidate, decision: Decision) -> dict[str, Any]:
    """An item's manifest entry: what lineage and measurement match it by, and what the read-time recheck
    passes through the policy again."""
    return {
        **_refs(c),
        "section": c.section.value,
        "layer": c.section.layer.value,
        "category": c.attrs.category,
        "sensitivity": c.attrs.sensitivity.value,
        "visibility": c.attrs.visibility.value,
        "subject_kind": c.attrs.subject_kind.value,
        "handles": [str(h) for h in c.attrs.handle_ids],
        "other_contact": c.attrs.other_contact,
        "purposes": sorted(c.attrs.purposes),
        "topics": list(c.attrs.topics),
        "audiences": sorted(a.value for a in c.attrs.audiences),
        "effect": decision.effect.value,
        # The mirror rule's inputs, so the read-time recheck decides as the compiler did (5.12).
        **({"spoken_by": c.attrs.spoken_by.value} if c.attrs.spoken_by else {}),
        **({"own_words": True} if c.attrs.own_words else {}),
        **({"rule": decision.rule} if decision.rule == MIRROR_RULE else {}),
    }


def _refs(c: Candidate) -> dict[str, Any]:
    """What lineage (5.13) and context-use measurement (19.2) match an item by: its public id, its
    origin events, and the predicate or object it is about. Ids and names only; values never enter a
    manifest."""
    refs: dict[str, Any] = {
        "id": public_item_id(c.item_id),
        "kind": c.kind.value,
        "evidence_event_ids": list(c.origin_events),
    }
    if c.predicate is not None:
        refs["predicate"] = c.predicate
    if c.object_id is not None:
        refs["object_id"] = str(c.object_id)
    if c.object_ref is not None:
        refs["object_ref"] = {
            "type": c.object_ref.type,
            "namespace": c.object_ref.namespace,
            "id": c.object_ref.external_id,
        }
    if c.bound:
        refs["bound"] = [{"kind": kind, "id": row_id} for kind, row_id in c.bound]
    return refs


def _object_included(decision: Decision | None) -> bool:
    return decision is not None and decision.included and decision.effect is Effect.ALLOW


def says_contact_time(pack: CompiledPack | None) -> bool:
    """Whether the pack says the subject's contact time as a contact preference (`_contact_time`), in the
    clear: a block beside it need not say the same hours again."""
    if pack is None:
        return False
    items = pack.manifest.get("items") or ()
    return any(i.get("predicate") == CONTACT_TIME_FACT and i.get("effect") == "allow" for i in items)


def render_delta(
    pack: CompiledPack, seen: Collection[str], since: datetime | None, *, timezone: str, locale: str
) -> str | None:
    """Items of `pack` the agent has not seen, as a block to append at the end of the prompt (2.4)."""
    new = [line.text for line in pack.lines if line.item_id and line.item_id not in seen]
    if not new:
        return None
    fmt = Formatter(timezone, locale)
    head = fmt.word("updates_since") + (f" {fmt.timestamp(since)}" if since else "")
    return "\n".join([f"[{head}]", *dict.fromkeys(new)])
