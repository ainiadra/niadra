import { screen, within } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import type { DiffOut } from '../api/types'
import { API, at, baseRoutes, choose, CONTROL, mockApi, problem, signInAt, SP_PROD } from './harness'
import { MEMORY } from './fixtures'

const PID = MEMORY.profile_id
const IN_USE = { rule_id: 'reclamacao.visita', catalog: 'recurring_complaint', version: 'v3', enabled: true, params: { category: 'visita_tecnica' }, purposes: [] }
const DIFF: DiffOut = {
  diff_id: 'd7',
  space_id: SP_PROD,
  type: 'trait-rules',
  status: 'pending',
  reason: 'r',
  base_version: 7,
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
  baseRoutes({
    [`GET ${CONTROL}/v1/config/trait-rules`]: { space_id: SP_PROD, type: 'trait-rules', version: 7, document: [IN_USE] },
    [`GET ${CONTROL}/v1/config/types`]: [{ type: 'trait-rules', description: 'Which behavior traits are derived.', roles: ['integration'] }],
    [`GET ${CONTROL}/v1/config/settings`]: { space_id: SP_PROD, type: 'settings', version: 3, document: { events_hot_days: 90 } },
    [`POST ${CONTROL}/v1/config/diffs`]: DIFF,
    [`POST ${API}/v1/traits/rules/dry-run`]: {
      rule_id: 'teste.reclamacao',
      window_days: 90,
      profiles_evaluated: 5000,
      profiles_matching: 42,
      sample: [{ profile_id: PID, pseudonym: 'p_7Q2M9', value: 'visita técnica', expires_at: '2026-12-21T12:00:00Z', evidence: [{ kind: 'episode', id: 'ep1' }] }],
    },
    [`GET ${API}/v1/profiles/${PID}/memory`]: MEMORY,
    [`POST ${API}/v1/profiles/${PID}/traits/opt-out`]: { profile_id: PID, opted_out_at: '2026-09-23T10:00:00Z' },
    ...extra,
  }, roles)

type User = Awaited<ReturnType<typeof signInAt>>['user']

async function testAndSend(user: User, reason: string) {
  await user.click(screen.getByRole('button', { name: 'Testar a regra' }))
  await screen.findByText(/clientes teriam o padrão nos últimos/)
  await user.type(screen.getByLabelText('Motivo'), reason)
  await user.click(screen.getByRole('button', { name: 'Enviar para aprovação' }))
}

describe('Patterns', () => {
  it('shows the rules in use from the trait-rules document', async () => {
    const api = mockApi(routes())
    await signInAt(at('padroes'))
    expect(await screen.findByText('reclamacao.visita')).toBeInTheDocument()
    expect(screen.getByText('Versão 7 da configuração.')).toBeInTheDocument()
    expect(api.find('GET', '/v1/config/trait-rules')[0]!.url.searchParams.get('space_id')).toBe(SP_PROD)
  })

  it('dry-runs a catalog rule with its default parameters', async () => {
    const api = mockApi(routes())
    const { user } = await signInAt(at('padroes'))
    await choose(user, await screen.findByLabelText('Padrão do catálogo'), 'overdue_invoices')
    await user.click(screen.getByRole('button', { name: 'Testar a regra' }))
    expect(await screen.findByText(/clientes teriam o padrão nos últimos 90 dias/)).toHaveTextContent('42 de 5.000')
    expect(screen.getByRole('link', { name: 'p_7Q2M9' })).toBeInTheDocument()
    expect(api.find('POST', '/v1/traits/rules/dry-run')[0]!.body).toEqual({
      // The id follows the catalog entry until the person types one.
      rule_id: 'faturas.em.atraso',
      catalog: 'overdue_invoices',
      params: { event_type: 'invoice.overdue', min_count: 2, window_days: 90 },
    })
  })

  it('asks for at least 2 in the count of a rule without single evidence, as the cell counts, and 1 where one piece of evidence is enough', async () => {
    const api = mockApi(routes())
    const { user } = await signInAt(at('padroes'))
    // Reclamação recorrente: the cell raises a count below 2 to 2 (rules.py _min_count).
    const count = await screen.findByLabelText('Quantas vezes, no mínimo')
    expect(count).toHaveAccessibleDescription('Um padrão pede repetição: no mínimo 2.')
    await user.clear(count)
    await user.type(count, '1')
    expect(count).toHaveAttribute('aria-invalid', 'true')
    expect(count).toHaveAccessibleDescription('Use um número inteiro de 2 a 100.')
    expect(screen.getByRole('button', { name: 'Testar a regra' })).toBeDisabled()
    for (const [catalog, field] of [
      ['preferred_channel', 'Conversas na janela, no mínimo'],
      ['does_not_answer_calls', 'Ligações sem resposta, no mínimo'],
      ['escalated_to_human', 'Quantas vezes, no mínimo'],
      ['overdue_invoices', 'Quantas vezes, no mínimo'],
    ] as const) {
      await choose(user, screen.getByLabelText('Padrão do catálogo'), catalog)
      await user.clear(screen.getByLabelText(field))
      await user.type(screen.getByLabelText(field), '1')
      expect(screen.getByLabelText(field)).toHaveAccessibleDescription(/^Use um número inteiro de 2 a /)
    }
    // Pediu cancelamento has single evidence: one request is enough.
    await choose(user, screen.getByLabelText('Padrão do catálogo'), 'asked_to_cancel')
    expect(screen.getByLabelText('Quantas vezes, no mínimo')).toHaveValue(1)
    expect(screen.getByLabelText('Quantas vezes, no mínimo')).not.toHaveAttribute('aria-invalid')
    await user.click(screen.getByRole('button', { name: 'Testar a regra' }))
    await screen.findByRole('status')
    expect(api.find('POST', '/v1/traits/rules/dry-run')[0]!.body.params).toEqual({ intent: 'cancellation', min_count: 1, window_days: 180 })
  })

  it('keeps the window of a rule over system events within the days they stay in the space', async () => {
    const api = mockApi(routes({ [`GET ${CONTROL}/v1/config/settings`]: { space_id: SP_PROD, type: 'settings', version: 4, document: { events_hot_days: 60 } } }))
    const { user } = await signInAt(at('padroes'))
    await choose(user, await screen.findByLabelText('Padrão do catálogo'), 'overdue_invoices')
    const window = screen.getByLabelText('Janela, em dias')
    // The catalog default is 90; this space keeps system events for 60 days.
    expect(window).toHaveValue(90)
    expect(await screen.findByText('Use um número inteiro de 1 a 60.')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Testar a regra' })).toBeDisabled()
    await user.clear(window)
    await user.type(window, '60')
    expect(window).toHaveAccessibleDescription('Eventos de sistema ficam 60 dias no espaço, e a janela não passa disso.')
    await user.click(screen.getByRole('button', { name: 'Testar a regra' }))
    await screen.findByRole('status')
    expect(api.find('POST', '/v1/traits/rules/dry-run')[0]!.body.params).toEqual({ event_type: 'invoice.overdue', min_count: 2, window_days: 60 })
    // A rule over conversations keeps the window of up to 365 days.
    await choose(user, screen.getByLabelText('Padrão do catálogo'), 'asked_to_cancel')
    expect(screen.getByLabelText('Janela, em dias')).toHaveAccessibleDescription('Conta só o que aconteceu nesse período.')
  })

  it('says, in the rules in use, the count and the window the cell runs instead of the stored ones', async () => {
    const old = { rule_id: 'faturas.antigas', catalog: 'overdue_invoices', version: '2', enabled: true, params: { event_type: 'invoice.overdue', min_count: 1, window_days: 180 }, purposes: [] }
    mockApi(routes({ [`GET ${CONTROL}/v1/config/trait-rules`]: { space_id: SP_PROD, type: 'trait-rules', version: 8, document: [IN_USE, old] } }))
    await signInAt(at('padroes'))
    const item = (await screen.findByText('faturas.antigas')).closest('li')!
    expect(item).toHaveTextContent('Quantas vezes, no mínimo: 1 (a célula conta 2) · Janela, em dias: 180 (a célula conta 90)')
    expect((await screen.findByText('reclamacao.visita')).closest('li')!).not.toHaveTextContent('a célula conta')
  })

  it('edits the parameters in fields, with the share in percent, and blocks an empty one', async () => {
    const api = mockApi(routes())
    const { user } = await signInAt(at('padroes'))
    await choose(user, await screen.findByLabelText('Padrão do catálogo'), 'preferred_channel')
    expect(screen.getByText(/Fixo no catálogo: Quem começou a conversa: customer/)).toBeInTheDocument()
    const share = screen.getByLabelText('Parte das conversas nesse canal, no mínimo (%)')
    expect(share).toHaveValue(70)
    await user.clear(share)
    expect(screen.getByRole('button', { name: 'Testar a regra' })).toBeDisabled()
    await user.type(share, '80')
    await user.clear(screen.getByLabelText('Janela, em dias'))
    await user.type(screen.getByLabelText('Janela, em dias'), '90')
    await user.click(screen.getByRole('button', { name: 'Testar a regra' }))
    await screen.findByRole('status')
    expect(api.find('POST', '/v1/traits/rules/dry-run')[0]!.body).toEqual({
      rule_id: 'canal.preferido',
      catalog: 'preferred_channel',
      params: { min_share: 0.8, min_conversations: 5, window_days: 90, initiated_by: 'customer' },
    })
  })

  it("opts a customer out of patterns", async () => {
    const api = mockApi(routes())
    const { user } = await signInAt(at('padroes'))
    const section = (await screen.findByRole('heading', { name: 'Padrões de um cliente' })).closest('section')!
    // A retraction leaves a tombstone until new evidence; nothing feeds an evaluation set.
    expect(within(section).getByText(/A retirada deixa uma lápide, e o padrão só volta com evidência nova\./)).toBeInTheDocument()
    expect(section).not.toHaveTextContent('conjunto de avaliação')
    await user.type(within(section).getByLabelText('ID do perfil'), PID)
    await user.click(within(section).getByRole('button', { name: 'Carregar padrões' }))
    expect(await within(section).findByText('Reclamação recorrente')).toBeInTheDocument()
    await user.click(within(section).getByRole('button', { name: 'Tirar dos padrões' }))
    await user.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Tirar dos padrões' }))
    expect(await screen.findByText('Feito em 23/09/2026 07:00.')).toBeInTheDocument()
    expect(api.find('POST', `/v1/profiles/${PID}/traits/opt-out`)).toHaveLength(1)
  })

  it('builds a rule from the catalog, tests it and sends it for approval, with the purposes that receive it', async () => {
    const api = mockApi(routes())
    const { user } = await signInAt(at('padroes'))
    await choose(user, await screen.findByLabelText('Padrão do catálogo'), 'overdue_invoices')
    await user.clear(screen.getByLabelText('Quantas vezes, no mínimo'))
    await user.type(screen.getByLabelText('Quantas vezes, no mínimo'), '3')
    // The purposes offered are the ones the space's sources declare.
    await user.click(screen.getByRole('checkbox', { name: 'cobranca' }))
    await testAndSend(user, 'avisar a cobrança de quem atrasa três faturas')

    expect(await screen.findByText('Regra faturas.em.atraso, versão 1, enviada para aprovação.')).toBeInTheDocument()
    expect(api.find('POST', '/v1/config/diffs')[0]!.body).toEqual({
      space_id: SP_PROD,
      type: 'trait-rules',
      reason: 'avisar a cobrança de quem atrasa três faturas',
      document: [
        IN_USE,
        { rule_id: 'faturas.em.atraso', catalog: 'overdue_invoices', version: '1', enabled: true, params: { event_type: 'invoice.overdue', min_count: 3, window_days: 90 }, purposes: ['cobranca'] },
      ],
    })
  })

  it('keeps a window beyond what the cell accepts from going out, with the reason on the field', async () => {
    const api = mockApi(routes())
    const { user } = await signInAt(at('padroes'))
    const window = await screen.findByLabelText('Janela, em dias')
    await user.clear(window)
    await user.type(window, '400')
    expect(window).toHaveAttribute('aria-invalid', 'true')
    expect(window).toHaveAccessibleDescription('Use um número inteiro de 1 a 365.')
    expect(screen.getByRole('button', { name: 'Testar a regra' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Enviar para aprovação' })).toBeDisabled()
    expect(api.find('POST', '/v1/traits/rules/dry-run')).toHaveLength(0)
  })

  it('adjusts a rule in use: the same id, the next version, and the parameters the form does not edit kept', async () => {
    const api = mockApi(routes())
    const { user } = await signInAt(at('padroes'))
    const item = (await screen.findByText('reclamacao.visita')).closest('li')!
    expect(item).toHaveTextContent('Quantas vezes, no mínimo: 3 · Janela, em dias: 90 · category: visita_tecnica')
    await user.click(within(item).getByRole('button', { name: 'Ajustar a regra reclamacao.visita' }))
    expect(await screen.findByRole('heading', { name: 'Ajustar a regra reclamacao.visita' })).toBeInTheDocument()
    expect(screen.getByText(/Parâmetros fora do formulário, mantidos como estão/)).toHaveTextContent('category: visita_tecnica')
    await user.clear(screen.getByLabelText('Quantas vezes, no mínimo'))
    await user.type(screen.getByLabelText('Quantas vezes, no mínimo'), '4')
    await testAndSend(user, 'quatro reclamações antes de marcar')

    expect(await screen.findByText('Regra reclamacao.visita, versão v4, enviada para aprovação.')).toBeInTheDocument()
    expect(api.find('POST', '/v1/config/diffs')[0]!.body.document).toEqual([
      { ...IN_USE, version: 'v4', params: { intent: 'complaint', min_count: 4, window_days: 90, category: 'visita_tecnica' } },
    ])
  })

  it('turns a rule off with a reason', async () => {
    const api = mockApi(routes())
    const { user } = await signInAt(at('padroes'))
    await user.click(await screen.findByRole('button', { name: 'Desligar a regra reclamacao.visita' }))
    const dialog = screen.getByRole('dialog', { name: 'Desligar a regra de padrão reclamacao.visita' })
    await user.type(within(dialog).getByLabelText('Motivo'), 'regra em revisão')
    await user.click(within(dialog).getByRole('button', { name: 'Enviar para aprovação' }))
    await within(dialog).findByText('Desligar a regra de padrão reclamacao.visita: enviado para aprovação.')
    expect(api.find('POST', '/v1/config/diffs')[0]!.body.document).toEqual([{ ...IN_USE, enabled: false }])
  })

  it("puts the control plane's refusal of a sensitive category in words", async () => {
    const detail = 'trait-rules reclamacao.visita: rule parameter `category` names a category that is sensitive or not allowed'
    mockApi(routes({ [`POST ${CONTROL}/v1/config/diffs`]: () => problem(422, 'invalid_input', detail) }))
    const { user } = await signInAt(at('padroes'))
    const item = (await screen.findByText('reclamacao.visita')).closest('li')!
    await user.click(within(item).getByRole('button', { name: 'Ajustar a regra reclamacao.visita' }))
    await testAndSend(user, 'ajuste da janela')
    expect(await screen.findByRole('alert')).toHaveTextContent('A regra de padrão reclamacao.visita usa uma categoria sensível ou fora da lista liberada para padrões.')
  })

  it('lets analysis test a rule but leaves sending to the roles that write the rules', async () => {
    mockApi(routes({}, ['analysis']))
    const { user } = await signInAt(at('padroes'))
    expect(await screen.findByText('reclamacao.visita')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Nova regra' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Ajustar a regra/ })).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Testar a regra' }))
    expect(await screen.findByText(/clientes teriam o padrão nos últimos/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Enviar para aprovação' })).not.toBeInTheDocument()
  })
})
