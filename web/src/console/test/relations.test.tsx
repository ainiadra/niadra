import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { screen, waitFor, within } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import type { GraphOut } from '../api/graph'
import { MEMORY, PROFILE } from './fixtures'
import { API, at, baseRoutes, mockApi, problem, signInAt, SP_PROD, type Handler, type Req } from './harness'

const FIXTURE = JSON.parse(readFileSync(join(__dirname, 'graph.fixture.json'), 'utf8')) as { customer: GraphOut; account: GraphOut; overview: GraphOut }
const CENTER = FIXTURE.customer.center!
const COMPANY = FIXTURE.account.center!

/** `GET /v1/graph` as the cell answers it: by center and depth, or the overview; a scenario changes one case. */
function graphRoute(scenario: 'data' | 'empty' | 'error' | 'forbidden' | 'notes' = 'data'): Handler {
  let failures = scenario === 'error' ? 1 : 0
  return (r: Req) => {
    if (scenario === 'forbidden') return problem(403, 'forbidden', 'role cannot read profiles')
    if (failures > 0) {
      failures -= 1
      return problem(500, 'internal_error', 'boom')
    }
    if (r.url.searchParams.get('overview') === '1') return scenario === 'notes' ? FIXTURE.overview : { ...FIXTURE.overview, truncated: false, withheld: 0 }
    const center = r.url.searchParams.get('center')
    if (center === COMPANY) return FIXTURE.account
    if (scenario === 'empty') return { center: CENTER, nodes: [FIXTURE.customer.nodes[0]], edges: [], truncated: false, withheld: 0, generated_at: '2026-10-02T12:00:00Z' }
    // Depth 2: the customer's answer plus the other person of the company and the company's CRM id, numbered on.
    if (r.url.searchParams.get('depth') === '2') {
      const [person, handle] = FIXTURE.account.nodes.slice(3)
      return {
        ...FIXTURE.customer,
        nodes: [...FIXTURE.customer.nodes, { ...person!, id: 'n20', meta: { ...person!.meta, hop: 2 } }, { ...handle!, id: 'n21', meta: { ...handle!.meta, hop: 2 } }],
        edges: [
          ...FIXTURE.customer.edges,
          { id: 'e20', from: 'n20', to: 'n4', kind: 'member_of', meta: { role: 'technical', since: '2026-02-20T13:00:00Z' } },
          { id: 'e21', from: 'n21', to: 'n4', kind: 'identifies', meta: { strength: 'strong', weight: 'system', since: '2025-11-02T10:00:00Z' } },
        ],
      }
    }
    return scenario === 'notes' ? { ...FIXTURE.customer, truncated: true, withheld: 3 } : FIXTURE.customer
  }
}

const profile = { ...PROFILE, profile_id: CENTER }
const routes = (graph: Handler = graphRoute()) =>
  baseRoutes({
    [`GET ${API}/v1/profiles/${CENTER}`]: profile,
    [`GET ${API}/v1/profiles/${CENTER}/memory`]: { ...MEMORY, profile_id: CENTER },
    [`GET ${API}/v1/graph`]: graph,
  })

const openTab = () => signInAt(`${at(`clientes/${CENTER}`)}?aba=relacoes`)

describe('the Relações tab of a customer', () => {
  it('draws the graph around the customer, with the legend counting each kind and the list saying the same', async () => {
    const api = mockApi(routes())
    const { user } = await openTab()
    expect(await screen.findByRole('tab', { name: 'Relações', selected: true })).toBeInTheDocument()
    const canvas = await screen.findByRole('application', { name: 'Mapa de relações do cliente' })
    // Every node is a button a screen reader names by kind and label; the center says so.
    expect(within(canvas).getByRole('button', { name: /^Pessoa p_eyxl5q, \d+ relações, o cliente desta tela$/ })).toBeInTheDocument()
    expect(within(canvas).getAllByRole('button')).toHaveLength(FIXTURE.customer.nodes.length)
    expect(within(canvas).getByRole('button', { name: /^Objeto Pedido PED-90039/ })).toBeInTheDocument()
    expect(within(canvas).getByRole('button', { name: /^Agente Fila: fechamento/ })).toBeInTheDocument()
    // The legend: kinds in Portuguese with counts; it is also the filter.
    const legend = screen.getByRole('group', { name: 'Legenda e filtro por tipo' })
    expect(within(legend).getByRole('button', { name: /^Pessoas 2$/ })).toHaveAttribute('aria-pressed', 'true')
    expect(within(legend).getByRole('button', { name: /^Objetos 4$/ })).toBeInTheDocument()
    expect(within(legend).getByRole('button', { name: /^Identificadores 2$/ })).toBeInTheDocument()
    // The first call asked for depth 1 around this customer.
    const first = api.find('GET', '/v1/graph')[0]!
    expect(first.url.searchParams.get('center')).toBe(CENTER)
    expect(first.url.searchParams.get('depth')).toBe('1')
    // The list carries the same items and relations, in words.
    await user.click(screen.getByRole('button', { name: 'Ver como lista' }))
    const table = screen.getByRole('table', { name: 'Itens do mapa e as relações de cada um' })
    const rows = within(table).getAllByRole('row').slice(1)
    expect(rows).toHaveLength(FIXTURE.customer.nodes.length)
    expect(rows[0]).toHaveTextContent('p_eyxl5q5omfynrwd5')
    expect(rows[0]).toHaveTextContent('O cliente desta tela')
    expect(rows[0]).toHaveTextContent('Objetos: mostrando 4 de 312, os mais recentes')
    expect(rows[0]).toHaveTextContent('+55*******0001 identifica este item')
    expect(rows[0]).toHaveTextContent('é dono de Pedido PED-90039')
    expect(rows[0]).toHaveTextContent('pode ser a mesma pessoa que p_c21dw9 (sugestão pendente)')
    expect(table).toHaveTextContent('Estado: Entregue')
    expect(within(table).getAllByRole('link', { name: 'Abrir' })[0]).toHaveAttribute('href', `/console/e/${SP_PROD}/clientes/${CENTER}`)
    // No contract value as written reaches the table.
    expect(table).not.toHaveTextContent(/\b(person|account|handle|object|agent|system|identifies|member_of|owns|talked_with|reported|handed_off|suggested_same)\b/)
    expect(table).not.toHaveTextContent('order PED')
  })

  it('hides a kind from the legend, in the canvas and in the list alike, and keeps the customer', async () => {
    mockApi(routes())
    const { user } = await openTab()
    const canvas = await screen.findByRole('application', { name: 'Mapa de relações do cliente' })
    const objects = screen.getByRole('button', { name: /^Objetos 4$/ })
    await user.click(objects)
    expect(objects).toHaveAttribute('aria-pressed', 'false')
    expect(within(canvas).getAllByRole('button')).toHaveLength(FIXTURE.customer.nodes.length - 4)
    await user.click(screen.getByRole('button', { name: 'Ver como lista' }))
    const table = screen.getByRole('table', { name: 'Itens do mapa e as relações de cada um' })
    expect(within(table).getAllByRole('row').slice(1)).toHaveLength(FIXTURE.customer.nodes.length - 4)
    expect(table).not.toHaveTextContent('Pedido PED-90039')
    expect(table).toHaveTextContent('p_eyxl5q5omfynrwd5')
  })

  it('opens the card on focus, expands a company with Enter by merging its graph, and closes the card with Escape', async () => {
    const api = mockApi(routes())
    const { user } = await openTab()
    const canvas = await screen.findByRole('application', { name: 'Mapa de relações do cliente' })
    const company = within(canvas).getByRole('button', { name: /^Empresa p_who365/ })
    company.focus()
    const card = await screen.findByRole('region', { name: 'Detalhes de p_who365r6p4drsarv' })
    expect(card).toHaveTextContent('Empresa')
    expect(card).toHaveTextContent('Identificadores1')
    expect(card).toHaveTextContent('p_eyxl5q faz parte de este item')
    expect(within(card).getByRole('link', { name: 'Abrir' })).toHaveAttribute('href', `/console/e/0192a000-0000-7000-8000-00000000a001/clientes/${COMPANY}`)
    await user.keyboard('{Enter}')
    await waitFor(() => expect(api.find('GET', '/v1/graph')).toHaveLength(2))
    expect(api.find('GET', '/v1/graph')[1]!.url.searchParams.get('center')).toBe(COMPANY)
    // The second person of the company and its CRM id joined the canvas; nothing was duplicated.
    await waitFor(() => expect(within(canvas).getByRole('button', { name: /^Pessoa p_9b02hh/ })).toBeInTheDocument())
    expect(within(canvas).getAllByRole('button', { name: /^Empresa p_who365/ })).toHaveLength(1)
    expect(within(canvas).getAllByRole('button')).toHaveLength(FIXTURE.customer.nodes.length + 2)
    expect(screen.getByRole('region', { name: 'Detalhes de p_who365r6p4drsarv' })).toHaveTextContent('As relações dele já estão no mapa.')
    await user.keyboard('{Escape}')
    expect(screen.queryByRole('region', { name: /^Detalhes de/ })).not.toBeInTheDocument()
  })

  it('brings the second level with one more call, and goes back to one', async () => {
    const api = mockApi(routes())
    const { user } = await openTab()
    await screen.findByRole('application', { name: 'Mapa de relações do cliente' })
    await user.click(screen.getByRole('button', { name: 'Mostrar mais um nível' }))
    await waitFor(() => expect(api.find('GET', '/v1/graph')).toHaveLength(2))
    expect(api.find('GET', '/v1/graph')[1]!.url.searchParams.get('depth')).toBe('2')
    expect(await screen.findByRole('button', { name: /^Pessoa p_9b02hh/ })).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Voltar a um nível' }))
    await waitFor(() => expect(api.find('GET', '/v1/graph')).toHaveLength(3))
    expect(await screen.findByRole('button', { name: 'Mostrar mais um nível' })).toBeInTheDocument()
  })

  it('finds a node by name and opens its card', async () => {
    mockApi(routes())
    const { user } = await openTab()
    await screen.findByRole('application', { name: 'Mapa de relações do cliente' })
    await user.type(screen.getByLabelText('Procurar um item pelo nome'), 'ch-2231')
    const found = screen.getByRole('list', { name: 'Itens encontrados' })
    await user.click(within(found).getByRole('button', { name: /Chamado CH-2231/ }))
    const card = await screen.findByRole('region', { name: 'Detalhes de Chamado CH-2231' })
    expect(card).toHaveTextContent('Estado: Em aberto')
    expect(card).toHaveTextContent('Sistema de origemCRM')
    expect(card).toHaveTextContent('Um objeto aparece pelos clientes ligados a ele.')
    await user.clear(screen.getByLabelText('Procurar um item pelo nome'))
    await user.type(screen.getByLabelText('Procurar um item pelo nome'), 'zzz')
    expect(screen.getByText(/Nenhum item com esse nome no mapa/)).toBeInTheDocument()
  })

  it('says when the server cut the graph and when the role hides items', async () => {
    mockApi(routes(graphRoute('notes')))
    await openTab()
    const note = (await screen.findByText(/itens mais próximos/)).closest('p')!
    expect(note).toHaveAttribute('role', 'status')
    expect(note).toHaveTextContent(`Mostrando os ${FIXTURE.customer.nodes.length} itens mais próximos deste cliente.`)
    expect(note).toHaveTextContent('3 itens ocultos pelo seu nível de acesso.')
    expect(note).toHaveTextContent('Objetos do cliente: mostrando 4 de 312, os mais recentes.')
  })

  it('says in the card how many objects the customer has beyond the map, and nothing when the server did not say', async () => {
    mockApi(routes())
    const { user } = await openTab()
    const canvas = await screen.findByRole('application', { name: 'Mapa de relações do cliente' })
    // Without truncation or withheld items, the objects alone bring the note.
    expect(screen.getByRole('status')).toHaveTextContent('Objetos do cliente: mostrando 4 de 312, os mais recentes.')
    within(canvas).getByRole('button', { name: /o cliente desta tela$/ }).focus()
    const card = await screen.findByRole('region', { name: 'Detalhes de p_eyxl5q5omfynrwd5' })
    expect(card).toHaveTextContent('Objetosmostrando 4 de 312, os mais recentes')
    await user.keyboard('{Escape}')
    within(canvas).getByRole('button', { name: /^Empresa p_who365/ }).focus()
    expect(await screen.findByRole('region', { name: 'Detalhes de p_who365r6p4drsarv' })).not.toHaveTextContent('Objetos')
  })

  it('has an honest empty state, an error with a retry, and an access-denied state', async () => {
    mockApi(routes(graphRoute('empty')))
    await openTab()
    expect(await screen.findByText('Este cliente ainda não tem relações registradas.')).toBeInTheDocument()
    expect(screen.getByText(/Uma relação nasce de um evento/)).toBeInTheDocument()

    mockApi(routes(graphRoute('error')))
    const { user } = await openTab()
    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('A Niadra teve um erro interno.')
    await user.click(within(alert).getByRole('button', { name: 'Tentar de novo' }))
    expect(await screen.findByRole('application', { name: 'Mapa de relações do cliente' })).toBeInTheDocument()

    mockApi(routes(graphRoute('forbidden')))
    await openTab()
    expect(await screen.findByText('O seu papel não vê as relações deste espaço.')).toBeInTheDocument()
  })
})

describe('the Mapa de relações screen', () => {
  it('is in the rail, loads the overview of the space and expands a customer on click', async () => {
    const api = mockApi(routes(graphRoute('notes')))
    const { user } = await signInAt(at('relacoes'))
    expect(await screen.findByRole('heading', { level: 1, name: 'Mapa de relações' })).toBeInTheDocument()
    expect(within(screen.getByRole('navigation', { name: 'Seções do Console' })).getByRole('link', { name: 'Mapa de relações' })).toHaveAttribute('aria-current', 'page')
    const canvas = await screen.findByRole('application', { name: 'Mapa de relações do espaço' })
    expect(api.find('GET', '/v1/graph')[0]!.url.searchParams.get('overview')).toBe('1')
    // No instructions under the drawing; the keys are said to a screen reader only, from the canvas.
    expect(screen.queryByText(/Clique num item para acendê-lo/)).not.toBeInTheDocument()
    expect(canvas).toHaveAccessibleDescription(/Setas movem entre os itens/)
    // The map draws what has a tie among profiles. Agents and systems start hidden (they touch everyone), and so
    // does the customer whose only line is an agent's; a note counts it, with the list one click away.
    const sources = FIXTURE.overview.nodes.filter((n) => n.kind === 'agent' || n.kind === 'system').length
    expect(within(canvas).getAllByRole('button')).toHaveLength(FIXTURE.overview.nodes.length - sources - 1)
    expect(within(canvas).queryByRole('button', { name: /^Pessoa p_s0l1t4/ })).not.toBeInTheDocument()
    expect(screen.getByText(/1 cliente sem vínculo com empresa ou parceiro não aparece aqui\./)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /^Agentes 2$/ })).toHaveAttribute('aria-pressed', 'false')
    // The loose customers come back on their own button.
    const looseToggle = screen.getByRole('button', { name: 'Mostrar clientes sem vínculo' })
    expect(looseToggle).toHaveAttribute('aria-pressed', 'false')
    await user.click(looseToggle)
    expect(within(canvas).getByRole('button', { name: /^Pessoa p_s0l1t4/ })).toBeInTheDocument()
    expect(screen.queryByText(/não aparece aqui/)).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Esconder clientes sem vínculo' }))
    expect(within(canvas).queryByRole('button', { name: /^Pessoa p_s0l1t4/ })).not.toBeInTheDocument()
    // And with the agents and systems, since they are what those customers are tied to.
    const toggle = screen.getByRole('button', { name: 'Mostrar agentes e sistemas' })
    expect(toggle).toHaveAttribute('aria-pressed', 'false')
    await user.click(toggle)
    expect(within(canvas).getAllByRole('button')).toHaveLength(FIXTURE.overview.nodes.length)
    expect(screen.getByRole('button', { name: 'Esconder agentes e sistemas' })).toHaveAttribute('aria-pressed', 'true')
    expect(screen.getByRole('button', { name: /^Sistemas 2$/ })).toHaveAttribute('aria-pressed', 'true')
    expect(screen.queryByRole('button', { name: /sem vínculo/ })).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Esconder agentes e sistemas' }))
    // The note's link opens the list narrowed to those customers.
    await user.click(screen.getByRole('button', { name: 'Ver na lista' }))
    const table = screen.getByRole('table', { name: 'Itens do mapa e as relações de cada um' })
    expect(within(table).getAllByRole('row').slice(1)).toHaveLength(1)
    expect(table).toHaveTextContent('p_s0l1t4ry8qn2vx9k')
    await user.click(screen.getByRole('button', { name: 'Ver todos' }))
    expect(within(table).getAllByRole('row').slice(1)).toHaveLength(FIXTURE.overview.nodes.length - sources)
    await user.click(screen.getByRole('button', { name: 'Ver o grafo' }))
    expect(screen.queryByRole('button', { name: 'Mostrar mais um nível' })).not.toBeInTheDocument()
    expect(screen.getByText(/3 itens ocultos pelo seu nível de acesso\./)).toBeInTheDocument()
    // The canvas is a new one after the list: find it again. Clicking a company brings its graph in.
    const again = await screen.findByRole('application', { name: 'Mapa de relações do espaço' })
    await user.click(within(again).getByRole('button', { name: /^Empresa p_who365/ }))
    await waitFor(() => expect(api.find('GET', '/v1/graph')).toHaveLength(2))
    expect(api.find('GET', '/v1/graph')[1]!.url.searchParams.get('center')).toBe(COMPANY)
    await waitFor(() => expect(within(again).getByRole('button', { name: /^Identificador CL\*\*\*\*\*01/ })).toBeInTheDocument())
    expect(within(again).getAllByRole('button', { name: /^Pessoa p_9b02hh/ })).toHaveLength(1)
    // The sources are hidden again and the loose customer with them: the company's answer added one identifier.
    expect(within(again).getAllByRole('button')).toHaveLength(FIXTURE.overview.nodes.length - sources - 1 + 1)
  })
})
