import { useCallback } from 'react'
import { useSpace } from '../../session'
import { Empty } from '../../ui/States'
import { GraphExplorer } from '../graph/GraphExplorer'

/**
 * The relations of one customer, drawn: identifiers, company and partner, objects, and the agents and
 * systems that touched them, one level around the customer, two on demand. The same read rules as the
 * memory of the profile, one admin receipt per call.
 */
export function RelationsTab({ profileId, pseudonym }: { profileId: string; pseudonym: string }) {
  const { space, cell } = useSpace()
  const load = useCallback((depth: 1 | 2, signal: AbortSignal) => cell.graph.around(profileId, depth, { signal }), [cell, profileId])
  const expand = useCallback((ref: string, signal: AbortSignal) => cell.graph.around(ref, 1, { signal }), [cell])
  return (
    <GraphExplorer
      mode="customer"
      load={load}
      expand={expand}
      space={space.space_id}
      tour="profile.relations"
      empty={
        <Empty title="Este cliente ainda não tem relações registradas." example={`${pseudonym} identificado por +55 11 9••••-4471 (WhatsApp), é dono de Pedido PED-90039, atendido por SAC voz`}>
          Uma relação nasce de um evento: um identificador novo deste cliente, um pedido ou chamado que um sistema registrou no nome dele, uma conversa com um agente, um vínculo com uma empresa feito
          em Identidade. Enquanto só existir o pseudônimo, o mapa fica vazio.
        </Empty>
      }
    />
  )
}
