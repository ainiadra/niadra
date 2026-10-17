import type { TurnRecord, TurnSummary, TurnView } from '../api/turns'

/**
 * Turn records shaped like the server's `contracts/turns.py`, with synthetic data from the three sectors by
 * role: a health plan sale (stored), a litigation draft whose values stay in the firm's bucket (pointer), and
 * a "show more" in a fashion store kept as hashes only.
 */

const SHA = (c: string) => `sha256:${c.repeat(64).slice(0, 64)}`

export const QUOTE_TURN: TurnRecord = {
  spec: 'turn-record.v0',
  turn_id: '0192b000-0000-7000-8000-00000000t001',
  conversation_id: 'conv-plano-0042',
  agent: { name: 'closing', role: 'specialist' },
  kind: 'message',
  started_at: '2026-09-29T13:02:11Z',
  ended_at: '2026-09-29T13:02:19Z',
  fidelity: 'gold',
  completeness: 'complete',
  content_mode: 'stored',
  build: {
    pins: {
      prompts: { core: 'v16', rules: 'v8' },
      corpus_digest: SHA('a1'),
      model: 'modelo-exemplo-2026-09',
      assembler: '3.2.0',
      tool_schemas: { quote: SHA('b2') },
      niadra: { compiler: 'compiler-1.15', pack_hash: 'ph_7c1e2b9d' },
    },
    sdk: 'niadra-python/0.7.0',
    adapter: 'openai_agents/1',
  },
  reads: [
    { surface: 'pack', etag: 'W/"pk-81"', blob: 'p:1', receipt_id: 'rct_0192' },
    { surface: 'constraints', version: 'cv_1f3a' },
  ],
  calls: [
    {
      call_id: 'k1',
      kind: 'tool',
      name: 'quote',
      attempt: 1,
      status: 'ok',
      latency_ms: 740,
      args_hash: SHA('c3'),
      args: 'b:1',
      result_model: 'b:2',
      result_ui: 'b:3',
      observations: [
        {
          ref: 'cotacao_plano_saude:op:q-77',
          fields: { price_full: 511.06, network: 'rede-b' },
          provenance: { source: 'live', source_observed_at: '2026-09-29T13:02:12Z', scope: 'context' },
        },
      ],
      applied: { constraints: 'cv_1f3a', hard_sent: ['plan.network not_in rede-x'], results_checked: 3, violations: 0, unverifiable: 1 },
      cost_units: { paid_call: 1 },
    },
    { call_id: 'k2', parent_call_id: 'k1', kind: 'tool', name: 'network_lookup', attempt: 2, status: 'ok', latency_ms: 88, cache_hit: true },
    { call_id: 'm1', kind: 'model', name: 'modelo-exemplo-2026-09', latency_ms: 1900, tokens: { in: 2953, cached: 118000, out: 138 } },
  ],
  interactions: [
    {
      kind: 'presented',
      exposure_id: '0192b000-0000-7000-8000-0000000e0001',
      list_id: 'plans',
      page: 1,
      offset: 0,
      items: [
        { pos: 1, ref: 'cotacao_plano_saude:op:q-77', shown: { price_full: 511.06 } },
        { pos: 2, ref: 'cotacao_plano_saude:op:q-78', shown: { price_full: 489.9 } },
      ],
      visible_k: 2,
      delivered_at: '2026-09-29T13:02:18Z',
      method: 'bridge',
    },
    { kind: 'preference', attr: 'plan.network', op: 'not_in', values: ['rede-x'], strength: 'must_not', scope: 'persistent' },
  ],
  claims: [
    {
      category: 'price',
      class: 'money',
      nature: 'computed',
      role: 'price_full',
      span: [41, 50],
      value: { amount: '511.06', unit: 'BRL' },
      evidence: { call_id: 'k1', field: 'price_full' },
      verdict: 'matched',
      action: 'none',
    },
    { category: 'coverage_date', class: 'date', nature: 'model', span: [80, 90], value: { date: '2026-11-01' }, verdict: 'no_evidence', action: 'count' },
  ],
  coordination: [{ decision_id: 'dec_0192', decision: 'allow' }],
  effects: [{ key: 'proposal:q-77', state: 'done' }],
  output: { event_keys: ['evt-out-881'], ui: 'b:4' },
  cost: { usd: 0.036 },
  latency_ms: 8200,
  flags: ['handoff'],
  blobs: {
    'p:1': { sha256: SHA('d4'), size: 1830, content: { lines: 6 } },
    'b:1': { sha256: SHA('c3'), size: 58, content: { plan: 'enfermaria', lives: 2, city: 'cidade-exemplo' } },
    'b:2': { sha256: SHA('e5'), size: 96, content: { quotes: [{ id: 'q-77', price_full: 511.06 }] } },
    'b:3': { sha256: SHA('f6'), size: 120, content: { cards: 2 } },
    'b:4': { sha256: SHA('a7'), size: 44, content: { shown: 'plans' } },
  },
}

export const DRAFT_TURN: TurnRecord = {
  turn_id: '0192b000-0000-7000-8000-00000000t002',
  task_id: 'task-contestacao-17',
  agent: { name: 'drafting' },
  kind: 'event',
  started_at: '2026-09-29T14:10:00Z',
  ended_at: '2026-09-29T14:11:05Z',
  fidelity: 'gold',
  completeness: 'complete',
  content_mode: 'pointer',
  build: { pins: { model: 'modelo-exemplo-2026-09' }, sdk: 'niadra-python/0.7.0' },
  calls: [
    {
      call_id: 'k1',
      kind: 'tool',
      name: 'fetch_notice',
      status: 'ok',
      latency_ms: 310,
      args: 'b:1',
      result_model: 'b:2',
      observations: [{ ref: 'intimacao_com_prazo:tribunal:n-551', fields: { due_date: '2026-10-14' } }],
    },
  ],
  claims: [
    {
      category: 'deadline',
      class: 'date',
      nature: 'quoted',
      span: [120, 130],
      value: { date: '2026-10-14' },
      evidence: { document: 'doc-notice-551', anchor: 'b:3', score: 0.97 },
      verdict: 'anchored',
      action: 'none',
    },
  ],
  output: { document: 'b:4' },
  flags: [],
  blobs: {
    'b:1': { sha256: SHA('11'), size: 64, pointer: 's3://escritorio-registros/turns/2026/09/29/t002/b1.json' },
    'b:2': { sha256: SHA('22'), size: 25400, pointer: 's3://escritorio-registros/turns/2026/09/29/t002/b2.json' },
    'b:3': { sha256: SHA('33'), size: 210, pointer: 's3://escritorio-registros/turns/2026/09/29/t002/b3.json' },
    'b:4': { sha256: SHA('44'), size: 88000, pointer: 's3://escritorio-registros/turns/2026/09/29/t002/b4.json' },
  },
}

export const MORE_TURN: TurnRecord = {
  turn_id: '0192b000-0000-7000-8000-00000000t003',
  conversation_id: 'conv-loja-0913',
  agent: { name: 'shopping' },
  kind: 'action',
  started_at: '2026-09-29T15:20:00Z',
  fidelity: 'gold',
  completeness: 'complete',
  content_mode: 'hash_only',
  calls: [{ call_id: 'k1', kind: 'tool', name: 'search_items', status: 'ok', args: 'b:1', result_model: 'b:2' }],
  interactions: [
    {
      kind: 'presented',
      exposure_id: '0192b000-0000-7000-8000-0000000e0003',
      list_id: 'dresses',
      page: 2,
      offset: 12,
      items: [{ pos: 13, ref: 'item_variant:loja:991', shown: { price: 189.9, color: 'azul' } }],
      delivered_at: '2026-09-29T15:20:01Z',
    },
  ],
  flags: ['synthetic'],
  blobs: { 'b:1': { sha256: SHA('55') }, 'b:2': { sha256: SHA('66') } },
}

export const view = (record: TurnRecord, over: Partial<TurnView> = {}): TurnView => ({
  record,
  tier: 'kept',
  kept_until: '2026-10-29T13:02:11Z',
  replayable: true,
  ...over,
})

export const summary = (record: TurnRecord, over: Partial<TurnSummary> = {}): TurnSummary => ({
  turn_id: record.turn_id,
  agent: record.agent.name,
  kind: record.kind ?? 'message',
  started_at: record.started_at,
  tier: 'kept',
  fidelity: record.fidelity ?? 'gold',
  completeness: record.completeness ?? 'complete',
  replayable: true,
  flags: record.flags ?? [],
  ...over,
})
