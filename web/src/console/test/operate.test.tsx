import { screen, within } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import type { DeliveryView, ReviewSample } from '../api/types'
import { API, at, baseRoutes, choose, CONTROL, mockApi, signInAt, SP_PROD, SRC_VOICE } from './harness'
import { PID } from './fixtures'

const ENDPOINTS = { space_id: SP_PROD, type: 'webhook-endpoints', version: 3, document: [{ endpoint_id: 'crm-hook', url: 'https://crm.example.com/niadra', source_id: SRC_VOICE, event_types: ['trigger.fired'], secret_refs: ['wh-1'], enabled: true }] }
const DEAD: DeliveryView = { delivery_id: 'd1', endpoint_id: 'crm-hook', event_type: 'trigger.fired', status: 'dead', attempts: 12, created_at: '2026-09-21T12:00:00Z', next_attempt_at: null, delivered_at: null, last_status_code: 502, last_error: 'bad gateway' }

describe('Alerts', () => {
  const routes = (extra = {}) =>
    baseRoutes({
      [`GET ${API}/v1/triggers/firings`]: { items: [{ firing_id: 'fi1', rule_id: 'promessa.vencida', rule_version: 'v2', subject_id: PID, window_key: '2026-09-22', fired_at: '2026-09-22T15:00:00Z', late: false, retracted_at: null, evidence: [{ kind: 'open_item', id: 'i1' }] }] },
      [`GET ${API}/v1/webhooks/deliveries`]: { items: [DEAD] },
      [`POST ${API}/v1/webhooks/deliveries/d1/redeliver`]: { ...DEAD, status: 'pending', attempts: 0 },
      [`GET ${CONTROL}/v1/config/webhook-endpoints`]: ENDPOINTS,
      [`GET ${CONTROL}/v1/config/trigger-rules`]: { space_id: SP_PROD, type: 'trigger-rules', version: 2, document: [{ rule_id: 'promessa.vencida', condition: 'promise.overdue', endpoint_id: 'crm-hook', version: 'v2' }] },
      [`POST ${API}/v1/triggers/dry-run`]: { rule_id: 'teste.promessa', window_days: 30, subjects_evaluated: 4000, would_fire: 17, target_problem: null, sample: [{ subject_id: PID, pseudonym: 'cli_7Q2M9', evidence: [] }] },
      [`GET ${CONTROL}/v1/config/trait-rules`]: { space_id: SP_PROD, type: 'trait-rules', version: 7, document: [{ rule_id: 'reclamacao.visita', catalog: 'recurring_complaint', version: 'v3', params: {} }] },
      ...extra,
    })

  /** Opens the dry run tab and waits for the endpoint list, which the form needs. */
  const openDryRun = async (user: Awaited<ReturnType<typeof signInAt>>['user']) => {
    await user.click(await screen.findByRole('tab', { name: 'Criar e testar' }))
    expect(await screen.findByRole('combobox', { name: 'Endpoint que recebe' })).toHaveTextContent(/crm-hook/)
  }

  it('lists firings with the rule, the profile and the evidence', async () => {
    const api = mockApi(routes())
    const { user } = await signInAt(at('avisos'))
    expect(await screen.findByText('promessa.vencida')).toBeInTheDocument()
    expect(screen.getByText('Pendência ou promessa i1')).toBeInTheDocument()
    await user.type(screen.getByLabelText('ID da regra'), 'promessa.vencida')
    await user.click(screen.getByRole('button', { name: 'Filtrar' }))
    expect(api.find('GET', '/v1/triggers/firings').at(-1)!.url.searchParams.get('rule_id')).toBe('promessa.vencida')
  })

  it('links a silent source firing to the agent screen, by the source name, and a profile firing to the profile', async () => {
    const SILENT = { firing_id: 'fi2', rule_id: 'fonte.parada', rule_version: 'v1', subject_id: SRC_VOICE, window_key: '24h:20718', fired_at: '2026-09-22T16:00:00Z', late: false, retracted_at: null, evidence: [{ kind: 'source', id: SRC_VOICE }] }
    const OVERDUE = { firing_id: 'fi1', rule_id: 'promessa.vencida', rule_version: 'v2', subject_id: PID, window_key: '24h:20718', fired_at: '2026-09-22T15:00:00Z', late: false, retracted_at: null, evidence: [{ kind: 'open_item', id: 'i1' }] }
    mockApi(routes({ [`GET ${API}/v1/triggers/firings`]: { items: [SILENT, OVERDUE] } }))
    await signInAt(at('avisos'))
    const table = await screen.findByRole('table', { name: 'Disparos de avisos' })
    // `source.silent` fires with the source as its subject (domain/triggers/firing.py): no profile has that id.
    expect(within(table).getByRole('link', { name: 'Agente de voz' })).toHaveAttribute('href', `/console/e/${SP_PROD}/agentes/${SRC_VOICE}`)
    expect(within(table).queryByRole('link', { name: SRC_VOICE })).not.toBeInTheDocument()
    const silent = within(table).getByRole('row', { name: /fonte\.parada/ })
    expect(within(silent).getAllByRole('cell')[4]).toHaveTextContent('Fonte Agente de voz')
    expect(within(table).getByRole('link', { name: PID })).toHaveAttribute('href', `/console/e/${SP_PROD}/clientes/${PID}`)
  })

  it('says once, in words, when every delivery on the page failed, and points to the endpoint in Systems', async () => {
    const failing: DeliveryView = { ...DEAD, delivery_id: 'd2', endpoint_id: 'erp-avisos', event_type: 'object.timer_due', status: 'failing', attempts: 9, next_attempt_at: '2026-10-01T14:22:49Z', last_status_code: null, last_error: 'Unavailable' }
    const api = mockApi(routes({ [`GET ${API}/v1/webhooks/deliveries`]: { items: [failing, { ...DEAD, endpoint_id: 'siem', last_error: 'endpoint_disabled' }] } }))
    await signInAt(at('avisos?aba=deliveries'))
    const note = (await screen.findByText('Nenhuma das 2 entregas desta página chegou.')).closest('[role=status]') as HTMLElement
    expect(note).toHaveTextContent('Os endpoints erp-avisos, siem não receberam nenhuma delas: o endereço não respondeu')
    expect(note).toHaveTextContent('(Unavailable)')
    expect(note).toHaveTextContent('o endpoint está desligado no documento de endpoints (endpoint_disabled)')
    expect(within(note).getByRole('link', { name: 'Conferir o endpoint em Sistemas' })).toHaveAttribute('href', expect.stringMatching(/\/sistemas$/))
    // The event types the cell sends and the last error read in Portuguese, with the code beside them.
    const table = screen.getByRole('table', { name: 'Entregas de webhook' })
    expect(table).toHaveTextContent('Um prazo de um objeto de negócio venceu')
    expect(table).toHaveTextContent('o endereço não respondeu')
    expect(api.find('GET', '/v1/webhooks/deliveries')).toHaveLength(1)
  })

  it('does not raise the note when a delivery of the page arrived', async () => {
    mockApi(routes({ [`GET ${API}/v1/webhooks/deliveries`]: { items: [DEAD, { ...DEAD, delivery_id: 'd3', status: 'delivered', delivered_at: '2026-09-21T12:00:01Z', last_status_code: 200, last_error: null }] } }))
    await signInAt(at('avisos?aba=deliveries'))
    await screen.findByRole('table', { name: 'Entregas de webhook' })
    expect(screen.queryByText(/entregas desta página chegou|desta página falhou/)).not.toBeInTheDocument()
  })

  it('says what a delivery needs when there is none, with an example marked as such', async () => {
    mockApi(routes({ [`GET ${API}/v1/webhooks/deliveries`]: { items: [] } }))
    await signInAt(at('avisos?aba=deliveries'))
    expect(await screen.findByText('Nenhuma entrega de webhook ainda.')).toBeInTheDocument()
    expect(screen.getByText(/cadastrar um endpoint em Sistemas/)).toBeInTheDocument()
    expect(screen.getByText('exemplo')).toBeInTheDocument()
  })

  it('redelivers a dead webhook delivery', async () => {
    const api = mockApi(routes())
    const { user } = await signInAt(at('avisos'))
    await user.click(await screen.findByRole('tab', { name: 'Entregas de webhook' }))
    expect(within(await screen.findByRole('table', { name: 'Entregas de webhook' })).getByText('Esgotou as tentativas')).toBeInTheDocument()
    expect(screen.getByText('HTTP 502')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Reenviar a entrega d1' }))
    expect(await screen.findByText('Na fila de novo')).toBeInTheDocument()
    expect(api.find('POST', '/v1/webhooks/deliveries/d1/redeliver')).toHaveLength(1)
  })

  it('dry-runs a trigger rule against a configured endpoint', async () => {
    const api = mockApi(routes())
    const { user } = await signInAt(at('avisos'))
    await user.click(await screen.findByRole('tab', { name: 'Criar e testar' }))
    // The endpoint list loaded: the dropdown shows the first endpoint as chosen.
    expect(await screen.findByRole('combobox', { name: 'Endpoint que recebe' })).toHaveTextContent(/crm-hook/)
    await user.click(screen.getByRole('button', { name: 'Testar a regra' }))
    expect(await screen.findByRole('status')).toHaveTextContent('17 de 4.000 perfis ativos nos últimos 30 dias disparariam a regra agora')
    expect(api.find('POST', '/v1/triggers/dry-run')[0]!.body).toEqual({
      // The id comes from the condition, free in the document: promessa.vencida is in use.
      rule_id: 'promessa.vencida.2',
      condition: 'promise.overdue',
      endpoint_id: 'crm-hook',
      window_hours: 24,
      late_policy: 'deliver_marked',
      // The defaults of domain/triggers/conditions.py, sent explicitly: no JSON to write.
      params: { days: 0, owner: 'company', late_after_minutes: 120 },
    })
  })

  it('builds the parameters of a pattern warning from the pattern rules in use, without JSON', async () => {
    const api = mockApi(routes())
    const { user } = await signInAt(at('avisos'))
    await openDryRun(user)
    expect(screen.queryByLabelText(/JSON/)).not.toBeInTheDocument()
    await choose(user, screen.getByRole('combobox', { name: 'Condição' }), 'trait.present')
    expect(await screen.findByRole('combobox', { name: 'Padrão' })).toHaveTextContent('reclamacao.visita (Reclamação recorrente)')
    await user.type(screen.getByLabelText('Valor do padrão (opcional)'), 'visita técnica')
    await user.click(screen.getByRole('button', { name: 'Testar a regra' }))
    await screen.findByRole('status')
    expect(api.find('POST', '/v1/triggers/dry-run')[0]!.body.params).toEqual({ name: 'reclamacao.visita', value: 'visita técnica', late_after_minutes: 120 })
  })

  it('sends an owner of any kind as null, and says a silent source is checked on the sources', async () => {
    const api = mockApi(routes())
    const { user } = await signInAt(at('avisos'))
    await openDryRun(user)
    await choose(user, screen.getByRole('combobox', { name: 'Condição' }), 'open_item.due')
    expect(screen.getByRole('combobox', { name: 'De quem é a pendência' })).toHaveTextContent('De qualquer um')
    await user.clear(screen.getByLabelText('Antecedência, em horas'))
    await user.type(screen.getByLabelText('Antecedência, em horas'), '48')
    await user.click(screen.getByRole('button', { name: 'Testar a regra' }))
    await screen.findByRole('status')
    expect(api.find('POST', '/v1/triggers/dry-run')[0]!.body.params).toEqual({ lead_hours: 48, owner: null, late_after_minutes: 120 })

    await choose(user, screen.getByRole('combobox', { name: 'Condição' }), 'source.silent')
    expect(screen.getByText(/Ligada, a regra confere as fontes a cada 5 minutos, e o teste confere as fontes agora\./)).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Testar a regra' }))
    await vi.waitFor(() => expect(api.find('POST', '/v1/triggers/dry-run')).toHaveLength(2))
    expect(api.find('POST', '/v1/triggers/dry-run')[1]!.body).toMatchObject({ condition: 'source.silent', params: { minutes: 30, late_after_minutes: 120 } })

    // What the SDK says it sent against what arrived, with the share in percent on screen.
    await choose(user, screen.getByRole('combobox', { name: 'Condição' }), 'source.gap')
    expect(screen.getByText(/compara a cada 5 minutos o que o SDK de cada fonte diz ter mandado/)).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Testar a regra' }))
    await vi.waitFor(() => expect(api.find('POST', '/v1/triggers/dry-run')).toHaveLength(3))
    expect(api.find('POST', '/v1/triggers/dry-run')[2]!.body).toMatchObject({ condition: 'source.gap', params: { min_missing: 10, share: 0.05 } })
  })

  it('keeps a parameter out of range from going out, with the reason on the field', async () => {
    const api = mockApi(routes())
    const { user } = await signInAt(at('avisos'))
    await openDryRun(user)
    const days = screen.getByLabelText('Dias de atraso antes de avisar')
    await user.clear(days)
    await user.type(days, '-1')
    expect(days).toHaveAttribute('aria-invalid', 'true')
    expect(days).toHaveAccessibleDescription('Use um número de 0 a 365.')
    expect(screen.getByRole('button', { name: 'Testar a regra' })).toBeDisabled()
    expect(api.find('POST', '/v1/triggers/dry-run')).toHaveLength(0)
  })

  it('says in words why the endpoint cannot receive the warning', async () => {
    mockApi(routes({ [`POST ${API}/v1/triggers/dry-run`]: { rule_id: 'teste.promessa', window_days: 30, subjects_evaluated: 10, would_fire: 2, target_problem: 'endpoint_not_subscribed', sample: [] } }))
    const { user } = await signInAt(at('avisos'))
    await openDryRun(user)
    await user.click(screen.getByRole('button', { name: 'Testar a regra' }))
    expect(await screen.findByText('O endpoint não pode receber este aviso: o endpoint não assina o evento trigger.fired.')).toBeInTheDocument()
  })

  it('shows the rules in use', async () => {
    mockApi(routes())
    const { user } = await signInAt(at('avisos'))
    await user.click(await screen.findByRole('tab', { name: 'Regras em uso' }))
    expect(await screen.findByText(/Promessa vencida/)).toBeInTheDocument()
  })
})

const SAMPLE: ReviewSample = {
  sample_id: 's1',
  session_id: 'se1',
  source_id: SRC_VOICE,
  channel: 'voice',
  condition: 'base',
  purpose: 'atendimento',
  created_at: '2026-09-22T18:00:00Z',
  facts: [{ fact_id: 'f1', predicate: 'preferred_channel', value: 'WhatsApp', category: 'preferência', evidence_event_ids: ['e1'], sensitive: false }],
  open_items: [{ item_id: 'oi1', description: 'Retornar com a segunda via', owner: 'company', promise: true, status: 'open', due_at: '2026-09-24T18:00:00Z', evidence_event_ids: ['e1'] }],
  traits: [{ trait_id: 't1', name: 'recurring_complaint', value: 'fatura', first_seen: '2026-09-20T10:00:00Z', confidence: 0.8, evidence: [{ kind: 'episode', id: '019a7c2e-5b1d-7f00-8000-0000000000e1' }] }],
  signals: [{ target_id: 'sig1', manifest_hash: 'm1', item_id: 'i1', signal: 'repeated' }],
  withheld: 2,
}

describe('Review', () => {
  const routes = (roles: Parameters<typeof baseRoutes>[1] = ['review']) =>
    baseRoutes(
      {
        [`GET ${API}/v1/review/queue`]: { items: [SAMPLE] },
        [`POST ${API}/v1/review/s1/verdicts`]: { sample_id: 's1', recorded: 2, reviewed_at: '2026-09-23T10:00:00Z' },
        [`GET ${API}/v1/review/agreement`]: { since: '2026-06-25T00:00:00Z', targets: [{ target: 'fact', judged: 120, agreed: 110, system_agreement: { value: 0.917, low: 0.855, high: 0.954, n: 120 }, reviewer_pairs: 0, reviewer_agreement: null }] },
        [`GET ${API}/v1/review/cases`]: { since: '2026-06-25T00:00:00Z', tallies: [], failing: [] },
      },
      roles,
    )

  it('sets aside the samples whose items the person may not see, and never offers to record zero verdicts', async () => {
    const empty = (id: string) => ({ ...SAMPLE, sample_id: id, condition: 'new_source', purpose: 'sales', channel: 'whatsapp', facts: [], open_items: [], traits: [], signals: [], withheld: 3 })
    mockApi({ ...routes(), [`GET ${API}/v1/review/queue`]: { items: [empty('s2'), SAMPLE, empty('s3')] } })
    const { user } = await signInAt(at('revisao'))
    // The one sample with items keeps its button, which never says "0 vereditos".
    expect(await screen.findByRole('button', { name: 'Gravar os vereditos' })).toBeDisabled()
    expect(screen.getAllByRole('button', { name: /Gravar/ })).toHaveLength(1)
    expect(screen.queryByText(/Gravar 0/)).not.toBeInTheDocument()
    const aside = screen.getByText('2 amostras só com itens fora da sua vista')
    await user.click(aside)
    const list = screen.getByRole('list', { name: 'Amostras só com itens fora da sua vista' })
    expect(within(list).getAllByRole('listitem')).toHaveLength(2)
    expect(list).toHaveTextContent('Agente de voz · Fornecedor A · WhatsApp · finalidade Vendas · fonte nova')
    expect(list).toHaveTextContent('3 itens fora da sua vista')
    expect(screen.getByText(/Quem pode ver esses itens julga essas amostras, e nenhum veredito seu fica em falta por elas/)).toBeInTheDocument()
  })

  it('says plainly when every sample holds only items the person may not see', async () => {
    const empty = { ...SAMPLE, sample_id: 's2', facts: [], open_items: [], traits: [], signals: [] }
    mockApi({ ...routes(), [`GET ${API}/v1/review/queue`]: { items: [empty, { ...empty, sample_id: 's3' }] } })
    await signInAt(at('revisao'))
    expect(await screen.findByText('Nenhuma amostra com algo para você julgar agora.')).toBeInTheDocument()
    expect(screen.getByText(/2 conversas sorteadas só têm itens fora da sua vista/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Gravar/ })).not.toBeInTheDocument()
  })

  it('records verdicts on facts and signals', async () => {
    const api = mockApi(routes())
    const { user } = await signInAt(at('revisao'))
    expect(await screen.findByText('2 itens ficam fora da sua vista pela finalidade desta sessão.')).toBeInTheDocument()
    await user.click(within(screen.getByRole('radiogroup', { name: 'Veredito sobre preferred_channel' })).getByText('Correto'))
    await user.click(within(screen.getByRole('radiogroup', { name: 'Veredito sobre Perguntou de novo' })).getByText('Não confere'))
    await user.click(screen.getByRole('button', { name: 'Gravar 2 vereditos' }))
    expect(await screen.findByText('2 vereditos gravados')).toBeInTheDocument()
    expect(api.find('POST', '/v1/review/s1/verdicts')[0]!.body).toEqual({
      verdicts: [
        { target: 'fact', target_id: 'f1', verdict: 'correct' },
        { target: 'signal', target_id: 'sig1', verdict: 'does_not_hold' },
      ],
    })
  })

  it('judges the promises and the patterns of the conversation, and says why it was sampled', async () => {
    const api = mockApi(routes())
    const { user } = await signInAt(at('revisao'))
    expect(await screen.findByText('Promessa da empresa')).toBeInTheDocument()
    expect(screen.getByText(/sorteio pela taxa base/)).toBeInTheDocument()
    await user.click(within(screen.getByRole('radiogroup', { name: 'Veredito sobre Retornar com a segunda via' })).getByText('Errado'))
    await user.click(within(screen.getByRole('radiogroup', { name: 'Veredito sobre recurring_complaint' })).getByText('Confere'))
    await user.click(screen.getByRole('button', { name: 'Gravar 2 vereditos' }))
    expect(await screen.findByText('2 vereditos gravados')).toBeInTheDocument()
    expect(api.find('POST', '/v1/review/s1/verdicts')[0]!.body).toEqual({
      verdicts: [
        { target: 'open_item', target_id: 'oi1', verdict: 'wrong' },
        { target: 'trait', target_id: 't1', verdict: 'holds' },
      ],
    })
  })

  it('shows agreement with its interval, and asks for the review role', async () => {
    mockApi(routes())
    await signInAt(at('revisao'))
    expect(await screen.findByText('91,7%')).toBeInTheDocument()
    expect(screen.getByText('85,5% a 95,4% · n 120')).toBeInTheDocument()
  })

  it('names every row of the agreement in words: each kind, each signal of the measurement and all verdicts', async () => {
    const row = (target: string, judged: number) => ({ target, judged, agreed: judged, system_agreement: { value: 1, low: 0.8, high: 1, n: judged }, reviewer_pairs: 0, reviewer_agreement: null })
    // The server sorts the kinds by name (domain/review/verdicts.py agreement); `signal:<name>` is one row per signal.
    const targets = ['all', 'fact', 'open_item', 'signal', 'signal:contradicted', 'signal:new_signal', 'signal:repeated', 'signal:selection_miss', 'signal:used', 'trait'].map((t, i) => row(t, i + 1))
    mockApi(baseRoutes({ [`GET ${API}/v1/review/queue`]: { items: [] }, [`GET ${API}/v1/review/agreement`]: { since: '2026-06-25T00:00:00Z', targets }, [`GET ${API}/v1/review/cases`]: { since: '2026-06-25T00:00:00Z', tallies: [], failing: [] } }, ['review']))
    await signInAt(at('revisao'))
    const table = await screen.findByRole('table', { name: /Concordância desde/ })
    expect(within(table).getAllByRole('rowheader').map((h) => h.textContent)).toEqual([
      'Fatos',
      'Pendências e promessas',
      'Sinais da medição',
      'Sinal: Aproveitado',
      'Sinal: Perguntou de novo',
      'Sinal: Contradisse a memória',
      'Sinal: Faltou no contexto',
      'Sinal: new_signal',
      'Padrões',
      'Todos os vereditos',
    ])
    expect(within(table).getByRole('row', { name: /Sinal: Aproveitado/ })).toHaveTextContent('n 9')
    expect(table).not.toHaveTextContent('signal:')
  })

  it('says what the agreement is good for, and runs each verdict as a test', async () => {
    mockApi(
      baseRoutes(
        {
          [`GET ${API}/v1/review/queue`]: { items: [] },
          [`GET ${API}/v1/review/agreement`]: { since: '2026-06-25T00:00:00Z', targets: [] },
          [`GET ${API}/v1/review/cases`]: {
            since: '2026-06-25T00:00:00Z',
            tallies: [{ target: 'signal:repeated', cases: 4, passing: 3, failing: 1 }],
            failing: [{ sample_id: 's9', target: 'signal', target_id: 'm1:i1:repeated', verdict: 'holds', now: 'not_marked', passes: false }],
          },
        },
        ['review'],
      ),
    )
    await signInAt(at('revisao'))
    expect(await screen.findByText(/a concordância diz quanto dá para confiar nos números./)).toBeInTheDocument()
    const table = await screen.findByRole('table', { name: /Casos de teste desde/ })
    expect(within(table).getByRole('row', { name: /Sinal: Perguntou de novo/ })).toHaveTextContent('431')
    expect(within(screen.getByRole('list', { name: 'Casos que falham hoje' })).getByText(/não marcado hoje/)).toBeInTheDocument()
  })

  it('explains the role when the person has no review role', async () => {
    mockApi(routes(['analysis']))
    await signInAt(at('revisao'))
    expect(await screen.findByText('A revisão por amostra exige o papel Revisão.')).toBeInTheDocument()
  })
})
