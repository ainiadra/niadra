import { screen, within } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import type { AgentRates, AttributionReport, CounterfactualRun, ExperimentReport, InterleavingReport, OutcomeLink, ReconcileResult } from '../api/measure'
import { API, at, baseRoutes, mockApi, problem, signInAt, SRC_VOICE, type Handler } from './harness'

const LINES: OutcomeLink[] = [
  {
    link_id: 'l1', outcome: 'purchase', object: 'order:shop:8812', line: '1', state: 'delivered', method: 'line', band: 'deterministic',
    finality: 'provisional', value_minor: 19990, currency: 'BRL', exposure_id: 'e1', position: 1, agent: 'stylist',
    valid_at: '2026-09-22T17:00:00Z', known_at: '2026-09-22T17:00:05Z',
  },
  {
    link_id: 'l2', outcome: 'purchase', object: 'order:shop:8812', line: '2', state: 'delivered', method: 'identity', band: 'probable',
    finality: 'provisional', value_minor: 14990, currency: 'BRL', agent: 'voice', confidence: 0.82,
    valid_at: '2026-09-22T17:00:00Z', known_at: '2026-09-22T17:00:05Z',
  },
]
const ATTRIBUTION: AttributionReport = {
  since: '2026-09-01',
  until: '2026-09-29',
  rows: [],
  totals: [
    { outcome: 'purchase', method: 'line', band: 'deterministic', finality: 'provisional', currency: 'BRL', outcomes: 1, lines: 1, value_minor: 19990 },
    { outcome: 'purchase', method: 'identity', band: 'probable', finality: 'provisional', currency: 'BRL', outcomes: 1, lines: 1, value_minor: 14990 },
  ],
  identity: { agreeing: 17, compared: 20, rate: 0.85 },
}
const EXPERIMENTS: ExperimentReport = {
  as_of: '2026-09-29T12:00:00Z',
  experiments: [
    {
      experiment: 'soft_on', kind: 'element', element: 'soft', starts_at: '2026-09-01T00:00:00Z', alpha: 0.05, cuped_days: 90,
      arms: [
        { arm: 'control', people: 200, converted: 34, outcomes: 34, value_minor: 272000 },
        { arm: 'on', people: 200, converted: 67, outcomes: 67, value_minor: 536000 },
      ],
      comparisons: [
        { arm: 'on', metric: 'conversion', control_mean: 0.17, arm_mean: 0.335, difference: 0.16, relative: 0.94, ci_low: 0.05, ci_high: 0.27,
          variance_reduction: 0.12, p_value: 0.0021, decided: true, looks: 3 },
      ],
    },
  ],
}
const INTERLEAVING: InterleavingReport = { since: '', until: '', rows: [], totals: [{ experiment: 'rank_v2', tool: 'search', impressions: 120, wins_a: 30, wins_b: 50, ties: 40, p_value: 0.033 }] }
const RUN: CounterfactualRun = {
  run_id: 'r1', tool: 'search_items', element: 'hard', label: 'ci-7f3c2a1', created_at: '2026-09-29T11:00:00Z', cases: 41, completed: 40,
  skipped: { no_dry_run: 1 }, overlap: 0.5, noise_floor: 0.95, effect: 0.45, below_noise: 40, above_noise: 0, ties: 0, p_value: 0,
  engaged: { items: 40, shown: 40, kept: 30, lost: 10, gained: 2, mean_shift: 2 },
  limits: ['not_quality', 'model_reaction_not_measured', 'trivial_for_hard', 'cases_skipped'],
}
const RATES: AgentRates = {
  claims: [{ source_id: SRC_VOICE, agent: 'closing', turns: 3, claims: 2, evidenced: 1, categories: { price: { matched: 1, unsupported: 1 } } }],
  constraints: [
    { source_id: SRC_VOICE, agent: 'closing', turns: 3, sent: 3, checked: 15, verifiable: 13, violated: 1, unverifiable: 2, relaxed_declared: 1, relaxed_suspected: 0,
      items_presented: 4, violations_per_1000_items: 250 },
  ],
  cost: [
    { source_id: SRC_VOICE, agent: 'closing', turns: 3, cost_per_turn_usd: 0.0233, model_calls_per_turn: 0.667, tool_calls_per_turn: 0.667,
      tokens_in_per_turn: 8000, tokens_cached_per_turn: 7333, tokens_out_per_turn: 60, memory_off_cost_per_turn_usd: 0.036, difference_usd: -0.0127 },
  ],
}

const routes = (over: Record<string, Handler> = {}) =>
  baseRoutes({
    [`GET ${API}/v1/measure/attribution`]: ATTRIBUTION,
    [`GET ${API}/v1/measure/outcomes`]: { items: LINES },
    [`GET ${API}/v1/measure/experiments`]: EXPERIMENTS,
    [`GET ${API}/v1/measure/interleaving`]: INTERLEAVING,
    [`GET ${API}/v1/measure/counterfactual-runs`]: { items: [RUN] },
    [`GET ${API}/v1/context-use`]: RATES,
    ...over,
  })

describe('Outcomes and experiments', () => {
  it('shows every line with its method, the experiments, the counterfactual limits, the cost and the rates', async () => {
    mockApi(routes())
    await signInAt(at('medicao'))
    const lines = await screen.findByRole('list', { name: 'Linhas atribuídas, com o método de cada uma' })
    const items = within(lines).getAllByRole('listitem')
    expect(within(items[0]!).getByText('Por linha')).toBeInTheDocument()
    expect(within(items[1]!).getByText('Por identidade')).toBeInTheDocument()
    expect(within(items[1]!).getByText('confiança 82,0%')).toBeInTheDocument()
    expect(screen.getByText(/acerta 85,0% das 20 linhas/)).toBeInTheDocument()
    const experiment = await screen.findByRole('article', { name: 'Experimento soft_on' })
    expect(within(experiment).getByText('Diferença real')).toBeInTheDocument()
    expect(within(experiment).getByText(/200 pessoas · 34 converteram/)).toBeInTheDocument()
    const run = await screen.findByRole('article', { name: 'Rodada ci-7f3c2a1' })
    expect(within(run).getByText('Diz se o elemento muda a lista, não se a lista ficou melhor.')).toBeInTheDocument()
    expect(within(run).getByText('Uma restrição dura muda a lista por construção: a mudança já era esperada.')).toBeInTheDocument()
    expect(screen.getByRole('table', { name: 'Custo por turno de cada agente' })).toHaveTextContent('Agente de voz')
    expect(screen.getByRole('table', { name: 'Afirmações com evidência por agente' })).toHaveTextContent('50,0%')
    expect(screen.getByRole('table', { name: 'Intercalação por experimento e ferramenta' })).toHaveTextContent('rank_v2')
  })

  it('uploads the BI file to storage by its digest and shows the gap against the period', async () => {
    const put = vi.fn(async () => new Response(null, { status: 200 }))
    const result: ReconcileResult = {
      report_id: 'rep1', deviation: 0.0013, tolerance: 0.02, within_tolerance: true, matched: 4, only_ours: 1, only_bi: 1,
      ours_minor: 75070, bi_minor: 74970, currency: 'BRL', worst: [{ order: 'e2e-8813', line: '1', ours_minor: 9090, bi_minor: 8990 }],
    }
    const api = mockApi(
      routes({
        [`POST ${API}/v1/measure/reconcile/uploads`]: { upload_id: 'u1', upload_url: 'https://objects.test/u1', upload_headers: { 'x-amz-checksum-sha256': 'x' }, expires_at: '2026-09-29T12:15:00Z' },
        [`POST ${API}/v1/measure/reconcile`]: result,
        'PUT https://objects.test/u1': put as unknown as Handler,
      }),
    )
    const { user } = await signInAt(at('medicao'))
    const file = new File(['order,line,value,currency\ne2e-8813,1,89.90,BRL\n'], 'bi.csv', { type: 'text/csv' })
    await user.upload(await screen.findByLabelText('Arquivo do BI'), file)
    await user.click(screen.getByRole('button', { name: 'Comparar com o período' }))
    expect(await screen.findByText('Diferença de 0,1%')).toBeInTheDocument()
    expect(screen.getByText(/dentro da tolerância de 2,0%/)).toBeInTheDocument()
    const [upload] = api.find('POST', '/v1/measure/reconcile/uploads')
    expect(upload!.body.size_bytes).toBe(file.size)
    expect(upload!.body.sha256).toMatch(/^[0-9a-f]{64}$/)
    expect(api.find('POST', '/v1/measure/reconcile')[0]!.body.file).toEqual({ format: 'csv', upload_id: 'u1' })
    expect(put).toHaveBeenCalled()
  })

  it('says when measurement is off, and when nothing was measured yet', async () => {
    mockApi(
      routes({
        [`GET ${API}/v1/measure/attribution`]: problem(404, 'not_found'),
        [`GET ${API}/v1/measure/outcomes`]: { items: [] },
        [`GET ${API}/v1/measure/experiments`]: { as_of: '2026-09-29T12:00:00Z', experiments: [] },
        [`GET ${API}/v1/measure/counterfactual-runs`]: { items: [] },
        [`GET ${API}/v1/context-use`]: {},
      }),
    )
    await signInAt(at('medicao'))
    expect(await screen.findByText('A medição de desfechos está desligada neste espaço.')).toBeInTheDocument()
    expect(await screen.findByText('Nenhum experimento de elemento ou de agente declarado.')).toBeInTheDocument()
    expect(screen.getByText('Nenhuma rodada enviada ainda.')).toBeInTheDocument()
    expect(screen.getByText('O registro de turnos está desligado neste espaço.')).toBeInTheDocument()
  })

  it('counts the period in UTC days, as the server does', async () => {
    vi.useFakeTimers({ toFake: ['Date'] })
    vi.setSystemTime(new Date('2026-10-01T01:30:00Z'))
    try {
      const api = mockApi(routes())
      await signInAt(at('medicao'))
      await screen.findByText(/De 02\/09\/2026 a 01\/10\/2026, em dias UTC, como o servidor conta: o dia de hoje termina às 21:00 no seu horário\./)
      for (const path of ['/v1/measure/attribution', '/v1/measure/interleaving', '/v1/context-use']) {
        const q = api.find('GET', path)[0]!.url.searchParams
        expect(q.get('since')).toBe('2026-09-02')
        expect(q.get('until')).toBe('2026-10-01')
      }
    } finally {
      vi.useRealTimers()
    }
  })

  it('says what the interleaving and the counterfactual need, with an example marked as such', async () => {
    mockApi(routes({ [`GET ${API}/v1/measure/interleaving`]: { since: '', until: '', rows: [], totals: [] }, [`GET ${API}/v1/measure/counterfactual-runs`]: { items: [] } }))
    await signInAt(at('medicao'))
    expect(await screen.findByText('Nenhuma lista intercalada no período.')).toBeInTheDocument()
    expect(screen.getByText(/A intercalação precisa de um experimento do tipo/)).toBeInTheDocument()
    expect(screen.getByText('Nenhuma rodada enviada ainda.')).toBeInTheDocument()
    expect(screen.getByText(/A rodada roda no CI da empresa, não aqui/)).toBeInTheDocument()
    expect(screen.getByText(/niadra counterfactual --tools/)).toBeInTheDocument()
    expect(screen.getAllByText('exemplo')).toHaveLength(2)
    expect(screen.getByText(/1\.240 impressões/)).toBeInTheDocument()
  })

  it('is for the analysis role', async () => {
    mockApi(baseRoutes({}, ['security']))
    await signInAt(at('medicao'))
    expect(await screen.findByText('Esta tela é do papel Análise.')).toBeInTheDocument()
  })
})
