import { createContext, useContext, type ReactNode } from 'react'
import { Link } from 'react-router-dom'
import type { OperationOut, Origin, SourceOut } from '../api/types'
import { fmtDateTime } from '../lib/format'
import { channel, eventKind, operationStatus } from '../lib/labels'
import { SCREENS, type ScreenId } from '../lib/screens'
import { useConsole } from '../session'
import { Icon } from './Icon'
import { danger, h1, mono, small } from './styles'

/* Sources of the space, loaded once by the space layout, so any id on screen reads as a name. */

export interface SourcesDirectory {
  status: 'loading' | 'success' | 'error'
  list: SourceOut[]
  byId: Map<string, SourceOut>
  error: unknown
  reload: () => void
}

export const SourcesCtx = createContext<SourcesDirectory>({ status: 'loading', list: [], byId: new Map(), error: null, reload: () => {} })
export const useSources = () => useContext(SourcesCtx)

/**
 * A person by the e-mail the API sends with the id (`person_label` on a receipt, `author_label` and
 * `decided_by_label` on a diff), whatever the reader's role; the signed-in person reads as "você". The id
 * shows only for a receipt older than the names, so nothing is hidden.
 */
export function PersonName({ id, label }: { id: string | null | undefined; label: string | null | undefined }) {
  const { me } = useConsole()
  if (!id) return <span className="text-ink2">sem pessoa</span>
  if (me && id === me.user_id)
    return (
      <span>
        {me.email} <span className="text-ink2">(você)</span>
      </span>
    )
  if (label) return <span>{label}</span>
  return <span className={mono}>pessoa {id}</span>
}

export function SourceName({ id, withVendor = false }: { id: string | null | undefined; withVendor?: boolean }) {
  const { byId } = useSources()
  if (!id) return <span className="text-ink2">sem fonte</span>
  const s = byId.get(id)
  if (!s) return <span className={mono}>{id}</span>
  return (
    <span>
      {s.name}
      {withVendor && s.vendor && <span className="text-ink2"> · {s.vendor}</span>}
    </span>
  )
}

/**
 * The origin stamp: every memory item says which agent or system produced it,
 * on which channel, as what kind of event, and when. It is the Console's
 * recurring signature, the same role the source chip plays on the site.
 */
export function OriginLine({ origin, className = '' }: { origin: Origin | null | undefined; className?: string }) {
  if (!origin) return <p className={`${mono} text-ink2 ${className}`}>origem não informada</p>
  return (
    <p className={`${mono} text-ink2 ${className}`}>
      <SourceName id={origin.source_id} /> · {channel(origin.channel)} · {eventKind(origin.kind)} · {fmtDateTime(origin.at)}
    </p>
  )
}

type Tone = 'neutral' | 'ok' | 'danger' | 'dark'
const TONES: Record<Tone, string> = {
  neutral: 'border-line bg-surface text-ink2',
  ok: 'border-accent/40 bg-accent-wash text-accent-ink',
  danger: `${danger.border} bg-[#fdf1ef] ${danger.text}`,
  dark: 'border-ink bg-ink text-bg',
}

/** A status label. Text always carries the meaning; color only repeats it. */
export function Chip({ tone = 'neutral', children }: { tone?: Tone; children: ReactNode }) {
  return <span className={`inline-flex items-center whitespace-nowrap rounded-md border px-1.5 py-px text-[0.75rem] font-semibold ${TONES[tone]}`}>{children}</span>
}

/**
 * The head of a screen. With `screen`, the title and the line under it come from the screen registry
 * (lib/screens.ts); a screen passes its own title or description only when the page is about one item (a
 * customer, a receipt, a source).
 */
export function PageHeader({
  screen,
  title,
  description,
  actions,
  back,
}: {
  screen?: ScreenId
  title?: ReactNode
  description?: ReactNode
  actions?: ReactNode
  back?: { to: string; label: string }
}) {
  const info = screen ? SCREENS[screen] : null
  return (
    <header className="mb-6 flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between">
      <div className="min-w-0">
        {back && (
          <Link to={back.to} className="mb-2 -ml-1 inline-flex min-h-11 items-center gap-1 rounded-md px-1 text-[0.8125rem] font-semibold text-ink2 hover:text-ink lg:min-h-8">
            <Icon name="back" size={16} />
            {back.label}
          </Link>
        )}
        <h1 className={h1}>{title ?? info?.label}</h1>
        {(description ?? info?.lead) && <div className={`${small} mt-1.5 max-w-[76ch]`}>{description ?? info?.lead}</div>}
      </div>
      {actions && <div className="flex flex-wrap gap-2">{actions}</div>}
    </header>
  )
}

/** Label and value pairs, for properties of a receipt, a source or a key. */
export function Props({ items }: { items: [string, ReactNode][] }) {
  return (
    <dl className="grid grid-cols-1 gap-x-6 gap-y-3 sm:grid-cols-2 xl:grid-cols-3">
      {items.map(([k, v]) => (
        <div key={k} className="min-w-0">
          <dt className="text-[0.75rem] font-semibold text-ink2">{k}</dt>
          <dd className="mt-0.5 break-words text-[0.875rem] text-ink">{v}</dd>
        </div>
      ))}
    </dl>
  )
}

/** A configuration document or a payload, as the server sent it. */
export function JsonView({ value, label, maxHeight = 'max-h-[28rem]' }: { value: unknown; label?: string; maxHeight?: string }) {
  return (
    <pre aria-label={label} tabIndex={0} className={`invert-block ${maxHeight} overflow-auto whitespace-pre-wrap break-words rounded-xl p-4 font-mono text-[0.75rem] leading-[1.2rem]`}>
      {value === undefined || value === null ? 'vazio' : JSON.stringify(value, null, 2)}
    </pre>
  )
}

/** What a queued identity operation answered: it applies asynchronously, so the op id is the receipt. */
export function OperationNotice({ op }: { op: OperationOut }) {
  return (
    <p role="status" className="rounded-lg border border-accent/40 bg-accent-wash px-3 py-2.5 text-[0.875rem] text-ink">
      <strong>{operationStatus(op.status)}.</strong> Operação <span className={mono}>{op.op_id}</span>
      {op.assertion_id && (
        <>
          {' '}
          · asserção <span className={mono}>{op.assertion_id}</span>
        </>
      )}
      {op.link_id && (
        <>
          {' '}
          · vínculo <span className={mono}>{op.link_id}</span>
        </>
      )}
    </p>
  )
}
