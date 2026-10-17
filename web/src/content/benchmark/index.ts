/**
 * The benchmark's published results. Every file in ./runs is a `summary.json` written by the harness
 * (niadra-sdk-python, benchmarks/) during a run inside the region, copied here by
 * `node scripts/import-benchmark.mjs <summary.json>`, which refuses anything else. No number on the
 * /benchmark page is typed by hand: the page reads these files, and Benchmark.test.tsx fails when a
 * number on the page is not in them.
 */

export type Across = { median: number | null; min: number | null; max: number | null; runs: (number | null)[] }

type LatencyRow = { system: string; path: string; rate: number; p50: Across; p95: Across; p99: Across; max: Across; errors: number; sent: number }
type AccuracyRow = {
  system: string
  scenario: string | null
  cases: Across
  judge: Across
  deterministic: Across
  context_has_answer: Across
  by_category: Record<string, { judge: Across; deterministic: Across }>
}
type TokensRow = { system: string; scenario: string | null; view: string; median: Across; p95: Across }
type PrivacyRow = {
  system: string
  scenario: string | null
  /** Since 26/09/2026: "per conversation" (Niadra), "none" (no verification mechanism), null for the references. */
  verification?: string | null
  cases: Across
  leaks: Across
  leak_rate: Across
}
/** Values without a source per thousand answers (metrics.backing, since 26/09/2026). */
type BackingRow = {
  system: string
  scenario: string | null
  per_1000_answers: Across
  answers: number
  unbacked: number
  by_kind: Record<string, number>
  by_category: Record<string, Across>
}
type CostRow = {
  system: string
  variant: string
  basis: string
  memory_usd_per_1000: Across
  agent_prompt_usd_per_1000: Across
  conversations_per_month?: number
}
type FreshnessRow = { system: string; p50: Across; p95: Across; max: Across; trials: number; timeouts: number }
export type ResilienceRow = {
  system: string
  fault: string
  memory_stage_p50_ms: Across
  memory_stage_max_ms: Across
  within_budget_rate: Across
  raised_rate: Across
  empty_rate: Across
  turn_budget_ms: number
}
/** Metrics 8 (history navigation) and 9 (ingestion acknowledgement): one API call timed like latency. */
export type OperationRow = {
  system: string
  path: string
  /** search or open (navigation); batch, add_raw or add_infer (ingestion). */
  operation: string
  /** The HTTP call, as the harness names it, e.g. "POST /v1/history/search". */
  call: string | null
  rate: number
  p50: Across
  p95: Across
  p99: Across
  max: Across
  errors: number
  error_kinds: Record<string, number>
  sent: number
  empty: number
  degraded: number
  /** Why the line has no number (for example, no item to open). */
  skipped?: string
}

export type BenchmarkRun = {
  schema: 'niadra-bench.results.v1'
  run_id: string
  date: string
  started_at: string
  finished_at: string
  environment: {
    kind: string
    region: string | null
    /** Niadra's cell: its machine and database. */
    machine_class: string | null
    database_class: string | null
    /** Since 26/09/2026: where the harness and every other system ran (a temporary host), and its machine. */
    harness_host?: string | null
    harness_machine_class?: string | null
    verified_by_instance_metadata: boolean
  }
  versions: Record<string, string | null>
  config: {
    hash: string
    repetitions: number
    agent_model: string
    judge_model: string | null
    extraction_model: string
    embedder: string
    latency_rates: number[]
    latency_duration_s: number
    latency_conversations: number
    mem0_top_k: number
    mem0_threshold: number
    token_encoding: string
    turns_per_conversation: number
    prices_checked_on: string
    /** Only in runs that measured history navigation and the ingestion acknowledgement. */
    history_rates?: number[]
    history_duration_s?: number
    history_conversations?: number
    history_max_tokens?: number
    ingest_rates?: number[]
    ingest_duration_s?: number
    ingest_conversations?: number
    ingest_mem0_modes?: string[]
    niadra_guards?: string
  }
  dataset: {
    hash: string
    cases: number
    valid: Across
    excluded: string[][]
    /** Since 28/09/2026 (`bench combine`): the run whose references decide validity for every system. */
    references?: { run_id: string; repetitions: number[] }
    /** Since 28/09/2026: the cases every system answered, and how many of them are valid. */
    shared?: { cases: number; valid: Across }
  }
  metrics: {
    latency?: { unit: string; results: LatencyRow[] }
    accuracy?: { unit: string; results: AccuracyRow[] }
    /** Since 28/09/2026: every system scored again on the valid cases all of them answered (the same cases). */
    accuracy_shared?: { unit: string; results: AccuracyRow[] }
    tokens?: { unit: string; results: TokensRow[] }
    privacy?: { unit: string; results: PrivacyRow[] }
    cost?: { unit: string; results: CostRow[] }
    freshness?: { unit: string; results: FreshnessRow[] }
    resilience?: { results: ResilienceRow[] }
    history?: { unit: string; results: OperationRow[] }
    ingest?: { unit: string; results: OperationRow[] }
    backing?: { unit: string; results: BackingRow[] }
  }
  /** A run put together by `bench combine` from runs of one system each (the temporary host measures one at a time). */
  combined_from?: { run_id: string; systems: string[]; started_at: string; finished_at: string; repetitions?: number; cases?: number }[]
  /** Since 28/09/2026: how many repetitions and cases each system ran, and in which source run. */
  per_system?: Record<string, { run_id: string; repetitions: number; cases: number }>
  /**
   * Since 29/09/2026: Niadra's `host` path (a pod on its own cell's machine, plain HTTP to each service),
   * measured in a run of its own right after the combined repetitions and merged by the importer
   * (`--host-lines`): its lines sit in metrics.latency, history and ingest with path "host".
   */
  /** Each run of Niadra's `host` path merged in, with the rates of the lines it added, per metric. */
  host_lines_from?: { run_id: string; started_at: string; finished_at: string; repetitions: number; cases: number; config_hash: string; environment_kind: string; rates: Record<string, number[]> }[]
  /** Optional, added by hand after a run: one line per metric where Niadra is not first, stating what was measured. */
  site_notes?: Partial<Record<'pt' | 'en', Partial<Record<string, string>>>>
  /**
   * Optional, added by hand after a run from the harness's per-system caveats: one line per system (a
   * system that was not measured included), printed in the table of repetitions and cases.
   */
  site_caveats?: Partial<Record<'pt' | 'en', Partial<Record<string, string>>>>
}

/**
 * Whether the run measured every system but Niadra on a host of its own (since 26/09/2026), rather than
 * as pods of Niadra's cluster: the page's method text follows what the file says.
 */
export const onTemporaryHost = (run: BenchmarkRun) =>
  Boolean(run.environment.harness_host) || (run.metrics.latency?.results ?? []).some((r) => r.path === 'host' || r.path === 'vpc')

/** Where each run's files live in the public harness repository. */
export const HARNESS_URL = 'https://github.com/ainiadra/niadra-sdk-python/tree/main/benchmarks'
export const runFolder = (run: BenchmarkRun) => `${run.date}-${run.run_id.slice(-6)}`
export const runUrl = (run: BenchmarkRun) => `${HARNESS_URL}/results/${runFolder(run)}`

/** Only runs measured in the region are published; the importer already refuses the rest. */
const isPublishable = (run: BenchmarkRun) =>
  run.schema === 'niadra-bench.results.v1' && run.environment.kind === 'region' && Boolean(run.environment.region)

const files = import.meta.glob<BenchmarkRun>('./runs/*.json', { eager: true, import: 'default' })

/**
 * Newest first, by when the run finished: a combined run starts when its oldest source started, so two
 * combines that share a source share a start (28/09/2026), and the one with the newer measurement wins.
 */
export const RUNS: BenchmarkRun[] = Object.values(files)
  .filter(isPublishable)
  .sort((a, b) => b.finished_at.localeCompare(a.finished_at) || b.run_id.localeCompare(a.run_id))

/** Whether the page shows numbers, goes to the sitemap, the prerender and llms.txt. */
export const BENCHMARK_LIVE = RUNS.length > 0

