"""In-memory `CoordinateRepository` for unit tests, with the same rules as the SQL: an effect keeps its latest
state, a release or a lift only ends what it names, a suppression's change moves its sequence, a contact
counts each use once, a budget spend takes only a settlement after it is written, and a commitment only
leaves a state that still holds."""

import dataclasses
from collections import Counter
from collections.abc import Collection, Sequence
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from niadra.adapters.memory.mentions import index_in, mentions_of
from niadra.adapters.memory.store import MemoryStore
from niadra.domain.coordinate.model import EffectSnapshot, EffectState
from niadra.domain.coordinate.records import (
    CommitmentRecord,
    ContactRecord,
    OwnershipRecord,
    SpaceBatch,
    SpendRecord,
    SuppressionRecord,
)
from niadra.domain.privacy.erasure import ErasedRow, ErasureScope, TargetKind
from niadra.domain.privacy.mentions import MentionKey, MentionStore
from niadra.ports.repos.coordinate import (
    CommitmentRow,
    HandoffRow,
    Overview,
    ReportRow,
    SealedOutcome,
    SealedTerms,
    SpendRow,
    SuppressionRow,
    TargetState,
)


class InMemoryCoordinateRepository:
    def __init__(self, store: MemoryStore) -> None:
        self._s = store.module("coordinate")
        self._effects: dict[bytes, EffectSnapshot] = self._s.setdefault("effects", {})
        self._claims: dict[UUID, OwnershipRecord] = self._s.setdefault("claims", {})
        self._suppressions: dict[UUID, SuppressionRow] = self._s.setdefault("suppressions", {})
        self._seq: list[int] = self._s.setdefault("seq", [0])
        self.contacts: dict[UUID, ContactRecord] = self._s.setdefault("contacts", {})
        self.spends: dict[UUID, SpendRecord] = self._s.setdefault("spends", {})
        self.commitments: dict[UUID, CommitmentRecord] = self._s.setdefault("commitments", {})
        self.effect_subjects: dict[bytes, bytes] = self._s.setdefault("effect_subjects", {})
        self.handoffs: dict[UUID, HandoffRow] = self._s.setdefault("handoffs", {})
        self.outcomes: dict[UUID, SealedOutcome] = self._s.setdefault("outcomes", {})
        self._mentions = mentions_of(store)
        self.report_rows: dict[UUID, ReportRow] = self._s.setdefault("reports", {})
        self.writes: list[SpaceBatch] = self._s.setdefault("writes", [])

    async def write(self, batch: SpaceBatch) -> None:
        self.writes.append(batch)
        for effect in batch.effects:
            held = self._effects.get(effect.key_hash)
            if held is None or held.order <= effect.snapshot.order:
                self._effects[effect.key_hash] = effect.snapshot
            if effect.subject_hmac is not None:
                self.effect_subjects[effect.key_hash] = effect.subject_hmac
        for claim in batch.claims:
            held_claim = self._claims.get(claim.claim_id)
            released = held_claim.released_at_ms if held_claim is not None else None
            self._claims[claim.claim_id] = dataclasses.replace(
                claim, released_at_ms=released or claim.released_at_ms
            )
        for release in batch.releases:
            found = self._claims.get(release.claim_id)
            if found is not None and found.released_at_ms is None:
                self._claims[release.claim_id] = dataclasses.replace(
                    found, released_at_ms=release.released_at_ms
                )
        for entry in batch.suppressions:
            held_row = self._suppressions.get(entry.suppression_id)
            self._suppressions[entry.suppression_id] = SuppressionRow(
                suppression_id=entry.suppression_id,
                handle_hmac=entry.handle_hmac,
                handle_ct=entry.handle_ct,
                dek_version=entry.dek_version,
                purpose=entry.purpose,
                channel=entry.channel,
                source_id=entry.source_id,
                since=held_row.since if held_row is not None else _at(entry.since_ms),
                until=_at(entry.until_ms) if entry.until_ms is not None else None,
                lifted_at=held_row.lifted_at
                if held_row is not None and held_row.lifted_at
                else _lifted(entry),
                change_seq=self._next(),
                window=entry.window,
            )
        for lift in batch.lifts:
            row = self._suppressions.get(lift.suppression_id)
            if row is not None and row.lifted_at is None:
                self._suppressions[lift.suppression_id] = dataclasses.replace(
                    row, lifted_at=_at(lift.lifted_at_ms), change_seq=self._next()
                )
        for contact in batch.contacts:
            held_contact = self.contacts.get(contact.contact_id)
            if held_contact is None:
                self.contacts[contact.contact_id] = contact
                continue
            self.contacts[contact.contact_id] = dataclasses.replace(
                held_contact,
                state="made" if contact.state == "made" else held_contact.state,
                reserved_at_ms=held_contact.reserved_at_ms or contact.reserved_at_ms,
                used_at_ms=max(
                    (t for t in (held_contact.used_at_ms, contact.used_at_ms) if t is not None), default=None
                ),
                decision_id=held_contact.decision_id or contact.decision_id,
                use_ids=tuple(dict.fromkeys((*held_contact.use_ids, *contact.use_ids))),
            )
        for spend in batch.spends:
            if spend.spend_id not in self.spends or spend.settled:
                self.spends[spend.spend_id] = spend
        for commitment in batch.commitments:
            self.commitments.setdefault(commitment.commitment_id, commitment)
        named = [
            (MentionKey(MentionStore.COMMITMENT, str(m.commitment_id)), frozenset(m.named))
            for m in batch.commitments
        ]
        index_in(self._mentions["rows"], named, replace=False)
        for status in batch.statuses:
            made = self.commitments.get(status.commitment_id)
            open_ = made is not None and made.status in ("active", "superseded")
            if made is not None and open_ and (made.status == "active" or status.status != "superseded"):
                self.commitments[status.commitment_id] = dataclasses.replace(made, status=status.status)

    async def effect(self, key_hash: bytes) -> EffectSnapshot | None:
        return self._effects.get(key_hash)

    async def target(self, target: bytes, *, kind: str, now: datetime) -> TargetState:
        return self._target(target, kind, now)

    async def live_effects(
        self, now: datetime, after: bytes | None, limit: int
    ) -> list[tuple[bytes, EffectSnapshot]]:
        now_ms = _ms(now)
        live = sorted(
            (key, snapshot)
            for key, snapshot in self._effects.items()
            if (after is None or key > after)
            and (snapshot.ttl_ms is None or snapshot.reserved_at_ms + snapshot.ttl_ms > now_ms)
        )
        return live[:limit]

    async def live_targets(
        self, now: datetime, after: tuple[str, bytes] | None, limit: int
    ) -> list[TargetState]:
        names = {
            ("object", c.object.encode()) if c.object else ("subject", c.subject_hmac or b"")
            for c in self._claims.values()
        }
        names |= {("subject", row.handle_hmac) for row in self._suppressions.values()}
        names |= {(spend.target_kind, spend.target) for spend in self.spends.values()}
        names |= {
            ("subject", m.subject_hmac) for m in self.commitments.values() if m.subject_hmac is not None
        }
        found = [self._target(name, kind, now) for kind, name in sorted(names)]
        live = [
            t
            for t in found
            if (t.claims or t.suppressions or t.spends or t.commitments)
            and (after is None or (t.kind, t.target) > after)
        ]
        return live[:limit]

    async def suppression_changes(
        self, after_seq: int | None, limit: int, now: datetime
    ) -> Sequence[SuppressionRow]:
        rows = sorted(self._suppressions.values(), key=lambda row: row.change_seq)
        if after_seq is None:
            rows = [row for row in rows if _in_force(row, now)]
        else:
            rows = [row for row in rows if row.change_seq > after_seq]
        return rows[:limit]

    def _target(self, target: bytes, kind: str, now: datetime) -> TargetState:
        now_ms = _ms(now)
        claims = tuple(
            claim
            for claim in self._claims.values()
            if claim.released_at_ms is None
            and claim.valid_until_ms > now_ms
            and (
                (claim.object or "").encode() == target
                if kind == "object"
                else kind == "subject" and claim.subject_hmac == target
            )
        )
        suppressions = tuple(
            row
            for row in self._suppressions.values()
            if kind == "subject" and row.handle_hmac == target and _in_force(row, now)
        )
        spends = tuple(
            SpendRow(s.spend_id, s.budget, s.slice, s.units, _at(s.spent_at_ms), _at(s.until_ms))
            for s in self.spends.values()
            if s.target_kind == kind and s.target == target and s.until_ms > now_ms
        )
        commitments = tuple(
            CommitmentRow(
                m.external_id,
                m.commitment_id,
                m.type,
                m.compatibility,
                _at(m.valid_until_ms),
                m.score,
                m.agent,
                _at(m.made_at_ms),
            )
            for m in self.commitments.values()
            if kind == "subject"
            and m.subject_hmac == target
            and m.status == "active"
            and m.valid_until_ms > now_ms
        )
        return TargetState(target, kind, claims, suppressions, spends, commitments)

    def _next(self) -> int:
        self._seq[0] += 1
        return self._seq[0]

    async def subject_effects(
        self, subject_hmac: bytes, now: datetime, limit: int
    ) -> list[tuple[bytes, EffectSnapshot]]:
        now_ms = _ms(now)
        found = [
            (key, snapshot)
            for key, snapshot in self._effects.items()
            if self.effect_subjects.get(key) == subject_hmac
            and (snapshot.ttl_ms is None or snapshot.reserved_at_ms + snapshot.ttl_ms > now_ms)
        ]
        return sorted(found, key=lambda pair: pair[1].reserved_at_ms, reverse=True)[:limit]

    async def add_handoff(self, handoff: HandoffRow) -> None:
        self.handoffs[handoff.handoff_id] = handoff
        self._index_handoff(handoff.handoff_id, handoff.named)

    def _index_handoff(self, handoff_id: UUID, named: frozenset[bytes]) -> None:
        index_in(
            self._mentions["rows"],
            [(MentionKey(MentionStore.HANDOFF, str(handoff_id)), named)],
            replace=False,
        )

    async def handoff(self, handoff_id: UUID) -> HandoffRow | None:
        return self.handoffs.get(handoff_id)

    async def move_handoff(
        self,
        handoff_id: UUID,
        *,
        status: str,
        from_statuses: Sequence[str],
        at: datetime,
        outcome: str | None = None,
        outcome_ct: SealedOutcome | None = None,
        named: frozenset[bytes] = frozenset(),
    ) -> HandoffRow | None:
        found = self.handoffs.get(handoff_id)
        if found is None or found.status not in from_statuses:
            return None
        if outcome_ct is not None:
            self.outcomes[handoff_id] = outcome_ct
        self._index_handoff(handoff_id, named)
        moved = dataclasses.replace(
            found,
            status=status,
            outcome=outcome or found.outcome,
            accepted_at=at if status == "accepted" else found.accepted_at,
            closed_at=at if status in ("closed", "expired") else found.closed_at,
        )
        self.handoffs[handoff_id] = moved
        return moved

    async def terms_of(self, commitment_ids: Sequence[UUID]) -> list[SealedTerms]:
        return [_terms(self.commitments[c]) for c in commitment_ids if c in self.commitments]

    async def terms_page(self, after: UUID | None, limit: int) -> list[SealedTerms]:
        return [_terms(self.commitments[c]) for c in sorted(self.commitments) if after is None or c > after][
            :limit
        ]

    async def rewrite_terms(self, terms: SealedTerms) -> None:
        if (made := self.commitments.get(terms.commitment_id)) is not None:
            self.commitments[terms.commitment_id] = dataclasses.replace(
                made, terms_ct=terms.terms_ct, dek_version=terms.dek_version
            )

    async def outcome_of(self, handoff_id: UUID) -> SealedOutcome | None:
        return self.outcomes.get(handoff_id)

    async def handoffs_page(self, after: UUID | None, limit: int) -> list[HandoffRow]:
        return [self.handoffs[h] for h in sorted(self.handoffs) if after is None or h > after][:limit]

    async def rewrite_handoff(self, handoff: HandoffRow, outcome: SealedOutcome | None) -> None:
        if handoff.handoff_id in self.handoffs:
            self.handoffs[handoff.handoff_id] = handoff
            if outcome is not None:
                self.outcomes[handoff.handoff_id] = outcome

    async def add_report(self, report_id: UUID, days: int, requested_at: datetime) -> None:
        self.report_rows[report_id] = ReportRow(report_id, "pending", days, requested_at, None, {})

    async def finish_report(
        self,
        report_id: UUID,
        *,
        status: str,
        summary: dict[str, Any],
        sample_ct: bytes | None,  # noqa: ARG002 (kept only by the SQL)
        dek_version: int | None,  # noqa: ARG002
        at: datetime,
    ) -> None:
        found = self.report_rows[report_id]
        self.report_rows[report_id] = dataclasses.replace(
            found, status=status, summary=summary, finished_at=at
        )

    async def report(self, report_id: UUID) -> ReportRow | None:
        return self.report_rows.get(report_id)

    async def reports(self, before: UUID | None, limit: int) -> list[ReportRow]:
        rows = sorted(self.report_rows.values(), key=lambda r: r.report_id, reverse=True)
        return [r for r in rows if before is None or r.report_id < before][:limit]

    async def overview(self, since: datetime, now: datetime, limits: dict[str, int]) -> Overview:
        since_ms, now_ms = _ms(since), _ms(now)
        live = [c for c in self._claims.values() if c.released_at_ms is None and c.valid_until_ms > now_ms]
        ownership: dict[tuple[str, str, str, str], set[str]] = {}
        counted: Counter[tuple[str, str, str, str]] = Counter()
        for c in live:
            key = (c.kind, c.level, c.via, c.holder_kind)
            counted[key] += 1
            ownership.setdefault(key, set()).add(c.holder)
        effects = Counter(
            (e.kind, e.state.value) for e in self._effects.values() if e.reserved_at_ms >= since_ms
        )
        totals: dict[str, dict[bytes, int]] = {}
        for spend in self.spends.values():
            if spend.budget in limits and spend.until_ms > now_ms:
                per = totals.setdefault(spend.budget, {})
                per[spend.target] = per.get(spend.target, 0) + spend.units
        contacts = Counter(
            (c.purpose, "outbound", c.state)
            for c in self.contacts.values()
            if (c.reserved_at_ms or c.used_at_ms or 0) >= since_ms
        )
        return Overview(
            ownership=[(*k, counted[k], len(h)) for k, h in sorted(ownership.items())],
            effects=[(*k, n) for k, n in sorted(effects.items())],
            awaiting_decision=sum(
                1 for e in self._effects.values() if e.state is EffectState.UNKNOWN_OUTCOME
            ),
            overlaps=0,
            commitments_superseded=0,
            handoffs_expired=sum(
                1
                for h in self.handoffs.values()
                if h.created_at >= since
                and (
                    h.status == "expired"
                    or (h.status in ("created", "accepted") and h.expected_by and h.expected_by < now)
                )
            ),
            tokens_reused=await self.tokens_used_twice(since),
            budgets=[
                (b, len(per), sum(1 for v in per.values() if v >= limits[b]), sum(per.values()))
                for b, per in sorted(totals.items())
            ],
            contacts=[(*k, n) for k, n in sorted(contacts.items())],
        )

    async def tokens_used_twice(self, since: datetime) -> int:
        return sum(
            1 for c in self.contacts.values() if len(c.use_ids) > 1 and (c.used_at_ms or 0) >= _ms(since)
        )

    async def erase_for(self, scope: ErasureScope, *, limit: int) -> list[ErasedRow]:
        if not scope.subject_hashes or scope.kind is TargetKind.CONVERSATION:
            return []
        subjects = set(scope.subject_hashes)
        erased: list[ErasedRow] = []
        tables: list[tuple[str, dict[Any, Any], Any]] = [
            ("ownership_claims", self._claims, lambda c: c.subject_hmac in subjects),
            ("contact_log", self.contacts, lambda c: c.subject_hmac in subjects),
            ("budget_spends", self.spends, lambda s: s.target_kind == "subject" and s.target in subjects),
            ("effects", self._effects, lambda _: False),
            ("commitments", self.commitments, lambda m: m.subject_hmac in subjects),
            ("handoffs", self.handoffs, lambda h: h.subject_hmac in subjects),
        ]
        for table, rows, doomed in tables:
            keys = [
                key
                for key, row in rows.items()
                if doomed(row) or (table == "effects" and self.effect_subjects.get(key) in subjects)
            ]
            for key in keys[: max(limit - len(erased), 0)]:
                del rows[key]
                erased.append(ErasedRow(table, {"id": key.hex() if isinstance(key, bytes) else str(key)}))
        return erased

    async def drop_expired_contact_weeks(self, now: datetime, held_subjects: Collection[bytes] = ()) -> int:  # noqa: ARG002
        return 0  # no weekly partitions in memory

    async def expire(
        self,
        now: datetime,
        *,
        limit: int,
        held_subjects: Collection[bytes] = (),
        held_handles: Collection[UUID] = (),  # noqa: ARG002 (this copy expires no claim or suppression)
    ) -> dict[str, int]:
        now_ms = _ms(now)
        gone = [
            key
            for key, s in self.spends.items()
            if s.until_ms < now_ms and not (s.target_kind == "subject" and s.target in held_subjects)
        ][:limit]
        for key in gone:
            del self.spends[key]
        late = [
            h
            for h in self.handoffs.values()
            if h.status in ("created", "accepted") and h.expected_by is not None and h.expected_by < now
        ][:limit]
        for handoff in late:
            self.handoffs[handoff.handoff_id] = dataclasses.replace(
                handoff, status="expired", closed_at=handoff.expected_by
            )
        return {"budget_spends": len(gone), "handoffs_expired": len(late)}


def _in_force(row: SuppressionRow, now: datetime) -> bool:
    return row.lifted_at is None and (row.until is None or row.until > now)


def _lifted(entry: SuppressionRecord) -> datetime | None:
    return _at(entry.lifted_at_ms) if entry.lifted_at_ms is not None else None


def _ms(at: datetime) -> int:
    return int(at.timestamp() * 1000)


def _at(ms: int) -> datetime:
    return datetime.fromtimestamp(ms / 1000, UTC)


def _terms(made: CommitmentRecord) -> SealedTerms:
    return SealedTerms(made.commitment_id, made.subject_hmac, made.terms_ct, made.dek_version)
