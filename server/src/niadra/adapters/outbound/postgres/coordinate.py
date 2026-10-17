"""PostgreSQL implementation of `CoordinateRepository` (24.3.4): the batch writer's one statement, the reads
that reload the state store after it came back empty, the handoffs, the shadow reports, the retention of
each class and the erasure of a subject.

A batch is written as one statement of data-modifying CTEs over `jsonb_to_recordset`, one per kind of record,
whatever its size (24.6: one statement per batch of up to 500). An effect's row moves only to a later state
(its attempt, then when it was last settled); a claim's release and a suppression's lift only end a row still
open. Binary values travel as hex, times as epoch milliseconds.
"""

import json
from collections.abc import Collection, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from niadra.adapters.outbound.postgres.mentions import NAMED_CTE, named_param
from niadra.adapters.outbound.postgres.sealer import Sealer
from niadra.domain.common.ids import uuid7_floor
from niadra.domain.coordinate.model import EffectSnapshot, EffectState, Settler
from niadra.domain.coordinate.records import OwnershipRecord, SpaceBatch
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

SPACE = "CAST(current_setting('niadra.space_id') AS uuid)"
CONTACTS_KEPT = timedelta(days=90)


def _ts(column: str) -> str:
    return f"to_timestamp(r.{column} / 1000.0)"


WRITE = text(
    f"""
WITH e AS (
  INSERT INTO effects AS t (space_id, effect_key_hash, kind, state, fact_type, fact_namespace,
    fact_external_id, subject_hmac, source_id, agent, attempt, cost_units, reserved_at, lease_until, done_at,
    settled_at, settled_by, key_valid_until)
  SELECT {SPACE}, decode(r.key, 'hex'), r.kind, r.state, r.fact_type, r.fact_namespace, r.fact_id,
    decode(r.subject, 'hex'), r.source_id, r.agent, r.attempt, r.cost, {_ts("reserved_at")},
    {_ts("lease_until")}, {_ts("done_at")}, {_ts("settled_at")}, r.settled_by, {_ts("valid_until")}
  FROM jsonb_to_recordset(CAST(:effects AS jsonb)) AS r(key text, kind text, state text, fact_type text,
    fact_namespace text, fact_id text, subject text, source_id uuid, agent text, attempt int, cost bigint,
    reserved_at bigint, lease_until bigint, done_at bigint, settled_at bigint, settled_by text,
    valid_until bigint)
  ON CONFLICT (space_id, effect_key_hash) DO UPDATE SET kind = EXCLUDED.kind, state = EXCLUDED.state,
    subject_hmac = coalesce(EXCLUDED.subject_hmac, t.subject_hmac),
    fact_type = EXCLUDED.fact_type, fact_namespace = EXCLUDED.fact_namespace,
    fact_external_id = EXCLUDED.fact_external_id, source_id = EXCLUDED.source_id, agent = EXCLUDED.agent,
    attempt = EXCLUDED.attempt, cost_units = EXCLUDED.cost_units, reserved_at = EXCLUDED.reserved_at,
    lease_until = EXCLUDED.lease_until, done_at = EXCLUDED.done_at, settled_at = EXCLUDED.settled_at,
    settled_by = EXCLUDED.settled_by, key_valid_until = EXCLUDED.key_valid_until
  WHERE (t.attempt, coalesce(t.settled_at, t.reserved_at))
     <= (EXCLUDED.attempt, coalesce(EXCLUDED.settled_at, EXCLUDED.reserved_at))
  RETURNING 1
), c AS (
  INSERT INTO ownership_claims AS t (space_id, claim_id, target, subject_hmac, object_type,
    object_namespace, object_external_id, kind, task, holder_kind, holder, level, via, source_id, observer,
    basis_event_id, claimed_at, valid_until, intents, epoch, released_at)
  SELECT {SPACE}, r.claim_id, CASE WHEN r.subject IS NULL THEN 'object' ELSE 'subject' END,
    decode(r.subject, 'hex'), r.object_type, r.object_namespace, r.object_id, r.kind, r.task, r.holder_kind,
    r.holder, r.level, r.via, r.source_id, r.observer, r.basis_event_id, {_ts("claimed_at")},
    {_ts("valid_until")}, ARRAY(SELECT jsonb_array_elements_text(r.intents)), r.epoch, {_ts("released_at")}
  FROM jsonb_to_recordset(CAST(:claims AS jsonb)) AS r(claim_id uuid, subject text, object_type text,
    object_namespace text, object_id text, kind text, task text, holder_kind text, holder text, level text,
    via text, source_id uuid, observer text, basis_event_id uuid, claimed_at bigint, valid_until bigint,
    intents jsonb, epoch bigint, released_at bigint)
  ON CONFLICT (space_id, claim_id) DO UPDATE SET released_at = coalesce(t.released_at, EXCLUDED.released_at)
  RETURNING 1
), cr AS (
  UPDATE ownership_claims AS t SET released_at = {_ts("released_at")}
  FROM jsonb_to_recordset(CAST(:releases AS jsonb)) AS r(claim_id uuid, released_at bigint)
  WHERE t.space_id = {SPACE} AND t.claim_id = r.claim_id AND t.released_at IS NULL
  RETURNING 1
), s AS (
  INSERT INTO suppressions AS t (space_id, suppression_id, handle_type, handle_hmac, handle_ct, dek_version,
    purpose, channel, reason, source_id, since, until, resume_after_s, lifted_at, contact_window)
  SELECT {SPACE}, r.id, r.handle_type, decode(r.hmac, 'hex'), decode(r.ct, 'hex'), r.dek, r.purpose,
    r.channel, r.reason, r.source_id, {_ts("since")}, {_ts("until")}, r.resume, {_ts("lifted_at")}, r.cw
  FROM jsonb_to_recordset(CAST(:suppressions AS jsonb)) AS r(id uuid, handle_type text, hmac text, ct text,
    dek int, purpose text, channel text, reason text, source_id uuid, since bigint, until bigint, resume int,
    lifted_at bigint, cw text)
  ON CONFLICT (space_id, suppression_id) DO UPDATE SET until = EXCLUDED.until, reason = EXCLUDED.reason,
    resume_after_s = EXCLUDED.resume_after_s, lifted_at = coalesce(t.lifted_at, EXCLUDED.lifted_at),
    contact_window = EXCLUDED.contact_window
  RETURNING 1
), sl AS (
  UPDATE suppressions AS t SET lifted_at = {_ts("lifted_at")}
  FROM jsonb_to_recordset(CAST(:lifts AS jsonb)) AS r(id uuid, lifted_at bigint)
  WHERE t.space_id = {SPACE} AND t.suppression_id = r.id AND t.lifted_at IS NULL
  RETURNING 1
), cl AS (
  INSERT INTO contact_log AS t (space_id, contact_id, subject_hmac, purpose, channel, direction, source_id,
    agent, gateway, decision_id, state, unchecked, uses, use_ids, reserved_at, used_at)
  SELECT {SPACE}, r.contact_id, decode(r.subject, 'hex'), r.purpose, r.channel, 'outbound', r.source_id,
    r.agent, r.gateway, r.decision_id, r.state, r.unchecked, cardinality(u.ids), u.ids, {_ts("reserved_at")},
    {_ts("used_at")}
  FROM jsonb_to_recordset(CAST(:contacts AS jsonb)) AS r(contact_id uuid, subject text, purpose text,
    channel text, source_id uuid, agent text, gateway text, decision_id uuid, state text, unchecked boolean,
    use_ids jsonb, reserved_at bigint, used_at bigint)
  CROSS JOIN LATERAL (
    SELECT ARRAY(SELECT CAST(x AS uuid) FROM jsonb_array_elements_text(r.use_ids) AS x) AS ids
  ) AS u
  ON CONFLICT (space_id, contact_id) DO UPDATE SET
    state = CASE WHEN EXCLUDED.state = 'made' THEN 'made' ELSE t.state END,
    subject_hmac = coalesce(t.subject_hmac, EXCLUDED.subject_hmac),
    agent = coalesce(t.agent, EXCLUDED.agent), gateway = coalesce(t.gateway, EXCLUDED.gateway),
    decision_id = coalesce(t.decision_id, EXCLUDED.decision_id),
    unchecked = t.unchecked AND EXCLUDED.unchecked,
    reserved_at = coalesce(t.reserved_at, EXCLUDED.reserved_at),
    used_at = greatest(t.used_at, EXCLUDED.used_at),
    use_ids = ARRAY(SELECT DISTINCT x FROM unnest(t.use_ids || EXCLUDED.use_ids) AS x),
    uses = (SELECT count(DISTINCT x) FROM unnest(t.use_ids || EXCLUDED.use_ids) AS x)
  RETURNING 1
), bs AS (
  INSERT INTO budget_spends AS t (space_id, spend_id, budget, slice, target_kind, target, units, settled,
    spent_at, until)
  SELECT {SPACE}, r.spend_id, r.budget, r.slice, r.target_kind, decode(r.target, 'hex'), r.units, r.settled,
    {_ts("spent_at")}, {_ts("until")}
  FROM jsonb_to_recordset(CAST(:spends AS jsonb)) AS r(spend_id uuid, budget text, slice text,
    target_kind text, target text, units int, settled boolean, spent_at bigint, until bigint)
  ON CONFLICT (space_id, spend_id) DO UPDATE SET units = EXCLUDED.units, settled = true
  WHERE EXCLUDED.settled
  RETURNING 1
), cm AS (
  INSERT INTO commitments AS t (space_id, commitment_id, external_id, subject_hmac, type, compatibility,
    terms_ct, dek_version, status, score, source_id, agent, made_at, valid_until)
  SELECT {SPACE}, r.commitment_id, r.external_id, decode(r.subject, 'hex'), r.type, r.compatibility,
    decode(r.terms, 'hex'), r.dek, r.status, r.score, r.source_id, r.agent, {_ts("made_at")},
    {_ts("valid_until")}
  FROM jsonb_to_recordset(CAST(:commitments AS jsonb)) AS r(commitment_id uuid, external_id text,
    subject text, type text, compatibility text, terms text, dek int, status text, score real, source_id uuid,
    agent text, made_at bigint, valid_until bigint)
  ON CONFLICT (space_id, commitment_id) DO NOTHING
  RETURNING 1
), cms AS (
  UPDATE commitments AS t SET status = r.status, decided_at = {_ts("at")}
  FROM jsonb_to_recordset(CAST(:statuses AS jsonb)) AS r(commitment_id uuid, status text, at bigint)
  WHERE t.space_id = {SPACE} AND t.commitment_id = r.commitment_id AND t.status IN ('active', 'superseded')
    AND (t.status = 'active' OR r.status <> 'superseded')
  RETURNING 1
), {NAMED_CTE}
SELECT (SELECT count(*) FROM e) + (SELECT count(*) FROM c) + (SELECT count(*) FROM cr)
  + (SELECT count(*) FROM s) + (SELECT count(*) FROM sl) + (SELECT count(*) FROM cl)
  + (SELECT count(*) FROM bs) + (SELECT count(*) FROM cm) + (SELECT count(*) FROM cms)
"""
)

EFFECT_COLUMNS = (
    "state, attempt, kind, source_id, agent, fact_type, fact_namespace, fact_external_id, cost_units,"
    " reserved_at, lease_until, settled_at, settled_by, done_at, key_valid_until"
)
EFFECT = text(f"SELECT {EFFECT_COLUMNS} FROM effects WHERE space_id = {SPACE} AND effect_key_hash = :key")
LIVE_EFFECTS = text(
    f"SELECT effect_key_hash, {EFFECT_COLUMNS} FROM effects WHERE space_id = {SPACE}"
    " AND (key_valid_until IS NULL OR key_valid_until > :now)"
    " AND (CAST(:after AS bytea) IS NULL OR effect_key_hash > CAST(:after AS bytea))"
    " ORDER BY effect_key_hash LIMIT :limit"
)

_LIVE_CLAIM = f"c.space_id = {SPACE} AND c.released_at IS NULL AND c.valid_until > :now"
_LIVE_SPEND = f"b.space_id = {SPACE} AND b.until > :now"
_HOLDS = (
    f"m.space_id = {SPACE} AND m.status = 'active' AND m.valid_until > :now AND m.subject_hmac IS NOT NULL"
)
# A target's name: the subject's keyed hash, the object as `<type>\t<namespace>\t<id>`, or nothing for the
# space's own budgets.
_LIVE = f"""
live AS (
  SELECT c.target AS kind, CASE WHEN c.target = 'subject' THEN c.subject_hmac
    ELSE convert_to(
      c.object_type || E'\\t' || coalesce(c.object_namespace, '') || E'\\t' || c.object_external_id, 'UTF8'
    ) END AS name, 'c' AS what, to_jsonb(c) AS row
  FROM ownership_claims c
  WHERE {_LIVE_CLAIM}
  UNION ALL
  SELECT 'subject', s.handle_hmac, 's', to_jsonb(s)
  FROM suppressions s
  WHERE s.space_id = {SPACE} AND s.lifted_at IS NULL AND (s.until IS NULL OR s.until > :now)
  UNION ALL
  SELECT b.target_kind, b.target, 'b', to_jsonb(b)
  FROM budget_spends b
  WHERE {_LIVE_SPEND}
  UNION ALL
  SELECT 'subject', m.subject_hmac, 'm', to_jsonb(m) - 'terms_ct'
  FROM commitments m
  WHERE {_HOLDS}
)"""
SUBJECT = text(
    f"SELECT 'c', to_jsonb(c) FROM ownership_claims c WHERE {_LIVE_CLAIM} AND c.subject_hmac = :name"
    " UNION ALL SELECT 's', to_jsonb(s) FROM suppressions s"
    f" WHERE s.space_id = {SPACE} AND s.handle_type IN ('phone', 'email') AND s.handle_hmac = :name"
    " AND s.lifted_at IS NULL AND (s.until IS NULL OR s.until > :now)"
    " UNION ALL SELECT 'b', to_jsonb(b) FROM budget_spends b"
    f" WHERE {_LIVE_SPEND} AND b.target_kind = 'subject' AND b.target = :name"
    " UNION ALL SELECT 'm', to_jsonb(m) - 'terms_ct' FROM commitments m"
    f" WHERE {_HOLDS} AND m.subject_hmac = :name"
)
OBJECT = text(
    f"SELECT 'c', to_jsonb(c) FROM ownership_claims c WHERE {_LIVE_CLAIM} AND c.object_type = :object_type"
    " AND c.object_namespace = :object_namespace AND c.object_external_id = :object_id"
    " UNION ALL SELECT 'b', to_jsonb(b) FROM budget_spends b"
    f" WHERE {_LIVE_SPEND} AND b.target_kind = 'object' AND b.target = :name"
)
SPACE_SPENDS = text(
    f"SELECT 'b', to_jsonb(b) FROM budget_spends b WHERE {_LIVE_SPEND} AND b.target_kind = 'space'"
)
LIVE_TARGETS = text(
    f"""WITH {_LIVE}, names AS (
  SELECT DISTINCT kind, name FROM live
  WHERE name IS NOT NULL
    AND (CAST(:after_kind AS text) IS NULL
      OR (kind, name) > (CAST(:after_kind AS text), CAST(:after AS bytea)))
  ORDER BY kind, name LIMIT :limit
)
SELECT live.kind, live.name, live.what, live.row FROM live JOIN names USING (kind, name)
ORDER BY live.kind, live.name"""
)
SUPPRESSION_COLUMNS = (
    "suppression_id, handle_hmac, handle_ct, dek_version, purpose, channel, source_id, since, until,"
    " lifted_at, change_seq, contact_window"
)
IN_FORCE = text(
    f"SELECT {SUPPRESSION_COLUMNS} FROM suppressions WHERE space_id = {SPACE} AND lifted_at IS NULL"
    " AND (until IS NULL OR until > :now) ORDER BY change_seq LIMIT :limit"
)
CHANGED = text(
    f"SELECT {SUPPRESSION_COLUMNS} FROM suppressions WHERE space_id = {SPACE} AND change_seq > :after"
    " ORDER BY change_seq LIMIT :limit"
)


SUBJECT_EFFECTS = text(
    f"SELECT effect_key_hash, {EFFECT_COLUMNS} FROM effects"
    f" WHERE space_id = {SPACE} AND subject_hmac = :subject"
    " AND (key_valid_until IS NULL OR key_valid_until > :now) ORDER BY reserved_at DESC LIMIT :limit"
)
HANDOFF_COLUMNS = (
    "handoff_id, subject_hmac, subject_ct, source_id, agent, target, package_ct, package_sha256, dek_version,"
    " status, outcome, created_at, expected_by, accepted_at, closed_at"
)
ADD_HANDOFF = text(
    f"WITH {NAMED_CTE}"
    " INSERT INTO handoffs (space_id, handoff_id, subject_hmac, subject_ct, source_id, agent, target,"
    " package_ct, package_sha256, dek_version, status, created_at, expected_by)"
    f" VALUES ({SPACE}, :handoff_id, :subject_hmac, :subject_ct, :source_id, :agent, :target, :package_ct,"
    " :package_sha256, :dek_version, :status, :created_at, :expected_by)"
)
HANDOFF = text(
    f"SELECT {HANDOFF_COLUMNS} FROM handoffs WHERE space_id = {SPACE} AND handoff_id = :handoff_id"
)
MOVE_HANDOFF = text(
    f"WITH {NAMED_CTE}"
    " UPDATE handoffs SET status = :status,"
    " accepted_at = CASE WHEN :status = 'accepted' THEN :at ELSE accepted_at END,"
    " closed_at = CASE WHEN :status IN ('closed', 'expired') THEN :at ELSE closed_at END,"
    " outcome = coalesce(:outcome, outcome), outcome_ct = coalesce(:outcome_ct, outcome_ct),"
    " outcome_dek_version = coalesce(:outcome_dek, outcome_dek_version)"
    f" WHERE space_id = {SPACE} AND handoff_id = :handoff_id AND status = ANY(:from_statuses)"
    f" RETURNING {HANDOFF_COLUMNS}"
)
TERMS = text(
    "SELECT commitment_id, subject_hmac, terms_ct, dek_version FROM commitments"
    f" WHERE space_id = {SPACE} AND commitment_id = ANY(:ids)"
)
TERMS_PAGE = text(
    "SELECT commitment_id, subject_hmac, terms_ct, dek_version FROM commitments"
    f" WHERE space_id = {SPACE} AND (CAST(:after AS uuid) IS NULL OR commitment_id > :after)"
    " ORDER BY commitment_id LIMIT :limit"
)
REWRITE_TERMS = text(
    "UPDATE commitments SET terms_ct = :terms_ct, dek_version = :dek"
    f" WHERE space_id = {SPACE} AND commitment_id = :commitment_id"
)
OUTCOME = text(
    "SELECT outcome_ct, outcome_dek_version FROM handoffs"
    f" WHERE space_id = {SPACE} AND handoff_id = :handoff_id"
    " AND outcome_ct IS NOT NULL AND outcome_dek_version IS NOT NULL"
)
HANDOFFS_PAGE = text(
    f"SELECT {HANDOFF_COLUMNS} FROM handoffs"
    f" WHERE space_id = {SPACE} AND (CAST(:after AS uuid) IS NULL OR handoff_id > :after)"
    " ORDER BY handoff_id LIMIT :limit"
)
REWRITE_HANDOFF = text(
    "UPDATE handoffs SET package_ct = :package_ct, package_sha256 = :package_sha256,"
    " subject_ct = :subject_ct, dek_version = :dek,"
    " outcome_ct = coalesce(:outcome_ct, outcome_ct),"
    " outcome_dek_version = coalesce(:outcome_dek, outcome_dek_version)"
    f" WHERE space_id = {SPACE} AND handoff_id = :handoff_id"
)
ADD_REPORT = text(
    f"INSERT INTO coord_reports (space_id, report_id, status, days, requested_at)"
    f" VALUES ({SPACE}, :report_id, 'pending', :days, :at)"
)
FINISH_REPORT = text(
    "UPDATE coord_reports SET status = :status, summary = CAST(:summary AS jsonb), sample_ct = :sample_ct,"
    f" dek_version = :dek_version, finished_at = :at WHERE space_id = {SPACE} AND report_id = :report_id"
)
REPORT = text(
    "SELECT report_id, status, days, requested_at, finished_at, summary FROM coord_reports"
    f" WHERE space_id = {SPACE} AND report_id = :report_id"
)
REPORTS = text(
    f"SELECT report_id, status, days, requested_at, finished_at, summary FROM coord_reports"
    f" WHERE space_id = {SPACE} AND (CAST(:before AS uuid) IS NULL OR report_id < CAST(:before AS uuid))"
    " ORDER BY report_id DESC LIMIT :limit"
)
# The Console's overview (admin): counts only, over the claims holding now and the window's decisions.
OVERVIEW_OWNERSHIP = text(
    f"SELECT kind, level, via, holder_kind, count(*), count(DISTINCT holder) FROM ownership_claims"
    f" WHERE space_id = {SPACE} AND released_at IS NULL AND valid_until > :now"
    " GROUP BY kind, level, via, holder_kind ORDER BY kind, level, via, holder_kind"
)
OVERVIEW_EFFECTS = text(
    f"SELECT kind, state, count(*) FROM effects WHERE space_id = {SPACE} AND reserved_at >= :since"
    " GROUP BY kind, state ORDER BY kind, state"
)
OVERVIEW_CONFLICTS = text(
    f"SELECT (SELECT count(*) FROM effects WHERE space_id = {SPACE} AND state = 'unknown_outcome'),"
    f" (SELECT count(*) FROM (SELECT DISTINCT a.target, a.subject_hmac, a.object_type, a.object_namespace,"
    " a.object_external_id FROM ownership_claims a JOIN ownership_claims b ON b.space_id = a.space_id"
    " AND b.target = a.target AND b.subject_hmac IS NOT DISTINCT FROM a.subject_hmac"
    " AND b.object_type IS NOT DISTINCT FROM a.object_type"
    " AND b.object_namespace IS NOT DISTINCT FROM a.object_namespace"
    " AND b.object_external_id IS NOT DISTINCT FROM a.object_external_id"
    " AND b.claim_id > a.claim_id AND b.holder <> a.holder"
    " AND a.claimed_at < coalesce(b.released_at, b.valid_until)"
    " AND b.claimed_at < coalesce(a.released_at, a.valid_until)"
    f" WHERE a.space_id = {SPACE} AND a.kind = 'owner' AND b.kind = 'owner'"
    " AND coalesce(a.released_at, a.valid_until) >= :since) overlapping),"
    f" (SELECT count(*) FROM commitments WHERE space_id = {SPACE} AND status = 'superseded'"
    " AND decided_at >= :since),"
    f" (SELECT count(*) FROM handoffs WHERE space_id = {SPACE} AND created_at >= :since"
    " AND (status = 'expired' OR (status IN ('created', 'accepted') AND expected_by < :now))),"
    f" (SELECT count(*) FROM contact_log WHERE space_id = {SPACE} AND contact_id >= :since_id AND uses > 1)"
)
OVERVIEW_BUDGETS = text(
    "SELECT s.budget, count(*), count(*) FILTER (WHERE s.total >= l.lim), sum(s.total)"
    " FROM (SELECT budget, target, sum(units) AS total FROM budget_spends"
    f" WHERE space_id = {SPACE} AND until > :now AND budget = ANY(:names) GROUP BY budget, target) s"
    " JOIN unnest(CAST(:names AS text[]), CAST(:limits AS int[])) AS l(name, lim) ON l.name = s.budget"
    " GROUP BY s.budget ORDER BY s.budget"
)
OVERVIEW_CONTACTS = text(
    f"SELECT purpose, direction, state, count(*) FROM contact_log WHERE space_id = {SPACE}"
    " AND contact_id >= :since_id GROUP BY purpose, direction, state ORDER BY purpose, direction, state"
)
TOKENS_USED_TWICE = text(
    f"SELECT count(*) FROM contact_log WHERE space_id = {SPACE} AND contact_id >= :since AND uses > 1"
)
# The erasure of a subject, one class at a time, by the keyed hashes coordination names its handles by.
ERASE = {
    table: text(
        f"DELETE FROM {table} WHERE space_id = {SPACE} AND ({', '.join(keys)}) IN ("
        f"SELECT {', '.join(keys)} FROM {table} WHERE space_id = {SPACE} AND {where} LIMIT :limit)"
        f" RETURNING {key}"
    )
    for table, keys, key, where in (
        ("ownership_claims", ("space_id", "claim_id"), "claim_id", "subject_hmac = ANY(:subjects)"),
        ("contact_log", ("space_id", "contact_id"), "contact_id", "subject_hmac = ANY(:subjects)"),
        (
            "budget_spends",
            ("space_id", "spend_id"),
            "spend_id",
            "target_kind = 'subject' AND target = ANY(:subjects)",
        ),
        (
            "effects",
            ("space_id", "effect_key_hash"),
            "encode(effect_key_hash, 'hex')",
            "subject_hmac = ANY(:subjects)",
        ),
        ("commitments", ("space_id", "commitment_id"), "commitment_id", "subject_hmac = ANY(:subjects)"),
        ("handoffs", ("space_id", "handoff_id"), "handoff_id", "subject_hmac = ANY(:subjects)"),
    )
}
HELD_SUBJECT = " AND (subject_hmac IS NULL OR subject_hmac <> ALL(:held_subjects))"
# The retention of each class (24.5.1), `:limit` rows at a time; a finished class answers fewer. What a legal
# hold keeps stays: the subjects and handles of held handles.
EXPIRE = {
    name: text(
        (
            f"DELETE FROM {table} WHERE space_id = {SPACE} AND ({keys}) IN ("
            f"SELECT {keys} FROM {table} WHERE space_id = {SPACE} AND {where} LIMIT :limit) RETURNING 1"
        )
        if verb == "delete"
        else (
            f"UPDATE {table} SET {verb} WHERE space_id = {SPACE} AND ({keys}) IN ("
            f"SELECT {keys} FROM {table} WHERE space_id = {SPACE} AND {where} LIMIT :limit) RETURNING 1"
        )
    )
    for name, table, keys, verb, where in (
        (
            "ownership_claims",
            "ownership_claims",
            "space_id, claim_id",
            "delete",
            "coalesce(released_at, valid_until) < :now - interval '30 days'"
            f"{HELD_SUBJECT} AND (origin_handle_id IS NULL OR origin_handle_id <> ALL(:held_handles))",
        ),
        (
            "contact_log",
            "contact_log",
            "space_id, contact_id",
            "delete",
            f"contact_id < :contacts_before{HELD_SUBJECT}",
        ),
        (
            "budget_spends",
            "budget_spends",
            "space_id, spend_id",
            "delete",
            "until < :now AND (target_kind <> 'subject' OR target <> ALL(:held_subjects))",
        ),
        (
            "effects",
            "effects",
            "space_id, effect_key_hash",
            "delete",
            f"key_valid_until < :now{HELD_SUBJECT}",
        ),
        (
            "commitments_expired",
            "commitments",
            "space_id, commitment_id",
            "status = 'expired', decided_at = valid_until",
            "status IN ('active', 'superseded') AND valid_until < :now",
        ),
        (
            "commitments",
            "commitments",
            "space_id, commitment_id",
            "delete",
            "status NOT IN ('active', 'superseded')"
            f" AND coalesce(decided_at, valid_until) < :now - interval '180 days'{HELD_SUBJECT}",
        ),
        (
            "handoffs_expired",
            "handoffs",
            "space_id, handoff_id",
            "status = 'expired', closed_at = expected_by",
            "status IN ('created', 'accepted') AND expected_by < :now",
        ),
        (
            "handoffs",
            "handoffs",
            "space_id, handoff_id",
            "delete",
            f"created_at < :now - interval '180 days'{HELD_SUBJECT}",
        ),
        (
            "suppressions",
            "suppressions",
            "space_id, suppression_id",
            "delete",
            "coalesce(lifted_at, until) < :now - interval '395 days'"
            " AND (origin_handle_id IS NULL OR origin_handle_id <> ALL(:held_handles))",
        ),
        (
            "coord_report_samples",
            "coord_reports",
            "space_id, report_id",
            "sample_ct = NULL, dek_version = NULL",
            "sample_ct IS NOT NULL AND requested_at < :now - interval '90 days'",
        ),
        (
            "coord_reports",
            "coord_reports",
            "space_id, report_id",
            "delete",
            "requested_at < :now - interval '395 days'",
        ),
    )
}


DROP_WEEKS = text("SELECT drop_expired_contact_weeks(:before, CAST(:held AS bytea[]))")


class PgCoordinateRepository:
    def __init__(self, session: AsyncSession, sealer: Sealer | None) -> None:  # noqa: ARG002 (nothing sealed here)
        self._db = session

    async def write(self, batch: SpaceBatch) -> None:
        effects = [
            {
                "key": e.key_hash.hex(),
                "kind": e.snapshot.kind,
                "state": e.snapshot.state.value,
                **_fact(e.snapshot.fact),
                "subject": e.subject_hmac.hex() if e.subject_hmac is not None else None,
                "source_id": str(e.source_id),
                "agent": e.snapshot.agent,
                "attempt": e.snapshot.attempt,
                "cost": e.snapshot.cost_units,
                "reserved_at": e.snapshot.reserved_at_ms,
                "lease_until": e.snapshot.lease_until_ms,
                "done_at": e.snapshot.done_at_ms,
                "settled_at": e.snapshot.settled_at_ms,
                "settled_by": e.snapshot.settled_by.value if e.snapshot.settled_by else None,
                "valid_until": (
                    e.snapshot.reserved_at_ms + e.snapshot.ttl_ms if e.snapshot.ttl_ms is not None else None
                ),
            }
            for e in batch.effects
        ]
        claims = [
            {
                "claim_id": str(c.claim_id),
                "subject": c.subject_hmac.hex() if c.subject_hmac is not None else None,
                **_object(c.object),
                "kind": c.kind,
                "task": c.task,
                "holder_kind": c.holder_kind,
                "holder": c.holder,
                "level": c.level,
                "via": c.via,
                "source_id": str(c.source_id),
                "observer": c.observer,
                "basis_event_id": str(c.basis_event_id) if c.basis_event_id is not None else None,
                "claimed_at": c.claimed_at_ms,
                "valid_until": c.valid_until_ms,
                "intents": list(c.intents),
                "epoch": c.epoch,
                "released_at": c.released_at_ms,
            }
            for c in batch.claims
        ]
        releases = [{"claim_id": str(r.claim_id), "released_at": r.released_at_ms} for r in batch.releases]
        suppressions = [
            {
                "id": str(s.suppression_id),
                "handle_type": s.handle_type,
                "hmac": s.handle_hmac.hex(),
                "ct": s.handle_ct.hex(),
                "dek": s.dek_version,
                "purpose": s.purpose,
                "channel": s.channel,
                "reason": s.reason,
                "source_id": str(s.source_id),
                "since": s.since_ms,
                "until": s.until_ms,
                "resume": s.resume_after_s,
                "lifted_at": s.lifted_at_ms,
                "cw": s.window,
            }
            for s in batch.suppressions
        ]
        lifts = [{"id": str(lift.suppression_id), "lifted_at": lift.lifted_at_ms} for lift in batch.lifts]
        contacts = [
            {
                "contact_id": str(c.contact_id),
                "subject": c.subject_hmac.hex() if c.subject_hmac is not None else None,
                "purpose": c.purpose,
                "channel": c.channel,
                "source_id": str(c.source_id),
                "agent": c.agent,
                "gateway": c.gateway,
                "decision_id": str(c.decision_id) if c.decision_id is not None else None,
                "state": c.state,
                "unchecked": c.unchecked,
                "use_ids": [str(u) for u in c.use_ids],
                "reserved_at": c.reserved_at_ms,
                "used_at": c.used_at_ms,
            }
            for c in batch.contacts
        ]
        spends = [
            {
                "spend_id": str(b.spend_id),
                "budget": b.budget,
                "slice": b.slice,
                "target_kind": b.target_kind,
                "target": b.target.hex(),
                "units": b.units,
                "settled": b.settled,
                "spent_at": b.spent_at_ms,
                "until": b.until_ms,
            }
            for b in batch.spends
        ]
        commitments = [
            {
                "commitment_id": str(m.commitment_id),
                "external_id": m.external_id,
                "subject": m.subject_hmac.hex() if m.subject_hmac is not None else None,
                "type": m.type,
                "compatibility": m.compatibility,
                "terms": m.terms_ct.hex(),
                "dek": m.dek_version,
                "status": m.status,
                "score": m.score,
                "source_id": str(m.source_id),
                "agent": m.agent,
                "made_at": m.made_at_ms,
                "valid_until": m.valid_until_ms,
            }
            for m in batch.commitments
        ]
        statuses = [
            {"commitment_id": str(st.commitment_id), "status": st.status, "at": st.at_ms}
            for st in batch.statuses
        ]
        await self._db.execute(
            WRITE,
            {
                "effects": json.dumps(effects),
                "claims": json.dumps(claims),
                "releases": json.dumps(releases),
                "suppressions": json.dumps(suppressions),
                "lifts": json.dumps(lifts),
                "contacts": json.dumps(contacts),
                "spends": json.dumps(spends),
                "commitments": json.dumps(commitments),
                "statuses": json.dumps(statuses),
                # What each commitment's terms name a person by (`domain.privacy.mentions`), indexed with it.
                "named": named_param(
                    [
                        (MentionKey(MentionStore.COMMITMENT, str(m.commitment_id)), frozenset(m.named))
                        for m in batch.commitments
                    ]
                ),
            },
        )

    async def effect(self, key_hash: bytes) -> EffectSnapshot | None:
        row = (await self._db.execute(EFFECT, {"key": key_hash})).mappings().first()
        return _snapshot(row) if row is not None else None

    async def target(self, target: bytes, *, kind: str, now: datetime) -> TargetState:
        if kind == "object":
            rows = await self._db.execute(OBJECT, {**_object(target.decode()), "name": target, "now": now})
        elif kind == "space":
            rows = await self._db.execute(SPACE_SPENDS, {"now": now})
        else:
            rows = await self._db.execute(SUBJECT, {"name": target, "now": now})
        return _target(target, kind, [(what, data) for what, data in rows.tuples()])

    async def live_effects(
        self, now: datetime, after: bytes | None, limit: int
    ) -> list[tuple[bytes, EffectSnapshot]]:
        rows = (await self._db.execute(LIVE_EFFECTS, {"now": now, "after": after, "limit": limit})).mappings()
        return [(bytes(row["effect_key_hash"]), _snapshot(row)) for row in rows]

    async def live_targets(
        self, now: datetime, after: tuple[str, bytes] | None, limit: int
    ) -> list[TargetState]:
        kind, name = after if after is not None else (None, None)
        params = {"now": now, "after_kind": kind, "after": name, "limit": limit}
        rows = (await self._db.execute(LIVE_TARGETS, params)).all()
        grouped: dict[tuple[str, bytes], list[tuple[str, Any]]] = {}
        for found_kind, found_name, what, data in rows:
            grouped.setdefault((found_kind, bytes(found_name)), []).append((what, data))
        return [_target(name, kind, found) for (kind, name), found in grouped.items()]

    async def suppression_changes(
        self, after_seq: int | None, limit: int, now: datetime
    ) -> Sequence[SuppressionRow]:
        if after_seq is None:
            rows = await self._db.execute(IN_FORCE, {"now": now, "limit": limit})
        else:
            rows = await self._db.execute(CHANGED, {"after": after_seq, "limit": limit})
        return [
            SuppressionRow(
                suppression_id=row["suppression_id"],
                handle_hmac=bytes(row["handle_hmac"]),
                handle_ct=bytes(row["handle_ct"]),
                dek_version=row["dek_version"],
                purpose=row["purpose"],
                channel=row["channel"],
                source_id=row["source_id"],
                since=row["since"],
                until=row["until"],
                lifted_at=row["lifted_at"],
                change_seq=row["change_seq"],
                window=row["contact_window"],
            )
            for row in rows.mappings()
        ]

    async def subject_effects(
        self, subject_hmac: bytes, now: datetime, limit: int
    ) -> list[tuple[bytes, EffectSnapshot]]:
        rows = await self._db.execute(SUBJECT_EFFECTS, {"subject": subject_hmac, "now": now, "limit": limit})
        return [(bytes(row["effect_key_hash"]), _snapshot(row)) for row in rows.mappings()]

    async def add_handoff(self, handoff: HandoffRow) -> None:
        await self._db.execute(
            ADD_HANDOFF,
            {
                "handoff_id": handoff.handoff_id,
                "subject_hmac": handoff.subject_hmac,
                "subject_ct": handoff.subject_ct,
                "source_id": handoff.source_id,
                "agent": handoff.agent,
                "target": handoff.target,
                "package_ct": handoff.package_ct,
                "package_sha256": handoff.package_sha256,
                "dek_version": handoff.dek_version,
                "status": handoff.status,
                "created_at": handoff.created_at,
                "expected_by": handoff.expected_by,
                "named": named_param(
                    [(MentionKey(MentionStore.HANDOFF, str(handoff.handoff_id)), handoff.named)]
                ),
            },
        )

    async def handoff(self, handoff_id: UUID) -> HandoffRow | None:
        row = (await self._db.execute(HANDOFF, {"handoff_id": handoff_id})).mappings().first()
        return _handoff(row) if row is not None else None

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
        params = {
            "handoff_id": handoff_id,
            "status": status,
            "from_statuses": list(from_statuses),
            "at": at,
            "outcome": outcome,
            "outcome_ct": outcome_ct.outcome_ct if outcome_ct else None,
            "outcome_dek": outcome_ct.dek_version if outcome_ct else None,
            "named": named_param([(MentionKey(MentionStore.HANDOFF, str(handoff_id)), named)]),
        }
        row = (await self._db.execute(MOVE_HANDOFF, params)).mappings().first()
        return _handoff(row) if row is not None else None

    async def terms_of(self, commitment_ids: Sequence[UUID]) -> list[SealedTerms]:
        if not commitment_ids:
            return []
        rows = await self._db.execute(TERMS, {"ids": list(commitment_ids)})
        return [_terms(r) for r in rows.mappings()]

    async def terms_page(self, after: UUID | None, limit: int) -> list[SealedTerms]:
        rows = await self._db.execute(TERMS_PAGE, {"after": after, "limit": limit})
        return [_terms(r) for r in rows.mappings()]

    async def rewrite_terms(self, terms: SealedTerms) -> None:
        await self._db.execute(
            REWRITE_TERMS,
            {"terms_ct": terms.terms_ct, "dek": terms.dek_version, "commitment_id": terms.commitment_id},
        )

    async def outcome_of(self, handoff_id: UUID) -> SealedOutcome | None:
        row = (await self._db.execute(OUTCOME, {"handoff_id": handoff_id})).first()
        return SealedOutcome(bytes(row.outcome_ct), row.outcome_dek_version) if row is not None else None

    async def handoffs_page(self, after: UUID | None, limit: int) -> list[HandoffRow]:
        rows = await self._db.execute(HANDOFFS_PAGE, {"after": after, "limit": limit})
        return [_handoff(r) for r in rows.mappings()]

    async def rewrite_handoff(self, handoff: HandoffRow, outcome: SealedOutcome | None) -> None:
        await self._db.execute(
            REWRITE_HANDOFF,
            {
                "handoff_id": handoff.handoff_id,
                "package_ct": handoff.package_ct,
                "package_sha256": handoff.package_sha256,
                "subject_ct": handoff.subject_ct,
                "dek": handoff.dek_version,
                "outcome_ct": outcome.outcome_ct if outcome else None,
                "outcome_dek": outcome.dek_version if outcome else None,
            },
        )

    async def add_report(self, report_id: UUID, days: int, requested_at: datetime) -> None:
        await self._db.execute(ADD_REPORT, {"report_id": report_id, "days": days, "at": requested_at})

    async def finish_report(
        self,
        report_id: UUID,
        *,
        status: str,
        summary: dict[str, Any],
        sample_ct: bytes | None,
        dek_version: int | None,
        at: datetime,
    ) -> None:
        await self._db.execute(
            FINISH_REPORT,
            {
                "report_id": report_id,
                "status": status,
                "summary": json.dumps(summary),
                "sample_ct": sample_ct,
                "dek_version": dek_version,
                "at": at,
            },
        )

    async def report(self, report_id: UUID) -> ReportRow | None:
        row = (await self._db.execute(REPORT, {"report_id": report_id})).mappings().first()
        if row is None:
            return None
        return ReportRow(
            row["report_id"],
            row["status"],
            row["days"],
            row["requested_at"],
            row["finished_at"],
            row["summary"],
        )

    async def reports(self, before: UUID | None, limit: int) -> list[ReportRow]:
        rows = await self._db.execute(REPORTS, {"before": before, "limit": limit})
        return [
            ReportRow(
                row["report_id"],
                row["status"],
                row["days"],
                row["requested_at"],
                row["finished_at"],
                row["summary"],
            )
            for row in rows.mappings()
        ]

    async def overview(self, since: datetime, now: datetime, limits: dict[str, int]) -> Overview:
        at = {"since": since, "now": now, "since_id": uuid7_floor(int(since.timestamp() * 1000))}
        ownership = (await self._db.execute(OVERVIEW_OWNERSHIP, at)).tuples().all()
        effects = (await self._db.execute(OVERVIEW_EFFECTS, at)).tuples().all()
        waiting, overlaps, superseded, expired, reused = (
            await self._db.execute(OVERVIEW_CONFLICTS, at)
        ).one()
        names = sorted(limits)
        budgets = (
            (
                await self._db.execute(
                    OVERVIEW_BUDGETS, at | {"names": names, "limits": [limits[n] for n in names]}
                )
            )
            .tuples()
            .all()
            if names
            else []
        )
        contacts = (await self._db.execute(OVERVIEW_CONTACTS, at)).tuples().all()
        return Overview(
            ownership=[(k, lv, via, hk, int(n), int(h)) for k, lv, via, hk, n, h in ownership],
            effects=[(k, st, int(n)) for k, st, n in effects],
            awaiting_decision=int(waiting),
            overlaps=int(overlaps),
            commitments_superseded=int(superseded),
            handoffs_expired=int(expired),
            tokens_reused=int(reused),
            budgets=[(b, int(n), int(full), int(units)) for b, n, full, units in budgets],
            contacts=[(p, d, st, int(n)) for p, d, st, n in contacts],
        )

    async def tokens_used_twice(self, since: datetime) -> int:
        since_id = uuid7_floor(int(since.timestamp() * 1000))
        return int((await self._db.execute(TOKENS_USED_TWICE, {"since": since_id})).scalar_one())

    async def erase_for(self, scope: ErasureScope, *, limit: int) -> list[ErasedRow]:
        if not scope.subject_hashes or scope.kind is TargetKind.CONVERSATION:
            return []
        erased: list[ErasedRow] = []
        for table, statement in ERASE.items():
            left = limit - len(erased)
            if left <= 0:
                break
            rows = await self._db.execute(statement, {"subjects": list(scope.subject_hashes), "limit": left})
            erased += [ErasedRow(table, {"id": str(row[0])}) for row in rows.tuples()]
        return erased

    async def drop_expired_contact_weeks(self, now: datetime, held_subjects: Collection[bytes] = ()) -> int:
        before = int((now - CONTACTS_KEPT).timestamp() * 1000)
        dropped = await self._db.scalar(DROP_WEEKS, {"before": before, "held": sorted(held_subjects)})
        return int(dropped or 0)

    async def expire(
        self,
        now: datetime,
        *,
        limit: int,
        held_subjects: Collection[bytes] = (),
        held_handles: Collection[UUID] = (),
    ) -> dict[str, int]:
        params = {
            "now": now,
            "limit": limit,
            "contacts_before": uuid7_floor(int((now - CONTACTS_KEPT).timestamp() * 1000)),
            "held_subjects": sorted(held_subjects),
            "held_handles": sorted(held_handles, key=str),
        }
        counts: dict[str, int] = {}
        for name, statement in EXPIRE.items():
            counts[name] = len((await self._db.execute(statement, params)).all())
        return counts


def _fact(fact: str | None) -> dict[str, str | None]:
    kind, namespace, external = fact.split("\t", 2) if fact else [None, None, None]
    return {"fact_type": kind, "fact_namespace": namespace, "fact_id": external}


def _object(name: str | None) -> dict[str, str | None]:
    kind, namespace, external = name.split("\t", 2) if name else [None, None, None]
    return {"object_type": kind, "object_namespace": namespace, "object_id": external}


def _snapshot(row: Any) -> EffectSnapshot:
    reserved = _ms(row["reserved_at"])
    valid_until = row["key_valid_until"]
    fact = [row["fact_type"], row["fact_namespace"], row["fact_external_id"]]
    return EffectSnapshot(
        state=EffectState(row["state"]),
        attempt=row["attempt"],
        kind=row["kind"],
        source=str(row["source_id"]),
        agent=row["agent"],
        reserved_at_ms=reserved,
        lease_until_ms=_ms(row["lease_until"]) if row["lease_until"] else None,
        settled_at_ms=_ms(row["settled_at"]) if row["settled_at"] else None,
        settled_by=Settler(row["settled_by"]) if row["settled_by"] else None,
        done_at_ms=_ms(row["done_at"]) if row["done_at"] else None,
        cost_units=row["cost_units"],
        fact="\t".join(part or "" for part in fact) if row["fact_type"] else None,
        ttl_ms=_ms(valid_until) - reserved if valid_until is not None else None,
    )


def _target(name: bytes, kind: str, rows: Sequence[tuple[str, Any]]) -> TargetState:
    claims: list[OwnershipRecord] = []
    suppressions: list[SuppressionRow] = []
    spends: list[SpendRow] = []
    commitments: list[CommitmentRow] = []
    for what, data in rows:
        if what == "c":
            claims.append(
                OwnershipRecord(
                    space_id=UUID(data["space_id"]),
                    claim_id=UUID(data["claim_id"]),
                    subject_hmac=_hex(data["subject_hmac"]),
                    object=name.decode() if kind == "object" else None,
                    kind=data["kind"],
                    holder=data["holder"],
                    level=data["level"],
                    via=data["via"],
                    source_id=UUID(data["source_id"]),
                    claimed_at_ms=_ms(_time(data["claimed_at"])),
                    valid_until_ms=_ms(_time(data["valid_until"])),
                    intents=tuple(data["intents"] or ()),
                    epoch=int(data["epoch"]),
                    holder_kind=data["holder_kind"],
                    task=data["task"],
                    observer=data["observer"],
                    basis_event_id=UUID(data["basis_event_id"]) if data["basis_event_id"] else None,
                )
            )
        elif what == "m":
            commitments.append(
                CommitmentRow(
                    external_id=data["external_id"],
                    commitment_id=UUID(data["commitment_id"]),
                    type=data["type"],
                    compatibility=data["compatibility"],
                    valid_until=_time(data["valid_until"]),
                    score=data["score"],
                    agent=data["agent"],
                    made_at=_time(data["made_at"]),
                )
            )
        elif what == "s":
            suppressions.append(
                SuppressionRow(
                    suppression_id=UUID(data["suppression_id"]),
                    handle_hmac=_hex(data["handle_hmac"]) or b"",
                    handle_ct=_hex(data["handle_ct"]) or b"",
                    dek_version=int(data["dek_version"]),
                    purpose=data["purpose"],
                    channel=data["channel"],
                    source_id=UUID(data["source_id"]) if data["source_id"] else None,
                    since=_time(data["since"]),
                    until=_time(data["until"]) if data["until"] else None,
                    lifted_at=None,
                    change_seq=int(data["change_seq"]),
                    window=data.get("contact_window"),
                )
            )
        else:
            spends.append(
                SpendRow(
                    spend_id=UUID(data["spend_id"]),
                    budget=data["budget"],
                    slice=data["slice"],
                    units=int(data["units"]),
                    spent_at=_time(data["spent_at"]),
                    until=_time(data["until"]),
                )
            )
    return TargetState(name, kind, tuple(claims), tuple(suppressions), tuple(spends), tuple(commitments))


def _hex(value: str | None) -> bytes | None:
    """`to_jsonb` writes a bytea as `\\x<hex>`."""
    return bytes.fromhex(value.removeprefix("\\x")) if value else None


def _time(value: str) -> datetime:
    return datetime.fromisoformat(value).astimezone(UTC)


def _ms(at: datetime) -> int:
    return int(at.timestamp() * 1000)


def _terms(row: Any) -> SealedTerms:
    subject = bytes(row["subject_hmac"]) if row["subject_hmac"] is not None else None
    return SealedTerms(row["commitment_id"], subject, bytes(row["terms_ct"]), row["dek_version"])


def _handoff(row: Any) -> HandoffRow:
    return HandoffRow(
        handoff_id=row["handoff_id"],
        subject_hmac=bytes(row["subject_hmac"]) if row["subject_hmac"] is not None else None,
        subject_ct=bytes(row["subject_ct"]) if row["subject_ct"] is not None else None,
        source_id=row["source_id"],
        agent=row["agent"],
        target=row["target"],
        package_ct=bytes(row["package_ct"]),
        package_sha256=bytes(row["package_sha256"]),
        dek_version=row["dek_version"],
        status=row["status"],
        outcome=row["outcome"],
        created_at=row["created_at"],
        expected_by=row["expected_by"],
        accepted_at=row["accepted_at"],
        closed_at=row["closed_at"],
    )
