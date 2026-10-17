import { screen, within } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import type { AssertionOut } from '../api/types'
import { API, at, baseRoutes, choose, mockApi, signInAt } from './harness'
import { PROFILE } from './fixtures'

const A: AssertionOut = {
  assertion_id: 'as1',
  handle_a: 'h1',
  handle_b: 'h2',
  method: 'system_import',
  state: 'applied',
  confidence: 0.99,
  level_at_capture: 'V1',
  created_at: '2026-09-20T12:00:00Z',
  origin_event_id: null,
  actor: 'user:u1',
  reason: 'importação do CRM',
  retracted_at: null,
}
const OP = { op_id: 'op_123', status: 'queued', assertion_id: null, link_id: null }
const PARENT = {
  parent_link_id: 'pl1',
  org_handle_id: 'h5',
  parent_handle_id: 'h6',
  org_profile_id: 'p_branch',
  parent_profile_id: 'p_group',
  method: 'system_import',
  valid_from: '2026-09-22T12:00:00Z',
  valid_to: null,
}

/**
 * Suggestions as domain/identity/suggest.py scores them: the sum of log2(m/u) of the signals a pair agrees
 * on, kept from 10 up. Two e-mails with the same Gmail address written differently score 10.891; with the
 * same name before the @ as well, 17.02. Phones alone (8.229) never reach 10.
 */
const GMAIL = { agree: ['gmail_canonical'] }
const GMAIL_AND_LOCAL = { agree: ['email_local', 'gmail_canonical'] }

const routes = (extra = {}, roles?: Parameters<typeof baseRoutes>[1]) =>
  baseRoutes(
    {
      [`GET ${API}/v1/identity/assertions`]: { items: [A], next_cursor: null },
      [`POST ${API}/v1/identity/assertions`]: { ...OP, assertion_id: 'as2' },
      [`POST ${API}/v1/identity/assertions/as1/retract`]: OP,
      [`POST ${API}/v1/identity/merge`]: OP,
      [`POST ${API}/v1/identity/unmerge`]: OP,
      [`POST ${API}/v1/identity/handles/block`]: { ...OP, status: 'blocked' },
      [`POST ${API}/v1/identity/links`]: { link_id: 'l9', person_handle_id: 'h1', org_handle_id: 'h9', role: 'buyer', can_see_contacts: true, valid_from: '2026-09-22T12:00:00Z', valid_to: null },
      [`POST ${API}/v1/identity/links/l9/end`]: { link_id: 'l9', person_handle_id: 'h1', org_handle_id: 'h9', role: 'buyer', can_see_contacts: true, valid_from: '2026-09-22T12:00:00Z', valid_to: '2026-09-23T12:00:00Z' },
      [`POST ${API}/v1/identity/parents`]: PARENT,
      [`POST ${API}/v1/identity/parents/pl1/end`]: { ...PARENT, valid_to: '2026-09-23T12:00:00Z' },
      [`GET ${API}/v1/profiles/${PROFILE.profile_id}`]: PROFILE,
      [`GET ${API}/v1/identity/suggestions`]: {
        items: [
          {
            suggestion_id: 'sg1',
            handle_a: 'h2',
            handle_b: 'h7',
            profile_a: PROFILE.profile_id,
            profile_b: 'p_outro',
            identifier_a: { ...PROFILE.handles[1]!, status: 'active' },
            identifier_b: { ...PROFILE.handles[1]!, handle_id: 'h7', value: 'marina.s@••••.com', status: 'active' },
            score: 10.891,
            features: GMAIL,
            status: 'open',
            created_at: '2026-09-22T12:00:00Z',
          },
          { suggestion_id: 'sg2', handle_a: 'h3', handle_b: 'h4', profile_a: null, profile_b: null, identifier_a: null, identifier_b: null, score: 17.02, features: GMAIL_AND_LOCAL, status: 'open', created_at: '2026-09-22T12:00:00Z' },
        ],
      },
      [`POST ${API}/v1/identity/suggestions/sg1/accept`]: { ...OP, assertion_id: 'as9' },
      [`POST ${API}/v1/identity/suggestions/sg1/dismiss`]: { suggestion_id: 'sg1', handle_a: 'h2', handle_b: 'h7', score: 10.891, features: GMAIL, status: 'rejected', created_at: '2026-09-22T12:00:00Z' },
      ...extra,
    },
    roles,
  )

describe('Identity', () => {
  it('says what a suggestion needs when there is none, with an example marked as such', async () => {
    mockApi(routes({ [`GET ${API}/v1/identity/suggestions`]: { items: [], next_cursor: null } }))
    const { user } = await signInAt(at('identidade'))
    await user.click(await screen.findByRole('tab', { name: 'Sugestões' }))
    expect(await screen.findByText('Nenhuma sugestão aberta.')).toBeInTheDocument()
    expect(screen.getByText(/os agentes precisam mandar eventos com mais de um identificador do mesmo cliente/)).toBeInTheDocument()
    expect(screen.getByText('exemplo')).toBeInTheDocument()
    expect(screen.getByText(/12 pontos · apareceram juntos em 3 eventos/)).toBeInTheDocument()
  })

  it('lists assertions, filters by state, and retracts one with an idempotency key', async () => {
    const api = mockApi(routes())
    const { user } = await signInAt(at('identidade'))
    expect(await screen.findByText('Importado de sistema')).toBeInTheDocument()
    await choose(user, screen.getByLabelText('Estado'), 'applied')
    await user.click(screen.getByRole('button', { name: 'Filtrar' }))
    expect(api.find('GET', '/v1/identity/assertions').at(-1)!.url.searchParams.get('state')).toBe('applied')

    await user.click(screen.getByRole('button', { name: 'Retirar a asserção as1' }))
    const dialog = screen.getByRole('dialog', { name: 'Retirar a asserção' })
    await user.type(within(dialog).getByLabelText('Motivo'), 'identificador reciclado')
    await user.click(within(dialog).getByRole('button', { name: 'Retirar a asserção' }))
    expect(await within(dialog).findByText('Na fila para aplicar.')).toBeInTheDocument()
    const call = api.find('POST', '/v1/identity/assertions/as1/retract')[0]!
    expect(call.body).toEqual({ reason: 'identificador reciclado' })
    expect(call.headers.get('Idempotency-Key')).toBeTruthy()
  })

  it('creates an assertion with both identifiers in the body', async () => {
    const api = mockApi(routes())
    const { user } = await signInAt(at('identidade'))
    await user.click(await screen.findByRole('button', { name: 'Nova asserção' }))
    const dialog = screen.getByRole('dialog', { name: 'Nova asserção' })
    const [first, second] = within(dialog).getAllByLabelText('Valor')
    await user.type(first!, '+15550100123')
    await user.type(second!, 'marina@example.com')
    await user.click(within(dialog).getByRole('button', { name: 'Criar asserção' }))
    expect(await within(dialog).findByText('as2')).toBeInTheDocument()
    expect(api.find('POST', '/v1/identity/assertions')[0]!.body).toEqual({
      handles: [
        { type: 'phone_e164', value: '+15550100123' },
        { type: 'email', value: 'marina@example.com' },
      ],
      method: 'explicit_identify',
      subject_kind: 'person',
    })
  })

  it('shows both sides of a suggestion, accepts one and dismisses another, with idempotency keys', async () => {
    const api = mockApi(
      routes({
        [`POST ${API}/v1/identity/suggestions/sg2/dismiss`]: { suggestion_id: 'sg2', handle_a: 'h3', handle_b: 'h4', profile_a: null, profile_b: null, identifier_a: null, identifier_b: null, score: 17.02, features: GMAIL_AND_LOCAL, status: 'rejected', created_at: '2026-09-22T12:00:00Z' },
      }),
    )
    const { user } = await signInAt(at('identidade'))
    await user.click(await screen.findByRole('tab', { name: 'Sugestões' }))
    expect(await screen.findByText('marina.s@••••.com')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'p_outro' })).toBeInTheDocument()
    expect(screen.getAllByText('identificador que não existe mais')).toHaveLength(2)
    await user.click(screen.getByRole('button', { name: 'Aceitar a sugestão sg1' }))
    expect(await screen.findByText('as9')).toBeInTheDocument()
    const accept = api.find('POST', '/v1/identity/suggestions/sg1/accept')[0]!
    expect(accept.body).toEqual({ reason: null })
    expect(accept.headers.get('Idempotency-Key')).toBeTruthy()
    await user.click(screen.getByRole('button', { name: 'Dispensar a sugestão sg2' }))
    expect(await screen.findByText('Dispensada')).toBeInTheDocument()
    expect(api.find('POST', '/v1/identity/suggestions/sg2/dismiss')[0]!.headers.get('Idempotency-Key')).toBeTruthy()
  })

  it('says in words that a suggestion came from a declared identify', async () => {
    mockApi(
      routes({
        [`GET ${API}/v1/identity/suggestions`]: {
          items: [
            { suggestion_id: 'sg3', handle_a: 'h3', handle_b: 'h4', profile_a: null, profile_b: null, identifier_a: null, identifier_b: null, score: 10, features: { agree: ['declared'] }, status: 'open', created_at: '2026-09-24T12:00:00Z' },
          ],
        },
      }),
    )
    const { user } = await signInAt(at('identidade'))
    await user.click(await screen.findByRole('tab', { name: 'Sugestões' }))
    expect(await screen.findByText('Sinais em comum: declarados juntos num identify, sem prova (método declared), por um agente ou sistema.')).toBeInTheDocument()
  })

  it('shows the suggestion score as points against the threshold of 10, with the signals in words, never as a percentage', async () => {
    mockApi(routes())
    const { user } = await signInAt(at('identidade'))
    await user.click(await screen.findByRole('tab', { name: 'Sugestões' }))
    expect(await screen.findByText('10,9 pontos')).toBeInTheDocument()
    expect(screen.getByText('17,0 pontos')).toBeInTheDocument()
    expect(screen.getByText(/só vira sugestão a partir de 10 pontos/)).toBeInTheDocument()
    expect(screen.getByText('Sinais em comum: mesmo endereço Gmail, escrito com pontos ou + diferentes.')).toBeInTheDocument()
    expect(screen.getByText('Sinais em comum: mesmo nome antes do @; mesmo endereço Gmail, escrito com pontos ou + diferentes.')).toBeInTheDocument()
    // The old reading, a percentage of the score, rendered 10.891 as "1.089,1%".
    expect(screen.getByRole('tabpanel')).not.toHaveTextContent('%')
  })

  it('merges two profiles, and a forced merge needs a reason', async () => {
    const api = mockApi(routes())
    const { user } = await signInAt(at('identidade'))
    await user.click(await screen.findByRole('tab', { name: 'Unir e separar' }))
    await user.type(screen.getByLabelText('ID do primeiro perfil'), 'p1')
    await user.type(screen.getByLabelText('ID do segundo perfil'), 'p2')
    await user.click(screen.getByLabelText(/Forçar a união/))
    const merge = screen.getByRole('button', { name: 'Unir os perfis' })
    expect(merge).toBeDisabled()
    await user.type(screen.getAllByLabelText('Motivo')[0]!, 'mesmo cliente, dois CRMs')
    await user.click(merge)
    expect(await screen.findByText('op_123')).toBeInTheDocument()
    expect(api.find('POST', '/v1/identity/merge')[0]!.body).toEqual({ profile_ids: ['p1', 'p2'], force: true, reason: 'mesmo cliente, dois CRMs' })
  })

  it('separates chosen identifiers from a profile', async () => {
    const api = mockApi(routes())
    const { user } = await signInAt(at('identidade'))
    await user.click(await screen.findByRole('tab', { name: 'Unir e separar' }))
    await user.type(screen.getByLabelText('ID do perfil'), PROFILE.profile_id)
    await user.click(screen.getByRole('button', { name: 'Carregar identificadores' }))
    await user.click(await screen.findByRole('checkbox', { name: /m•••@••••.com/ }))
    await user.click(screen.getByRole('button', { name: 'Separar os identificadores escolhidos' }))
    expect(await screen.findByText('op_123')).toBeInTheDocument()
    expect(api.find('POST', '/v1/identity/unmerge')[0]!.body).toEqual({ profile_id: PROFILE.profile_id, handle_ids: ['h2'], reason: null })
  })

  it('separates a profile by the assertions that joined it, named by their identifiers', async () => {
    const api = mockApi(routes())
    const { user } = await signInAt(at('identidade'))
    await user.click(await screen.findByRole('tab', { name: 'Unir e separar' }))
    await user.type(screen.getByLabelText('ID do perfil'), PROFILE.profile_id)
    await user.click(screen.getByRole('button', { name: 'Carregar identificadores' }))
    await user.click(await screen.findByRole('radio', { name: 'Ligações que se desfazem' }))
    const join = await screen.findByRole('checkbox', { name: /Telefone •••• 4471 com E-mail m•••@••••\.com/ })
    expect(join).toHaveAccessibleName(/Importado de sistema/)
    // Only the applied assertions of this profile, as the server lists them.
    const listed = api.find('GET', '/v1/identity/assertions').at(-1)!.url.searchParams
    expect([listed.get('profile_id'), listed.get('state')]).toEqual([PROFILE.profile_id, 'applied'])
    const undo = screen.getByRole('button', { name: 'Desfazer as ligações escolhidas' })
    expect(undo).toBeDisabled()
    await user.click(join)
    await user.type(screen.getAllByLabelText('Motivo (opcional)').at(-1)!, 'importação juntou duas pessoas')
    await user.click(undo)
    expect(await screen.findByText('op_123')).toBeInTheDocument()
    expect(api.find('POST', '/v1/identity/unmerge')[0]!.body).toEqual({ profile_id: PROFILE.profile_id, assertion_ids: ['as1'], reason: 'importação juntou duas pessoas' })
    expect(api.find('POST', '/v1/identity/unmerge')[0]!.headers.get('idempotency-key')).toBeTruthy()
  })

  it('blocks an identifier', async () => {
    const api = mockApi(routes())
    const { user } = await signInAt(at('identidade'))
    await user.click(await screen.findByRole('tab', { name: 'Bloquear identificador' }))
    await user.type(screen.getByLabelText('Valor'), '+15550100000')
    await user.click(screen.getByRole('button', { name: 'Bloquear o identificador' }))
    expect(await screen.findByText('Identificador bloqueado.')).toBeInTheDocument()
    expect(api.find('POST', '/v1/identity/handles/block')[0]!.body).toEqual({ handle: { type: 'phone_e164', value: '+15550100000' }, reason: null })
  })

  it('creates and ends a link between a person and an account', async () => {
    const api = mockApi(routes())
    const { user } = await signInAt(at('identidade'))
    await user.click(await screen.findByRole('tab', { name: 'Vínculos' }))
    const [person, org] = screen.getAllByLabelText('Valor')
    await user.type(person!, 'joao@cliente.com')
    await user.type(org!, 'cliente.com')
    await user.type(screen.getByLabelText('Papel'), 'buyer')
    await user.click(screen.getByLabelText('A pessoa pode ver os contatos da organização'))
    await user.click(screen.getByRole('button', { name: 'Criar o vínculo' }))
    expect(await screen.findByText('Vínculo criado.')).toBeInTheDocument()
    expect(api.find('POST', '/v1/identity/links')[0]!.body).toEqual({
      person: { type: 'email', value: 'joao@cliente.com' },
      organization: { type: 'email_domain', value: 'cliente.com', subject_kind: 'account' },
      role: 'buyer',
      can_see_contacts: true,
      method: 'declared',
    })

    await user.type(screen.getByLabelText('ID do vínculo'), 'l9')
    await user.click(screen.getByRole('button', { name: 'Encerrar o vínculo' }))
    expect(await screen.findByText('Vínculo encerrado')).toBeInTheDocument()
    expect(api.find('POST', '/v1/identity/links/l9/end')[0]!.body).toEqual({ valid_to: null })
  })

  it('puts an organization under its parent by id, and ends it', async () => {
    const api = mockApi(routes())
    const { user } = await signInAt(at('identidade'))
    await user.click(await screen.findByRole('tab', { name: 'Matriz e subsidiárias' }))
    const [child, parent] = screen.getAllByLabelText('Valor')
    const [childSystem, parentSystem] = screen.getAllByLabelText('Sistema')
    // An e-mail domain is a hint, never an organization's id: the field offers a registry or a system id.
    await user.click(screen.getAllByLabelText('Tipo')[0]!)
    expect(document.querySelector('[role="option"][data-value="email_domain"]')).toBeNull()
    expect(document.querySelector('[role="option"][data-value="org_registry_hmac"]')).not.toBeNull()
    await user.keyboard('{Escape}')
    await user.type(child!, 'ACC-2201')
    await user.type(childSystem!, 'crm')
    await user.type(parent!, 'GRP-10')
    await user.type(parentSystem!, 'crm')
    await choose(user, screen.getByLabelText('Como se sabe'), 'system_import')
    await user.click(screen.getByRole('button', { name: 'Registrar a matriz' }))
    expect(await screen.findByText('Matriz registrada.')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'abrir a subsidiária' })).toHaveAttribute('href', expect.stringContaining('/clientes/p_branch'))
    expect(api.find('POST', '/v1/identity/parents')[0]!.body).toEqual({
      organization: { type: 'system_id', value: 'ACC-2201', scope: 'crm', subject_kind: 'account' },
      parent: { type: 'system_id', value: 'GRP-10', scope: 'crm', subject_kind: 'account' },
      method: 'system_import',
    })

    await user.type(screen.getByLabelText('ID da ligação com a matriz'), 'pl1')
    await user.click(screen.getByRole('button', { name: 'Encerrar a matriz' }))
    expect(await screen.findByText('Matriz encerrada')).toBeInTheDocument()
    expect(api.find('POST', '/v1/identity/parents/pl1/end')[0]!.body).toEqual({ valid_to: null })
  })

  it('lets people without the roles read but not change', async () => {
    mockApi(routes({}, ['analysis']))
    await signInAt(at('identidade'))
    expect(await screen.findByText('Importado de sistema')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Nova asserção' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Retirar a asserção/ })).not.toBeInTheDocument()
  })
})
