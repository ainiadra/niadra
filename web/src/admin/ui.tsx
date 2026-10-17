/** Loading, empty and error: every data view of the panel has all three (antislop R-27). */
import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react'
import { Icon } from '../console/ui/Icon'
import { btnSecondary, card, danger, small } from '../console/ui/styles'
import { AdminError, describe } from './api'

export function Loading({ label }: { label: string }) {
  return (
    <div role="status" aria-live="polite" className="flex items-center gap-2.5 py-8 text-[0.875rem] text-ink2">
      <svg width="16" height="16" viewBox="0 0 24 24" aria-hidden="true" className="animate-spin motion-reduce:animate-none">
        <circle cx="12" cy="12" r="9" fill="none" stroke="currentColor" strokeOpacity="0.25" strokeWidth="2.5" />
        <path d="M21 12a9 9 0 0 0-9-9" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" />
      </svg>
      {label}
    </div>
  )
}

export function Empty({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div className={`${card} px-5 py-8 sm:px-8`}>
      <p className="text-[0.95rem] font-semibold text-ink">{title}</p>
      {children && <div className={`${small} mt-1.5 max-w-[62ch]`}>{children}</div>}
    </div>
  )
}

export function ErrorBox({ error, onRetry }: { error: unknown; onRetry: () => void }) {
  const e = describe(error)
  return (
    <div role="alert" className={`rounded-xl border ${danger.border} bg-surface px-5 py-6 sm:px-6`}>
      <div className="flex items-start gap-2.5">
        <Icon name="alert" className={`mt-0.5 ${danger.text}`} />
        <div className="min-w-0">
          <p className="text-[0.9rem] font-semibold text-ink">{e.title}</p>
          <p className={`${small} mt-1`}>{e.detail}</p>
          <button type="button" className={`${btnSecondary} mt-3`} onClick={onRetry}>
            Tentar de novo
          </button>
        </div>
      </div>
    </div>
  )
}

export type Load<T> = { status: 'loading' } | { status: 'error'; error: unknown } | { status: 'ok'; data: T }

/**
 * Loads `fn` when `key` changes and on `reload`. A 401 means the session ended: it calls `onExpired`
 * instead of showing an error. The previous data stays on screen while a new range loads.
 */
export function useLoad<T>(fn: () => Promise<T>, key: string, onExpired: () => void) {
  const [state, setState] = useState<Load<T>>({ status: 'loading' })
  const [busy, setBusy] = useState(true)
  const seq = useRef(0)
  const fnRef = useRef(fn)
  fnRef.current = fn
  const expiredRef = useRef(onExpired)
  expiredRef.current = onExpired

  const run = useCallback(() => {
    const n = ++seq.current
    setBusy(true)
    fnRef.current().then(
      (data) => {
        if (n !== seq.current) return
        setState({ status: 'ok', data })
        setBusy(false)
      },
      (error: unknown) => {
        if (n !== seq.current) return
        setBusy(false)
        if (error instanceof AdminError && error.status === 401) return expiredRef.current()
        setState({ status: 'error', error })
      },
    )
  }, [])

  useEffect(() => {
    run()
  }, [run, key])

  return { state, busy, reload: run }
}
