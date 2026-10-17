/**
 * The type registry as the Console shows it (the server's `config/object_types.py`, `contracts/state.py` and
 * `contracts/turns.py`): the declared and derived types from the `object-types` document of the control plane,
 * the coverage of their fields on the cell's admin API, and the data issues that say a type drifted from the
 * company's system or that the agents saw a type nobody declared. Names and counts only: no value of a
 * customer's object ever reaches this screen.
 */
import { call, type Transport } from './http'
import type { Page } from './types'

export type Ownership = 'subject' | 'shared' | 'agent'
type DerivedBy = 'declaration' | 'introspection' | 'observation'

interface MirrorOf {
  system: string
  derived_by?: DerivedBy
  fingerprint?: string | null
  drift?: 'alert' | 'ignore'
}

export interface FieldDecl {
  type: string
  sensitivity?: string | null
  pii?: boolean
  access?: { readers: string[]; purposes?: string[]; effect: 'allow' | 'mask' | 'deny' }[]
}

/** One type of the registry, the members this screen reads. */
export interface ObjectTypeDecl {
  type: string
  version?: string
  ownership: Ownership
  nature?: 'observed' | 'derived'
  mirror_of?: MirrorOf | null
  fields?: Record<string, FieldDecl>
  states?: string[]
  sources?: Record<string, { kind: string; licence?: unknown }>
  readings?: Record<string, unknown>
}

export interface ObjectTypeRegistry {
  version?: string
  types?: ObjectTypeDecl[]
  commercial_purposes?: string[]
}

interface FieldCoverage {
  field: string
  /** The share of the type's objects some source observed the field of, 0 to 1. */
  coverage: number
  age_p50_s?: number | null
}

export interface TypeCoverage {
  type: string
  /** The objects measured: the ones of the type that changed last. */
  objects: number
  fields: FieldCoverage[]
}

export interface ObjectCoverage {
  types: TypeCoverage[]
}

export type DataIssueKind =
  | 'null_field'
  | 'out_of_vocabulary'
  | 'stale_source'
  | 'coverage_drop'
  | 'invalid_value'
  | 'divergence'
  | 'drift'
  | 'rule_conflict'
  | 'type_undeclared'
  | 'field_held'
  | 'budget_reached'
  | 'row_failed'
  | 'inference_limit'

/** A batch of the company's data that failed every attempt to be taken in: kept aside, sealed, never
 * deleted, until someone retries it (`GET /v1/intake/failed`). Kind, source and times; never its content. */
export interface FailedIntake {
  intake_id: string
  kind: 'events' | 'turns'
  source_id: string
  received_at: string
  failed_at: string
  attempts: number
}

export interface FailedIntakePage {
  items: FailedIntake[]
  /** Every batch kept aside, listed or not: a retry with no ids puts all of them back. */
  kept: number
}

export interface DataIssue {
  issue_id: string
  kind: DataIssueKind
  object_type?: string | null
  field?: string | null
  source?: string | null
  /** Objects as `type:namespace:id`: system ids, never a value. */
  refs: string[]
  occurrences: number
  opened_at: string
  last_seen_at: string
  acknowledged_at?: string | null
}

type Opt = { signal?: AbortSignal }

export function objectTypesClient(t: Transport) {
  return {
    /** Each declared field's share and median age over the objects that changed last (integration role). */
    coverage: (o: Opt = {}) => call<ObjectCoverage>(t, 'GET', '/v1/objects/coverage', o),
    /** The open data issues of one kind, newest first. */
    issues: (kind: DataIssueKind, o: Opt = {}) =>
      call<Page<DataIssue>>(t, 'GET', '/v1/data-issues', { ...o, query: { kind, status: 'open', limit: 100 } }),
    /** Every open data issue, newest first, a page at a time. */
    openIssues: (cursor: string | null, o: Opt = {}) =>
      call<Page<DataIssue>>(t, 'GET', '/v1/data-issues', { ...o, query: { status: 'open', limit: 100, ...(cursor ? { cursor } : {}) } }),
    /** The batches kept aside after failing every attempt, oldest first. */
    failedIntake: (o: Opt = {}) => call<FailedIntakePage>(t, 'GET', '/v1/intake/failed', o),
    /** Puts kept batches back for a worker to store: the ones named, or every one with no ids. */
    retryIntake: (intakeIds: string[] | null) =>
      call<{ retried: number }>(t, 'POST', '/v1/intake/failed/retry', { body: intakeIds ? { intake_ids: intakeIds } : {} }),
    /** Closes an issue; the next occurrence opens a new one. The id is a system id. */
    acknowledge: (issueId: string) =>
      call<DataIssue>(t, 'POST', `/v1/data-issues/${encodeURIComponent(issueId)}/ack`, { body: {} }),
  }
}
