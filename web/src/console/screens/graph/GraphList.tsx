import { Link } from 'react-router-dom'
import { linkOf, objectsShown, rows, type Graph } from '../../lib/graph'
import { displayLabel, edgeDashed, nodeKind, nodeSummary, objectsLine } from '../../lib/graphLabels'
import { Chip } from '../../ui/bits'
import { link, small, th } from '../../ui/styles'
import { KindSwatch } from './shapes'

/** A cell: a block of the stacked row on a phone, a table cell with the Console's padding from 640 px. */
const cell = 'block p-0 sm:table-cell sm:border-b sm:border-line2 sm:px-3 sm:py-2.5 sm:align-top'

/**
 * The same graph as a table: one row per visible node, every visible relation of it as a sentence, and the
 * link to its screen. On a phone each row stacks as a block; from 640 px it is a table. Nothing here that is
 * not also on the canvas, and nothing on the canvas that is not here.
 */
export function GraphList({ graph, visible, space }: { graph: Graph; visible: ReadonlySet<string>; space: string }) {
  const list = rows(graph, visible)
  return (
    <div className="rounded-xl border border-line bg-surface">
      <table className="w-full border-collapse">
        <caption className="sr-only">Itens do mapa e as relações de cada um</caption>
        <thead className="hidden sm:table-header-group">
          <tr>
            <th scope="col" className={th}>
              Item
            </th>
            <th scope="col" className={th}>
              Tipo
            </th>
            <th scope="col" className={th}>
              Relações
            </th>
            <th scope="col" className={`${th} text-right`}>
              Tela
            </th>
          </tr>
        </thead>
        <tbody>
          {list.map(({ node, relations }) => {
            const href = linkOf(graph, node, space)
            const objects = objectsLine(node, objectsShown(graph, node.id))
            const summary = nodeSummary(node) ?? (objects ? `Objetos: ${objects}` : null)
            const isCenter = node.id === graph.center
            const isProfile = node.kind === 'person' || node.kind === 'account' || node.kind === 'partner'
            // The table has room for the whole pseudonym, as Clientes shows it; the canvas shortens it.
            const title = isProfile ? node.label : displayLabel(node)
            return (
              <tr key={node.id} className="block border-b border-line2 px-4 py-3 last:border-b-0 sm:table-row sm:border-0 sm:p-0" data-node={node.id}>
                <td className={cell}>
                  <span className="flex items-start gap-2.5">
                    <KindSwatch kind={node.kind} />
                    <span className="min-w-0">
                      <span className={`${isProfile || node.kind === 'handle' ? 'font-mono text-[0.8125rem]' : 'text-[0.875rem]'} block break-all font-semibold text-ink`}>{title}</span>
                      {summary && <span className={`${small} block`}>{summary}</span>}
                      {isCenter && (
                        <span className="mt-1 block">
                          <Chip tone="dark">O cliente desta tela</Chip>
                        </span>
                      )}
                    </span>
                  </span>
                </td>
                <td className={`${cell} pt-1.5 whitespace-nowrap text-[0.8125rem] text-ink2`}>
                  <span className="sm:hidden">Tipo: </span>
                  {nodeKind(node.kind)}
                </td>
                <td className={`${cell} pt-1.5`}>
                  {relations.length === 0 ? (
                    <span className={small}>Sem relações no mapa</span>
                  ) : (
                    <ul className="grid gap-0.5">
                      {relations.map((r) => (
                        <li key={r.edge.id} className="text-[0.8125rem] leading-5 text-ink">
                          {r.text}
                          {edgeDashed(r.edge.kind) && <span className="text-ink2"> (sugestão pendente)</span>}
                        </li>
                      ))}
                    </ul>
                  )}
                </td>
                <td className={`${cell} pt-1 sm:text-right`}>
                  {href ? (
                    <Link to={href} className={`${link} inline-flex min-h-11 items-center text-[0.8125rem] lg:min-h-8`}>
                      Abrir
                    </Link>
                  ) : (
                    <span className={`${small} whitespace-nowrap`}>sem tela própria</span>
                  )}
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}
