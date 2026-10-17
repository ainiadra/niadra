import { useEffect, useState, type ReactNode } from 'react'
import { describeError } from '../api/describe'
import { Retrying } from '../lib/useResource'
import { Icon } from './Icon'
import { btnSecondary, card, danger, mono, small } from './styles'

/** Loading, empty and error: every data view in the Console has all three (antislop R-27). */

export function Loading({ label, className = '' }: { label: string; className?: string }) {
  return (
    <div role="status" aria-live="polite" className={`flex items-center gap-2.5 py-8 text-[0.875rem] text-ink2 ${className}`}>
      <svg width="16" height="16" viewBox="0 0 24 24" aria-hidden="true" className="animate-spin motion-reduce:animate-none">
        <circle cx="12" cy="12" r="9" fill="none" stroke="currentColor" strokeOpacity="0.25" strokeWidth="2.5" />
        <path d="M21 12a9 9 0 0 0-9-9" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" />
      </svg>
      {label}
    </div>
  )
}

/**
 * An honest empty state: what the screen needs to fill, the one action that gets there, and an example of
 * what it will show, marked as such so nobody takes it for data.
 */
export function Empty({ title, children, action, example }: { title: string; children?: ReactNode; action?: ReactNode; example?: ReactNode }) {
  return (
    <div className={`${card} px-5 py-8 sm:px-8`}>
      <p className="text-[0.95rem] font-semibold text-ink">{title}</p>
      {children && <div className={`${small} mt-1.5 max-w-[60ch]`}>{children}</div>}
      {action && <div className="mt-4">{action}</div>}
      {example && (
        <p className="mt-4 flex flex-wrap items-baseline gap-x-2 gap-y-1 text-[0.8125rem] leading-5 text-ink">
          <span className="inline-flex items-center whitespace-nowrap rounded-md border border-line bg-bg px-1.5 py-px text-[0.75rem] font-semibold text-ink2">exemplo</span>
          <span className="min-w-0 max-w-[60ch]">{example}</span>
        </p>
      )}
    </div>
  )
}

/**
 * A read the server asked to try again later: the Console waits and tries again by itself (lib/useResource),
 * and says so, with the seconds left and a way to try now. It is a wait, not a failure.
 */
function Waiting({ retrying, onRetry, compact }: { retrying: Retrying; onRetry?: () => void; compact: boolean }) {
  const left = () => Math.max(0, Math.ceil((retrying.at - Date.now()) / 1000))
  const [seconds, setSeconds] = useState(left)
  useEffect(() => {
    setSeconds(left())
    const timer = window.setInterval(() => setSeconds(left()), 1000)
    return () => window.clearInterval(timer)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [retrying])
  const busy = retrying.cause.status === 429
  return (
    <div role="status" aria-live="polite" className={`rounded-xl border border-line bg-surface ${compact ? 'px-4 py-3' : 'px-5 py-6 sm:px-6'}`}>
      <div className="flex items-start gap-2.5">
        <svg width="16" height="16" viewBox="0 0 24 24" aria-hidden="true" className="mt-0.5 shrink-0 animate-spin text-ink2 motion-reduce:animate-none">
          <circle cx="12" cy="12" r="9" fill="none" stroke="currentColor" strokeOpacity="0.25" strokeWidth="2.5" />
          <path d="M21 12a9 9 0 0 0-9-9" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" />
        </svg>
        <div className="min-w-0">
          <p className="text-[0.9rem] font-semibold text-ink">{busy ? 'Muitas consultas seguidas: esperando a vez.' : 'A região deste espaço pediu um instante.'}</p>
          <p className={`${small} mt-1`}>
            {busy
              ? 'O limite de consultas desta chave foi atingido por alguns segundos.'
              : 'Ela está ocupada ou ainda preparando o espaço. Nenhum agente é afetado, e nada se perde.'}{' '}
            {seconds > 0 ? `O Console tenta de novo em ${seconds} s.` : 'Tentando de novo agora.'}
          </p>
          {onRetry && (
            <button type="button" className={`${btnSecondary} mt-3`} onClick={onRetry}>
              Tentar agora
            </button>
          )}
        </div>
      </div>
    </div>
  )
}

export function ErrorState({ error, onRetry, compact = false }: { error: unknown; onRetry?: () => void; compact?: boolean }) {
  if (error instanceof Retrying) return <Waiting retrying={error} onRetry={onRetry} compact={compact} />
  const e = describeError(error)
  return (
    <div role="alert" className={`rounded-xl border ${danger.border} bg-surface ${compact ? 'px-4 py-3' : 'px-5 py-6 sm:px-6'}`}>
      <div className="flex items-start gap-2.5">
        <Icon name="alert" className={`mt-0.5 ${danger.text}`} />
        <div className="min-w-0">
          <p className="text-[0.9rem] font-semibold text-ink">{e.title}</p>
          <p className={`${small} mt-1`}>{e.detail}</p>
          {e.requestId && (
            <p className={`${small} mt-1`}>
              Código da requisição: <span className={`${mono} break-all text-ink`}>{e.requestId}</span>
            </p>
          )}
          {onRetry && (
            <button type="button" className={`${btnSecondary} mt-3`} onClick={onRetry}>
              Tentar de novo
            </button>
          )}
        </div>
      </div>
    </div>
  )
}

export function LoadMore({ hasMore, loading, error, onClick }: { hasMore: boolean; loading: boolean; error: unknown; onClick: () => void }) {
  if (!hasMore) return null
  return (
    <div className="mt-4 flex flex-col items-start gap-3">
      {Boolean(error) && <ErrorState error={error} compact />}
      <button type="button" className={btnSecondary} onClick={onClick} disabled={loading}>
        {loading ? 'Carregando…' : 'Carregar mais'}
      </button>
    </div>
  )
}

/**
 * Previous and next for a list read one page at a time (lib/useResource `useNumberedPages`), with the number
 * of the page on screen. A button that cannot act stays focusable with aria-disabled, so the focus of a
 * person paging by keyboard is not lost when the last page arrives. A list of one page shows no pager.
 */
export function Pager({
  label,
  page,
  hasPrevious,
  hasNext,
  busy,
  onPrevious,
  onNext,
}: {
  label: string
  page: number
  hasPrevious: boolean
  hasNext: boolean
  busy: boolean
  onPrevious: () => void
  onNext: () => void
}) {
  if (!hasPrevious && !hasNext) return null
  const button = `${btnSecondary} aria-disabled:cursor-not-allowed aria-disabled:opacity-55 aria-disabled:hover:bg-surface`
  return (
    <nav aria-label={label} className="mt-3 flex items-center justify-between gap-3">
      <p className="text-[0.8125rem] leading-5 text-ink2" aria-live="polite">
        {busy ? 'Carregando…' : <>Página <span className="font-semibold tabular-nums text-ink">{page}</span></>}
      </p>
      <div className="flex gap-2">
        <button type="button" className={`${button} pl-3`} aria-disabled={!hasPrevious || busy} onClick={() => hasPrevious && !busy && onPrevious()}>
          <Icon name="back" size={16} className="text-ink2" />
          Anterior
        </button>
        <button type="button" className={`${button} pr-3`} aria-disabled={!hasNext || busy} onClick={() => hasNext && !busy && onNext()}>
          Próxima
          <Icon name="chevron" size={16} className="text-ink2" />
        </button>
      </div>
    </nav>
  )
}
