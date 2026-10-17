/**
 * Turn records on the cell's admin API (the server's `adapters/inbound/http/turns.py`): one turn by id, in
 * either tier, and the search over the kept tier. The shapes mirror `contracts/turns.py`, with the
 * interactions of `contracts/signals.py` and the claims of `contracts/claims.py`.
 *
 * A turn id is a system id and may go in a path. A conversation id never does: it may be a phone number or
 * an e-mail, so the search carries it in the body.
 */
import { call, type Transport } from './http'
import type { Page } from './types'

export type TurnKind = 'message' | 'action' | 'event' | 'timer'
export type Fidelity = 'bronze' | 'silver' | 'gold'
export type Completeness = 'complete' | 'partial' | 'incomplete' | 'unknown'
export type ContentMode = 'stored' | 'pointer' | 'hash_only'
type Tier = 'short' | 'kept'
export type TurnFlag =
  | 'error'
  | 'guard_acted'
  | 'guard_budget_exceeded'
  | 'handoff'
  | 'assertion_failed'
  | 'synthetic'
  | 'incomplete'
  | 'negative_feedback'
  | 'truncated'
export type ReadSurface = 'pack' | 'state' | 'constraints' | 'coordination' | 'budget' | 'history' | 'agent_memory' | 'agent_state'

interface TurnAgent {
  name: string
  role?: string | null
  parent_turn_id?: string | null
}

interface TurnPins {
  prompts?: Record<string, string>
  corpus_digest?: string | null
  model?: string | null
  assembler?: string | null
  tool_schemas?: Record<string, string>
  niadra?: { compiler?: string | null; pack_hash?: string | null } | null
}

interface TurnBuild {
  pins?: TurnPins
  sdk?: string | null
  adapter?: string | null
}

interface TurnRead {
  surface: ReadSurface
  etag?: string | null
  version?: string | null
  blob?: string | null
  receipt_id?: string | null
}

interface TurnObservation {
  ref: string
  fields: Record<string, unknown>
  provenance?: { source: 'live' | 'snapshot' | 'cache'; source_observed_at: string; scope?: 'global' | 'customer' | 'context' } | null
}

interface TurnConstraints {
  constraints: string
  hard_sent?: string[]
  results_checked: number
  violations: number
  unverifiable: number
  relaxed?: 'declared' | 'suspected' | null
}

export interface TurnCall {
  call_id: string
  parent_call_id?: string | null
  kind: 'tool' | 'model'
  name?: string | null
  attempt?: number
  synthetic?: boolean
  cache_hit?: boolean
  status?: 'ok' | 'error' | 'timeout' | 'cancelled'
  latency_ms?: number | null
  args_hash?: string | null
  args?: string | null
  result_model?: string | null
  result_ui?: string | null
  observations?: TurnObservation[]
  applied?: TurnConstraints | null
  effect_key?: string | null
  cost_units?: Record<string, number>
  tokens?: { in: number; cached?: number; out: number } | null
  truncated?: boolean
}

/* The interaction spec (contracts/signals.py): a union on `kind`. `for` says whom it was for. */
type Scalar = boolean | number | string
interface InteractionBase {
  for?: string
}
interface PresentedItem {
  pos: number
  ref: string
  shown?: Record<string, Scalar | null>
  team?: 'a' | 'b' | null
}
interface Presented extends InteractionBase {
  kind: 'presented'
  exposure_id: string
  list_id: string
  list_kind?: string
  page?: number
  offset?: number
  parent_list_id?: string | null
  items: PresentedItem[]
  visible_k?: number | null
  delivered_at: string
  method?: 'bridge' | 'otel' | 'tool_result'
}
interface Seen extends InteractionBase {
  kind: 'seen'
  exposure_id: string
  max_index_seen: number
}
interface Engaged extends InteractionBase {
  kind: 'engaged'
  exposure_id?: string | null
  ref: string
  how: 'click' | 'detail' | 'mention' | 'add_to_cart' | 'compare' | 'share' | 'other'
}
interface Feedback extends InteractionBase {
  kind: 'feedback'
  ref: string
  polarity: 'positive' | 'negative'
  reason_attrs?: string[]
}
interface Preference extends InteractionBase {
  kind: 'preference'
  attr: string
  op: 'in' | 'not_in' | 'eq' | 'ne' | 'lt' | 'lte' | 'gt' | 'gte' | 'between'
  values: Scalar[]
  strength: 'must' | 'must_not' | 'prefer' | 'avoid'
  scope?: 'turn' | 'session' | 'persistent'
  category?: string | null
  expires_at?: string | null
  source?: 'stated' | 'tool_args' | 'correction'
}
interface Attribute extends InteractionBase {
  kind: 'attribute'
  name: string
  system?: string | null
  value: Scalar
  source?: 'stated' | 'correction'
  category?: string | null
}
interface Watch extends InteractionBase {
  kind: 'watch'
  ref: string
  condition: string
  consent_event_key: string
  expires_at?: string | null
}
interface Unwatch extends InteractionBase {
  kind: 'unwatch'
  ref: string
}
export type Interaction = Presented | Seen | Engaged | Feedback | Preference | Attribute | Watch | Unwatch

/* The claim contract's record (contracts/claims.py `ClaimRecord`): what the SDK found, and its verdict. */
type ClaimClass = 'money' | 'percent' | 'date' | 'duration' | 'quantity' | 'count' | 'dosage'
export interface ClaimValue {
  amount?: string | null
  min?: string | null
  max?: string | null
  unit?: string | null
  date?: string | null
  date_from?: string | null
  date_to?: string | null
}
interface ClaimEvidence {
  call_id?: string | null
  ref?: string | null
  field?: string | null
  document?: string | null
  anchor?: string | null
  score?: number | null
}
export interface ClaimRecord {
  category: string
  class?: ClaimClass | null
  nature?: 'computed' | 'quoted' | 'model' | null
  role?: string | null
  span: [number, number]
  value?: ClaimValue | null
  evidence?: ClaimEvidence | null
  verdict: string
  action: string
}

interface TurnOutput {
  event_keys?: string[]
  ui?: string | null
  document?: string | null
  handoff_id?: string | null
}

/** A large value of the record. In `stored` mode it carries `content`; in `pointer` mode only where it lives. */
export interface TurnBlob {
  sha256: string
  size?: number | null
  pointer?: string | null
  content?: unknown
}

export interface TurnRecord {
  spec?: 'turn-record.v0'
  turn_id: string
  conversation_id?: string | null
  task_id?: string | null
  agent: TurnAgent
  kind?: TurnKind
  started_at: string
  ended_at?: string | null
  fidelity?: Fidelity
  completeness?: Completeness
  content_mode: ContentMode
  build?: TurnBuild
  reads?: TurnRead[]
  calls?: TurnCall[]
  interactions?: Interaction[]
  claims?: ClaimRecord[]
  coordination?: { decision_id: string; decision: 'allow' | 'defer' | 'deny' | 'handoff_to' }[]
  effects?: { key: string; state: 'reserved' | 'done' | 'failed' | 'unknown_outcome' }[]
  output?: TurnOutput
  cost?: { usd: number } | null
  latency_ms?: number | null
  flags?: TurnFlag[]
  blobs?: Record<string, TurnBlob>
}

/** Why a turn cannot be run again, as the cell decides it from the record and the pins it requires. */
interface ReplayBlocker {
  reason: 'content_mode' | 'fidelity' | 'completeness' | 'missing_pin'
  /** E.g. `hash_only`, `bronze`, `partial` or the pin's name, `model`. */
  value: string
}

export interface TurnView {
  record: TurnRecord
  tier: Tier
  kept_until: string | null
  replayable: boolean
  replay_blockers?: ReplayBlocker[]
}

/** The search over the kept tier: these fields only, no free text. */
export interface TurnSearchRequest {
  conversation_id?: string
  agent?: string
  since?: string
  until?: string
  flags?: TurnFlag[]
  cursor?: string
  limit?: number
}

export interface TurnSummary {
  turn_id: string
  agent: string
  kind: TurnKind
  started_at: string
  tier: Tier
  fidelity: Fidelity
  completeness: Completeness
  replayable: boolean
  flags: TurnFlag[]
}

export type TurnSearchResponse = Page<TurnSummary>

/** Turn ids as the record allows them: safe in a URL path and in a storage key. */
export const TURN_ID = /^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$/

const enc = encodeURIComponent
type Opt = { signal?: AbortSignal }

export function turnsClient(t: Transport) {
  return {
    /** Up to 200 kept turns a page, newest first; the conversation id goes in the body. */
    search: (q: TurnSearchRequest, o: Opt = {}) =>
      call<TurnSearchResponse>(t, 'POST', '/v1/turns/search', { ...o, body: { limit: 50, ...q } }),
    /** One turn, in the short tier (every turn, 7 days) or the kept one. */
    get: (turnId: string, o: Opt = {}) => call<TurnView>(t, 'GET', `/v1/turns/${enc(turnId)}`, o),
  }
}
