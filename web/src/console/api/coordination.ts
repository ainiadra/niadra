/**
 * Coordination on the cell's admin API (the server's `adapters/inbound/http/coordinate.py`): what holds a
 * customer now, the effects, the conflicts settled, the budgets and the shadow runs, in counts. Nobody's
 * handle or message comes back.
 */
import { call, type Transport } from './http'

interface OwnershipCount {
  kind: 'owner' | 'case' | 'task_lock'
  level: string
  via: 'declaration' | 'mapping' | 'worker'
  holder_kind: 'agent' | 'human' | 'queue' | 'system'
  claims: number
  holders: number
}

interface EffectCount {
  kind: string
  state: 'reserved' | 'done' | 'failed' | 'unknown_outcome'
  effects: number
}

interface ConflictCounts {
  ownership_overlaps: number
  commitments_superseded: number
  handoffs_expired: number
  tokens_reused: number
}

interface BudgetUse {
  purpose: string
  limit: number
  per_hours: number
  subjects: number
  at_limit: number
  spent: number
}

interface ContactCount {
  purpose: string
  direction: 'inbound' | 'outbound'
  state: 'reserved' | 'made' | 'expired'
  contacts: number
}

export interface CoordinationOverview {
  since: string
  ownership: OwnershipCount[]
  effects: EffectCount[]
  awaiting_decision: number
  conflicts: ConflictCounts
  budgets: BudgetUse[]
  contacts: ContactCount[]
}

export interface CoordinationReport {
  report_id: string
  status: 'pending' | 'done' | 'failed'
  days: number
  requested_at: string
  finished_at?: string | null
  summary: Record<string, unknown>
}

type Opt = { signal?: AbortSignal }

export function coordinationClient(t: Transport) {
  return {
    overview: (days: number, o: Opt = {}) => call<CoordinationOverview>(t, 'GET', '/v1/coordination/overview', { ...o, query: { days } }),
    reports: (o: Opt = {}) => call<{ items: CoordinationReport[]; next_cursor?: string | null }>(t, 'GET', '/v1/coordination/reports', { ...o, query: { limit: 20 } }),
    /** The space's policy over the outbound messages already ingested, enforcing nothing; listed when done. */
    runShadow: (days: number) => call<{ report_id: string; status: 'pending' | 'done' }>(t, 'POST', '/v1/coordination/shadow', { body: { days } }),
  }
}
