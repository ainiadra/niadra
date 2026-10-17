/**
 * The words of the relationship graph: each kind of node and of relation as a tenant reads it, the label the
 * canvas shows for a node, and each value the server sends in a node's or an edge's `meta`, with a label. No
 * contract value reaches the screen as written.
 */
import type { GraphEdge, GraphEdgeKind, GraphMeta, GraphNode, GraphNodeKind } from '../api/graph'
import { fmtDateTime, fmtInt } from './format'
import { channel, handleType, handleWeight, linkRole, objectType, operation, stateValue } from './labels'
import type { HandleType } from '../api/types'

export const NODE_KINDS: readonly GraphNodeKind[] = ['person', 'account', 'partner', 'handle', 'object', 'agent', 'system']

const NODE_KIND: Record<GraphNodeKind, { one: string; many: string }> = {
  person: { one: 'Pessoa', many: 'Pessoas' },
  account: { one: 'Empresa', many: 'Empresas' },
  partner: { one: 'Parceiro', many: 'Parceiros' },
  handle: { one: 'Identificador', many: 'Identificadores' },
  object: { one: 'Objeto', many: 'Objetos' },
  agent: { one: 'Agente', many: 'Agentes' },
  system: { one: 'Sistema', many: 'Sistemas' },
}
export const nodeKind = (k: GraphNodeKind) => NODE_KIND[k].one
export const nodeKindPlural = (k: GraphNodeKind) => NODE_KIND[k].many

/** What each kind is, for the legend and the help: one line a business reader understands. */
export const NODE_KIND_MEANS: Record<GraphNodeKind, string> = {
  person: 'Um cliente que é uma pessoa, pelo pseudônimo.',
  account: 'Uma organização cliente, com as pessoas ligadas a ela.',
  partner: 'Uma organização que participa do atendimento sem ser cliente.',
  handle: 'Um telefone, e-mail, WhatsApp ou ID de sistema, mascarado.',
  object: 'Um pedido, fatura, chamado ou outro registro de um sistema.',
  agent: 'Um agente de IA ou pessoa que falou com o cliente ou agiu por ele.',
  system: 'Um sistema da empresa (CRM, ERP) que mandou eventos.',
}

/** A hand-off target that is not a source: the server marks it with `meta.target` ("queue:fechamento"). */
const isQueue = (n: GraphNode) => n.kind === 'agent' && !n.ref && typeof n.meta.target === 'string'

/**
 * The pseudonym as the canvas has room for: `p_` and its first six characters. The card and the list say the
 * whole code, as Clientes does.
 */
export const shortPseudonym = (p: string) => (p.length > 10 && p.startsWith('p_') ? p.slice(0, 8) : p)

/**
 * What the canvas writes under a node. A profile shows the short pseudonym; an identifier its masked value;
 * an object its type in words and its number ("Pedido PED-90039"); a queue "Fila: <name>"; an agent or a
 * system its name.
 */
export function displayLabel(n: GraphNode): string {
  switch (n.kind) {
    case 'person':
    case 'account':
    case 'partner':
      return shortPseudonym(n.label)
    case 'object': {
      const type = typeof n.meta.type === 'string' ? n.meta.type : null
      const ext = typeof n.meta.external_id === 'string' ? n.meta.external_id : null
      if (type && ext) return `${objectType(type)} ${ext}`
      return n.label
    }
    case 'agent':
      return isQueue(n) ? `Fila: ${n.label}` : n.label
    default:
      return n.label
  }
}

/** The verb of each relation, read from the `from` node to the `to` node. */
const EDGE_VERB: Record<GraphEdgeKind, string> = {
  identifies: 'identifica',
  member_of: 'faz parte de',
  partner_of: 'é parceiro de',
  parent_of: 'é matriz de',
  owns: 'é dono de',
  relates_to: 'está ligado a',
  acted_on: 'agiu sobre',
  talked_with: 'atendeu',
  reported: 'registrou',
  handed_off: 'transferiu para',
  suggested_same: 'pode ser a mesma pessoa que',
}
export const edgeVerb = (k: GraphEdgeKind) => EDGE_VERB[k]

/** The word on a line of the drawing: the verb, with the count when the relation repeats ("atendeu 4x"). */
export function edgeShortLabel(e: GraphEdge): string {
  if (e.kind === 'suggested_same') return 'pode ser a mesma pessoa'
  if (e.kind === 'owns') {
    // What the person said about the object, counted on the line.
    const said = [
      typeof e.meta.observations === 'number' && e.meta.observations > 0 && `${fmtInt(e.meta.observations)} ${e.meta.observations === 1 ? 'dito' : 'ditos'}`,
      typeof e.meta.instructions === 'number' && e.meta.instructions > 0 && `${fmtInt(e.meta.instructions)} ${e.meta.instructions === 1 ? 'instrução' : 'instruções'}`,
    ].filter(Boolean)
    if (said.length > 0) return `${edgeVerb(e.kind)} · ${said.join(', ')}`
  }
  const n = e.meta.count
  return typeof n === 'number' && n > 1 ? `${edgeVerb(e.kind)} ${fmtInt(n)}x` : edgeVerb(e.kind)
}

/** A relation drawn dashed: a suggestion nobody confirmed yet. */
export const edgeDashed = (k: GraphEdgeKind) => k === 'suggested_same'

/** A full sentence for one relation: "+55*******0001 identifica p_eyxl5q". */
export const edgeSentence = (e: GraphEdge, nodes: ReadonlyMap<string, GraphNode>) => {
  const a = nodes.get(e.from)
  const b = nodes.get(e.to)
  return `${a ? displayLabel(a) : 'item'} ${edgeVerb(e.kind)} ${b ? displayLabel(b) : 'item'}`
}

const STRENGTH: Record<string, string> = { strong: 'Forte', weak: 'Fraca' }
const OWN_ROLE: Record<string, string> = { owner: 'Dono', participant: 'Participante' }
const OUTCOME: Record<string, string> = {
  accepted: 'Aceita',
  completed: 'Concluída',
  resolved: 'Resolvida',
  pending: 'Pendente',
  rejected: 'Recusada',
  failed: 'Falhou',
  abandoned: 'Abandonada',
  unknown: 'Sem desfecho registrado',
}
const HANDOFF_STATUS: Record<string, string> = { open: 'Em aberto', accepted: 'Aceita', closed: 'Encerrada', pending: 'Pendente', rejected: 'Recusada' }

export interface MetaLine {
  label: string
  value: string
}

const asText = (v: GraphMeta[string]) => (Array.isArray(v) ? v.join(', ') : v === null ? 'não informado' : String(v))
/** A value the tenant wrote with underscores ("venda_fechada") reads with spaces. */
const words = (v: GraphMeta[string]) => asText(v).replace(/_/g, ' ')
const isIso = (v: unknown): v is string => typeof v === 'string' && /^\d{4}-\d{2}-\d{2}T/.test(v)
const when = (v: GraphMeta[string]) => (isIso(v) ? fmtDateTime(v) : asText(v))
const count = (v: GraphMeta[string]) => (typeof v === 'number' ? fmtInt(v) : asText(v))

/**
 * The `meta` of a node or an edge as lines a person reads. Known keys get their word and their value in words;
 * a key the Console does not know is still shown, named as a value of the system, never dropped. Ids and
 * ranks the server uses for itself (hop, about, score of a profile) are not for reading.
 */
export function metaLines(meta: GraphMeta | null | undefined, kind?: GraphNodeKind): MetaLine[] {
  if (!meta) return []
  const out: MetaLine[] = []
  for (const [key, raw] of Object.entries(meta)) {
    if (raw === undefined || raw === '' || raw === null) continue
    switch (key) {
      case 'pseudonym':
        // The label of a profile is its pseudonym already; the card and the list say it whole.
        break
      case 'handles':
        out.push({ label: 'Identificadores', value: count(raw) })
        break
      case 'links':
        out.push({ label: 'Vínculos', value: count(raw) })
        break
      case 'objects':
        out.push({ label: 'Objetos', value: count(raw) })
        break
      case 'objects_total':
        // Read with the objects on the canvas, by objectsLine, never as a bare number.
        break
      case 'interactions':
        out.push({ label: 'Conversas e ações', value: count(raw) })
        break
      case 'strength':
        out.push({ label: 'Força da identificação', value: STRENGTH[String(raw)] ?? asText(raw) })
        break
      case 'weight':
        out.push({ label: 'Peso do identificador', value: handleWeight(String(raw)) })
        break
      case 'type':
        if (kind === 'handle') out.push({ label: 'Tipo', value: handleType(String(raw) as HandleType) })
        else if (kind === 'object') {
          const t = objectType(String(raw))
          out.push(t === raw ? { label: 'Tipo (valor técnico)', value: asText(raw) } : { label: 'Tipo', value: t })
        } else out.push({ label: 'Tipo', value: asText(raw) })
        break
      case 'channel':
        out.push({ label: 'Canal', value: channel(String(raw)) })
        break
      case 'channels':
        out.push({ label: Array.isArray(raw) && raw.length === 1 ? 'Canal' : 'Canais', value: (Array.isArray(raw) ? raw : [String(raw)]).map(channel).join(', ') })
        break
      case 'provisional':
        out.push({ label: 'Provisório', value: raw === true ? 'Sim, ainda sem confirmação' : 'Não' })
        break
      case 'last_seen':
        out.push({ label: 'Visto pela última vez', value: when(raw) })
        break
      case 'since':
        out.push({ label: 'Desde', value: when(raw) })
        break
      case 'as_of':
        out.push({ label: 'Atualizado em', value: when(raw) })
        break
      case 'last_at':
        out.push({ label: 'Última vez', value: when(raw) })
        break
      case 'count':
        out.push({ label: 'Vezes', value: count(raw) })
        break
      case 'observations':
        // On a person's edge to an object: what they said about it in conversations.
        out.push({ label: 'Dito nas conversas', value: count(raw) })
        break
      case 'instructions':
        out.push({ label: 'Instruções', value: count(raw) })
        break
      case 'role':
        if (kind === undefined) {
          // On an edge: the role of a person in a company, or of a profile over an object.
          const own = OWN_ROLE[String(raw)]
          out.push({ label: 'Papel', value: own ?? linkRole(String(raw)).label })
        } else out.push({ label: 'Papel', value: words(raw) })
        break
      case 'relation':
        out.push({ label: 'Relação', value: words(raw) })
        break
      case 'operations':
        out.push({ label: Array.isArray(raw) && raw.length === 1 ? 'Operação' : 'Operações', value: (Array.isArray(raw) ? raw : [String(raw)]).map((op) => operation(op).label).join(', ') })
        break
      case 'status':
        out.push({ label: 'Situação', value: HANDOFF_STATUS[String(raw)] ?? words(raw) })
        break
      case 'outcome':
        out.push({ label: 'Desfecho', value: OUTCOME[String(raw)] ?? words(raw) })
        break
      case 'score':
        if (kind === undefined) out.push({ label: 'Semelhança', value: typeof raw === 'number' ? `${Math.round(raw * 100)}%` : asText(raw) })
        break
      case 'state':
        if (meta.state_label === undefined || meta.state_label === null) {
          const s = stateValue(String(raw))
          out.push(s.raw ? { label: 'Estado', value: s.label } : { label: 'Estado (valor técnico)', value: s.label })
        }
        break
      case 'state_label':
        out.push({ label: 'Estado', value: asText(raw) })
        break
      case 'external_id':
        out.push({ label: 'Número no sistema', value: asText(raw) })
        break
      case 'namespace':
        out.push({ label: 'Sistema de origem', value: channel(String(raw)) })
        break
      case 'masked':
        if (raw === true) out.push({ label: 'Conteúdo', value: 'Mascarado para o seu papel' })
        break
      case 'vendor':
        out.push({ label: 'Fornecedor', value: asText(raw) })
        break
      case 'audience':
        out.push({ label: 'Audiência', value: String(raw) === 'customer_agent' ? 'Fala com o cliente' : String(raw) === 'internal_agent' ? 'Trabalha por dentro' : words(raw) })
        break
      case 'target':
        out.push({ label: 'Destino da transferência', value: String(raw).startsWith('queue:') ? `Fila ${String(raw).slice(6)}` : words(raw) })
        break
      case 'display_name':
        out.push({ label: 'Nome', value: asText(raw) })
        break
      case 'hop':
      case 'about':
      case 'type_label':
      case 'profile_id':
      case 'object_id':
      case 'source_id':
        break
      default:
        out.push({ label: `Valor do sistema (${key.replace(/_/g, ' ')})`, value: words(raw) })
    }
  }
  return out
}

/**
 * How many of a customer's objects the map shows, against how many there are: the server sends the latest
 * 25 at most and says the whole count in `meta.objects_total`. Nothing when the server did not say.
 */
export function objectsLine(n: GraphNode, shown: number): string | null {
  const total = n.meta.objects_total
  if (typeof total !== 'number' || total < 0) return null
  if (total === 0) return 'nenhum'
  if (shown >= total) return total === 1 ? '1, no mapa' : `${fmtInt(total)}, todos no mapa`
  return `mostrando ${fmtInt(shown)} de ${fmtInt(total)}, os mais recentes`
}

/** The one-line summary of a node under its label: the state of an object, the channel of a handle, a queue. */
export function nodeSummary(n: GraphNode): string | null {
  if (n.kind === 'object') {
    if (n.meta.masked === true) return 'Estado mascarado para o seu papel'
    if (typeof n.meta.state_label === 'string') return `Estado: ${n.meta.state_label}`
    if (typeof n.meta.state === 'string') return `Estado: ${stateValue(n.meta.state).label}`
    return null
  }
  if (n.kind === 'handle') {
    const parts = [n.meta.channel ? channel(String(n.meta.channel)) : null, n.meta.provisional === true ? 'provisório' : null].filter(Boolean)
    return parts.length > 0 ? parts.join(', ') : null
  }
  if (isQueue(n)) return 'Fila de transferência, não é uma fonte'
  if ((n.kind === 'agent' || n.kind === 'system') && n.meta.channel) return channel(String(n.meta.channel))
  return null
}
