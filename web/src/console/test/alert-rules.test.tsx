import { screen, within } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import type { DiffOut } from '../api/types'
import { API, at, baseRoutes, choose, CONTROL, mockApi, problem, signInAt, SP_PROD, SRC_BILLING, SRC_VOICE } from './harness'

const ENDPOINTS = {
  space_id: SP_PROD,
  type: 'webhook-endpoints',
  version: 3,
  document: [
    { endpoint_id: 'crm-hook', url: 'https://crm.example.com/niadra', source_id: SRC_VOICE, event_types: ['trigger.fired'], secret_refs: ['wh-1'], enabled: true },
    { endpoint_id: 'erp-hook', url: 'https://erp.example.com/niadra', source_id: SRC_BILLING, event_types: ['trigger.fired'], secret_refs: ['wh-2'], enabled: true },
  ],
}
const IN_USE = {
  rule_id: 'promessa.vencida',
  condition: 'promise.overdue',
  version: 'v2',
  enabled: true,
  endpoint_id: 'crm-hook',
  window_hours: 48,
  late_policy: 'suppress',
  // `note` came from a JSON diff: the form does not edit it and sends it back as it was.
  params: { days: 2, owner: null, late_after_minutes: 60, note: 'fila da cobrança' },
}
const RULES = { space_id: SP_PROD, type: 'trigger-rules', version: 2, document: [IN_USE] }
const DRY_RUN = { rule_id: 'x', window_days: 30, subjects_evaluated: 4000, would_fire: 17, target_problem: null, sample: [] }
const DIFF: DiffOut = {
  diff_id: 'd42',
  space_id: SP_PROD,
  type: 'trigger-rules',
  status: 'pending',
  reason: 'r',
  base_version: 2,
  author_id: 'u1',
  author_label: null,
  decided_by: null,
  decided_by_label: null,
  decided_at: null,
  applied_version: null,
  rollback_of: null,
  created_at: '2026-09-24T12:00:00Z',
  document: [],
}

const routes = (extra = {}, roles?: Parameters<typeof baseRoutes>[1]) =>
  baseRoutes(
    {
      [`GET ${API}/v1/triggers/firings`]: { items: [] },
      [`GET ${CONTROL}/v1/config/webhook-endpoints`]: ENDPOINTS,
      [`GET ${CONTROL}/v1/config/trigger-rules`]: RULES,
      [`GET ${CONTROL}/v1/config/trait-rules`]: { space_id: SP_PROD, type: 'trait-rules', version: 7, document: [{ rule_id: 'faturas.atrasadas', catalog: 'overdue_invoices', version: '1', enabled: true, params: {} }] },
      [`GET ${CONTROL}/v1/config/types`]: [{ type: 'trigger-rules', description: 'Conditions that notify an endpoint.', roles: ['integration'] }],
      [`POST ${API}/v1/triggers/dry-run`]: DRY_RUN,
      [`POST ${CONTROL}/v1/config/diffs`]: DIFF,
      ...extra,
    },
    roles,
  )

type User = Awaited<ReturnType<typeof signInAt>>['user']

async function openEditor(user: User) {
  await user.click(await screen.findByRole('tab', { name: 'Criar e testar' }))
  expect(await screen.findByRole('combobox', { name: 'Endpoint que recebe' })).toHaveTextContent(/crm-hook/)
}

async function testAndSend(user: User, reason: string) {
  await user.click(screen.getByRole('button', { name: 'Testar a regra' }))
  await screen.findByText(/perfis ativos nos últimos 30 dias disparariam a regra agora/)
  await user.type(screen.getByLabelText('Motivo'), reason)
  await user.click(screen.getByRole('button', { name: 'Enviar para aprovação' }))
}

describe('Alert rules without JSON', () => {
  it('builds a rule from the catalog, tests it and sends it for approval as a diff with a reason', async () => {
    const api = mockApi(routes())
    const { user } = await signInAt(at('avisos'))
    await openEditor(user)
    await choose(user, screen.getByRole('combobox', { name: 'Condição' }), 'trait.present')
    // The id follows the condition until the person types one.
    expect(screen.getByLabelText('ID da regra')).toHaveValue('padrao.presente')
    await choose(user, screen.getByRole('combobox', { name: 'Endpoint que recebe' }), 'erp-hook')
    await testAndSend(user, 'avisar a cobrança de faturas em atraso')

    expect(await screen.findByText('Regra padrao.presente, versão 1, enviada para aprovação.')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Abrir os diffs em Configuração' })).toHaveAttribute('href', `/console/e/${SP_PROD}/configuracao?aba=diffs`)
    const sent = api.find('POST', '/v1/config/diffs')[0]!.body
    expect(sent).toEqual({
      space_id: SP_PROD,
      type: 'trigger-rules',
      reason: 'avisar a cobrança de faturas em atraso',
      // The document of now, read again before sending, with the new rule at the end.
      document: [
        IN_USE,
        {
          rule_id: 'padrao.presente',
          condition: 'trait.present',
          version: '1',
          enabled: true,
          endpoint_id: 'erp-hook',
          window_hours: 24,
          late_policy: 'deliver_marked',
          params: { name: 'faturas.atrasadas', late_after_minutes: 120 },
        },
      ],
    })
    // What was tested is what was sent.
    expect(api.find('POST', '/v1/triggers/dry-run')[0]!.body).toMatchObject({ rule_id: 'padrao.presente', endpoint_id: 'erp-hook', params: { name: 'faturas.atrasadas' } })
  })

  it('asks for a test of the rule as it will go out, and never sends one the endpoint cannot receive', async () => {
    const api = mockApi(routes({ [`POST ${API}/v1/triggers/dry-run`]: { ...DRY_RUN, target_problem: 'audience_not_allowed' } }))
    const { user } = await signInAt(at('avisos'))
    await openEditor(user)
    await user.type(screen.getByLabelText('Motivo'), 'teste de envio')
    const send = screen.getByRole('button', { name: 'Enviar para aprovação' })
    expect(send).toBeDisabled()
    expect(screen.getByText('Teste a regra do jeito que ela vai sair antes de enviar.')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Testar a regra' }))
    expect(await screen.findByText('O endpoint não pode receber este aviso: o padrão não é liberado para a audiência da fonte do endpoint.')).toBeInTheDocument()
    expect(screen.getByText('Escolha outro endpoint ou mude a regra: do jeito que está, ela nunca chegaria ao endpoint.')).toBeInTheDocument()
    expect(send).toBeDisabled()

    // A change after the test asks for another test.
    await user.clear(screen.getByLabelText('Dias de atraso antes de avisar'))
    await user.type(screen.getByLabelText('Dias de atraso antes de avisar'), '3')
    expect(screen.getByText('Este resultado é da regra antes da última mudança. Teste de novo.')).toBeInTheDocument()
    expect(api.find('POST', '/v1/config/diffs')).toHaveLength(0)
  })

  it('refuses a new rule with an id in use, and points to the rule to adjust', async () => {
    mockApi(routes())
    const { user } = await signInAt(at('avisos'))
    await openEditor(user)
    const id = screen.getByLabelText('ID da regra')
    await user.clear(id)
    await user.type(id, 'promessa.vencida')
    expect(id).toHaveAttribute('aria-invalid', 'true')
    expect(id).toHaveAccessibleDescription('Outra regra já usa este ID. Escolha outro, ou ajuste a regra em Regras em uso.')
    expect(screen.getByRole('button', { name: 'Testar a regra' })).toBeDisabled()
  })

  it('adjusts a rule in use: its values in the form, the same id, the next version and the parameters kept', async () => {
    const api = mockApi(routes())
    const { user } = await signInAt(at('avisos'))
    await user.click(await screen.findByRole('tab', { name: 'Regras em uso' }))
    const item = (await screen.findByText('promessa.vencida')).closest('li')!
    expect(item).toHaveTextContent('Dias de atraso antes de avisar: 2 · De quem é a promessa: De qualquer um · Janela de 48 h · Atrasado depois de 60 min: não entrega')
    await user.click(within(item).getByRole('button', { name: 'Ajustar a regra promessa.vencida' }))

    expect(await screen.findByRole('heading', { name: 'Ajustar a regra promessa.vencida' })).toBeInTheDocument()
    expect(screen.getByText(/Versão v2 em uso\. Enviada, a regra sai como versão v3, com o mesmo ID\./)).toBeInTheDocument()
    expect(screen.getByLabelText('ID da regra')).toHaveAttribute('readonly')
    expect(screen.getByLabelText('Janela, em horas')).toHaveValue(48)
    expect(screen.getByRole('combobox', { name: 'De quem é a promessa' })).toHaveTextContent('De qualquer um')
    expect(screen.getByText(/Parâmetros fora do formulário, mantidos como estão/)).toHaveTextContent('note')
    await user.clear(screen.getByLabelText('Dias de atraso antes de avisar'))
    await user.type(screen.getByLabelText('Dias de atraso antes de avisar'), '5')
    await testAndSend(user, 'cinco dias de tolerância')

    expect(await screen.findByText('Regra promessa.vencida, versão v3, enviada para aprovação.')).toBeInTheDocument()
    expect(api.find('POST', '/v1/config/diffs')[0]!.body.document).toEqual([
      {
        rule_id: 'promessa.vencida',
        condition: 'promise.overdue',
        version: 'v3',
        enabled: true,
        endpoint_id: 'crm-hook',
        window_hours: 48,
        late_policy: 'suppress',
        params: { note: 'fila da cobrança', days: 5, owner: null, late_after_minutes: 60 },
      },
    ])
  })

  it('turns a rule off with a reason, as a diff of the document of now', async () => {
    const api = mockApi(routes())
    const { user } = await signInAt(at('avisos'))
    await user.click(await screen.findByRole('tab', { name: 'Regras em uso' }))
    await user.click(await screen.findByRole('button', { name: 'Desligar a regra promessa.vencida' }))
    const dialog = screen.getByRole('dialog', { name: 'Desligar a regra de aviso promessa.vencida' })
    await user.type(within(dialog).getByLabelText('Motivo'), 'o CRM está em manutenção')
    await user.click(within(dialog).getByRole('button', { name: 'Enviar para aprovação' }))
    expect(await within(dialog).findByText('Desligar a regra de aviso promessa.vencida: enviado para aprovação.')).toBeInTheDocument()
    expect(api.find('POST', '/v1/config/diffs')[0]!.body).toEqual({
      space_id: SP_PROD,
      type: 'trigger-rules',
      reason: 'o CRM está em manutenção',
      document: [{ ...IN_USE, enabled: false }],
    })
  })

  it("puts the control plane's refusal in words", async () => {
    const detail = 'trigger-rules padrao.presente: endpoint erp-hook cannot receive it (purpose_not_allowed); webhook-endpoints erp-hook: event_types.1 is not an event type Niadra sends'
    mockApi(routes({ [`POST ${CONTROL}/v1/config/diffs`]: () => problem(422, 'invalid_input', detail) }))
    const { user } = await signInAt(at('avisos'))
    await openEditor(user)
    await testAndSend(user, 'avisar a cobrança')
    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('A mudança deixaria uma regra sem efeito.')
    expect(alert).toHaveTextContent(
      'A regra de aviso padrao.presente não chegaria ao endpoint erp-hook: a regra do padrão não tem finalidade em comum com a fonte do endpoint. O endpoint erp-hook assina, na posição 2 da lista, um tipo de evento que a Niadra não envia.',
    )
  })

  it('sets the repetition rate in percent, for one source or each on its own, and tries it on the sources', async () => {
    const api = mockApi(
      routes({
        [`POST ${API}/v1/triggers/dry-run`]: { ...DRY_RUN, subjects_evaluated: 2, would_fire: 1, sample: [{ subject_id: SRC_VOICE, pseudonym: 'Agente de voz', evidence: [] }] },
      }),
    )
    const { user } = await signInAt(at('avisos'))
    await openEditor(user)
    await choose(user, screen.getByRole('combobox', { name: 'Condição' }), 'context_use.repetition_rate')
    expect(screen.getByRole('combobox', { name: 'Fonte vigiada' })).toHaveTextContent('Todas as fontes')
    const above = screen.getByLabelText('Repetição acima de (%)')
    expect(above).toHaveValue(20)
    await user.clear(above)
    await user.type(above, '100')
    expect(above).toHaveAccessibleDescription('Use um número de 0 a 99.')
    await user.clear(above)
    await user.type(above, '15')
    await user.click(screen.getByRole('button', { name: 'Testar a regra' }))
    // A source condition is tried on the space's sources now; the sample links to the agent.
    expect(await screen.findByText(/avisos sairiam agora, de 2 fontes do espaço\./)).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Agente de voz' })).toHaveAttribute('href', `/console/e/${SP_PROD}/agentes/${SRC_VOICE}`)
    expect(api.find('POST', '/v1/triggers/dry-run')[0]!.body.params).toEqual({ above: 0.15, window_days: 7, min_questions: 30, late_after_minutes: 120 })

    await choose(user, screen.getByRole('combobox', { name: 'Fonte vigiada' }), SRC_VOICE)
    await user.click(screen.getByRole('button', { name: 'Testar a regra' }))
    await vi.waitFor(() => expect(api.find('POST', '/v1/triggers/dry-run')).toHaveLength(2))
    expect(api.find('POST', '/v1/triggers/dry-run')[1]!.body.params).toEqual({ above: 0.15, window_days: 7, min_questions: 30, source_id: SRC_VOICE, late_after_minutes: 120 })
  })

  it('points to Systems when no endpoint exists yet', async () => {
    mockApi(routes({ [`GET ${CONTROL}/v1/config/webhook-endpoints`]: { ...ENDPOINTS, document: [] } }))
    const { user } = await signInAt(at('avisos'))
    await user.click(await screen.findByRole('tab', { name: 'Criar e testar' }))
    const hint = await screen.findByText(/Crie um endpoint em/)
    expect(within(hint).getByRole('link', { name: 'Sistemas' })).toHaveAttribute('href', `/console/e/${SP_PROD}/sistemas`)
    expect(screen.getByRole('button', { name: 'Testar a regra' })).toBeDisabled()
  })

  it('offers creation, adjustment and sending only to the roles that write the rules', async () => {
    mockApi(routes({}, ['review']))
    const { user } = await signInAt(at('avisos'))
    await user.click(await screen.findByRole('tab', { name: 'Regras em uso' }))
    expect(await screen.findByText('promessa.vencida')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Nova regra' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Ajustar a regra/ })).not.toBeInTheDocument()
    await user.click(screen.getByRole('tab', { name: 'Criar e testar' }))
    expect(await screen.findByText('O teste exige o papel Integração.')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Enviar para aprovação' })).not.toBeInTheDocument()
  })
})
