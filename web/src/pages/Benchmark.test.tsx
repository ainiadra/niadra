import { existsSync, statSync } from 'node:fs'
import { render } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it } from 'vitest'
import { I18n, dicts, type Lang } from '../i18n'
import { Benchmark } from './Benchmark'
import { FORMATTERS, fmtDate, fmtInt, fmtMs, fmtPct, fmtUsd, leanTo, numbersIn } from '../components/benchmark/format'
import { RUNS, onTemporaryHost, type BenchmarkRun } from '../content/benchmark'
import { TYPED_RUNS, typedRunUrl, type TypedRun } from '../content/benchmark/typed'
import { SERIES, isReference } from '../components/benchmark/Charts'
import { LOGO_DIR, SYSTEMS_WITH_LOGO, VARIANTS, logoOf } from '../components/benchmark/logos'
// A multi-system run with the harness's current shape and synthetic values, never published: written by
// `node scripts/benchmark-fixture.mjs <combined local dry run>/summary.json`.
import fixture from './Benchmark.fixture.json'
// @ts-expect-error: a plain Node script, without types
import { hostLineProblems, problems, withHostLines } from '../../scripts/import-benchmark.mjs'
// @ts-expect-error: a plain Node script, without types
import { problems as typedProblems, trimmed as typedTrimmed } from '../../scripts/import-typed-benchmark.mjs'

const run = fixture as unknown as BenchmarkRun
const LANGS: Lang[] = ['pt', 'en']

// The typed-object set is off unless a test passes it: its numbers come from another file than the region run's.
function page(lang: Lang, runs: BenchmarkRun[], typed: TypedRun[] = []) {
  const { container } = render(
    <MemoryRouter>
      <I18n.Provider value={{ lang, t: dicts[lang], setLang: () => {} }}>
        <Benchmark runs={runs} typed={typed} />
      </I18n.Provider>
    </MemoryRouter>,
  )
  return container
}

/** Every text node, apart: table cells side by side must not merge into one number. */
function spacedText(root: Node) {
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT)
  const parts: string[] = []
  while (walker.nextNode()) parts.push(walker.currentNode.textContent ?? '')
  // "Mem0" is a name, not a number.
  return parts.join(' ').replace(/mem0/gi, 'mem')
}

/** The page's text without identifiers (versions, commit, folder names), which are not measurements. */
function measuredText(container: HTMLElement) {
  const copy = container.cloneNode(true) as HTMLElement
  copy.querySelectorAll('[data-id]').forEach((el) => el.remove())
  return spacedText(copy)
}

/** Whether an element names a system: the logo's text alternative, or the name in text for a line with no logo. */
const names = (el: Element, name: string) => [...el.querySelectorAll('img')].some((img) => img.alt === name) || (el.textContent ?? '').includes(name)

/** The system keys of a results file, every metric together. */
const systemsOf = (run: BenchmarkRun) => new Set(Object.values(run.metrics).flatMap((metric) => (metric?.results ?? []).map((r: { system: string }) => r.system)))

/**
 * Every way a number of the results file may appear: each formatter, each language, and the dates. The
 * one number the page derives is a rank, a position among the file's own systems: 1 up to their count.
 */
function allowedNumbers(data: unknown): Set<string> {
  const out = new Set<string>()
  const systems = systemsOf(data as BenchmarkRun).size
  for (let rank = 1; rank <= systems; rank++) out.add(String(rank))
  const addValue = (v: number) => {
    for (const lang of LANGS) for (const f of FORMATTERS) numbersIn(f(lang, v)).forEach((n) => out.add(n))
  }
  const walk = (v: unknown, key: string) => {
    numbersIn(key).forEach((n) => out.add(n))
    if (typeof v === 'number') addValue(v)
    else if (typeof v === 'string' && !/hash/.test(key)) {
      numbersIn(v).forEach((n) => {
        out.add(n)
        addValue(Number(n.replace(/,/g, '')))
      })
      if (/^\d{4}-\d{2}-\d{2}/.test(v)) for (const lang of LANGS) numbersIn(fmtDate(lang, v)).forEach((n) => out.add(n))
    } else if (Array.isArray(v)) v.forEach((x) => walk(x, key))
    else if (v && typeof v === 'object') for (const [k, x] of Object.entries(v)) walk(x, k)
  }
  walk(data, '')
  return out
}

describe('/benchmark', () => {
  it.each(LANGS)('shows no number at all before the first run (%s)', (lang) => {
    const container = page(lang, [])
    expect(container.textContent).toContain(dicts[lang].benchmark.pendingH2)
    expect(numbersIn(spacedText(container))).toEqual([])
  })

  it.each(LANGS)('shows only numbers that are in the results file (%s)', (lang) => {
    const container = page(lang, [run])
    const allowed = allowedNumbers(fixture)
    const shown = numbersIn(measuredText(container))
    expect(shown.length).toBeGreaterThan(50)
    expect(shown.filter((n) => !allowed.has(n))).toEqual([])
  })

  // The published runs pass the same check, notes included: a note that quotes a number must quote it as
  // the page formats a value of its own file.
  it.each(LANGS.flatMap((lang) => RUNS.map((r) => [lang, r.run_id] as const)))('published run shows only its own numbers (%s, %s)', (lang, id) => {
    const published = RUNS.find((r) => r.run_id === id)!
    const container = page(lang, [published])
    const shown = numbersIn(measuredText(container))
    const allowed = allowedNumbers(published)
    expect(shown.filter((n) => !allowed.has(n))).toEqual([])
    for (const note of Object.values(published.site_notes?.[lang] ?? {})) expect(container.textContent).toContain(note)
  })

  it('fails the check when a number is typed by hand', () => {
    const allowed = allowedNumbers(fixture)
    expect(allowed.has('97,3')).toBe(false)
    expect(numbersIn('Latência de 97,3 ms').filter((n) => !allowed.has(n))).toEqual(['97,3'])
  })

  it('gives every chart a table', () => {
    const container = page('pt', [run])
    const figures = [...container.querySelectorAll('figure')]
    expect(figures.length).toBeGreaterThanOrEqual(11)
    for (const figure of figures) expect(figure.querySelector('details table')).not.toBeNull()
  })

  it.each(LANGS)('shows history navigation and the ingestion acknowledgement only for runs that measured them (%s)', (lang) => {
    const b = dicts[lang].benchmark
    const withThem = page(lang, [run]).textContent ?? ''
    for (const text of [b.charts.history.h, b.charts.ingest.h, b.operations.open, b.operations.add_infer]) expect(withThem).toContain(text)
    const { history: _h, ingest: _i, ...older } = run.metrics
    const without = page(lang, [{ ...run, metrics: older }]).textContent ?? ''
    for (const text of [b.charts.history.h, b.charts.ingest.h, b.methodOps.slice(0, 30)]) expect(without).not.toContain(text)
  })

  it.each(LANGS)('says Niadra ran at the production cap only when its lines miss a rate the others ran (%s)', (lang) => {
    const cap = dicts[lang].benchmark.methodOpsCap.split('{')[0]
    // The fixture keeps Niadra at 10 per second only; the published combine has Niadra at both rates.
    expect(page(lang, [run]).textContent).toContain(cap)
    expect(page(lang, [RUNS[0]]).textContent).not.toContain(cap)
  })

  it('writes a line the harness could not measure as not measured, with no number', () => {
    const none = { median: null, min: null, max: null, runs: [null] }
    const lines = run.metrics.history!.results.map((r) =>
      r.system === 'mem0_oss' && r.operation === 'open' ? { ...r, p50: none, p95: none, p99: none, max: none, sent: 0, skipped: 'no memory returned by any search' } : r,
    )
    const container = page('en', [{ ...run, metrics: { ...run.metrics, history: { unit: 'ms', results: lines } } }])
    expect(container.textContent).toContain(dicts.en.benchmark.notMeasured)
    const allowed = allowedNumbers(fixture)
    expect(numbersIn(measuredText(container)).filter((n) => !allowed.has(n))).toEqual([])
  })

  it('names and colors every system of the run in both languages', () => {
    for (const system of systemsOf(run)) {
      for (const lang of LANGS) expect(dicts[lang].benchmark.systems[system], `${lang} name of ${system}`).toBeTruthy()
      if (!isReference(system)) expect(SERIES[system], `color of ${system}`).toBeTruthy()
    }
  })

  it('ranks every bar chart best first, and marks only the first row as the best value', () => {
    const container = page('en', [run])
    const group = container.querySelector('#latencia ol')!
    const rows = [...group.querySelectorAll('li')]
    const values = rows.map((li) => Number(li.lastElementChild!.textContent!.replace(/[^\d.]/g, '')))
    expect(values).toEqual([...values].sort((x, y) => x - y))
    expect(rows[0].textContent).toContain(dicts.en.benchmark.best)
    expect(rows.slice(1).some((li) => li.textContent!.includes(dicts.en.benchmark.best))).toBe(false)
    const accuracy = [...container.querySelector('#acerto ol')!.querySelectorAll('li')].filter((li) => li.firstElementChild!.textContent)
    const shares = accuracy.map((li) => Number(li.lastElementChild!.textContent!.replace(/[^\d.]/g, '')))
    expect(shares).toEqual([...shares].sort((x, y) => y - x))
  })

  it('covers every measured system in the highlights and in the cost and accuracy scatter', () => {
    const container = page('en', [run])
    const measured = [...new Set(run.metrics.accuracy!.results.map((r) => r.system))].filter((s) => !isReference(s))
    const tiles = [...container.querySelectorAll('h3')].map((h) => h.closest('div.flex-col')).filter(Boolean) as HTMLElement[]
    const tokens = tiles.find((t) => t.textContent!.includes(dicts.en.benchmark.tiles.tokens))!
    const scatter = container.querySelector('#custo-e-acerto')!
    for (const system of measured) {
      expect(names(tokens, dicts.en.benchmark.systems[system]), system).toBe(true)
      expect(names(scatter, dicts.en.benchmark.systems[system]), system).toBe(true)
    }
  })

  it('reads the latency highlight at the public address, in every run', () => {
    const tile = (r: BenchmarkRun) =>
      ([...page('en', [r]).querySelectorAll('h3')].find((h) => h.textContent!.startsWith('Context latency, p95'))!.closest('div.flex-col') as HTMLElement).textContent!
    expect(tile(run)).toContain(dicts.en.benchmark.paths.edge)
    const older = RUNS.find((r) => !onTemporaryHost(r))
    if (older) expect(tile(older)).toContain(dicts.en.benchmark.paths.edge)
  })

  it.each(LANGS)('keeps network jargon out of the visible copy (%s)', (lang) => {
    const text = page(lang, [run]).textContent ?? ''
    expect(text).not.toMatch(/VPC|TLS/)
  })

  it.each(LANGS)('shows no score for a system without a verification mechanism (%s)', (lang) => {
    const b = dicts[lang].benchmark
    const privacy = page(lang, [run]).querySelector('#privacidade')!
    const none = run.metrics.privacy!.results.filter((r) => r.verification === 'none')
    expect(none.length).toBeGreaterThan(0)
    const rows = [...privacy.querySelectorAll('ol li')]
    for (const r of none) {
      const row = rows.find((li) => names(li, b.systems[r.system]))!
      expect(row.textContent).toContain(b.noMechanism)
    }
  })

  it.each(LANGS)('says how the run was measured: a temporary host since 26/09, the cluster before (%s)', (lang) => {
    const b = dicts[lang].benchmark
    const text = page(lang, [run]).textContent ?? ''
    expect(onTemporaryHost(run)).toBe(true)
    expect(text).toContain(b.methodHost[1])
    expect(text).toContain(b.methodCombined.replace('{refRun}', run.dataset.references!.run_id))
    expect(text).toContain(b.methodBacking)
    expect(text).not.toContain(b.method[3])
    for (const source of run.combined_from!) expect(text).toContain(`${source.started_at.slice(0, 10)}-${source.run_id.slice(-6)}`)
    const older = RUNS.find((r) => !onTemporaryHost(r))
    if (older) {
      const before = page(lang, [older]).textContent ?? ''
      expect(before).toContain(b.method[3])
      expect(before).not.toContain(b.methodHost[1])
    }
  })

  it.each(LANGS)('shows values without a source per thousand answers when the run has them (%s)', (lang) => {
    const b = dicts[lang].benchmark
    expect(page(lang, [run]).querySelector('#sem-lastro')?.textContent).toContain(b.charts.backing.h)
    const { backing: _b, ...older } = run.metrics
    expect(page(lang, [{ ...run, metrics: older }]).querySelector('#sem-lastro')).toBeNull()
  })

  it.each(LANGS)('puts each company logo before the system name, with the name as its text alternative (%s)', (lang) => {
    const b = dicts[lang].benchmark
    const container = page(lang, [run])
    const logos = [...container.querySelectorAll('img')]
    for (const system of systemsOf(run)) {
      const shown = logos.filter((img) => img.alt === b.systems[system])
      if (logoOf(system)) {
        expect(shown.length, system).toBeGreaterThan(0)
        for (const img of shown) expect(img.getAttribute('src')).toBe(logoOf(system))
      } else expect(shown, system).toEqual([])
    }
    // Every image on the page is a logo with a name.
    for (const img of logos) expect(img.alt).toBeTruthy()
    // The references and a system with no logo on file keep a neutral icon and their name in text.
    const accuracy = container.querySelector('#acerto')!
    for (const system of ['full_history', 'no_memory']) expect(accuracy.textContent).toContain(b.systems[system])
  })

  it.each(LANGS)('tells the variants of one company apart by a visible tag (%s)', (lang) => {
    const b = dicts[lang].benchmark
    const accuracy = page(lang, [run]).querySelector('#acerto')!
    const variants = Object.keys(VARIANTS).filter((system) => systemsOf(run).has(system))
    expect(variants.length).toBeGreaterThan(0)
    for (const system of variants) {
      const row = [...accuracy.querySelectorAll('ol li')].find((li) => names(li, b.systems[system]))!
      expect(row.textContent).toContain(b.systems[VARIANTS[system].base])
      expect(row.textContent).toContain(VARIANTS[system].tag)
    }
  })

  it('keeps a small logo file for every compared system, and a color', () => {
    for (const system of SYSTEMS_WITH_LOGO) {
      const file = `public${logoOf(system)}`
      expect(existsSync(file), file).toBe(true)
      expect(statSync(file).size, file).toBeLessThan(6 * 1024)
      expect(logoOf(system)!.startsWith(`${LOGO_DIR}/`)).toBe(true)
      expect(SERIES[system], `color of ${system}`).toBeTruthy()
      for (const lang of LANGS) expect(dicts[lang].benchmark.systems[system], `${lang} name of ${system}`).toBeTruthy()
    }
    // Every system the page can name has a logo, apart from the references and ai-memory, which left the benchmark.
    const without = Object.keys(dicts.en.benchmark.systems).filter((system) => !isReference(system) && !logoOf(system))
    expect(without.sort()).toEqual(['ai_memory', 'ai_memory_llm'])
  })

  it.each(LANGS)('says the names and logos belong to their owners (%s)', (lang) => {
    expect(page(lang, [run]).textContent).toContain(dicts[lang].benchmark.trademarks)
  })

  it('prints the note written for a metric', () => {
    expect(page('en', [run]).textContent).toContain('Test note about freshness.')
  })

  it.each(LANGS)('copy has no em dash or en dash (%s)', (lang) => {
    const text = JSON.stringify(dicts[lang].benchmark) + (page(lang, [run]).textContent ?? '')
    expect(text).not.toMatch(/[\u2013\u2014]/)
  })

  it.each(LANGS)('leads with accuracy on the same cases for every system, the whole-dataset line labeled and unranked (%s)', (lang) => {
    const b = dicts[lang].benchmark
    const container = page(lang, [run])
    const card = container.querySelector('#acerto')!
    // The headline is the first chart on the page.
    expect(container.querySelector('figure')!.id).toBe('acerto')
    const shared = run.metrics.accuracy_shared!.results
    const niadra = shared.find((r) => r.system === 'niadra')!
    const rows = [...card.querySelector('ol')!.querySelectorAll('li')]
    const niadraRows = rows.filter((li) => names(li, b.systems.niadra))
    // One ranked line on the shared cases, and the whole-dataset line with no rank.
    expect(niadraRows).toHaveLength(2)
    // Niadra's share rounds down, never in its favor.
    expect(niadraRows[0].textContent).toContain(fmtPct(lang, niadra.judge.median!, 'down'))
    expect(niadraRows[0].firstElementChild!.textContent).not.toBe('')
    const own = run.metrics.accuracy!.results.find((r) => r.system === 'niadra')!
    expect(niadraRows[1].textContent).toContain(fmtPct(lang, own.judge.median!, 'down'))
    expect(niadraRows[1].textContent).toContain(b.fullLine.replace('{n}', String(own.cases.median)))
    expect(niadraRows[1].firstElementChild!.textContent).toBe('')
    // No line and no table cell carries a repetition count: that number lives in the method section only.
    const reps = new RegExp(`${b.repOne.replace('{n}', '\\d+')}|${b.repMany.replace('{n}', '\\d+')}`)
    expect(card.textContent).not.toMatch(reps)
    expect(card.querySelector('table')!.textContent).not.toContain(b.th.reps)
  })

  it.each(LANGS)('says how many repetitions each system ran once, in the method section (%s)', (lang) => {
    const b = dicts[lang].benchmark
    const container = page(lang, [run])
    const niadraReps = (run.per_system!.niadra.repetitions === 1 ? b.repOne : b.repMany).replace('{n}', String(run.per_system!.niadra.repetitions))
    const method = [...container.querySelectorAll('section')].find((s) => s.textContent!.includes(b.methodH2))!
    expect(method.textContent).toContain(niadraReps)
    const elsewhere = (container.textContent ?? '').split(niadraReps).length - 1
    expect(elsewhere).toBe(method.textContent!.split(niadraReps).length - 1)
  })

  it.each(LANGS)('states the cases and caveats of every system, one not measured included (%s)', (lang) => {
    const b = dicts[lang].benchmark
    const container = page(lang, [run])
    const rows = [...container.querySelector('#sistemas')!.querySelectorAll('li')]
    for (const [system, source] of Object.entries(run.per_system!)) {
      const row = rows.find((li) => names(li, b.systems[system]))!
      expect(row, system).toBeTruthy()
      expect(row.textContent).not.toMatch(new RegExp(b.repMany.replace('{n}', '\\d+')))
      expect(row.textContent).toContain(b.casesText.replace('{n}', fmtInt(lang, source.cases)))
      expect(row.querySelector('p')!.textContent).toBe(run.site_caveats![lang]![system])
    }
    const redis = rows.find((li) => names(li, b.systems.redis_agent_memory))!
    expect(redis.textContent).toContain(b.notMeasuredRow)
  })

  it('ranks cost by one method when Niadra has its measured model spend, and lists its price beside it', () => {
    const b = dicts.en.benchmark
    const rows = [...page('en', [run]).querySelector('#custo ol')!.querySelectorAll('li')]
    const ranked = rows.filter((li) => li.firstElementChild!.textContent)
    // Every ranked line is a models_only line: as many as the file has.
    expect(ranked).toHaveLength(run.metrics.cost!.results.filter((r) => r.variant === 'models_only').length)
    expect(ranked.some((li) => names(li, b.systems.niadra))).toBe(true)
    const price = rows.find((li) => li.textContent!.includes(b.variants.price_low))!
    expect(price.firstElementChild!.textContent).toBe('')
  })

  it('shows a latency line whose reads all failed as that count, unranked, never as a time', () => {
    const b = dicts.en.benchmark
    const none = { median: null, min: null, max: null, runs: [null, null, null] }
    const lines = run.metrics.latency!.results.map((r) => (r.system === 'graphiti' ? { ...r, p50: none, p95: none, p99: none, max: none, errors: r.sent } : r))
    const container = page('en', [{ ...run, metrics: { ...run.metrics, latency: { unit: 'ms', results: lines } } }])
    const row = [...container.querySelector('#latencia ol')!.querySelectorAll('li')].find((li) => names(li, b.systems.graphiti))!
    const sent = lines.find((r) => r.system === 'graphiti')!.sent
    expect(row.textContent).toContain(b.allErrors.replace('{n}', fmtInt('en', sent)).replace('{total}', fmtInt('en', sent)))
    expect(row.lastElementChild!.textContent).toBe(b.noNumber)
    expect(row.firstElementChild!.textContent).toBe('')
  })

  it('the importer publishes only runs measured in the region', () => {
    expect(problems(run)).toEqual([])
    expect(problems({ ...run, environment: { ...run.environment, kind: 'dry-run' } })).toContain('environment is "dry-run", not "region"')
    expect(problems({ ...run, config: { ...run.config, judge_model: null } })).toContain('no judge: a dry run')
  })

  it('puts the run with the newest measurement first, even when two combines share their first source', () => {
    for (let i = 1; i < RUNS.length; i++) expect(RUNS[i - 1].finished_at >= RUNS[i].finished_at).toBe(true)
  })

  it('never rounds in Niadra\'s favor', () => {
    // 164 of 165 is 99.39%: Niadra reads 99.3%, a competitor with the same share reads 99.4%.
    expect(fmtPct('en', 0.9939, 'down')).toBe('99.3%')
    expect(fmtPct('en', 0.9939, 'up')).toBe('99.4%')
    // Lower is better: Niadra's time and cost round up, and an exact value stays exact.
    expect(fmtMs('en', 336.3, 'up')).toBe('337 ms')
    expect(fmtMs('en', 16.1, 'up')).toBe('16.1 ms')
    expect(fmtUsd('en', 0.3646, 'up')).toBe('$0.37')
    expect(leanTo(0.95 * 100, 1, 'down')).toBe(95)
    for (const lang of LANGS) {
      const text = page(lang, [run]).querySelector('#acerto')!.textContent!
      const niadra = run.metrics.accuracy_shared!.results.find((r) => r.system === 'niadra')!
      if (fmtPct(lang, niadra.judge.median!, 'down') !== fmtPct(lang, niadra.judge.median!)) expect(text).not.toContain(fmtPct(lang, niadra.judge.median!))
    }
  })

  it.each(LANGS)('labels Niadra\'s lines from its own cell\'s machine apart from the harness host (%s)', (lang) => {
    const b = dicts[lang].benchmark
    const published = RUNS.find((r) => r.host_lines_from)
    if (!published) return
    const container = page(lang, [published])
    expect(container.querySelector('#latencia')!.textContent).toContain(b.paths.niadraHost)
    expect(container.textContent).toContain(b.methodHostLines.slice(0, 30))
  })

  it.each(LANGS)('writes what the agent gets next to each failure time, and never ranks a broken turn above a served one (%s)', (lang) => {
    const b = dicts[lang].benchmark
    const across = (v: number) => ({ median: v, min: v, max: v, runs: [v] })
    const row = (system: string, ms: number, raised: number, within: number, empty: number) => ({
      system,
      fault: 'http_503',
      memory_stage_p50_ms: across(ms),
      memory_stage_max_ms: across(ms),
      within_budget_rate: across(within),
      raised_rate: across(raised),
      empty_rate: across(empty),
      turn_budget_ms: 1000,
    })
    // A fast error (0,8 ms) and a slow empty context within the budget (168 ms).
    const results = [row('mem0_oss', 0.8, 1, 1, 1), row('niadra', 168, 0, 1, 1)]
    const container = page(lang, [{ ...run, metrics: { ...run.metrics, resilience: { results } } }])
    const rows = [...container.querySelector('#falhas ol')!.querySelectorAll('li')]
    expect(names(rows[0], b.systems.niadra)).toBe(true)
    expect(rows[0].textContent).toContain(b.outcomes.empty)
    expect(rows[1].textContent).toContain(b.outcomes.raised)
    // Neither row carries a rank or the best mark: one served turn has nothing to be ranked against.
    expect(rows.map((li) => li.firstElementChild!.textContent)).toEqual(['', ''])
    expect(container.querySelector('#falhas')!.textContent).not.toContain(b.best)
    expect(container.querySelector('#falhas')!.textContent).toContain(b.turnBudget.split('{')[0])
    expect(container.querySelectorAll('#falhas ol li svg line[stroke-dasharray]').length).toBe(2)
  })

  it('merges only Niadra\'s host lines measured right after its combined run', () => {
    const niadra = run.combined_from!.find((s) => s.systems.includes('niadra'))!
    const line = { ...run.metrics.latency!.results.find((r) => r.system === 'niadra')!, path: 'host' }
    const host = {
      schema: 'niadra-bench.results.v1',
      run_id: 'host-run-abcdef',
      started_at: niadra.finished_at,
      finished_at: niadra.finished_at,
      environment: { kind: 'dry-run' },
      config: { ...run.config, repetitions: 3 },
      dataset: { ...run.dataset, cases: 20 },
      metrics: { latency: { unit: 'ms', results: [line] } },
    }
    expect(hostLineProblems(run, host)).toEqual([])
    const merged = withHostLines(run, host)
    expect(merged.host_lines_from).toHaveLength(1)
    expect(merged.host_lines_from[0].repetitions).toBe(3)
    expect(merged.host_lines_from[0].rates).toEqual({ latency: [line.rate] })
    expect(merged.metrics.latency.results.filter((r: { path: string }) => r.path === 'host' && run.metrics.latency!.results.every((o) => o !== r)).length).toBeGreaterThan(0)
    // A second host run adds only the lines the run lacks, the first one's stay; one with nothing new is refused.
    const later = { ...host, run_id: 'host-run-fedcba', metrics: { latency: { unit: 'ms', results: [{ ...line, p50: { ...line.p50, median: 1 } }, { ...line, rate: 999 }] } } }
    expect(hostLineProblems(merged, later)).toEqual([])
    const twice = withHostLines(merged, later)
    const hostRows = twice.metrics.latency.results.filter((r: { system: string; path: string }) => r.system === 'niadra' && r.path === 'host')
    expect(hostRows.map((r: { rate: number }) => r.rate).sort()).toEqual([line.rate, 999].sort())
    expect(hostRows.find((r: { rate: number }) => r.rate === line.rate).p50).toEqual(line.p50)
    expect(twice.host_lines_from.map((h: { run_id: string; rates: object }) => [h.run_id, h.rates])).toEqual([
      ['host-run-abcdef', { latency: [line.rate] }],
      ['host-run-fedcba', { latency: [999] }],
    ])
    expect(hostLineProblems(merged, host)).toContain('host lines: the combined run already has them')
    expect(hostLineProblems(run, { ...host, config: { ...host.config, hash: 'other' } })).toContain('host lines: another configuration hash')
    expect(hostLineProblems(run, { ...host, metrics: { latency: { unit: 'ms', results: [{ ...line, system: 'mem0_oss' }] } } })).toContain('host lines: a line that is not Niadra on the host path')
    expect(hostLineProblems(run, { ...host, started_at: '2020-01-01T00:00:00+00:00' })).toContain('host lines: not measured right after a Niadra run of the combined run')
  })
})

/**
 * The typed set's numbers, every way the page may write them: the formatters of every value, and a share
 * (0 to 1) also as whole percentage points, the way the run's own results file writes an interval.
 */
function allowedTypedNumbers(data: unknown): Set<string> {
  const out = allowedNumbers(data)
  const walk = (v: unknown) => {
    if (typeof v === 'number') {
      if (v >= 0 && v <= 1) for (const lang of LANGS) for (const lean of ['nearest', 'up', 'down'] as const) numbersIn(fmtInt(lang, v * 100, lean)).forEach((n) => out.add(n))
    } else if (Array.isArray(v)) v.forEach(walk)
    else if (v && typeof v === 'object') Object.values(v).forEach(walk)
  }
  walk(data)
  return out
}

describe('/benchmark, with the state and constraints blocks', () => {
  const typed = TYPED_RUNS[0]

  it('has a published typed-object run, with more than one repetition and a judge', () => {
    expect(typed).toBeTruthy()
    expect(typed.config.repetitions).toBeGreaterThan(1)
    expect(typed.config.judge.model).toBeTruthy()
  })

  it.each(LANGS)('shows the typed set under the headline, with only numbers from the two files (%s)', (lang) => {
    const container = page(lang, [run], [typed])
    const y = dicts[lang].benchmark.typed
    expect(container.textContent).toContain(y.h2)
    // The headline chart still comes first; the typed chart follows it.
    const ids = [...container.querySelectorAll('figure')].map((f) => f.id)
    expect(ids[0]).toBe('acerto')
    expect(ids).toContain('blocos')
    const allowed = new Set([...allowedNumbers(fixture), ...allowedTypedNumbers(typed)])
    expect(numbersIn(measuredText(container)).filter((n) => !allowed.has(n))).toEqual([])
  })

  it.each(LANGS)('shows the typed set on its own before a region run, with no other number (%s)', (lang) => {
    const container = page(lang, [], [typed])
    expect(container.textContent).toContain(dicts[lang].benchmark.pendingH2)
    const shown = numbersIn(measuredText(container))
    expect(shown.length).toBeGreaterThan(20)
    const allowed = allowedTypedNumbers(typed)
    expect(shown.filter((n) => !allowed.has(n))).toEqual([])
  })

  it.each(LANGS)('names both arms, states the interval, never rounds the arm with the blocks up, and links the run files (%s)', (lang) => {
    const y = dicts[lang].benchmark.typed
    const container = page(lang, [], [typed])
    const text = container.textContent ?? ''
    const all = typed.metrics.accuracy.all.all
    expect(text).toContain(y.arms.with)
    expect(text).toContain(y.arms.without)
    expect(text).toContain(fmtPct(lang, all.with.judge.share, 'down'))
    expect(text).toContain(fmtPct(lang, all.without.judge.share, 'up'))
    expect(text).toContain(y.interval.replace('{lo}', fmtInt(lang, all.with.judge.ci95[0] * 100)).replace('{hi}', fmtInt(lang, all.with.judge.ci95[1] * 100)))
    const up = fmtPct(lang, all.with.judge.share, 'up')
    if (up !== fmtPct(lang, all.with.judge.share, 'down')) expect(text).not.toContain(up)
    expect(container.querySelector(`a[href="${typedRunUrl(typed)}"]`)).not.toBeNull()
    // Every category of the file is a row of the chart, in the visitor's language.
    for (const c of Object.keys(typed.metrics.accuracy.all)) expect(text).toContain(y.categories[c] ?? c)
    // The chart has its table, like every other chart.
    expect(container.querySelector('#blocos details table')).not.toBeNull()
  })

  it('the typed importer keeps the figures and drops the per-case lists', () => {
    expect(typedProblems(typed)).toEqual([])
    expect(typedProblems({ ...typed, config: { ...typed.config, repetitions: 1 } })).toContain('a single repetition')
    expect(typedProblems({ ...typed, schema: 'other' })).toContain('not a niadra-bench.typed.v1 file')
    const raw = { ...typed, setup: { types: [] }, settle: [], tokens_without_blocks_v2: { reads: {} }, metrics: { ...typed.metrics, flips: [{ case_id: 'x' }], guard: {}, validity: { cases: 1, valid: 1, invalid: ['x'] }, tokens: { ...typed.metrics.tokens, without_reads: {} } } }
    const kept = typedTrimmed(raw)
    expect(kept.setup).toBeUndefined()
    expect(kept.metrics.flips).toBeUndefined()
    expect(kept.metrics.validity).toEqual({ cases: 1, valid: 1 })
    expect(kept.metrics.tokens.without_reads).toBeUndefined()
    expect(kept.metrics.accuracy).toEqual(typed.metrics.accuracy)
  })
})
