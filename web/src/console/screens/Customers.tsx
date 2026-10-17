import { useId, useRef, useState, type FormEvent } from 'react'
import { Select } from '../../components/Select'
import { Link, useSearchParams } from 'react-router-dom'
import type { HandleType, ProfileListed, ProfileMatch, ProfileOrder, SubjectKind } from '../api/types'
import { fmtDateTime } from '../lib/format'
import { recognize, unrecognizedHint } from '../lib/identifiers'
import { HANDLE_TYPES, handleType, SUBJECT_KINDS, subjectKind } from '../lib/labels'
import { useNumberedPages } from '../lib/useResource'
import { paths } from '../paths'
import { useSpace } from '../session'
import { PageHeader } from '../ui/bits'
import { Select as LabelledSelect } from '../ui/forms'
import { Icon } from '../ui/Icon'
import { useTitle } from '../ui/layout'
import { Empty, ErrorState, Loading, Pager } from '../ui/States'
import { btnPrimary, btnSecondary, card, h2, hint, input, label, mono, select, small } from '../ui/styles'

/**
 * Finding one customer's memory to inspect it. This is governance, not service: there is no queue here, and
 * nothing on these screens answers a customer. Two ways in: the exact identifier the person already knows, and
 * the list of every customer of the space, by pseudonym, with the identifiers masked.
 */
export function Customers() {
  const { space } = useSpace()
  useTitle('Clientes', space.environment === 'sandbox')
  return (
    <>
      <PageHeader screen="customers" />
      <Search />
      <CustomerList />
    </>
  )
}

/** The opaque identifier types a person may type, which the cell cannot tell apart by format. */
const TYPED: HandleType[] = ['app_user_id', 'system_id', 'anon_id', 'wa_bsuid']

function Search() {
  const { space, cell } = useSpace()
  const [query, setQuery] = useState('')
  const [type, setType] = useState<'' | HandleType>('')
  const [scope, setScope] = useState('')
  const [state, setState] = useState<{ status: 'idle' | 'unrecognized' | 'loading' | 'success' | 'error'; data?: { items: ProfileMatch[] }; error?: unknown }>({ status: 'idle' })
  const qid = useId()
  const tid = useId()
  const sid = useId()
  const noteId = useId()

  async function search(e?: FormEvent) {
    e?.preventDefault()
    const q = query.trim()
    if (!q) return
    // A name or a loose word never leaves the browser: the memory finds only an exact identifier.
    if (!type && !recognize(q)) {
      setState({ status: 'unrecognized' })
      return
    }
    setState({ status: 'loading' })
    try {
      setState({ status: 'success', data: await cell.searchProfiles({ query: q, ...(type ? { type } : {}), ...(type === 'system_id' && scope.trim() ? { scope: scope.trim() } : {}) }) })
    } catch (error) {
      setState({ status: 'error', error })
    }
  }

  return (
    <section aria-labelledby={`${qid}-h`} data-tour="customers.search" className={`${card} p-4 sm:p-5`}>
      <h2 id={`${qid}-h`} className={h2}>
        Buscar um cliente por um dado exato
      </h2>
      <p className={`${small} mt-0.5 max-w-[76ch]`}>
        Telefone com o código do país, e-mail, identificador do WhatsApp ou o pseudônimo (o código <span className={`${mono} text-ink`}>p_…</span> que aparece na lista abaixo, nos comprovantes e nos avisos). A Niadra
        guarda os identificadores como hash e só encontra o que você já sabe: nome e texto livre não encontram ninguém.
      </p>
      <form onSubmit={search} className="mt-4" aria-label="Buscar cliente">
        <div className="flex flex-col gap-3 lg:flex-row lg:items-end">
          <div className="flex-1">
            <label htmlFor={qid} className={label}>
              Telefone, e-mail, WhatsApp ou pseudônimo
            </label>
            <input
              id={qid}
              className={`${input} font-mono`}
              placeholder="+55 11 90000 0000, nome@empresa.com, p_…"
              value={query}
              onChange={(e) => {
                setQuery(e.target.value)
                if (state.status === 'unrecognized') setState({ status: 'idle' })
              }}
              autoComplete="off"
              spellCheck={false}
              aria-describedby={state.status === 'unrecognized' ? noteId : undefined}
              aria-invalid={state.status === 'unrecognized' ? true : undefined}
            />
          </div>
          <div className="lg:w-64">
            <label htmlFor={tid} className={label}>
              Tipo do dado
            </label>
            <Select
              id={tid}
              triggerClassName={select}
              value={type}
              onChange={(t) => {
                setType(t)
                if (state.status === 'unrecognized') setState({ status: 'idle' })
              }}
              options={[{ value: '' as const, label: 'Reconhecer pelo formato' }, ...TYPED.map((t) => ({ value: t, label: HANDLE_TYPES[t] }))]}
            />
          </div>
          {type === 'system_id' && (
            <div className="lg:w-44">
              <label htmlFor={sid} className={label}>
                Sistema
              </label>
              <input id={sid} className={input} value={scope} onChange={(e) => setScope(e.target.value)} placeholder="crm" autoComplete="off" />
            </div>
          )}
          <button type="submit" className={btnPrimary} disabled={!query.trim() || state.status === 'loading'}>
            <Icon name="search" size={16} />
            Buscar
          </button>
        </div>
        <p className={hint}>Um ID do seu app ou de um sistema (CRM, ERP) não tem formato reconhecível: escolha o tipo. Sandbox e produção têm memórias separadas, e o mesmo telefone pode existir só numa delas.</p>
      </form>

      <div className="mt-4" aria-live="polite" aria-label="Resultado da busca">
        {state.status === 'unrecognized' && (
          <p id={noteId} role="status" className="rounded-lg border border-line bg-bg px-3 py-2.5 text-[0.875rem] text-ink">
            {unrecognizedHint(query)}
          </p>
        )}
        {state.status === 'loading' && <Loading label="Buscando na memória…" />}
        {state.status === 'error' && <ErrorState error={state.error} onRetry={() => search()} />}
        {state.status === 'success' && state.data && state.data.items.length === 0 && (
          <Empty title="Nenhum cliente com esse dado neste espaço.">
            Confira o formato: telefone vai com o código do país (+1, +44, +55). Se o cliente nunca falou com um agente ligado a este espaço, a memória ainda não o conhece.
          </Empty>
        )}
        {state.status === 'success' && state.data && state.data.items.length > 0 && (
          <ul className={`${card} divide-y divide-line2 overflow-hidden`} aria-label="Clientes encontrados">
            {state.data.items.map((p) => (
              <li key={p.profile_id}>
                <Link to={paths.profile(space.space_id, p.profile_id)} className="flex min-h-14 items-center gap-4 px-4 py-3 transition-colors hover:bg-bg">
                  <span className="min-w-0 flex-1">
                    <span className="flex flex-wrap items-baseline gap-x-2">
                      <span className="font-mono text-[0.875rem] font-semibold text-ink">{p.pseudonym}</span>
                      <span className="text-[0.8125rem] text-ink2">{subjectKind(p.kind)}</span>
                    </span>
                    <span className={`${mono} mt-0.5 block text-ink2`}>
                      {[p.handles.slice(0, 3).map((h) => `${handleType(h.type)} ${h.value}`).join(' · '), p.last_seen && `última atividade ${fmtDateTime(p.last_seen)}`, p.partial && 'só IDs privados de canal']
                        .filter(Boolean)
                        .join(' · ')}
                    </span>
                  </span>
                  <span className="hidden text-[0.8125rem] font-semibold text-ink sm:inline">Abrir memória</span>
                  <Icon name="chevron" className="text-ink2" />
                </Link>
              </li>
            ))}
          </ul>
        )}
      </div>
    </section>
  )
}

const ORDERS: { value: ProfileOrder; label: string }[] = [
  { value: 'last_seen_desc', label: 'Atividade mais recente primeiro' },
  { value: 'last_seen_asc', label: 'Há mais tempo sem atividade primeiro' },
]

/** `?order=` and `?kind=` of the address, read back; anything else is the default. */
function orderOf(raw: string | null): ProfileOrder {
  return ORDERS.some((o) => o.value === raw) ? (raw as ProfileOrder) : 'last_seen_desc'
}
function kindOf(raw: string | null): '' | SubjectKind {
  return SUBJECT_KINDS.includes(raw as SubjectKind) ? (raw as SubjectKind) : ''
}

/**
 * Every customer the memory knows, by the latest activity on any of their identifiers, one page at a time
 * (`?p=`), with the order and the kind in the address too, so a reload or the way back from a customer lands
 * on the same page. The cell sends each identifier masked, whatever the role of the person: the list is for
 * recognizing and opening a customer, and the whole value stays on the customer's own screen, for the roles
 * that may see it. A page may come short, even empty, when one customer has many identifiers; the list ends
 * where the cell sends no cursor. Each page read leaves one receipt.
 */
function CustomerList() {
  const { space, cell } = useSpace()
  const [params, setParams] = useSearchParams()
  const order = orderOf(params.get('order'))
  const kind = kindOf(params.get('kind'))
  const pages = useNumberedPages((cursor, signal) => cell.listProfiles({ order, kind: kind || undefined, limit: 50, cursor: cursor ?? undefined }, { signal }), [order, kind, cell])
  const hid = useId()
  const top = useRef<HTMLElement>(null)

  // Another order or kind starts again on the first page, without the cursors of the old one.
  const filter = (key: 'order' | 'kind', value: string, fallback: string) => {
    const next = new URLSearchParams(params)
    next.delete('p')
    if (value && value !== fallback) next.set(key, value)
    else next.delete(key)
    setParams(next)
  }
  // A page changed from the pager at the bottom starts at the top of the list, at once (no smooth scroll).
  const turn = (go: () => void) => () => {
    go()
    const el = top.current
    if (el && el.getBoundingClientRect().top < 0) el.scrollIntoView?.({ block: 'start', behavior: 'instant' })
  }

  return (
    <section ref={top} aria-labelledby={hid} data-tour="customers.list" className="mt-6">
      <div className="mb-3 flex flex-col gap-3 lg:flex-row lg:items-end lg:justify-between">
        <h2 id={hid} className={h2}>
          Clientes deste espaço
        </h2>
        <div className="grid gap-3 sm:grid-cols-2 lg:w-[34rem]">
          <LabelledSelect label="Ordem" value={order} onChange={(v) => filter('order', v, 'last_seen_desc')} options={ORDERS} />
          <LabelledSelect
            label="Tipo de cliente"
            value={kind}
            onChange={(v) => filter('kind', v, '')}
            options={[{ value: '' as const, label: 'Todos' }, ...SUBJECT_KINDS.map((k) => ({ value: k, label: subjectKind(k) }))]}
          />
        </div>
      </div>

      {(pages.status === 'loading' || pages.status === 'idle') && <Loading label="Lendo os clientes do espaço…" />}
      {pages.status === 'error' && <ErrorState error={pages.error} onRetry={pages.reload} />}
      {pages.status === 'success' && pages.page === 1 && pages.items.length === 0 && !pages.hasNext && (
        <Empty
          title={kind ? 'Nenhum cliente deste tipo neste espaço.' : 'A memória deste espaço ainda não conhece nenhum cliente.'}
          action={
            <Link to={paths.agents(space.space_id)} className={btnSecondary}>
              Ver agentes e chaves
            </Link>
          }
        >
          Um cliente aparece aqui assim que chega o primeiro evento com um identificador dele. Exemplo do que você verá: <span className="text-ink">p_7q2m9x4k… · Pessoa · Telefone +55*******4321 · última atividade 22/09/2026 14:07</span>.
        </Empty>
      )}
      {pages.status === 'success' && (pages.page > 1 || pages.items.length > 0 || pages.hasNext) && (
        <>
          {pages.items.length > 0 ? (
            <ul aria-label="Clientes deste espaço" aria-busy={pages.busy} className={`${card} divide-y divide-line2 overflow-hidden transition-opacity ${pages.busy ? 'opacity-60' : ''}`}>
              {pages.items.map((p) => (
                <li key={p.profile_id}>
                  <Link to={paths.profile(space.space_id, p.profile_id)} className="flex min-h-14 items-center gap-4 px-4 py-3 transition-colors hover:bg-bg">
                    <span className="min-w-0 flex-1">
                      <span className="flex flex-wrap items-baseline gap-x-2">
                        <span className="font-mono text-[0.875rem] font-semibold text-ink">{p.pseudonym}</span>
                        <span className="text-[0.8125rem] text-ink2">
                          {subjectKind(p.kind)} · última atividade {fmtDateTime(p.last_seen)}
                        </span>
                      </span>
                      <span className={`${mono} mt-0.5 block break-all text-ink2`}>{maskedHandles(p)}</span>
                    </span>
                    <span className="hidden text-[0.8125rem] font-semibold text-ink sm:inline">Abrir memória</span>
                    <Icon name="chevron" className="text-ink2" />
                  </Link>
                </li>
              ))}
            </ul>
          ) : (
            <p className={`${card} px-4 py-4 text-[0.875rem] text-ink`}>{pages.hasNext ? 'Nenhum cliente nesta página. A lista continua na próxima.' : 'Nenhum cliente nesta página.'}</p>
          )}
          <Pager
            label="Páginas de clientes"
            page={pages.page}
            hasPrevious={pages.hasPrevious}
            hasNext={pages.hasNext}
            busy={pages.busy}
            onPrevious={turn(pages.previous)}
            onNext={turn(pages.next)}
          />
        </>
      )}
    </section>
  )
}

/** The masked identifiers of a row, and how many more the customer has than the three the cell sends. */
function maskedHandles(p: ProfileListed): string {
  const shown = p.handles.map((h) => `${handleType(h.type)} ${h.value}`)
  const more = p.handle_count - p.handles.length
  return [...shown, more > 0 && (more === 1 ? 'mais 1 identificador' : `mais ${more} identificadores`)].filter(Boolean).join(' · ')
}
