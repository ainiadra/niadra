/**
 * The benchmark's line in llms.txt (scripts/prerender.mjs): the headline of the newest published run,
 * read from its results file and written with the page's own formatters and rounding, so an AI reads the
 * numbers the page shows. Accuracy is every system on the same valid cases; cost is the one method the
 * page ranks (model spend at the script's prices). After it, the typed-object set: Niadra's own agent with
 * and without the state and constraints blocks.
 */
import { fmtDate, fmtInt, fmtPct, fmtUsd, type Lean } from '../../components/benchmark/format'
import { dicts, type Lang } from '../../i18n'
import { RUNS } from '.'
import { TYPED_RUNS, typedRunDate, typedRunUrl, type Share, type TypedRun } from './typed'

const REFERENCES = new Set(['no_memory', 'full_history', 'embedder'])
/** Niadra's value never rounds in its favor; every other system's rounds toward it (format.ts). */
const lean = (system: string, better: 'higher' | 'lower'): Lean => ((system === 'niadra') === (better === 'higher') ? 'down' : 'up')

/** Fills {name} marks; a function replacer, so a value with a dollar sign is never read as a pattern. */
const fill = (text: string, vars: Record<string, string>) => text.replace(/\{(\w+)\}/g, (_, k: string) => vars[k] ?? `{${k}}`)

function regionFact(lang: Lang): string | null {
  const run = RUNS[0]
  if (!run) return null
  const b = dicts[lang].benchmark
  const name = (system: string) => b.systems[system] ?? system
  const reps = (system: string) => {
    const n = run.per_system?.[system]?.repetitions ?? run.config.repetitions
    return (n === 1 ? b.repOne : b.repMany).replace('{n}', String(n))
  }
  const rows = (run.metrics.accuracy_shared?.results ?? run.metrics.accuracy?.results ?? [])
    .filter((r) => !REFERENCES.has(r.system) && r.scenario !== 'per_channel_id' && r.judge.median != null)
    .sort((x, y) => y.judge.median! - x.judge.median!)
  if (!rows.length) return null
  const accuracy = rows.map((r) => `${name(r.system)} ${fmtPct(lang, r.judge.median!, lean(r.system, 'higher'))} (${reps(r.system)})`).join(', ')
  const cost = (run.metrics.cost?.results ?? [])
    .filter((r) => r.variant === 'models_only' && r.memory_usd_per_1000.median != null && !REFERENCES.has(r.system))
    .sort((x, y) => x.memory_usd_per_1000.median! - y.memory_usd_per_1000.median!)
    .map((r) => `${name(r.system)} ${fmtUsd(lang, r.memory_usd_per_1000.median!, lean(r.system, 'lower'))}`)
    .join(', ')
  const valid = run.dataset.shared?.valid.median ?? run.dataset.valid.median ?? 0
  return fill(b.llmsFact, { date: fmtDate(lang, run.date), region: run.environment.region ?? '', valid: String(valid), judge: run.config.judge_model ?? '', accuracy, cost })
}

/** The arm with the blocks is Niadra's claim and rounds down; the arm without them rounds up (Benchmark.tsx does the same). */
function typedFact(lang: Lang, run: TypedRun): string {
  const y = dicts[lang].benchmark.typed
  const int = (v: number) => fmtInt(lang, v)
  const acc = run.metrics.accuracy.all
  const withPct = (s: Share) => fmtPct(lang, s.share, 'down')
  const withoutPct = (s: Share) => fmtPct(lang, s.share, 'up')
  const interval = (s: Share) => fill(y.interval, { lo: int(s.ci95[0] * 100), hi: int(s.ci95[1] * 100) })
  const fromTo = (c: string) => (acc[c] ? fill(y.fromTo, { from: withoutPct(acc[c].without.judge), to: withPct(acc[c].with.judge) }) : '')
  const paired = run.baseline?.tokens.v2_sample.voice
  const signed = (v: number, text: string) => (v >= 0 ? `+${text}` : text)
  const v0 = run.metrics.v0.with
  return fill(y.llmsFact, {
    date: fmtDate(lang, typedRunDate(run)),
    reps: int(run.config.repetitions),
    answers: int(acc.all.with.judge.n),
    with: withPct(acc.all.with.judge),
    interval: interval(acc.all.with.judge),
    without: withoutPct(acc.all.without.judge),
    hard: fromTo('hard_constraint'),
    changes: fromTo('changes_since_seen'),
    leaks: `${int(v0.sensitive_in_block)} ${lang === 'pt' ? 'de' : 'of'} ${int(v0.reads)}`,
    effect: fill(y.checksOf, { n: int(run.metrics.coordination.refused_as_done), total: int(run.metrics.coordination.checks) }),
    paired: paired ? signed(paired.median_change, fmtPct(lang, paired.median_change)) : '',
    url: typedRunUrl(run),
  })
}

export function benchmarkFact(lang: Lang): string | null {
  const parts = [regionFact(lang), TYPED_RUNS[0] ? typedFact(lang, TYPED_RUNS[0]) : null].filter((p): p is string => Boolean(p))
  return parts.length ? parts.join(' ') : null
}
