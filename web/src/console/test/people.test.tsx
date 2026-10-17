import { screen, within } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import type { UserOut } from '../api/types'
import { at, baseRoutes, choose, CONTROL, json, mockApi, problem, signInAt, SP_SBX, SRC_VOICE, type Req } from './harness'

const ME_ID = '0192a000-0000-7000-8000-0000000000u1'
const NINA = '0192a000-0000-7000-8000-0000000000u2'
const RITA = '0192a000-0000-7000-8000-0000000000u3'
const OLD = '0192a000-0000-7000-8000-0000000000u4'

const person = (over: Partial<UserOut>): UserOut => ({
  user_id: 'x',
  email: 'x@empresa.com',
  active: true,
  roles: [{ role: 'review', space_id: null, source_ids: [] }],
  last_login_at: null,
  totp_uri: null,
  created_at: '2026-09-01T12:00:00Z',
  deactivated_at: null,
  password_set: true,
  sso: false,
  last_sso_login_at: null,
  second_factor_confirmed_at: null,
  recovery_codes_left: 0,
  invitation_expires_at: null,
  invitation: null,
  ...over,
})

const PEOPLE: UserOut[] = [
  person({ user_id: ME_ID, email: 'ana.costa@empresa.com', roles: [{ role: 'admin', space_id: null, source_ids: [] }], last_login_at: '2026-09-22T12:00:00Z', second_factor_confirmed_at: '2026-09-02T12:00:00Z', recovery_codes_left: 9 }),
  person({ user_id: NINA, email: 'nina@empresa.com', password_set: false, invitation_expires_at: '2026-09-29T12:00:00Z', roles: [{ role: 'review', space_id: SP_SBX, source_ids: [] }] }),
  person({ user_id: RITA, email: 'rita@empresa.com', password_set: false, sso: true, last_sso_login_at: '2026-09-23T12:00:00Z', last_login_at: '2026-09-23T12:00:00Z' }),
  person({ user_id: OLD, email: 'old@empresa.com', active: false, deactivated_at: '2026-09-10T12:00:00Z' }),
]

const routes = (extra = {}, roles?: Parameters<typeof baseRoutes>[1]) => baseRoutes({ [`GET ${CONTROL}/v1/users`]: PEOPLE, ...extra }, roles)
const row = (email: string) => screen.getByRole('listitem', { name: email })

describe('People and roles', () => {
  it('lists each person with roles, how they sign in and the second factor', async () => {
    mockApi(routes())
    await signInAt(at('pessoas'))
    const list = await screen.findByRole('list', { name: 'Pessoas da empresa' })
    const emails = within(list)
      .getAllByRole('listitem')
      .filter((li) => li.getAttribute('aria-labelledby'))
      .map((li) => li.querySelector('p')?.textContent)
    // Active people first, then by e-mail; the deactivated one last.
    expect(emails).toEqual(['ana.costa@empresa.com', 'nina@empresa.com', 'rita@empresa.com', 'old@empresa.com'])

    const me = row('ana.costa@empresa.com')
    expect(me).toHaveTextContent('Você')
    expect(me).toHaveTextContent('Administração · todos os espaços')
    expect(me).toHaveTextContent('Aplicativo ativo desde 02/09/2026 · 9 códigos de recuperação')
    expect(within(me).queryByRole('button', { name: 'Desativar ana.costa@empresa.com' })).not.toBeInTheDocument()

    const nina = row('nina@empresa.com')
    expect(nina).toHaveTextContent('Revisão · Atendimento e cobrança · Sandbox')
    expect(nina).toHaveTextContent('Convite aberto até 29/09/2026 09:00')
    expect(nina).toHaveTextContent('Configura no primeiro acesso')
    expect(nina).toHaveTextContent('Nunca entrou')

    const rita = row('rita@empresa.com')
    expect(rita).toHaveTextContent('Pelo SSO da empresa')
    expect(rita).toHaveTextContent('O do provedor de identidade')
    expect(within(rita).queryByRole('button', { name: /convite/i })).not.toBeInTheDocument()

    const old = row('old@empresa.com')
    expect(old).toHaveTextContent('Desativada em 10/09/2026')
    expect(within(old).getByRole('button', { name: 'Reativar old@empresa.com' })).toBeInTheDocument()
  })

  it('adds a person by e-mail and roles and shows the invitation link once', async () => {
    const api = mockApi(
      routes({
        [`POST ${CONTROL}/v1/users`]: (r: Req) =>
          json(201, person({ user_id: 'new', email: r.body.email, password_set: false, roles: r.body.roles, invitation: { token: 'TOKEN-OF-THE-INVITATION-0123456789', expires_at: '2026-10-01T12:00:00Z' } })),
      }),
    )
    const { user } = await signInAt(at('pessoas'))
    await user.click(await screen.findByRole('button', { name: 'Adicionar pessoa' }))
    const dialog = screen.getByRole('dialog', { name: 'Adicionar pessoa' })
    await user.click(within(dialog).getByRole('button', { name: 'Adicionar pessoa' }))
    expect(within(dialog).getByText('Digite o e-mail corporativo da pessoa.')).toBeInTheDocument()
    expect(within(dialog).getByText('Dê ao menos um papel.')).toBeInTheDocument()
    expect(api.find('POST', '/v1/users')).toHaveLength(0)

    await user.type(within(dialog).getByLabelText('E-mail corporativo'), 'bia@empresa.com')
    await choose(user, within(dialog).getByLabelText('Papel a adicionar'), 'security')
    await user.click(within(dialog).getByRole('button', { name: 'Adicionar papel' }))
    await choose(user, within(dialog).getByLabelText('Onde vale'), SP_SBX)
    await user.click(within(dialog).getByRole('button', { name: 'Adicionar pessoa' }))

    const done = await screen.findByRole('dialog', { name: 'Pessoa adicionada' })
    expect(api.find('POST', '/v1/users')[0]!.body).toEqual({ email: 'bia@empresa.com', roles: [{ role: 'security', space_id: SP_SBX, source_ids: [] }], with_totp: false })
    expect(within(done).getByTestId('invitation-link')).toHaveTextContent(`${window.location.origin}/console/convite#TOKEN-OF-THE-INVITATION-0123456789`)
    await user.click(within(done).getByRole('button', { name: 'Copiar o link' }))
    expect(await navigator.clipboard.readText()).toContain('#TOKEN-OF-THE-INVITATION-0123456789')
    await user.click(within(done).getByRole('button', { name: 'Concluir' }))
    expect(screen.queryByText(/TOKEN-OF-THE-INVITATION/)).not.toBeInTheDocument()
  })

  it('says an SSO person needs no invitation', async () => {
    mockApi(routes({ [`POST ${CONTROL}/v1/users`]: (r: Req) => json(201, person({ email: r.body.email, password_set: false, sso: true, roles: r.body.roles })) }))
    const { user } = await signInAt(at('pessoas'))
    await user.click(await screen.findByRole('button', { name: 'Adicionar pessoa' }))
    const dialog = screen.getByRole('dialog', { name: 'Adicionar pessoa' })
    await user.type(within(dialog).getByLabelText('E-mail corporativo'), 'leo@empresa.com')
    await user.click(within(dialog).getByRole('button', { name: 'Adicionar papel' }))
    await user.click(within(dialog).getByRole('button', { name: 'Adicionar pessoa' }))
    expect(await screen.findByRole('dialog', { name: 'Pessoa adicionada' })).toHaveTextContent('entra pelo SSO da empresa, sem convite')
  })

  it('gives a vendor role with the sources of one space', async () => {
    const api = mockApi(routes({ [`POST ${CONTROL}/v1/users`]: (r: Req) => json(201, person({ email: r.body.email, roles: r.body.roles, invitation: { token: 't'.repeat(43), expires_at: '2026-10-01T12:00:00Z' } })) }))
    const { user } = await signInAt(at('pessoas'))
    await user.click(await screen.findByRole('button', { name: 'Adicionar pessoa' }))
    const dialog = screen.getByRole('dialog', { name: 'Adicionar pessoa' })
    await user.type(within(dialog).getByLabelText('E-mail corporativo'), 'vendor@fornecedor.com')
    await choose(user, within(dialog).getByLabelText('Papel a adicionar'), 'vendor')
    await user.click(within(dialog).getByRole('button', { name: 'Adicionar papel' }))
    await user.click(within(dialog).getByRole('button', { name: 'Adicionar pessoa' }))
    expect(within(dialog).getByText('O papel Fornecedor precisa de um espaço e de ao menos uma fonte.')).toBeInTheDocument()
    await user.click(await within(dialog).findByLabelText(/Agente de voz/))
    await user.click(within(dialog).getByRole('button', { name: 'Adicionar pessoa' }))
    await screen.findByRole('dialog', { name: 'Pessoa adicionada' })
    expect(api.find('POST', '/v1/users')[0]!.body.roles).toEqual([{ role: 'vendor', space_id: '0192a000-0000-7000-8000-00000000a001', source_ids: [SRC_VOICE] }])
  })

  it('changes roles with a reason and explains the last-admin refusal', async () => {
    let attempt = 0
    const api = mockApi(routes({ [`PUT ${CONTROL}/v1/users/${NINA}/roles`]: () => (++attempt === 1 ? problem(409, 'conflict', 'the tenant must keep at least one admin') : [{ role: 'analysis', space_id: null, source_ids: [] }]) }))
    const { user } = await signInAt(at('pessoas'))
    await user.click(within(await screen.findByRole('listitem', { name: 'nina@empresa.com' })).getByRole('button', { name: 'Papéis de nina@empresa.com' }))
    const dialog = screen.getByRole('dialog', { name: 'Papéis de nina@empresa.com' })
    await user.click(within(dialog).getByRole('button', { name: 'Tirar o papel Revisão' }))
    await choose(user, within(dialog).getByLabelText('Papel a adicionar'), 'analysis')
    await user.click(within(dialog).getByRole('button', { name: 'Adicionar papel' }))
    const save = within(dialog).getByRole('button', { name: 'Salvar papéis' })
    expect(save).toBeDisabled()
    await user.type(within(dialog).getByLabelText('Motivo'), 'passou para a medição')
    await user.click(save)
    expect(await within(dialog).findByRole('alert')).toHaveTextContent('A empresa precisa manter ao menos uma pessoa com Administração')
    await user.click(save)
    expect(api.find('PUT', `/v1/users/${NINA}/roles`).at(-1)!.body).toEqual({ roles: [{ role: 'analysis', space_id: null, source_ids: [] }], reason: 'passou para a medição' })
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })

  it('deactivates with a reason, and reactivates', async () => {
    const api = mockApi(
      routes({
        [`POST ${CONTROL}/v1/users/${NINA}/deactivate`]: person({ user_id: NINA, active: false }),
        [`POST ${CONTROL}/v1/users/${OLD}/reactivate`]: person({ user_id: OLD }),
      }),
    )
    const { user } = await signInAt(at('pessoas'))
    await user.click(await screen.findByRole('button', { name: 'Desativar nina@empresa.com' }))
    const dialog = screen.getByRole('dialog', { name: 'Desativar nina@empresa.com' })
    expect(dialog).toHaveTextContent('15 minutos no máximo')
    await user.type(within(dialog).getByLabelText('Motivo'), 'saiu da empresa')
    await user.click(within(dialog).getByRole('button', { name: 'Desativar' }))
    expect(api.find('POST', `/v1/users/${NINA}/deactivate`)[0]!.body).toEqual({ reason: 'saiu da empresa' })

    await user.click(await screen.findByRole('button', { name: 'Reativar old@empresa.com' }))
    const back = screen.getByRole('dialog', { name: 'Reativar old@empresa.com' })
    await user.type(within(back).getByLabelText('Motivo'), 'voltou ao time')
    await user.click(within(back).getByRole('button', { name: 'Reativar' }))
    expect(api.find('POST', `/v1/users/${OLD}/reactivate`)[0]!.body).toEqual({ reason: 'voltou ao time' })
    expect(api.find('GET', '/v1/users').length).toBeGreaterThanOrEqual(3)
  })

  it('resets a second factor with a reason, and issues a link to change a password', async () => {
    const api = mockApi(
      routes({
        [`POST ${CONTROL}/v1/users/${ME_ID}/second-factor/reset`]: { reset_id: 'r', user_id: ME_ID, reset_by: ME_ID, reason: 'x', at: '2026-09-24T12:00:00Z' },
        [`POST ${CONTROL}/v1/users/${ME_ID}/invitation`]: json(201, { token: 'NEW-LINK-TOKEN-0123456789ABCDEF', expires_at: '2026-10-01T12:00:00Z' }),
      }),
    )
    const { user } = await signInAt(at('pessoas'))
    await user.click(await screen.findByRole('button', { name: 'Redefinir o segundo fator de ana.costa@empresa.com' }))
    const reset = screen.getByRole('dialog', { name: 'Redefinir o segundo fator' })
    await user.type(within(reset).getByLabelText('Motivo'), 'perdeu o celular e os códigos')
    await user.click(within(reset).getByRole('button', { name: 'Redefinir o segundo fator' }))
    expect(api.find('POST', `/v1/users/${ME_ID}/second-factor/reset`)[0]!.body).toEqual({ reason: 'perdeu o celular e os códigos' })

    await user.click(await screen.findByRole('button', { name: 'Link para trocar a senha de ana.costa@empresa.com' }))
    const link = screen.getByRole('dialog', { name: 'Link para trocar a senha' })
    expect(link).toHaveTextContent('A senha atual continua valendo até o link ser usado.')
    await user.click(within(link).getByRole('button', { name: 'Gerar o link' }))
    expect(await within(link).findByTestId('invitation-link')).toHaveTextContent('#NEW-LINK-TOKEN-0123456789ABCDEF')
  })

  it('shows the access history with who did what and why', async () => {
    mockApi(
      routes({
        [`GET ${CONTROL}/v1/users/${RITA}/history`]: [
          { change_id: 'c2', kind: 'sso_roles_synced', at: '2026-09-23T12:00:00Z', user_id: RITA, actor_id: null, reason: null, detail: { before: ['review'], after: ['admin', 'review'], kept_admin: false } },
          { change_id: 'c1', kind: 'roles_changed', at: '2026-09-20T12:00:00Z', user_id: RITA, actor_id: ME_ID, reason: 'cobre férias', detail: { before: [{ role: 'analysis' }], after: [{ role: 'review' }] } },
        ],
      }),
    )
    const { user } = await signInAt(at('pessoas'))
    await user.click(await screen.findByRole('button', { name: 'Histórico de rita@empresa.com' }))
    const dialog = screen.getByRole('dialog', { name: 'Histórico de rita@empresa.com' })
    const items = within(await within(dialog).findByRole('list', { name: 'Mudanças de acesso' })).getAllByRole('listitem')
    expect(items[0]).toHaveTextContent('Papéis atualizados pelos grupos do SSO')
    expect(items[0]).toHaveTextContent('pelo provedor de identidade (SSO)')
    expect(items[0]).toHaveTextContent('Antes: Revisão. Depois: Administração, Revisão.')
    expect(items[1]).toHaveTextContent('por ana.costa@empresa.com')
    expect(items[1]).toHaveTextContent('Motivo: cobre férias')
  })

  it('is for admins only', async () => {
    const api = mockApi(routes({}, ['security']))
    await signInAt(at('pessoas'))
    expect(await screen.findByText('Esta tela exige o papel Administração.')).toBeInTheDocument()
    expect(api.find('GET', '/v1/users')).toHaveLength(0)
  })
})
