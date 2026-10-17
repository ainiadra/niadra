import { useId, useState, type FormEvent } from 'react'
import { Select } from '../../components/Select'
import { Link, useLocation, useNavigate, useSearchParams } from 'react-router-dom'
import type { ChainVerification, ReceiptKind, ReceiptQuery } from '../api/types'
import { dayToISO, fmtDateTime, fmtInt, shortHash, utcDayEndsAt, utcDayInput } from '../lib/format'
import { chainBreak, receiptKind, RECEIPT_KINDS, surface, verification } from '../lib/labels'
import { usePaged } from '../lib/useResource'
import { paths } from '../paths'
import { useSpace } from '../session'
import { Chip, PageHeader, useSources } from '../ui/bits'
import { ActionLine, ReceiptActor } from '../ui/ReceiptWords'
import { useAction } from '../ui/forms'
import { useTitle } from '../ui/layout'
import { Empty, ErrorState, LoadMore, Loading } from '../ui/States'
import { btnPrimary, btnQuiet, btnSecondary, card, h2, input, label, mono, select, small, td, th } from '../ui/styles'
import { Term } from '../ui/Term'

/**
 * Filters live in the URL so a filtered list can be shared: pseudonymous ids, a receipt kind and dates. The
 * conversation id is the exception: a channel's conversation id may be a phone number or an e-mail, so it
 * stays in the router state and travels to the cell only in the body of `POST /v1/receipts/search`.
 */
const URL_KEYS = ['source', 'profile', 'kind', 'from', 'to'] as const
const KEYS = ['source', 'profile', 'conversation', 'kind', 'from', 'to'] as const
type Filters = Record<(typeof KEYS)[number], string>

/** The router state of this screen: the conversation filter, never in the address. */
type ReceiptsState = { conversation?: string } | null

function readFilters(sp: URLSearchParams, conversation: string): Filters {
  return { ...(Object.fromEntries(URL_KEYS.map((k) => [k, sp.get(k) ?? ''])) as Omit<Filters, 'conversation'>), conversation }
}

function toQuery(f: Filters): ReceiptQuery {
  return {
    source_id: f.source || undefined,
    profile_id: f.profile || undefined,
    conversation_id: f.conversation || undefined,
    kind: (f.kind || undefined) as ReceiptKind | undefined,
    since: f.from ? dayToISO(f.from) : undefined,
    until: f.to ? dayToISO(f.to, true) : undefined,
    limit: 50,
  }
}

export function Receipts() {
  const { space, cell } = useSpace()
  useTitle('Comprovantes', space.environment === 'sandbox')
  const sources = useSources()
  const navigate = useNavigate()
  const location = useLocation()
  const [sp, setSp] = useSearchParams()
  const conversation = (location.state as ReceiptsState)?.conversation ?? ''
  const applied = readFilters(sp, conversation)
  const [draft, setDraft] = useState<Filters>(applied)
  const base = useId()
  const ids = Object.fromEntries(KEYS.map((k) => [k, `${base}-${k}`])) as Record<(typeof KEYS)[number], string>

  const q = toQuery(applied)
  const page = usePaged((cursor, signal) => cell.searchReceipts({ ...q, cursor: cursor ?? undefined }, { signal }), [JSON.stringify(q), cell])

  const set = (k: keyof Filters) => (e: { target: { value: string } }) => setDraft((d) => ({ ...d, [k]: e.target.value }))
  const apply = (e: FormEvent) => {
    e.preventDefault()
    const next = new URLSearchParams()
    for (const k of URL_KEYS) if (draft[k].trim()) next.set(k, draft[k].trim())
    const state: ReceiptsState = draft.conversation.trim() ? { conversation: draft.conversation.trim() } : null
    setSp(next, { state })
  }
  const clear = () => {
    setDraft(readFilters(new URLSearchParams(), ''))
    setSp(new URLSearchParams(), { state: null })
  }
  const filtered = KEYS.some((k) => applied[k])

  return (
    <>
      <PageHeader
        screen="receipts"
      />

      <form onSubmit={apply} data-tour="receipts.filters" className={`${card} grid gap-3 p-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-6`} aria-label="Filtrar comprovantes">
        <div>
          <label htmlFor={ids.source} className={label}>
            Agente ou fonte
          </label>
          <Select
            id={ids.source}
            triggerClassName={select}
            value={draft.source}
            onChange={(value) => set('source')({ target: { value } })}
            options={[{ value: '', label: 'Todos' }, ...sources.list.map((s) => ({ value: s.source_id, label: `${s.name}${s.vendor ? ` (${s.vendor})` : ''}` }))]}
          />
        </div>
        <div>
          <label htmlFor={ids.kind} className={label}>
            Tipo
          </label>
          <Select
            id={ids.kind}
            triggerClassName={select}
            value={draft.kind}
            onChange={(value) => set('kind')({ target: { value } })}
            options={[{ value: '', label: 'Todos' }, ...RECEIPT_KINDS.map((k) => ({ value: k, label: receiptKind(k) }))]}
          />
        </div>
        <div>
          <label htmlFor={ids.profile} className={label}>
            ID do perfil
          </label>
          <input id={ids.profile} className={`${input} font-mono`} value={draft.profile} onChange={set('profile')} autoComplete="off" spellCheck={false} />
        </div>
        <div>
          <label htmlFor={ids.conversation} className={label}>
            ID da conversa
          </label>
          <input id={ids.conversation} className={`${input} font-mono`} value={draft.conversation} onChange={set('conversation')} autoComplete="off" spellCheck={false} />
        </div>
        <div>
          <label htmlFor={ids.from} className={label}>
            De
          </label>
          <input id={ids.from} type="date" className={input} value={draft.from} onChange={set('from')} />
        </div>
        <div>
          <label htmlFor={ids.to} className={label}>
            Até
          </label>
          <input id={ids.to} type="date" className={input} value={draft.to} onChange={set('to')} min={draft.from || undefined} />
        </div>
        <div className="flex flex-wrap gap-2 sm:col-span-2 lg:col-span-3 xl:col-span-6">
          <button type="submit" className={btnPrimary}>
            Filtrar
          </button>
          {filtered && (
            <button type="button" className={btnQuiet} onClick={clear}>
              Limpar filtros
            </button>
          )}
        </div>
      </form>

      <section className="mt-6" data-tour="receipts.list" aria-label="Lista de comprovantes">
        {(page.status === 'loading' || page.status === 'idle') && <Loading label="Buscando comprovantes…" />}
        {page.status === 'error' && <ErrorState error={page.error} onRetry={page.reload} />}
        {page.status === 'success' && page.items.length === 0 && (
          <Empty title={filtered ? 'Nenhum comprovante com esses filtros.' : 'Nenhum comprovante neste espaço ainda.'}>
            {filtered ? (
              'Amplie o período ou tire um dos filtros.'
            ) : (
              <>
                O primeiro comprovante aparece quando um agente pede contexto, consulta o histórico ou alguém abre a memória de um cliente aqui no Console. Exemplo do que você verá:{' '}
                <span className="text-ink">30/09/2026 14:07 · Leitura · Agente de voz · 3 itens entregues, 1 retido</span>.
              </>
            )}
          </Empty>
        )}
        {page.status === 'success' && page.items.length > 0 && (
          <>
            <p className={`${small} mb-2`}>
              Do mais recente para o mais antigo, 50 por página. O perfil aparece pelo pseudônimo, o mesmo da lista de Clientes. Uma ação no Console diz quem fez o quê; uma chave emitida ou revogada é do plano de controle, sem pessoa. Clique numa linha para abrir o comprovante.
            </p>
            <div className={`${card} overflow-x-auto`}>
              <table className="w-full min-w-[56rem] border-collapse">
                <caption className="sr-only">Comprovantes, do mais recente para o mais antigo</caption>
                <thead>
                  <tr>
                    <th scope="col" className={th}>Quando</th>
                    <th scope="col" className={th}>Tipo</th>
                    <th scope="col" className={th}>Quem</th>
                    <th scope="col" className={th}>
                      <Term k="perfil">Perfil</Term>
                    </th>
                    <th scope="col" className={th}>Conversa ou tarefa</th>
                    <th scope="col" className={th}>O que leu ou fez</th>
                    <th scope="col" className={`${th} text-right`}>Itens</th>
                    <th scope="col" className={`${th} text-right`}>
                      <Term k="retido">Retidos</Term>
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {page.items.map((r) => {
                    const to = paths.receipt(space.space_id, r.receipt_id)
                    return (
                      <tr key={r.receipt_id} className="cursor-pointer transition-colors hover:bg-bg" onClick={() => navigate(to)}>
                        <td className={`${td} ${mono} whitespace-nowrap`}>
                          <Link to={to} className="inline-flex min-h-11 items-center font-semibold text-ink underline decoration-accent decoration-2 underline-offset-4 lg:min-h-0" onClick={(e) => e.stopPropagation()}>
                            {fmtDateTime(r.read_at)}
                          </Link>
                        </td>
                        <td className={td}>{r.kind === 'denied' ? <Chip tone="danger">{receiptKind(r.kind)}</Chip> : receiptKind(r.kind)}</td>
                        <td className={td}>
                          <ReceiptActor receipt={r} />
                        </td>
                        <td className={`${td} ${mono}`} title={r.pseudonym && r.profile_id ? `ID do perfil ${r.profile_id}` : undefined}>
                          {r.pseudonym ?? r.profile_id ?? <span className="text-ink2">{r.kind === 'admin' ? 'nenhum' : 'sem cliente'}</span>}
                        </td>
                        <td className={`${td} ${mono} text-ink2`}>{r.conversation_id ?? r.task_id ?? 'nenhuma'}</td>
                        <td className={`${td} min-w-[16rem]`}>
                          {r.kind === 'admin' ? (
                            <ActionLine receipt={r} compact />
                          ) : (
                            <>
                              {surface(r.surface)}
                              {r.verification && <span className="text-ink2"> · {verification(r.verification)}</span>}
                            </>
                          )}
                        </td>
                        <td className={`${td} text-right tabular-nums`}>{fmtInt(r.item_ids.length)}</td>
                        <td className={`${td} text-right tabular-nums ${r.withheld > 0 ? 'font-semibold text-ink' : 'text-ink2'}`}>{fmtInt(r.withheld)}</td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>
            <LoadMore hasMore={page.hasMore} loading={page.loadingMore} error={page.moreError} onClick={page.loadMore} />
          </>
        )}
      </section>

      <ChainCheck />
    </>
  )
}

/**
 * The copy of the day's root kept outside the database (app/audit.py verify_chain): whether it holds the
 * recomputed root (false when it is missing or holds another one, null when it cannot be read now), and
 * where it was written, write-once or not. Null for an anchored day answered by a cell without the copy.
 */
function anchorCopy(r: ChainVerification): string | null {
  if (r.anchor_copy_matches === undefined) return null
  const copy =
    r.anchor_copy_matches === null
      ? 'A cópia fora do banco não pôde ser lida agora e ficou sem conferir: confira de novo em alguns minutos.'
      : r.anchor_copy_matches
        ? 'A cópia fora do banco guarda a mesma raiz.'
        : 'A cópia fora do banco falta ou guarda outra raiz.'
  const where =
    r.anchor_locked === true
      ? 'Ela foi gravada em armazenamento de gravação única (S3 com trava de objeto em modo de conformidade), que ninguém, nem a própria célula, apaga ou troca antes do fim da retenção.'
      : r.anchor_locked === false
        ? 'Ela foi gravada no bucket da célula, sem trava de objeto: não é gravação única.'
        : null
  return where ? `${copy} ${where}` : copy
}

/** Recomputes one day's hash chain and compares it with the anchored root. */
function ChainCheck() {
  const { cell } = useSpace()
  // The chain is per UTC day, like the receipts' `day`: the default is yesterday in UTC, not in the browser's zone.
  const [day, setDay] = useState(utcDayInput(1))
  const act = useAction<ChainVerification>()
  const id = useId()
  const r = act.result
  const copy = r && r.root_matches !== null ? anchorCopy(r) : null
  return (
    <section aria-labelledby={`${id}-h`} data-tour="receipts.chain" className={`${card} mt-8 p-4 sm:p-5`}>
      <h2 id={`${id}-h`} className={h2}>
        Conferir a cadeia de um dia
      </h2>
      <p className={`${small} mt-0.5 max-w-[72ch]`}>
        Cada comprovante guarda o hash do anterior, e a raiz do dia fica gravada no banco e numa cópia fora dele. A conferência refaz a cadeia e compara com as duas.
      </p>
      <form
        className="mt-4 flex flex-wrap items-end gap-3"
        onSubmit={(e) => {
          e.preventDefault()
          act.run(() => cell.verifyChain(day))
        }}
      >
        <div>
          <label htmlFor={id} className={label}>
            Dia (UTC)
          </label>
          <input id={id} type="date" className={input} value={day} onChange={(e) => setDay(e.target.value)} required aria-describedby={`${id}-utc`} />
        </div>
        <button type="submit" className={btnSecondary} disabled={act.busy || !day}>
          {act.busy ? 'Conferindo…' : 'Conferir'}
        </button>
        <p id={`${id}-utc`} className={`${small} basis-full`}>
          O dia da cadeia é contado em UTC, como o servidor grava cada comprovante: o dia de hoje termina às {utcDayEndsAt()} no seu horário, e o de ontem já pode ser conferido.
        </p>
      </form>
      <div className="mt-4" aria-live="polite">
        {Boolean(act.error) && <ErrorState error={act.error} compact />}
        {r && (
          <div role="status" className="grid gap-2">
            <p className="flex flex-wrap items-center gap-2 text-ink">
              {r.valid ? <Chip tone="ok">Cadeia íntegra</Chip> : <Chip tone="danger">Cadeia quebrada</Chip>}
              {fmtInt(r.receipts)} comprovantes em {r.heads.length} {r.heads.length === 1 ? 'fatia' : 'fatias'}
            </p>
            <p className={small}>
              {r.root_matches === null
                ? 'O dia ainda não foi ancorado: a raiz e a cópia fora do banco saem quando todos os comprovantes do dia estiverem gravados. Até lá, só a cadeia é conferida.'
                : r.root_matches
                  ? `A raiz confere com a ancorada (${shortHash(r.anchored_root, 16)}).`
                  : `A raiz calculada não confere com a ancorada (${shortHash(r.anchored_root, 16)}).`}
            </p>
            {copy && <p className={small}>{copy}</p>}
            {r.breaks.length > 0 && (
              <ul className="grid gap-1" aria-label="Onde a cadeia quebrou">
                {r.breaks.map((b) => (
                  <li key={`${b.slice}:${b.seq}:${b.reason}`} className="text-[0.8125rem] leading-5 text-ink">
                    <span className={mono}>
                      fatia {b.slice}, comprovante {b.seq}
                    </span>
                    : {chainBreak(b.reason)}.
                  </li>
                ))}
              </ul>
            )}
          </div>
        )}
      </div>
    </section>
  )
}
