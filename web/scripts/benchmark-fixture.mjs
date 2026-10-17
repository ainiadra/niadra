/**
 * Writes the /benchmark page's test fixture (src/pages/Benchmark.fixture.json) from a local dry run of
 * the harness, so the fixture has exactly the shape the harness writes today:
 *
 *   node scripts/benchmark-fixture.mjs ../niadra-sdk-python/benchmarks/deploy/local/results/<combined>/summary.json
 *
 * A dry run has the shape but not the values (fake models, niadra-mock, one quick repetition), so every
 * measured value is replaced by a synthetic one from the profiles below, and the lines a quick dry run
 * skips are added (Niadra's `vpc` path, the second rate). These numbers exist for the tests only: the
 * fixture never goes to src/content/benchmark/runs, and the importer refuses anything but a region run.
 */
import { readFileSync, writeFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { withCombineShape } from './benchmark-fixture-shape.mjs'

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..')
const REPS = 3

// A fixed pseudo-random sequence, so the fixture is the same on every run of this script.
let seed = 7
const rand = () => ((seed = (seed * 16807) % 2147483647) - 1) / 2147483646

const round = (v, digits) => Math.round(v * 10 ** digits) / 10 ** digits
/** Three repetitions around `v`, spread by `spread` (a share), as the harness summarizes them. */
function across(v, spread = 0.08, digits = 1) {
  if (v == null) return { median: null, min: null, max: null, runs: Array(REPS).fill(null) }
  const runs = Array.from({ length: REPS }, (_, i) => (i === 0 ? v : v * (1 + (rand() * 2 - 1) * spread))).map((x) => round(Math.max(0, x), digits))
  const sorted = [...runs].sort((a, b) => a - b)
  return { median: sorted[1], min: sorted[0], max: sorted[2], runs }
}
const share = (v, spread = 0.05) => across(Math.min(1, v), spread, 4)
const count = (v) => across(v, 0, 0)

// Profiles: the order of magnitude each system shows in the harness's own notes, never a measurement.
const LATENCY_P50 = { niadra: { vpc: 38, edge: 61 }, mem0_oss: 95, ai_memory: 24, ai_memory_llm: 26, graphiti: 310, hindsight: 180, hindsight_reflect: 2400, memobase: 70, memos: 140, supermemory: 55, embedder: 9 }
const ACCURACY = { niadra: 0.62, mem0_oss: 0.58, mem0_oss_rerank: 0.61, ai_memory: 0.31, ai_memory_llm: 0.36, graphiti: 0.49, hindsight: 0.57, hindsight_reflect: 0.66, memobase: 0.41, memos: 0.47, supermemory: 0.52, full_history: 0.97, no_memory: 0.03 }
const PER_CHANNEL = { mem0_oss: 0.22, mem0_oss_rerank: 0.24 }
const TOKENS = { niadra: { voice: 236, chat: 314 }, mem0_oss: 165, mem0_oss_rerank: 158, ai_memory: 420, ai_memory_llm: 280, graphiti: 510, hindsight: 690, hindsight_reflect: 140, memobase: 380, memos: 450, supermemory: 300, full_history: 5200, no_memory: 0 }
const LEAKS = { niadra: 0, mem0_oss: 27, mem0_oss_rerank: 27, ai_memory: 24, ai_memory_llm: 21, graphiti: 19, hindsight: 25, hindsight_reflect: 12, memobase: 16, memos: 22, supermemory: 23, full_history: 32, no_memory: 0 }
const COST = { mem0_oss: 2.7, mem0_oss_rerank: 3.9, ai_memory: 0, ai_memory_llm: 1.9, graphiti: 11.2, hindsight: 4.1, hindsight_reflect: 9.8, memobase: 3.0, memos: 2.2, supermemory: 1.4 }
const BACKING = { niadra: 21.4, mem0_oss: 38.2, mem0_oss_rerank: 35.9, ai_memory: 71.3, ai_memory_llm: 64.8, graphiti: 44.1, hindsight: 29.7, hindsight_reflect: 17.2, memobase: 52.6, memos: 47.5, supermemory: 40.3, full_history: 6.1, no_memory: 112.5 }
const FRESHNESS = { niadra: 4100, mem0_oss: 2600, ai_memory: 900, ai_memory_llm: 1400, graphiti: null, hindsight: 5200, hindsight_reflect: 5400, memobase: 9800, memos: 7300, supermemory: 3100 }
const SEARCH_P50 = { niadra: { vpc: 44, edge: 70 }, mem0_oss: 98, ai_memory: 30, ai_memory_llm: 31, graphiti: 290, hindsight: 170, hindsight_reflect: 2350, memobase: 66, memos: 150, supermemory: 52, embedder: 9 }
const OPEN_P50 = { niadra: { vpc: 31, edge: 55 }, mem0_oss: 12, ai_memory: 6, ai_memory_llm: 6 }
const WRITE_P50 = { niadra: { vpc: 22, edge: 41 }, ai_memory: 8, ai_memory_llm: 9, graphiti: 14, hindsight: 19, hindsight_reflect: 19, memobase: 17, memos: 36, supermemory: 25 }
const MEM0_ADD = { add_raw: 120, add_infer: 1900 }

const pick = (table, system, path) => {
  const v = table[system]
  return v != null && typeof v === 'object' ? v[path] : v
}
const timing = (p50, rateFactor = 1) => {
  const base = p50 * rateFactor
  return { p50: across(base), p95: across(base * 1.8, 0.12), p99: across(base * 2.6, 0.18), max: across(base * 4, 0.25) }
}

const source = process.argv[2]
if (!source) {
  console.error('usage: node scripts/benchmark-fixture.mjs <dry run summary.json>')
  process.exit(2)
}
const dry = JSON.parse(readFileSync(source, 'utf8'))
const m = dry.metrics
const run = {
  ...dry,
  run_id: '20260926T120000Z-f1x7re',
  date: '2026-09-26',
  started_at: '2026-09-26T08:00:00+00:00',
  finished_at: '2026-09-26T19:40:00+00:00',
  environment: {
    kind: 'region',
    region: 'us-east-2',
    machine_class: 'm7i-flex.large',
    database_class: 'db.t4g.micro',
    harness_host: "temporary EC2 instance in the cell's VPC, one system at a time (deploy/temp-host)",
    harness_machine_class: 'm7i-flex.large',
    verified_by_instance_metadata: true,
    host: null,
    python: dry.environment.python,
  },
  versions: { ...dry.versions, niadra_server: '2026.09.26-0900-3c1a2b7' },
  config: { ...dry.config, repetitions: REPS, judge_model: 'shared', agent: 'llm' },
  dataset: { ...dry.dataset, cases: 356, valid: across(301, 0.02, 0), excluded: Array(REPS).fill(dry.dataset.excluded[0] ?? []) },
  site_notes: { pt: { freshness: 'Nota de teste sobre frescor.' }, en: { freshness: 'Test note about freshness.' } },
}

const niadraPaths = ['vpc', 'edge']
const rates = [10, 25]
const others = [...new Set(m.latency.results.map((r) => r.system))].filter((s) => s !== 'niadra')

const latency = []
for (const rate of rates) {
  for (const path of niadraPaths) latency.push({ system: 'niadra', path, rate, ...timing(pick(LATENCY_P50, 'niadra', path), rate === 25 ? 1.1 : 1), errors: 0, sent: 900 * REPS })
  for (const system of others) latency.push({ system, path: 'host', rate, ...timing(pick(LATENCY_P50, system), rate === 25 ? 1.2 : 1), errors: system === 'hindsight_reflect' && rate === 25 ? 41 : 0, sent: (rate === 25 ? 750 : 300) * REPS })
}

const accuracy = m.accuracy.results.map((r) => {
  const base = r.scenario === 'per_channel_id' ? PER_CHANNEL[r.system] : ACCURACY[r.system]
  const categories = Object.fromEntries(
    Object.keys(r.by_category).map((c) => {
      const v = Math.max(0, Math.min(1, base + (rand() - 0.5) * 0.5))
      return [c, { judge: share(v, 0.1), deterministic: share(Math.max(0, v - 0.06), 0.1) }]
    }),
  )
  return {
    ...r,
    cases: count(301),
    judge: share(base),
    deterministic: share(Math.max(0, base - 0.05)),
    context_has_answer: share(Math.min(1, base + 0.06)),
    context_has_answer_loose: share(Math.min(1, base + 0.09)),
    by_category: categories,
  }
})

const tokens = m.tokens.results.map((r) => {
  const base = r.system === 'niadra' ? TOKENS.niadra[r.view] : r.scenario === 'per_channel_id' ? pick(TOKENS, r.system) * 0.3 : pick(TOKENS, r.system)
  return { ...r, median: across(base, 0.04), p95: across(base * 1.6, 0.06) }
})

const privacy = m.privacy.results.map((r) => {
  const leaks = r.scenario === 'per_channel_id' ? 3 : LEAKS[r.system]
  return { ...r, cases: count(32), leaks: across(leaks, 0.1, 0), leak_rate: share(leaks / 32, 0.1) }
})

const promptUsd = (system) => {
  const t = tokens.find((x) => x.system === system && (x.view === 'chat' || x.view === 'default') && x.scenario !== 'per_channel_id')
  return t ? across(round((t.median.median * 10000 * 0.4) / 1e6, 4), 0.04, 4) : across(null)
}
const cost = m.cost.results.map((r) => {
  if (r.system === 'niadra' || r.system === 'mem0_platform') return { ...r, agent_prompt_usd_per_1000: r.system === 'niadra' ? promptUsd('niadra') : r.agent_prompt_usd_per_1000 }
  return { ...r, memory_usd_per_1000: across(COST[r.system], COST[r.system] ? 0.1 : 0, 4), agent_prompt_usd_per_1000: promptUsd(r.system) }
})

const freshness = m.freshness.results.map((r) => {
  const p50 = FRESHNESS[r.system]
  const timeouts = p50 == null ? 30 : r.system === 'memobase' ? 11 : 0
  return { ...r, p50: across(p50), p95: across(p50 == null ? null : p50 * 1.7), max: across(p50 == null ? null : p50 * 2.4), trials: 30, timeouts }
})

const resilience = m.resilience.results.map((r) => {
  const niadra = r.system === 'niadra'
  const delay = r.fault.startsWith('delay')
  const p50 = niadra ? (delay ? 302 : 14) : delay ? 2040 + rand() * 40 : 1 + rand() * 3
  return {
    ...r,
    memory_stage_p50_ms: across(p50, 0.03),
    memory_stage_max_ms: across(p50 * 1.15, 0.05),
    within_budget_rate: share(niadra || !delay ? 1 : 0, 0),
    raised_rate: share(niadra || delay ? 0 : 1, 0),
    empty_rate: share(niadra ? 1 : r.empty_rate.median ?? 0, 0),
  }
})

/** Metric 8 and 9 lines: Niadra at 10 per second on both paths (the production cap), the others at 10 and 25. */
function operations(results, profile) {
  const out = []
  for (const r of results) {
    const paths = r.system === 'niadra' ? niadraPaths : [r.path]
    const lineRates = r.system === 'niadra' ? [10] : rates
    for (const path of paths) {
      for (const rate of lineRates) {
        if (r.skipped) {
          out.push({ ...r, path, rate })
          continue
        }
        const p50 = profile(r, path)
        out.push({ ...r, path, rate, ...timing(p50, rate === 25 ? 1.2 : 1), errors: 0, error_kinds: {}, sent: (rate === 25 ? 750 : 300) * REPS, empty: r.operation === 'search' ? 4 : 0, degraded: 0 })
      }
    }
  }
  return out
}
const history = operations(m.history.results, (r, path) => pick(r.operation === 'open' ? OPEN_P50 : SEARCH_P50, r.system, path))
const ingest = operations(m.ingest.results, (r, path) => (r.system === 'mem0_oss' ? MEM0_ADD[r.operation] : pick(WRITE_P50, r.system, path)))

const backing = m.backing.results.map((r) => {
  const base = r.scenario === 'per_channel_id' ? BACKING[r.system] * 1.4 : BACKING[r.system]
  return {
    ...r,
    per_1000_answers: across(base, 0.1),
    answers: 356 * REPS,
    unbacked: Math.round((base * 356 * REPS) / 1000),
    by_kind: { amount: Math.round(base / 3), code: Math.round(base / 4), date: Math.round(base / 5), number: Math.round(base / 6) },
    by_category: Object.fromEntries(Object.keys(r.by_category).map((c) => [c, across(base * (0.5 + rand()), 0.15)])),
  }
})

run.metrics = {
  latency: { unit: 'ms', results: latency },
  accuracy: { unit: m.accuracy.unit, results: accuracy },
  tokens: { unit: m.tokens.unit, results: tokens },
  privacy: { unit: m.privacy.unit, results: privacy },
  cost: { unit: m.cost.unit, results: cost },
  freshness: { unit: 'ms', results: freshness },
  resilience: { results: resilience },
  history: { unit: 'ms', results: history },
  ingest: { unit: 'ms', results: ingest },
  backing: { unit: m.backing.unit, results: backing },
}

withCombineShape(run)

const target = join(ROOT, 'src', 'pages', 'Benchmark.fixture.json')
writeFileSync(target, JSON.stringify(run, null, 1) + '\n')
console.log(`wrote ${target}`)
