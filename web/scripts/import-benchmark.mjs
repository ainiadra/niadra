/**
 * Copies a benchmark run's summary.json into the site, after checking it may be published.
 *
 *   node scripts/import-benchmark.mjs ../niadra-sdk-python/benchmarks/results/<date>-<id>/summary.json
 *
 * Only a run measured inside the cloud region passes (environment.kind "region" with a region): dry
 * runs, local and smoke runs are refused, so a number from a laptop never reaches the page. A campaign
 * of the temporary host (one run per system) is imported as the one folder `bench combine` wrote, with
 * its sources in `combined_from`. The file lands in src/content/benchmark/runs/<date>-<id>.json, which
 * the /benchmark page, the prerender, the sitemap and llms.txt read.
 *
 *   node scripts/import-benchmark.mjs <combined summary.json> --host-lines <host run summary.json> [--host-lines ...]
 *
 * adds Niadra's `host` path (deploy/cell/host-lines.sh: a pod on the cell's own machine, plain HTTP to each
 * service), which runs on its own after the repetitions and is not combined. Only Niadra's `host` lines of
 * latency, history and ingest are taken, from a run with the same configuration and dataset that started
 * after one of the combined run's Niadra sources and within a day of it. Each host run adds only the lines
 * the run does not have yet (the first one given wins), and must add at least one; `host_lines_from` lists
 * each, with the rates of the lines it added per metric.
 */
import { mkdirSync, readFileSync, writeFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..')
const REQUIRED_METRICS = ['latency', 'accuracy', 'tokens', 'privacy', 'cost']

/** Every system key a run has lines for, in any metric. */
const systemsOf = (run) =>
  [...new Set(Object.values(run?.metrics ?? {}).flatMap((metric) => (metric?.results ?? []).map((r) => r.system)))].sort()

/** The reasons a run cannot be published; empty when it can. */
export function problems(run) {
  const out = []
  if (run?.schema !== 'niadra-bench.results.v1') out.push('not a niadra-bench.results.v1 file')
  if (run?.environment?.kind !== 'region') out.push(`environment is "${run?.environment?.kind}", not "region"`)
  if (!run?.environment?.region) out.push('no region recorded')
  if (run?.combined_from && !run.combined_from.length) out.push('combined_from is empty')
  if (!run?.config?.judge_model) out.push('no judge: a dry run')
  if (!/^\d{4}-\d{2}-\d{2}$/.test(run?.date ?? '')) out.push('no date')
  for (const m of REQUIRED_METRICS) if (!run?.metrics?.[m]?.results?.length) out.push(`metric ${m} is missing`)
  return out
}

const fileName = (run) => `${run.date}-${run.run_id.slice(-6)}.json`

const HOST_METRICS = ['latency', 'history', 'ingest']
const DAY_MS = 24 * 3600 * 1000

/** The key of a timed line: two lines with the same key are the same measurement. */
const lineKey = (metric, r) => [metric, r.system, r.path, r.operation ?? '', r.rate].join('|')

/** Niadra's host lines of a host run that the run does not have yet, by metric. */
const missingLines = (run, host) => {
  const have = new Set(HOST_METRICS.flatMap((m) => (run?.metrics?.[m]?.results ?? []).map((r) => lineKey(m, r))))
  return Object.fromEntries(HOST_METRICS.map((m) => [m, (host?.metrics?.[m]?.results ?? []).filter((r) => !have.has(lineKey(m, r)))]))
}

/** The reasons a host-path run cannot be merged into a combined run; empty when it can. */
export function hostLineProblems(run, host) {
  const out = []
  if (host?.schema !== 'niadra-bench.results.v1') out.push('host lines: not a niadra-bench.results.v1 file')
  if (host?.config?.hash !== run?.config?.hash) out.push('host lines: another configuration hash')
  if (host?.dataset?.hash !== run?.dataset?.hash) out.push('host lines: another dataset hash')
  const rows = HOST_METRICS.flatMap((m) => host?.metrics?.[m]?.results ?? [])
  if (!rows.length) out.push('host lines: no latency, history or ingest line')
  if (rows.some((r) => r.system !== 'niadra' || r.path !== 'host')) out.push('host lines: a line that is not Niadra on the host path')
  if (rows.length && !Object.values(missingLines(run, host)).some((added) => added.length)) out.push('host lines: the combined run already has them')
  const niadra = (run?.combined_from ?? []).filter((s) => s.systems.includes('niadra'))
  const start = Date.parse(host?.started_at ?? '')
  if (!niadra.length) out.push('host lines: the combined run has no Niadra source')
  else if (!niadra.some((s) => start >= Date.parse(s.started_at) && start - Date.parse(s.finished_at) < DAY_MS)) out.push("host lines: not measured right after a Niadra run of the combined run")
  return out
}

export function withHostLines(run, host) {
  const merged = structuredClone(run)
  const added = missingLines(run, host)
  for (const m of HOST_METRICS) if (added[m].length) merged.metrics[m].results.push(...added[m])
  merged.host_lines_from = [
    ...(merged.host_lines_from ?? []),
    {
      run_id: host.run_id,
      started_at: host.started_at,
      finished_at: host.finished_at,
      repetitions: host.config.repetitions,
      cases: host.dataset.cases,
      config_hash: host.config.hash,
      environment_kind: host.environment.kind,
      rates: Object.fromEntries(HOST_METRICS.filter((m) => added[m].length).map((m) => [m, [...new Set(added[m].map((r) => r.rate))].sort((x, y) => x - y)])),
    },
  ]
  return merged
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  const source = process.argv[2]
  if (!source) {
    console.error('usage: node scripts/import-benchmark.mjs <summary.json>')
    process.exit(2)
  }
  let run = JSON.parse(readFileSync(source, 'utf8'))
  const found = problems(run)
  process.argv.forEach((arg, at) => {
    if (arg !== '--host-lines') return
    const hostFile = process.argv[at + 1]
    const host = hostFile ? JSON.parse(readFileSync(hostFile, 'utf8')) : null
    const hostFound = hostLineProblems(run, host)
    if (hostFound.length) found.push(...hostFound)
    else run = withHostLines(run, host)
  })
  if (found.length) {
    console.error(`refused ${source}:\n  ${found.join('\n  ')}`)
    process.exit(1)
  }
  const target = join(ROOT, 'src', 'content', 'benchmark', 'runs', fileName(run))
  mkdirSync(dirname(target), { recursive: true })
  writeFileSync(target, JSON.stringify(run, null, 1) + '\n')
  console.log(`imported ${target}`)
  console.log(`  systems: ${systemsOf(run).join(', ')}`)
  for (const host of run.host_lines_from ?? []) console.log(`  host lines from: ${host.run_id} ${JSON.stringify(host.rates)}`)
  if (run.combined_from) console.log(`  combined from: ${run.combined_from.map((s) => `${s.run_id} (${s.systems.join(', ')})`).join('; ')}`)
  console.log('  a system key the page does not name yet shows as its key: add it to benchmark.systems in src/i18n/pt.ts and en.ts')
}
