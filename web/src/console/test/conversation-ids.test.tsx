import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { API, at, baseRoutes, choose, mockApi, signInAt, SRC_VOICE } from './harness'
import { RECEIPT } from './fixtures'

/**
 * A channel's conversation id may be a phone number or an e-mail, and a URL ends up in access logs, in the
 * browser history and in the Referer. The Console keeps it out of every URL it fetches and every address it
 * shows: the id travels in request bodies, and a filter holding it lives in the router state.
 */
const CANARY = '+5511987650042'
const DIGITS = '5511987650042'

const ENTRY = {
  manifest_hash: 'm1',
  source_id: null,
  measure_version: 'm-2',
  channel: 'whatsapp',
  view: 'chat',
  experiment_group: 'memory',
  items: [],
  questions: 1,
  repeated: 0,
  used: 1,
  contradicted: 0,
  late: false,
  transfer_unread: null,
  not_measured_reason: null,
  measured_at: '2026-09-22T17:20:00Z',
}

/** Every address the browser was sent to, through the router or by hand, during a test. */
let addresses: string[] = []

beforeEach(() => {
  addresses = []
  for (const name of ['pushState', 'replaceState'] as const) {
    const original = window.history[name].bind(window.history)
    vi.spyOn(window.history, name).mockImplementation((data: unknown, unused: string, url?: string | URL | null) => {
      if (url) addresses.push(String(url))
      original(data, unused, url)
    })
  }
})

afterEach(() => {
  vi.restoreAllMocks()
})

function expectNowhereInAnURL(calls: { url: URL }[]) {
  const urls = [...calls.map((c) => c.url.href), ...addresses, window.location.href]
  for (const url of urls) {
    expect(url).not.toContain(DIGITS)
    expect(decodeURIComponent(url)).not.toContain(DIGITS)
  }
}

const routes = () =>
  baseRoutes({
    [`POST ${API}/v1/receipts/search`]: (r) => (r.body.cursor ? { items: [] } : { items: [{ ...RECEIPT, conversation_id: CANARY }], next_cursor: 'c2' }),
    [`POST ${API}/v1/context-use/conversation`]: (r) => ({ conversation_id: r.body.conversation_id, entries: [ENTRY] }),
    [`GET ${API}/v1/context-use/conversations`]: { items: [{ conversation_id: CANARY, source_id: SRC_VOICE, channel: 'voice', ended_at: '2026-09-22T17:00:00Z', measured_at: '2026-09-22T17:20:00Z', no_context: false, experiment_group: '' }], next_cursor: null },
    [`GET ${API}/v1/usage`]: { items: [] },
    [`GET ${API}/v1/usage/prompt-cache`]: { since: '', until: '', group_by: ['source_id'], buckets: [], prices: [], prices_as_of: '2026-09-24', estimate: true },
    [`GET ${API}/v1/usage/budget`]: { items: [] },
    [`GET ${API}/v1/context-use`]: { since: '', until: '', group_by: ['source_id'], buckets: [] },
    [`POST ${API}/v1/forget`]: { request_id: 'er1', status: 'pending', target_kind: 'conversation', target_id: 'x', requested_at: '2026-09-23T10:00:00Z', completed_at: null, erased: {}, export_run_ids: [], receipt_hash: null },
    [`GET ${API}/v1/forget`]: { items: [] },
    [`GET ${API}/v1/exports/runs`]: { items: [] },
  })

describe('A conversation id never reaches a URL', () => {
  it('filters the receipts by conversation, pages and clears, with the id only in the search body', async () => {
    const api = mockApi(routes())
    const { user } = await signInAt(at('recibos'))
    await user.type(await screen.findByLabelText('ID da conversa'), CANARY)
    await user.click(screen.getByRole('button', { name: 'Filtrar' }))
    await user.click(await screen.findByRole('button', { name: 'Carregar mais' }))
    const searches = api.find('POST', '/v1/receipts/search')
    expect(searches.at(-1)!.body).toMatchObject({ conversation_id: CANARY, cursor: 'c2' })
    // The filter survives a trip to a receipt and back, from the router state.
    await user.click(screen.getAllByRole('link', { name: /22\/09\/2026/ })[0]!)
    window.history.back()
    expect(await screen.findByLabelText('ID da conversa')).toHaveValue(CANARY)
    await user.click(screen.getByRole('button', { name: 'Limpar filtros' }))
    expectNowhereInAnURL(api.calls)
  })

  it("opens one conversation's measurement with the id in the body", async () => {
    const api = mockApi(routes())
    const { user } = await signInAt(at('uso'))
    await user.type(await screen.findByLabelText('ID da conversa'), CANARY)
    await user.click(screen.getByRole('button', { name: 'Abrir a medição' }))
    expect(await screen.findByText(/1 itens usados/)).toBeInTheDocument()
    expect(api.find('POST', '/v1/context-use/conversation').map((c) => c.body)).toEqual([{ conversation_id: CANARY }])
    expectNowhereInAnURL(api.calls)
  })

  it('opens a listed measured conversation with the id in the body, never in an address', async () => {
    const api = mockApi(routes())
    const { user } = await signInAt(at('uso'))
    await user.click(await screen.findByRole('button', { name: `Abrir a medição de ${CANARY}` }))
    expect(await screen.findByText(/1 itens usados/)).toBeInTheDocument()
    expect(api.find('POST', '/v1/context-use/conversation').map((c) => c.body)).toEqual([{ conversation_id: CANARY }])
    const listed = api.find('GET', '/v1/context-use/conversations')[0]!
    expect(listed.url.searchParams.get('since')).toMatch(/^\d{4}-\d{2}-\d{2}$/)
    expectNowhereInAnURL(api.calls)
  })

  it('asks for the erasure of one conversation with the id in the body', async () => {
    const api = mockApi(routes())
    const { user } = await signInAt(at('dados'))
    await choose(user, await screen.findByLabelText('O que apagar'), 'conversation')
    await user.type(screen.getByLabelText('ID da conversa'), CANARY)
    await user.click(screen.getByLabelText('Entendo que o apagamento não se desfaz'))
    await user.click(screen.getByRole('button', { name: 'Pedir o apagamento' }))
    expect(await screen.findByText('Pedido registrado.')).toBeInTheDocument()
    expect(api.find('POST', '/v1/forget')[0]!.body).toEqual({ target: 'conversation', conversation_id: CANARY })
    expectNowhereInAnURL(api.calls)
  })

  it('has no client call that puts a conversation id in a path or a query string', () => {
    const source = readFileSync(resolve(__dirname, '../api/clients.ts'), 'utf8')
      .replace(/\/\*[\s\S]*?\*\//g, '')
      .replace(/^\s*\/\/.*$/gm, '')
    // Each client method: its name, then its call.
    const methods = source.split(/\n(?=\s{4}\w+: \()/)
    expect(methods.length).toBeGreaterThan(50)
    const offenders = methods.filter((m) => {
      const call = m.slice(m.indexOf('call'))
      if (!/conversation/i.test(m)) return false
      const inPath = /`[^`]*\$\{[^}]*conversation[^}]*\}[^`]*`/i.test(call)
      const inQuery = /query:\s*\{[^}]*conversation/i.test(call) || (/query:\s*\{\s*\.\.\.q/.test(call) && /ReceiptQuery/.test(m))
      // The list of measured conversations is a GET by agent and period: ids only come back in the body.
      const theList = /'GET', '\/v1\/context-use\/conversations'/.test(call) && !/conversation_id/.test(call)
      const aGet = /'GET'/.test(call) && !theList
      return inPath || inQuery || aGet
    })
    expect(offenders).toEqual([])
  })
})
