import { Link } from 'react-router-dom'
import type { GraphNode } from '../../api/graph'
import { linkOf, objectsShown, relationsOf, type Graph } from '../../lib/graph'
import { displayLabel, edgeDashed, metaLines, nodeKind, nodeSummary, objectsLine } from '../../lib/graphLabels'
import { Chip } from '../../ui/bits'
import { Icon } from '../../ui/Icon'
import { ErrorState } from '../../ui/States'
import { btnQuiet, btnSecondary, small } from '../../ui/styles'
import { KindSwatch } from './shapes'

const SHOWN_RELATIONS = 6

/** Whether the server can draw the graph around this node: only a profile is a center (graph-contract.md). */
export const canExpand = (n: GraphNode) => (n.kind === 'person' || n.kind === 'account' || n.kind === 'partner') && Boolean(n.ref)

/**
 * The card of one node: what it is, in words, its values, its relations as sentences, and the two actions:
 * open its screen and bring its relations into the map. It opens on hover, focus or tap, and closes with
 * Escape or its button.
 */
export function NodeCard({
  graph,
  node,
  space,
  visible,
  expanding,
  expandError,
  onExpand,
  onClose,
}: {
  graph: Graph
  node: GraphNode
  space: string
  visible: ReadonlySet<string>
  expanding: boolean
  expandError: unknown
  onExpand: (id: string) => void
  onClose: () => void
}) {
  const relations = relationsOf(graph, node.id, visible)
  const hiddenRelations = relationsOf(graph, node.id).length - relations.length
  const href = linkOf(graph, node, space)
  const expanded = graph.expanded.has(node.id)
  const isCenter = node.id === graph.center
  const summary = nodeSummary(node)
  const objects = objectsLine(node, objectsShown(graph, node.id))
  const lines = [...metaLines(node.meta, node.kind), ...(objects ? [{ label: 'Objetos', value: objects }] : [])]
  const isProfile = node.kind === 'person' || node.kind === 'account' || node.kind === 'partner'
  // The card has room for the whole pseudonym; the canvas shortens it.
  const title = isProfile ? node.label : displayLabel(node)
  return (
    <div role="region" aria-label={`Detalhes de ${title}`} className="flex min-h-0 max-h-full flex-col rounded-xl border border-line bg-surface shadow-[0_12px_32px_-20px_rgba(24,28,18,0.45)]">
      <div className="flex items-start gap-3 px-4 pt-4">
        <KindSwatch kind={node.kind} />
        <div className="min-w-0 flex-1">
          <p className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
            <span className={`${isProfile || node.kind === 'handle' ? 'font-mono text-[0.85rem]' : 'text-[0.95rem]'} break-all font-semibold text-ink`}>{title}</span>
            <Chip>{nodeKind(node.kind)}</Chip>
            {isCenter && <Chip tone="dark">O cliente desta tela</Chip>}
          </p>
          {summary && <p className={`${small} mt-0.5`}>{summary}</p>}
        </div>
        <button type="button" onClick={onClose} className="-mr-2 -mt-2 inline-flex h-11 w-11 shrink-0 items-center justify-center rounded-lg text-ink2 hover:bg-bg hover:text-ink lg:h-9 lg:w-9" aria-label="Fechar o cartão">
          <Icon name="close" size={16} />
        </button>
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto px-4 pb-4">
        {lines.length > 0 && (
          <dl className="mt-3 grid grid-cols-[auto_minmax(0,1fr)] gap-x-3 gap-y-1">
            {lines.map((l) => (
              <div key={l.label} className="contents">
                <dt className="text-[0.75rem] font-semibold text-ink2">{l.label}</dt>
                <dd className="break-words text-[0.8125rem] text-ink">{l.value}</dd>
              </div>
            ))}
          </dl>
        )}
        <h3 className="mt-3 text-[0.75rem] font-semibold text-ink2">{relations.length === 0 ? 'Sem relações no mapa' : relations.length === 1 ? '1 relação' : `${relations.length} relações`}</h3>
        {relations.length > 0 && (
          <ul className="mt-1 grid gap-1">
            {relations.slice(0, SHOWN_RELATIONS).map((r) => (
              <li key={r.edge.id} className="text-[0.8125rem] leading-5 text-ink">
                {r.text}
                {edgeDashed(r.edge.kind) && <span className="text-ink2"> (sugestão pendente)</span>}
              </li>
            ))}
            {relations.length > SHOWN_RELATIONS && <li className={small}>e mais {relations.length - SHOWN_RELATIONS}. A lista mostra todas.</li>}
          </ul>
        )}
        {hiddenRelations > 0 && <p className={`${small} mt-1`}>{hiddenRelations === 1 ? '1 relação está com um tipo escondido pela legenda.' : `${hiddenRelations} relações estão com tipos escondidos pela legenda.`}</p>}
        {expandError !== null && expandError !== undefined && (
          <div className="mt-3">
            <ErrorState error={expandError} compact onRetry={() => onExpand(node.id)} />
          </div>
        )}
        <div className="mt-4 flex flex-wrap gap-2">
          {href ? (
            <Link to={href} className={btnSecondary}>
              Abrir
              <Icon name="chevron" size={14} />
            </Link>
          ) : (
            <p className={small}>Este item não tem tela própria no Console.</p>
          )}
          {canExpand(node) && !isCenter && !expanded && (
            <button type="button" className={btnQuiet} onClick={() => onExpand(node.id)} disabled={expanding}>
              {expanding ? 'Trazendo as relações…' : 'Trazer as relações dele'}
            </button>
          )}
          {canExpand(node) && !isCenter && expanded && <p className={`${small} self-center`}>As relações dele já estão no mapa.</p>}
        </div>
        {!canExpand(node) && (
          <p className={`${small} mt-2`}>
            {node.kind === 'handle'
              ? 'Um identificador aparece pelo cliente que ele identifica.'
              : node.kind === 'object'
                ? 'Um objeto aparece pelos clientes ligados a ele.'
                : 'Um agente ou sistema aparece pelos clientes que ele atendeu ou registrou.'}
          </p>
        )}
        {isProfile && <p className={`${small} mt-2`}>O pseudônimo substitui nome, telefone e e-mail em todas as telas.</p>}
      </div>
    </div>
  )
}
