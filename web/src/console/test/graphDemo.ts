import type { GraphEdge, GraphNode, GraphOut } from '../api/graph'
import { seeded } from '../lib/graphLayout'

/**
 * A space shaped like the demo one: a dozen companies, sixty people (most in a company, some in none), three
 * agents and three systems wired to most of the people, a few partners and pending suggestions. Built from a
 * seed, so the layout tests see the same graph every time. The shape is what matters: a few hubs touching
 * nearly everyone, which is what knots a drawing.
 */
export function demoLike(seed = 7, people = 60, companies = 12): GraphOut {
  const r = seeded(seed)
  const nodes: GraphNode[] = []
  const edges: GraphEdge[] = []
  const alpha = 'abcdefghijklmnopqrstuvwxyz0123456789'
  const code = () => Array.from({ length: 16 }, () => alpha[Math.floor(r() * alpha.length)]).join('')
  let n = 0
  let e = 0
  const node = (kind: GraphNode['kind'], label: string, ref: string | null, meta: GraphNode['meta'] = {}): GraphNode => {
    n += 1
    const id = `n${n}`
    nodes.push({ id, kind, label, ref, meta: { hop: 0, ...meta } })
    return nodes[nodes.length - 1]!
  }
  const edge = (from: GraphNode, to: GraphNode, kind: GraphEdge['kind'], meta: GraphEdge['meta'] = {}) => {
    e += 1
    edges.push({ id: `e${e}`, from: from.id, to: to.id, kind, meta })
  }
  const cos = Array.from({ length: companies }, () => node('account', `p_${code()}`, `acc-${n}`, { pseudonym: 'x', handles: 1 }))
  const partners = Array.from({ length: 3 }, () => node('partner', `p_${code()}`, `par-${n}`, { pseudonym: 'x', handles: 1 }))
  const agents = ['sac-voz', 'vendas-whatsapp', 'cobranca'].map((name) => node('agent', name, `src-${name}`, { channel: 'voice' }))
  const systems = ['erp', 'crm', 'plataforma-loja'].map((name) => node('system', name, `src-${name}`, { channel: name }))
  // The second company is the parent of the first: a chain of two.
  edge(cos[1]!, cos[0]!, 'parent_of', {})
  const persons: GraphNode[] = []
  for (let i = 0; i < people; i += 1) {
    const p = node('person', `p_${code()}`, `per-${n}`, { pseudonym: 'x', handles: 1 + Math.floor(r() * 3) })
    persons.push(p)
    if (r() < 0.7) edge(p, cos[Math.floor(r() * companies)]!, 'member_of', { role: 'compras' })
    if (r() < 0.1) edge(p, partners[Math.floor(r() * partners.length)]!, 'partner_of', { role: 'driver' })
    for (const a of agents) if (r() < 0.6) edge(a, p, 'talked_with', { count: 1 + Math.floor(r() * 9), channels: ['voice'] })
    for (const sys of systems) if (r() < 0.8) edge(sys, p, 'reported', { count: 1 + Math.floor(r() * 20) })
  }
  for (let i = 0; i < 4; i += 1) edge(persons[i]!, persons[people - 1 - i]!, 'suggested_same', { score: 0.7 + r() * 0.2 })
  return { center: null, nodes, edges, truncated: false, withheld: 0, generated_at: '2026-10-02T12:00:00Z' }
}
