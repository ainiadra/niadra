/**
 * Measurement and the reviewable profile on the cell's admin API (the server's `adapters/inbound/http/signals.py`
 * and `measure.py`): outcomes and their attribution by method, the reconciliation with the company's BI file,
 * interleaving, experiments, counterfactual runs, unmet demand, the cost and the rates of the agents; and a
 * person's inferences, their correction and deletion, and requests to review an automated decision. The shapes
 * mirror `contracts/signals.py` and `contracts/measure.py`.
 */
import { call, newIdempotencyKey, type Transport } from './http'
import type { Page } from './types'

export type Method = 'line' | 'order' | 'identity' | 'assisted_handoff'
export type Band = 'deterministic' | 'probable'
export type Finality = 'provisional' | 'final' | 'expired_without_outcome'

export interface OutcomeLink {
  link_id: string
  outcome: string
  object: string
  line?: string | null
  state: string
  method: Method
  band: Band
  finality: Finality
  value_minor?: number | null
  currency?: string | null
  exposure_id?: string | null
  position?: number | null
  turn_id?: string | null
  action_id?: string | null
  handoff_id?: string | null
  agent?: string | null
  confidence?: number | null
  valid_at: string
  known_at: string
  final_at?: string | null
}

interface AttributionTotal {
  outcome: string
  method: Method
  band: Band
  finality: Finality
  currency: string
  outcomes: number
  lines: number
  value_minor: number
}

export interface AttributionReport {
  since: string
  until: string
  rows: (AttributionTotal & { day: string; agent: string })[]
  totals: AttributionTotal[]
  identity: { agreeing: number; compared: number; rate?: number | null }
}

export interface ReconcileResult {
  report_id: string
  deviation: number | null
  tolerance: number
  within_tolerance: boolean
  matched: number
  only_ours: number
  only_bi: number
  ours_minor: number
  bi_minor: number
  currency?: string | null
  worst: { order: string; line: string; ours_minor: number; bi_minor: number }[]
}

export interface BiUpload {
  upload_id: string
  upload_url: string
  upload_headers: Record<string, string>
  expires_at: string
}

interface InterleavingTotal {
  experiment: string
  tool: string
  impressions: number
  wins_a: number
  wins_b: number
  ties: number
  p_value: number | null
}

export interface InterleavingReport {
  since: string
  until: string
  rows: (Omit<InterleavingTotal, 'p_value'> & { day: string })[]
  totals: InterleavingTotal[]
}

type Metric = 'conversion' | 'value'

interface ArmOutcomes {
  arm: string
  people: number
  converted: number
  outcomes: number
  value_minor: number
}

export interface ArmComparison {
  arm: string
  metric: Metric
  control_mean: number
  arm_mean: number
  difference: number
  relative: number | null
  ci_low: number
  ci_high: number
  variance_reduction: number | null
  p_value: number
  decided: boolean
  looks: number
}

export interface ExperimentResult {
  experiment: string
  kind: 'element' | 'agent'
  element?: string | null
  agent?: string | null
  starts_at: string
  ends_at?: string | null
  alpha: number
  cuped_days: number
  arms: ArmOutcomes[]
  comparisons: ArmComparison[]
}

export interface ExperimentReport {
  as_of: string
  experiments: ExperimentResult[]
}

export type LimitCode =
  | 'not_quality'
  | 'model_reaction_not_measured'
  | 'trivial_for_hard'
  | 'few_cases'
  | 'noisy_tool'
  | 'cases_skipped'
  | 'dry_run'

export interface CounterfactualRun {
  run_id: string
  tool: string
  element: 'constraints' | 'hard' | 'size' | 'exclude'
  label?: string | null
  created_at: string
  cases: number
  completed: number
  skipped: Record<string, number>
  overlap: number | null
  noise_floor: number | null
  effect: number | null
  below_noise: number
  above_noise: number
  ties: number
  p_value: number | null
  engaged: { items: number; shown: number; kept: number; lost: number; gained: number; mean_shift?: number | null }
  limits: LimitCode[]
}

interface UnmetDemand {
  week: string
  tool: string
  asked: { attr: string; op: string; values: (string | number | boolean)[] }[]
  calls: number
  empty: number
  people: number
}

export interface UnmetDemandPage {
  since: string
  until: string
  min_people: number
  items: UnmetDemand[]
  next_cursor?: string | null
}

interface ClaimRates {
  source_id: string
  agent: string
  turns: number
  claims: number
  evidenced: number
  categories: Record<string, Record<string, number>>
}

interface ConstraintRates {
  source_id: string
  agent: string
  turns: number
  sent: number
  checked: number
  verifiable: number
  violated: number
  unverifiable: number
  relaxed_declared: number
  relaxed_suspected: number
  items_presented: number
  violations_per_1000_items?: number | null
}

interface CostRates {
  source_id: string
  agent: string
  turns: number
  cost_per_turn_usd: number | null
  model_calls_per_turn: number | null
  tool_calls_per_turn: number | null
  tokens_in_per_turn: number | null
  tokens_cached_per_turn: number | null
  tokens_out_per_turn: number | null
  memory_off_cost_per_turn_usd?: number | null
  difference_usd?: number | null
}

/** The part of `GET /v1/context-use` the measurement screen reads: per agent, never per person. */
export interface AgentRates {
  claims?: ClaimRates[]
  constraints?: ConstraintRates[]
  cost?: CostRates[]
}

/* The reviewable profile (LGPD, art. 20). */

export type InferenceKind = 'affinity' | 'soft_constraint' | 'attribute' | 'interest'

export interface Inference {
  key: string
  kind: InferenceKind
  for?: string
  attr?: string | null
  value?: string | number | boolean | null
  polarity?: 'prefer' | 'avoid' | null
  ref?: string | null
  origin: string
  evidence: Record<string, unknown>
  confidence?: number | null
  used_for: string[]
  at: string
}

export interface ReviewRequest {
  request_id: string
  profile_id: string
  status: 'open' | 'resolved'
  created_at: string
  decision_receipt_id?: string | null
  reason?: string | null
  explanation: Record<string, unknown>
  outcome?: 'upheld' | 'reversed' | 'corrected' | null
  resolution?: string | null
  resolved_at?: string | null
}

const enc = encodeURIComponent
type Opt = { signal?: AbortSignal }
type Period = { since: string; until: string }

export function measureClient(t: Transport) {
  return {
    outcomes: (cursor: string | null, o: Opt = {}) =>
      call<Page<OutcomeLink>>(t, 'GET', '/v1/measure/outcomes', { ...o, query: { cursor, limit: 50 } }),
    attribution: (q: Period, o: Opt = {}) => call<AttributionReport>(t, 'GET', '/v1/measure/attribution', { ...o, query: q }),
    interleaving: (q: Period, o: Opt = {}) => call<InterleavingReport>(t, 'GET', '/v1/measure/interleaving', { ...o, query: q }),
    experiments: (o: Opt = {}) => call<ExperimentReport>(t, 'GET', '/v1/measure/experiments', o),
    counterfactualRuns: (cursor: string | null, o: Opt = {}) =>
      call<Page<CounterfactualRun>>(t, 'GET', '/v1/measure/counterfactual-runs', { ...o, query: { cursor, limit: 20 } }),
    unmetDemand: (q: Period & { cursor?: string | null }, o: Opt = {}) =>
      call<UnmetDemandPage>(t, 'GET', '/v1/measure/unmet-demand', { ...o, query: { ...q, limit: 50 } }),
    agentRates: (q: Period, o: Opt = {}) =>
      call<AgentRates>(t, 'GET', '/v1/context-use', { ...o, query: { since: q.since, until: q.until, group_by: ['source_id'] } }),
    /** The BI file goes to storage first, for exactly its bytes; the reconciliation reads it by its id. */
    biUpload: (body: { size_bytes: number; sha256: string }) => call<BiUpload>(t, 'POST', '/v1/measure/reconcile/uploads', { body }),
    reconcile: (body: Period & { upload_id: string }) =>
      call<ReconcileResult>(t, 'POST', '/v1/measure/reconcile', {
        body: { since: body.since, until: body.until, file: { format: 'csv', upload_id: body.upload_id } },
      }),

    inferences: (profileId: string, cursor: string | null, o: Opt = {}) =>
      call<Page<Inference>>(t, 'GET', `/v1/profiles/${enc(profileId)}/inferences`, { ...o, query: { cursor, limit: 50 } }),
    correctInference: (profileId: string, key: string, body: { value: string | number | boolean; reason?: string }) =>
      call<Inference>(t, 'POST', `/v1/profiles/${enc(profileId)}/inferences/${enc(key)}/correct`, { body }),
    deleteInference: (profileId: string, key: string) =>
      call<void>(t, 'DELETE', `/v1/profiles/${enc(profileId)}/inferences/${enc(key)}`),
    reviewRequest: (profileId: string, body: { reason: string; decision_receipt_id?: string }) =>
      call<ReviewRequest>(t, 'POST', `/v1/profiles/${enc(profileId)}/review-requests`, {
        body,
        idempotencyKey: newIdempotencyKey(),
      }),
    reviewRequests: (cursor: string | null, o: Opt = {}) =>
      call<Page<ReviewRequest>>(t, 'GET', '/v1/review-requests', { ...o, query: { cursor, limit: 50 } }),
  }
}

/** The hex SHA-256 of a file, which the storage checks the upload against. */
export async function sha256Hex(data: ArrayBuffer): Promise<string> {
  const digest = await crypto.subtle.digest('SHA-256', data)
  return Array.from(new Uint8Array(digest), (b) => b.toString(16).padStart(2, '0')).join('')
}

/** The upload itself goes straight to storage, with the headers the reservation gave: no token travels. */
export async function putBiFile(upload: BiUpload, data: ArrayBuffer): Promise<void> {
  const res = await fetch(upload.upload_url, { method: 'PUT', headers: upload.upload_headers, body: data })
  if (!res.ok) throw new Error(`upload ${res.status}`)
}
