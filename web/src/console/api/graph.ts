/**
 * The relationship graph of the cell's admin API (the server, `GET /v1/graph`): who a customer is across
 * channels, which company and partner they belong to, what objects they have, and which agents and systems
 * touched them. Labels come masked from the server (a pseudonym, a masked identifier); the Console never
 * receives a raw identifier here. One `graph.read` receipt per call, not per node.
 *
 * Shapes mirror the shared contract (`graph-contract.md`). A node `ref` is the id of the screen the node
 * opens: a profile id, an object id, a source id.
 */
import { call, type Transport } from './http'

export type GraphNodeKind = 'person' | 'account' | 'partner' | 'handle' | 'object' | 'agent' | 'system'

export type GraphEdgeKind =
  | 'identifies'
  | 'member_of'
  | 'partner_of'
  | 'parent_of'
  | 'owns'
  | 'relates_to'
  | 'acted_on'
  | 'talked_with'
  | 'reported'
  | 'handed_off'
  | 'suggested_same'

export type GraphMeta = Record<string, string | number | boolean | string[] | null>

export interface GraphNode {
  id: string
  kind: GraphNodeKind
  label: string
  ref: string | null
  meta: GraphMeta
}

export interface GraphEdge {
  id: string
  from: string
  to: string
  kind: GraphEdgeKind
  meta: GraphMeta
}

export interface GraphOut {
  /** The node id at the center of a customer graph; null in the overview of the space. */
  center: string | null
  nodes: GraphNode[]
  edges: GraphEdge[]
  /** The limit cut nodes: the Console says so. */
  truncated: boolean
  /** Nodes hidden by field access or by the reader's role. */
  withheld: number
  generated_at: string
}

const GRAPH_LIMIT = 150
const OVERVIEW_LIMIT = 100

type Opt = { signal?: AbortSignal }

export function graphClient(t: Transport) {
  return {
    /** The graph around one profile: depth 1 by default, 2 on demand. */
    around: (center: string, depth: 1 | 2, o: Opt = {}) => call<GraphOut>(t, 'GET', '/v1/graph', { ...o, query: { center, depth, limit: GRAPH_LIMIT } }),
    /** The most connected entities of the space, to explore from. */
    overview: (o: Opt = {}) => call<GraphOut>(t, 'GET', '/v1/graph', { ...o, query: { overview: 1, limit: OVERVIEW_LIMIT } }),
  }
}
