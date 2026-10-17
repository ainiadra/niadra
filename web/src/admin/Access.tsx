/**
 * Acessos: the site's requests per page and per day, counted on the server from Caddy's access log (AI
 * crawlers run no JavaScript, so nothing in the browser could see them). Five classes: people, AIs by
 * name, search engines, scanners and other robots. Assets, probes and our own health watch are not
 * counted.
 *
 * The class tiles are also the chart's and the top pages' selector: one question at a time ("how many
 * people, per day, and on which pages?"), with the other classes a click away.
 */
import { useId, useState } from 'react'
import type { AccessClass } from '../../server/access/classify'
import { fmtDate, fmtInt, fmtPct } from '../console/lib/format'
import { card, h1, h2, small, td, th } from '../console/ui/styles'
import { api, type AccessReport, type Range } from './api'
import { LANGS, type Lang } from '../../server/access/lang'
import { CLASS_HINT, CLASS_LABEL, CLASS_OF, CLASS_ORDER, PURPOSE } from './labels'
import { Empty, ErrorBox, Loading, useLoad } from './ui'

const RANGES: { key: Range; label: string }[] = [
  { key: 'today', label: 'Hoje' },
  { key: '7d', label: '7 dias' },
  { key: '30d', label: '30 dias' },
]

const WEEKDAY = new Intl.DateTimeFormat('pt-BR', { weekday: 'short', timeZone: 'UTC' })
/** 2026-09-28 -> "28/09" and "seg." */
const shortDay = (day: string) => `${day.slice(8, 10)}/${day.slice(5, 7)}`
const weekday = (day: string) => WEEKDAY.format(new Date(`${day}T12:00:00Z`))

export function Access({ onExpired }: { onExpired: () => void }) {
  const [range, setRange] = useState<Range>('7d')
  const [cls, setCls] = useState<AccessClass>('human')
  const { state, busy, reload } = useLoad(() => api.access(range), range, onExpired)
  const report = state.status === 'ok' ? state.data : null

  return (
    <section aria-labelledby="acessos-titulo">
      <header className="mb-5 flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between">
        <div className="min-w-0">
          <h1 id="acessos-titulo" className={h1}>
            Acessos
          </h1>
          <p className={`${small} mt-1 max-w-[70ch]`}>
            Cada requisição ao site, contada no servidor: páginas, blog, llms.txt e as versões em Markdown. Arquivos estáticos, as sondas do cluster e o
            nosso monitor de saúde ficam fora. Dias no horário de Brasília.
            {report?.since && <> Contagem desde {fmtDate(report.since)}.</>}
          </p>
        </div>
        <RangePicker value={range} onChange={setRange} busy={busy && Boolean(report)} />
      </header>

      {state.status === 'loading' && <Loading label="Carregando os acessos…" />}
      {state.status === 'error' && <ErrorBox error={state.error} onRetry={reload} />}
      {report && <ReportView report={report} cls={cls} onClass={setCls} />}
    </section>
  )
}

function RangePicker({ value, onChange, busy }: { value: Range; onChange: (r: Range) => void; busy: boolean }) {
  return (
    <div className="flex items-center gap-3">
      {busy && (
        <span role="status" className={small}>
          Atualizando…
        </span>
      )}
      <div role="group" aria-label="Período" className="inline-flex rounded-lg border border-edge bg-surface p-0.5">
        {RANGES.map((r) => (
          <button
            key={r.key}
            type="button"
            aria-pressed={value === r.key}
            onClick={() => onChange(r.key)}
            className={`h-10 min-w-[4.5rem] rounded-md px-3 text-[0.875rem] font-semibold transition-colors lg:h-8 ${value === r.key ? 'bg-ink text-bg' : 'text-ink2 hover:bg-bg hover:text-ink'}`}
          >
            {r.label}
          </button>
        ))}
      </div>
    </div>
  )
}

function ReportView({ report, cls, onClass }: { report: AccessReport; cls: AccessClass; onClass: (c: AccessClass) => void }) {
  const total = CLASS_ORDER.reduce((n, c) => n + report.totals[c], 0)
  if (!report.since) {
    return (
      <Empty title="A contagem ainda não registrou nenhum acesso.">
        O servidor grava os números uma vez por minuto. Abra qualquer página de niadra.com e volte aqui em um ou dois minutos.
      </Empty>
    )
  }
  return (
    <div className="grid gap-5 [&>*]:min-w-0">
      <Tiles report={report} total={total} cls={cls} onClass={onClass} />
      <DayChart report={report} cls={cls} />
      <Languages report={report} cls={cls} />
      <div className="grid gap-5 lg:grid-cols-2 [&>*]:min-w-0">
        <Countries report={report} cls={cls} />
        <Campaigns report={report} cls={cls} />
      </div>
      <div className="grid gap-5 lg:grid-cols-2 [&>*]:min-w-0">
        <TopPages report={report} cls={cls} />
        <Blog report={report} />
      </div>
      <Crawlers report={report} />
    </div>
  )
}

function Tiles({ report, total, cls, onClass }: { report: AccessReport; total: number; cls: AccessClass; onClass: (c: AccessClass) => void }) {
  return (
    <div role="group" aria-label="Totais por tipo de visitante; escolha um para ver o gráfico e as páginas" className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-5">
      {CLASS_ORDER.map((c) => {
        const on = c === cls
        const n = report.totals[c]
        return (
          <button
            key={c}
            type="button"
            aria-pressed={on}
            onClick={() => onClass(c)}
            className={`flex min-h-11 flex-col items-start rounded-xl border bg-surface p-4 text-left transition-colors ${on ? 'border-ink ring-1 ring-ink' : 'border-line hover:border-edge'} ${c === 'human' ? 'col-span-2 sm:col-span-1' : ''}`}
          >
            <span className="text-[0.875rem] font-semibold text-ink">{CLASS_LABEL[c]}</span>
            <span className="mt-1 font-display text-[1.75rem] font-bold leading-none tracking-[-0.02em] text-ink [font-stretch:92%]">{fmtInt(n)}</span>
            <span className="mt-1.5 text-[0.75rem] leading-4 text-ink2">
              {total ? `${fmtPct(n / total)} das requisições` : 'nenhuma requisição'}
            </span>
            <span className="mt-2 text-[0.75rem] leading-4 text-ink2">
              {c === 'human' && report.visitors > 0 ? visitorsLine(report) : CLASS_HINT[c]}
            </span>
          </button>
        )
      })}
    </div>
  )
}

/**
 * Distinct people: for one day, the count; for a range, the mean over the days that have a count (the
 * visitor count started later than the page count, and a day without it is not a day with nobody).
 */
function visitorsLine(report: AccessReport): string {
  const counted = report.days.filter((d) => d.visitors > 0)
  const plural = (n: number) => `${fmtInt(n)} ${n === 1 ? 'visitante distinto' : 'visitantes distintos'}`
  if (report.days.length === 1) return plural(report.visitors)
  if (counted.length === 1) return `${plural(report.visitors)} no único dia com essa contagem`
  const mean = `Média de ${plural(Math.round(report.visitors / counted.length))} por dia`
  return counted.length < report.days.length ? `${mean}, nos ${counted.length} dias com essa contagem` : mean
}

function DayChart({ report, cls }: { report: AccessReport; cls: AccessClass }) {
  const [active, setActive] = useState<number | null>(null)
  const titleId = useId()
  const days = report.days
  const values = days.map((d) => d.counts[cls])
  const max = Math.max(...values, 0)
  // A round top for the axis: 1, 2 or 5 times a power of ten.
  const top = max === 0 ? 1 : niceCeil(max)
  const label = `Requisições de ${CLASS_OF[cls]} por dia`
  const labelEvery = days.length > 14 ? 5 : 1
  const shown = active ?? days.length - 1

  return (
    <section aria-labelledby={titleId} className={`${card} p-4 sm:p-5`}>
      <div className="mb-4 flex flex-wrap items-end justify-between gap-2">
        <div>
          <h2 id={titleId} className={h2}>
            {label}
          </h2>
          <p className={`${small} mt-0.5`}>
            {days.length === 1 ? 'Hoje, até agora. Escolha 7 ou 30 dias para ver a série.' : `${fmtDate(report.from)} a ${fmtDate(report.to)}. Passe o mouse ou o foco numa barra para ver o dia.`}
          </p>
        </div>
      </div>

      {days.length > 1 && (
        <div className="relative">
          <DayDetail report={report} index={shown} cls={cls} />
          <div className="relative mt-3 h-44 border-b border-line sm:h-52">
            {[1, 0.5].map((f) => (
              <div key={f} aria-hidden="true" className="absolute inset-x-0 border-t border-line2" style={{ bottom: `${f * 100}%` }}>
                <span className="absolute -top-2.5 right-0 bg-surface pl-1.5 font-mono text-[0.6875rem] text-ink2">{fmtInt(top * f)}</span>
              </div>
            ))}
            <div className="absolute inset-0 right-10 flex items-end gap-[2px]" onMouseLeave={() => setActive(null)}>
              {days.map((d, i) => {
                const v = values[i]!
                return (
                  <button
                    key={d.day}
                    type="button"
                    aria-label={`${weekday(d.day)} ${fmtDate(d.day)}: ${fmtInt(v)} requisições de ${CLASS_OF[cls]}`}
                    onMouseEnter={() => setActive(i)}
                    onFocus={() => setActive(i)}
                    onClick={() => setActive(i)}
                    className="group flex h-full min-w-0 flex-1 items-end justify-center rounded-sm focus-visible:outline-offset-1"
                  >
                    <span
                      className={`block w-full max-w-12 rounded-t-[4px] transition-colors ${i === shown ? 'bg-ink' : 'bg-accent group-hover:bg-ink'}`}
                      style={{ height: v ? `max(2px, ${(v / top) * 100}%)` : '0' }}
                    />
                  </button>
                )
              })}
            </div>
          </div>
          <div aria-hidden="true" className="mr-10 flex gap-[2px] pt-1.5">
            {days.map((d, i) => (
              <span key={d.day} className="min-w-0 flex-1 text-center font-mono text-[0.6875rem] text-ink2">
                {(days.length - 1 - i) % labelEvery === 0 ? shortDay(d.day).slice(0, 2) : ''}
              </span>
            ))}
          </div>
        </div>
      )}
      {days.length === 1 && <DayDetail report={report} index={0} cls={cls} />}

      <details className="mt-4 border-t border-line2 pt-3">
        <summary className="inline-flex min-h-11 cursor-pointer items-center text-[0.875rem] font-semibold text-ink lg:min-h-8">Ver a tabela por dia</summary>
        <div className="mt-2 overflow-x-auto">
          <table className="w-full min-w-[34rem] text-[0.8125rem]">
            <thead>
              <tr>
                <th scope="col" className={th}>Dia</th>
                {CLASS_ORDER.map((c) => (
                  <th key={c} scope="col" className={`${th} text-right`}>{CLASS_LABEL[c]}</th>
                ))}
                <th scope="col" className={`${th} text-right`}>Visitantes</th>
              </tr>
            </thead>
            <tbody>
              {[...days].reverse().map((d) => (
                <tr key={d.day}>
                  <th scope="row" className={`${td} text-left font-mono text-[0.75rem] font-normal`}>{weekday(d.day)} {fmtDate(d.day)}</th>
                  {CLASS_ORDER.map((c) => (
                    <td key={c} className={`${td} text-right tabular-nums`}>{fmtInt(d.counts[c])}</td>
                  ))}
                  <td className={`${td} text-right tabular-nums`}>{fmtInt(d.visitors)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </details>
    </section>
  )
}

/** The chosen day in words: the selected class first, then the others. */
function DayDetail({ report, index, cls }: { report: AccessReport; index: number; cls: AccessClass }) {
  const d = report.days[index]
  if (!d) return null
  return (
    <div aria-live="polite" className="flex flex-wrap items-baseline gap-x-4 gap-y-1 rounded-lg bg-bg px-3 py-2 text-[0.8125rem]">
      <span className="font-mono text-[0.75rem] text-ink2">
        {weekday(d.day)} {fmtDate(d.day)}
      </span>
      {[cls, ...CLASS_ORDER.filter((c) => c !== cls)].map((c) => (
        <span key={c} className={c === cls ? 'font-semibold text-ink' : 'text-ink2'}>
          {CLASS_LABEL[c]} <span className="tabular-nums">{fmtInt(d.counts[c])}</span>
        </span>
      ))}
    </div>
  )
}

export function niceCeil(n: number): number {
  const p = 10 ** Math.floor(Math.log10(n))
  for (const m of [1, 2, 5, 10]) if (m * p >= n) return m * p
  return 10 * p
}

const LANG_LABEL: Record<Lang, string> = { pt: 'Português', en: 'Inglês (/en)', other: 'Sem idioma' }
const LANG_HINT: Record<Lang, string> = {
  pt: 'Páginas sem prefixo de idioma.',
  en: 'Páginas sob /en, inclusive as versões em Markdown.',
  other: 'llms.txt, sitemap e outros arquivos que não são de uma língua.',
}

/** Which language version was asked for, for the chosen class: read from the path, nothing new is collected. */
function Languages({ report, cls }: { report: AccessReport; cls: AccessClass }) {
  const titleId = useId()
  const rows = LANGS.map((l) => ({ lang: l, hits: report.languages?.[l]?.[cls] ?? 0 }))
  const total = rows.reduce((n, r) => n + r.hits, 0)
  return (
    <section aria-labelledby={titleId} className={`${card} p-4 sm:p-5`}>
      <h2 id={titleId} className={h2}>
        Idioma pedido por {CLASS_OF[cls]}
      </h2>
      <p className={`${small} mt-0.5`}>Português ou inglês, pelo endereço da página, no período.</p>
      {total === 0 ? (
        <p className={`${small} mt-4`}>Nenhuma requisição de {CLASS_OF[cls]} no período.</p>
      ) : (
        <ul className="mt-4 grid gap-3 sm:grid-cols-3">
          {rows.map((r) => (
            <li key={r.lang} className="min-w-0 rounded-lg border border-line p-3">
              <div className="flex items-baseline justify-between gap-3">
                <span className="text-[0.875rem] font-semibold text-ink">{LANG_LABEL[r.lang]}</span>
                <span className="shrink-0 text-[0.8125rem] font-semibold tabular-nums text-ink">{fmtInt(r.hits)}</span>
              </div>
              <div aria-hidden="true" className="mt-2 h-1.5 rounded-full bg-line2">
                <div className="h-1.5 rounded-full bg-accent" style={{ width: `${(r.hits / total) * 100}%` }} />
              </div>
              <p className="mt-1.5 text-[0.75rem] leading-4 text-ink2">
                {fmtPct(r.hits / total)} · {LANG_HINT[r.lang]}
              </p>
            </li>
          ))}
        </ul>
      )}
    </section>
  )
}

const COUNTRY_NAMES = new Intl.DisplayNames(['pt-BR'], { type: 'region' })
/** "AE" -> "Emirados Árabes Unidos"; a code the browser does not know stays a code. */
function countryName(code: string): string {
  if (code === 'unknown') return 'Sem país'
  try {
    return COUNTRY_NAMES.of(code) ?? code
  } catch {
    return code
  }
}

/**
 * Which countries the requests of the chosen class came from: the country of the masked address, read
 * when the request is counted and never stored per request (server/access/geo.ts). "Sem país" is what
 * the table could not place, or every request counted before the table existed.
 */
function Countries({ report, cls }: { report: AccessReport; cls: AccessClass }) {
  const titleId = useId()
  const rows = (report.countries ?? []).map((r) => ({ country: r.country, hits: r.counts[cls] })).filter((r) => r.hits > 0).sort((a, b) => b.hits - a.hits)
  const total = rows.reduce((n, r) => n + r.hits, 0)
  const max = rows[0]?.hits ?? 0
  return (
    <section aria-labelledby={titleId} className={`${card} p-4 sm:p-5`}>
      <h2 id={titleId} className={h2}>
        País de {CLASS_OF[cls]}
      </h2>
      <p className={`${small} mt-0.5`}>Pelo endereço já mascarado, no momento da contagem. Nenhum endereço é guardado.</p>
      {total === 0 ? (
        <p className={`${small} mt-4`}>Nenhuma requisição de {CLASS_OF[cls]} com país no período.</p>
      ) : (
        <ol className="mt-4 grid gap-2">
          {rows.slice(0, 12).map((r) => (
            <li key={r.country} className="grid grid-cols-[minmax(0,1fr)_auto] items-center gap-x-3 gap-y-1">
              <span className="truncate text-[0.875rem] text-ink">
                {countryName(r.country)} <span className="font-mono text-[0.75rem] text-ink2">{r.country === 'unknown' ? '' : r.country}</span>
              </span>
              <span className="text-[0.8125rem] font-semibold tabular-nums text-ink">
                {fmtInt(r.hits)} <span className="font-normal text-ink2">({fmtPct(r.hits / total)})</span>
              </span>
              <div aria-hidden="true" className="col-span-2 h-1.5 rounded-full bg-line2">
                <div className="h-1.5 rounded-full bg-accent" style={{ width: `${(r.hits / max) * 100}%` }} />
              </div>
            </li>
          ))}
        </ol>
      )}
    </section>
  )
}

/**
 * The links that carried a campaign (utm_source, utm_medium, utm_campaign), for the chosen class: it is
 * how a LinkedIn post, an ad or a message with its own link shows up here, visit by visit, before anyone
 * sends the form.
 */
function Campaigns({ report, cls }: { report: AccessReport; cls: AccessClass }) {
  const titleId = useId()
  const rows = (report.campaigns ?? []).map((r) => ({ ...r, hits: r.counts[cls] })).filter((r) => r.hits > 0).sort((a, b) => b.hits - a.hits)
  return (
    <section aria-labelledby={titleId} className={`${card} p-4 sm:p-5`}>
      <h2 id={titleId} className={h2}>
        Campanhas (utm) de {CLASS_OF[cls]}
      </h2>
      <p className={`${small} mt-0.5`}>Requisições cujo link trazia utm_source, por origem, meio e campanha.</p>
      {rows.length === 0 ? (
        <p className={`${small} mt-4`}>Nenhum link com utm no período. Um link com ?utm_source=linkedin&amp;utm_campaign=benchmark aparece aqui.</p>
      ) : (
        <table className="mt-4 w-full text-[0.8125rem]">
          <caption className="sr-only">Campanhas, da maior para a menor</caption>
          <thead>
            <tr>
              <th scope="col" className={th}>Origem</th>
              <th scope="col" className={th}>Meio</th>
              <th scope="col" className={th}>Campanha</th>
              <th scope="col" className={`${th} text-right`}>Req.</th>
            </tr>
          </thead>
          <tbody>
            {rows.slice(0, 20).map((r) => (
              <tr key={`${r.source}\t${r.medium}\t${r.campaign}`}>
                <td className={`${td} break-words font-mono text-[0.75rem]`}>{r.source}</td>
                <td className={`${td} break-words font-mono text-[0.75rem] text-ink2`}>{r.medium || '·'}</td>
                <td className={`${td} break-words font-mono text-[0.75rem] text-ink2`}>{r.campaign || '·'}</td>
                <td className={`${td} text-right font-semibold tabular-nums`}>{fmtInt(r.hits)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  )
}

function TopPages({ report, cls }: { report: AccessReport; cls: AccessClass }) {
  const rows = report.top[cls]
  const max = rows[0]?.hits ?? 0
  const titleId = useId()
  return (
    <section aria-labelledby={titleId} className={`${card} p-4 sm:p-5`}>
      <h2 id={titleId} className={h2}>
        Páginas mais pedidas por {CLASS_OF[cls]}
      </h2>
      <p className={`${small} mt-0.5`}>As 15 primeiras no período, com o blog incluído.</p>
      {rows.length === 0 ? (
        <p className={`${small} mt-4`}>Nenhuma requisição de {CLASS_OF[cls]} no período.</p>
      ) : (
        <ol className="mt-4 grid gap-2.5">
          {rows.map((r) => (
            <li key={r.path} className="min-w-0">
              <div className="flex items-baseline justify-between gap-3">
                <span className="min-w-0 break-all font-mono text-[0.75rem] leading-5 text-ink">{r.path}</span>
                <span className="shrink-0 text-[0.8125rem] font-semibold tabular-nums text-ink">{fmtInt(r.hits)}</span>
              </div>
              <div aria-hidden="true" className="mt-1 h-1 rounded-full bg-line2">
                <div className="h-1 rounded-full bg-accent" style={{ width: `${(r.hits / max) * 100}%` }} />
              </div>
            </li>
          ))}
        </ol>
      )}
    </section>
  )
}

function Blog({ report }: { report: AccessReport }) {
  const titleId = useId()
  return (
    <section aria-labelledby={titleId} className={`${card} p-4 sm:p-5`}>
      <h2 id={titleId} className={h2}>
        Blog
      </h2>
      <p className={`${small} mt-0.5`}>Cada post e o índice, em português e em inglês, com as versões em Markdown que as IAs leem.</p>
      {report.blog.length === 0 ? (
        <p className={`${small} mt-4`}>Nenhuma leitura do blog no período.</p>
      ) : (
        <>
        <ul className="mt-3 grid sm:hidden">
          {report.blog.map((b) => (
            <li key={b.path} className="border-b border-line2 py-2.5 last:border-b-0">
              <p className="break-all font-mono text-[0.75rem] leading-5 text-ink">{b.path}</p>
              <p className="mt-0.5 text-[0.8125rem] text-ink2">
                Pessoas <span className="font-semibold tabular-nums text-ink">{fmtInt(b.counts.human)}</span> · IAs{' '}
                <span className="font-semibold tabular-nums text-ink">{fmtInt(b.counts.ai)}</span> · Buscadores{' '}
                <span className="font-semibold tabular-nums text-ink">{fmtInt(b.counts.search)}</span>
              </p>
            </li>
          ))}
        </ul>
        <div className="mt-3 hidden overflow-x-auto sm:block">
          <table className="w-full min-w-[26rem] text-[0.8125rem]">
            <thead>
              <tr>
                <th scope="col" className={th}>Página</th>
                <th scope="col" className={`${th} text-right`}>Pessoas</th>
                <th scope="col" className={`${th} text-right`}>IAs</th>
                <th scope="col" className={`${th} text-right`}>Buscadores</th>
              </tr>
            </thead>
            <tbody>
              {report.blog.map((b) => (
                <tr key={b.path}>
                  <th scope="row" className={`${td} break-all text-left font-mono text-[0.75rem] font-normal leading-5`}>{b.path}</th>
                  <td className={`${td} text-right tabular-nums`}>{fmtInt(b.counts.human)}</td>
                  <td className={`${td} text-right tabular-nums`}>{fmtInt(b.counts.ai)}</td>
                  <td className={`${td} text-right tabular-nums`}>{fmtInt(b.counts.search)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        </>
      )}
    </section>
  )
}

function Crawlers({ report }: { report: AccessReport }) {
  const ai = report.crawlers.filter((c) => c.cls === 'ai')
  const search = report.crawlers.filter((c) => c.cls === 'search')
  const titleId = useId()
  return (
    <section aria-labelledby={titleId} className={`${card} p-4 sm:p-5`}>
      <h2 id={titleId} className={h2}>
        IAs por nome
      </h2>
      <p className={`${small} mt-0.5 max-w-[80ch]`}>
        Treino de modelo coleta para treinar; busca com IA indexa para responder com fonte; pedido de uma pessoa é a leitura que alguém pediu no chat, o sinal
        mais forte de que uma IA usa o site para responder.
      </p>
      {ai.length === 0 ? (
        <p className={`${small} mt-4`}>Nenhum robô de IA no período.</p>
      ) : (
        <CrawlerTable rows={ai} withPurpose caption="Robôs de IA" />
      )}
      <h3 className="mt-6 text-[0.9rem] font-semibold text-ink">Buscadores por nome</h3>
      {search.length === 0 ? <p className={`${small} mt-2`}>Nenhum buscador no período.</p> : <CrawlerTable rows={search} caption="Buscadores" />}
    </section>
  )
}

function CrawlerTable({ rows, withPurpose = false, caption }: { rows: AccessReport['crawlers']; withPurpose?: boolean; caption: string }) {
  return (
    <>
    <ul aria-label={caption} className="mt-3 grid sm:hidden">
      {rows.map((c) => (
        <li key={c.name} className="border-b border-line2 py-2.5 last:border-b-0">
          <div className="flex items-baseline justify-between gap-3">
            <span className="font-mono text-[0.75rem] font-semibold text-ink">{c.name}</span>
            <span className="text-[0.8125rem] font-semibold tabular-nums text-ink">{fmtInt(c.hits)}</span>
          </div>
          <p className="mt-0.5 text-[0.8125rem] text-ink2">
            {[c.company, withPurpose && c.purpose ? PURPOSE[c.purpose] : null, `${fmtInt(c.pages)} ${c.pages === 1 ? 'página' : 'páginas'}`].filter(Boolean).join(' · ')}
          </p>
          <p className="mt-0.5 break-all text-[0.8125rem] text-ink2">
            Mais lida: <span className="font-mono text-[0.75rem] text-ink">{c.topPath}</span>
          </p>
        </li>
      ))}
    </ul>
    <div className="mt-3 hidden overflow-x-auto sm:block">
      <table className="w-full min-w-[40rem] text-[0.8125rem]">
        <caption className="sr-only">{caption}</caption>
        <thead>
          <tr>
            <th scope="col" className={th}>Nome</th>
            <th scope="col" className={th}>Empresa</th>
            {withPurpose && <th scope="col" className={th}>Para quê</th>}
            <th scope="col" className={`${th} text-right`}>Requisições</th>
            <th scope="col" className={`${th} text-right`}>Páginas</th>
            <th scope="col" className={th}>Página mais lida</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((c) => (
            <tr key={c.name}>
              <th scope="row" className={`${td} text-left font-mono text-[0.75rem] font-semibold`}>{c.name}</th>
              <td className={td}>{c.company}</td>
              {withPurpose && <td className={td}>{c.purpose ? PURPOSE[c.purpose] : ''}</td>}
              <td className={`${td} text-right tabular-nums`}>{fmtInt(c.hits)}</td>
              <td className={`${td} text-right tabular-nums`}>{fmtInt(c.pages)}</td>
              <td className={`${td} break-all font-mono text-[0.75rem]`}>{c.topPath}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
    </>
  )
}
