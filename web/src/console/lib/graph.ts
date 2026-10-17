/**
 * The relationship graph as the screen holds it: what the server sent, merged across expansions, filtered by
 * kind, searched by label, and read as a list. Pure functions, so the canvas and the list view read the same
 * graph and the tests prove they agree.
 *
 * The server numbers nodes and edges per answer (`n1`, `e1`...), so two answers about the same customer name
 * the same thing differently. The canvas keys every node by what it is instead: `person:<profile id>` for
 * anything with a `ref`, and for an identifier or a hand-off queue (no `ref`) its kind, its label and the node
 * it hangs from. An edge is keyed by its kind and its two ends. Merging is then a set union.
 */
import type { GraphEdge, GraphEdgeKind, GraphNode, GraphNodeKind, GraphOut } from '../api/graph'
import { paths } from '../paths'
import { displayLabel, edgeVerb } from './graphLabels'

export interface Graph {
  /** The key of the center node; null in the overview. */
  center: string | null
  /** Nodes by key; a node's `id` is its key. */
  nodes: Map<string, GraphNode>
  edges: Map<string, GraphEdge>
  /** Nodes already expanded (the center counts), so a second click does not fetch again. */
  expanded: Set<string>
  truncated: boolean
  withheld: number
}

/** Re-keys one answer: nodes by identity, edges by kind and ends, so answers can be merged. */
export function canonical(r: GraphOut): { center: string | null; nodes: GraphNode[]; edges: GraphEdge[] } {
  const keys = new Map<string, string>()
  for (const n of r.nodes) if (n.ref) keys.set(n.id, `${n.kind}:${n.ref}`)
  // An identifier hangs from the profile it identifies; a queue from the agent that handed off to it.
  for (const n of r.nodes) {
    if (keys.has(n.id)) continue
    const anchor = r.edges.find((e) => (e.kind === 'identifies' && e.from === n.id) || (e.kind === 'handed_off' && e.to === n.id))
    const anchorKey = anchor ? keys.get(anchor.kind === 'identifies' ? anchor.to : anchor.from) : null
    keys.set(n.id, `${n.kind}:${n.label}@${anchorKey ?? 'solto'}`)
  }
  const seen = new Set<string>()
  const nodes: GraphNode[] = []
  for (const n of r.nodes) {
    const id = keys.get(n.id)!
    if (seen.has(id)) continue
    seen.add(id)
    nodes.push({ ...n, id })
  }
  const edges: GraphEdge[] = []
  const edgeSeen = new Set<string>()
  for (const e of r.edges) {
    const from = keys.get(e.from)
    const to = keys.get(e.to)
    if (!from || !to) continue
    const id = `${e.kind}:${from}>${to}`
    if (edgeSeen.has(id)) continue
    edgeSeen.add(id)
    const about = typeof e.meta.about === 'string' ? keys.get(e.meta.about) : undefined
    edges.push({ ...e, id, from, to, meta: about ? { ...e.meta, about } : e.meta })
  }
  const centerNode = r.center ? r.nodes.find((n) => n.ref === r.center) ?? r.nodes[0] : null
  return { center: centerNode ? keys.get(centerNode.id)! : null, nodes, edges }
}

export function fromResponse(r: GraphOut): Graph {
  const c = canonical(r)
  const nodes = new Map(c.nodes.map((n) => [n.id, n]))
  return { center: c.center, nodes, edges: new Map(c.edges.map((e) => [e.id, e])), expanded: new Set(c.center ? [c.center] : []), truncated: r.truncated, withheld: r.withheld }
}

/**
 * Merges the graph around an expanded node into the canvas: new nodes and edges are added, nodes already on
 * the canvas keep their place and take the fresher data, and the notes (truncated, withheld) accumulate.
 * Returns the keys that are new, for the layout to place at the expanded node.
 */
export function merge(graph: Graph, r: GraphOut, expandedId: string): { graph: Graph; added: string[] } {
  const c = canonical(r)
  const nodes = new Map(graph.nodes)
  const edges = new Map(graph.edges)
  const added: string[] = []
  for (const n of c.nodes) {
    if (!nodes.has(n.id)) added.push(n.id)
    nodes.set(n.id, n)
  }
  for (const e of c.edges) edges.set(e.id, e)
  const expanded = new Set(graph.expanded)
  expanded.add(expandedId)
  return {
    graph: { center: graph.center, nodes, edges, expanded, truncated: graph.truncated || r.truncated, withheld: graph.withheld + r.withheld },
    added,
  }
}

/** A tie of the structure: who belongs to whom, who owns what, who may be whom. A source's lines are not one. */
const STRUCTURAL: ReadonlySet<GraphEdgeKind> = new Set<GraphEdgeKind>(['member_of', 'partner_of', 'parent_of', 'identifies', 'owns', 'relates_to', 'suggested_same'])

/**
 * The profiles (people, companies, partners) among `ids` with no tie of the structure to anything else among
 * `ids`: customers whose only lines are an agent's or a system's. The map of the space leaves them out by
 * default and says how many there are; the center of a customer's tab never counts.
 */
export function looseProfiles(graph: Graph, ids: ReadonlySet<string>): Set<string> {
  const tied = new Set<string>()
  for (const e of graph.edges.values()) {
    if (!STRUCTURAL.has(e.kind) || !ids.has(e.from) || !ids.has(e.to)) continue
    tied.add(e.from)
    tied.add(e.to)
  }
  const loose = new Set<string>()
  for (const n of graph.nodes.values()) {
    if (!ids.has(n.id) || n.id === graph.center || tied.has(n.id)) continue
    if (n.kind === 'person' || n.kind === 'account' || n.kind === 'partner') loose.add(n.id)
  }
  return loose
}

/** The edges touching a node, in the order the server sent them. */
export function edgesOf(graph: Graph, id: string): GraphEdge[] {
  const out: GraphEdge[] = []
  for (const e of graph.edges.values()) if (e.from === id || e.to === id) out.push(e)
  return out
}

/**
 * How many objects of a profile the canvas holds: its `owns` edges, each object once however many answers
 * brought it (edges are keyed by kind and ends). The server sends at most the 25 latest per customer and says
 * the whole count in `meta.objects_total`.
 */
export function objectsShown(graph: Graph, id: string): number {
  let n = 0
  for (const e of graph.edges.values()) if (e.kind === 'owns' && e.from === id) n += 1
  return n
}

/** How many nodes of each kind the graph holds, for the legend. */
export function countByKind(graph: Graph): Record<GraphNodeKind, number> {
  const counts: Record<GraphNodeKind, number> = { person: 0, account: 0, partner: 0, handle: 0, object: 0, agent: 0, system: 0 }
  for (const n of graph.nodes.values()) counts[n.kind] += 1
  return counts
}

/**
 * The nodes the filters leave visible: the kinds turned on, plus the center, which never hides. An edge is
 * visible when both ends are.
 */
export function visibleIds(graph: Graph, hidden: ReadonlySet<GraphNodeKind>): Set<string> {
  const ids = new Set<string>()
  for (const n of graph.nodes.values()) if (!hidden.has(n.kind) || n.id === graph.center) ids.add(n.id)
  return ids
}

export function visibleEdges(graph: Graph, ids: ReadonlySet<string>): GraphEdge[] {
  const out: GraphEdge[] = []
  for (const e of graph.edges.values()) if (ids.has(e.from) && ids.has(e.to)) out.push(e)
  return out
}

const fold = (s: string) =>
  s
    .normalize('NFD')
    .replace(/[̀-ͯ]/g, '')
    .toLowerCase()

/** The nodes whose label, as the server sent it or as the canvas shows it, holds the typed text, accents and case aside. */
export function search(graph: Graph, text: string, ids: ReadonlySet<string>): GraphNode[] {
  const q = fold(text.trim())
  if (!q) return []
  const out: GraphNode[] = []
  for (const n of graph.nodes.values()) {
    if (!ids.has(n.id)) continue
    if (fold(n.label).includes(q) || fold(displayLabel(n)).includes(q)) out.push(n)
  }
  return out
}

export interface Relation {
  edge: GraphEdge
  /** The other end of the relation. */
  other: GraphNode
  /** The sentence as read from this node: "identifica p_7f3a", or "+55 ... identifica este item". */
  text: string
}

/** The relations of a node, each read from the node's side, for the card and the list. */
export function relationsOf(graph: Graph, id: string, ids?: ReadonlySet<string>): Relation[] {
  const out: Relation[] = []
  for (const e of edgesOf(graph, id)) {
    const otherId = e.from === id ? e.to : e.from
    if (ids && !ids.has(otherId)) continue
    const other = graph.nodes.get(otherId)
    if (!other) continue
    const text = e.from === id ? `${edgeVerb(e.kind)} ${displayLabel(other)}` : `${displayLabel(other)} ${edgeVerb(e.kind)} este item`
    out.push({ edge: e, other, text })
  }
  return out
}

export interface Row {
  node: GraphNode
  relations: Relation[]
}

/** The list view: every visible node with every visible relation of it. The center comes first, then by kind and label. */
export function rows(graph: Graph, ids: ReadonlySet<string>): Row[] {
  const order: GraphNodeKind[] = ['person', 'account', 'partner', 'handle', 'object', 'agent', 'system']
  const list = [...graph.nodes.values()].filter((n) => ids.has(n.id))
  list.sort((a, b) => {
    if (a.id === graph.center) return -1
    if (b.id === graph.center) return 1
    const k = order.indexOf(a.kind) - order.indexOf(b.kind)
    return k !== 0 ? k : displayLabel(a).localeCompare(displayLabel(b), 'pt-BR')
  })
  return list.map((node) => ({ node, relations: relationsOf(graph, node.id, ids) }))
}

/**
 * Where a node opens in the Console. A profile opens its memory; an identifier opens the identifiers tab of
 * the profile it identifies; an object opens the objects tab of the profile that owns it; an agent or a
 * system opens its source. Null when there is no screen: a hand-off queue, or an object whose owner is not
 * on the canvas.
 */
export function linkOf(graph: Graph, n: GraphNode, space: string): string | null {
  switch (n.kind) {
    case 'person':
    case 'account':
    case 'partner':
      return n.ref ? paths.profile(space, n.ref) : null
    case 'agent':
    case 'system':
      return n.ref ? paths.agent(space, n.ref) : null
    case 'handle': {
      const owner = edgesOf(graph, n.id).find((e) => e.kind === 'identifies' && e.from === n.id)
      const profile = owner ? graph.nodes.get(owner.to) : null
      return profile?.ref ? `${paths.profile(space, profile.ref)}?aba=ids` : null
    }
    case 'object': {
      const owner = edgesOf(graph, n.id).find((e) => e.kind === 'owns' && e.to === n.id)
      const profile = owner ? graph.nodes.get(owner.from) : null
      return profile?.ref ? `${paths.profile(space, profile.ref)}?aba=objetos` : null
    }
  }
}
