import { useId, useState, type FormEvent, type ReactNode } from 'react'
import { Link, useLocation, useNavigate, useParams, useSearchParams } from 'react-router-dom'
import { Select } from '../../components/Select'
import { ApiError } from '../api/http'
import {
  TURN_ID,
  type ClaimRecord,
  type ClaimValue,
  type Completeness,
  type ContentMode,
  type Fidelity,
  type Interaction,
  type ReadSurface,
  type TurnBlob,
  type TurnCall,
  type TurnFlag,
  type TurnKind,
  type TurnRecord,
  type TurnSearchRequest,
  type TurnView,
} from '../api/turns'
import { dayToISO, fmtDate, fmtInt, shortHash } from '../lib/format'
import { Retrying, usePaged, useResource } from '../lib/useResource'
import { paths } from '../paths'
import { useSpace } from '../session'
import { Chip, JsonView, PageHeader, Props } from '../ui/bits'
import { Icon } from '../ui/Icon'
import { useTitle } from '../ui/layout'
import { Empty, ErrorState, LoadMore, Loading } from '../ui/States'
import { btnPrimary, btnQuiet, btnSecondary, card, danger, h2, input, label, link, mono, small, td, th } from '../ui/styles'

/**
 * The turn viewer: why the agent did what it did, turn by turn. The list is the kept tier,
 * searched by the fields the server allows; any turn of the short tier opens by its id. A value
 * the company keeps in its own storage (`pointer`) or never sent (`hash_only`) is shown as where it lives
 * and its digest, and the Console never fetches it.
 */

/* Words for the record's vocabularies, in the tenant's language. */

const KIND: Record<TurnKind, string> = { message: 'Mensagem', action: 'Ação na interface', event: 'Evento', timer: 'Cronômetro' }
const FIDELITY: Record<Fidelity, string> = { gold: 'Ouro', silver: 'Prata', bronze: 'Bronze' }
const FIDELITY_MEANS: Record<Fidelity, string> = {
  gold: 'o SDK interceptou as chamadas do agente no processo dele',
  silver: 'montado dos spans de OpenTelemetry, com o que a pessoa viu',
  bronze: 'montado dos spans de OpenTelemetry',
}
const COMPLETENESS: Record<Completeness, string> = {
  complete: 'Completo',
  partial: 'Parcial: o SDK largou valores para proteger a fila de envio',
  incomplete: 'Incompleto: a gravação falhou durante o turno',
  unknown: 'Desconhecida: o OpenTelemetry não diz se o turno veio inteiro',
}
const MODE: Record<ContentMode, string> = {
  stored: 'Valores guardados na Niadra, cifrados',
  pointer: 'Valores no armazenamento da empresa; na Niadra, só onde estão e o hash',
  hash_only: 'Só hashes: nenhum valor foi gravado',
}
const SURFACE: Record<ReadSurface, string> = {
  pack: 'Contexto',
  state: 'Estado dos objetos',
  constraints: 'Restrições',
  coordination: 'Coordenação',
  budget: 'Orçamento de contexto',
  history: 'Histórico',
  agent_memory: 'Memória dos agentes',
  agent_state: 'Memória de trabalho',
}
const FLAGS: Record<TurnFlag, { label: string; means: string }> = {
  error: { label: 'Erro', means: 'Uma chamada do turno deu erro.' },
  guard_acted: { label: 'Contrato agiu', means: 'O contrato de afirmação agiu sobre o que o agente ia dizer.' },
  guard_budget_exceeded: { label: 'Conferência no limite', means: 'A conferência das afirmações passou do tempo, e a saída seguiu como estava.' },
  handoff: { label: 'Transbordo', means: 'O turno passou a conversa para outra pessoa ou outro agente.' },
  assertion_failed: { label: 'Asserção falhou', means: 'Uma asserção sobre o turno falhou.' },
  synthetic: { label: 'Sintético', means: 'Turno de teste, com dados sintéticos.' },
  incomplete: { label: 'Incompleto', means: 'A gravação falhou durante o turno.' },
  negative_feedback: { label: 'Feedback negativo', means: 'A pessoa deu um retorno negativo sobre o turno.' },
  truncated: { label: 'Valor cortado', means: 'Algum argumento ou resultado chegou cortado.' },
}
const TURN_FLAGS = Object.keys(FLAGS) as TurnFlag[]
const CALL_STATUS: Record<string, string> = { ok: 'ok', error: 'erro', timeout: 'tempo esgotado', cancelled: 'cancelada' }
const DECISION: Record<string, string> = { allow: 'Liberar', defer: 'Adiar', deny: 'Negar', handoff_to: 'Transbordar' }
const EFFECT: Record<string, string> = { reserved: 'Reservado', done: 'Feito', failed: 'Falhou', unknown_outcome: 'Resultado desconhecido' }
const NATURE: Record<string, string> = { computed: 'calculado', quoted: 'citado', model: 'do modelo' }
/** What a claim is about, by the categories of the claim contract (domain/serve/claims.py); a tenant's own category shows as written. */
const CLAIM_CATEGORY: Record<string, string> = {
  money: 'Valor',
  price: 'Preço',
  discount: 'Desconto',
  fee: 'Taxa',
  date: 'Data',
  deadline: 'Prazo',
  delivery: 'Entrega',
  availability: 'Disponibilidade',
  stock: 'Estoque',
  quantity: 'Quantidade',
  identifier: 'Identificador',
  status: 'Situação',
  policy: 'Regra da empresa',
  commitment: 'Compromisso',
  eligibility: 'Elegibilidade',
  coverage: 'Cobertura',
  dosage: 'Dose',
  contact: 'Contato',
  address: 'Endereço',
  product: 'Produto',
  other: 'Outro',
}
const CLAIM_CLASS: Record<string, string> = {
  money: 'dinheiro',
  percent: 'percentual',
  date: 'data',
  duration: 'duração',
  quantity: 'quantidade',
  count: 'contagem',
  dosage: 'dose',
}
const VERDICT: Record<string, string> = {
  matched: 'Confere',
  mismatch: 'Não confere',
  stale: 'Evidência vencida',
  gap_not_stated: 'Lacuna não dita',
  role_ambiguous: 'Papel ambíguo',
  unsupported: 'Sem suporte',
  no_evidence: 'Sem evidência',
  quoted_found: 'Citação encontrada',
  quoted_missing: 'Citação não encontrada',
  anchored: 'Ancorado no texto',
  below_threshold: 'Âncora abaixo do limite',
  source_exists_claim_unverified: 'Fonte existe, afirmação não conferida',
  source_missing: 'Fonte ausente',
  not_checked: 'Não conferido',
}
const VERDICT_OK = new Set(['matched', 'quoted_found', 'anchored'])
const VERDICT_BAD = new Set(['mismatch', 'no_evidence', 'unsupported', 'quoted_missing', 'source_missing'])
const CLAIM_ACTION: Record<string, string> = {
  none: 'nenhuma',
  block: 'bloqueou',
  warn: 'avisou',
  count: 'contou',
  rewrite: 'reescreveu',
  discard_anchor: 'descartou a âncora',
}
const HOW: Record<string, string> = {
  click: 'Clicou em',
  detail: 'Abriu o detalhe de',
  mention: 'Mencionou',
  add_to_cart: 'Pôs no carrinho',
  compare: 'Comparou',
  share: 'Compartilhou',
  other: 'Interagiu com',
}
const STRENGTH: Record<string, string> = { must: 'Exige', must_not: 'Recusa', prefer: 'Prefere', avoid: 'Evita' }
const OP: Record<string, string> = {
  in: 'entre',
  not_in: 'fora de',
  eq: 'igual a',
  ne: 'diferente de',
  lt: 'menor que',
  lte: 'até',
  gt: 'maior que',
  gte: 'a partir de',
  between: 'entre',
}
const PREF_SCOPE: Record<string, string> = { turn: 'neste turno', session: 'nesta sessão', persistent: 'daqui em diante' }
const METHOD: Record<string, string> = {
  bridge: 'pela ponte da interface',
  otel: 'por um span de OpenTelemetry',
  tool_result: 'inferida do resultado da ferramenta, que conta mais do que foi visto',
}

/* Numbers and times. Turns happen in seconds, so their instants show the seconds. */

const two = (n: number) => String(n).padStart(2, '0')
function fmtInstant(iso: string | null | undefined): string {
  if (!iso) return 'sem data'
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return iso
  return `${two(d.getDate())}/${two(d.getMonth() + 1)}/${d.getFullYear()} ${two(d.getHours())}:${two(d.getMinutes())}:${two(d.getSeconds())}`
}
const oneDecimal = new Intl.NumberFormat('pt-BR', { maximumFractionDigits: 1 })
const fmtMs = (ms: number) => (ms < 1000 ? `${fmtInt(ms)} ms` : `${oneDecimal.format(ms / 1000)} s`)
const usd = new Intl.NumberFormat('pt-BR', { style: 'currency', currency: 'USD', minimumFractionDigits: 2, maximumFractionDigits: 4 })
const fmtBytes = (n: number) => (n < 1024 ? `${fmtInt(n)} bytes` : n < 1024 * 1024 ? `${oneDecimal.format(n / 1024)} KB` : `${oneDecimal.format(n / 1024 / 1024)} MB`)
const plural = (n: number, one: string, many: string) => `${fmtInt(n)} ${n === 1 ? one : many}`
const scalar = (v: unknown) => (v === null || v === undefined ? 'vazio' : typeof v === 'string' ? v : JSON.stringify(v))

/* The answers that are states, not failures. */

const FEATURE_OFF = 'not enabled for this space'
const cause = (e: unknown) => (e instanceof Retrying ? e.cause : e)
const status = (e: unknown) => {
  const c = cause(e)
  return c instanceof ApiError ? c.status : 0
}

function RecordingOff() {
  const { space } = useSpace()
  return (
    <Empty
      title="O registro de turnos está desligado neste espaço."
      action={
        <Link className={`${link} inline-flex min-h-11 items-center lg:min-h-0`} to={paths.config(space.space_id)}>
          Abrir a Configuração
        </Link>
      }
    >
      O registro de turnos vem ligado; neste espaço, uma decisão registrada em Configuração desligou o recurso <span className={mono}>turns</span> no documento{' '}
      <span className={mono}>features</span>. Ligado, cada turno dos agentes chega aqui com o que o agente leu, chamou, mostrou, afirmou e decidiu.
    </Empty>
  )
}

/* The list: the kept tier, and a box to open any turn by its id. */

/**
 * The agent, a flag and dates live in the URL so a filtered list can be shared. The conversation id stays in
 * the router state and travels only in the body of `POST /v1/turns/search`: it may be a phone number.
 */
const URL_KEYS = ['agent', 'flag', 'from', 'to'] as const
const KEYS = ['agent', 'flag', 'conversation', 'from', 'to'] as const
type Filters = Record<(typeof KEYS)[number], string>
type TurnsState = { conversation?: string } | null

function readFilters(sp: URLSearchParams, conversation: string): Filters {
  return { ...(Object.fromEntries(URL_KEYS.map((k) => [k, sp.get(k) ?? ''])) as Omit<Filters, 'conversation'>), conversation }
}

function toQuery(f: Filters): TurnSearchRequest {
  return {
    agent: f.agent || undefined,
    flags: f.flag ? [f.flag as TurnFlag] : undefined,
    conversation_id: f.conversation || undefined,
    since: f.from ? dayToISO(f.from) : undefined,
    until: f.to ? dayToISO(f.to, true) : undefined,
    limit: 50,
  }
}

export function Turns() {
  const { space, cell } = useSpace()
  useTitle('Turnos dos agentes', space.environment === 'sandbox')
  const navigate = useNavigate()
  const location = useLocation()
  const [sp, setSp] = useSearchParams()
  const conversation = (location.state as TurnsState)?.conversation ?? ''
  const applied = readFilters(sp, conversation)
  const [draft, setDraft] = useState<Filters>(applied)
  const base = useId()
  const ids = Object.fromEntries([...KEYS, 'open'].map((k) => [k, `${base}-${k}`])) as Record<(typeof KEYS)[number] | 'open', string>

  const q = toQuery(applied)
  const page = usePaged((cursor, signal) => cell.turns.search({ ...q, cursor: cursor ?? undefined }, { signal }), [JSON.stringify(q), cell])

  const set = (k: keyof Filters) => (e: { target: { value: string } }) => setDraft((d) => ({ ...d, [k]: e.target.value }))
  const apply = (e: FormEvent) => {
    e.preventDefault()
    const next = new URLSearchParams()
    for (const k of URL_KEYS) if (draft[k].trim()) next.set(k, draft[k].trim())
    const state: TurnsState = draft.conversation.trim() ? { conversation: draft.conversation.trim() } : null
    setSp(next, { state })
  }
  const clear = () => {
    setDraft(readFilters(new URLSearchParams(), ''))
    setSp(new URLSearchParams(), { state: null })
  }
  const filtered = KEYS.some((k) => applied[k])

  const [turnId, setTurnId] = useState('')
  const [badId, setBadId] = useState(false)
  const open = (e: FormEvent) => {
    e.preventDefault()
    const id = turnId.trim()
    if (!TURN_ID.test(id)) return setBadId(true)
    navigate(paths.turn(space.space_id, id))
  }

  return (
    <>
      <PageHeader
        screen="turns"
      />

      <form onSubmit={open} data-tour="turns.open" className={`${card} mb-4 flex flex-wrap items-end gap-3 p-4`} aria-label="Abrir um turno pelo ID">
        <div className="min-w-0 flex-1 basis-64">
          <label htmlFor={ids.open} className={label}>
            ID do turno
          </label>
          <input
            id={ids.open}
            className={`${input} font-mono`}
            value={turnId}
            onChange={(e) => {
              setTurnId(e.target.value)
              setBadId(false)
            }}
            autoComplete="off"
            spellCheck={false}
            aria-invalid={badId}
            aria-describedby={badId ? `${ids.open}-hint` : undefined}
          />
          {badId && (
            <p id={`${ids.open}-hint`} className={`mt-1.5 text-[0.8125rem] leading-5 ${danger.text}`}>
              Um ID de turno tem de 1 a 64 letras, números, ponto, sublinhado ou hífen, e começa com letra ou número.
            </p>
          )}
        </div>
        <button type="submit" className={btnSecondary} disabled={!turnId.trim()}>
          Abrir o turno
        </button>
      </form>

      <form onSubmit={apply} data-tour="turns.filters" className={`${card} grid gap-3 p-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-5`} aria-label="Filtrar turnos guardados">
        <div>
          <label htmlFor={ids.agent} className={label}>
            Agente
          </label>
          <input id={ids.agent} className={input} value={draft.agent} onChange={set('agent')} autoComplete="off" spellCheck={false} placeholder="nome no SDK" />
        </div>
        <div>
          <label htmlFor={ids.flag} className={label}>
            Marca
          </label>
          <Select
            id={ids.flag}
            triggerClassName={input}
            value={draft.flag}
            onChange={(value) => set('flag')({ target: { value } })}
            options={[{ value: '', label: 'Todas' }, ...TURN_FLAGS.map((f) => ({ value: f, label: FLAGS[f].label }))]}
          />
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
        <div className="flex flex-wrap gap-2 sm:col-span-2 lg:col-span-3 xl:col-span-5">
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

      <section className="mt-6" aria-label="Turnos guardados">
        {(page.status === 'loading' || page.status === 'idle') && <Loading label="Buscando turnos…" />}
        {page.status === 'error' && (status(page.error) === 404 ? <RecordingOff /> : <ErrorState error={page.error} onRetry={page.reload} />)}
        {page.status === 'success' && page.items.length === 0 && (
          <Empty title={filtered ? 'Nenhum turno guardado com esses filtros.' : 'Nenhum turno guardado neste espaço ainda.'}>
            {!filtered && (
              <p className="mb-1.5">
                Exemplo do que você verá: <span className="text-ink">30/09/2026 14:07 · Vendas no WhatsApp · mensagem · 1 leitura, 2 chamadas, 1 afirmação</span>.
              </p>
            )}
            {filtered
              ? 'Amplie o período ou tire um dos filtros. A busca só alcança os turnos guardados; qualquer outro turno abre pelo ID, na caixa acima.'
              : 'Um turno é guardado quando a captura o marca (um erro, um transbordo, o contrato de afirmação agindo), quando cai na amostra ou quando o agente pede para promover a conversa. Os outros ficam no nível curto e abrem pelo ID.'}
          </Empty>
        )}
        {page.status === 'success' && page.items.length > 0 && (
          <>
            <div data-tour="turns.list" className={`${card} overflow-x-auto`}>
              <table className="w-full min-w-[52rem] border-collapse">
                <caption className="sr-only">Turnos guardados, do mais recente para o mais antigo</caption>
                <thead>
                  <tr>
                    <th scope="col" className={th}>Início</th>
                    <th scope="col" className={th}>Agente</th>
                    <th scope="col" className={th}>Tipo</th>
                    <th scope="col" className={th}>Registro</th>
                    <th scope="col" className={th}>Completude</th>
                    <th scope="col" className={th}>Replay</th>
                    <th scope="col" className={th}>Marcas</th>
                  </tr>
                </thead>
                <tbody>
                  {page.items.map((t) => {
                    const to = paths.turn(space.space_id, t.turn_id)
                    return (
                      <tr key={t.turn_id} className="cursor-pointer transition-colors hover:bg-bg" onClick={() => navigate(to)}>
                        <td className={`${td} ${mono} whitespace-nowrap`}>
                          <Link to={to} className="inline-flex min-h-11 items-center font-semibold text-ink underline decoration-accent decoration-2 underline-offset-4 lg:min-h-0" onClick={(e) => e.stopPropagation()}>
                            {fmtInstant(t.started_at)}
                          </Link>
                        </td>
                        <td className={td}>{t.agent}</td>
                        <td className={td}>{KIND[t.kind] ?? t.kind}</td>
                        <td className={td}>{FIDELITY[t.fidelity] ?? t.fidelity}</td>
                        <td className={td}>{t.completeness === 'complete' ? 'Completo' : <Chip>{(COMPLETENESS[t.completeness] ?? t.completeness).split(':')[0]}</Chip>}</td>
                        <td className={td}>{t.replayable ? 'Sim' : 'Não'}</td>
                        <td className={td}>
                          {t.flags.length === 0 ? (
                            <span className="text-ink2">nenhuma</span>
                          ) : (
                            <span className="flex flex-wrap gap-1">
                              {t.flags.map((f) => (
                                <Chip key={f} tone={f === 'error' || f === 'assertion_failed' ? 'danger' : 'neutral'}>
                                  {FLAGS[f]?.label ?? f}
                                </Chip>
                              ))}
                            </span>
                          )}
                        </td>
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
    </>
  )
}

/* One turn. */

export function TurnDetail() {
  const { turnId = '' } = useParams()
  const { space, cell } = useSpace()
  useTitle('Turno', space.environment === 'sandbox')
  const view = useResource((signal) => cell.turns.get(turnId, { signal }), [turnId, cell])
  const back = { to: paths.turns(space.space_id), label: 'Turnos dos agentes' }

  if (view.status === 'loading' || view.status === 'idle') return <Loading label="Abrindo o turno…" />
  if (view.status === 'error' || !view.data) {
    const err = cause(view.error)
    const off = err instanceof ApiError && err.status === 404 && err.detail === FEATURE_OFF
    return (
      <>
        <PageHeader screen="turn" back={back} description={<span className={`${mono} break-all`}>{turnId}</span>} />
        {off ? (
          <RecordingOff />
        ) : status(view.error) === 404 ? (
          <Empty title="Este turno não está guardado.">
            Todo turno fica no nível curto até a retenção que a empresa definir; sem ela, nada expira. Ele sai quando o titular pede o apagamento dos dados, e não aparece se nunca foi gravado neste espaço.
          </Empty>
        ) : (
          <ErrorState error={view.error} onRetry={view.reload} />
        )}
      </>
    )
  }
  return <TurnScreen view={view.data} back={back} />
}

const PIN_NAME: Record<string, string> = {
  prompts: 'prompts',
  model: 'modelo',
  corpus_digest: 'digest do corpus',
  assembler: 'montador de contexto',
  tool_schemas: 'esquemas das ferramentas',
}

const INCOMPLETE: Record<string, string> = {
  partial: 'o registro está parcial: a fila do SDK deixou valores de fora',
  incomplete: 'a gravação falhou durante o turno',
  unknown: 'não se sabe se o registro está completo',
}

/** Why a turn cannot be run again, in the words of the reasons the cell gives (`replay_blockers`). */
function replayBlockers(v: TurnView): string[] {
  if (v.replayable) return []
  const why = (v.replay_blockers ?? []).map((b) => {
    if (b.reason === 'content_mode') return 'só os hashes foram gravados, sem os valores'
    if (b.reason === 'fidelity')
      return `o registro veio do OpenTelemetry (nível ${b.value === 'silver' ? 'prata' : 'bronze'}), que não guarda o bastante para reexecutar o turno`
    if (b.reason === 'completeness') return INCOMPLETE[b.value] ?? 'o registro não está completo'
    return `falta o pino ${PIN_NAME[b.value] ?? b.value}, que a gravação deste espaço exige`
  })
  return why.length ? why : ['o servidor não informou o motivo']
}

const SECTIONS: [string, string][] = [
  ['resumo', 'Resumo'],
  ['build', 'Build'],
  ['leu', 'O que leu'],
  ['chamou', 'O que chamou'],
  ['mostrou', 'O que mostrou'],
  ['afirmou', 'O que afirmou'],
  ['decidiu', 'O que decidiu'],
  ['marcas', 'Marcas'],
]

function Section({ id, title, children, intro, tour }: { id: string; title: string; intro?: ReactNode; children: ReactNode; tour?: string }) {
  return (
    <section data-tour={tour} id={id} aria-labelledby={`h-${id}`} className="min-w-0 scroll-mt-20">
      <h2 id={`h-${id}`} className={`${h2} mb-1`}>
        {title}
      </h2>
      {intro && <p className={`${small} mb-3 max-w-[76ch]`}>{intro}</p>}
      {!intro && <div className="mb-2" />}
      {children}
    </section>
  )
}

function TurnScreen({ view, back }: { view: TurnView; back: { to: string; label: string } }) {
  const { space } = useSpace()
  const r = view.record
  const flags = r.flags ?? []
  const blockers = replayBlockers(view)
  const fidelity = r.fidelity ?? 'gold'
  const completeness = r.completeness ?? 'complete'
  const calls = r.calls ?? []
  const anchors = new Map(calls.map((c, i) => [c.call_id, `chamada-${i + 1}`]))

  return (
    <>
      <PageHeader screen="turn" back={back} title={`${KIND[r.kind ?? 'message']} de ${r.agent.name}, ${fmtInstant(r.started_at)}`} description={<span className={`${mono} break-all`}>{r.turn_id}</span>} />

      <nav aria-label="Partes do turno" className="mb-6">
        <ol className="flex flex-wrap items-center gap-x-1 gap-y-2 text-[0.875rem]">
          {SECTIONS.map(([id, text], i) => (
            <li key={id} className="flex items-center gap-1">
              {i > 0 && <Icon name="chevron" size={16} className="hidden text-ink3 sm:block" />}
              <a href={`#${id}`} className="inline-flex min-h-11 items-center rounded-md border border-line bg-surface px-2.5 font-semibold text-ink hover:bg-bg lg:min-h-8">
                {text}
              </a>
            </li>
          ))}
        </ol>
      </nav>

      <div className="grid gap-8">
        <section id="resumo" aria-labelledby="h-resumo" className={`${card} min-w-0 scroll-mt-20 p-4 sm:p-5`}>
          <h2 id="h-resumo" className={`${h2} mb-4`}>
            Resumo
          </h2>
          <Props
            items={[
              ['Agente', r.agent.role ? `${r.agent.name} · ${r.agent.role}` : r.agent.name],
              [
                'Sub-turno de',
                r.agent.parent_turn_id ? (
                  <Link className={`${link} break-all font-mono`} to={paths.turn(space.space_id, r.agent.parent_turn_id)}>
                    {r.agent.parent_turn_id}
                  </Link>
                ) : (
                  'nenhum: é um turno de primeiro nível'
                ),
              ],
              ['Conversa ou tarefa', <span className={`${mono} break-all`}>{r.conversation_id ?? r.task_id ?? 'nenhuma'}</span>],
              ['Tipo', KIND[r.kind ?? 'message']],
              ['Início', <span className={mono}>{fmtInstant(r.started_at)}</span>],
              ['Fim', <span className={mono}>{r.ended_at ? fmtInstant(r.ended_at) : 'não informado'}</span>],
              ['Latência', typeof r.latency_ms === 'number' ? fmtMs(r.latency_ms) : 'não informada'],
              ['Custo', r.cost ? usd.format(r.cost.usd) : 'não informado'],
              ['Registro', `${FIDELITY[fidelity]}: ${FIDELITY_MEANS[fidelity]}`],
              ['Completude', COMPLETENESS[completeness]],
              ['Conteúdo', MODE[r.content_mode]],
              ['Nível', view.tier === 'kept' ? `Guardado${view.kept_until ? ` até ${fmtDate(view.kept_until)}` : ' até a retenção da empresa'}` : 'Curto: fica até a retenção da empresa'],
              [
                'Replay',
                view.replayable ? (
                  <Chip tone="ok">Pode ser reexecutado</Chip>
                ) : (
                  <span>
                    <Chip>Não pode ser reexecutado</Chip>
                    <span className={`${small} mt-1 block`}>{blockers.map((b) => b.charAt(0).toUpperCase() + b.slice(1)).join('. ')}.</span>
                  </span>
                ),
              ],
              [
                'Marcas',
                flags.length ? (
                  <span className="flex flex-wrap gap-1">
                    {flags.map((f) => (
                      <Chip key={f} tone={f === 'error' || f === 'assertion_failed' ? 'danger' : 'neutral'}>
                        {FLAGS[f]?.label ?? f}
                      </Chip>
                    ))}
                  </span>
                ) : (
                  'nenhuma'
                ),
              ],
            ]}
          />
        </section>

        <Section id="build" title="O build fixado" intro="O que precisa ser igual para o replay reproduzir este turno: prompts, modelo, corpus, montador de contexto e esquemas das ferramentas.">
          <Build record={r} />
        </Section>

        <Section id="leu" tour="turn.read" title="O que leu" intro="Cada leitura da Niadra, pela versão do que foi lido.">
          <Reads record={r} />
        </Section>

        <Section id="chamou" tour="turn.calls" title="O que chamou" intro="Chamadas de ferramenta e de modelo, na ordem em que aconteceram. Uma chamada feita dentro de outra aparece recuada.">
          <Calls record={r} anchors={anchors} />
        </Section>

        <Section id="mostrou" title="O que mostrou" intro="Listas entregues à pessoa, o que ela viu, escolheu, preferiu ou recusou.">
          <Interactions items={r.interactions ?? []} />
        </Section>

        <Section id="afirmou" tour="turn.claims" title="O que afirmou" intro="Cada afirmação que o contrato encontrou no que o agente disse ou escreveu, com o veredito e a evidência no próprio turno.">
          <Claims items={r.claims ?? []} anchors={anchors} calls={calls} />
        </Section>

        <Section id="decidiu" title="O que decidiu" intro="As decisões de coordenação em que o turno se apoiou, os efeitos externos e o que o turno emitiu.">
          <Decisions record={r} />
        </Section>

        <Section id="marcas" title="Marcas da captura">
          {flags.length === 0 ? (
            <Empty title="Nenhuma marca na captura." />
          ) : (
            <ul className={`${card} divide-y divide-line2`}>
              {flags.map((f) => (
                <li key={f} className="flex flex-wrap items-baseline gap-2 px-4 py-3">
                  <Chip tone={f === 'error' || f === 'assertion_failed' ? 'danger' : 'neutral'}>{FLAGS[f]?.label ?? f}</Chip>
                  <span className="text-ink">{FLAGS[f]?.means ?? f}</span>
                </li>
              ))}
            </ul>
          )}
        </Section>
      </div>
    </>
  )
}

/* The build. */

function Build({ record }: { record: TurnRecord }) {
  const b = record.build ?? {}
  const pins = b.pins ?? {}
  const prompts = Object.entries(pins.prompts ?? {})
  const tools = Object.entries(pins.tool_schemas ?? {})
  const anything = prompts.length || tools.length || pins.model || pins.corpus_digest || pins.assembler || pins.niadra?.compiler || pins.niadra?.pack_hash || b.sdk || b.adapter
  if (!anything) return <Empty title="Nenhum pino gravado neste turno.">Sem os pinos, o turno fica registrado mas não pode ser reexecutado.</Empty>
  const none = <span className="text-ink2">não gravado</span>
  const hash = (h: string | null | undefined) => (h ? <span className={`${mono} break-all`} title={h}>{h}</span> : none)
  return (
    <div className={`${card} grid gap-5 p-4 sm:p-5`}>
      <Props
        items={[
          ['Modelo', pins.model ? <span className={mono}>{pins.model}</span> : none],
          ['Montador de contexto da empresa', pins.assembler ? <span className={mono}>{pins.assembler}</span> : none],
          ['Digest do corpus', hash(pins.corpus_digest)],
          ['Compilador da Niadra', pins.niadra?.compiler ? <span className={mono}>{pins.niadra.compiler}</span> : none],
          ['Hash do contexto lido', hash(pins.niadra?.pack_hash)],
          ['SDK', b.sdk ? <span className={mono}>{b.sdk}</span> : none],
          ['Adaptador', b.adapter ? <span className={mono}>{b.adapter}</span> : none],
        ]}
      />
      <div className="grid gap-5 lg:grid-cols-2">
        <PairTable caption="Prompts e versões" head={['Prompt', 'Versão']} rows={prompts} empty="Nenhum prompt gravado." />
        <PairTable caption="Esquemas das ferramentas" head={['Ferramenta', 'Hash do esquema']} rows={tools} empty="Nenhum esquema de ferramenta gravado." hashes />
      </div>
    </div>
  )
}

function PairTable({ caption, head, rows, empty, hashes = false }: { caption: string; head: [string, string]; rows: [string, string][]; empty: string; hashes?: boolean }) {
  return (
    <div className="min-w-0">
      <p className="mb-1.5 text-[0.75rem] font-semibold text-ink2">{caption}</p>
      {rows.length === 0 ? (
        <p className={small}>{empty}</p>
      ) : (
        <div className="overflow-x-auto rounded-lg border border-line">
          <table className="w-full border-collapse">
            <caption className="sr-only">{caption}</caption>
            <thead>
              <tr>
                <th scope="col" className={th}>{head[0]}</th>
                <th scope="col" className={th}>{head[1]}</th>
              </tr>
            </thead>
            <tbody>
              {rows.map(([k, v]) => (
                <tr key={k}>
                  <td className={`${td} ${mono}`}>{k}</td>
                  <td className={`${td} ${mono} ${hashes ? 'break-all' : ''}`} title={v}>
                    {hashes ? shortHash(v, 24) : v}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}

/* Blobs: the value, where it lives, or only its digest. */

function BlobValue({ title, blobKey, record }: { title: string; blobKey: string | null | undefined; record: TurnRecord }) {
  if (!blobKey) return null
  const blob: TurnBlob | undefined = record.blobs?.[blobKey]
  const head = (
    <p className="text-[0.75rem] font-semibold text-ink2">
      {title} <span className={`${mono} font-normal`}>({blobKey})</span>
    </p>
  )
  if (!blob) {
    return (
      <div className="min-w-0">
        {head}
        <p className={`${small} mt-0.5`}>O valor não chegou: o SDK o largou para proteger a fila de envio.</p>
      </div>
    )
  }
  const digest = (
    <span className={`${mono} break-all text-ink2`} title={blob.sha256}>
      {shortHash(blob.sha256, 23)}
      {typeof blob.size === 'number' ? ` · ${fmtBytes(blob.size)}` : ''}
    </span>
  )
  if (record.content_mode === 'pointer' || blob.pointer) {
    return (
      <div className="min-w-0">
        {head}
        <p className={`${small} mt-0.5`}>Fica no armazenamento da empresa, fora da Niadra. O Console não busca o valor.</p>
        <p className={`${mono} mt-1 break-all text-ink`}>{blob.pointer ?? 'sem endereço'}</p>
        <p className="mt-0.5">{digest}</p>
      </div>
    )
  }
  if (record.content_mode === 'hash_only') {
    return (
      <div className="min-w-0">
        {head}
        <p className={`${small} mt-0.5`}>Só o hash foi gravado; o valor não fica na Niadra.</p>
        <p className="mt-0.5">{digest}</p>
      </div>
    )
  }
  return (
    <details className="group min-w-0">
      <summary className="flex min-h-11 cursor-pointer list-none flex-wrap items-center gap-x-2 gap-y-0.5 rounded-md lg:min-h-8 [&::-webkit-details-marker]:hidden">
        <Icon name="chevron" size={14} className="shrink-0 text-ink2 transition-transform group-open:rotate-90 motion-reduce:transition-none" />
        <span className="text-[0.75rem] font-semibold text-ink">
          {title} <span className={`${mono} font-normal text-ink2`}>({blobKey})</span>
        </span>
        {digest}
      </summary>
      <div className="mt-2">
        <JsonView value={blob.content} label={title} maxHeight="max-h-80" />
      </div>
    </details>
  )
}

/* Reads. */

function Reads({ record }: { record: TurnRecord }) {
  const { space } = useSpace()
  const reads = record.reads ?? []
  if (reads.length === 0) return <Empty title="O turno não leu nada da Niadra." />
  return (
    <ul className={`${card} divide-y divide-line2`}>
      {reads.map((read, i) => (
        <li key={i} className="grid gap-2 px-4 py-3">
          <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
            <span className="font-semibold text-ink">{SURFACE[read.surface] ?? read.surface}</span>
            <span className={`${mono} break-all text-ink2`}>{read.etag ? `ETag ${read.etag}` : read.version ? `versão ${read.version}` : 'sem versão'}</span>
            {read.receipt_id && (
              <Link className={`${link} text-[0.8125rem]`} to={paths.receipt(space.space_id, read.receipt_id)}>
                Ver o comprovante
              </Link>
            )}
          </div>
          <BlobValue title="O que foi lido" blobKey={read.blob} record={record} />
        </li>
      ))}
    </ul>
  )
}

/* Calls, nested by `parent_call_id`. */

function Calls({ record, anchors }: { record: TurnRecord; anchors: Map<string, string> }) {
  const calls = record.calls ?? []
  if (calls.length === 0) return <Empty title="O turno não chamou ferramenta nem modelo." />
  const known = new Set(calls.map((c) => c.call_id))
  const children = new Map<string, TurnCall[]>()
  for (const c of calls) if (c.parent_call_id && known.has(c.parent_call_id)) children.set(c.parent_call_id, [...(children.get(c.parent_call_id) ?? []), c])
  const roots = calls.filter((c) => !c.parent_call_id || !known.has(c.parent_call_id))
  const tree = (list: TurnCall[], depth: number): ReactNode => (
    <ol className={depth === 0 ? 'grid gap-3' : 'mt-3 grid gap-3 border-l-2 border-line pl-3 sm:pl-4'}>
      {list.map((c) => (
        <li key={c.call_id} className="min-w-0">
          <CallCard call={c} record={record} anchor={anchors.get(c.call_id) ?? ''} />
          {children.has(c.call_id) && tree(children.get(c.call_id)!, depth + 1)}
        </li>
      ))}
    </ol>
  )
  return <>{tree(roots, 0)}</>
}

function CallCard({ call: c, record, anchor }: { call: TurnCall; record: TurnRecord; anchor: string }) {
  const facts: string[] = []
  if (typeof c.latency_ms === 'number') facts.push(fmtMs(c.latency_ms))
  if (c.tokens) facts.push(`${fmtInt(c.tokens.in)} tokens de entrada, ${fmtInt(c.tokens.cached ?? 0)} em cache, ${fmtInt(c.tokens.out)} de saída`)
  for (const [unit, n] of Object.entries(c.cost_units ?? {})) facts.push(`${fmtInt(n)} ${unit}`)
  const bad = c.status && c.status !== 'ok'
  return (
    <article id={anchor} className={`${card} scroll-mt-20 p-4`} aria-label={`Chamada ${c.name ?? c.call_id}`}>
      <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
        <Chip tone="dark">{c.kind === 'tool' ? 'Ferramenta' : 'Modelo'}</Chip>
        <span className="font-semibold text-ink">{c.name ?? 'sem nome'}</span>
        <span className={`${mono} break-all text-ink2`}>{c.call_id}</span>
        {bad ? <Chip tone="danger">{CALL_STATUS[c.status!] ?? c.status}</Chip> : <Chip tone="ok">ok</Chip>}
        {(c.attempt ?? 1) > 1 && <Chip>tentativa {c.attempt}</Chip>}
        {c.synthetic && <Chip>respondida pelo SDK, sem a ferramenta</Chip>}
        {c.cache_hit && <Chip>resposta do cache da ferramenta</Chip>}
        {c.truncated && <Chip>chegou cortada</Chip>}
      </div>
      {facts.length > 0 && <p className={`${small} mt-1`}>{facts.join(' · ')}</p>}
      {(c.effect_key || c.args_hash) && (
        <p className={`${mono} mt-1 break-all text-ink2`}>
          {c.effect_key ? `chave de efeito ${c.effect_key}` : ''}
          {c.effect_key && c.args_hash ? ' · ' : ''}
          {c.args_hash ? `hash dos argumentos ${shortHash(c.args_hash, 23)}` : ''}
        </p>
      )}
      <div className="mt-3 grid gap-3">
        <BlobValue title="Argumentos" blobKey={c.args} record={record} />
        <BlobValue title="Resultado que o modelo viu" blobKey={c.result_model} record={record} />
        <BlobValue title="Resultado que a interface recebeu" blobKey={c.result_ui} record={record} />
      </div>
      {(c.observations ?? []).length > 0 && (
        <div className="mt-4">
          <p className="text-[0.75rem] font-semibold text-ink2">O que a chamada mostrou do estado</p>
          <ul className="mt-1.5 grid gap-2">
            {c.observations!.map((o, i) => (
              <li key={i} className="rounded-lg border border-line2 bg-bg px-3 py-2">
                <p className={`${mono} break-all text-ink`}>{o.ref}</p>
                <p className={`${mono} mt-0.5 break-all text-ink2`}>
                  {Object.entries(o.fields)
                    .map(([k, v]) => `${k}: ${scalar(v)}`)
                    .join(' · ')}
                </p>
                <p className={`${small} mt-0.5`}>
                  {o.provenance
                    ? `Fonte ${o.provenance.source === 'live' ? 'ao vivo' : o.provenance.source === 'snapshot' ? 'por instantâneo' : 'em cache'}, observada em ${fmtInstant(o.provenance.source_observed_at)}${
                        o.provenance.scope && o.provenance.scope !== 'global' ? `, escopo ${o.provenance.scope === 'customer' ? 'do cliente' : 'do contexto'}: não muda estado compartilhado` : ''
                      }.`
                    : 'Sem procedência: serve só para exibir e não muda o estado.'}
                </p>
              </li>
            ))}
          </ul>
        </div>
      )}
      {c.applied && (
        <div className="mt-4">
          <p className="text-[0.75rem] font-semibold text-ink2">
            Restrições aplicadas <span className={`${mono} font-normal`}>({c.applied.constraints})</span>
          </p>
          <p className={`${small} mt-0.5`}>
            {plural((c.applied.hard_sent ?? []).length, 'restrição dura enviada', 'restrições duras enviadas')}, {plural(c.applied.results_checked, 'resultado conferido', 'resultados conferidos')},{' '}
            {plural(c.applied.violations, 'violação', 'violações')} e {fmtInt(c.applied.unverifiable)} sem como conferir.
            {c.applied.relaxed === 'declared' && ' A ferramenta declarou que relaxou uma restrição.'}
            {c.applied.relaxed === 'suspected' && ' Há sinal de que a ferramenta relaxou uma restrição.'}
          </p>
          {(c.applied.hard_sent ?? []).length > 0 && <p className={`${mono} mt-0.5 break-all text-ink2`}>{c.applied.hard_sent!.join(' · ')}</p>}
        </div>
      )}
    </article>
  )
}

/* Interactions. */

function describe(i: Interaction): string {
  switch (i.kind) {
    case 'presented':
      return `Lista ${i.list_id}${i.list_kind && i.list_kind !== 'results' ? ` (${i.list_kind})` : ''}, página ${i.page ?? 1}, com ${fmtInt(i.items.length)} ${i.items.length === 1 ? 'item' : 'itens'}${
        i.visible_k ? `, ${fmtInt(i.visible_k)} visíveis na entrega` : ''
      }. Entregue em ${fmtInstant(i.delivered_at)}, ${METHOD[i.method ?? 'bridge']}.`
    case 'seen':
      return `Viu até a posição ${fmtInt(i.max_index_seen)} da lista entregue.`
    case 'engaged':
      return `${HOW[i.how] ?? i.how} ${i.ref}.`
    case 'feedback':
      return `Retorno ${i.polarity === 'positive' ? 'positivo' : 'negativo'} sobre ${i.ref}${i.reason_attrs?.length ? `, por ${i.reason_attrs.join(', ')}` : ''}.`
    case 'preference':
      return `${STRENGTH[i.strength] ?? i.strength} ${i.attr} ${OP[i.op] ?? i.op} ${i.values.map(scalar).join(', ')}, ${PREF_SCOPE[i.scope ?? 'session']}${i.source === 'tool_args' ? ', tirado dos argumentos de uma ferramenta' : ''}.`
    case 'attribute':
      return `${i.name}: ${scalar(i.value)}${i.system ? ` (${i.system})` : ''}.`
    case 'watch':
      return `Pediu aviso sobre ${i.ref} quando ${i.condition}.`
    case 'unwatch':
      return `Cancelou o aviso sobre ${i.ref}.`
  }
}

const INTERACTION: Record<Interaction['kind'], string> = {
  presented: 'Lista mostrada',
  seen: 'Visto',
  engaged: 'Escolha',
  feedback: 'Retorno',
  preference: 'Preferência',
  attribute: 'Atributo',
  watch: 'Pedido de aviso',
  unwatch: 'Fim do aviso',
}

function Interactions({ items }: { items: Interaction[] }) {
  if (items.length === 0) return <Empty title="O turno não mostrou lista nem registrou interação." />
  return (
    <ul className={`${card} divide-y divide-line2`}>
      {items.map((i, n) => (
        <li key={n} className="px-4 py-3">
          <div className="flex flex-wrap items-baseline gap-2">
            <Chip>{INTERACTION[i.kind] ?? i.kind}</Chip>
            <span className="min-w-0 break-words text-ink">{describe(i)}</span>
            {i.for && i.for !== 'self' && <span className={`${mono} text-ink2`}>para {i.for === 'gift' ? 'presente' : i.for}</span>}
          </div>
          {i.kind === 'presented' && (
            <div className="mt-2 overflow-x-auto rounded-lg border border-line">
              <table className="w-full min-w-[32rem] border-collapse">
                <caption className="sr-only">Itens na ordem em que foram mostrados</caption>
                <thead>
                  <tr>
                    <th scope="col" className={`${th} text-right`}>Posição</th>
                    <th scope="col" className={th}>Objeto</th>
                    <th scope="col" className={th}>Valores mostrados</th>
                  </tr>
                </thead>
                <tbody>
                  {i.items.map((it) => (
                    <tr key={it.pos}>
                      <td className={`${td} text-right tabular-nums`}>{it.pos}</td>
                      <td className={`${td} ${mono} break-all`}>{it.ref}</td>
                      <td className={`${td} ${mono} break-all text-ink2`}>
                        {Object.entries(it.shown ?? {})
                          .map(([k, v]) => `${k}: ${scalar(v)}`)
                          .join(' · ') || 'nenhum'}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </li>
      ))}
    </ul>
  )
}

/* Claims. */

function claimValue(v: ClaimValue | null | undefined): string {
  if (!v) return 'sem valor lido'
  const unit = v.unit ? ` ${v.unit}` : ''
  if (v.amount != null) return `${v.amount}${unit}`
  if (v.min != null && v.max != null) return `de ${v.min} a ${v.max}${unit}`
  if (v.date) return v.date
  if (v.date_from && v.date_to) return `de ${v.date_from} a ${v.date_to}`
  return 'sem valor lido'
}

function Claims({ items, anchors, calls }: { items: ClaimRecord[]; anchors: Map<string, string>; calls: TurnCall[] }) {
  if (items.length === 0) return <Empty title="Nenhuma afirmação encontrada neste turno." />
  const byId = new Map(calls.map((c) => [c.call_id, c]))
  return (
    <div className={`${card} overflow-x-auto`}>
      <table className="w-full min-w-[56rem] border-collapse">
        <caption className="sr-only">Afirmações e vereditos</caption>
        <thead>
          <tr>
            <th scope="col" className={th}>Categoria e papel</th>
            <th scope="col" className={th}>Valor</th>
            <th scope="col" className={th}>Natureza</th>
            <th scope="col" className={th}>Veredito</th>
            <th scope="col" className={th}>Ação</th>
            <th scope="col" className={th}>Evidência</th>
          </tr>
        </thead>
        <tbody>
          {items.map((c, n) => {
            const ev = c.evidence
            const call = ev?.call_id ? byId.get(ev.call_id) : undefined
            return (
              <tr key={n}>
                <td className={td}>
                  <span className="font-semibold text-ink" title={CLAIM_CATEGORY[c.category] ? c.category : undefined}>
                    {CLAIM_CATEGORY[c.category] ?? c.category}
                  </span>
                  {c.role && <span className={`${mono} block text-ink2`}>{c.role}</span>}
                  <span className={`${small} block`}>
                    posições {c.span[0]} a {c.span[1]}
                  </span>
                </td>
                <td className={`${td} ${mono}`}>
                  {claimValue(c.value)}
                  {c.class && <span className={`${small} block font-sans`}>{CLAIM_CLASS[c.class] ?? c.class}</span>}
                </td>
                <td className={td}>{c.nature ? NATURE[c.nature] ?? c.nature : 'não informada'}</td>
                <td className={td}>
                  <Chip tone={VERDICT_OK.has(c.verdict) ? 'ok' : VERDICT_BAD.has(c.verdict) ? 'danger' : 'neutral'}>{VERDICT[c.verdict] ?? c.verdict}</Chip>
                </td>
                <td className={td}>{CLAIM_ACTION[c.action] ?? c.action}</td>
                <td className={td}>
                  {!ev ? (
                    <span className="text-ink2">nenhuma</span>
                  ) : (
                    <span className="grid gap-0.5">
                      {ev.call_id &&
                        (call ? (
                          <a className={`${link} text-[0.8125rem]`} href={`#${anchors.get(ev.call_id)}`}>
                            Chamada {call.name ?? ev.call_id}
                            {ev.field ? `, campo ${ev.field}` : ''}
                          </a>
                        ) : (
                          <span className={mono}>chamada {ev.call_id} fora do registro</span>
                        ))}
                      {ev.ref && (
                        <span className={`${mono} break-all`}>
                          {ev.ref}
                          {ev.field && !ev.call_id ? `, campo ${ev.field}` : ''}
                        </span>
                      )}
                      {(ev.document || ev.anchor) && (
                        <span className={`${mono} break-all text-ink2`}>
                          {ev.document ? `documento ${ev.document}` : ''}
                          {ev.document && ev.anchor ? ' · ' : ''}
                          {ev.anchor ? `âncora ${ev.anchor}` : ''}
                          {typeof ev.score === 'number' ? ` · semelhança ${oneDecimal.format(ev.score * 100)}%` : ''}
                        </span>
                      )}
                    </span>
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

/* Decisions, effects and output. */

function Decisions({ record }: { record: TurnRecord }) {
  const coordination = record.coordination ?? []
  const effects = record.effects ?? []
  const out = record.output ?? {}
  const events = out.event_keys ?? []
  return (
    <div className="grid gap-4 lg:grid-cols-2">
      <div className={`${card} p-4`}>
        <p className="text-[0.75rem] font-semibold text-ink2">Decisões de coordenação</p>
        {coordination.length === 0 ? (
          <p className={`${small} mt-1`}>O turno não se apoiou em decisão de coordenação.</p>
        ) : (
          <ul className="mt-1.5 grid gap-1.5">
            {coordination.map((d) => (
              <li key={d.decision_id} className="flex flex-wrap items-baseline gap-2">
                <Chip tone={d.decision === 'deny' ? 'danger' : 'neutral'}>{DECISION[d.decision] ?? d.decision}</Chip>
                <span className={`${mono} break-all text-ink2`}>{d.decision_id}</span>
              </li>
            ))}
          </ul>
        )}
        <p className="mt-4 text-[0.75rem] font-semibold text-ink2">Efeitos externos</p>
        {effects.length === 0 ? (
          <p className={`${small} mt-1`}>Nenhum efeito externo.</p>
        ) : (
          <ul className="mt-1.5 grid gap-1.5">
            {effects.map((e) => (
              <li key={e.key} className="flex flex-wrap items-baseline gap-2">
                <Chip tone={e.state === 'failed' || e.state === 'unknown_outcome' ? 'danger' : e.state === 'done' ? 'ok' : 'neutral'}>{EFFECT[e.state] ?? e.state}</Chip>
                <span className={`${mono} break-all text-ink`}>{e.key}</span>
              </li>
            ))}
          </ul>
        )}
      </div>
      <div className={`${card} p-4`}>
        <p className="text-[0.75rem] font-semibold text-ink2">O que o turno emitiu</p>
        <p className={`${small} mt-1`}>
          {events.length === 0
            ? 'Nenhum evento de conversa enviado pelo turno.'
            : `${fmtInt(events.length)} ${events.length === 1 ? 'evento enviado' : 'eventos enviados'}. O texto da conversa não se repete no registro: estas são as chaves dos eventos.`}
        </p>
        {events.length > 0 && <p className={`${mono} mt-1 break-all text-ink`}>{events.join(' · ')}</p>}
        <div className="mt-3 grid gap-3">
          <BlobValue title="O que a interface recebeu" blobKey={out.ui} record={record} />
          <BlobValue title="Documento escrito" blobKey={out.document} record={record} />
        </div>
        {out.handoff_id && (
          <p className={`${small} mt-3`}>
            Transbordo <span className={`${mono} break-all text-ink`}>{out.handoff_id}</span>
          </p>
        )}
      </div>
    </div>
  )
}
