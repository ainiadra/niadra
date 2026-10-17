import { screen, within } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import type { KeyOut } from '../api/types'
import { API, at, baseRoutes, choose, CONTROL, json, mockApi, problem, signInAt, SOURCES, SP_PROD, SRC_VOICE, type Req } from './harness'

const KEY: KeyOut = { key_id: 'key_live_01', source_id: SRC_VOICE, scopes: ['track', 'context'], status: 'active', created_at: '2026-09-01T12:00:00Z', expires_at: null, revoked_at: null }
const issued = (id: string, secret: string) => ({ key: { ...KEY, key_id: id, created_at: '2026-09-22T12:00:00Z' }, secret })
const revocation = { seq: 812, space_id: SP_PROD, kind: 'source', source_id: SRC_VOICE, key_id: null, at: '2026-09-22T17:30:00Z' }

const routes = (extra = {}, roles?: Parameters<typeof baseRoutes>[1]) =>
  baseRoutes({ [`GET ${CONTROL}/v1/sources/${SRC_VOICE}/keys`]: [KEY], ...extra }, roles)

describe('Agents and keys', () => {
  it('lists sources with vendor, channel, purposes, audience and verification ceiling', async () => {
    mockApi(routes())
    await signInAt(at('agentes'))
    const row = within(await screen.findByRole('table', { name: 'Fontes do espaço' })).getByRole('row', { name: /Agente de voz/ })
    for (const text of ['Fornecedor A', 'Voz', 'atendimento', 'Agente de atendimento', 'V2', 'Ativa']) expect(row).toHaveTextContent(text)
  })

  it('issues a key with the audience defaults and shows the secret once', async () => {
    const api = mockApi(routes({ [`POST ${CONTROL}/v1/sources/${SRC_VOICE}/keys`]: issued('key_live_02', 'nia_sk_live_S3CR3T') }))
    const { user } = await signInAt(at(`agentes/${SRC_VOICE}`))
    await user.click(await screen.findByRole('button', { name: 'Emitir chave' }))
    await user.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Emitir chave' }))
    const secret = await screen.findByRole('dialog', { name: 'Chave emitida' })
    expect(within(secret).getByTestId('secret')).toHaveTextContent('nia_sk_live_S3CR3T')
    expect(api.find('POST', `/v1/sources/${SRC_VOICE}/keys`)[0]!.body).toEqual({ scopes: null })
    const done = within(secret).getByRole('button', { name: 'Concluir' })
    expect(done).toBeDisabled()
    await user.click(within(secret).getByLabelText('Guardei o segredo em lugar seguro'))
    await user.click(done)
    expect(screen.queryByText(/S3CR3T/)).not.toBeInTheDocument()
    expect(api.find('GET', `/v1/sources/${SRC_VOICE}/keys`).length).toBeGreaterThanOrEqual(2)
  })

  it('issues a key with chosen scopes', async () => {
    const api = mockApi(routes({ [`POST ${CONTROL}/v1/sources/${SRC_VOICE}/keys`]: issued('key_live_03', 's') }))
    const { user } = await signInAt(at(`agentes/${SRC_VOICE}`))
    await user.click(await screen.findByRole('button', { name: 'Emitir chave' }))
    const dialog = screen.getByRole('dialog')
    await user.click(within(dialog).getByLabelText('Escolher os escopos'))
    await user.click(within(dialog).getByLabelText('Ler contexto'))
    await user.click(within(dialog).getByLabelText('Enviar eventos'))
    await user.click(within(dialog).getByRole('button', { name: 'Emitir chave' }))
    await screen.findByRole('dialog', { name: 'Chave emitida' })
    expect(api.find('POST', `/v1/sources/${SRC_VOICE}/keys`)[0]!.body).toEqual({ scopes: ['context', 'track'] })
  })

  it('rotates a key with a grace period', async () => {
    const api = mockApi(routes({ [`POST ${CONTROL}/v1/sources/${SRC_VOICE}/keys/key_live_01/rotate`]: issued('key_live_04', 'NOVO') }))
    const { user } = await signInAt(at(`agentes/${SRC_VOICE}`))
    await user.click(await screen.findByRole('button', { name: 'Girar a chave key_live_01' }))
    const dialog = screen.getByRole('dialog', { name: 'Girar a chave' })
    await choose(user, within(dialog).getByLabelText('Período de troca'), '3600')
    await user.click(within(dialog).getByRole('button', { name: 'Girar a chave' }))
    const secret = await screen.findByRole('dialog', { name: 'Chave emitida' })
    expect(secret).toHaveTextContent('vale por mais 1 hora')
    expect(api.find('POST', `/v1/sources/${SRC_VOICE}/keys/key_live_01/rotate`)[0]!.body).toEqual({ grace_seconds: 3600 })
  })

  it('closes a dialog with Escape and returns focus to the opener', async () => {
    mockApi(routes())
    const { user } = await signInAt(at(`agentes/${SRC_VOICE}`))
    const open = await screen.findByRole('button', { name: 'Emitir chave' })
    await user.click(open)
    await user.keyboard('{Escape}')
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(open).toHaveFocus()
  })

  it('revokes a key with a reason of at least three characters', async () => {
    const api = mockApi(routes({ [`POST ${CONTROL}/v1/sources/${SRC_VOICE}/keys/key_live_01/revoke`]: { ...revocation, kind: 'key', key_id: 'key_live_01' } }))
    const { user } = await signInAt(at(`agentes/${SRC_VOICE}`))
    await user.click(await screen.findByRole('button', { name: 'Revogar a chave key_live_01' }))
    const dialog = screen.getByRole('dialog', { name: 'Revogar a chave' })
    await user.type(within(dialog).getByLabelText('Motivo'), 'ok')
    expect(within(dialog).getByRole('button', { name: 'Revogar a chave' })).toBeDisabled()
    await user.type(within(dialog).getByLabelText('Motivo'), ' servidor antigo')
    await user.click(within(dialog).getByRole('button', { name: 'Revogar a chave' }))
    expect(api.find('POST', `/v1/sources/${SRC_VOICE}/keys/key_live_01/revoke`)[0]!.body).toEqual({ reason: 'ok servidor antigo' })
  })

  it('cuts access in the cell and in the control plane with one idempotency key', async () => {
    const api = mockApi(
      routes({
        [`POST ${API}/v1/sources/${SRC_VOICE}/revoke`]: { source_id: SRC_VOICE, revoked_key_ids: ['key_live_01'], revoked_at: '2026-09-22T17:30:00Z' },
        [`POST ${CONTROL}/v1/sources/${SRC_VOICE}/revoke`]: revocation,
      }),
    )
    const { user } = await signInAt(at(`agentes/${SRC_VOICE}`))
    await user.click(await screen.findByRole('button', { name: 'Cortar o acesso de Agente de voz' }))
    const dialog = screen.getByRole('dialog')
    await user.type(within(dialog).getByLabelText('Motivo'), 'chave exposta em log')
    expect(within(dialog).getByRole('button', { name: 'Cortar o acesso' })).toBeDisabled()
    await user.type(within(dialog).getByLabelText(/para confirmar/), 'Agente de voz')
    await user.click(within(dialog).getByRole('button', { name: 'Cortar o acesso' }))
    expect(await within(dialog).findByText('Acesso cortado na região às 22/09/2026 14:30.')).toBeInTheDocument()
    expect(within(dialog).getByText(/revogação\s+812/)).toBeInTheDocument()
    const calls = api.find('POST', `/v1/sources/${SRC_VOICE}/revoke`)
    const cellCall = calls.find((c) => c.url.origin === API)!
    const controlCall = calls.find((c) => c.url.origin === CONTROL)!
    expect(controlCall.body).toEqual({ reason: 'chave exposta em log' })
    expect(cellCall.headers.get('Idempotency-Key')).toBeTruthy()
    expect(cellCall.headers.get('Idempotency-Key')).toBe(controlCall.headers.get('Idempotency-Key'))
  })

  it('says plainly when the cut worked in the region but was not recorded in the control plane', async () => {
    mockApi(
      routes({
        [`POST ${API}/v1/sources/${SRC_VOICE}/revoke`]: { source_id: SRC_VOICE, revoked_key_ids: [], revoked_at: '2026-09-22T17:30:00Z' },
        [`POST ${CONTROL}/v1/sources/${SRC_VOICE}/revoke`]: problem(503, 'unavailable'),
      }),
    )
    const { user } = await signInAt(at(`agentes/${SRC_VOICE}`))
    await user.click(await screen.findByRole('button', { name: 'Cortar o acesso de Agente de voz' }))
    const dialog = screen.getByRole('dialog')
    await user.type(within(dialog).getByLabelText('Motivo'), 'teste de corte')
    await user.type(within(dialog).getByLabelText(/para confirmar/), 'Agente de voz')
    await user.click(within(dialog).getByRole('button', { name: 'Cortar o acesso' }))
    const alert = await within(dialog).findByRole('alert')
    expect(alert).toHaveTextContent('Na região: cortado')
    expect(alert).toHaveTextContent('No plano de controle: não registrado.')
    expect(within(dialog).getByRole('button', { name: 'Tentar de novo' })).toBeEnabled()
  })

  it('hides key management and the cut from people without the roles', async () => {
    mockApi(routes({}, ['analysis']))
    await signInAt(at(`agentes/${SRC_VOICE}`))
    expect(await screen.findByText('Emitir, girar e revogar chaves exige o papel Administração.')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Emitir chave' })).not.toBeInTheDocument()
    expect(screen.getByText('O corte exige o papel Segurança e privacidade.')).toBeInTheDocument()
  })
})

describe('a new source', () => {
  const NEW_ID = '0192a000-0000-7000-8000-00000000b009'
  const created = (body: Record<string, unknown>) => ({
    source_id: NEW_ID,
    space_id: SP_PROD,
    trusted_action_ops: [],
    revoked_at: null,
    ...body,
  })

  /** GET /v1/sources answers with what was created so far, like the control plane. */
  function sourcesRoutes(extra = {}, roles?: Parameters<typeof baseRoutes>[1]) {
    const made: Record<string, unknown>[] = []
    return {
      made,
      routes: routes(
        {
          [`GET ${CONTROL}/v1/sources`]: () => [...SOURCES, ...made],
          [`POST ${CONTROL}/v1/sources`]: (r: Req) => {
            const source = created(r.body)
            made.push(source)
            return json(201, source)
          },
          [`POST ${CONTROL}/v1/sources/${NEW_ID}/keys`]: issued('key_new_01', 'nia_sk_live_us-east-2_atendimento-prod_key_new_01_SEGREDO'),
          [`GET ${CONTROL}/v1/sources/${NEW_ID}/keys`]: [],
          ...extra,
        },
        roles,
      ),
    }
  }

  it('creates a source with its first key, shows the secret once and opens the source', async () => {
    const { routes: r } = sourcesRoutes()
    const api = mockApi(r)
    const { user } = await signInAt(at('agentes'))
    await user.click(await screen.findByRole('button', { name: 'Nova fonte' }))
    const dialog = screen.getByRole('dialog', { name: 'Nova fonte' })
    await user.type(within(dialog).getByLabelText('Nome'), 'Agente de WhatsApp')
    await choose(user, within(dialog).getByLabelText('Canal'), 'whatsapp')
    await choose(user, within(dialog).getByLabelText('Teto de verificação'), 'V3')
    await user.type(within(dialog).getByLabelText('Fornecedor (opcional)'), 'Fornecedor C')
    await user.type(within(dialog).getByLabelText('Finalidades'), 'atendimento, cobranca, atendimento')
    await user.click(within(dialog).getByLabelText('Confirma antes de agir'))
    await user.click(within(dialog).getByRole('button', { name: 'Criar e emitir a chave' }))

    const secret = await screen.findByRole('dialog', { name: 'Chave emitida' })
    expect(within(secret).getByTestId('secret')).toHaveTextContent('nia_sk_live_us-east-2_atendimento-prod_key_new_01_SEGREDO')
    expect(secret).toHaveTextContent('A fonte Agente de WhatsApp foi criada.')
    expect(api.find('POST', '/v1/sources')[0]!.body).toEqual({
      space_id: SP_PROD,
      name: 'Agente de WhatsApp',
      audience: 'customer_agent',
      vendor: 'Fornecedor C',
      channel: 'whatsapp',
      purposes: ['atendimento', 'cobranca'],
      verification_ceiling: 'V3',
      confirms_before_acting: true,
      navigation_enabled: true,
      trusted_action_ops: [],
      critical: false,
    })
    expect(api.find('POST', `/v1/sources/${NEW_ID}/keys`)[0]!.body).toEqual({ scopes: null })

    await user.click(within(secret).getByLabelText('Guardei o segredo em lugar seguro'))
    await user.click(within(secret).getByRole('button', { name: 'Concluir' }))
    expect(await screen.findByRole('heading', { name: 'Agente de WhatsApp' })).toBeInTheDocument()
    expect(screen.queryByText(/SEGREDO/)).not.toBeInTheDocument()
  })

  it('offers the ceilings that fit the audience: V4 only for a human desk, no customer for internal agents', async () => {
    const { routes: r } = sourcesRoutes()
    const api = mockApi(r)
    const { user } = await signInAt(at('agentes'))
    await user.click(await screen.findByRole('button', { name: 'Nova fonte' }))
    const dialog = screen.getByRole('dialog', { name: 'Nova fonte' })
    const ceilings = async () => {
      await user.click(within(dialog).getByLabelText('Teto de verificação'))
      const values = Array.from(document.querySelectorAll('[role="option"]')).map((o) => o.getAttribute('data-value'))
      await user.keyboard('{Escape}')
      return values
    }
    expect(await ceilings()).toEqual(['V2', 'V3', 'V1', 'V0'])
    await choose(user, within(dialog).getByLabelText('Audiência'), 'human')
    expect(await ceilings()).toEqual(['V3', 'V4', 'V2', 'V1'])
    await choose(user, within(dialog).getByLabelText('Audiência'), 'internal_agent')
    expect(within(dialog).getByLabelText('Teto de verificação')).toHaveTextContent('Sem cliente: tarefa interna')
    expect(within(dialog).getByLabelText('Canal')).toHaveTextContent('ERP')

    await user.type(within(dialog).getByLabelText('Nome'), 'Cobrança')
    await user.click(within(dialog).getByLabelText('Emitir a primeira chave agora'))
    await user.click(within(dialog).getByRole('button', { name: 'Criar fonte' }))
    expect(await screen.findByRole('heading', { name: 'Cobrança' })).toBeInTheDocument()
    expect(api.find('POST', '/v1/sources')[0]!.body).toMatchObject({ audience: 'internal_agent', verification_ceiling: 'no_customer', channel: 'erp' })
    expect(api.find('POST', `/v1/sources/${NEW_ID}/keys`)).toHaveLength(0)
  })

  it('says what to fix before sending anything', async () => {
    const { routes: r } = sourcesRoutes()
    const api = mockApi(r)
    const { user } = await signInAt(at('agentes'))
    await user.click(await screen.findByRole('button', { name: 'Nova fonte' }))
    const dialog = screen.getByRole('dialog', { name: 'Nova fonte' })
    await user.click(within(dialog).getByRole('button', { name: 'Criar e emitir a chave' }))
    expect(within(dialog).getByText('Dê um nome à fonte, como o agente é conhecido no seu time.')).toBeInTheDocument()
    expect(within(dialog).getByLabelText('Nome')).toHaveAttribute('aria-invalid', 'true')
    expect(api.find('POST', '/v1/sources')).toHaveLength(0)
  })

  it('offers internal agents every ceiling the control plane accepts for them, V4 and no customer included', async () => {
    const { routes: r } = sourcesRoutes()
    mockApi(r)
    const { user } = await signInAt(at('agentes'))
    await user.click(await screen.findByRole('button', { name: 'Nova fonte' }))
    const dialog = screen.getByRole('dialog', { name: 'Nova fonte' })
    await choose(user, within(dialog).getByLabelText('Audiência'), 'internal_agent')
    await user.click(within(dialog).getByLabelText('Teto de verificação'))
    // domain/control/rules.py check_ceiling: V4 for a human desk or a system of record, no_customer only here.
    expect(Array.from(document.querySelectorAll('[role="option"]')).map((o) => o.getAttribute('data-value'))).toEqual(['no_customer', 'V4', 'V3', 'V2', 'V1', 'V0'])
    await user.keyboard('{Escape}')
    await choose(user, within(dialog).getByLabelText('Audiência'), 'human')
    expect(within(dialog).getByText('V4, documento conferido, vale só para a mesa de atendimento humano e para agentes internos, como um sistema de registro.')).toBeInTheDocument()
  })

  it('sends the trusted operations of an internal agent, and shows them on the source', async () => {
    const { routes: r } = sourcesRoutes()
    const api = mockApi(r)
    const { user } = await signInAt(at('agentes'))
    await user.click(await screen.findByRole('button', { name: 'Nova fonte' }))
    const dialog = screen.getByRole('dialog', { name: 'Nova fonte' })
    // Only an internal agent is offered the list (guides/internal-agents): a customer agent sends none.
    expect(within(dialog).queryByLabelText('Operações confiáveis (opcional)')).not.toBeInTheDocument()
    await choose(user, within(dialog).getByLabelText('Audiência'), 'internal_agent')
    await user.type(within(dialog).getByLabelText('Nome'), 'Cobrança')
    const ops = within(dialog).getByLabelText('Operações confiáveis (opcional)')
    expect(within(dialog).getByText(/^Separadas por vírgula, como o agente registra: credit, refund\. Com a lista, a fonte só registra essas operações/)).toBeInTheDocument()
    await user.type(ops, 'credit, refund, credit')
    await user.click(within(dialog).getByLabelText('Emitir a primeira chave agora'))
    await user.click(within(dialog).getByRole('button', { name: 'Criar fonte' }))
    expect(await screen.findByRole('heading', { name: 'Cobrança' })).toBeInTheDocument()
    expect(api.find('POST', '/v1/sources')[0]!.body.trusted_action_ops).toEqual(['credit', 'refund'])
    expect(screen.getByText('credit, refund')).toBeInTheDocument()
  })

  it('puts the ceiling refusal of the control plane in words, on the ceiling field', async () => {
    // The control plane checks the ceiling against the audience again (domain/control/rules.py check_ceiling).
    const { routes: r } = sourcesRoutes({ [`POST ${CONTROL}/v1/sources`]: problem(422, 'verification_not_allowed', "a customer_agent source's ceiling is at most V3") })
    mockApi(r)
    const { user } = await signInAt(at('agentes'))
    await user.click(await screen.findByRole('button', { name: 'Nova fonte' }))
    const dialog = screen.getByRole('dialog', { name: 'Nova fonte' })
    await user.type(within(dialog).getByLabelText('Nome'), 'Agente de WhatsApp')
    await choose(user, within(dialog).getByLabelText('Teto de verificação'), 'V3')
    await user.click(within(dialog).getByRole('button', { name: 'Criar e emitir a chave' }))

    const ceiling = within(dialog).getByLabelText('Teto de verificação')
    await vi.waitFor(() => expect(ceiling).toHaveAttribute('aria-invalid', 'true'))
    expect(ceiling).toHaveAccessibleDescription(
      'Uma fonte com a audiência Agente de atendimento declara no máximo V3. V4, documento conferido, fica para a mesa de atendimento humano e para agentes internos. Escolha V3 ou um nível abaixo.',
    )
    expect(ceiling).toHaveFocus()
    // Said once, on the field: no second alert with the server's English text.
    expect(within(dialog).queryByRole('alert')).not.toBeInTheDocument()
    expect(dialog).not.toHaveTextContent("ceiling is at most")
    // Another choice clears it.
    await choose(user, ceiling, 'V2')
    expect(ceiling).not.toHaveAttribute('aria-invalid')
    expect(within(dialog).queryByText(/declara no máximo V3/)).not.toBeInTheDocument()
  })

  it('says that no customer is only for internal agents when the control plane refuses it', async () => {
    const { routes: r } = sourcesRoutes({ [`POST ${CONTROL}/v1/sources`]: problem(422, 'verification_not_allowed', 'only internal agents work without the customer') })
    mockApi(r)
    const { user } = await signInAt(at('agentes'))
    await user.click(await screen.findByRole('button', { name: 'Nova fonte' }))
    const dialog = screen.getByRole('dialog', { name: 'Nova fonte' })
    await user.type(within(dialog).getByLabelText('Nome'), 'Mesa')
    await user.click(within(dialog).getByRole('button', { name: 'Criar e emitir a chave' }))
    const ceiling = within(dialog).getByLabelText('Teto de verificação')
    await vi.waitFor(() => expect(ceiling).toHaveAccessibleDescription('Sem cliente vale só para um agente interno, que trabalha sem o cliente na conversa. Escolha um nível de verificação para esta audiência.'))
  })

  it('shows the server refusal inside the dialog', async () => {
    const { routes: r } = sourcesRoutes({ [`POST ${CONTROL}/v1/sources`]: problem(403, 'forbidden', 'the role cannot do this') })
    mockApi(r)
    const { user } = await signInAt(at('agentes'))
    await user.click(await screen.findByRole('button', { name: 'Nova fonte' }))
    const dialog = screen.getByRole('dialog', { name: 'Nova fonte' })
    await user.type(within(dialog).getByLabelText('Nome'), 'Agente')
    await user.click(within(dialog).getByRole('button', { name: 'Criar e emitir a chave' }))
    expect(await within(dialog).findByRole('alert')).toHaveTextContent('Seu papel não dá acesso a isto.')
  })

  it('offers the first source in an empty space, and hides creation from other roles', async () => {
    mockApi(routes({ [`GET ${CONTROL}/v1/sources`]: [] }))
    await signInAt(at('agentes'))
    expect(await screen.findByText('Nenhuma fonte neste espaço.')).toBeInTheDocument()
    expect(screen.getAllByRole('button', { name: 'Nova fonte' }).length).toBeGreaterThan(0)
  })

  it('does not offer creation without the admin role', async () => {
    mockApi(routes({ [`GET ${CONTROL}/v1/sources`]: [] }, ['security']))
    await signInAt(at('agentes'))
    expect(await screen.findByText('Quem tem o papel Administração cria as fontes deste espaço.')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Nova fonte' })).not.toBeInTheDocument()
  })
})
