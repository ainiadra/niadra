import { screen, within } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { RECEIPT } from './fixtures'
import { API, at, baseRoutes, CONTROL, mockApi, problem, signInAt, SRC_BILLING, SRC_VOICE, type Req } from './harness'

const KEY = { key_id: 'key_1', scopes: ['track', 'context'], status: 'active', created_at: '2026-09-01T00:00:00Z', expires_at: null, revoked_at: null }
const ARRIVED = (arrived: boolean) => ({ arrived, since: '2026-07-01T00:00:00Z' })

describe('Início', () => {
  it('says what the memory is doing, read from the real state, and asks for no task', async () => {
    const api = mockApi(
      baseRoutes({
        [`GET ${CONTROL}/v1/sources/${SRC_VOICE}/keys`]: [KEY],
        [`GET ${CONTROL}/v1/sources/${SRC_BILLING}/keys`]: [],
        [`GET ${API}/v1/sources/arrived`]: ARRIVED(true),
        [`POST ${API}/v1/receipts/search`]: (r: Req) => ({ items: r.body.kind === 'read' ? [RECEIPT] : [], next_cursor: null }),
      }),
    )
    await signInAt(`/console/e/0192a000-0000-7000-8000-00000000a001`)
    expect(await screen.findByRole('heading', { level: 1, name: 'Início' })).toBeInTheDocument()
    const section = await screen.findByRole('region', { name: 'Estado da memória' })
    const items = within(section).getAllByRole('listitem')
    await within(items[2]!).findByText('Sim')
    // Each fact is one tile, the whole tile a link to the screen behind it, with its name and its answer only.
    expect(within(items[0]!).getByRole('link')).toHaveAccessibleName('Agentes conectados: Sim')
    expect(within(items[0]!).getByRole('link')).toHaveAttribute('href', expect.stringContaining('/agentes'))
    expect(within(items[1]!).getByRole('link')).toHaveAccessibleName('Eventos chegando: Sim')
    expect(within(items[1]!).getByRole('link')).toHaveAttribute('href', expect.stringContaining('/sistemas'))
    expect(within(items[2]!).getByRole('link')).toHaveAccessibleName('Contexto entregue: Sim')
    expect(within(items[2]!).getByRole('link')).toHaveAttribute('href', expect.stringContaining('/recibos'))
    expect(within(section).queryByRole('link', { name: 'Ver' })).not.toBeInTheDocument()
    // No heading or explanation over the tiles, and no note under each answer.
    expect(screen.queryByRole('heading', { name: 'O que a memória está fazendo' })).not.toBeInTheDocument()
    expect(screen.queryByText(/Nada aqui é tarefa/)).not.toBeInTheDocument()
    expect(section).not.toHaveTextContent('fontes ativas')
    expect(section).not.toHaveTextContent('eventos recebidos nos últimos 90 dias')
    expect(section).not.toHaveTextContent('há comprovantes de leitura')
    // Zero touch: no step counter, no "Falta", no configuration asked for.
    expect(section).not.toHaveTextContent('Falta')
    expect(section).not.toHaveTextContent(/de \d+ feitos/)
    expect(within(section).queryByRole('link', { name: 'Criar uma chave' })).not.toBeInTheDocument()
    // The tutorials come first.
    const tutorials = screen.getByRole('heading', { name: 'Tutoriais' }).closest('section')!
    expect(tutorials.compareDocumentPosition(section) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    // Whether events arrived is asked over 90 days; the rules and schedules are not read here.
    expect(api.find('GET', '/v1/sources/arrived')[0]!.url.searchParams.get('days')).toBe('90')
    expect(api.find('GET', '/v1/config/trigger-rules')).toHaveLength(0)
    expect(api.find('POST', '/v1/receipts/search').map((c) => c.body.kind)).toEqual(['read'])
    const jobs = screen.getByRole('heading', { name: 'Os trabalhos do Console' }).closest('section')!
    expect(within(jobs).getByRole('link', { name: /Comprovantes/ })).toHaveAttribute('href', expect.stringContaining('/recibos'))
    expect(screen.getByRole('button', { name: 'Fazer o tour de 5 minutos' })).toBeInTheDocument()
  })

  it('offers the one manual act, a key, only while no agent is connected', async () => {
    mockApi(
      baseRoutes({
        [`GET ${CONTROL}/v1/sources`]: [],
        [`GET ${API}/v1/sources/arrived`]: ARRIVED(false),
        [`POST ${API}/v1/receipts/search`]: { items: [], next_cursor: null },
      }),
    )
    await signInAt(at('inicio'))
    const section = await screen.findByRole('region', { name: 'Estado da memória' })
    expect(await within(section).findByRole('link', { name: 'Criar uma chave' })).toHaveAttribute('href', expect.stringContaining('/agentes'))
    expect(section).toHaveTextContent('É o único passo manual')
    expect(within(section).getByRole('link', { name: /Agentes conectados/ })).toHaveAccessibleName('Agentes conectados: Ainda não')
    expect(section).not.toHaveTextContent('nenhum agente usou uma chave ainda')
    expect(section).not.toHaveTextContent('Falta')
  })

  it('says what it could not check instead of guessing', async () => {
    mockApi(
      baseRoutes(
        {
          [`GET ${API}/v1/sources/arrived`]: problem(500, 'internal'),
          [`POST ${API}/v1/receipts/search`]: problem(403, 'forbidden'),
        },
        ['review'],
      ),
    )
    await signInAt(at('inicio'))
    const section = await screen.findByRole('region', { name: 'Estado da memória' })
    const items = within(section).getAllByRole('listitem')
    await within(items[1]!).findByText('Não conferido')
    expect(items[1]).not.toHaveTextContent('a região não respondeu agora')
    expect(items[2]).toHaveTextContent('Não conferido')
  })
})
