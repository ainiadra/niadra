import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { describe, expect, it } from 'vitest'
import type { GraphEdgeKind, GraphNode, GraphNodeKind, GraphOut } from '../api/graph'
import { canonical, countByKind, fromResponse, linkOf, looseProfiles, merge, objectsShown, relationsOf, rows, search, visibleEdges, visibleIds } from '../lib/graph'
import { displayLabel, edgeShortLabel, edgeVerb, metaLines, objectsLine, NODE_KINDS, nodeKind, nodeKindPlural, nodeSummary, shortPseudonym } from '../lib/graphLabels'
import { SP_PROD } from './harness'

const FIXTURE = JSON.parse(readFileSync(join(__dirname, 'graph.fixture.json'), 'utf8')) as { customer: GraphOut; account: GraphOut; overview: GraphOut }
const CUSTOMER = 'person:01a0b2c4-0000-7000-8000-0000000000f2'
const COMPANY = 'account:01a0b2c4-0000-7000-8000-0000000000e4'

describe('the graph as the screen holds it', () => {
  it('keys nodes by what they are, so the same customer in two answers is one node', () => {
    const c = canonical(FIXTURE.customer)
    expect(c.center).toBe(CUSTOMER)
    const ids = c.nodes.map((n) => n.id)
    expect(ids).toContain(COMPANY)
    // An identifier hangs from the profile it identifies; a queue from the agent that handed off to it.
    expect(ids).toContain(`handle:+55*******0001@${CUSTOMER}`)
    expect(ids).toContain('agent:fechamento@agent:0192a000-0000-7000-8000-00000000b001')
    // Edge ends are remapped, and `about` on a hand-off too.
    const handoff = c.edges.find((e) => e.kind === 'handed_off')!
    expect(handoff.from).toBe('agent:0192a000-0000-7000-8000-00000000b001')
    expect(handoff.meta.about).toBe(CUSTOMER)
    expect(c.edges.every((e) => ids.includes(e.from) && ids.includes(e.to))).toBe(true)
  })

  it('drops an edge whose end is not in the answer', () => {
    const r: GraphOut = { ...FIXTURE.customer, edges: [...FIXTURE.customer.edges, { id: 'e99', from: 'n1', to: 'n77', kind: 'owns', meta: {} }] }
    const g = fromResponse(r)
    expect(g.edges.size).toBe(FIXTURE.customer.edges.length)
    expect(g.expanded.has(CUSTOMER)).toBe(true)
  })

  it('merges an expansion without duplicating what is already on the canvas, and reports the new nodes', () => {
    const g = fromResponse(FIXTURE.customer)
    const before = g.nodes.size
    const { graph, added } = merge(g, FIXTURE.account, COMPANY)
    // The company, its parent and the customer were already there; the second person and the CRM id are new.
    expect(added.sort()).toEqual([`handle:CL*****01@${COMPANY}`, 'person:01a0b2c4-0000-7000-8000-0000000000f3'].sort())
    expect(graph.nodes.size).toBe(before + 2)
    // The member_of edge both answers carry is one edge.
    expect([...graph.edges.values()].filter((e) => e.kind === 'member_of' && e.from === CUSTOMER)).toHaveLength(1)
    expect(graph.expanded.has(COMPANY)).toBe(true)
    expect(graph.center).toBe(CUSTOMER)
    // Merging the same answer again adds nothing.
    expect(merge(graph, FIXTURE.account, COMPANY).added).toEqual([])
  })

  it('counts the objects of a customer on the canvas once, however many answers brought them', () => {
    const g = fromResponse(FIXTURE.customer)
    expect(objectsShown(g, CUSTOMER)).toBe(4)
    // The company's answer names the customer again, with the same objects' edges and a fresher total.
    const again: GraphOut = {
      ...FIXTURE.account,
      nodes: FIXTURE.account.nodes.map((n) => (n.id === 'n3' ? { ...n, meta: { ...n.meta, objects_total: 313 } } : n)),
      edges: [...FIXTURE.account.edges, { id: 'e90', from: 'n3', to: 'n9', kind: 'owns', meta: { role: 'owner' } }],
    }
    const withObject: GraphOut = { ...again, nodes: [...again.nodes, { id: 'n9', kind: 'object', label: 'order PED-90039', ref: '01a0b2c4-0000-7000-8000-000000000012', meta: { hop: 2, type: 'order', external_id: 'PED-90039' } }] }
    const { graph } = merge(g, withObject, COMPANY)
    expect(objectsShown(graph, CUSTOMER)).toBe(4)
    expect(graph.nodes.get(CUSTOMER)!.meta.objects_total).toBe(313)
    expect(objectsLine(graph.nodes.get(CUSTOMER)!, objectsShown(graph, CUSTOMER))).toBe('mostrando 4 de 313, os mais recentes')
    // A node without the field says nothing; the company owns nothing on the canvas.
    expect(objectsLine(graph.nodes.get(COMPANY)!, objectsShown(graph, COMPANY))).toBeNull()
  })

  it('names the customers with no tie among profiles, whatever an agent or a system says about them', () => {
    const g = fromResponse(FIXTURE.overview)
    const all = visibleIds(g, new Set())
    expect([...looseProfiles(g, all)]).toEqual(['person:01a0b2c4-0000-7000-8000-0000000000f7'])
    // With the companies hidden by the legend, a member loses its tie; the partner's person keeps its own.
    const noCompanies = visibleIds(g, new Set<GraphNodeKind>(['account']))
    const loose = looseProfiles(g, noCompanies)
    expect(loose.has(CUSTOMER)).toBe(true)
    expect(loose.has('person:01a0b2c4-0000-7000-8000-0000000000f3')).toBe(false)
    // The center of a customer's tab never counts, and agents and systems are not profiles.
    const c = fromResponse({ ...FIXTURE.customer, edges: [] })
    expect(looseProfiles(c, visibleIds(c, new Set())).has(CUSTOMER)).toBe(false)
    expect([...looseProfiles(c, visibleIds(c, new Set()))].every((id) => !id.startsWith('agent:') && !id.startsWith('system:'))).toBe(true)
  })

  it('accumulates the notes of the answers', () => {
    const g = fromResponse({ ...FIXTURE.customer, withheld: 2 })
    const { graph } = merge(g, { ...FIXTURE.account, truncated: true, withheld: 1 }, COMPANY)
    expect(graph.truncated).toBe(true)
    expect(graph.withheld).toBe(3)
  })

  it('hides a kind and the edges touching it, never the center', () => {
    const g = fromResponse(FIXTURE.customer)
    const counts = countByKind(g)
    expect(counts.object).toBe(4)
    expect(counts.handle).toBe(2)
    const ids = visibleIds(g, new Set<GraphNodeKind>(['object', 'person']))
    expect(ids.has(CUSTOMER)).toBe(true)
    expect([...ids].some((id) => g.nodes.get(id)!.kind === 'object')).toBe(false)
    const edges = visibleEdges(g, ids)
    expect(edges.some((e) => e.kind === 'owns' || e.kind === 'reported' || e.kind === 'suggested_same')).toBe(false)
    expect(edges.some((e) => e.kind === 'identifies')).toBe(true)
  })

  it('finds a node by its label as sent or as shown, accents and case aside', () => {
    const g = fromResponse(FIXTURE.customer)
    const all = visibleIds(g, new Set())
    expect(search(g, 'ped-90039', all).map((n) => displayLabel(n))).toEqual(['Pedido PED-90039'])
    expect(search(g, 'PEDIDO', all)).toHaveLength(3)
    expect(search(g, 'p_eyxl', all).map((n) => n.id)).toEqual([CUSTOMER])
    expect(search(g, 'cobrança', all).map((n) => n.label)).toEqual(['cobranca'])
    expect(search(g, '  ', all)).toEqual([])
  })

  it('reads the list from the same graph as the canvas: every visible node, every visible relation', () => {
    const g = fromResponse(FIXTURE.customer)
    const hidden = new Set<GraphNodeKind>(['system'])
    const ids = visibleIds(g, hidden)
    const list = rows(g, ids)
    expect(list.map((r) => r.node.id).sort()).toEqual([...ids].sort())
    expect(list[0]!.node.id).toBe(CUSTOMER)
    const edges = visibleEdges(g, ids)
    for (const row of list) {
      const touching = edges.filter((e) => e.from === row.node.id || e.to === row.node.id)
      expect(row.relations.map((r) => r.edge.id).sort(), row.node.id).toEqual(touching.map((e) => e.id).sort())
    }
    // Sentences read from the node's side.
    const center = list[0]!.relations.map((r) => r.text)
    expect(center).toContain('+55*******0001 identifica este item')
    expect(center).toContain('faz parte de p_who365')
    expect(center).toContain('é dono de Pedido PED-90039')
    expect(center).toContain('sac-voz atendeu este item')
    expect(center).toContain('pode ser a mesma pessoa que p_c21dw9')
  })

  it('links each kind to its screen, and says when there is none', () => {
    const g = fromResponse(FIXTURE.customer)
    const n = (id: string) => g.nodes.get(id)!
    expect(linkOf(g, n(CUSTOMER), SP_PROD)).toBe(`/console/e/${SP_PROD}/clientes/01a0b2c4-0000-7000-8000-0000000000f2`)
    expect(linkOf(g, n(`handle:+55*******0001@${CUSTOMER}`), SP_PROD)).toBe(`/console/e/${SP_PROD}/clientes/01a0b2c4-0000-7000-8000-0000000000f2?aba=ids`)
    expect(linkOf(g, n('object:01a0b2c4-0000-7000-8000-000000000012'), SP_PROD)).toBe(`/console/e/${SP_PROD}/clientes/01a0b2c4-0000-7000-8000-0000000000f2?aba=objetos`)
    expect(linkOf(g, n('agent:0192a000-0000-7000-8000-00000000b001'), SP_PROD)).toBe(`/console/e/${SP_PROD}/agentes/0192a000-0000-7000-8000-00000000b001`)
    expect(linkOf(g, n('system:0192a000-0000-7000-8000-00000000b003'), SP_PROD)).toBe(`/console/e/${SP_PROD}/agentes/0192a000-0000-7000-8000-00000000b003`)
    expect(linkOf(g, n('agent:fechamento@agent:0192a000-0000-7000-8000-00000000b001'), SP_PROD)).toBeNull()
    expect(relationsOf(g, 'agent:fechamento@agent:0192a000-0000-7000-8000-00000000b001').map((r) => r.text)).toEqual(['sac-voz transferiu para este item'])
  })
})

describe('the words of the graph', () => {
  const EDGE_KINDS: GraphEdgeKind[] = ['identifies', 'member_of', 'partner_of', 'parent_of', 'owns', 'relates_to', 'acted_on', 'talked_with', 'reported', 'handed_off', 'suggested_same']

  it('names every kind of node and of relation in Portuguese, never by the contract value', () => {
    for (const k of NODE_KINDS) {
      expect(nodeKind(k)).not.toBe(k)
      expect(nodeKind(k)).toMatch(/^[A-ZÁÉÍÓÚ]/)
      expect(nodeKindPlural(k)).not.toBe(nodeKind(k))
    }
    for (const k of EDGE_KINDS) {
      expect(edgeVerb(k)).not.toBe(k)
      expect(edgeVerb(k)).not.toMatch(/_/)
    }
    expect(edgeVerb('suggested_same')).toBe('pode ser a mesma pessoa que')
    // The word on a line: the verb, with the count when the relation repeats, and the suggestion in short.
    expect(edgeShortLabel({ id: 'e', from: 'a', to: 'b', kind: 'talked_with', meta: { count: 4 } })).toBe('atendeu 4x')
    expect(edgeShortLabel({ id: 'e', from: 'a', to: 'b', kind: 'acted_on', meta: { count: 1 } })).toBe('agiu sobre')
    expect(edgeShortLabel({ id: 'e', from: 'a', to: 'b', kind: 'suggested_same', meta: { score: 0.8 } })).toBe('pode ser a mesma pessoa')
    // A person's edge to an object counts what they said about it.
    expect(edgeShortLabel({ id: 'e', from: 'a', to: 'b', kind: 'owns', meta: { role: 'lawyer', observations: 2, instructions: 1 } })).toBe('é dono de · 2 ditos, 1 instrução')
    expect(edgeShortLabel({ id: 'e', from: 'a', to: 'b', kind: 'owns', meta: { role: 'owner' } })).toBe('é dono de')
    const said = Object.fromEntries(metaLines({ observations: 2, instructions: 1 }).map((l) => [l.label, l.value]))
    expect(said).toEqual({ 'Dito nas conversas': '2', Instruções: '1' })
  })

  it('shows what the canvas has room for: a short pseudonym, the object type in words, a queue as a queue', () => {
    const g = fromResponse(FIXTURE.customer)
    const n = (id: string) => g.nodes.get(id)!
    expect(shortPseudonym('p_eyxl5q5omfynrwd5')).toBe('p_eyxl5q')
    expect(shortPseudonym('p_7f3a')).toBe('p_7f3a')
    expect(displayLabel(n(CUSTOMER))).toBe('p_eyxl5q')
    expect(displayLabel(n('object:01a0b2c4-0000-7000-8000-000000000015'))).toBe('Chamado CH-2231')
    expect(displayLabel(n('agent:fechamento@agent:0192a000-0000-7000-8000-00000000b001'))).toBe('Fila: fechamento')
    expect(nodeSummary(n('object:01a0b2c4-0000-7000-8000-000000000012'))).toBe('Estado: Entregue')
    expect(nodeSummary(n(`handle:+55*******0001@${CUSTOMER}`))).toBe('WhatsApp')
    expect(nodeSummary({ ...n('object:01a0b2c4-0000-7000-8000-000000000012'), meta: { type: 'order', state: null, masked: true } })).toBe('Estado mascarado para o seu papel')
  })

  it('reads every value of a node or a relation with a label, and never drops an unknown one', () => {
    const edge = Object.fromEntries(metaLines({ strength: 'weak', weight: 'declared', since: '2026-03-12T14:02:00Z' }).map((l) => [l.label, l.value]))
    expect(edge).toEqual({ 'Força da identificação': 'Fraca', 'Peso do identificador': 'Declarado', Desde: '12/03/2026 11:02' })
    const talked = Object.fromEntries(metaLines({ count: 4, last_at: '2026-09-29T17:40:00Z', channels: ['voice', 'whatsapp'] }).map((l) => [l.label, l.value]))
    expect(talked).toEqual({ Vezes: '4', 'Última vez': '29/09/2026 14:40', Canais: 'Voz, WhatsApp' })
    const handoff = Object.fromEntries(metaLines({ count: 1, status: 'closed', outcome: 'venda_fechada', about: 'n1' }).map((l) => [l.label, l.value]))
    expect(handoff).toEqual({ Vezes: '1', Situação: 'Encerrada', Desfecho: 'venda fechada' })
    const member = Object.fromEntries(metaLines({ role: 'compras' }).map((l) => [l.label, l.value]))
    expect(member).toEqual({ Papel: 'compras' })
    expect(Object.fromEntries(metaLines({ role: 'owner' }).map((l) => [l.label, l.value]))).toEqual({ Papel: 'Dono' })
    const object = Object.fromEntries(metaLines({ hop: 1, type: 'order', namespace: 'erp', external_id: 'PED-90039', state: 'delivered', state_label: null, masked: false, as_of: '2026-09-20T12:05:00Z' }, 'object').map((l) => [l.label, l.value]))
    expect(object).toEqual({ Tipo: 'Pedido', 'Sistema de origem': 'ERP', 'Número no sistema': 'PED-90039', Estado: 'Entregue', 'Atualizado em': '20/09/2026 09:05' })
    const unknownState = metaLines({ type: 'warranty_claim', state: 'awaiting_parts' }, 'object')
    expect(unknownState).toEqual([
      { label: 'Tipo (valor técnico)', value: 'warranty_claim' },
      { label: 'Estado (valor técnico)', value: 'awaiting_parts' },
    ])
    const handle = Object.fromEntries(metaLines({ hop: 1, type: 'wa_id', channel: 'whatsapp', weight: 'verified', provisional: true, last_seen: '2026-09-29T17:40:00Z' }, 'handle').map((l) => [l.label, l.value]))
    expect(handle).toEqual({ Tipo: 'WhatsApp', Canal: 'WhatsApp', 'Peso do identificador': 'Verificado', Provisório: 'Sim, ainda sem confirmação', 'Visto pela última vez': '29/09/2026 14:40' })
    const profile = Object.fromEntries(metaLines({ hop: 0, pseudonym: 'p_eyxl5q5omfynrwd5', handles: 2, links: 1, objects: 4, interactions: 6, score: 13 }, 'person').map((l) => [l.label, l.value]))
    expect(profile).toEqual({ Identificadores: '2', Vínculos: '1', Objetos: '4', 'Conversas e ações': '6' })
    expect(metaLines({ something_new: 'x_y' })).toEqual([{ label: 'Valor do sistema (something new)', value: 'x y' }])
    expect(metaLines({ target: 'queue:fechamento' })).toEqual([{ label: 'Destino da transferência', value: 'Fila fechamento' }])
    // The whole count of objects is read with the ones on the canvas, never as a bare value.
    expect(metaLines({ objects_total: 312 }, 'person')).toEqual([])
  })

  it('says how many of a customer\'s objects the map shows, against how many there are', () => {
    const node = (total: unknown): GraphNode => ({ id: 'x', kind: 'person', label: 'p_1', ref: 'p1', meta: total === undefined ? {} : { objects_total: total as number } })
    expect(objectsLine(node(312), 4)).toBe('mostrando 4 de 312, os mais recentes')
    expect(objectsLine(node(1312), 25)).toBe('mostrando 25 de 1.312, os mais recentes')
    expect(objectsLine(node(4), 4)).toBe('4, todos no mapa')
    expect(objectsLine(node(1), 1)).toBe('1, no mapa')
    expect(objectsLine(node(0), 0)).toBe('nenhum')
    expect(objectsLine(node(undefined), 4)).toBeNull()
    expect(objectsLine(node(null), 4)).toBeNull()
    expect(objectsLine(node('312'), 4)).toBeNull()
  })
})
