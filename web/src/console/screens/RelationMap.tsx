import { useCallback } from 'react'
import { Link } from 'react-router-dom'
import { paths } from '../paths'
import { useSpace } from '../session'
import { PageHeader } from '../ui/bits'
import { useTitle } from '../ui/layout'
import { Empty } from '../ui/States'
import { btnSecondary } from '../ui/styles'
import { GraphExplorer } from './graph/GraphExplorer'

/**
 * The map of the space: the most connected items the memory knows, to explore from. Clicking a customer
 * brings its relations in; Abrir goes to its memory. Every call leaves one admin receipt (graph.read).
 */
export function RelationMap() {
  const { space, cell } = useSpace()
  useTitle('Mapa de relações', space.environment === 'sandbox')
  const load = useCallback((_depth: 1 | 2, signal: AbortSignal) => cell.graph.overview({ signal }), [cell])
  const expand = useCallback((ref: string, signal: AbortSignal) => cell.graph.around(ref, 1, { signal }), [cell])
  return (
    <>
      <PageHeader screen="relations" />
      <GraphExplorer
        mode="overview"
        load={load}
        expand={expand}
        space={space.space_id}
        tour="relations.map"
        empty={
          <Empty
            title="O mapa deste espaço ainda está vazio."
            action={
              <Link to={paths.agents(space.space_id)} className={btnSecondary}>
                Ver agentes e chaves
              </Link>
            }
            example="p_7f3a (Pessoa) identificado por +55 11 9••••-4471 (WhatsApp), faz parte de Loja Centro (Empresa), atendido por SAC voz (Agente)"
          >
            As relações nascem dos eventos: o primeiro identificador de um cliente, um pedido ou chamado que um sistema registrou, uma conversa com um agente, um vínculo com uma empresa feito em
            Identidade. Assim que a memória conhecer um cliente, ele aparece aqui com o que está ligado a ele.
          </Empty>
        }
      />
    </>
  )
}
