import { screen, waitFor, within } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import type { ContextUseBucket, PromptCacheReport } from '../api/types'
import { fmtDateTime, fmtPct, utcDayInput } from '../lib/format'
import { mondayOf, wilson } from '../lib/weeks'
import { API, at, baseRoutes, CONTROL, mockApi, problem, signInAt, SP_PROD, SP_SBX, SRC_BILLING, SRC_VOICE } from './harness'

const bucket = (b: Partial<ContextUseBucket>): ContextUseBucket => ({
  sessions: 0,
  no_context: 0,
  deliveries: 0,
  not_measured: 0,
  late_deliveries: 0,
  deliveries_used: 0,
  questions: 0,
  repeated: 0,
  contradicted: 0,
  selection_misses: 0,
  transfers: 0,
  transfers_unread: 0,
  recontacts: 0,
  trimmed_deliveries: 0,
  tokens_saved: 0,
  usage_rate: null,
  repetition_rate: null,
  transfer_unread_rate: null,
  recontact_rate: null,
  ...b,
})

const VOICE = bucket({
  source_id: SRC_VOICE,
  sessions: 1200,
  no_context: 12,
  deliveries: 1150,
  not_measured: 50,
  late_deliveries: 40,
  deliveries_used: 700,
  questions: 400,
  repeated: 32,
  contradicted: 7,
  transfers: 20,
  transfers_unread: 3,
  recontacts: 130,
  usage_rate: { value: 0.636, low: 0.607, high: 0.664, n: 1100 },
  repetition_rate: { value: 0.08, low: 0.057, high: 0.111, n: 400 },
  recontact_rate: { value: 0.108, low: 0.092, high: 0.127, n: 1200 },
})
const BILLING = bucket({ source_id: SRC_BILLING, sessions: 300, deliveries: 300, not_measured: 300 })

const CACHE: PromptCacheReport = {
  since: '2026-08-26',
  until: '2026-09-24',
  group_by: ['source_id'],
  buckets: [
    {
      source_id: SRC_VOICE,
      calls: 1240,
      prompt_tokens: 3_720_000,
      cached_tokens: 2_540_000,
      cache_write_tokens: 0,
      hit_rate: 0.6828,
      estimated_savings_micros: 3_810_000,
      unpriced_calls: 0,
    },
    {
      source_id: SRC_BILLING,
      calls: 80,
      prompt_tokens: 160_000,
      cached_tokens: 0,
      cache_write_tokens: 0,
      hit_rate: 0,
      estimated_savings_micros: null,
      unpriced_calls: 80,
    },
  ],
  prices: [{ provider: 'openai', model: 'gpt-4.1', input: 2, cached_input: 0.5, cache_write: 2, table: 'product' }],
  prices_as_of: '2026-09-24',
  estimate: true,
}

/** app/metering.py budget: extraction held by the contract's ceiling and waiting, the assistant on the default. */
const BUDGET = {
  items: [
    { purpose: 'assistant', day: '2026-09-24', cap_micros: 200_000, from_contract: false, spent_micros: 41_300, reached: false, resumes_at: null },
    { purpose: 'extraction', day: '2026-09-24', cap_micros: 2_000_000, from_contract: true, spent_micros: 2_004_120, reached: true, resumes_at: '2026-09-25T00:00:00Z' },
  ],
}

/** app/latency.py: a day of reads inside the objective and a week whose series has no reads. */
const LATENCY = {
  available: true,
  target_ms: 100,
  slo: 0.99,
  windows: [
    { window: '24h', reads: 12450, p50_ms: 8.1, p95_ms: 42.3, under_target: 0.996, within_slo: true, error_budget_used: 0.4, degraded: 0.0025 },
    { window: '7d', reads: 0 },
  ],
  as_of: '2026-09-24T12:00:00Z',
}

const routes = (extra = {}, roles?: Parameters<typeof baseRoutes>[1]) =>
  baseRoutes({
    [`GET ${API}/v1/context-use/conversations`]: { items: [], next_cursor: null },
    [`GET ${CONTROL}/v1/usage`]: {
      items: [
        { space_id: SP_PROD, period_start: '2026-09-20T00:00:00Z', metric: 'session_billable', quantity: 1000, cost_micros: 0 },
        { space_id: SP_PROD, period_start: '2026-09-21T00:00:00Z', metric: 'session_billable', quantity: 480, cost_micros: 0 },
        { space_id: SP_PROD, period_start: '2026-09-21T00:00:00Z', metric: 'extraction_run', quantity: 900, cost_micros: 2_500_000 },
        { space_id: SP_SBX, period_start: '2026-09-21T00:00:00Z', metric: 'session_billable', quantity: 77777, cost_micros: 0 },
      ],
      quotas: [{ tenant_id: 't', metric: 'session_billable', limit: 50000, period: 'month' }],
    },
    [`GET ${API}/v1/usage`]: {
      since: '',
      until: '',
      granularity: 'day',
      source_id: null,
      group_by: 'source',
      items: [
        { start: '2026-09-21T00:00:00Z', metric: 'session_billable', quantity: 700, cost_micros: 0, source_id: SRC_VOICE },
        { start: '2026-09-22T00:00:00Z', metric: 'session_billable', quantity: 480, cost_micros: 0, source_id: SRC_VOICE },
        { start: '2026-09-22T00:00:00Z', metric: 'context_read', quantity: 2400, cost_micros: 0, source_id: SRC_VOICE },
        { start: '2026-09-22T00:00:00Z', metric: 'event_ingested', quantity: 4100, cost_micros: 0, source_id: SRC_BILLING },
        { start: '2026-09-22T00:00:00Z', metric: 'event_ingested', quantity: 90, cost_micros: 0, source_id: null },
      ],
    },
    [`GET ${API}/v1/usage/budget`]: BUDGET,
    [`GET ${API}/v1/usage/context-latency`]: LATENCY,
    [`GET ${API}/v1/usage/prompt-cache`]: (r: { url: URL }) =>
      r.url.searchParams.get('group_by') === 'vendor'
        ? { ...CACHE, group_by: ['vendor'], buckets: [{ ...CACHE.buckets[0]!, source_id: null, vendor: 'Fornecedor B' }] }
        : CACHE,
    [`GET ${API}/v1/context-use`]: (r: { url: URL }) => {
      const g = r.url.searchParams.get('group_by')
      if (g === 'channel') return { since: '', until: '', group_by: ['channel'], buckets: [bucket({ channel: 'voice', sessions: 10, deliveries: 10, deliveries_used: 5 })] }
      if (g === 'vendor')
        return { since: '', until: '', group_by: ['vendor'], buckets: [bucket({ vendor: 'Fornecedor A', sessions: 1200, deliveries: 1150, usage_rate: { value: 0.61, low: 0.58, high: 0.64, n: 1100 } })] }
      return { since: '', until: '', group_by: ['source_id'], buckets: [VOICE, BILLING] }
    },
    ...extra,
  }, roles)

describe('Usage and context use', () => {
  it('shows usage by agent from the data API, with the space\'s own work apart', async () => {
    const api = mockApi(routes())
    await signInAt(at('uso'))
    const table = await screen.findByRole('table', { name: 'Uso por agente' })
    expect(within(table).getByRole('row', { name: /Agente de voz/ })).toHaveTextContent('1.1802.400')
    expect(within(table).getByRole('row', { name: /Trabalho do próprio espaço/ })).toHaveTextContent('90')
    expect(within(table).getByRole('row', { name: /Total/ })).toHaveTextContent('4.190')
    const q = api.find('GET', '/v1/usage').find((c) => c.url.origin === API)!.url.searchParams
    expect(q.get('group_by')).toBe('source')
    expect(q.get('granularity')).toBe('day')
  })

  it('sums usage by metric for this space only, with the quota and our cost', async () => {
    const api = mockApi(routes())
    await signInAt(at('uso'))
    const table = await screen.findByRole('table', { name: 'Uso do espaço por métrica' })
    const billable = within(table).getByRole('row', { name: /Conversas e tarefas cobradas/ })
    expect(billable).toHaveTextContent('1.480')
    expect(billable).toHaveTextContent('50.000 por mês')
    expect(table).not.toHaveTextContent('77.777')
    expect(within(table).getByRole('row', { name: /Extrações/ })).toHaveTextContent('US$ 2,50')
    const q = api.find('GET', '/v1/usage').find((c) => c.url.origin === CONTROL)!.url.searchParams
    expect(q.get('since')).toBeTruthy()
    expect(q.get('until')).toBeTruthy()
  })

  it('downloads the month per space as CSV, to invoice from, with the session', async () => {
    // The statement comes from the control plane, one UTC month, for the analysis or security roles.
    URL.createObjectURL = vi.fn(() => 'blob:statement')
    URL.revokeObjectURL = vi.fn()
    const api = mockApi(
      routes({ [`GET ${CONTROL}/v1/usage/statement`]: new Response('month,tenant_id\n', { status: 200, headers: { 'Content-Type': 'text/csv' } }) }),
    )
    const { user } = await signInAt(at('uso'))
    await user.click(await screen.findByRole('button', { name: 'Baixar o extrato em CSV' }))
    await waitFor(() => expect(api.find('GET', '/v1/usage/statement')).toHaveLength(1))
    const [call] = api.find('GET', '/v1/usage/statement')
    expect(call!.url.origin).toBe(CONTROL)
    expect(call!.url.searchParams.get('format')).toBe('csv')
    expect(call!.url.searchParams.get('month')).toMatch(/^\d{4}-\d{2}$/)
    expect(call!.headers.get('Authorization')).toMatch(/^Bearer /)
  })

  it('fills the quota and cost table from the hours the cell pushes, with a daily quota in words', async () => {
    // app/control_sync.py push_usage: one row per space, hour and metric, summed over sources.
    const hour = (h: number) => `2026-09-24T${String(h).padStart(2, '0')}:00:00Z`
    mockApi(
      routes({
        [`GET ${CONTROL}/v1/usage`]: {
          items: [
            { space_id: SP_PROD, period_start: hour(9), metric: 'session_billable', quantity: 12, cost_micros: 0 },
            { space_id: SP_PROD, period_start: hour(10), metric: 'session_billable', quantity: 30, cost_micros: 0 },
            { space_id: SP_PROD, period_start: hour(10), metric: 'extraction_tokens', quantity: 51_000, cost_micros: 0 },
            { space_id: SP_PROD, period_start: hour(10), metric: 'extraction_run', quantity: 42, cost_micros: 118_400 },
            { space_id: SP_PROD, period_start: hour(10), metric: 'export_run', quantity: 1, cost_micros: 0 },
          ],
          quotas: [
            { tenant_id: 't', metric: 'session_billable', limit: 50000, period: 'month' },
            { tenant_id: 't', metric: 'extraction_run', limit: 400, period: 'day' },
            { tenant_id: 't', metric: 'llm_daily_micros:extraction', limit: 2_000_000, period: 'day' },
          ],
        },
      }),
    )
    await signInAt(at('uso'))
    const table = await screen.findByRole('table', { name: 'Uso do espaço por métrica' })
    expect(within(table).getByRole('row', { name: /Conversas e tarefas cobradas/ })).toHaveTextContent('42')
    const runs = within(table).getByRole('row', { name: /^Extrações/ })
    expect(runs).toHaveTextContent('400 por dia')
    expect(runs).toHaveTextContent('US$ 0,12')
    expect(within(table).getByRole('row', { name: /Tokens da extração/ })).toHaveTextContent('51.000')
    expect(within(table).getByRole('row', { name: /Execuções da exportação/ })).toHaveTextContent('1')
    // The AI spend ceiling is a quota with no usage metric: it shows in the budget table, not here.
    expect(table).not.toHaveTextContent('llm_daily_micros')
    expect(screen.getByText(/manda o uso de cada hora ao plano de controle a cada 15 minutos/)).toBeInTheDocument()
  })

  it("shows today's AI spend per purpose against the daily ceiling, who set it, and when waiting work resumes", async () => {
    const api = mockApi(routes())
    await signInAt(at('uso'))
    const table = await screen.findByRole('table', { name: 'Gasto de hoje com IA contra o teto diário, por finalidade' })
    const extraction = within(table).getByRole('row', { name: /Extração da memória/ })
    expect(extraction).toHaveTextContent('US$ 2,0041')
    expect(extraction).toHaveTextContent('100,0% do teto')
    expect(extraction).toHaveTextContent('US$ 2,00')
    expect(extraction).toHaveTextContent('O contrato')
    expect(extraction).toHaveTextContent('Teto atingido')
    expect(extraction).toHaveTextContent(`O trabalho espera e volta em ${fmtDateTime('2026-09-25T00:00:00Z')}.`)
    const assistant = within(table).getByRole('row', { name: /Assistente de configuração/ })
    expect(assistant).toHaveTextContent('US$ 0,0413')
    expect(assistant).toHaveTextContent('20,7% do teto')
    expect(assistant).toHaveTextContent('O padrão da Niadra, sem teto no contrato')
    expect(assistant).toHaveTextContent('Dentro do teto')
    expect(screen.getByText(/^Dia 24\/09\/2026 em UTC\./)).toBeInTheDocument()
    expect(screen.getByText(/A primeira espera da extração no dia manda o evento usage\.budget_reached/)).toBeInTheDocument()
    expect(api.find('GET', '/v1/usage/budget')).toHaveLength(1)
  })

  it('shows the budget loading, then empty', async () => {
    let release: (v: object) => void = () => undefined
    mockApi(routes({ [`GET ${API}/v1/usage/budget`]: () => new Promise<object>((r) => (release = r)) }))
    await signInAt(at('uso'))
    expect(await screen.findByText('Lendo o gasto de hoje…')).toBeInTheDocument()
    release({ items: [] })
    expect(await screen.findByText('Nenhuma finalidade com teto de gasto neste espaço.')).toBeInTheDocument()
  })

  it('says when the budget cannot be read, with the request code, and reads it again on request', async () => {
    let fail = true
    const api = mockApi(routes({ [`GET ${API}/v1/usage/budget`]: () => (fail ? problem(500, 'internal_error', 'boom', 'req_budget') : BUDGET) }))
    const { user } = await signInAt(at('uso'))
    const section = (await screen.findByRole('heading', { name: 'Teto diário de gasto com IA' })).closest('section')!
    expect(await within(section).findByRole('alert')).toHaveTextContent('req_budget')
    // The rest of the screen stays.
    expect(screen.getByRole('table', { name: 'Uso por agente' })).toBeInTheDocument()
    fail = false
    await user.click(within(section).getByRole('button', { name: 'Tentar de novo' }))
    expect(await within(section).findByText('Teto atingido')).toBeInTheDocument()
    expect(api.find('GET', '/v1/usage/budget')).toHaveLength(2)
  })

  it('leaves the budget to the roles that may read it', async () => {
    const api = mockApi(routes({}, ['security']))
    await signInAt(at('uso'))
    expect(await screen.findByText('O teto de gasto exige o papel Análise.')).toBeInTheDocument()
    expect(api.find('GET', '/v1/usage/budget')).toHaveLength(0)
  })

  it('shows the server time of the context reads per window, against the target and the objective', async () => {
    const api = mockApi(routes())
    await signInAt(at('uso'))
    const section = (await screen.findByRole('heading', { name: 'Tempo de leitura do contexto' })).closest('section')!
    const day = await within(section).findByRole('article', { name: 'Últimas 24 horas' })
    expect(within(day).getByText('Mediana (p50)').nextSibling).toHaveTextContent('8,1 ms')
    expect(within(day).getByText('p95').nextSibling).toHaveTextContent('42,3 ms')
    expect(within(day).getByText('Abaixo de 100 ms').nextSibling).toHaveTextContent('99,6%meta publicada: 100 ms')
    expect(within(day).getByText('Objetivo de 99%').nextSibling).toHaveTextContent('Dentro40,0% da margem de erro usada')
    expect(within(day).getByText('Leituras').nextSibling).toHaveTextContent('12.450')
    expect(within(day).getByText('Servidas degradadas').nextSibling).toHaveTextContent('0,3%')
    expect(within(section).getByRole('article', { name: 'Últimos 7 dias' })).toHaveTextContent('Sem leituras no período.')
    expect(within(section).getAllByText(/tempo do servidor, sem a rede/i).length).toBeGreaterThanOrEqual(1)
    expect(api.find('GET', '/v1/usage/context-latency')).toHaveLength(1)
  })

  it('says when the metrics are out', async () => {
    mockApi(routes({ [`GET ${API}/v1/usage/context-latency`]: { ...LATENCY, available: false, windows: [] } }))
    await signInAt(at('uso'))
    expect(await screen.findByText('As métricas da região não responderam agora.')).toBeInTheDocument()
  })

  it('reads the latency only with the analysis role', async () => {
    const api = mockApi(routes({}, ['security']))
    await signInAt(at('uso'))
    expect(await screen.findByText('O tempo de leitura exige o papel Análise.')).toBeInTheDocument()
    expect(api.find('GET', '/v1/usage/context-latency')).toHaveLength(0)
  })

  it('says when a window misses the objective, with the margin it spent', async () => {
    const over = { window: '24h', reads: 300, p50_ms: 20, p95_ms: 130, under_target: 0.9, within_slo: false, error_budget_used: 10, degraded: 0 }
    mockApi(routes({ [`GET ${API}/v1/usage/context-latency`]: { ...LATENCY, windows: [over, { ...over, window: '7d' }] } }))
    await signInAt(at('uso'))
    const week = await screen.findByRole('article', { name: 'Últimos 7 dias' })
    expect(within(week).getByText('Objetivo de 99%').nextSibling).toHaveTextContent('Fora1.000,0% da margem de erro usada')
  })

  it('shows rates with their 95% interval, recontact beside them, and "não medido" instead of zero', async () => {
    const api = mockApi(routes())
    await signInAt(at('uso'))
    const table = await screen.findByRole('table', { name: /Aproveitamento do contexto por agente/ })
    const voice = within(table).getByRole('row', { name: /Agente de voz/ })
    expect(voice).toHaveTextContent('63,6%')
    expect(voice).toHaveTextContent('60,7% a 66,4% · n 1.100')
    expect(voice).toHaveTextContent('10,8%')
    expect(voice).toHaveTextContent('3 de 20')
    const billing = within(table).getByRole('row', { name: /Agente de cobrança/ })
    expect(within(billing).getAllByText('não medido').length).toBeGreaterThanOrEqual(3)
    expect(api.find('GET', '/v1/context-use')[0]!.url.searchParams.getAll('group_by')).toEqual(['source_id'])
  })

  it('asks the server to group by vendor and by channel', async () => {
    const api = mockApi(routes())
    const { user } = await signInAt(at('uso'))
    await screen.findByRole('table', { name: /Aproveitamento do contexto por agente/ })
    const grouping = screen.getByRole('radiogroup', { name: 'Agrupar por' })
    await user.click(within(grouping).getByText('Por fornecedor'))
    const vendors = await screen.findByRole('table', { name: /Aproveitamento do contexto por fornecedor/ })
    expect(within(vendors).getByRole('row', { name: /Fornecedor A/ })).toHaveTextContent('61,0%')
    expect(api.find('GET', '/v1/context-use').at(-1)!.url.searchParams.getAll('group_by')).toEqual(['vendor'])
    await user.click(within(grouping).getByText('Por canal'))
    expect(await screen.findByRole('table', { name: /por canal/ })).toBeInTheDocument()
    expect(api.find('GET', '/v1/context-use').at(-1)!.url.searchParams.getAll('group_by')).toEqual(['channel'])
  })

  it('counts the period in UTC days, as the server does, so the current day is whole at night in Brazil', async () => {
    // 22:30 of 30/09 in Brasília is already 01/10 in UTC: a period built from the local day asked for
    // "until 30/09" and the measured conversations of the current UTC day never showed.
    vi.useFakeTimers({ toFake: ['Date'] })
    vi.setSystemTime(new Date('2026-10-01T01:30:00Z'))
    try {
      const api = mockApi(routes())
      await signInAt(at('uso'))
      await screen.findByRole('table', { name: /Aproveitamento do contexto por agente/ })
      const use = api.find('GET', '/v1/context-use')[0]!.url.searchParams
      expect(use.get('since')).toBe('2026-09-02')
      expect(use.get('until')).toBe('2026-10-01')
      for (const c of api.find('GET', '/v1/usage')) {
        expect(c.url.searchParams.get('since')).toBe('2026-09-02T00:00:00.000Z')
        expect(c.url.searchParams.get('until')).toBe('2026-10-01T23:59:59.999Z')
      }
      expect(api.find('GET', '/v1/usage/prompt-cache')[0]!.url.searchParams.get('until')).toBe('2026-10-01')
      expect(api.find('GET', '/v1/context-use/conversations')[0]!.url.searchParams.get('until')).toBe('2026-10-01')
      // The screen says the days are UTC and what that means in the person's zone.
      expect(screen.getByText(/De 02\/09\/2026 a 01\/10\/2026, em dias UTC, como o servidor conta: o dia de hoje termina às 21:00 no seu horário\./)).toBeInTheDocument()
      expect(screen.getByRole('combobox', { name: 'Período' })).toHaveTextContent('Últimos 7 dias (UTC)')
    } finally {
      vi.useRealTimers()
    }
  })

  it('sums the days into weeks, with the rates of each week and its interval', async () => {
    const today = utcDayInput(0)
    const lastMonday = mondayOf(today)
    const api = mockApi(
      routes({
        [`GET ${API}/v1/context-use`]: (r: { url: URL }) =>
          r.url.searchParams.get('group_by') === 'day'
            ? {
                since: '',
                until: '',
                group_by: ['day'],
                buckets: [
                  bucket({ day: lastMonday, sessions: 20, deliveries: 20, deliveries_used: 12, recontacts: 2, usage_rate: { value: 0.6, low: 0.39, high: 0.78, n: 20 } }),
                  bucket({ day: today, sessions: 10, deliveries: 10, deliveries_used: 3, recontacts: 1, usage_rate: { value: 0.3, low: 0.1, high: 0.6, n: 10 } }),
                ],
              }
            : { since: '', until: '', group_by: ['source_id'], buckets: [VOICE, BILLING] },
      }),
    )
    const { user } = await signInAt(at('uso'))
    await screen.findByRole('table', { name: /Aproveitamento do contexto por agente/ })
    await user.click(within(screen.getByRole('radiogroup', { name: 'Agrupar por' })).getByText('Por semana'))
    const weeks = await screen.findByRole('table', { name: /Aproveitamento do contexto por semana/ })
    expect(api.find('GET', '/v1/context-use').at(-1)!.url.searchParams.getAll('group_by')).toEqual(['day'])
    const label = `${lastMonday.slice(8, 10)}/${lastMonday.slice(5, 7)}`
    const row = within(weeks).getByRole('row', { name: new RegExp(`^${label}`) })
    // 15 of 30 deliveries used that week: 50,0%, with its interval, not an average of the two days.
    const rate = wilson(15, 30)!
    expect(row).toHaveTextContent('50,0%')
    expect(row).toHaveTextContent(`${fmtPct(rate.low)} a ${fmtPct(rate.high)} · n 30`)
    expect(row).toHaveTextContent('10,0%')
    // Every week of the period is listed, a quiet one too.
    expect(within(weeks).getAllByRole('row').length).toBeGreaterThanOrEqual(5)
  })

  it('opens one conversation, delivery by delivery', async () => {
    const api = mockApi(
      routes({
        [`POST ${API}/v1/context-use/conversation`]: {
          conversation_id: 'conv_881',
          entries: [
            { manifest_hash: 'm1', source_id: SRC_VOICE, measure_version: 'm-2', channel: 'voice', view: 'voice', experiment_group: 'memory', items: [], questions: 3, repeated: 1, used: 2, contradicted: 0, late: false, transfer_unread: null, not_measured_reason: null, measured_at: '2026-09-22T17:20:00Z' },
            { manifest_hash: 'm2', source_id: SRC_BILLING, measure_version: 'm-2', channel: 'erp', view: 'task:billing', experiment_group: 'memory', items: [], questions: 0, repeated: 0, used: 0, contradicted: 0, late: false, transfer_unread: null, not_measured_reason: 'partial_transcript', measured_at: '2026-09-22T17:20:00Z' },
          ],
        },
      }),
    )
    const { user } = await signInAt(at('uso'))
    await user.type(await screen.findByLabelText('ID da conversa'), 'conv_881')
    await user.click(screen.getByRole('button', { name: 'Abrir a medição' }))
    expect(await screen.findByText(/2 itens usados · 1 de 3 perguntas repetidas/)).toBeInTheDocument()
    expect(screen.getByText('Não medida: transcrição parcial da voz.')).toBeInTheDocument()
    expect(api.find('POST', '/v1/context-use/conversation').map((c) => c.body)).toEqual([{ conversation_id: 'conv_881' }])
  })

  it('shows beside the numbers how far sample review confirms each signal, for the period of the report', async () => {
    const review = [
      { signal: 'used', judged: 40, confirmed: 36, agreement: { value: 0.9, low: 0.769, high: 0.96, n: 40 }, contract_ready: false, measured: 200, calibrated: { value: 180, low: 153.8, high: 192 } },
      { signal: 'repeated', judged: 12, confirmed: 9, agreement: { value: 0.75, low: 0.468, high: 0.911, n: 12 } },
      { signal: 'contradicted', judged: 0, confirmed: 0, agreement: null },
      { signal: 'selection_miss', judged: 3, confirmed: 1, agreement: { value: 0.333, low: 0.061, high: 0.792, n: 3 } },
    ]
    mockApi(routes({ [`GET ${API}/v1/context-use`]: { since: '2026-08-26', until: '2026-09-24', group_by: ['source_id'], buckets: [VOICE, BILLING], review } }))
    await signInAt(at('uso'))
    const table = await screen.findByRole('table', { name: 'Concordância da revisão por sinal' })
    expect(within(table).getAllByRole('rowheader').map((h) => h.textContent)).toEqual([
      'Aproveitadopor trás da coluna Aproveitamento',
      'Perguntou de novopor trás da coluna Repetição',
      'Contradisse a memóriapor trás da coluna Contradições',
      'Faltou no contextopor trás da falta no contexto, em Contradições',
    ])
    const used = within(table).getByRole('row', { name: /Aproveitado/ })
    expect(within(used).getAllByRole('cell').map((c) => c.textContent)).toEqual(['40', '36', '90,0%76,9% a 96,0% · n 40', '180de 200 marcados · 154 a 192'])
    expect(within(table).getByRole('row', { name: /Contradisse a memória/ })).toHaveTextContent('sem veredito')
    expect(table).not.toHaveTextContent('pode entrar em contrato')
    expect(screen.getByText('Vereditos da revisão por amostra sobre as fontes deste relatório, de 26/08/2026 a 24/09/2026: o período do relatório. Ao lado de cada taxa, é o que diz quanto confiar nela, e a contagem corrigida é o que a revisão manteria das marcas.')).toBeInTheDocument()
  })

  it('says when the review covers only the last 90 days of a longer report, and when no verdict exists', async () => {
    const none = ['used', 'repeated', 'contradicted', 'selection_miss'].map((signal) => ({ signal, judged: 0, confirmed: 0, agreement: null }))
    mockApi(routes({ [`GET ${API}/v1/context-use`]: { since: '2025-09-25', until: '2026-09-24', group_by: ['source_id'], buckets: [VOICE], review: none } }))
    await signInAt(at('uso'))
    // app/measure.py: the verdicts of max(since, until - 89 days) to until.
    expect(await screen.findByText(/de 27\/06\/2026 a 24\/09\/2026: os últimos 90 dias do período do relatório, que é mais longo\./)).toBeInTheDocument()
    expect(screen.getByText(/Nenhum veredito sobre estes sinais nesse intervalo\./)).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'revisão por amostra' })).toHaveAttribute('href', `/console/e/${SP_PROD}/revisao`)
    expect(screen.queryByRole('table', { name: 'Concordância da revisão por sinal' })).not.toBeInTheDocument()
  })

  it('shows the tokens each agent stopped receiving and what it left out, with the evidence', async () => {
    const lean = { ...VOICE, trimmed_deliveries: 640, tokens_saved: 58_300 }
    const trims = [
      {
        source_id: SRC_VOICE,
        view: 'voice',
        active: true,
        inactive_reason: null,
        units: [
          { unit: 'fact:contract', kind: 'fact', category: 'contract', deliveries: 212 },
          { unit: 'episode:billing', kind: 'episode', category: 'billing', deliveries: 97 },
        ],
        derived_at: '2026-09-22T17:20:00Z',
        window_days: 28,
        min_deliveries: 50,
      },
      { source_id: SRC_BILLING, view: 'task:billing', active: false, inactive_reason: 'opt_out', units: [{ unit: 'object:order', kind: 'object', category: 'order', deliveries: 60 }], derived_at: '2026-09-22T17:20:00Z', window_days: 28, min_deliveries: 50 },
    ]
    mockApi(routes({ [`GET ${API}/v1/context-use`]: { since: '', until: '', group_by: ['source_id'], buckets: [lean, BILLING], trims } }))
    await signInAt(at('uso'))
    const table = await screen.findByRole('table', { name: /Aproveitamento do contexto por agente/ })
    expect(within(table).getByRole('row', { name: /Agente de voz/ })).toHaveTextContent('58.300 tokensem 640 entregas')
    expect(within(table).getByRole('row', { name: /Agente de cobrança/ })).toHaveTextContent('nada enxugado')
    expect(screen.getByRole('heading', { name: 'O que cada agente deixou de receber' })).toBeInTheDocument()
    const [voice, billing] = screen.getAllByRole('list', { name: 'Tipos de item fora do contexto' })
    expect(voice).toHaveTextContent('Fato · Contrato · 212 entregas sem uso')
    expect(voice).toHaveTextContent('Episódio · Cobrança · 97 entregas sem uso')
    expect(billing).toHaveTextContent('Objeto de negócio · Pedido')
    expect(screen.getByText(/Em vigor desde 22\/09\/2026/)).toHaveTextContent('Medido em pelo menos 50 entregas dos últimos 28 dias.')
    expect(screen.getByText(/Esta fonte está fora do enxugamento/)).toBeInTheDocument()
    expect(screen.getByText(/visão tarefa billing/)).toBeInTheDocument()
  })

  it('says when no agent has anything left out', async () => {
    mockApi(routes({ [`GET ${API}/v1/context-use`]: { since: '', until: '', group_by: ['source_id'], buckets: [VOICE], trims: [] } }))
    await signInAt(at('uso'))
    expect(await screen.findByText(/Nenhum agente tem itens de fora agora/)).toBeInTheDocument()
  })

  it('shows the slots by channel: what each retrieval channel brought, how much was used and its weight today', async () => {
    const slot = (c: Record<string, unknown>) => ({ view: 'chat', turns: 0, skipped: 0, slots: 0, used: 0, repeated: 0, contradicted: 0, usage_rate: null, ...c })
    const slot_channels = [
      slot({ slot_channel: 'semantic', turns: 400, skipped: 37, slots: 210, used: 21, usage_rate: { value: 0.1, low: 0.066, high: 0.148, n: 210 }, weight: 0.9, default_weight: 1 }),
      slot({ slot_channel: 'values', turns: 380, slots: 300, used: 240, contradicted: 2, usage_rate: { value: 0.8, low: 0.751, high: 0.842, n: 300 }, weight: 2.2, default_weight: 2 }),
      slot({ view: 'voice', slot_channel: 'lexical', slots: 0, weight: 1, default_weight: 1 }),
    ]
    const retrieval_weights = {
      version: 7,
      weights: { chat: { values: 2.2, semantic: 0.9 } },
      defaults: { exact: 2, values: 2, lexical: 1, temporal: 1, semantic: 1 },
      runs: [{ day: '2026-09-25', computed_at: '2026-09-25T06:10:00Z', version: 7, outcome: 'vetoed', guard: 'worse' }],
    }
    mockApi(routes({ [`GET ${API}/v1/context-use`]: { since: '', until: '', group_by: ['source_id'], buckets: [VOICE], slot_channels, retrieval_weights } }))
    await signInAt(at('uso'))
    const table = await screen.findByRole('table', { name: 'Encaixes por visão e canal de busca' })
    const rows = within(table).getAllByRole('row').slice(1)
    expect(rows.map((r) => within(r).getByRole('rowheader').textContent)).toEqual(['Valores fixadosvisão Chat', 'Significadovisão Chat', 'Palavras em comumvisão Voz'])
    expect(rows[0]).toHaveTextContent('300')
    expect(rows[0]).toHaveTextContent(`${fmtPct(0.8)}${fmtPct(0.751)} a ${fmtPct(0.842)} · n 300`)
    expect(rows[0]).toHaveTextContent('2,20inicial 2,00')
    expect(rows[1]).toHaveTextContent('37 de 400')
    expect(rows[1]).toHaveTextContent('0,90inicial 1,00')
    expect(rows[2]).toHaveTextContent('não rodou')
    expect(rows[2]).toHaveTextContent('não medido')
    expect(
      screen.getByText(/Última revisão dos pesos em 25\/09\/2026: pesos mantidos pelo grupo de controle, porque os clientes com memória foram perguntados de novo mais vezes/),
    ).toHaveTextContent('Pesos na versão 7.')
  })

  it('leaves the channels out when the report brings neither slots nor weights', async () => {
    mockApi(routes({ [`GET ${API}/v1/context-use`]: { since: '', until: '', group_by: ['source_id'], buckets: [VOICE], slot_channels: [], retrieval_weights: null } }))
    await signInAt(at('uso'))
    await screen.findByRole('table', { name: /Aproveitamento do contexto por agente/ })
    expect(screen.queryByRole('heading', { name: 'Encaixes por canal de busca' })).not.toBeInTheDocument()
  })

  it('says when no slot was measured yet and the weights are the initial ones', async () => {
    const retrieval_weights = { version: 0, weights: {}, defaults: { exact: 2, values: 2, lexical: 1, temporal: 1, semantic: 1 }, runs: [] }
    mockApi(routes({ [`GET ${API}/v1/context-use`]: { since: '', until: '', group_by: ['source_id'], buckets: [VOICE], slot_channels: [], retrieval_weights } }))
    await signInAt(at('uso'))
    expect(await screen.findByRole('heading', { name: 'Encaixes por canal de busca' })).toBeInTheDocument()
    expect(screen.getByText('Nenhum encaixe medido no período.')).toBeInTheDocument()
    expect(screen.getByText(/ainda não passaram por nenhuma revisão noturna/)).toHaveTextContent('Pesos iniciais em uso.')
  })

  it("shows each agent's prompt cache: tokens, hits and the saving labeled as an estimate", async () => {
    const api = mockApi(routes())
    const { user } = await signInAt(at('uso'))
    const table = await screen.findByRole('table', { name: 'Cache de prompt do provedor por agente' })
    const voice = within(table).getByRole('row', { name: /Agente de voz/ })
    expect(within(voice).getAllByRole('cell').map((c) => c.textContent)).toEqual(['1.240', '3.720.000', '2.540.000', '68,3%', 'US$\u00a03,81'])
    const billing = within(table).getByRole('row', { name: /Agente de cobrança/ })
    expect(billing).toHaveTextContent('sem preço na tabela')
    expect(screen.getByText(/Estimativa: tokens lidos do cache pelo preço de entrada menos o preço de cache/)).toHaveTextContent(
      '80 chamadas de modelos sem preço na tabela ficam fora da estimativa.',
    )
    expect(screen.getByText(/openai\/gpt-4.1 · entrada US\$ 2 · cache US\$ 0.5/)).toBeInTheDocument()
    const q = api.find('GET', '/v1/usage/prompt-cache')[0]!.url.searchParams
    expect(q.getAll('group_by')).toEqual(['source_id'])
    expect(q.get('since')).toMatch(/^\d{4}-\d{2}-\d{2}$/)
    await user.click(within(screen.getByRole('radiogroup', { name: 'Agrupar o cache por' })).getByText('Por fornecedor'))
    const vendors = await screen.findByRole('table', { name: 'Cache de prompt do provedor por fornecedor' })
    expect(within(vendors).getByRole('row', { name: /Fornecedor B/ })).toHaveTextContent('68,3%')
  })

  it('says how the usage arrives when no model call reported it', async () => {
    mockApi(routes({ [`GET ${API}/v1/usage/prompt-cache`]: { ...CACHE, buckets: [], prices: [] } }))
    await signInAt(at('uso'))
    expect(await screen.findByText('Nenhuma chamada ao modelo informou uso no período.')).toBeInTheDocument()
    expect(screen.getByText(/o wrap\(\) dos SDKs lê o que o provedor devolve em cada chamada/)).toBeInTheDocument()
  })

  it('keeps the rest of the screen when the prompt cache fails', async () => {
    mockApi(routes({ [`GET ${API}/v1/usage/prompt-cache`]: problem(503, 'unavailable') }))
    await signInAt(at('uso'))
    expect(await screen.findByText('A região deste espaço pediu um instante.')).toBeInTheDocument()
    expect(await screen.findByRole('table', { name: 'Uso por agente' })).toBeInTheDocument()
    expect(screen.getByRole('table', { name: /Aproveitamento do contexto por agente/ })).toBeInTheDocument()
  })

  it('keeps usage visible when the measurement fails', async () => {
    mockApi(routes({ [`GET ${API}/v1/context-use`]: problem(503, 'unavailable') }))
    await signInAt(at('uso'))
    expect(await screen.findByText('A região deste espaço pediu um instante.')).toBeInTheDocument()
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
    expect(screen.getByRole('table', { name: 'Uso do espaço por métrica' })).toBeInTheDocument()
    expect(screen.getByRole('table', { name: 'Uso por agente' })).toBeInTheDocument()
  })
})
