import { useT, type Lang } from '../i18n'
import { Link } from '../i18n/link'
import { QuickAccess } from '../components/QuickAccess'
import { H2, Lede, PageHead, Sec } from '../components/Shell'
import { ArmBars, Bars, ChartCard, Legend, Scatter, SystemLabel, familyOf, isReference, rankBars, type ArmRow, type Bar, type Better, type Family, type Point, type TableProps } from '../components/benchmark/Charts'
import { fmtDate, fmtInt, fmtMs, fmtNum, fmtPct, fmtUsd, type Lean } from '../components/benchmark/format'
import { HARNESS_URL, RUNS, onTemporaryHost, runFolder, runUrl, type Across, type BenchmarkRun, type OperationRow, type ResilienceRow } from '../content/benchmark'
import { TYPED_RUNS, typedRunDate, typedRunUrl, type Share, type TypedRun } from '../content/benchmark/typed'
import { display, h3cls, panel, textLink, wrap } from '../ui'

/** Fills {name} marks in a text. Values are already formatted strings from the results file. */
const fill = (text: string, vars: Record<string, string>) => text.replace(/\{(\w+)\}/g, (_, k: string) => vars[k] ?? `{${k}}`)

/** Reading order of the systems in tables: Niadra, Mem0, the field, then the references. */
const SYSTEM_ORDER = [
  'niadra',
  'mem0_oss',
  'mem0_oss_rerank',
  'mem0_platform',
  'ai_memory',
  'ai_memory_llm',
  'graphiti',
  'hindsight',
  'hindsight_reflect',
  'memobase',
  'memos',
  'supermemory',
  'redis_agent_memory',
  'honcho',
  'honcho_dialectic',
  'langmem',
  'cognee',
  'agentcore_memory',
  'embedder',
  'full_history',
  'no_memory',
]
// A key the page does not know yet goes after the known systems, before the references.
const orderOf = (system: string) => {
  const i = SYSTEM_ORDER.indexOf(system)
  return i < 0 ? SYSTEM_ORDER.indexOf('embedder') - 0.5 : i
}
const bySystem = <T extends { system: string }>(a: T, b: T) => orderOf(a.system) - orderOf(b.system)
const has = (a: Across | undefined): a is Across & { median: number } => a != null && a.median != null

/** Operations of metrics 8 and 9 in reading order: navigation, the embedder alone, then the writes. */
const OPERATION_ORDER = ['search', 'open', 'encode', 'batch', 'write', 'add_raw', 'add_infer']
const opOrder = (operation: string) => (OPERATION_ORDER.indexOf(operation) + OPERATION_ORDER.length + 1) % (OPERATION_ORDER.length + 1)
const byOperation = (a: OperationRow, b: OperationRow) =>
  bySystem(a, b) || opOrder(a.operation) - opOrder(b.operation) || a.path.localeCompare(b.path) || a.rate - b.rate

/** The path a highlight reads per system: Niadra's public address first, the way an agent calls it (owner, 28/09/2026). */
const PATH_PREFERENCE = ['edge', 'vpc', 'cluster', 'host']
/** The view a highlight reads per system: Niadra's voice view, every other system's only view. */
const VIEW_PREFERENCE = ['voice', 'default', 'chat']
const preferred = (list: string[], v: string) => {
  const i = list.indexOf(v)
  return i < 0 ? list.length : i
}

/** Judge grade when the run had a judge, the exact check otherwise. */
const grade = (row: { judge: Across; deterministic: Across }) => (has(row.judge) ? row.judge : row.deterministic)

/**
 * Which way a system's value rounds: Niadra's against it, every other system's toward it, the references
 * and unnamed values to the nearest.
 */
const leanOf = (system: string, better: Better): Lean =>
  !system || isReference(system) ? 'nearest' : (system === 'niadra') === (better === 'higher') ? 'down' : 'up'

/** A call as the harness names it, with the customer's own identifiers (a bank, a user UUID) as {id}. */
const shortCall = (call: string) =>
  call
    .split('/')
    .map((part) => (/\d/.test(part) && part.length >= 12 && !part.includes('{') ? '{id}' : part))
    .join('/')

function useB() {
  const { t, lang } = useT()
  return { b: t.benchmark, lang }
}

function Pending() {
  const { b } = useB()
  return (
    <Sec>
      <div className={`${panel} p-6 sm:p-8`}>
        <div className="flex items-center gap-3">
          {/* Open ring: pending, not done. */}
          <svg width="18" height="18" viewBox="0 0 18 18" aria-hidden="true">
            <circle cx="9" cy="9" r="7" fill="none" stroke="var(--color-accent)" strokeWidth="2" />
          </svg>
          <h2 className={`${h3cls} text-[1.35rem]`}>{b.pendingH2}</h2>
        </div>
        <p className="mt-4 max-w-[64ch] text-[1.02rem] leading-relaxed text-ink2">{b.pendingBody}</p>
        <h3 className="mt-7 text-[0.95rem] font-semibold text-ink">{b.pendingListH}</h3>
        <ul className="mt-3 grid gap-2.5 sm:grid-cols-2">
          {b.measures.map((m) => (
            <li key={m} className="flex gap-3 text-[0.96rem] leading-snug text-ink2">
              <span className="mt-[0.45rem] h-1.5 w-1.5 shrink-0 rounded-full bg-ink3" aria-hidden="true" />
              {m}
            </li>
          ))}
        </ul>
        <p className="mt-7 max-w-[64ch] text-[0.94rem] text-ink3">{b.harnessNote}</p>
        <Link className={`${textLink} mt-2`} to={HARNESS_URL}>
          {b.harnessCta}
        </Link>
      </div>
    </Sec>
  )
}

type TileLine = { key: string; system: string; name: string; detail?: string; value: number; text: string; unranked?: boolean }

/** A highlight in the manner of a leaderboard: every system of the run, best first, with its position and logo. */
function RankTile({ label, better, lines }: { label: string; better: Better; lines: TileLine[] }) {
  const { b } = useB()
  const rows = rankBars(lines.map((l) => ({ ...l, label: l.name })), better)
  const rankedCount = rows.filter((r) => r.rank != null).length
  const firsts = rows.filter((r) => r.rank === 1).length
  return (
    <div className={`${panel} flex flex-col p-5`}>
      <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
        <h3 className="text-[0.95rem] font-semibold leading-snug text-ink">{label}</h3>
        <span className="text-[0.8rem] text-ink3">{b.better[better]}</span>
      </div>
      <ol className="mt-3 grid gap-1">
        {rows.map(({ bar, rank }) => {
          const first = rank === 1 && rankedCount > 1 && firsts === 1
          return (
            <li key={bar.key} className="grid min-h-7 grid-cols-[1.25rem_minmax(0,1fr)_auto] items-center gap-x-2">
              <span className="text-right font-mono text-[0.8rem] tabular-nums text-ink3">{rankedCount > 1 ? (rank ?? '') : ''}</span>
              <span className={`min-w-0 text-[0.9rem] leading-snug ${first ? 'font-semibold text-ink' : 'text-ink2'}`}>
                <SystemLabel system={bar.system} label={bar.label} size={18} />
                {bar.detail && <span className="text-ink3">, {bar.detail}</span>}
              </span>
              <span className={`whitespace-nowrap text-right font-mono tabular-nums ${first ? 'text-[1.02rem] font-semibold text-ink' : 'text-[0.9rem] text-ink2'}`}>{bar.text}</span>
            </li>
          )
        })}
      </ol>
    </div>
  )
}

function Results({ run, runs, typed }: { run: BenchmarkRun; runs: BenchmarkRun[]; typed?: TypedRun }) {
  const { b, lang } = useB()
  const m = run.metrics
  const host = onTemporaryHost(run)
  const name = (system: string) => b.systems[system] ?? system
  const note = (key: string) => run.site_notes?.[lang]?.[key]
  // A system's value is never rounded in Niadra's favor (format.ts): the system passed along picks the direction.
  const ms = (v: number, system = '', better: Better = 'lower') => fmtMs(lang, v, leanOf(system, better))
  const pct = (v: number, system = '', better: Better = 'higher') => fmtPct(lang, v, leanOf(system, better))
  const usd = (v: number, system = '', better: Better = 'lower') => fmtUsd(lang, v, leanOf(system, better))
  const int = (v: number, system = '', better: Better = 'lower') => fmtInt(lang, v, leanOf(system, better))
  const num = (v: number, system = '', better: Better = 'lower') => fmtNum(lang, v, leanOf(system, better))
  const acr = (a: Across, f: (v: number, system?: string) => string, system = '') => (has(a) ? f(a.median, system) : '')
  const and = (list: string[]) => list.join(lang === 'pt' ? ' e ' : ' and ')
  const direction = (better: Better) => ({ better, text: b.better[better] })
  const ranked = (props: { bars: Bar[]; groupLabel?: string; better: Better; limit?: { value: number; label: string } }) => <Bars {...props} bestLabel={b.best} />

  const latency = [...(m.latency?.results ?? [])].sort(bySystem)
  const rates = [...new Set(latency.map((r) => r.rate))].sort((x, y) => x - y)
  // Each system on all of its own valid cases.
  const ownAccuracy = [...(m.accuracy?.results ?? [])].sort(bySystem)
  // Since 28/09/2026 the headline reads every system on the same valid cases; older runs had one set of cases for all.
  const sharedRun = Boolean(m.accuracy_shared?.results.length)
  const accuracy = sharedRun ? [...m.accuracy_shared!.results].sort(bySystem) : ownAccuracy
  const tokens = [...(m.tokens?.results ?? [])].filter((r) => r.system !== 'no_memory' && r.scenario !== 'per_channel_id').sort(bySystem)
  const privacy = [...(m.privacy?.results ?? [])].filter((r) => r.system !== 'no_memory').sort(bySystem)
  const cost = [...(m.cost?.results ?? [])].sort(bySystem)
  const freshness = [...(m.freshness?.results ?? [])].sort(bySystem)
  const resilience = [...(m.resilience?.results ?? [])].sort(bySystem)
  const navigation = [...(m.history?.results ?? [])].sort(byOperation)
  const ingest = [...(m.ingest?.results ?? [])].sort(byOperation)
  const backing = [...(m.backing?.results ?? [])].sort(bySystem)
  const scenarios = ['known_id', 'per_channel_id']
  const inScenario = <T extends { scenario: string | null }>(rows: T[], scenario: string) => rows.filter((r) => r.scenario === scenario || r.scenario === null)
  const withMechanism = privacy.some((r) => r.verification != null)
  const hasMem0 = [...accuracy, ...latency, ...cost].some((r) => r.system.startsWith('mem0'))
  const hasAgentCore = accuracy.some((r) => r.system === 'agentcore_memory')

  const accIn = (scenario: string) => inScenario(accuracy, scenario)
  const faultLabel = (fault: string) => {
    const delay = /^delay_(\d+)ms$/.exec(fault)
    if (delay) return fill(b.faults.delay, { ms: ms(Number(delay[1])) })
    return fill(b.faults.status, { status: fault.replace(/^http_/, '') })
  }
  // Niadra's `host` path is a pod on its own cell's machine; every other system's is the harness's host.
  // What the agent gets when the memory fails. Raised is checked first: a fast error is still a broken turn.
  const outcomeOf = (r: ResilienceRow) => {
    const raised = r.raised_rate.median ?? 0
    if (raised >= 1) return b.outcomes.raised
    const got = (r.within_budget_rate.median ?? 0) < 1 ? b.outcomes.late : (r.empty_rate.median ?? 0) >= 1 ? b.outcomes.empty : b.outcomes.served
    return raised > 0 ? `${got}, ${fill(b.outcomes.raisedSome, { share: pct(raised, r.system, 'lower') })}` : got
  }
  /** A turn served: no error reached the agent, and every trial came back within the turn's budget. */
  const served = (r: ResilienceRow) => r.raised_rate.median === 0 && r.within_budget_rate.median === 1
  const pathLabel = (path: string, system = '') => (system === 'niadra' && path === 'host' ? b.paths.niadraHost : (b.paths[path] ?? path))
  const opLabel = (operation: string) => b.operations[operation] ?? operation
  const leaksText = (r: (typeof privacy)[number]) => (has(r.leaks) && has(r.cases) ? fill(b.ofCases, { n: int(r.leaks.median), total: int(r.cases.median) }) : '')
  /** How many repetitions a system ran: its own source run's in a combined run, the run's otherwise. */
  const repsOf = (system: string) => run.per_system?.[system]?.repetitions ?? run.config.repetitions
  const repsText = (n: number) => fill(n === 1 ? b.repOne : b.repMany, { n: int(n) })
  /** Every call of a line failed: the line has no timing, only that count. */
  const failedText = (r: { errors: number; sent: number }) => fill(b.allErrors, { n: int(r.errors), total: int(r.sent) })
  /** The table row of an operation line; a line the harness could not measure says so instead of a number. */
  const opRow = (r: OperationRow) => [
    name(r.system),
    opLabel(r.operation),
    <code key="call" data-id className="font-mono text-[0.8rem]">{shortCall(r.call ?? '')}</code>,
    pathLabel(r.path, r.system),
    int(r.rate),
    r.skipped ? b.notMeasured : acr(r.p50, ms, r.system),
    acr(r.p95, ms, r.system),
    acr(r.p99, ms, r.system),
    `${int(r.errors)} / ${int(r.sent)}`,
    int(r.empty),
  ]
  const opBar = (r: OperationRow, detail: string): Bar =>
    has(r.p95)
      ? { key: `${r.system}-${r.operation}-${r.path}-${r.rate}`, system: r.system, label: name(r.system), detail: r.errors ? `${detail}, ${failedText(r)}` : detail, value: r.p95.median, min: r.p95.min, max: r.p95.max, text: ms(r.p95.median, r.system), unranked: isReference(r.system) }
      : !r.skipped && r.sent > 0 && r.errors === r.sent
        ? { key: `${r.system}-${r.operation}-${r.path}-${r.rate}`, system: r.system, label: name(r.system), detail: `${detail}, ${failedText(r)}`, value: 0, text: b.noNumber, unranked: true }
        : { key: `${r.system}-${r.operation}-${r.path}-${r.rate}`, system: r.system, label: name(r.system), detail, value: 0, text: b.notMeasured, unranked: true }
  const table = (t: TableProps) => t

  // Niadra's navigation and acknowledgement rates, from the lines themselves (runs before 02/10/2026 capped them at 10).
  const niadraOps = [...new Set([...navigation, ...ingest].filter((r) => r.system === 'niadra').map((r) => r.rate))].sort((x, y) => x - y)
  const vars = {
    date: fmtDate(lang, run.date),
    region: run.environment.region ?? '',
    machine: run.environment.machine_class ?? '',
    database: run.environment.database_class ?? '',
    harnessMachine: run.environment.harness_machine_class ?? '',
    reps: int(run.config.repetitions),
    server: run.versions.niadra_server ?? '',
    sdk: run.versions.niadra_sdk ?? '',
    mem0: run.versions.mem0_server ? `${run.versions.mem0_server} (mem0ai ${run.versions.mem0ai ?? ''})` : `mem0ai ${run.versions.mem0ai ?? ''}`,
    extraction: run.config.extraction_model,
    embedder: run.config.embedder,
    agent: run.config.agent_model,
    judge: run.config.judge_model ?? '',
    topK: int(run.config.mem0_top_k),
    threshold: fmtNum(lang, run.config.mem0_threshold),
    cases: int(run.dataset.cases),
    valid: acr(run.dataset.valid, int),
    rates: and(run.config.latency_rates.map((v) => int(v))),
    duration: int(run.config.latency_duration_s),
    conversations: int(run.config.latency_conversations),
    encoding: run.config.token_encoding,
    turns: int(run.config.turns_per_conversation),
    prices: fmtDate(lang, run.config.prices_checked_on),
    historyRates: and((run.config.history_rates ?? []).map((v) => int(v))),
    historyDuration: run.config.history_duration_s != null ? int(run.config.history_duration_s) : '',
    historyConversations: run.config.history_conversations != null ? int(run.config.history_conversations) : '',
    ingestRates: and((run.config.ingest_rates ?? []).map((v) => int(v))),
    ingestDuration: run.config.ingest_duration_s != null ? int(run.config.ingest_duration_s) : '',
    niadraOpsRates: and(niadraOps.map((v) => int(v))),
    refRun: run.dataset.references?.run_id ?? '',
    sharedCases: run.dataset.shared ? int(run.dataset.shared.cases) : '',
    sharedValid: run.dataset.shared ? acr(run.dataset.shared.valid, int) : '',
    niadraReps: repsText(repsOf('niadra')),
    mem0Reps: repsText(repsOf('mem0_oss')),
    agentcoreReps: repsText(repsOf('agentcore_memory')),
  }

  // Highlights: one line per system, the way each chart below ranks it.
  const firstRate = rates[0]
  const onePer = <T extends { system: string }>(rows: T[], rank: (r: T) => number) => {
    const best = new Map<string, T>()
    for (const r of rows) {
      const held = best.get(r.system)
      if (!held || rank(r) < rank(held)) best.set(r.system, r)
    }
    return [...best.values()].sort(bySystem)
  }
  const latTile: TileLine[] = onePer(
    latency.filter((r) => r.rate === firstRate && has(r.p95)),
    (r) => preferred(PATH_PREFERENCE, r.path),
  ).map((r) => ({ key: r.system, system: r.system, name: name(r.system), detail: r.system === 'niadra' ? pathLabel(r.path, r.system) : undefined, value: r.p95.median!, text: ms(r.p95.median!, r.system) }))
  const accTile: TileLine[] = accIn('known_id')
    .filter((r) => !isReference(r.system) && has(grade(r)))
    .map((r) => ({ key: r.system, system: r.system, name: name(r.system), value: grade(r).median!, text: pct(grade(r).median!, r.system) }))
  const tokTile: TileLine[] = onePer(
    tokens.filter((r) => !isReference(r.system) && has(r.median)),
    (r) => preferred(VIEW_PREFERENCE, r.view),
  ).map((r) => ({ key: r.system, system: r.system, name: name(r.system), detail: b.views[r.view] || undefined, value: r.median.median!, text: int(r.median.median!, r.system) }))
  const costOf = (system: string, variant?: string) => cost.find((c) => c.system === system && has(c.memory_usd_per_1000) && (!variant || c.variant === variant))
  const measured = new Set(accuracy.map((r) => r.system))
  const low = costOf('niadra', 'price_low')
  const high = costOf('niadra', 'price_high')
  // Since 28/09/2026: Niadra's model spend by the method of every other system (read from its cell).
  const niadraModels = costOf('niadra', 'models_only')
  const costTile: TileLine[] = [...measured]
    .filter((system) => !isReference(system))
    .sort((x, y) => orderOf(x) - orderOf(y))
    .flatMap<TileLine>((system) => {
      if (system === 'niadra') {
        if (niadraModels) return [{ key: system, system, name: name(system), value: niadraModels.memory_usd_per_1000.median!, text: usd(niadraModels.memory_usd_per_1000.median!, system) }]
        // Ranked by the top of its price range, the less favorable end.
        return low && high ? [{ key: system, system, name: name(system), value: high.memory_usd_per_1000.median!, text: fill(b.range, { low: usd(low.memory_usd_per_1000.median!, system), high: usd(high.memory_usd_per_1000.median!, system) }) }] : []
      }
      const c = system === 'mem0_platform' ? costOf(system, 'pro') : costOf(system)
      // A managed service's line is its provider's price, not a measured model spend: the line says so.
      const detail = c?.variant === 'service_price' ? b.variants.service_price : undefined
      return c ? [{ key: system, system, name: name(system), detail, value: c.memory_usd_per_1000.median!, text: usd(c.memory_usd_per_1000.median!, system) }] : []
    })
  const backTile: TileLine[] = inScenario(backing, 'known_id')
    .filter((r) => !isReference(r.system) && has(r.per_1000_answers))
    .map((r) => ({ key: r.system, system: r.system, name: name(r.system), value: r.per_1000_answers.median!, text: num(r.per_1000_answers.median!, r.system) }))
  const privTile: TileLine[] = inScenario(privacy, 'known_id')
    .filter((r) => !isReference(r.system) && has(r.leaks) && has(r.cases))
    .map((r) =>
      r.verification === 'none'
        ? { key: r.system, system: r.system, name: name(r.system), value: 0, text: b.noMechanism, unranked: true }
        : { key: r.system, system: r.system, name: name(r.system), value: r.leaks.median!, text: leaksText(r) },
    )

  // Scatter: cost against accuracy in the known ID scenario, every system with both.
  const points: Point[] = []
  for (const acc of accIn('known_id')) {
    const system = acc.system
    if (isReference(system) || !has(grade(acc))) continue
    const y = grade(acc).median!
    if (system === 'niadra') {
      if (niadraModels) {
        points.push({ key: system, system, label: name(system), x: niadraModels.memory_usd_per_1000.median!, y, text: `${usd(niadraModels.memory_usd_per_1000.median!, system)} · ${pct(y, system)}` })
        continue
      }
      if (low && high) points.push({ key: system, system, label: name(system), x: low.memory_usd_per_1000.median!, xTo: high.memory_usd_per_1000.median!, y, text: `${usd(low.memory_usd_per_1000.median!, system)} / ${usd(high.memory_usd_per_1000.median!, system)} · ${pct(y, system)}` })
      continue
    }
    const c = system === 'mem0_platform' ? costOf(system, 'pro') : costOf(system)
    if (c) points.push({ key: system, system, label: name(system), x: c.memory_usd_per_1000.median!, y, text: `${usd(c.memory_usd_per_1000.median!, system)} · ${pct(y, system)}` })
  }

  const families = new Set<Family>([...accuracy, ...latency, ...cost, ...navigation].map((r) => familyOf(r.system)))
  const legend = (['niadra', 'mem0', 'field', 'reference'] as Family[]).filter((f) => families.has(f)).map((f) => ({ family: f, label: b.families[f] }))

  const methodLines = [
    ...(host ? b.methodHost.filter((line) => hasMem0 || !line.includes('{topK}')) : b.method),
    ...(hasAgentCore ? [b.methodAgentCore] : []),
    ...(run.combined_from?.length ? [b.methodCombined] : []),
    ...(backing.length ? [b.methodBacking] : []),
    ...(withMechanism ? [b.methodVerification] : []),
    ...(navigation.length > 0 || ingest.length > 0 ? [host ? b.methodOpsHost : b.methodOps] : []),
    // Niadra's lines short of a rate the other systems ran: the production cap of that run.
    ...(host && niadraOps.length > 0 && [...navigation, ...ingest].some((r) => !niadraOps.includes(r.rate)) ? [b.methodOpsCap] : []),
    // One sentence per run of Niadra's `host` path, with what it added, then the dry-run remark once.
    ...(run.host_lines_from ?? []).map((source) =>
      fill(b.methodHostLines, {
        what: and(Object.entries(source.rates).map(([metric, list]) => fill(b.atRates, { metric: b.hostMetrics[metric] ?? metric, rates: and(list.map((v) => int(v))) }))),
        hostRun: `${source.started_at.slice(0, 10)}-${source.run_id.slice(-6)}`,
        hostReps: repsText(source.repetitions),
        hostCases: int(source.cases),
      }),
    ),
    ...(run.host_lines_from?.length ? [b.methodHostLinesDry] : []),
  ]

  // The headline: accuracy across channels, every system on the same valid cases when the run has them.
  // No repetition count on the chart or in its table: that lives in the method section only (owner, 28/09/2026).
  const niadraOwn = ownAccuracy.find((r) => r.system === 'niadra' && has(grade(r)))
  const niadraShared = accuracy.find((r) => r.system === 'niadra')
  // Niadra on all of its own valid cases, when they are more than the shared ones: shown, labeled, never ranked.
  const fullLine: Bar | null =
    sharedRun && niadraOwn && niadraShared && has(niadraOwn.cases) && niadraOwn.cases.median !== niadraShared.cases.median
      ? { key: 'niadra-full', system: 'niadra', label: name('niadra'), detail: fill(b.fullLine, { n: int(niadraOwn.cases.median) }), value: grade(niadraOwn).median!, text: pct(grade(niadraOwn).median!, 'niadra'), unranked: true }
      : null
  // The table under the headline: every line on the shared cases, then each system's line on all of its own cases where they differ.
  const ownExtra = sharedRun
    ? ownAccuracy.filter((r) => {
        const same = accuracy.find((a) => a.system === r.system && a.scenario === r.scenario)
        return !isReference(r.system) && has(r.cases) && same && same.cases.median !== r.cases.median
      })
    : []
  const categoryRows = accIn('known_id').filter((r) => r.system !== 'no_memory')
  const cats = Object.keys(categoryRows[0]?.by_category ?? {})
  const accuracyCard = (
    <ChartCard
      id="acerto"
      title={b.charts.accuracy.h}
      sub={sharedRun ? fill(b.charts.accuracy.shared, { valid: vars.sharedValid, cases: vars.sharedCases, agent: vars.agent }) : b.charts.accuracy.sub}
      direction={direction('higher')}
      tableLabel={b.table}
      note={note('accuracy')}
      table={table({
        caption: b.charts.accuracy.h,
        head: [b.th.system, b.th.scenario, b.th.judge, b.th.deterministic, b.th.cases],
        rows: [
          ...accuracy.map((r) => [name(r.system), r.scenario ? b.scenarios[r.scenario] : '', acr(r.judge, pct, r.system), acr(r.deterministic, pct, r.system), acr(r.cases, int)]),
          ...ownExtra.map((r) => [
            name(r.system),
            r.scenario ? `${b.scenarios[r.scenario]}, ${b.wholeDataset}` : b.wholeDataset,
            acr(r.judge, pct, r.system),
            acr(r.deterministic, pct, r.system),
            acr(r.cases, int),
          ]),
        ],
      })}
    >
      {scenarios.map((sc) => (
        <div key={sc}>
          {ranked({
            groupLabel: b.scenarios[sc],
            better: 'higher',
            bars: [
              ...accIn(sc)
                .filter((r) => has(grade(r)))
                .map<Bar>((r) => ({ key: `${r.system}-${sc}`, system: r.system, label: name(r.system), value: grade(r).median!, min: grade(r).min, max: grade(r).max, text: pct(grade(r).median!, r.system), unranked: isReference(r.system) })),
              ...(sc === 'known_id' && fullLine ? [fullLine] : []),
            ],
          })}
        </div>
      ))}
      <h4 className="mt-6 text-[0.95rem] font-semibold text-ink">{sharedRun ? b.categorySharedH : b.categoryH}</h4>
      <div className="mt-2 overflow-x-auto rounded-lg border border-line bg-surface">
        <table className="w-full min-w-[36rem] text-left text-[0.86rem] leading-snug">
          <caption className="sr-only">{sharedRun ? b.categorySharedH : b.categoryH}</caption>
          <thead>
            <tr>
              <th scope="col" className="sticky left-0 z-10 border-b border-line bg-surface px-3 py-2 font-semibold text-ink">{b.th.category}</th>
              {categoryRows.map((r) => (
                <th key={r.system} scope="col" className="min-w-[7rem] border-b border-line px-3 py-2 align-bottom font-semibold text-ink">{name(r.system)}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {cats.map((c) => (
              <tr key={c} className="border-b border-line last:border-b-0">
                <th scope="row" className="sticky left-0 z-10 bg-surface px-3 py-2 font-medium text-ink">{b.categories[c] ?? c}</th>
                {categoryRows.map((r) => (
                  <td key={r.system} className="px-3 py-2 font-mono tabular-nums text-ink2">{r.by_category[c] ? acr(grade(r.by_category[c]), pct, r.system) : ''}</td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </ChartCard>
  )

  // Cases and caveats of every system, a system that was not measured included (its caveat says why).
  const caveats = run.site_caveats?.[lang] ?? {}
  const systemRows = run.per_system
    ? [...new Set([...Object.keys(run.per_system), ...Object.keys(caveats)])]
        .sort((x, y) => orderOf(x) - orderOf(y))
        .map((system) => ({ system, source: run.per_system![system], caveat: caveats[system] }))
    : []

  return (
    <>
      <Sec>
        <p className="max-w-[72ch] text-[0.95rem] leading-relaxed text-ink2">
          {fill(host ? b.runLineHost : b.runLine, vars)}
          {run.combined_from?.length ? ` ${b.combinedLine}` : ''}{' '}
          <Link className="font-semibold text-ink underline decoration-accent decoration-2 underline-offset-4" to={runUrl(run)}>
            {b.resultsLink}
          </Link>
        </p>
        <div className="mt-6">
          <Legend items={legend} />
        </div>
        <div className="mt-5">{accuracy.length > 0 && accuracyCard}</div>
        {systemRows.length > 0 && (
          <div className="mt-10">
            <h2 className={`${h3cls} text-[1.3rem]`}>{b.systemsH2}</h2>
            <p className="mt-2 max-w-[70ch] text-[0.93rem] leading-relaxed text-ink2">{b.systemsSub}</p>
            {/* A list, not a table: at phone width each system keeps its caveat under it, with no sideways scroll. */}
            <ul id="sistemas" aria-label={b.systemsH2} className="mt-4 divide-y divide-line rounded-xl border border-line bg-surface">
              {systemRows.map((r) => (
                <li key={r.system} className="grid grid-cols-[minmax(0,1fr)_auto] gap-x-4 gap-y-1.5 px-4 py-3.5 sm:grid-cols-[15rem_9rem_minmax(0,1fr)] sm:gap-x-6">
                  <span className="min-w-0 text-[0.93rem] font-semibold text-ink">
                    <SystemLabel system={r.system} label={name(r.system)} size={18} />
                  </span>
                  <span className="text-right font-mono text-[0.84rem] tabular-nums leading-6 text-ink2 sm:text-left">
                    {r.source ? fill(b.casesText, { n: int(r.source.cases) }) : b.notMeasuredRow}
                  </span>
                  <p className="col-span-2 text-[0.9rem] leading-relaxed text-ink2 sm:col-span-1">{r.caveat ?? ''}</p>
                </li>
              ))}
            </ul>
          </div>
        )}
      </Sec>

      {/* The typed-object set sits under the headline; the sections after it swap tones so the page keeps alternating. */}
      {typed && <TypedResults run={typed} tone="soft" />}

      <Sec tone={typed ? undefined : 'soft'}>
        <h2 className={`${h3cls} text-[1.3rem]`}>{b.highlightsH2}</h2>
        <p className="mt-2 max-w-[70ch] text-[0.93rem] leading-relaxed text-ink2">{b.highlightsSub}</p>
        <p className="mt-3 max-w-[70ch] text-[0.82rem] leading-relaxed text-ink3">{b.trademarks}</p>
        <div className="mt-5 grid gap-4 md:grid-cols-2 xl:grid-cols-3">
          {accTile.length > 0 && <RankTile label={sharedRun ? b.tiles.accuracy : b.tiles.accuracyOwn} better="higher" lines={accTile} />}
          {privTile.length > 0 && <RankTile label={b.tiles.privacy} better="lower" lines={privTile} />}
          {backTile.length > 0 && <RankTile label={b.tiles.backing} better="lower" lines={backTile} />}
          {costTile.length > 0 && <RankTile label={niadraModels ? b.tiles.costModels : b.tiles.cost} better="lower" lines={costTile} />}
          {latTile.length > 0 && <RankTile label={fill(b.tiles.latency, { rate: int(firstRate) })} better="lower" lines={latTile} />}
          {tokTile.length > 0 && <RankTile label={b.tiles.tokens} better="lower" lines={tokTile} />}
        </div>
        <div className="mt-10 grid gap-6">
          {privacy.length > 0 && (
            <ChartCard
              id="privacidade"
              title={b.charts.privacy.h}
              sub={withMechanism ? `${b.charts.privacy.sub} ${b.charts.privacy.mechanism}` : b.charts.privacy.sub}
              direction={direction('lower')}
              tableLabel={b.table}
              note={note('privacy')}
              table={table({
                caption: b.charts.privacy.h,
                head: withMechanism ? [b.th.system, b.th.scenario, b.th.verification, b.th.leaks] : [b.th.system, b.th.scenario, b.th.leaks],
                rows: privacy.map((r) => [
                  name(r.system),
                  r.scenario ? b.scenarios[r.scenario] : '',
                  ...(withMechanism ? [r.verification ? (b.verification[r.verification] ?? r.verification) : ''] : []),
                  leaksText(r),
                ]),
              })}
            >
              {ranked({
                better: 'lower',
                bars: privacy
                  .filter((r) => has(r.leaks) && has(r.cases))
                  .map<Bar>((r) =>
                    r.verification === 'none'
                      ? // No verification at all: the block goes to any caller, so no score, only the fact.
                        // The count of blocks that held the value stays beside it.
                        { key: `${r.system}-${r.scenario}`, system: r.system, label: name(r.system), detail: r.scenario ? `${b.scenarios[r.scenario]}, ${b.noMechanism}` : b.noMechanism, value: 0, text: leaksText(r), unranked: true }
                      : { key: `${r.system}-${r.scenario}`, system: r.system, label: name(r.system), detail: r.scenario ? b.scenarios[r.scenario] : undefined, value: r.leaks.median!, min: r.leaks.min, max: r.leaks.max, text: leaksText(r), unranked: isReference(r.system) },
                  ),
              })}
            </ChartCard>
          )}


          {backing.length > 0 && (
            <ChartCard
              id="sem-lastro"
              title={b.charts.backing.h}
              sub={b.charts.backing.sub}
              direction={direction('lower')}
              tableLabel={b.table}
              note={note('backing')}
              table={table({
                caption: b.charts.backing.h,
                head: [b.th.system, b.th.scenario, b.th.per1000, b.th.unbacked, b.th.answers],
                rows: backing.map((r) => [name(r.system), r.scenario ? b.scenarios[r.scenario] : '', acr(r.per_1000_answers, num, r.system), int(r.unbacked), int(r.answers)]),
              })}
            >
              {scenarios
                .filter((s) => backing.some((r) => r.scenario === s))
                .map((s) => (
                  <div key={s}>
                    {ranked({
                      groupLabel: b.scenarios[s],
                      better: 'lower',
                      bars: inScenario(backing, s)
                        .filter((r) => has(r.per_1000_answers))
                        .map<Bar>((r) => ({ key: `${r.system}-${s}`, system: r.system, label: name(r.system), value: r.per_1000_answers.median!, min: r.per_1000_answers.min, max: r.per_1000_answers.max, text: num(r.per_1000_answers.median!, r.system), unranked: isReference(r.system) })),
                    })}
                  </div>
                ))}
            </ChartCard>
          )}


          {cost.length > 0 && (
            <ChartCard
              id="custo"
              title={b.charts.cost.h}
              sub={fill(niadraModels ? b.charts.cost.measured : b.charts.cost.sub, vars)}
              direction={direction('lower')}
              tableLabel={b.table}
              note={note('cost')}
              table={table({
                caption: b.charts.cost.h,
                head: [b.th.system, b.th.variant, b.th.memory, b.th.prompt, b.th.perMonth],
                rows: cost.map((r) => [
                  name(r.system),
                  b.variants[r.variant] ?? r.variant,
                  acr(r.memory_usd_per_1000, usd, r.system),
                  acr(r.agent_prompt_usd_per_1000, usd, r.system),
                  r.conversations_per_month != null ? int(r.conversations_per_month) : '',
                ]),
              })}
            >
              {ranked({
                better: 'lower',
                // A system this run did not measure (Mem0 Platform's public plans) is listed, not ranked.
                bars: cost
                  .filter((r) => has(r.memory_usd_per_1000))
                  // With Niadra's measured model spend, only that one method is ranked; its list price and ledger are listed beside it.
                  .map<Bar>((r) => ({ key: `${r.system}-${r.variant}`, system: r.system, label: name(r.system), detail: niadraModels && r.variant === 'models_only' ? undefined : (b.variants[r.variant] ?? r.variant), value: r.memory_usd_per_1000.median!, min: r.memory_usd_per_1000.min, max: r.memory_usd_per_1000.max, text: usd(r.memory_usd_per_1000.median!, r.system), unranked: !measured.has(r.system) || (Boolean(niadraModels) && r.variant !== 'models_only') })),
              })}
            </ChartCard>
          )}


          {points.length > 1 && (
            <ChartCard
              id="custo-e-acerto"
              title={b.charts.scatter.h}
              sub={niadraModels && sharedRun ? b.charts.scatter.measured : b.charts.scatter.sub}
              tableLabel={b.table}
              table={table({ caption: b.charts.scatter.h, head: [b.th.system, b.charts.scatter.x, b.charts.scatter.y], rows: points.map((p) => [p.label, p.text.split(' · ')[0], p.text.split(' · ')[1]]) })}
            >
              <Scatter points={points} xTitle={b.charts.scatter.x} yTitle={b.charts.scatter.y} better={b.charts.scatter.better} />
            </ChartCard>
          )}


          {latency.length > 0 && (
            <ChartCard
              id="latencia"
              title={b.charts.latency.h}
              sub={b.charts.latency.sub}
              direction={direction('lower')}
              tableLabel={b.table}
              note={note('latency')}
              table={table({
                caption: b.charts.latency.h,
                head: [b.th.system, b.th.path, b.th.rate, 'p50', 'p95', 'p99', b.th.errors],
                rows: latency.map((r) => [name(r.system), pathLabel(r.path, r.system), int(r.rate), acr(r.p50, ms, r.system), acr(r.p95, ms, r.system), acr(r.p99, ms, r.system), `${int(r.errors)} / ${int(r.sent)}`]),
              })}
            >
              {rates.map((rate) => (
                <div key={rate}>
                  {ranked({
                    groupLabel: fill(b.charts.latency.group, { rate: int(rate), duration: vars.duration }),
                    better: 'lower',
                    bars: latency
                      .filter((r) => r.rate === rate)
                      .map<Bar>((r) =>
                        has(r.p95)
                          ? { key: `${r.system}-${r.path}`, system: r.system, label: name(r.system), detail: r.errors ? `${pathLabel(r.path, r.system)}, ${failedText(r)}` : pathLabel(r.path, r.system), value: r.p95.median!, min: r.p95.min, max: r.p95.max, text: ms(r.p95.median!, r.system) }
                          : // No read came back in time: no timing, only the count of failed reads.
                            { key: `${r.system}-${r.path}`, system: r.system, label: name(r.system), detail: r.sent ? `${pathLabel(r.path, r.system)}, ${failedText(r)}` : pathLabel(r.path, r.system), value: 0, text: r.sent ? b.noNumber : b.notMeasured, unranked: true },
                      ),
                  })}
                </div>
              ))}
            </ChartCard>
          )}


          {tokens.length > 0 && (
            <ChartCard
              id="tokens"
              title={b.charts.tokens.h}
              sub={fill(b.charts.tokens.sub, vars)}
              direction={direction('lower')}
              tableLabel={b.table}
              note={note('tokens')}
              table={table({
                caption: b.charts.tokens.h,
                head: [b.th.system, b.th.view, b.th.median, 'p95'],
                rows: tokens.map((r) => [name(r.system), b.views[r.view] ?? r.view, acr(r.median, int, r.system), acr(r.p95, int, r.system)]),
              })}
            >
              {ranked({
                better: 'lower',
                bars: tokens
                  .filter((r) => has(r.median))
                  .map<Bar>((r) => ({ key: `${r.system}-${r.view}`, system: r.system, label: name(r.system), detail: b.views[r.view] || undefined, value: r.median.median!, min: r.median.min, max: r.median.max, text: int(r.median.median!, r.system), unranked: isReference(r.system) })),
              })}
            </ChartCard>
          )}


          {freshness.length > 0 && (
            <ChartCard
              id="frescor"
              title={b.charts.freshness.h}
              sub={b.charts.freshness.sub}
              direction={direction('lower')}
              tableLabel={b.table}
              note={note('freshness')}
              table={table({
                caption: b.charts.freshness.h,
                head: [b.th.system, 'p50', 'p95', b.th.timeouts],
                rows: freshness.map((r) => [name(r.system), acr(r.p50, ms, r.system), acr(r.p95, ms, r.system), `${int(r.timeouts)} / ${int(r.trials)}`]),
              })}
            >
              {ranked({
                better: 'lower',
                bars: freshness.map<Bar>((r) =>
                  has(r.p50)
                    ? { key: r.system, system: r.system, label: name(r.system), value: r.p50.median, min: r.p50.min, max: r.p50.max, text: ms(r.p50.median, r.system) }
                    : // Never readable within the timeout: shown as such, not left out.
                      { key: r.system, system: r.system, label: name(r.system), value: 0, text: fill(b.neverArrived, { n: int(r.timeouts), total: int(r.trials) }), unranked: true },
                ),
              })}
            </ChartCard>
          )}


          {resilience.length > 0 && (
            <ChartCard
              id="falhas"
              title={b.charts.resilience.h}
              sub={b.charts.resilience.sub}
              direction={direction('lower')}
              tableLabel={b.table}
              note={note('resilience')}
              table={table({
                caption: b.charts.resilience.h,
                head: [b.th.system, b.th.fault, b.th.outcome, 'p50', 'max', fill(b.th.withinBudget, { ms: ms(resilience[0].turn_budget_ms) }), b.th.raised, b.th.empty],
                rows: resilience.map((r) => [
                  name(r.system),
                  faultLabel(r.fault),
                  outcomeOf(r),
                  acr(r.memory_stage_p50_ms, ms, r.system),
                  acr(r.memory_stage_max_ms, ms, r.system),
                  acr(r.within_budget_rate, pct, r.system),
                  acr(r.raised_rate, (v, system) => pct(v, system, 'lower'), r.system),
                  acr(r.empty_rate, (v, system) => pct(v, system, 'lower'), r.system),
                ]),
              })}
            >
              {[...new Set(resilience.map((r) => r.fault))].map((fault) => (
                <div key={fault}>
                  {ranked({
                    groupLabel: faultLabel(fault),
                    better: 'lower',
                    limit: { value: resilience[0].turn_budget_ms, label: fill(b.turnBudget, { ms: ms(resilience[0].turn_budget_ms) }) },
                    // Only a served turn is ranked; a broken one follows, fastest first, with what the agent got.
                    bars: resilience
                      .filter((r) => r.fault === fault && has(r.memory_stage_p50_ms))
                      .sort((x, y) => x.memory_stage_p50_ms.median! - y.memory_stage_p50_ms.median!)
                      .map<Bar>((r) => ({ key: `${r.system}-${fault}`, system: r.system, label: name(r.system), detail: outcomeOf(r), value: r.memory_stage_p50_ms.median!, min: r.memory_stage_p50_ms.min, max: r.memory_stage_p50_ms.max, text: ms(r.memory_stage_p50_ms.median!, r.system), unranked: !served(r) })),
                  })}
                </div>
              ))}
            </ChartCard>
          )}


          {navigation.length > 0 && (
            <ChartCard
              id="navegacao"
              title={b.charts.history.h}
              sub={navigation.some((r) => r.operation === 'encode') ? `${b.charts.history.sub} ${b.charts.history.encodeNote}` : b.charts.history.sub}
              direction={direction('lower')}
              tableLabel={b.table}
              note={note('history')}
              table={table({
                caption: b.charts.history.h,
                head: [b.th.system, b.th.operation, b.th.call, b.th.path, b.th.calls, 'p50', 'p95', 'p99', b.th.errors, b.th.emptyAnswers],
                rows: navigation.map(opRow),
              })}
            >
              {[...new Set(navigation.filter((r) => r.operation !== 'encode').map((r) => `${r.operation}|${r.rate}`))]
                .sort((x, y) => opOrder(x.split('|')[0]) - opOrder(y.split('|')[0]) || Number(x.split('|')[1]) - Number(y.split('|')[1]))
                .map((group) => {
                  const [operation, rate] = group.split('|')
                  // The embedder's line sits under each search, as the step every search starts with.
                  const lines = navigation.filter((r) => r.rate === Number(rate) && (r.operation === operation || (operation === 'search' && r.operation === 'encode')))
                  return (
                    <div key={group}>
                      {ranked({
                        groupLabel: fill(b.charts.history.group, { operation: opLabel(operation), rate: int(Number(rate)), duration: vars.historyDuration }),
                        better: 'lower',
                        bars: lines.map((r) => opBar(r, pathLabel(r.path, r.system))),
                      })}
                    </div>
                  )
                })}
            </ChartCard>
          )}


          {ingest.length > 0 && (
            <ChartCard
              id="confirmacao"
              title={b.charts.ingest.h}
              sub={b.charts.ingest.sub}
              direction={direction('lower')}
              tableLabel={b.table}
              note={note('ingest')}
              table={table({
                caption: b.charts.ingest.h,
                head: [b.th.system, b.th.operation, b.th.call, b.th.path, b.th.writes, 'p50', 'p95', 'p99', b.th.errors, b.th.emptyAnswers],
                rows: ingest.map(opRow),
              })}
            >
              {[...new Set(ingest.map((r) => r.rate))]
                .sort((x, y) => x - y)
                .map((rate) => (
                  <div key={rate}>
                    {ranked({
                      groupLabel: fill(b.charts.ingest.group, { rate: int(rate), duration: vars.ingestDuration }),
                      better: 'lower',
                      bars: ingest.filter((r) => r.rate === rate).map((r) => opBar(r, `${opLabel(r.operation)}, ${pathLabel(r.path, r.system)}`)),
                    })}
                  </div>
                ))}
            </ChartCard>
          )}
        </div>
      </Sec>

      <Sec tone={typed ? 'soft' : undefined}>
        <H2>{b.methodH2}</H2>
        <ul className="mt-6 grid max-w-[76ch] gap-3">
          {methodLines.map((line) => (
            <li key={line} className="flex gap-3 text-[1rem] leading-relaxed text-ink2">
              <span className="mt-[0.6rem] h-1.5 w-1.5 shrink-0 rounded-full bg-accent" aria-hidden="true" />
              {fill(line, vars)}
            </li>
          ))}
        </ul>
        <Link className={`${textLink} mt-4`} to={HARNESS_URL}>
          {b.harnessCta}
        </Link>
      </Sec>

      <Sec tone={typed ? undefined : 'soft'}>
        <H2>{b.runsH2}</H2>
        <RunsTable lang={lang} runs={runs} />
      </Sec>
    </>
  )
}

/**
 * The typed-object set: Niadra's own agent with and without the state and constraints blocks, on the cases
 * that ask for a number, a deadline or a choice only the state or a constraint backs. The arm with the blocks
 * is Niadra's claim, so it rounds down; the arm without them rounds up. The interval is written in whole
 * percentage points, as the run's own results file writes it.
 */
function TypedResults({ run, tone }: { run: TypedRun; tone?: 'soft' }) {
  const { b, lang } = useB()
  const y = b.typed
  const int = (v: number) => fmtInt(lang, v)
  const withPct = (s: Share) => fmtPct(lang, s.share, 'down')
  const withoutPct = (s: Share) => fmtPct(lang, s.share, 'up')
  const interval = (s: Share) => fill(y.interval, { lo: int(s.ci95[0] * 100), hi: int(s.ci95[1] * 100) })
  const signed = (v: number, text: string) => (v >= 0 ? `+${text}` : text)
  const acc = run.metrics.accuracy.all
  const all = acc.all
  const categories = Object.keys(acc).filter((c) => c !== 'all')
  const name = (c: string) => y.categories[c] ?? c
  const tokens = run.metrics.tokens
  const paired = run.baseline?.tokens.v2_sample
  const v0 = run.metrics.v0
  const coordination = run.metrics.coordination
  const validAll = run.metrics.accuracy.valid.all
  const vars = {
    date: fmtDate(lang, typedRunDate(run)),
    reps: int(run.config.repetitions),
    cases: int(run.dataset.cases),
    answers: int(all.with.judge.n),
    agent: run.config.agent.model,
    sdk: `${run.versions.niadra_sdk} (${run.versions.niadra_sdk_commit})`,
    encoding: run.config.tokens,
    valid: int(run.metrics.validity.valid),
    checks: int(coordination.checks),
  }
  const fromTo = (c: string) => fill(y.fromTo, { from: withoutPct(acc[c].without.judge), to: withPct(acc[c].with.judge) })
  const sameReads = v0.with.reads === v0.without.reads && v0.with.sensitive_in_block === v0.without.sensitive_in_block
  const pairedText = (p: { median_change: number; ci95: [number, number] }) =>
    `${signed(p.median_change, fmtPct(lang, p.median_change))} ${fill(y.interval, { lo: signed(p.ci95[0], fmtPct(lang, p.ci95[0])), hi: signed(p.ci95[1], fmtPct(lang, p.ci95[1])) })}`
  // Six tiles: the coordination count lives in the method list.
  const tiles: { label: string; big: string; small: string }[] = [
    { label: y.tiles.all, big: withPct(all.with.judge), small: `${fromTo('all')} ${interval(all.with.judge)}` },
    ...(acc.hard_constraint ? [{ label: y.tiles.hard, big: withPct(acc.hard_constraint.with.judge), small: `${fromTo('hard_constraint')} ${interval(acc.hard_constraint.with.judge)}` }] : []),
    ...(acc.changes_since_seen ? [{ label: y.tiles.changes, big: withPct(acc.changes_since_seen.with.judge), small: `${fromTo('changes_since_seen')} ${interval(acc.changes_since_seen.with.judge)}` }] : []),
    { label: y.tiles.leaks, big: int(v0.with.sensitive_in_block), small: fill(sameReads ? y.readsOf : y.readsOfWith, { n: int(v0.with.sensitive_in_block), total: int(v0.with.reads) }) },
    {
      label: y.tiles.added,
      big: signed(tokens.voice.added.median, int(tokens.voice.added.median)),
      small: fill(y.tokensLine, { voice: int(tokens.voice.added.median), chat: int(tokens.chat.added.median), voiceJson: int(tokens.voice.tool_json.median), chatJson: int(tokens.chat.tool_json.median) }),
    },
    ...(paired
      ? [{ label: y.tiles.paired, big: signed(paired.voice.median_change, fmtPct(lang, paired.voice.median_change)), small: fill(y.pairedLine, { voice: pairedText(paired.voice), chat: pairedText(paired.chat), pairs: int(paired.voice.pairs) }) }]
      : []),
  ]
  const arm = (s: Share, text: string) => ({ value: s.share, lo: s.ci95[0], hi: s.ci95[1], text })
  const rows: ArmRow[] = [...categories, 'all'].map((c) => ({
    key: c,
    label: name(c),
    without: arm(acc[c].without.judge, `${withoutPct(acc[c].without.judge)} ${interval(acc[c].without.judge)}`),
    with: arm(acc[c].with.judge, `${withPct(acc[c].with.judge)} ${interval(acc[c].with.judge)}`),
  }))
  const cell = (a: Arms1, f: (s: Share) => string) => `${f(a.judge)} ${interval(a.judge)}`
  return (
    <Sec tone={tone}>
      <H2>{y.h2}</H2>
      <Lede>{y.sub}</Lede>
      <p className="mt-6 max-w-[72ch] text-[0.95rem] leading-relaxed text-ink2">
        {fill(y.runLine, vars)}{' '}
        <Link className="font-semibold text-ink underline decoration-accent decoration-2 underline-offset-4" to={typedRunUrl(run)}>
          {b.resultsLink}
        </Link>
      </p>
      <h3 className={`${h3cls} mt-8 text-[1.3rem]`}>{y.tilesH}</h3>
      <div className="mt-4 grid gap-4 md:grid-cols-2 xl:grid-cols-3">
        {tiles.map((tile) => (
          <div key={tile.label} className={`${panel} flex flex-col p-5`}>
            <h4 className="text-[0.95rem] font-semibold leading-snug text-ink">{tile.label}</h4>
            <div className={`${display} mt-3 text-[clamp(1.9rem,6vw,2.5rem)] leading-none tabular-nums`}>{tile.big}</div>
            {tile.small && <p className="mt-2 font-mono text-[0.84rem] leading-relaxed text-ink2">{tile.small}</p>}
          </div>
        ))}
      </div>
      <div className="mt-8">
        <ChartCard
          id="blocos"
          title={y.chart.h}
          sub={y.chart.sub}
          direction={{ better: 'higher', text: b.better.higher }}
          tableLabel={b.table}
          table={{
            caption: y.chart.h,
            head: [y.th.category, y.th.withoutJudge, y.th.withJudge, y.th.withoutExact, y.th.withExact],
            rows: [
              ...[...categories, 'all'].map((c) => [name(c), cell(acc[c].without, withoutPct), cell(acc[c].with, withPct), `${withoutPct(acc[c].without.exact)} ${interval(acc[c].without.exact)}`, `${withPct(acc[c].with.exact)} ${interval(acc[c].with.exact)}`]),
              [fill(y.validRow, { n: int(validAll.with.judge.n) }), cell(validAll.without, withoutPct), cell(validAll.with, withPct), `${withoutPct(validAll.without.exact)} ${interval(validAll.without.exact)}`, `${withPct(validAll.with.exact)} ${interval(validAll.with.exact)}`],
            ],
          }}
        >
          <ArmBars rows={rows} arms={y.arms} />
        </ChartCard>
      </div>
      <h3 className={`${h3cls} mt-10 text-[1.3rem]`}>{y.methodH}</h3>
      <ul className="mt-4 grid max-w-[76ch] gap-3">
        {y.method.map((line) => (
          <li key={line} className="flex gap-3 text-[1rem] leading-relaxed text-ink2">
            <span className="mt-[0.6rem] h-1.5 w-1.5 shrink-0 rounded-full bg-accent" aria-hidden="true" />
            {fill(line, vars)}
          </li>
        ))}
      </ul>
      <Link className={`${textLink} mt-4`} to={typedRunUrl(run)}>
        {b.resultsLink}
      </Link>
    </Sec>
  )
}
type Arms1 = { judge: Share; exact: Share }

/** Versions that are not a system's own (or repeat another key's): the rest are listed per system. */
const VERSION_SKIP = new Set(['harness', 'harness_commit', 'niadra_sdk', 'niadra_server', 'mem0_server', 'ai_memory_llm', 'hindsight_reflect'])

/** Where a run's source runs live: `bench combine` keeps them next to the combined folder. */
const sourceFolder = (source: { run_id: string; started_at: string }) => `${source.started_at.slice(0, 10)}-${source.run_id.slice(-6)}`

function RunsTable({ lang, runs }: { lang: Lang; runs: BenchmarkRun[] }) {
  const { b } = useB()
  const name = (key: string) => b.systems[key] ?? key
  return (
    <div className="mt-6 overflow-x-auto rounded-xl border border-line bg-surface">
      <table className="w-full min-w-[40rem] text-left text-[0.9rem] leading-snug">
        <caption className="sr-only">{b.runsH2}</caption>
        <thead>
          <tr>
            {[b.th.date, b.th.region, b.th.versions, b.th.commit, b.th.files].map((h) => (
              <th key={h} scope="col" className="border-b border-line px-4 py-3 font-semibold text-ink">
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {runs.map((run) => (
            <tr key={run.run_id} className="border-b border-line align-top last:border-b-0">
              <th scope="row" className="px-4 py-3 font-medium text-ink">{fmtDate(lang, run.date)}</th>
              <td className="px-4 py-3 text-ink2">
                {run.environment.region} · {run.environment.machine_class}
                {run.environment.harness_machine_class ? ` · ${b.harnessShort} ${run.environment.harness_machine_class}` : ''}
              </td>
              {/* Identifiers (versions, commit, folder), not measurements: the numbers test skips data-id. */}
              <td data-id className="px-4 py-3 font-mono text-[0.84rem] text-ink2">
                {[
                  `Niadra ${run.versions.niadra_server ?? ''}`,
                  `SDK ${run.versions.niadra_sdk ?? ''}`,
                  ...Object.entries(run.versions)
                    .filter(([key, v]) => v && !VERSION_SKIP.has(key))
                    .map(([key, v]) => `${name(key)} ${v}`),
                ].join(' · ')}
              </td>
              <td data-id className="px-4 py-3 font-mono text-[0.84rem] text-ink2">{(run.versions.harness_commit ?? '').slice(0, 7)}</td>
              <td data-id className="px-4 py-3">
                <Link className="font-semibold text-ink underline decoration-accent decoration-2 underline-offset-4" to={runUrl(run)}>
                  {runFolder(run)}
                </Link>
                {run.combined_from?.length ? (
                  <div className="mt-2 text-[0.84rem] text-ink2">
                    {b.combinedFrom}
                    <ul className="mt-1 grid gap-1">
                      {run.combined_from.map((source) => (
                        <li key={source.run_id}>
                          <Link className="font-mono text-ink underline decoration-accent underline-offset-4" to={`${HARNESS_URL}/results/${sourceFolder(source)}`}>
                            {sourceFolder(source)}
                          </Link>
                          <div className="font-sans">{source.systems.map(name).join(', ')}</div>
                        </li>
                      ))}
                    </ul>
                  </div>
                ) : null}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

/**
 * /benchmark: Niadra and the memory systems it is compared with, in the style of Artificial Analysis
 * (a leaderboard per metric, a ranked bar chart per metric with the value on each bar and which way is
 * better, cost against accuracy, the method and every run), with the brand's type and palette. Until a run measured in the region is imported, it shows the waiting state
 * and no number at all.
 */
export function Benchmark({ runs = RUNS, typed = TYPED_RUNS }: { runs?: BenchmarkRun[]; typed?: TypedRun[] }) {
  const { b } = useB()
  const latest = runs[0]
  return (
    <>
      <PageHead a={b.h1a} b={b.h1b} sub={b.sub} />
      {/* The measured numbers are here; what each system documents, feature by feature, is on the comparison page. */}
      <div className={wrap}>
        <Link to="/comparativo" className={`${textLink} mt-5`}>{b.comparisonLink}</Link>
      </div>
      {latest ? <Results run={latest} runs={runs} typed={typed[0]} /> : <Pending />}
      {!latest && typed[0] && <TypedResults run={typed[0]} tone="soft" />}
      <QuickAccess />
    </>
  )
}
