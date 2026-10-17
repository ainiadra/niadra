/**
 * Copies a typed-object run's typed.json into the site, keeping the figures and dropping the per-case lists.
 *
 *   node scripts/import-typed-benchmark.mjs ../niadra-sdk-python/benchmarks/results/typed/<date>-<id>/typed.json
 *
 * The file must be a `niadra-bench.typed.v1` result with a judge and more than one repetition. What the page
 * does not read goes: the per-case flips, the ids of the invalid cases, the per-read token counts, the paired
 * reads, the setup and the settle log. The file lands in src/content/benchmark/typed/<run id>.json, which the
 * /benchmark page, the prerender and llms.txt read.
 */
import { mkdirSync, readFileSync, writeFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..')

/** The reasons a run cannot be published; empty when it can. */
export function problems(run) {
  const out = []
  if (run?.schema !== 'niadra-bench.typed.v1') out.push('not a niadra-bench.typed.v1 file')
  if (!run?.config?.judge?.model) out.push('no judge')
  if (!(run?.config?.repetitions > 1)) out.push('a single repetition')
  if (!run?.metrics?.accuracy?.all?.all?.with?.judge) out.push('no accuracy figure for every case')
  if (!run?.metrics?.v0?.with) out.push('no privacy figure')
  if (!/^\d{4}-\d{2}-\d{2}/.test(run?.started_at ?? '')) out.push('no date')
  return out
}

/** The figures only: the lists a reader never sees stay in the repository's own results folder. */
export function trimmed(run) {
  const { setup: _setup, settle: _settle, tokens_without_blocks_v2: _reads, ...kept } = run
  const metrics = { ...kept.metrics }
  delete metrics.flips
  delete metrics.guard
  metrics.validity = { cases: run.metrics.validity.cases, valid: run.metrics.validity.valid }
  metrics.tokens = { ...run.metrics.tokens }
  delete metrics.tokens.without_reads
  const baseline = run.baseline ? { run_id: run.baseline.run_id, repetitions: run.baseline.repetitions, tokens: run.baseline.tokens } : undefined
  return { ...kept, metrics, ...(baseline ? { baseline } : {}) }
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  const source = process.argv[2]
  if (!source) {
    console.error('usage: node scripts/import-typed-benchmark.mjs <typed.json>')
    process.exit(2)
  }
  const run = JSON.parse(readFileSync(source, 'utf8'))
  const found = problems(run)
  if (found.length) {
    console.error(`refused ${source}:\n  ${found.join('\n  ')}`)
    process.exit(1)
  }
  const target = join(ROOT, 'src', 'content', 'benchmark', 'typed', `${run.run_id}.json`)
  mkdirSync(dirname(target), { recursive: true })
  writeFileSync(target, JSON.stringify(trimmed(run), null, 1) + '\n')
  console.log(`imported ${target}`)
}
