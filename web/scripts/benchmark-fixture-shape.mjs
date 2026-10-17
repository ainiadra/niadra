/**
 * The shape `bench combine` writes since 28/09/2026, added to the test fixture with synthetic values:
 * per_system (repetitions and cases of each system), dataset.references and dataset.shared,
 * metrics.accuracy_shared (every system on the same valid cases), Niadra's measured model spend
 * (models_only and cell_ledger, from the cell) and one caveat per system (site_caveats). Mem0 and Niadra
 * keep the run's repetitions and cases; every other system ran one repetition on half of them.
 *
 *   node scripts/benchmark-fixture-shape.mjs src/pages/Benchmark.fixture.json
 *
 * benchmark-fixture.mjs calls it too, so a fixture written from a dry run has the same shape.
 */
import { readFileSync, writeFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'

const REFERENCES = new Set(['no_memory', 'full_history', 'embedder'])

export function withCombineShape(run) {
  const reps = run.config.repetitions
  const full = run.dataset.cases
  const half = Math.round(full / 2)
  const sharedValid = Math.round(half * 0.93)
  const repsOf = (system) => (system === 'niadra' || system.startsWith('mem0') ? { repetitions: system === 'niadra' ? 1 : reps, cases: full } : { repetitions: 1, cases: half })
  run.per_system = {}
  for (const source of run.combined_from ?? []) for (const system of source.systems) run.per_system[system] = { run_id: source.run_id, ...repsOf(system) }
  run.combined_from = (run.combined_from ?? []).map((source) => ({ ...source, ...repsOf(source.systems[0]) }))
  run.dataset = { ...run.dataset, references: { run_id: run.combined_from[0]?.run_id ?? run.run_id, repetitions: [1] }, shared: { cases: half, valid: { median: sharedValid, min: sharedValid, max: sharedValid, runs: Array(reps).fill(sharedValid) } } }
  // The same lines on the shared cases: a slightly different grade, the shared valid count.
  const shift = (a, by) => (a.median == null ? a : { ...a, median: Math.min(1, +(a.median + by).toFixed(4)), min: Math.min(1, +(a.min + by).toFixed(4)), max: Math.min(1, +(a.max + by).toFixed(4)), runs: a.runs.map((v) => (v == null ? v : Math.min(1, +(v + by).toFixed(4)))) })
  const count = (n) => ({ median: n, min: n, max: n, runs: Array(reps).fill(n) })
  run.metrics.accuracy_shared = {
    unit: 'share of the valid cases every system answered',
    results: run.metrics.accuracy.results.map((r) => ({
      ...r,
      cases: count(sharedValid),
      judge: shift(r.judge, 0.01),
      deterministic: shift(r.deterministic, 0.01),
      by_category: Object.fromEntries(Object.entries(r.by_category).map(([c, v]) => [c, { judge: shift(v.judge, 0.01), deterministic: shift(v.deterministic, 0.01) }])),
    })),
  }
  const prompt = run.metrics.cost.results.find((r) => r.system === 'niadra')?.agent_prompt_usd_per_1000
  const one = (v) => ({ median: v, min: v, max: v, runs: [v, ...Array(reps - 1).fill(null)] })
  run.metrics.cost.results = [
    ...run.metrics.cost.results.filter((r) => !(r.system === 'niadra' && (r.variant === 'models_only' || r.variant === 'cell_ledger'))),
    { system: 'niadra', variant: 'models_only', basis: 'extraction tokens read from the cell, at the harness prices', memory_usd_per_1000: one(2.4131), agent_prompt_usd_per_1000: prompt },
    { system: 'niadra', variant: 'cell_ledger', basis: 'the cell ledger, every purpose', memory_usd_per_1000: one(2.9016), agent_prompt_usd_per_1000: prompt },
  ]
  const systems = Object.keys(run.per_system).filter((s) => !REFERENCES.has(s))
  run.site_caveats = {
    pt: Object.fromEntries([...systems.map((s) => [s, `Ressalva de teste de ${s}.`]), ['redis_agent_memory', 'Não medido: ressalva de teste.']]),
    en: Object.fromEntries([...systems.map((s) => [s, `Test caveat of ${s}.`]), ['redis_agent_memory', 'Not measured: test caveat.']]),
  }
  return run
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  const file = process.argv[2]
  if (!file) {
    console.error('usage: node scripts/benchmark-fixture-shape.mjs <fixture.json>')
    process.exit(2)
  }
  const run = withCombineShape(JSON.parse(readFileSync(file, 'utf8')))
  writeFileSync(file, JSON.stringify(run, null, 1) + '\n')
  console.log(`reshaped ${file}`)
}
