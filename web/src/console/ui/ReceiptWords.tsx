import type { ReactNode } from 'react'
import type { ReceiptOut } from '../api/types'
import { actionWords, isControlPlane } from '../lib/actions'
import { role } from '../lib/labels'
import { PersonName, SourceName } from './bits'
import { mono } from './styles'

/**
 * Who a receipt is from: the agent that read (by name), the person who acted here (by e-mail), or the
 * control plane, for a change no person of this cell made (a key issued or revoked). Never "não informado"
 * when the receipt says who.
 */
export function ReceiptActor({ receipt: r }: { receipt: ReceiptOut }) {
  if (r.source_id) return <SourceName id={r.source_id} withVendor />
  if (r.person_id) return <PersonName id={r.person_id} label={r.person_label} />
  if (isControlPlane(r.detail)) return <span>Plano de controle</span>
  return <span className="text-ink2">sem autor registrado</span>
}

/** The action of an `admin` receipt in words, with what it was on under it. */
export function ActionLine({ receipt: r, compact = false }: { receipt: ReceiptOut; compact?: boolean }) {
  const w = actionWords(r.detail)
  const did: ReactNode = w.did ?? (w.code ? <span className={mono}>{w.code}</span> : <span className="text-ink2">ação sem código</span>)
  return (
    <span className="block">
      <span className="text-ink">{did}</span>
      {w.on && (
        <span className={`${mono} block text-ink2`}>
          {w.on.kind === 'source' ? (
            <>
              agente <SourceName id={w.on.id} />
            </>
          ) : compact ? (
            `${w.on.label} ${w.on.id}`
          ) : (
            <>
              {w.on.label} <span className="break-all">{w.on.id}</span>
            </>
          )}
          {w.on.sourceId && (
            <>
              {' '}
              do agente <SourceName id={w.on.sourceId} />
            </>
          )}
        </span>
      )}
    </span>
  )
}

/** The roles the person held at the time, in words, or the key an agent used. */
export function ActionCredentials({ receipt: r }: { receipt: ReceiptOut }) {
  const roles = r.detail?.roles
  const key = r.detail?.key_id
  if (roles) return <span>{roles.split(',').filter(Boolean).map(role).join(', ')}</span>
  if (key)
    return (
      <span>
        chave <span className={mono}>{key}</span>
      </span>
    )
  return <span className="text-ink2">não registrado</span>
}
