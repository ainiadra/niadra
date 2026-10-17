import { screen, within } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { cellClient } from '../api/clients'
import { API, at, baseRoutes, choose, mockApi, problem, signInAt } from './harness'
import { DRAFT_TURN, MORE_TURN, QUOTE_TURN, summary, view } from './turnFixtures'

const SEARCH = `POST ${API}/v1/turns/search`
const turnURL = (id: string) => `GET ${API}/v1/turns/${id}`
/** A conversation id that looks like a phone number: it may go in a body, never in an address. */
const CANARY = '+5511987650042'

describe('Turns client', () => {
  it('searches the kept tier with the filters and the conversation in the body, 50 a page', async () => {
    const api = mockApi({ [SEARCH]: { items: [] } })
    await cellClient({ baseURL: API, token: () => 'jwt.x' }).turns.search({ conversation_id: CANARY, flags: ['error'] })
    const call = api.calls[0]!
    expect(call.body).toEqual({ conversation_id: CANARY, flags: ['error'], limit: 50 })
    expect(decodeURIComponent(call.url.href)).not.toContain('5511987650042')
  })

  it('reads one turn by its id, in the path', async () => {
    const api = mockApi({ [turnURL('t.1')]: view(QUOTE_TURN) })
    await cellClient({ baseURL: API, token: () => 'jwt.x' }).turns.get('t.1')
    expect(api.calls[0]!.url.pathname).toBe('/v1/turns/t.1')
  })
})

describe('Turns', () => {
  it('lists kept turns, filters with the server fields and keeps the conversation out of every address', async () => {
    const api = mockApi(baseRoutes({ [SEARCH]: (r) => ({ items: r.body.cursor ? [] : [summary(QUOTE_TURN, { flags: ['handoff', 'error'] })], next_cursor: r.body.cursor ? null : 'c2' }) }))
    const { user } = await signInAt(at('turnos'))
    const table = await screen.findByRole('table', { name: /Turnos guardados/ })
    const row = within(table).getByRole('row', { name: /closing/ })
    expect(row).toHaveTextContent('29/09/2026 10:02:11')
    expect(row).toHaveTextContent('Transbordo')
    expect(row).toHaveTextContent('Erro')
    await user.type(screen.getByLabelText('Agente'), 'closing')
    await choose(user, screen.getByLabelText('Marca'), 'handoff')
    await user.type(screen.getByLabelText('ID da conversa'), CANARY)
    await user.type(screen.getByLabelText('De'), '2026-09-28')
    await user.click(screen.getByRole('button', { name: 'Filtrar' }))
    await screen.findByRole('button', { name: 'Limpar filtros' })
    await user.click(await screen.findByRole('button', { name: 'Carregar mais' }))
    const bodies = api.find('POST', '/v1/turns/search').map((c) => c.body)
    expect(bodies.at(-1)).toMatchObject({ agent: 'closing', flags: ['handoff'], conversation_id: CANARY, cursor: 'c2', limit: 50 })
    expect(bodies.at(-1).since).toBe(new Date(2026, 8, 28).toISOString())
    expect(new URLSearchParams(window.location.search).get('flag')).toBe('handoff')
    for (const url of [...api.calls.map((c) => c.url.href), window.location.href]) expect(decodeURIComponent(url)).not.toContain('5511987650042')
  })

  it('says when there is no kept turn yet, and points to opening any other one by id', async () => {
    mockApi(baseRoutes({ [SEARCH]: { items: [] } }))
    await signInAt(at('turnos'))
    expect(await screen.findByText('Nenhum turno guardado neste espaço ainda.')).toBeInTheDocument()
    expect(screen.getByText(/Os outros ficam no nível curto e abrem pelo ID\./)).toBeInTheDocument()
  })

  it('shows recording turned off as a state of the space, not as an error', async () => {
    mockApi(baseRoutes({ [SEARCH]: problem(404, 'not_found', 'not enabled for this space') }))
    await signInAt(at('turnos'))
    expect(await screen.findByText('O registro de turnos está desligado neste espaço.')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Abrir a Configuração' })).toBeInTheDocument()
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })

  it('shows the error of a failed search with its request code', async () => {
    mockApi(baseRoutes({ [SEARCH]: problem(500, 'internal_error', 'boom', 'req_turns_9') }))
    await signInAt(at('turnos'))
    expect(await screen.findByRole('alert')).toHaveTextContent('req_turns_9')
  })

  it('opens a turn of the short tier by its id, and refuses an id the record cannot have', async () => {
    const api = mockApi(baseRoutes({ [SEARCH]: { items: [] }, [turnURL(MORE_TURN.turn_id)]: view(MORE_TURN, { tier: 'short', kept_until: null }) }))
    const { user } = await signInAt(at('turnos'))
    const box = await screen.findByLabelText('ID do turno')
    await user.type(box, 'não é id')
    await user.click(screen.getByRole('button', { name: 'Abrir o turno' }))
    expect(screen.getByText(/Um ID de turno tem de 1 a 64 letras/)).toBeInTheDocument()
    await user.clear(box)
    await user.type(box, MORE_TURN.turn_id)
    await user.click(screen.getByRole('button', { name: 'Abrir o turno' }))
    expect(await screen.findByText('Curto: fica até a retenção da empresa')).toBeInTheDocument()
    expect(api.find('GET', `/v1/turns/${MORE_TURN.turn_id}`)).toHaveLength(1)
  })
})

describe('Turn detail', () => {
  it('shows what the agent read, called, showed, claimed and decided, with the build pinned, in stored mode', async () => {
    mockApi(baseRoutes({ [turnURL(QUOTE_TURN.turn_id)]: view(QUOTE_TURN) }))
    const { user } = await signInAt(at(`turnos/${QUOTE_TURN.turn_id}`))
    expect(await screen.findByRole('heading', { level: 1, name: 'Mensagem de closing, 29/09/2026 10:02:11' })).toBeInTheDocument()
    const parts = screen.getByRole('navigation', { name: 'Partes do turno' })
    expect(within(parts).getAllByRole('link').map((a) => a.textContent)).toEqual(['Resumo', 'Build', 'O que leu', 'O que chamou', 'O que mostrou', 'O que afirmou', 'O que decidiu', 'Marcas'])
    expect(screen.getByText('Guardado até 29/10/2026')).toBeInTheDocument()
    expect(screen.getByText('Pode ser reexecutado')).toBeInTheDocument()
    expect(screen.getByText('US$ 0,036')).toBeInTheDocument()
    // The build.
    expect(screen.getByText('compiler-1.15')).toBeInTheDocument()
    expect(within(screen.getByRole('table', { name: 'Prompts e versões' })).getByRole('row', { name: 'core v16' })).toBeInTheDocument()
    // The calls, the nested one inside its parent, and the model's tokens.
    const quote = screen.getByRole('article', { name: 'Chamada quote' })
    expect(within(quote).getByText(/740 ms · 1 paid_call/)).toBeInTheDocument()
    expect(within(quote).getByText('cotacao_plano_saude:op:q-77')).toBeInTheDocument()
    expect(within(quote).getByText(/1 restrição dura enviada, 3 resultados conferidos, 0 violações e 1 sem como conferir\./)).toBeInTheDocument()
    const lookup = screen.getByRole('article', { name: 'Chamada network_lookup' })
    expect(quote.closest('li')).toContainElement(lookup)
    expect(within(lookup).getByText('tentativa 2')).toBeInTheDocument()
    expect(screen.getByText(/2.953 tokens de entrada, 118.000 em cache, 138 de saída/)).toBeInTheDocument()
    // A stored value opens on demand, as JSON.
    const args = within(quote).getByText('Argumentos').closest('details')!
    expect(args).not.toHaveAttribute('open')
    await user.click(within(args).getByText('Argumentos'))
    expect(args).toHaveAttribute('open')
    expect(within(args).getByLabelText('Argumentos')).toHaveTextContent('"plan": "enfermaria"')
    // What it showed, claimed and decided.
    expect(screen.getByText(/^Lista plans, página 1, com 2 itens, 2 visíveis na entrega\./)).toBeInTheDocument()
    expect(screen.getByText('Recusa plan.network fora de rede-x, daqui em diante.')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Chamada quote, campo price_full' })).toHaveAttribute('href', '#chamada-1')
    expect(screen.getByText('Confere')).toBeInTheDocument()
    expect(screen.getByText('Sem evidência')).toBeInTheDocument()
    expect(screen.getByText('proposal:q-77')).toBeInTheDocument()
    expect(screen.getByText('evt-out-881')).toBeInTheDocument()
    expect(screen.getByText('O turno passou a conversa para outra pessoa ou outro agente.')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Ver o comprovante' })).toHaveAttribute('href', at('recibos/rct_0192'))
  })

  it('shows where a value lives in pointer mode and never fetches it', async () => {
    const blockers = [
      { reason: 'missing_pin' as const, value: 'prompts' },
      { reason: 'missing_pin' as const, value: 'assembler' },
    ]
    const fetched = mockApi(
      baseRoutes({ [turnURL(DRAFT_TURN.turn_id)]: view(DRAFT_TURN, { replayable: false, replay_blockers: blockers, tier: 'short', kept_until: null }) }),
    )
    await signInAt(at(`turnos/${DRAFT_TURN.turn_id}`))
    expect(await screen.findByText('s3://escritorio-registros/turns/2026/09/29/t002/b1.json')).toBeInTheDocument()
    expect(screen.getAllByText('Fica no armazenamento da empresa, fora da Niadra. O Console não busca o valor.')).toHaveLength(3)
    expect(screen.getByText(/24,8 KB/)).toBeInTheDocument()
    expect(document.querySelector('details')).toBeNull()
    expect(screen.getByText('task-contestacao-17')).toBeInTheDocument()
    expect(screen.getByText(/documento doc-notice-551 · âncora b:3 · semelhança 97%/)).toBeInTheDocument()
    expect(
      screen.getByText('Falta o pino prompts, que a gravação deste espaço exige. Falta o pino montador de contexto, que a gravação deste espaço exige.'),
    ).toBeInTheDocument()
    expect(fetched.calls.some((c) => c.url.protocol === 's3:' || c.url.href.includes('escritorio-registros'))).toBe(false)
  })

  it('shows only the digest in hash-only mode, and says why the turn cannot be run again', async () => {
    const blockers = [{ reason: 'content_mode' as const, value: 'hash_only' }]
    mockApi(baseRoutes({ [turnURL(MORE_TURN.turn_id)]: view(MORE_TURN, { replayable: false, replay_blockers: blockers }) }))
    await signInAt(at(`turnos/${MORE_TURN.turn_id}`))
    expect(await screen.findAllByText('Só o hash foi gravado; o valor não fica na Niadra.')).toHaveLength(2)
    expect(screen.getByText(/Só os hashes foram gravados, sem os valores\./)).toBeInTheDocument()
    expect(screen.getByText('item_variant:loja:991')).toBeInTheDocument()
    expect(screen.getByText('Nenhum pino gravado neste turno.')).toBeInTheDocument()
    expect(screen.getByText('O turno não leu nada da Niadra.')).toBeInTheDocument()
    expect(screen.getByText('Nenhuma afirmação encontrada neste turno.')).toBeInTheDocument()
  })

  it('says in words each reason the cell gives for a turn that cannot be run again', async () => {
    const blockers = [
      { reason: 'fidelity' as const, value: 'silver' },
      { reason: 'completeness' as const, value: 'unknown' },
    ]
    mockApi(baseRoutes({ [turnURL(MORE_TURN.turn_id)]: view(MORE_TURN, { replayable: false, replay_blockers: blockers }) }))
    await signInAt(at(`turnos/${MORE_TURN.turn_id}`))
    expect(
      await screen.findByText(
        'O registro veio do OpenTelemetry (nível prata), que não guarda o bastante para reexecutar o turno. Não se sabe se o registro está completo.',
      ),
    ).toBeInTheDocument()
  })

  it('says a turn is not held when the cell has no such turn', async () => {
    mockApi(baseRoutes({ [turnURL('gone-1')]: problem(404, 'not_found', 'turn not found') }))
    await signInAt(at('turnos/gone-1'))
    expect(await screen.findByText('Este turno não está guardado.')).toBeInTheDocument()
  })

  it('says recording is off when the space never turned it on', async () => {
    mockApi(baseRoutes({ [turnURL('off-1')]: problem(404, 'not_found', 'not enabled for this space') }))
    await signInAt(at('turnos/off-1'))
    expect(await screen.findByText('O registro de turnos está desligado neste espaço.')).toBeInTheDocument()
  })
})
