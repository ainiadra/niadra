/**
 * The typed-object set: Niadra's own agent, with and without the state and constraints blocks, on the cases
 * that ask for a number, a deadline or a choice that only the state or a constraint backs (`bench typed`,
 * niadra-sdk-python/benchmarks). Every file in ./typed is a `typed.json` written by the harness, copied here
 * by `node scripts/import-typed-benchmark.mjs <typed.json>`, which strips the per-case lists and keeps the
 * figures. No number of the section is typed by hand: the page reads these files, and Benchmark.test.tsx
 * fails when a number on the page is not in them.
 */
import { HARNESS_URL } from '.'

/** A share of right answers with its 95% Wilson interval, as the harness writes it. */
export type Share = { n: number; correct: number; share: number; ci95: [number, number] }
type Grades = { judge: Share; exact: Share }
/** The two arms, and the two references that decide validity. */
type Arms = { without: Grades; with: Grades; no_memory: Grades; full_history: Grades }
type Median = { median: number; p95: number }
type TokenView = {
  cases: number
  without: Median
  with: Median
  added: Median & { max: number }
  section: Median
  tool_json: Median
  no_section: number
}
/** A paired comparison of the same subjects' reads against the baseline run's code. */
type Paired = { pairs: number; before_median: number; now_median: number; median_change: number; ci95: [number, number]; changed: number; within_5pct: boolean }
type Reads = { reads: number; effective_v0: number; sensitive_in_block: number; sensitive_in_block_v1: number; proven: number; withheld_when_proven: number }

export type TypedRun = {
  schema: 'niadra-bench.typed.v1'
  run_id: string
  environment: string
  started_at: string
  ended_at: string
  dataset: { name: string; sha256: string; cases: number; generator_version: string }
  config: {
    arms: { without: null; with: string[] }
    agent: { model: string; reasoning_effort: string | null; seed: number | null }
    judge: { model: string; reasoning_effort: string | null; seed: number | null }
    tokens: string
    repetitions: number
    levels: Record<string, string>
  }
  versions: { niadra_sdk: string; niadra_sdk_commit: string }
  metrics: {
    accuracy: { all: Record<string, Arms>; valid: Record<string, Arms> }
    tokens: { voice: TokenView; chat: TokenView; added_by_category: Record<string, number> }
    v0: Record<'without' | 'with', Reads>
    validity: { cases: number; valid: number }
    coordination: { checks: number; refused_as_done: number }
  }
  baseline?: {
    run_id: string
    repetitions: number
    tokens: { v2_sample: { voice: Paired; chat: Paired }; typed_without: { voice: Paired; chat: Paired } }
  }
}

/** Where the run's files live in the public harness repository. */
export const typedRunUrl = (run: TypedRun) => `${HARNESS_URL}/results/typed/${run.run_id}`
/** The run's date, as the folder names it (YYYY-MM-DD). */
export const typedRunDate = (run: TypedRun) => run.started_at.slice(0, 10)

const files = import.meta.glob<TypedRun>('./typed/*.json', { eager: true, import: 'default' })

/** Newest first. */
export const TYPED_RUNS: TypedRun[] = Object.values(files)
  .filter((r) => r.schema === 'niadra-bench.typed.v1')
  .sort((a, b) => b.ended_at.localeCompare(a.ended_at))
