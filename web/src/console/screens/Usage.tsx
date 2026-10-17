import { useId, useState, type ReactNode } from 'react'
import { Link } from 'react-router-dom'
import type { BudgetReport, ContextLatencyReport, ContextLatencyWindow, ContextTrimOut, ContextUseBucket, ContextUseDetail, ContextUseReport, PromptCacheReport, RateOut, RetrievalWeightsOut, SlotChannelUse } from '../api/types'
import { fmtDate, fmtDateTime, fmtInt, fmtPct, utcDayEndsAt, utcDayInput, utcDayToISO } from '../lib/format'
import { budgetPurpose, category, channel, itemKind, metric, notMeasuredReason, signal, SIGNALS, VIEWS } from '../lib/labels'
import { usePaged, useResource } from '../lib/useResource'
import { byWeek, weekLabel, type WeekBucket } from '../lib/weeks'
import { paths } from '../paths'
import { SCREENS } from '../lib/screens'
import { useSpace } from '../session'
import { Chip, PageHeader, SourceName, useSources } from '../ui/bits'
import { Select, useAction } from '../ui/forms'
import { useTitle } from '../ui/layout'
import { Empty, ErrorState, LoadMore, Loading } from '../ui/States'
import { btnSecondary, card, h2, input, label, mono, small, td, th } from '../ui/styles'
import { Term } from '../ui/Term'

const PERIODS = [
  { days: 7, label: '7 dias' },
  { days: 30, label: '30 dias' },
  { days: 90, label: '90 dias' },
] as const

type Group = 'source' | 'vendor' | 'channel' | 'week'
type CacheGroup = 'source' | 'vendor'

export function Segmented<T extends string | number>({ label: text, value, options, onChange }: { label: string; value: T; options: { value: T; label: string }[]; onChange: (v: T) => void }) {
  return (
    <div role="radiogroup" aria-label={text} className="inline-flex flex-wrap rounded-lg border border-edge bg-surface p-0.5">
      {options.map((o) => (
        <label
          key={String(o.value)}
          className={`inline-flex min-h-10 cursor-pointer items-center rounded-md px-3 text-[0.875rem] font-semibold has-[:focus-visible]:outline has-[:focus-visible]:outline-2 has-[:focus-visible]:outline-ink lg:min-h-8 ${
            value === o.value ? 'bg-ink text-bg' : 'text-ink2 hover:text-ink'
          }`}
        >
          <input type="radio" className="sr-only" name={text} checked={value === o.value} onChange={() => onChange(o.value)} />
          {o.label}
        </label>
      ))}
    </div>
  )
}

/** The last twelve UTC months, newest first, as `YYYY-MM`. */
function lastMonths(count = 12): { value: string; label: string }[] {
  const now = new Date()
  return Array.from({ length: count }, (_, i) => {
    const d = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth() - i, 1))
    const value = d.toISOString().slice(0, 7)
    return { value, label: `${value.slice(5, 7)}/${value.slice(0, 4)}` }
  })
}

/** The month per space of the tenant, as CSV: conversations and tasks, every metric and the AI cost. */
function MonthlyStatement() {
  const { control } = useSpace()
  const months = lastMonths()
  const [month, setMonth] = useState(months[0]!.value)
  const download = useAction<Blob>()
  const save = () =>
    download.run(
      () => control.usageStatementCsv(month),
      (blob) => {
        const url = URL.createObjectURL(blob)
        const a = document.createElement('a')
        a.href = url
        a.download = `niadra-uso-${month}.csv`
        a.click()
        setTimeout(() => URL.revokeObjectURL(url), 1000)
      },
    )
  return (
    <div className="mt-4 flex flex-wrap items-end gap-3">
      <div className="w-44">
        <Select label="Extrato do mês" value={month} onChange={setMonth} options={months} />
      </div>
      <button type="button" className={btnSecondary} disabled={download.busy} onClick={save}>
        {download.busy ? 'Preparando…' : 'Baixar o extrato em CSV'}
      </button>
      <p className={`${small} basis-full max-w-[80ch]`}>
        Um mês em UTC, uma linha por espaço da sua empresa: conversas e tarefas, que são a unidade de cobrança, cada métrica e o custo de IA. O extrato diz o que foi usado, não o preço.
      </p>
      {Boolean(download.error) && <ErrorState error={download.error} compact />}
    </div>
  )
}

export function Usage() {
  const { space, control, cell, hasRole } = useSpace()
  useTitle('Uso e aproveitamento', space.environment === 'sandbox')
  const [days, setDays] = useState<number>(30)
  const [group, setGroup] = useState<Group>('source')
  // The server counts its days in UTC: the period is built from UTC days, so the current one is never cut.
  const fromDay = utcDayInput(days - 1)
  const toDay = utcDayInput(0)
  const since = utcDayToISO(fromDay)!
  const until = utcDayToISO(toDay, true)!

  const bySource = useResource((signal) => cell.usageBySource(since, until, { signal }), [since, until, cell], hasRole('analysis'))
  const usage = useResource((signal) => control.usage(since, until, { signal }), [since, until, control])
  // A week is the sum of its days: the server groups by day, and the rates come from the weekly sums.
  const groupBy = group === 'channel' ? ['channel'] : group === 'vendor' ? ['vendor'] : group === 'week' ? ['day'] : ['source_id']
  const use = useResource((signal) => cell.contextUse({ since: fromDay, until: toDay, group_by: groupBy }, { signal }), [fromDay, toDay, group, cell])

  return (
    <>
      <PageHeader
        screen="usage"
        description={`De ${fmtDate(fromDay)} a ${fmtDate(toDay)}, em dias UTC, como o servidor conta: o dia de hoje termina às ${utcDayEndsAt()} no seu horário. ${SCREENS.usage.lead}`}
        actions={<Segmented label="Período" value={days} options={PERIODS.map((p) => ({ value: p.days, label: p.label }))} onChange={setDays} />}
      />
      {hasRole('vendor') && !hasRole('analysis') && <p className={`${small} -mt-3 mb-5`}>Você vê só as fontes do seu fornecedor.</p>}

      <section aria-labelledby="h-by-source" data-tour="usage.sources" className="mb-10">
        <h2 id="h-by-source" className={`${h2} mb-1`}>
          Uso por agente
        </h2>
        <p className={`${small} mb-3`}>Conversas e tarefas são a unidade de cobrança. Eventos de sistema entram na contagem de eventos e não no preço.</p>
        {!hasRole('analysis') && <p className={small}>O uso por agente exige o papel Análise.</p>}
        {bySource.status === 'loading' && <Loading label="Somando o uso por agente…" />}
        {bySource.status === 'error' && <ErrorState error={bySource.error} onRetry={bySource.reload} />}
        {bySource.status === 'success' && bySource.data && <BySourceTable data={bySource.data} />}
      </section>

      <PromptCache fromDay={fromDay} toDay={toDay} />

      <section aria-labelledby="h-usage" data-tour="usage.quota" className="mb-10">
        <h2 id="h-usage" className={`${h2} mb-1`}>
          Cota e custo do espaço
        </h2>
        <p className={`${small} mb-3 max-w-[80ch]`}>
          O total do espaço por métrica, com a cota da empresa e o custo para a Niadra. A região do espaço manda o uso de cada hora ao plano de controle a cada 15 minutos, então a
          hora corrente pode aparecer incompleta.
        </p>
        {usage.status === 'loading' && <Loading label="Somando o uso do período…" />}
        {usage.status === 'error' && <ErrorState error={usage.error} onRetry={usage.reload} />}
        {usage.status === 'success' && usage.data && <UsageTable data={usage.data} spaceId={space.space_id} />}
        {(hasRole('analysis') || hasRole('security')) && <MonthlyStatement />}
      </section>

      <Budget />

      <ReadLatency />

      <section aria-labelledby="h-use">
        <div className="mb-3 flex flex-wrap items-end justify-between gap-3">
          <div>
            <h2 id="h-use" data-tour="usage.context" className={h2}>
              Aproveitamento do contexto
            </h2>
            <p className={`${small} mt-0.5 max-w-[80ch]`}>
              Depois de cada conversa, a Niadra confere se o agente usou o que recebeu, se perguntou de novo o que já sabia, se contradisse a memória e se transferiu sem o destino ler. O recontato fica ao lado: é o número que mostra o efeito no negócio.
            </p>
          </div>
          <Segmented
            label="Agrupar por"
            value={group}
            options={[
              { value: 'source', label: 'Por agente' },
              { value: 'vendor', label: 'Por fornecedor' },
              { value: 'channel', label: 'Por canal' },
              { value: 'week', label: 'Por semana' },
            ]}
            onChange={setGroup}
          />
        </div>
        {use.status === 'loading' && <Loading label="Carregando a medição do período…" />}
        {use.status === 'error' && <ErrorState error={use.error} onRetry={use.reload} />}
        {use.status === 'success' &&
          use.data &&
          (use.data.buckets.length === 0 ? (
            <Empty title="Nenhuma conversa medida no período.">A medição começa quando um agente pede contexto numa conversa ou tarefa.</Empty>
          ) : (
            <ContextUseTable buckets={group === 'week' ? byWeek(use.data.buckets, fromDay, toDay) : use.data.buckets} group={group} />
          ))}
        {use.status === 'success' && use.data && use.data.buckets.length > 0 && <Trims trims={use.data.trims ?? []} />}
        {use.status === 'success' && use.data && ((use.data.slot_channels?.length ?? 0) > 0 || use.data.retrieval_weights) && (
          <SlotChannels channels={use.data.slot_channels ?? []} weights={use.data.retrieval_weights ?? null} />
        )}
        {use.status === 'success' && use.data?.review && use.data.review.length > 0 && <ReviewBeside report={use.data} />}
        <p className={`${small} mt-3 max-w-[80ch]`}>
          A medição fala do uso da memória, não da qualidade do atendimento, e não dá nota ao fornecedor. Cada taxa vem com o intervalo de 95% e o número de casos; taxa com poucos casos tem intervalo largo.
        </p>
      </section>

      <ConversationDetail />
    </>
  )
}

const MAIN = ['session_billable', 'context_read', 'history_call', 'event_ingested'] as const

/** The periods of a tenant quota (contracts/control.py QuotaIn.period), in words. */
const PERIOD_NAMES: Record<string, string> = { month: 'mês', day: 'dia' }

const usdSmall = new Intl.NumberFormat('pt-BR', { style: 'currency', currency: 'USD', minimumFractionDigits: 2, maximumFractionDigits: 4 })

/** A saving in millionths of a US dollar; a negative one means the cache writes cost more than the reads saved. */
const savings = (micros: number) => usdSmall.format(micros / 1_000_000)

/**
 * The agents' prompt cache: what each model call reported to the SDK, per agent or
 * vendor, with the provider's cache hit rate and the saving estimated from a table of public prices.
 */
function PromptCache({ fromDay, toDay }: { fromDay: string; toDay: string }) {
  const { cell } = useSpace()
  const [group, setGroup] = useState<CacheGroup>('source')
  const report = useResource(
    (signal) => cell.promptCache({ since: fromDay, until: toDay, group_by: group === 'vendor' ? ['vendor'] : ['source_id'] }, { signal }),
    [fromDay, toDay, group, cell],
  )
  return (
    <section aria-labelledby="h-cache" className="mb-10">
      <div className="mb-3 flex flex-wrap items-end justify-between gap-3">
        <div>
          <h2 id="h-cache" className={h2}>
            Cache de prompt do provedor
          </h2>
          <p className={`${small} mt-0.5 max-w-[80ch]`}>
            Quanto do prompt de cada agente o provedor de IA leu do próprio cache, pelo uso que cada chamada ao modelo informou ao SDK. O provedor cobra menos pelo que lê do cache; a economia
            abaixo é uma estimativa pela tabela de preços, e o valor cobrado de fato está na fatura do provedor.
          </p>
        </div>
        <Segmented
          label="Agrupar o cache por"
          value={group}
          options={[
            { value: 'source', label: 'Por agente' },
            { value: 'vendor', label: 'Por fornecedor' },
          ]}
          onChange={setGroup}
        />
      </div>
      {report.status === 'loading' && <Loading label="Somando o que as chamadas ao modelo informaram…" />}
      {report.status === 'error' && <ErrorState error={report.error} onRetry={report.reload} />}
      {report.status === 'success' &&
        report.data &&
        (report.data.buckets.length === 0 ? (
          <Empty title="Nenhuma chamada ao modelo informou uso no período.">
            O uso chega com o turno do agente: o wrap() dos SDKs lê o que o provedor devolve em cada chamada, e quem não usa o wrap() passa a resposta do provedor no turno do agente.
          </Empty>
        ) : (
          <PromptCacheTable report={report.data} group={group} />
        ))}
    </section>
  )
}

function PromptCacheTable({ report, group }: { report: PromptCacheReport; group: CacheGroup }) {
  const num = `${td} text-right tabular-nums whitespace-nowrap`
  const unpriced = report.buckets.reduce((n, b) => n + b.unpriced_calls, 0)
  return (
    <>
      <div className={`${card} overflow-x-auto`}>
        <table className="w-full min-w-[48rem] border-collapse">
          <caption className="sr-only">Cache de prompt do provedor {group === 'source' ? 'por agente' : 'por fornecedor'}</caption>
          <thead>
            <tr>
              <th scope="col" className={th}>
                {group === 'source' ? 'Agente' : 'Fornecedor'}
              </th>
              <th scope="col" className={`${th} text-right`}>
                Chamadas
              </th>
              <th scope="col" className={`${th} text-right`}>
                Tokens de entrada
              </th>
              <th scope="col" className={`${th} text-right`}>
                Lidos do cache
              </th>
              <th scope="col" className={`${th} text-right`}>
                Acerto do cache
              </th>
              <th scope="col" className={`${th} text-right`}>
                Economia estimada
              </th>
            </tr>
          </thead>
          <tbody>
            {report.buckets.map((b, i) => (
              <tr key={b.source_id ?? b.vendor ?? String(i)}>
                <th scope="row" className={`${td} text-left font-normal`}>
                  {group === 'source' ? <SourceName id={b.source_id} withVendor /> : <span className="font-semibold">{b.vendor || 'Sem fornecedor'}</span>}
                </th>
                <td className={num}>{fmtInt(b.calls)}</td>
                <td className={num}>{fmtInt(b.prompt_tokens)}</td>
                <td className={num}>
                  {fmtInt(b.cached_tokens)}
                  {b.cache_write_tokens > 0 && <span className="block text-[0.75rem] text-ink2">{fmtInt(b.cache_write_tokens)} gravados no cache</span>}
                </td>
                <td className={num}>{b.hit_rate === null ? <span className="text-ink2">sem tokens</span> : <span className="font-semibold text-ink">{fmtPct(b.hit_rate)}</span>}</td>
                <td className={num}>
                  {b.estimated_savings_micros === null ? (
                    <span className="text-ink2">sem preço na tabela</span>
                  ) : (
                    <span className="font-semibold text-ink">{savings(b.estimated_savings_micros)}</span>
                  )}
                  {b.estimated_savings_micros !== null && b.unpriced_calls > 0 && (
                    <span className="block text-[0.75rem] text-ink2">sem {fmtInt(b.unpriced_calls)} chamadas sem preço</span>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className={`${small} mt-3 max-w-[80ch]`}>
        Estimativa: tokens lidos do cache pelo preço de entrada menos o preço de cache, descontado o adicional de gravação, pelo preço do modelo de cada chamada. Tabela de preços públicos
        de {fmtDate(report.prices_as_of)}; o espaço pode definir os próprios preços nas configurações.
        {unpriced > 0 && ` ${fmtInt(unpriced)} chamadas de modelos sem preço na tabela ficam fora da estimativa.`}
      </p>
      {report.prices.length > 0 && (
        <details className="mt-2">
          <summary className="inline-flex min-h-11 cursor-pointer items-center text-[0.8125rem] font-semibold text-ink lg:min-h-8">Preços usados na estimativa ({report.prices.length})</summary>
          <ul className="mt-2 grid gap-1 border-t border-line2 pt-2">
            {report.prices.map((p) => (
              <li key={`${p.provider}/${p.model}`} className={`${mono} text-ink2`}>
                {p.provider}/{p.model} · entrada US$ {p.input} · cache US$ {p.cached_input} · gravação US$ {p.cache_write} por milhão de tokens ·{' '}
                {p.table === 'space' ? 'preço do espaço' : 'tabela da Niadra'}
              </li>
            ))}
          </ul>
        </details>
      )}
    </>
  )
}

function BySourceTable({ data }: { data: import('../api/types').CellUsageOut }) {
  const rows = new Map<string, Record<string, number>>()
  for (const i of data.items) {
    const key = i.source_id ?? ''
    const r = rows.get(key) ?? {}
    r[i.metric] = (r[i.metric] ?? 0) + i.quantity
    rows.set(key, r)
  }
  if (rows.size === 0) return <Empty title="Nenhum uso registrado no período." />
  const total = (m: string) => [...rows.values()].reduce((n, r) => n + (r[m] ?? 0), 0)
  const num = `${td} text-right tabular-nums`
  return (
    <div className={`${card} overflow-x-auto`}>
      <table className="w-full min-w-[46rem] border-collapse">
        <caption className="sr-only">Uso por agente</caption>
        <thead>
          <tr>
            <th scope="col" className={th}>Agente</th>
            {MAIN.map((m) => (
              <th key={m} scope="col" className={`${th} text-right`}>
                {metric(m)}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {[...rows.entries()].map(([id, r]) => (
            <tr key={id || 'space'}>
              <th scope="row" className={`${td} text-left font-normal`}>
                {id ? <SourceName id={id} withVendor /> : <span className="text-ink2">Trabalho do próprio espaço</span>}
              </th>
              {MAIN.map((m) => (
                <td key={m} className={num}>
                  {fmtInt(r[m] ?? 0)}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
        <tfoot>
          <tr className="font-semibold">
            <th scope="row" className="px-3 py-2.5 text-left">
              Total
            </th>
            {MAIN.map((m) => (
              <td key={m} className="px-3 py-2.5 text-right tabular-nums">
                {fmtInt(total(m))}
              </td>
            ))}
          </tr>
        </tfoot>
      </table>
    </div>
  )
}

function UsageTable({ data, spaceId }: { data: import('../api/types').UsageOut; spaceId: string }) {
  const mine = data.items.filter((i) => i.space_id === spaceId)
  const byMetric = new Map<string, { quantity: number; cost: number }>()
  for (const i of mine) {
    const cur = byMetric.get(i.metric) ?? { quantity: 0, cost: 0 }
    byMetric.set(i.metric, { quantity: cur.quantity + i.quantity, cost: cur.cost + i.cost_micros })
  }
  if (byMetric.size === 0) return <Empty title="Nenhum uso registrado no período." />
  const quota = new Map(data.quotas.map((q) => [q.metric, q]))
  const usd = new Intl.NumberFormat('pt-BR', { style: 'currency', currency: 'USD', maximumFractionDigits: 2 })
  return (
    <div className={`${card} overflow-x-auto`}>
      <table className="w-full min-w-[36rem] border-collapse">
        <caption className="sr-only">Uso do espaço por métrica</caption>
        <thead>
          <tr>
            <th scope="col" className={th}>Métrica</th>
            <th scope="col" className={`${th} text-right`}>Quantidade</th>
            <th scope="col" className={`${th} text-right`}>Cota da empresa</th>
            <th scope="col" className={`${th} text-right`}>Custo para a Niadra</th>
          </tr>
        </thead>
        <tbody>
          {[...byMetric.entries()].map(([m, v]) => {
            const q = quota.get(m)
            return (
              <tr key={m}>
                <th scope="row" className={`${td} text-left font-normal`}>
                  {metric(m)}
                  <span className={`${mono} block text-ink2`}>{m}</span>
                </th>
                <td className={`${td} text-right tabular-nums`}>{fmtInt(v.quantity)}</td>
                <td className={`${td} text-right tabular-nums text-ink2`}>{q ? `${fmtInt(q.limit)} por ${PERIOD_NAMES[q.period] ?? q.period}` : 'sem cota'}</td>
                <td className={`${td} text-right tabular-nums text-ink2`}>{v.cost ? usd.format(v.cost / 1_000_000) : 'não se aplica'}</td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}

/**
 * The daily ceiling of AI spend (app/metering.py budget): per purpose, what the space spent today
 * with the AI provider, the ceiling and who set it. Past the ceiling, that purpose's work waits for the next
 * UTC day: memory arrives later, and nothing is lost.
 */
function Budget() {
  const { cell, hasRole } = useSpace()
  const allowed = hasRole('analysis')
  const report = useResource((signal) => cell.budget({ signal }), [cell], allowed)
  return (
    <section aria-labelledby="h-budget" data-tour="usage.budget" className="mb-10">
      <h2 id="h-budget" className={`${h2} mb-1`}>
        Teto diário de gasto com IA
      </h2>
      <p className={`${small} mb-3 max-w-[80ch]`}>
        O que o espaço gastou hoje com o provedor de IA, por finalidade, contra o teto do dia. Passado o teto, a finalidade só volta a chamar o provedor no dia seguinte (UTC). Na
        extração, as conversas esperam na fila e a memória chega mais tarde, sem perder nada. A primeira espera da extração no dia manda o evento usage.budget_reached aos endpoints
        que o assinam.
      </p>
      {!allowed && <p className={small}>O teto de gasto exige o papel Análise.</p>}
      {report.status === 'loading' && <Loading label="Lendo o gasto de hoje…" />}
      {report.status === 'error' && <ErrorState error={report.error} onRetry={report.reload} />}
      {report.status === 'success' &&
        report.data &&
        (report.data.items.length === 0 ? (
          <Empty title="Nenhuma finalidade com teto de gasto neste espaço.">O teto vale para o trabalho que chama o provedor de IA, como a extração da memória.</Empty>
        ) : (
          <BudgetTable report={report.data} />
        ))}
    </section>
  )
}

const WINDOW_NAMES: Record<ContextLatencyWindow['window'], string> = { '24h': 'Últimas 24 horas', '7d': 'Últimos 7 dias' }

const fmtMs = (ms: number | null | undefined) => (ms === null || ms === undefined ? 'sem dado' : `${ms.toLocaleString('pt-BR', { maximumFractionDigits: 1 })} ms`)

/**
 * The server time of the space's context reads: what the read process measured by space,
 * from the cell's Prometheus through the admin API, refreshed at most once a minute. Never the network.
 */
function ReadLatency() {
  const { cell, hasRole } = useSpace()
  const allowed = hasRole('analysis')
  const report = useResource((signal) => cell.contextLatency({ signal }), [cell], allowed)
  return (
    <section aria-labelledby="h-latency" className="mb-10">
      <h2 id="h-latency" className={`${h2} mb-1`}>
        Tempo de leitura do contexto
      </h2>
      <p className={`${small} mb-3 max-w-[80ch]`}>
        Quanto o servidor levou para responder cada leitura de contexto deste espaço: tempo do servidor, sem a rede. O tempo que o agente vê soma a ida e a volta até a região do
        espaço.
      </p>
      {!allowed && <p className={small}>O tempo de leitura exige o papel Análise.</p>}
      {report.status === 'loading' && <Loading label="Lendo o tempo das leituras…" />}
      {report.status === 'error' && <ErrorState error={report.error} onRetry={report.reload} />}
      {report.status === 'success' && report.data && <LatencyCards report={report.data} />}
    </section>
  )
}

function LatencyCards({ report }: { report: ContextLatencyReport }) {
  if (!report.available)
    return <Empty title="As métricas da região não responderam agora.">O tempo de leitura volta a aparecer quando elas responderem; as leituras seguem normalmente.</Empty>
  const slo = fmtPct(report.slo).replace(',0%', '%')
  return (
    <>
      <div className="grid gap-4 md:grid-cols-2">
        {report.windows.map((w) => (
          <article key={w.window} aria-labelledby={`latency-${w.window}`} className={`${card} p-4`}>
            <h3 id={`latency-${w.window}`} className="text-[0.875rem] font-semibold text-ink">
              {WINDOW_NAMES[w.window]}
            </h3>
            {w.reads === 0 ? (
              <p className={`${small} mt-2`}>Sem leituras no período.</p>
            ) : (
              <dl className="mt-3 grid grid-cols-2 gap-x-4 gap-y-3">
                <div>
                  <dt className={small}>Mediana (p50)</dt>
                  <dd className="text-[1.25rem] font-semibold tabular-nums text-ink">{fmtMs(w.p50_ms)}</dd>
                </div>
                <div>
                  <dt className={small}>p95</dt>
                  <dd className="text-[1.25rem] font-semibold tabular-nums text-ink">{fmtMs(w.p95_ms)}</dd>
                </div>
                <div>
                  <dt className={small}>Abaixo de {report.target_ms} ms</dt>
                  <dd className="tabular-nums text-ink">
                    {w.under_target === null || w.under_target === undefined ? 'sem dado' : fmtPct(w.under_target)}
                    <span className={`${small} block`}>meta publicada: {report.target_ms} ms</span>
                  </dd>
                </div>
                <div>
                  <dt className={small}>Objetivo de {slo}</dt>
                  <dd className="text-ink">
                    {w.within_slo === null || w.within_slo === undefined ? (
                      <span className="text-ink2">sem dado</span>
                    ) : w.within_slo ? (
                      <Chip tone="ok">Dentro</Chip>
                    ) : (
                      <Chip tone="danger">Fora</Chip>
                    )}
                    {w.error_budget_used !== null && w.error_budget_used !== undefined && (
                      <span className={`${small} block tabular-nums`}>{fmtPct(w.error_budget_used)} da margem de erro usada</span>
                    )}
                  </dd>
                </div>
                <div>
                  <dt className={small}>Leituras</dt>
                  <dd className="tabular-nums text-ink">{fmtInt(w.reads)}</dd>
                </div>
                <div>
                  <dt className={small}>Servidas degradadas</dt>
                  <dd className="tabular-nums text-ink">{fmtPct(w.degraded ?? 0)}</dd>
                </div>
              </dl>
            )}
          </article>
        ))}
      </div>
      <p className={`${small} mt-3 max-w-[80ch]`}>
        Tempo do servidor, sem a rede. O objetivo é que {slo} das leituras fiquem abaixo de {report.target_ms} ms; a margem de erro é a parte que pode passar disso. Degradada é a
        leitura que saiu com a memória atrasada, sem o contexto guardado ou com um bloco de fora. Atualizado em {fmtDateTime(report.as_of)}.
      </p>
    </>
  )
}

/** The instant a UTC day (YYYY-MM-DD) ends, as ISO. */
function nextUtcMidnight(day: string): string {
  const [y, m, d] = day.split('-').map(Number)
  return new Date(Date.UTC(y!, m! - 1, d! + 1)).toISOString()
}

function BudgetTable({ report }: { report: BudgetReport }) {
  const num = `${td} text-right tabular-nums whitespace-nowrap`
  const day = report.items[0]?.day
  return (
    <>
      <div className={`${card} overflow-x-auto`}>
        <table className="w-full min-w-[44rem] border-collapse">
          <caption className="sr-only">Gasto de hoje com IA contra o teto diário, por finalidade</caption>
          <thead>
            <tr>
              <th scope="col" className={th}>
                Finalidade
              </th>
              <th scope="col" className={`${th} text-right`}>
                Gasto hoje
              </th>
              <th scope="col" className={`${th} text-right`}>
                Teto do dia
              </th>
              <th scope="col" className={th}>
                Quem definiu o teto
              </th>
              <th scope="col" className={th}>
                Situação
              </th>
            </tr>
          </thead>
          <tbody>
            {report.items.map((b) => (
              <tr key={b.purpose}>
                <th scope="row" className={`${td} text-left font-normal`}>
                  {budgetPurpose(b.purpose)}
                  <span className={`${mono} block text-ink2`}>{b.purpose}</span>
                </th>
                <td className={num}>
                  <span className="font-semibold text-ink">{savings(b.spent_micros)}</span>
                  {b.cap_micros ? <span className="block text-[0.75rem] text-ink2">{fmtPct(Math.min(b.spent_micros / b.cap_micros, 1))} do teto</span> : null}
                </td>
                <td className={num}>{b.cap_micros === null ? <span className="text-ink2">sem teto</span> : savings(b.cap_micros)}</td>
                <td className={td}>{b.cap_micros === null ? <span className="text-ink2">não se aplica</span> : b.from_contract ? 'O contrato' : 'O padrão da Niadra, sem teto no contrato'}</td>
                <td className={td}>
                  {b.reached ? (
                    <>
                      <Chip tone="danger">Teto atingido</Chip>
                      <span className="mt-1 block text-[0.8125rem] text-ink">O trabalho espera e volta em {fmtDateTime(b.resumes_at)}.</span>
                    </>
                  ) : (
                    <Chip tone="ok">Dentro do teto</Chip>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {day && (
        <p className={`${small} mt-3`}>
          Dia {fmtDate(day)} em UTC. O gasto volta a zero à meia-noite UTC, {fmtDateTime(nextUtcMidnight(day))} no seu horário.
        </p>
      )}
    </>
  )
}

/** A rate from the server with its 95% interval, or "não medido": never a zero standing for "no data". */
function Rate({ rate, fallback }: { rate: RateOut | null; fallback?: { num: number; den: number } }) {
  if (rate) {
    return (
      <>
        <span className="font-semibold text-ink">{fmtPct(rate.value)}</span>
        <span className="block whitespace-nowrap text-[0.75rem] text-ink2">
          {fmtPct(rate.low)} a {fmtPct(rate.high)} · n {fmtInt(rate.n)}
        </span>
      </>
    )
  }
  if (fallback && fallback.den > 0) {
    return (
      <>
        <span className="font-semibold text-ink">{fmtPct(fallback.num / fallback.den)}</span>
        <span className="block whitespace-nowrap text-[0.75rem] text-ink2">n {fmtInt(fallback.den)}</span>
      </>
    )
  }
  return <span className="text-ink2">não medido</span>
}

type Row = { key: string; label: ReactNode; bucket: ContextUseBucket }

const GROUP_NAMES: Record<Group, string> = { source: 'agente', vendor: 'fornecedor', channel: 'canal', week: 'semana' }
const GROUP_HEADS: Record<Group, string> = { source: 'Agente', vendor: 'Fornecedor', channel: 'Canal', week: 'Semana' }

function ContextUseTable({ buckets, group }: { buckets: ContextUseBucket[]; group: Group }) {
  const rows: Row[] = buckets.map((b, i) => ({
    key: group === 'week' ? (b as WeekBucket).week_start : (b.source_id ?? b.vendor ?? b.channel ?? String(i)),
    label:
      group === 'week' ? (
        <span className="font-semibold whitespace-nowrap">{weekLabel(b as WeekBucket)}</span>
      ) : group === 'channel' ? (
        <span className="font-semibold">{channel(b.channel)}</span>
      ) : group === 'vendor' ? (
        <span className="font-semibold">{b.vendor || 'Sem fornecedor'}</span>
      ) : (
        <SourceName id={b.source_id} withVendor />
      ),
    bucket: b,
  }))
  const num = `${td} text-right tabular-nums whitespace-nowrap`
  const head = (text: ReactNode, extra = '') => (
    <th scope="col" className={`${th} text-right ${extra}`}>
      {text}
    </th>
  )
  const groupName = GROUP_NAMES[group]
  return (
    <div className={`${card} overflow-x-auto`}>
      <table className="w-full min-w-[72rem] border-collapse">
        <caption className="sr-only">Aproveitamento do contexto por {groupName}, com o recontato ao lado</caption>
        <thead>
          <tr>
            <th scope="col" className={th}>
              {GROUP_HEADS[group]}
            </th>
            {head('Sessões')}
            {head('Entregas')}
            {head(<Term k="aproveitamento">Aproveitamento</Term>)}
            {head(<Term k="repeticao">Repetição</Term>)}
            {head(<Term k="contradicao">Contradições</Term>)}
            {head(<Term k="transferenciaSemLeitura">Transferências sem leitura</Term>)}
            {head(<Term k="cobertura">Cobertura</Term>)}
            {head('Contexto enxugado')}
            {head(<Term k="recontato">Recontato</Term>, 'border-l border-line')}
          </tr>
        </thead>
        <tbody>
          {rows.map(({ key, label: text, bucket: b }) => {
            const measured = b.deliveries - b.not_measured
            return (
              <tr key={key}>
                <th scope="row" className={`${td} text-left font-normal`}>
                  {text}
                </th>
                <td className={num}>{fmtInt(b.sessions)}</td>
                <td className={num}>
                  {fmtInt(b.deliveries)}
                  {b.not_measured > 0 && <span className="block text-[0.75rem] text-ink2">{fmtInt(b.not_measured)} não medidas</span>}
                </td>
                <td className={num}>
                  <Rate rate={b.usage_rate} fallback={{ num: b.deliveries_used, den: measured }} />
                </td>
                <td className={num}>
                  <Rate rate={b.repetition_rate} fallback={{ num: b.repeated, den: b.questions }} />
                </td>
                <td className={num}>
                  {measured > 0 ? fmtInt(b.contradicted) : <span className="text-ink2">não medido</span>}
                  {b.selection_misses > 0 && <span className="block text-[0.75rem] text-ink2">+{fmtInt(b.selection_misses)} por falta no contexto</span>}
                </td>
                <td className={num}>
                  {b.transfers > 0 ? (
                    <>
                      {fmtInt(b.transfers_unread)} de {fmtInt(b.transfers)}
                    </>
                  ) : (
                    <span className="text-ink2">nenhuma transferência</span>
                  )}
                </td>
                <td className={`${td} text-right text-[0.8125rem] text-ink2`}>
                  <span className="block whitespace-nowrap">{fmtInt(b.no_context)} sem contexto</span>
                  <span className="block whitespace-nowrap">{fmtInt(b.late_deliveries)} com contexto tardio</span>
                </td>
                <td className={num}>
                  {b.tokens_saved > 0 ? (
                    <>
                      <span className="font-semibold text-ink">{fmtInt(b.tokens_saved)} tokens</span>
                      <span className="block text-[0.75rem] text-ink2">em {fmtInt(b.trimmed_deliveries)} entregas</span>
                    </>
                  ) : (
                    <span className="text-ink2">nada enxugado</span>
                  )}
                </td>
                <td className={`${num} border-l border-line`}>
                  <Rate rate={b.recontact_rate} fallback={{ num: b.recontacts, den: b.sessions }} />
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}

const INACTIVE: Record<string, string> = {
  disabled: 'Desligado nas configurações do espaço: todos os agentes recebem o contexto inteiro.',
  opt_out: 'Esta fonte está fora do enxugamento nas configurações do espaço e recebe o contexto inteiro.',
  critical: 'Fonte crítica: recebe sempre o contexto inteiro.',
}

const viewLabel = (v: string) => (v.startsWith('task:') ? `tarefa ${v.slice(5)}` : (VIEWS.find((x) => x.value === v)?.label ?? v))

/**
 * What each agent stopped receiving because it never used it: the item units of its trim, with the
 * deliveries behind each one. The saving itself is the "Contexto enxugado" column.
 */
function Trims({ trims }: { trims: ContextTrimOut[] }) {
  return (
    <div className="mt-6">
      <h3 className="text-[0.9375rem] font-semibold text-ink">O que cada agente deixou de receber</h3>
      <p className={`${small} mt-0.5 max-w-[80ch]`}>
        Um tipo de item que o agente recebeu em várias entregas medidas e nunca usou sai do contexto dele, e o contexto fica menor. Pendências, promessas, dados sensíveis e o que a
        política libera pela finalidade do agente nunca saem. Quando o agente pergunta, afirma ou age sobre um item que ficou de fora, o tipo volta na medição seguinte.
      </p>
      {trims.length === 0 ? (
        <p className={`${small} mt-2`}>Nenhum agente tem itens de fora agora: todos usaram o que receberam, ou ainda não há entregas medidas suficientes.</p>
      ) : (
        <ul className="mt-3 grid gap-2">
          {trims.map((t) => (
            <li key={`${t.source_id}/${t.view}`} className={`${card} px-4 py-3`}>
              <p className="text-ink">
                <SourceName id={t.source_id} withVendor /> · visão {viewLabel(t.view)}
              </p>
              <ul className="mt-1.5 flex flex-wrap gap-1.5" aria-label="Tipos de item fora do contexto">
                {t.units.map((u) => (
                  <li key={u.unit} className="rounded-md border border-line bg-bg px-2 py-1 text-[0.8125rem] text-ink">
                    {itemKind(u.kind)}
                    {u.category ? ` · ${category(u.category).label}` : ''}
                    <span className="text-ink2"> · {fmtInt(u.deliveries)} entregas sem uso</span>
                  </li>
                ))}
              </ul>
              <p className={`${small} mt-1.5`}>
                {t.active ? `Em vigor desde ${fmtDateTime(t.derived_at)}.` : (INACTIVE[t.inactive_reason ?? ''] ?? 'Fora de vigor.')} Medido em pelo menos{' '}
                {fmtInt(t.min_deliveries)} entregas dos últimos {fmtInt(t.window_days)} dias.
              </p>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

/** The retrieval channels of the per-turn slots in words, in the order the table lists them. */
const SLOT_CHANNELS: Record<string, string> = {
  exact: 'Número exato',
  values: 'Valores fixados',
  lexical: 'Palavras em comum',
  temporal: 'Tempo e âncoras',
  semantic: 'Significado',
}
const slotChannel = (c: string) => SLOT_CHANNELS[c] ?? c
const slotRank = (c: string) => {
  const i = Object.keys(SLOT_CHANNELS).indexOf(c)
  return i < 0 ? Object.keys(SLOT_CHANNELS).length : i
}

const RUN_OUTCOMES: Record<string, string> = {
  applied: 'pesos ajustados',
  unchanged: 'nenhum canal se destacou dos outros, pesos mantidos',
  no_verdicts: 'nenhum encaixe medido na semana, pesos mantidos',
  vetoed: 'pesos mantidos pelo grupo de controle',
}
const GUARDS: Record<string, string> = {
  worse: 'os clientes com memória foram perguntados de novo mais vezes que os do grupo de controle',
  narrowed: 'a vantagem sobre o grupo de controle diminuiu desde o último ajuste',
  too_small: 'o experimento ainda tem poucas perguntas nos dois grupos para comparar',
}

const weightFmt = new Intl.NumberFormat('pt-BR', { minimumFractionDigits: 2, maximumFractionDigits: 2 })

/**
 * The slots by channel (11, 3.4): per view, what each retrieval channel brought to the per-turn slots and
 * how much of it the agents used, with the weight the fusion gives it today and the last nightly review
 * of the weights.
 */
function SlotChannels({ channels, weights }: { channels: SlotChannelUse[]; weights: RetrievalWeightsOut | null }) {
  const rows = [...channels].sort((a, b) => a.view.localeCompare(b.view) || slotRank(a.slot_channel) - slotRank(b.slot_channel))
  const last = weights?.runs[0]
  const num = `${td} text-right tabular-nums whitespace-nowrap`
  const head = (text: string) => (
    <th scope="col" className={`${th} text-right`}>
      {text}
    </th>
  )
  return (
    <div className="mt-6">
      <h3 className="text-[0.9375rem] font-semibold text-ink">Encaixes por canal de busca</h3>
      <p className={`${small} mt-0.5 max-w-[80ch]`}>
        A cada turno, a última fala do cliente escolhe alguns itens a mais da memória, por cinco canais de busca: o número exato, os valores fixados, as palavras em comum, o
        tempo e o significado. A tabela mostra quanto do que cada canal trouxe o agente usou. Uma vez por noite o peso de cada canal se ajusta pelo que foi usado: no máximo 10%
        por semana, entre metade e o dobro do peso inicial, e nunca quando o grupo de controle mostra piora.
      </p>
      {rows.length === 0 ? (
        <p className={`${small} mt-2`}>Nenhum encaixe medido no período.</p>
      ) : (
        <div className={`${card} mt-3 overflow-x-auto`}>
          <table className="w-full min-w-[56rem] border-collapse">
            <caption className="sr-only">Encaixes por visão e canal de busca</caption>
            <thead>
              <tr>
                <th scope="col" className={th}>
                  Canal de busca
                </th>
                {head('Encaixes')}
                {head('Aproveitamento')}
                {head('Repetição')}
                {head('Contradições')}
                {head('Fora do prazo')}
                {head('Peso hoje')}
              </tr>
            </thead>
            <tbody>
              {rows.map((c) => (
                <tr key={`${c.view}/${c.slot_channel}`}>
                  <th scope="row" className={`${td} text-left font-normal`}>
                    <span className="font-semibold">{slotChannel(c.slot_channel)}</span>
                    <span className="block text-[0.75rem] text-ink2">visão {viewLabel(c.view)}</span>
                  </th>
                  <td className={num}>{fmtInt(c.slots)}</td>
                  <td className={num}>
                    <Rate rate={c.usage_rate} fallback={{ num: c.used, den: c.slots }} />
                  </td>
                  <td className={num}>{fmtInt(c.repeated)}</td>
                  <td className={num}>{fmtInt(c.contradicted)}</td>
                  <td className={num}>
                    {c.turns > 0 ? (
                      <>
                        {fmtInt(c.skipped)} de {fmtInt(c.turns)}
                      </>
                    ) : (
                      <span className="text-ink2">não rodou</span>
                    )}
                  </td>
                  <td className={num}>
                    <span className="font-semibold text-ink">{weightFmt.format(c.weight)}</span>
                    <span className="block text-[0.75rem] text-ink2">inicial {weightFmt.format(c.default_weight)}</span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {weights && (
        <p className={`${small} mt-2 max-w-[80ch]`}>
          {last
            ? `Última revisão dos pesos em ${fmtDate(last.day)}: ${RUN_OUTCOMES[last.outcome] ?? last.outcome}${
                last.outcome === 'vetoed' && last.guard && GUARDS[last.guard] ? `, porque ${GUARDS[last.guard]}` : ''
              }.`
            : 'Os pesos ainda não passaram por nenhuma revisão noturna: a busca usa os pesos iniciais.'}{' '}
          {weights.version > 0 ? `Pesos na versão ${fmtInt(weights.version)}.` : 'Pesos iniciais em uso.'}
        </p>
      )}
    </div>
  )
}

/** The number of the table that each signal stands behind (the columns of ContextUseTable). */
const SIGNAL_COLUMN: Record<string, string> = {
  used: 'por trás da coluna Aproveitamento',
  repeated: 'por trás da coluna Repetição',
  contradicted: 'por trás da coluna Contradições',
  selection_miss: 'por trás da falta no contexto, em Contradições',
}

/** app/measure.py REVIEW_WINDOW: the verdicts beside a report are at most those of its last 90 days. */
const REVIEW_DAYS = 90

/** A signal the Console does not know yet goes after the four it does. */
const rank = (s: string) => {
  const i = (SIGNALS as readonly string[]).indexOf(s)
  return i < 0 ? SIGNALS.length : i
}

/** The calendar day `days` before `day`, both as YYYY-MM-DD. */
function daysBefore(day: string, days: number): string {
  const [y, m, d] = day.split('-').map(Number)
  return new Date(Date.UTC(y!, m! - 1, d! - days)).toISOString().slice(0, 10)
}

/**
 * How far sample review confirms each signal behind the numbers: the verdicts on the report's
 * sources over its period, or over its last 90 days when the period is longer.
 */
function ReviewBeside({ report }: { report: ContextUseReport }) {
  const { space } = useSpace()
  const floor = daysBefore(report.until, REVIEW_DAYS - 1)
  const clipped = floor > report.since
  const rows = [...(report.review ?? [])].sort((a, b) => rank(a.signal) - rank(b.signal))
  const num = `${td} text-right tabular-nums whitespace-nowrap`
  return (
    <div className="mt-6">
      <h3 className="text-[0.9375rem] font-semibold text-ink">Quanto a revisão confirma cada sinal</h3>
      <p className={`${small} mt-0.5 max-w-[80ch]`}>
        Vereditos da revisão por amostra sobre as fontes deste relatório, de {fmtDate(clipped ? floor : report.since)} a {fmtDate(report.until)}:{' '}
        {clipped ? 'os últimos 90 dias do período do relatório, que é mais longo' : 'o período do relatório'}. Ao lado de cada taxa, é o que diz quanto confiar nela, e a contagem corrigida é o que a revisão manteria das marcas.
      </p>
      {rows.some((r) => r.judged > 0) ? (
        <div className={`${card} mt-3 overflow-x-auto`}>
          <table className="w-full min-w-[36rem] border-collapse">
            <caption className="sr-only">Concordância da revisão por sinal</caption>
            <thead>
              <tr>
                <th scope="col" className={th}>
                  Sinal
                </th>
                <th scope="col" className={`${th} text-right`}>
                  Julgados
                </th>
                <th scope="col" className={`${th} text-right`}>
                  Confirmados
                </th>
                <th scope="col" className={`${th} text-right`}>
                  Concordância
                </th>
                <th scope="col" className={`${th} text-right`}>
                  Contagem corrigida
                </th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.signal}>
                  <th scope="row" className={`${td} text-left font-normal`}>
                    {signal(r.signal)}
                    {SIGNAL_COLUMN[r.signal] && <span className="block text-[0.75rem] text-ink2">{SIGNAL_COLUMN[r.signal]}</span>}
                  </th>
                  <td className={num}>{fmtInt(r.judged)}</td>
                  <td className={num}>{fmtInt(r.confirmed)}</td>
                  <td className={num}>
                    {r.agreement ? <Rate rate={r.agreement} /> : <span className="text-ink2">sem veredito</span>}
                    {r.contract_ready && <span className="block text-[0.75rem] text-ink2">pode entrar em contrato</span>}
                  </td>
                  <td className={num}>
                    {r.calibrated ? (
                      <>
                        <span className="font-semibold text-ink">{fmtInt(Math.round(r.calibrated.value))}</span>
                        <span className="block text-[0.75rem] text-ink2">
                          de {fmtInt(r.measured)} marcados · {fmtInt(Math.round(r.calibrated.low))} a {fmtInt(Math.round(r.calibrated.high))}
                        </span>
                      </>
                    ) : (
                      <span className="text-ink2">sem veredito</span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <p className={`${small} mt-2`}>
          Nenhum veredito sobre estes sinais nesse intervalo. Os vereditos vêm da{' '}
          <Link to={paths.review(space.space_id)} className="text-ink underline decoration-accent decoration-2 underline-offset-4">
            revisão por amostra
          </Link>
          .
        </p>
      )}
    </div>
  )
}

/** One conversation, delivery by delivery: the evidence behind the numbers. */
function ConversationDetail() {
  const { cell } = useSpace()
  const { list: agents } = useSources()
  const [id, setId] = useState('')
  const [agent, setAgent] = useState('')
  const [period, setPeriod] = useState<'1' | '7' | '30'>('7')
  const act = useAction<ContextUseDetail>()
  const fid = useId()
  const days = Number(period)
  const conversations = usePaged(
    (cursor, signal) => cell.measuredConversations({ source_id: agent || null, since: utcDayInput(days - 1), until: utcDayInput(0), cursor }, { signal }),
    [cell, agent, days],
  )
  const open = (conversationId: string) => {
    setId(conversationId)
    act.run(() => cell.contextUseDetail(conversationId))
  }
  return (
    <section aria-labelledby={`${fid}-h`} data-tour="usage.conversation" className={`${card} mt-8 p-4 sm:p-5`}>
      <h2 id={`${fid}-h`} className={h2}>
        Uma conversa, entrega por entrega
      </h2>
      <p className={`${small} mt-0.5`}>Escolha uma conversa medida do agente e do período, ou digite o ID que aparece nos comprovantes. A medição mostra cada entrega de contexto e o que o agente fez com ela.</p>
      <div className="mt-4 grid gap-3 sm:grid-cols-2 lg:max-w-2xl">
        <Select
          label="Agente"
          value={agent}
          onChange={setAgent}
          options={[{ value: '', label: 'Todos os agentes' }, ...agents.map((s) => ({ value: s.source_id, label: s.vendor ? `${s.name} (${s.vendor})` : s.name }))]}
        />
        <Select
          label="Período"
          value={period}
          onChange={setPeriod}
          options={[
            { value: '1', label: 'Hoje (UTC)' },
            { value: '7', label: 'Últimos 7 dias (UTC)' },
            { value: '30', label: 'Últimos 30 dias (UTC)' },
          ]}
        />
      </div>
      <div className="mt-3">
        {(conversations.status === 'loading' || conversations.status === 'idle') && <Loading label="Carregando as conversas medidas…" />}
        {conversations.status === 'error' && <ErrorState error={conversations.error} onRetry={conversations.reload} compact />}
        {conversations.status === 'success' && conversations.items.length === 0 && <p className={small}>Nenhuma conversa medida neste período, contado em dias UTC.</p>}
        {conversations.items.length > 0 && (
          // `relative`: the hidden "Abrir" header is positioned, and would otherwise escape the scroll box and widen the page on a phone.
          <div className="relative overflow-x-auto">
            <table className="w-full min-w-[36rem] border-collapse">
              <caption className="sr-only">Conversas medidas</caption>
              <thead>
                <tr>
                  <th scope="col" className={th}>Conversa</th>
                  <th scope="col" className={th}>Agente</th>
                  <th scope="col" className={th}>Terminou</th>
                  <th scope="col" className={th}>Contexto</th>
                  <th scope="col" className={th}>
                    <span className="sr-only">Abrir</span>
                  </th>
                </tr>
              </thead>
              <tbody>
                {conversations.items.map((c) => (
                  <tr key={`${c.conversation_id}-${c.source_id}-${c.ended_at}`}>
                    <td className={`${td} ${mono} break-all`}>{c.conversation_id}</td>
                    <td className={td}>
                      <SourceName id={c.source_id} /> · {channel(c.channel)}
                    </td>
                    <td className={td}>{fmtDateTime(c.ended_at)}</td>
                    <td className={td}>{c.no_context ? 'Sem leitura de contexto' : 'Leu contexto'}</td>
                    <td className={`${td} text-right`}>
                      <button type="button" className={btnSecondary} onClick={() => open(c.conversation_id)} aria-label={`Abrir a medição de ${c.conversation_id}`}>
                        Abrir
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        <LoadMore hasMore={conversations.hasMore} loading={conversations.loadingMore} error={conversations.moreError} onClick={conversations.loadMore} />
      </div>
      <form
        className="mt-4 flex flex-wrap items-end gap-3"
        onSubmit={(e) => {
          e.preventDefault()
          if (id.trim()) act.run(() => cell.contextUseDetail(id.trim()))
        }}
      >
        <div className="min-w-[16rem] flex-1">
          <label htmlFor={fid} className={label}>
            ID da conversa
          </label>
          <input id={fid} className={`${input} font-mono`} value={id} onChange={(e) => setId(e.target.value)} autoComplete="off" spellCheck={false} />
        </div>
        <button type="submit" className={btnSecondary} disabled={act.busy || !id.trim()}>
          {act.busy ? 'Abrindo…' : 'Abrir a medição'}
        </button>
      </form>
      <div className="mt-4" aria-live="polite">
        {Boolean(act.error) && <ErrorState error={act.error} compact />}
        {act.result && act.result.entries.length === 0 && <p className={small}>Esta conversa não tem entrega medida.</p>}
        {act.result && act.result.entries.length > 0 && (
          <ul className="divide-y divide-line2 rounded-xl border border-line">
            {act.result.entries.map((en) => (
              <li key={en.manifest_hash + en.source_id} className="px-4 py-3">
                <p className="text-ink">
                  <SourceName id={en.source_id} withVendor /> · {channel(en.channel)} · visão {en.view}
                </p>
                {en.not_measured_reason ? (
                  <p className={`${small} mt-0.5`}>Não medida: {notMeasuredReason(en.not_measured_reason)}.</p>
                ) : (
                  <p className={`${small} mt-0.5`}>
                    {fmtInt(en.used)} itens usados · {fmtInt(en.repeated)} de {fmtInt(en.questions)} perguntas repetidas · {fmtInt(en.contradicted)} contradições
                    {en.late ? ' · contexto pedido depois da primeira resposta' : ''}
                    {en.transfer_unread ? ' · transferiu sem o destino ler' : ''}
                    {en.tokens_saved ? ` · ${fmtInt(en.tokens_saved)} tokens a menos, sem o que este agente não usa` : ''}
                  </p>
                )}
                <p className={`${mono} mt-0.5 text-ink2`}>
                  grupo {en.experiment_group} · medição {en.measure_version} · {fmtDateTime(en.measured_at)}
                </p>
              </li>
            ))}
          </ul>
        )}
      </div>
    </section>
  )
}
