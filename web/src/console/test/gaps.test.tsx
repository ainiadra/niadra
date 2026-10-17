import { screen, within } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import type { GapsReport } from '../api/types'
import { API, at, baseRoutes, choose, CONTROL, mockApi, SP_PROD, SRC_BILLING, signInAt } from './harness'

const REPORT: GapsReport = {
  since: '2026-08-27',
  until: '2026-09-26',
  gaps: [
    {
      key: 'no_record:refund:billing',
      kind: 'no_record',
      subject: 'refund',
      topic: 'billing',
      customers: 132,
      turns: 210,
      could_record: [],
      answered_share: null,
      review: null,
    },
    {
      key: 'no_record:invoice:billing',
      kind: 'no_record',
      subject: 'invoice',
      topic: 'billing',
      customers: 40,
      turns: 51,
      could_record: [{ source_id: SRC_BILLING, name: 'Agente de cobrança', via: 'mapping' }],
      answered_share: 0.82,
      review: { decision: 'dismissed', decided_by: 'person:u2', decided_at: '2026-09-25T12:00:00Z', note: null },
    },
    {
      key: 'object_unresolved:lawsuit:legal',
      kind: 'object_unresolved',
      subject: 'lawsuit',
      topic: 'legal',
      customers: 9,
      turns: 14,
      could_record: [],
      answered_share: null,
      review: null,
    },
    {
      key: 'agent_question:address:delivery',
      kind: 'agent_question',
      subject: 'address',
      topic: 'delivery',
      customers: 18,
      turns: 20,
      could_record: [],
      answered_share: null,
      review: null,
    },
  ],
  recurring: [{ key: 'answered:protocol:support', subject: 'protocol', topic: 'support', customers: 300, turns: 900, answered_share: 0.97, review: null }],
  proposals: [
    {
      key: 'proposal:not_recorded:refund:billing',
      kind: 'not_recorded',
      subject: 'refund',
      topic: 'billing',
      text: 'Nenhum sistema envia refund para a memória: a falta de registro não quer dizer que o cliente não tenha refund; consulte o sistema de origem antes de responder.',
      review: null,
    },
  ],
  withheld_groups: 4,
}

describe('Knowledge gaps', () => {
  const routes = (extra = {}, roles: Parameters<typeof baseRoutes>[1] = ['analysis']) =>
    baseRoutes({ [`GET ${API}/v1/insights/gaps`]: REPORT, ...extra }, roles)

  it('lists the gaps by subject and category, with who could record them, and never a customer', async () => {
    const api = mockApi(routes())
    await signInAt(at('lacunas'))
    const table = await screen.findByRole('table', { name: 'Lacunas de conhecimento' })
    const rows = within(table).getAllByRole('row')
    expect(within(rows[1]).getByText('refund')).toBeInTheDocument()
    expect(within(rows[1]).getByText('132')).toBeInTheDocument()
    expect(within(rows[1]).getByText('Nenhuma fonte grava')).toBeInTheDocument()
    expect(within(rows[2]).getByText('fatura')).toBeInTheDocument()
    expect(within(rows[2]).getByText('Agente de cobrança · mapeamento')).toBeInTheDocument()
    expect(within(rows[2]).getByText('Dispensada')).toBeInTheDocument()
    expect(within(rows[3]).getByText('o agente precisou perguntar ao cliente')).toBeInTheDocument()
    expect(screen.getByText(/4 grupos ficaram de fora/)).toBeInTheDocument()
    expect(within(screen.getByRole('table', { name: 'Perguntas recorrentes' })).getByText('protocolo')).toBeInTheDocument()
    expect(api.find('GET', '/v1/insights/gaps')[0].url.searchParams.get('days')).toBe('30')
    // An object a conversation named by its key and no system sent: apart, by type.
    expect(within(table).queryByText('lawsuit')).not.toBeInTheDocument()
    const unresolved = screen.getByRole('table', { name: 'Objetos citados sem cadastro' })
    expect(within(unresolved).getByText('14')).toBeInTheDocument()
  })

  it('records the team accepting a proposed standing answer', async () => {
    const api = mockApi(routes({ [`POST ${API}/v1/insights/gaps/review`]: { decision: 'accepted', decided_by: 'person:u1', decided_at: '2026-09-26T09:00:00Z', note: null } }))
    const { user } = await signInAt(at('lacunas'))
    const proposals = await screen.findByRole('list', { name: 'Respostas prontas propostas' })
    expect(within(proposals).getByText(/Nenhum sistema envia refund/)).toBeInTheDocument()
    await user.click(within(proposals).getByRole('button', { name: 'Aceitar a resposta pronta sobre refund' }))
    expect(await within(proposals).findByText('Aceita')).toBeInTheDocument()
    expect(api.find('POST', '/v1/insights/gaps/review')[0].body).toEqual({ key: 'proposal:not_recorded:refund:billing', decision: 'accepted' })
  })

  it('says what each list needs when the period has nothing, with an example marked as such', async () => {
    mockApi(routes({ [`GET ${API}/v1/insights/gaps`]: { since: '2026-08-31', withheld_groups: 2, gaps: [], recurring: [], proposals: [] } }))
    await signInAt(at('lacunas'))
    expect(await screen.findByText('Nenhuma lacuna com clientes suficientes no período.')).toBeInTheDocument()
    expect(screen.getByText(/fica de fora \(2 grupos neste período\)\. Com poucas conversas, amplie o período para 90 dias/)).toBeInTheDocument()
    expect(screen.getByText('Nenhuma pergunta recorrente com clientes suficientes no período.')).toBeInTheDocument()
    expect(screen.getByText('Nenhuma resposta pronta proposta no período.')).toBeInTheDocument()
    expect(screen.getAllByText('exemplo')).toHaveLength(3)
    expect(screen.getByText(/rastreio · entrega · 23 clientes · 31 turnos/)).toBeInTheDocument()
  })

  it('asks again for another period', async () => {
    const api = mockApi(routes())
    const { user } = await signInAt(at('lacunas'))
    await screen.findByRole('table', { name: 'Lacunas de conhecimento' })
    await choose(user, screen.getByLabelText('Período'), '90')
    await screen.findByRole('table', { name: 'Lacunas de conhecimento' })
    expect(api.find('GET', '/v1/insights/gaps').map((c) => c.url.searchParams.get('days'))).toContain('90')
  })

  it('says the screen needs the analysis role', async () => {
    const api = mockApi(routes({}, ['security']))
    await signInAt(at('lacunas'))
    expect(await screen.findByText('As lacunas exigem o papel Análise.')).toBeInTheDocument()
    expect(api.find('GET', '/v1/insights/gaps')).toHaveLength(0)
  })
})

describe('Objection to a purpose', () => {
  const purposes = {
    space_id: SP_PROD,
    type: 'purposes',
    version: 2,
    document: {
      version: '2',
      purposes: [
        { name: 'cobranca', legal_basis: 'contract', retention_days: 1825 },
        { name: 'marketing', legal_basis: 'consent', retention_days: 180 },
      ],
    },
  }
  const routes = (extra = {}) =>
    baseRoutes({
      [`GET ${API}/v1/forget`]: { items: [], next_cursor: null },
      [`GET ${API}/v1/operations/dead-tasks`]: { items: [] },
      [`GET ${CONTROL}/v1/config/purposes`]: purposes,
      ...extra,
    }, ['security'])

  it('sends the objection as a forget with the purpose, and says what left and what stayed', async () => {
    const api = mockApi(
      routes({
        [`POST ${API}/v1/forget`]: {
          request_id: '0192a000-0000-8000-8000-0000000000r1',
          status: 'completed',
          target_kind: 'profile',
          target_id: '0192a000-0000-7000-8000-0000000000c1',
          requested_at: '2026-09-26T10:00:00Z',
          completed_at: '2026-09-26T10:00:00Z',
          erased: { episodes: 2, settled_values: 1, purpose_narrowed: 3 },
          export_run_ids: [],
          receipt_hash: null,
        },
      }),
    )
    const { user } = await signInAt(at('dados'))
    const form = await screen.findByRole('form', { name: 'Oposição a uma finalidade' })
    await choose(user, within(form).getByLabelText('Finalidade'), 'marketing')
    await user.type(within(form).getByLabelText('ID do perfil'), '0192a000-0000-7000-8000-0000000000c1')
    await user.click(within(form).getByLabelText('Entendo que o que foi colhido só para essa finalidade sai de vez'))
    await user.click(within(form).getByRole('button', { name: 'Registrar a oposição' }))
    expect(await within(form).findByText(/3 registros apagados · 3 itens continuam para outras finalidades/)).toBeInTheDocument()
    const [sent] = api.find('POST', '/v1/forget')
    expect(sent.body).toEqual({ target: 'profile', profile_id: '0192a000-0000-7000-8000-0000000000c1', purpose: 'marketing' })
    expect(sent.headers.get('Idempotency-Key')).toBeTruthy()
  })

  it('points to the configuration when the space declares no purpose', async () => {
    mockApi(routes({ [`GET ${CONTROL}/v1/config/purposes`]: { ...purposes, version: 0, document: { purposes: [] } } }))
    await signInAt(at('dados'))
    expect(await screen.findByText('A oposição precisa de uma finalidade declarada.')).toBeInTheDocument()
    // What it needs, the one action, and an example marked as such.
    expect(screen.getByRole('link', { name: 'Declarar as finalidades em Configuração' })).toHaveAttribute('href', expect.stringMatching(/\/configuracao\?documento=purposes$/))
    expect(screen.getByText('exemplo')).toBeInTheDocument()
    expect(screen.getByText(/finalidade vendas, interesse legítimo/)).toBeInTheDocument()
  })
})
