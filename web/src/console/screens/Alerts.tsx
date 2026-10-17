import { Fragment, useState, type FormEvent } from 'react'
import { useTab } from '../lib/tabs'
import { Link, useSearchParams } from 'react-router-dom'
import type { DeliveryStatus, DeliveryView, DiffOut, FiringView, TriggerCondition, TriggerDryRunResponse, TriggerRuleDoc, WebhookEndpointDoc } from '../api/types'
import { TRIGGER_CONDITIONS } from '../api/types'
import { fmtDateTime, fmtInt } from '../lib/format'
import { isSiemStream } from '../lib/endpoints'
import { DELIVERY_STATUSES, deliveryError, deliveryEvent, deliveryStatus, itemKind, targetProblem, traitName, triggerCondition } from '../lib/labels'
import { usePaged, useResource } from '../lib/useResource'
import { paths } from '../paths'
import { useSpace } from '../session'
import { Chip, JsonView, PageHeader, SourceName, useSources, type SourcesDirectory } from '../ui/bits'
import { Field, Section, Select, TextInput, useAction } from '../ui/forms'
import { useTitle } from '../ui/layout'
import { fmtNumber, NumberField, numberProblem } from '../ui/NumberField'
import { DiffReason, diffReasonOk, DiffSent, freeId, nextVersion, ToggleRule, useCanPropose } from '../ui/proposal'
import { Empty, ErrorState, LoadMore, Loading } from '../ui/States'
import { Tabs } from '../ui/Tabs'
import { btnPrimary, btnQuiet, btnSecondary, card, danger, hint, input, mono, small, td, th } from '../ui/styles'

type TabKey = 'firings' | 'deliveries' | 'editor' | 'rules'
const TAB_KEYS: TabKey[] = ['firings', 'deliveries', 'rules', 'editor']

/**
 * Triggers and webhook delivery. The Niadra warns by
 * webhook when a tenant rule happens in the memory; the tenant's agent or
 * system acts. Nothing here runs an action. Rules are built from the condition
 * catalog, tested and sent for approval here, never written as JSON.
 */
export function Alerts() {
  const { space } = useSpace()
  useTitle('Avisos', space.environment === 'sandbox')
  // `?aba=deliveries&endpoint=<id>` opens the deliveries of one endpoint: Systems links here from each SIEM stream.
  const [params] = useSearchParams()
  const [tab, setTab] = useTab<TabKey>(TAB_KEYS, 'firings')
  const endpointFilter = params.get('endpoint') ?? ''
  // What the editor starts from: a new rule, or a rule in use to adjust. The key starts it over.
  const [start, setStart] = useState<{ key: number; rule: TriggerRuleDoc | null }>({ key: 0, rule: null })
  const edit = (rule: TriggerRuleDoc | null) => {
    setStart((cur) => ({ key: cur.key + 1, rule }))
    setTab('editor')
  }
  return (
    <>
      <PageHeader
        screen="alerts"
      />
      <div data-tour="alerts.tabs">
      <Tabs
        label="Avisos"
        active={tab}
        onChange={setTab}
        tabs={[
          { key: 'firings', label: 'Disparos' },
          { key: 'deliveries', label: 'Entregas de webhook' },
          { key: 'rules', label: 'Regras em uso' },
          { key: 'editor', label: 'Criar e testar' },
        ]}
      >
        <div data-tour={`alerts.${tab}`}>
          {tab === 'firings' && <Firings />}
          {tab === 'deliveries' && <Deliveries initialEndpoint={endpointFilter} />}
          {tab === 'rules' && <Rules onEdit={edit} />}
          {tab === 'editor' && <RuleEditor key={start.key} start={start.rule} onNew={() => edit(null)} />}
        </div>
      </Tabs>
      </div>
    </>
  )
}

/**
 * The subject of a firing is a profile, except for `source.silent`, whose subject is the source that went
 * quiet (domain/triggers/firing.py); its evidence points at that same source.
 */
const firedForSource = (f: FiringView, sources: SourcesDirectory) => f.evidence.some((e) => e.kind === 'source' && e.id === f.subject_id) || sources.byId.has(f.subject_id)

function Firings() {
  const { cell, space } = useSpace()
  const sources = useSources()
  const [rule, setRule] = useState('')
  const [subject, setSubject] = useState('')
  const [applied, setApplied] = useState<{ rule_id?: string; subject_id?: string }>({})
  const page = usePaged((cursor, signal) => cell.firings({ ...applied, cursor }, { signal }), [applied.rule_id, applied.subject_id, cell])
  return (
    <div className="grid gap-5">
      <form
        className={`${card} flex flex-wrap items-end gap-3 p-4`}
        aria-label="Filtrar disparos"
        onSubmit={(e) => {
          e.preventDefault()
          setApplied({ rule_id: rule.trim() || undefined, subject_id: subject.trim() || undefined })
        }}
      >
        <div className="min-w-[12rem] flex-1">
          <TextInput label="ID da regra" value={rule} onChange={setRule} mono />
        </div>
        <div className="min-w-[14rem] flex-1">
          <TextInput label="ID do perfil ou da fonte" value={subject} onChange={setSubject} mono />
        </div>
        <button type="submit" className={btnSecondary}>
          Filtrar
        </button>
      </form>
      {(page.status === 'loading' || page.status === 'idle') && <Loading label="Carregando os disparos…" />}
      {page.status === 'error' && <ErrorState error={page.error} onRetry={page.reload} />}
      {page.status === 'success' && page.items.length === 0 && <Empty title="Nenhum disparo com esses filtros.">
          Um disparo nasce quando a condição de uma regra ligada acontece na memória, e aparece aqui assim que acontece. Exemplo do que você verá:{' '}
          <span className="text-ink">Promessa vencida · regra promessa-vencida v2 · cliente p_… · 30/09/2026 06:00 · entregue ao endpoint da loja</span>.
        </Empty>}
      {page.status === 'success' && page.items.length > 0 && (
        <>
          <div className={`${card} overflow-x-auto`}>
            <table className="w-full min-w-[52rem] border-collapse">
              <caption className="sr-only">Disparos de avisos</caption>
              <thead>
                <tr>
                  <th scope="col" className={th}>Quando</th>
                  <th scope="col" className={th}>Regra</th>
                  <th scope="col" className={th}>Perfil ou fonte</th>
                  <th scope="col" className={th}>Janela</th>
                  <th scope="col" className={th}>Evidências</th>
                  <th scope="col" className={th}>Estado</th>
                </tr>
              </thead>
              <tbody>
                {page.items.map((f) => (
                  <tr key={f.firing_id}>
                    <td className={`${td} ${mono} whitespace-nowrap`}>{fmtDateTime(f.fired_at)}</td>
                    <td className={`${td} ${mono}`}>
                      {f.rule_id}
                      <span className="block text-ink2">versão {f.rule_version}</span>
                    </td>
                    <td className={td}>
                      {firedForSource(f, sources) ? (
                        <>
                          <Link to={paths.agent(space.space_id, f.subject_id)} className="text-ink underline decoration-accent decoration-2 underline-offset-4">
                            <SourceName id={f.subject_id} />
                          </Link>
                          <span className="block text-[0.75rem] text-ink2">fonte</span>
                        </>
                      ) : (
                        <Link to={paths.profile(space.space_id, f.subject_id)} className="font-mono text-ink underline decoration-accent decoration-2 underline-offset-4">
                          {f.subject_id}
                        </Link>
                      )}
                    </td>
                    <td className={`${td} ${mono} text-ink2`}>{f.window_key}</td>
                    <td className={`${td} text-[0.8125rem]`}>
                      {f.evidence.length === 0
                        ? 'nenhuma'
                        : f.evidence.map((e, i) => (
                            <Fragment key={`${e.kind}:${e.id}`}>
                              {i > 0 && ', '}
                              {e.kind === 'source' ? (
                                <>
                                  Fonte <SourceName id={e.id} />
                                </>
                              ) : (
                                `${itemKind(e.kind)} ${e.id}`
                              )}
                            </Fragment>
                          ))}
                    </td>
                    <td className={td}>
                      {f.retracted_at ? <Chip tone="danger">Retirado {fmtDateTime(f.retracted_at)}</Chip> : f.late ? <Chip>Entregue com atraso</Chip> : <Chip tone="ok">Disparado</Chip>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <LoadMore hasMore={page.hasMore} loading={page.loadingMore} error={page.moreError} onClick={page.loadMore} />
        </>
      )}
    </div>
  )
}

const DELIVERY_TONE: Record<DeliveryStatus, 'ok' | 'neutral' | 'danger'> = { delivered: 'ok', pending: 'neutral', failing: 'danger', dead: 'danger' }

function Deliveries({ initialEndpoint }: { initialEndpoint: string }) {
  const { cell, hasRole } = useSpace()
  const [status, setStatus] = useState<'' | DeliveryStatus>('')
  const [endpoint, setEndpoint] = useState(initialEndpoint)
  const [applied, setApplied] = useState<{ status?: DeliveryStatus; endpoint_id?: string }>(initialEndpoint ? { endpoint_id: initialEndpoint } : {})
  const page = usePaged((cursor, signal) => cell.deliveries({ ...applied, cursor }, { signal }), [applied.status, applied.endpoint_id, cell])
  const [redelivered, setRedelivered] = useState<Record<string, DeliveryView | { error: unknown }>>({})
  const canRedeliver = hasRole('integration')

  const redeliver = async (d: DeliveryView) => {
    try {
      const v = await cell.redeliver(d.delivery_id)
      setRedelivered((m) => ({ ...m, [d.delivery_id]: v }))
    } catch (error) {
      setRedelivered((m) => ({ ...m, [d.delivery_id]: { error } }))
    }
  }

  return (
    <div className="grid gap-5">
      <form
        className={`${card} flex flex-wrap items-end gap-3 p-4`}
        aria-label="Filtrar entregas"
        onSubmit={(e) => {
          e.preventDefault()
          setApplied({ status: status || undefined, endpoint_id: endpoint.trim() || undefined })
        }}
      >
        <div className="w-full sm:w-56">
          <Select label="Estado" value={status} onChange={setStatus} options={[{ value: '', label: 'Todos' }, ...DELIVERY_STATUSES.map((s) => ({ value: s, label: deliveryStatus(s) }))]} />
        </div>
        <div className="min-w-[12rem] flex-1">
          <TextInput label="ID do endpoint" value={endpoint} onChange={setEndpoint} mono />
        </div>
        <button type="submit" className={btnSecondary}>
          Filtrar
        </button>
      </form>
      <p className={small}>O corpo dos avisos nunca aparece aqui: cada entrega mostra só o tipo, as tentativas e a última resposta do endpoint. Depois de 24 horas de falha, a entrega esgota as tentativas e pode ser reenviada à mão.</p>
      {(page.status === 'loading' || page.status === 'idle') && <Loading label="Carregando as entregas…" />}
      {page.status === 'error' && <ErrorState error={page.error} onRetry={page.reload} />}
      {page.status === 'success' && page.items.length === 0 && (
        <Empty
          title={applied.status || applied.endpoint_id ? 'Nenhuma entrega com esses filtros.' : 'Nenhuma entrega de webhook ainda.'}
          example={
            <>
              01/10/2026 06:00 · Uma regra de aviso disparou · <span className={mono}>erp-avisos</span> · Entregue · 1 tentativa · HTTP 200
            </>
          }
        >
          {applied.status || applied.endpoint_id
            ? 'Tire um dos filtros para ver as outras entregas. Uma entrega Falhando ou que esgotou as tentativas pode ser reenviada à mão.'
            : 'Uma entrega nasce quando um evento que um endpoint assina acontece: uma regra de aviso dispara, um agente registra uma ação, uma pendência fecha. O caminho é cadastrar um endpoint em Sistemas, na fonte que o recebe, com os tipos de evento que ele assina, e testar o endereço de lá.'}
        </Empty>
      )}
      {page.status === 'success' && page.items.length > 0 && <FailingNote items={page.items} />}
      {page.status === 'success' && page.items.length > 0 && (
        <>
          <div className={`${card} overflow-x-auto`}>
            <table className="w-full min-w-[58rem] border-collapse">
              <caption className="sr-only">Entregas de webhook</caption>
              <thead>
                <tr>
                  <th scope="col" className={th}>Criada</th>
                  <th scope="col" className={th}>Tipo</th>
                  <th scope="col" className={th}>Endpoint</th>
                  <th scope="col" className={th}>Estado</th>
                  <th scope="col" className={`${th} text-right`}>Tentativas</th>
                  <th scope="col" className={th}>Última resposta</th>
                  {canRedeliver && <th scope="col" className={`${th} text-right`}>Ação</th>}
                </tr>
              </thead>
              <tbody>
                {page.items.map((d) => {
                  const again = redelivered[d.delivery_id]
                  const event = deliveryEvent(d.event_type)
                  return (
                    <tr key={d.delivery_id}>
                      <td className={`${td} ${mono} whitespace-nowrap`}>{fmtDateTime(d.created_at)}</td>
                      <td className={`${td} text-[0.8125rem]`}>
                        {event}
                        <span className={`${mono} ${event ? 'block text-ink2' : ''}`}>{d.event_type}</span>
                      </td>
                      <td className={`${td} ${mono}`}>{d.endpoint_id}</td>
                      <td className={td}>
                        <Chip tone={DELIVERY_TONE[d.status] ?? 'neutral'}>{deliveryStatus(d.status)}</Chip>
                        {d.delivered_at && <span className="block text-[0.75rem] text-ink2">{fmtDateTime(d.delivered_at)}</span>}
                        {d.next_attempt_at && d.status !== 'delivered' && <span className="block text-[0.75rem] text-ink2">próxima {fmtDateTime(d.next_attempt_at)}</span>}
                      </td>
                      <td className={`${td} text-right tabular-nums`}>{fmtInt(d.attempts)}</td>
                      <td className={`${td} text-[0.8125rem]`}>
                        {d.last_status_code ? <span className={mono}>HTTP {d.last_status_code}</span> : 'sem resposta'}
                        {d.last_error && (
                          <span className="block text-ink2">
                            {deliveryError(d.last_error)} <span className={mono}>({d.last_error})</span>
                          </span>
                        )}
                      </td>
                      {canRedeliver && (
                        <td className={`${td} text-right`}>
                          {again && 'delivery_id' in again ? (
                            <span className="text-[0.8125rem] font-semibold text-ink">Na fila de novo</span>
                          ) : again && 'error' in again ? (
                            <span className={`text-[0.8125rem] font-semibold ${danger.text}`}>O reenvio falhou</span>
                          ) : d.status === 'failing' || d.status === 'dead' ? (
                            <button type="button" className={btnQuiet} onClick={() => redeliver(d)} aria-label={`Reenviar a entrega ${d.delivery_id}`}>
                              Reenviar
                            </button>
                          ) : null}
                        </td>
                      )}
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
          <LoadMore hasMore={page.hasMore} loading={page.loadingMore} error={page.moreError} onClick={page.loadMore} />
        </>
      )}
    </div>
  )
}

/**
 * When every delivery on the page failed or ran out of attempts, the page says so once, in words, with the
 * endpoints and what each last error means, and the one thing to do: fix the endpoint in Systems, then resend.
 */
function FailingNote({ items }: { items: DeliveryView[] }) {
  const { space } = useSpace()
  const bad = items.filter((d) => d.status === 'failing' || d.status === 'dead')
  if (bad.length === 0 || bad.length < items.length) return null
  const endpoints = [...new Set(bad.map((d) => d.endpoint_id))]
  const errors = [...new Set(bad.map((d) => d.last_error).filter((e): e is string => Boolean(e)))]
  return (
    <div role="status" className={`${card} px-4 py-3`}>
      <p className="text-[0.95rem] font-semibold text-ink">
        {bad.length === items.length && items.length === 1 ? 'A entrega desta página falhou.' : `Nenhuma das ${fmtInt(items.length)} entregas desta página chegou.`}
      </p>
      <p className={`${small} mt-1 max-w-[72ch]`}>
        {endpoints.length === 1 ? (
          <>
            O endpoint <span className={mono}>{endpoints[0]}</span> não recebeu nenhuma delas
          </>
        ) : (
          <>Os endpoints {endpoints.map((e, i) => <span key={e}>{i > 0 ? ', ' : ''}<span className={mono}>{e}</span></span>)} não receberam nenhuma delas</>
        )}
        {errors.length > 0 ? ': ' : '.'}
        {errors.map((e, i) => (
          <span key={e}>
            {i > 0 ? '; ' : ''}
            {deliveryError(e)} <span className={mono}>({e})</span>
          </span>
        ))}
        {errors.length > 0 ? '.' : ''} A Niadra tenta de novo com intervalos crescentes por 24 horas e então esgota as tentativas. Confira o endereço e o estado do endpoint em Sistemas, teste o endereço de lá e, com ele respondendo, reenvie as entregas aqui.
      </p>
      <Link to={paths.systems(space.space_id)} className={`${btnSecondary} mt-3`}>
        Conferir o endpoint em Sistemas
      </Link>
    </div>
  )
}

const RULE_ID = /^[a-z][a-z0-9_.:-]{0,63}$/

/* The condition catalog: what each condition reads in `params`, with the server's defaults (domain/triggers/conditions.py). */

/** The owner choice that sends `null`: the condition then reads the items of any owner. */
const ANY_OWNER = 'any'
const OWNERS = [
  { value: 'company', label: 'Da empresa' },
  { value: 'customer', label: 'Do cliente' },
  { value: ANY_OWNER, label: 'De qualquer um' },
]

type ParamSpec =
  | { kind: 'number'; key: string; label: string; hint: string; min: number; max: number; step?: number; integer?: boolean; fallback: number; percent?: boolean }
  | { kind: 'source'; key: string; label: string; hint: string }
  | { kind: 'choice'; key: string; label: string; options: { value: string; label: string }[]; fallback: string }
  | { kind: 'flag'; key: string; label: string; fallback: boolean }
  | { kind: 'trait'; key: string; label: string; hint: string }
  | { kind: 'text'; key: string; label: string; hint: string }

const CONDITION_PARAMS: Record<TriggerCondition, ParamSpec[]> = {
  'promise.overdue': [
    { kind: 'number', key: 'days', label: 'Dias de atraso antes de avisar', hint: 'Com 0, avisa assim que o prazo passa.', min: 0, max: 365, fallback: 0 },
    { kind: 'choice', key: 'owner', label: 'De quem é a promessa', options: OWNERS, fallback: 'company' },
  ],
  'open_item.due': [
    { kind: 'number', key: 'lead_hours', label: 'Antecedência, em horas', hint: 'Avisa quando falta menos que isso para o prazo.', min: 1, max: 720, fallback: 24 },
    { kind: 'choice', key: 'owner', label: 'De quem é a pendência', options: OWNERS, fallback: ANY_OWNER },
  ],
  'trait.present': [
    { kind: 'trait', key: 'name', label: 'Padrão', hint: 'Uma das regras de padrão ligadas neste espaço.' },
    { kind: 'text', key: 'value', label: 'Valor do padrão (opcional)', hint: 'Só avisa quando o padrão tem este valor. Vazio avisa com qualquer valor.' },
  ],
  'sentiment.drop': [
    { kind: 'number', key: 'sessions', label: 'Conversas de comparação', hint: 'A última conversa é comparada com a média destas, as anteriores a ela.', min: 1, max: 20, integer: true, fallback: 3 },
    { kind: 'number', key: 'drop', label: 'Queda mínima', hint: 'O sentimento vai de -1 a 1: uma queda de 0,5 é forte.', min: 0.1, max: 2, step: 0.1, fallback: 0.5 },
  ],
  'identity.ambiguous': [],
  'fact.contradicted_by_system_event': [
    { kind: 'number', key: 'within_days', label: 'Janela, em dias', hint: 'Ações de agentes que o sistema desmentiu, ou não confirmou a tempo, nesses dias.', min: 1, max: 90, fallback: 7 },
  ],
  recontact: [
    // The cell reads 90 days of episodes, so a longer wait never matches.
    { kind: 'number', key: 'hours', label: 'Voltou em até quantas horas', hint: 'O cliente procura de novo dentro desse prazo depois da conversa anterior.', min: 1, max: 2160, fallback: 72 },
    { kind: 'flag', key: 'same_category', label: 'Só quando o assunto é o mesmo', fallback: true },
  ],
  'context_use.repetition_rate': [
    // domain/triggers/conditions.py RepetitionParams: `above` is a share in [0, 1), shown in percent.
    { kind: 'number', key: 'above', label: 'Repetição acima de (%)', hint: 'Parte das perguntas do cliente que o agente repete, na janela.', min: 0, max: 99, fallback: 20, percent: true },
    { kind: 'number', key: 'window_days', label: 'Janela, em dias', hint: 'A taxa soma as perguntas desses dias.', min: 1, max: 90, integer: true, fallback: 7 },
    { kind: 'number', key: 'min_questions', label: 'Perguntas na janela, no mínimo', hint: 'Com menos perguntas, a janela não diz nada e a regra não avisa.', min: 1, max: 100000, integer: true, fallback: 30 },
    { kind: 'source', key: 'source_id', label: 'Fonte vigiada', hint: 'Sem uma fonte escolhida, cada fonte é vigiada por conta própria.' },
  ],
  'source.silent': [
    // The sweep reads the last 30 days of events per source, so a longer silence never fires.
    { kind: 'number', key: 'minutes', label: 'Minutos sem eventos', hint: 'Avisa quando a fonte passa esse tempo sem mandar evento.', min: 1, max: 43200, fallback: 30 },
  ],
  'source.gap': [
    // domain/triggers/conditions.py GAP_DEFAULTS: `share` is a fraction, shown in percent.
    { kind: 'number', key: 'min_missing', label: 'Eventos que não chegaram, no mínimo', hint: 'O que o SDK diz ter mandado no dia, menos o que chegou e o que foi recusado.', min: 1, max: 1000000, integer: true, fallback: 10 },
    { kind: 'number', key: 'share', label: 'E mais que esta parte do enviado (%)', hint: 'Uma diferença pequena num dia de muito volume não avisa.', min: 0, max: 99, fallback: 5, percent: true },
  ],
}

/** What the person needs to know before testing a condition the dry run cannot see or that has nothing to set. */
const CONDITION_NOTES: Partial<Record<TriggerCondition, string>> = {
  'identity.ambiguous': 'Sem parâmetros: avisa quando um identificador do cliente fica ambíguo.',
  'context_use.repetition_rate':
    'Avisa uma vez cada vez que a taxa de uma fonte passa do limite, com as contagens no aviso. O grupo de controle fica de fora. Ligada, a regra confere as fontes a cada 5 minutos, e o teste confere as fontes agora.',
  'source.silent': 'Ligada, a regra confere as fontes a cada 5 minutos, e o teste confere as fontes agora. Cada silêncio avisa uma vez, e uma fonte que volta e para de novo avisa de novo.',
  'source.gap': 'Ligada, a regra compara a cada 5 minutos o que o SDK de cada fonte diz ter mandado ontem e hoje com o que chegou. Avisa uma vez por fonte e dia, com as contagens no aviso.',
}

/** A starting id for a new rule of each condition, in the tenant's words; the person can change it. */
const SUGGESTED_ID: Record<TriggerCondition, string> = {
  'promise.overdue': 'promessa.vencida',
  'open_item.due': 'pendencia.no.prazo',
  'trait.present': 'padrao.presente',
  'sentiment.drop': 'queda.de.satisfacao',
  'identity.ambiguous': 'identidade.ambigua',
  'fact.contradicted_by_system_event': 'acao.desmentida',
  recontact: 'recontato',
  'context_use.repetition_rate': 'taxa.de.repeticao',
  'source.silent': 'fonte.parada',
  'source.gap': 'fonte.com.perda',
}

/** Counts for every condition (conditions.py is_late): after this, the warning is late. */
const LATE_AFTER = { min: 1, max: 10080, fallback: 120 }
const WINDOW = { min: 1, max: 2160, integer: true }
const LATE_POLICIES = [
  { value: 'deliver_marked' as const, label: 'Entregar marcado como atrasado' },
  { value: 'suppress' as const, label: 'Não entregar' },
]

type ParamValues = Record<string, string | boolean>

/** Conditions whose subject is a source, not a profile: the sweep and the dry run look at the space's sources. */
const SOURCE_CONDITIONS: ReadonlySet<TriggerCondition> = new Set(['source.silent', 'context_use.repetition_rate', 'source.gap'])

const defaultParams = (c: TriggerCondition): ParamValues =>
  Object.fromEntries(
    CONDITION_PARAMS[c].map((p) => [p.key, p.kind === 'number' ? String(p.fallback) : p.kind === 'choice' ? p.fallback : p.kind === 'flag' ? p.fallback : '']),
  )

/** The form values of a stored rule: its parameters over the defaults, `null` owner as "any". */
function valuesOf(c: TriggerCondition, params: Record<string, unknown>): ParamValues {
  const values = defaultParams(c)
  for (const p of CONDITION_PARAMS[c]) {
    if (!(p.key in params)) continue
    const v = params[p.key]
    if (p.kind === 'flag') values[p.key] = Boolean(v)
    else if (p.kind === 'choice') values[p.key] = v === null ? ANY_OWNER : String(v)
    else if (p.kind === 'number' && p.percent) values[p.key] = String(Math.round(Number(v) * 10000) / 100)
    else values[p.key] = v === null || v === undefined ? '' : String(v)
  }
  return values
}

/** The parameters of a stored rule that the form does not edit: they go back as they are. */
function extraParams(c: TriggerCondition, params: Record<string, unknown>): Record<string, unknown> {
  const known = new Set([...CONDITION_PARAMS[c].map((p) => p.key), 'late_after_minutes'])
  return Object.fromEntries(Object.entries(params).filter(([k]) => !known.has(k)))
}

/** The `params` of the draft rule: every parameter of the condition, with the owner "any" as null. */
function paramsOf(c: TriggerCondition, values: ParamValues, lateAfter: string): Record<string, unknown> {
  const out: Record<string, unknown> = {}
  for (const p of CONDITION_PARAMS[c]) {
    const v = values[p.key]
    if (p.kind === 'number') out[p.key] = p.percent ? Number(v) / 100 : Number(v)
    else if (p.kind === 'choice') out[p.key] = v === ANY_OWNER ? null : v
    // No source chosen: every source on its own, so the key stays out.
    else if (p.kind === 'source') {
      if (typeof v === 'string' && v) out[p.key] = v
    }
    else if (p.kind === 'flag') out[p.key] = Boolean(v)
    else if (p.kind === 'trait') out[p.key] = v
    // An empty value would ask for a pattern whose value is empty; leaving it out means any value.
    else if (typeof v === 'string' && v.trim()) out[p.key] = v.trim()
  }
  out.late_after_minutes = Number(lateAfter)
  return out
}

/** A stored rule in words: its parameters, window and lateness, as the form would show them. */
function describeRule(rule: TriggerRuleDoc, sourceName: (id: string) => string): string {
  const params = rule.params ?? {}
  const values = valuesOf(rule.condition, params)
  const parts = CONDITION_PARAMS[rule.condition]?.map((p) => {
    const v = values[p.key]
    if (p.kind === 'number') return `${p.label}: ${fmtNumber(Number(v))}`
    if (p.kind === 'source') return `${p.label}: ${v ? sourceName(String(v)) : 'todas, cada uma por conta própria'}`
    if (p.kind === 'choice') return `${p.label}: ${p.options.find((o) => o.value === v)?.label ?? String(v)}`
    if (p.kind === 'flag') return `${p.label}: ${v ? 'sim' : 'não'}`
    if (p.kind === 'trait') return `Padrão: ${String(v || 'não informado')}`
    return typeof v === 'string' && v ? `Valor: ${v}` : null
  }) ?? []
  const late = Number(params.late_after_minutes ?? LATE_AFTER.fallback)
  const policy = rule.late_policy === 'suppress' ? 'não entrega' : 'entrega marcado'
  return [...parts.filter(Boolean), `Janela de ${fmtNumber(rule.window_hours ?? 24)} h`, `Atrasado depois de ${fmtNumber(late)} min: ${policy}`].join(' · ')
}

/** The draft as it would be tested and sent; a test counts only for the exact draft it ran on. */
type Draft = { rule_id: string; condition: TriggerCondition; endpoint_id: string; window_hours: number; late_policy: 'deliver_marked' | 'suppress'; params: Record<string, unknown> }

/**
 * Builds a warning rule from the condition catalog, tests it against the last 30 days and sends it for
 * approval as a diff of the `trigger-rules` document. A rule in use comes in
 * from "Regras em uso" with its values, keeps its id and goes out with the next version.
 */
function RuleEditor({ start, onNew }: { start: TriggerRuleDoc | null; onNew: () => void }) {
  const { cell, control, space, hasRole } = useSpace()
  const canPropose = useCanPropose('trigger-rules', ['integration'])
  const endpoints = useResource((signal) => control.configDocument(space.space_id, 'webhook-endpoints', { signal }), [space.space_id, control])
  const rules = useResource((signal) => control.configDocument(space.space_id, 'trigger-rules', { signal }), [space.space_id, control])
  const every = Array.isArray(endpoints.data?.document) ? (endpoints.data!.document as WebhookEndpointDoc[]) : []
  // A SIEM stream carries receipts only: a warning never reaches it (app/notify.py _subscribed).
  const list = every.filter((e) => !isSiemStream(e))
  const streams = new Set(every.filter(isSiemStream).map((e) => e.endpoint_id))
  const inUse = Array.isArray(rules.data?.document) ? (rules.data!.document as TriggerRuleDoc[]) : []
  const initial = start?.condition ?? 'promise.overdue'
  // Null until the person types: a new rule then takes its id from the condition.
  const [ruleId, setRuleId] = useState<string | null>(start?.rule_id ?? null)
  const [condition, setCondition] = useState<TriggerCondition>(initial)
  const [endpointId, setEndpointId] = useState(start?.endpoint_id ?? '')
  const [hours, setHours] = useState(String(start?.window_hours ?? 24))
  const [late, setLate] = useState<'deliver_marked' | 'suppress'>(start?.late_policy ?? 'deliver_marked')
  const [lateAfter, setLateAfter] = useState(String(start?.params?.late_after_minutes ?? LATE_AFTER.fallback))
  const [values, setValues] = useState<ParamValues>(() => (start ? valuesOf(initial, start.params ?? {}) : defaultParams(initial)))
  const extra = start && start.condition === condition ? extraParams(condition, start.params ?? {}) : {}
  const specs = CONDITION_PARAMS[condition]
  // The pattern rules in use, only when the condition asks for one of them.
  const needsTrait = specs.some((p) => p.kind === 'trait')
  const traitRules = useResource((signal) => control.configDocument(space.space_id, 'trait-rules', { signal }), [space.space_id, control], needsTrait)
  const traits = Array.isArray(traitRules.data?.document) ? (traitRules.data!.document as { rule_id?: unknown; catalog?: unknown; enabled?: unknown }[]).filter((t) => typeof t.rule_id === 'string' && t.enabled !== false) : []
  const sources = useSources()
  const test = useAction<{ draft: string; condition: TriggerCondition; result: TriggerDryRunResponse }>()
  const send = useAction<{ diff: DiffOut; rule: TriggerRuleDoc } | null>()
  const [reason, setReason] = useState('')
  const [idTaken, setIdTaken] = useState<string | null>(null)
  const chosen = (endpointId && !streams.has(endpointId) ? endpointId : '') || list[0]?.endpoint_id || ''
  // A rule in use that pointed at a SIEM stream (a JSON diff can write that) never warned anyone: say so.
  const onStream = endpointId !== '' && streams.has(endpointId)
  const chosenTrait = typeof values.name === 'string' && values.name ? values.name : String(traits[0]?.rule_id ?? '')
  const effective: ParamValues = needsTrait ? { ...values, name: chosenTrait } : values
  // A new rule starts with an id taken from the condition, free in the document.
  const id = ruleId ?? freeId(SUGGESTED_ID[condition], inUse.map((r) => r.rule_id))
  const problems = Object.fromEntries(specs.flatMap((p) => (p.kind === 'number' ? [[p.key, numberProblem(String(effective[p.key] ?? ''), p)]] : [])))
  const lateProblem = numberProblem(lateAfter, LATE_AFTER)
  const hoursProblem = numberProblem(hours, WINDOW)
  const idProblem = !RULE_ID.test(id)
    ? 'Letras minúsculas, números, ponto, dois-pontos, hífen e sublinhado, começando por letra, até 64.'
    : !start && (inUse.some((r) => r.rule_id === id) || idTaken === id)
      ? 'Outra regra já usa este ID. Escolha outro, ou ajuste a regra em Regras em uso.'
      : null
  const ok = !idProblem && Boolean(chosen) && !hoursProblem && Object.values(problems).every((x) => !x) && !lateProblem && (!needsTrait || Boolean(chosenTrait))
  const draft: Draft = { rule_id: id, condition, endpoint_id: chosen, window_hours: Number(hours), late_policy: late, params: { ...extra, ...paramsOf(condition, effective, lateAfter) } }
  const key = JSON.stringify(draft)
  const tested = test.result && test.result.draft === key ? test.result.result : null
  const set = (k: string, v: string | boolean) => setValues((cur) => ({ ...cur, [k]: v }))
  const r = test.result?.result
  const testedCondition = test.result?.condition

  const submitTest = (e: FormEvent) => {
    e.preventDefault()
    const sent = key
    test.run(async () => ({ draft: sent, condition: draft.condition, result: await cell.dryRunTrigger(draft) }))
  }
  const submitSend = () =>
    send.run(async () => {
      // The document as it is now, so a change approved while the form was open is kept.
      const now = await control.configDocument(space.space_id, 'trigger-rules')
      if (!Array.isArray(now.document)) throw new Error('the trigger-rules document is not a list')
      const current = now.document as TriggerRuleDoc[]
      const existing = current.find((x) => x.rule_id === draft.rule_id)
      if (existing && !start) {
        setIdTaken(draft.rule_id)
        return null
      }
      const rule: TriggerRuleDoc = { ...draft, version: existing ? nextVersion(existing.version) : '1', enabled: existing?.enabled ?? true }
      const document = existing ? current.map((x) => (x.rule_id === rule.rule_id ? rule : x)) : [...current, rule]
      return { diff: await control.submitDiff(space.space_id, 'trigger-rules', document, reason.trim()), rule }
    }, rules.reload)

  const sent = send.result
  return (
    <Section
      title={start ? `Ajustar a regra ${start.rule_id}` : 'Nova regra de aviso'}
      description="Monte a regra a partir do catálogo e teste: o teste diz quantos clientes ativos nos últimos 30 dias disparariam a regra agora e se o endpoint pode receber, sem enviar nada. Depois, envie para aprovação."
      actions={
        start ? (
          <button type="button" className={btnQuiet} onClick={onNew}>
            Começar uma regra nova
          </button>
        ) : undefined
      }
    >
      {start && (
        <p className={`${small} mb-3`}>
          Versão {start.version} em uso. Enviada, a regra sai como versão {nextVersion(start.version)}, com o mesmo ID{start.enabled === false ? ', e continua desligada' : ''}.
        </p>
      )}
      {!hasRole('integration') && <p className={`${small} mb-3`}>O teste exige o papel Integração.</p>}
      <form className="grid gap-4" onSubmit={submitTest} noValidate>
        <div className="grid gap-3 sm:grid-cols-2">
          {start ? (
            <TextInput label="ID da regra" value={start.rule_id} onChange={() => undefined} mono readOnly hint="O ID de uma regra em uso não muda: para outro ID, comece uma regra nova." />
          ) : (
            <Field label="ID da regra">
              {(fid) => (
                <>
                  <input
                    id={fid}
                    className={`${idProblem ? input.replace('border-edge', danger.border) : input} font-mono`}
                    value={id}
                    onChange={(e) => setRuleId(e.target.value)}
                    autoComplete="off"
                    spellCheck={false}
                    aria-invalid={idProblem ? true : undefined}
                    aria-describedby={`${fid}-note`}
                  />
                  <p id={`${fid}-note`} className={idProblem ? `mt-1.5 text-[0.8125rem] font-semibold ${danger.text}` : hint}>
                    {idProblem ?? 'Os disparos e os webhooks levam este ID.'}
                  </p>
                </>
              )}
            </Field>
          )}
          <Select
            label="Condição"
            value={condition}
            onChange={(c) => {
              setCondition(c)
              setValues(start && start.condition === c ? valuesOf(c, start.params ?? {}) : defaultParams(c))
            }}
            options={TRIGGER_CONDITIONS.map((c) => ({ value: c, label: triggerCondition(c) }))}
          />
          {endpoints.status === 'error' ? (
            <ErrorState error={endpoints.error} onRetry={endpoints.reload} compact />
          ) : (
            <Select
              label="Endpoint que recebe"
              hint={
                endpoints.status === 'success' && list.length === 0 ? (
                  <>
                    Crie um endpoint em{' '}
                    <Link to={paths.systems(space.space_id)} className="text-ink underline decoration-accent decoration-2 underline-offset-4">
                      Sistemas
                    </Link>
                    , na fonte que recebe os avisos.
                  </>
                ) : onStream ? (
                  `A regra apontava para ${endpointId}, um fluxo de comprovantes para o SIEM, que não recebe avisos. Escolha o endpoint que recebe.`
                ) : undefined
              }
              value={chosen}
              onChange={setEndpointId}
              disabled={list.length === 0}
              options={list.length === 0 ? [{ value: '', label: endpoints.status === 'loading' ? 'Carregando…' : 'Nenhum endpoint configurado' }] : list.map((e) => ({ value: e.endpoint_id, label: `${e.endpoint_id} (${e.url})${e.enabled === false ? ', desligado' : ''}` }))}
            />
          )}
          <NumberField label="Janela, em horas" hint="Um aviso por perfil, ou por fonte, nesta janela." value={hours} onChange={setHours} problem={hoursProblem} min={WINDOW.min} max={WINDOW.max} />
          <Select label="Aviso que chega atrasado" value={late} onChange={setLate} options={LATE_POLICIES} />
          <NumberField
            label="Conta como atrasado depois de, em minutos"
            hint="Tempo entre a condição acontecer e o aviso sair."
            value={lateAfter}
            onChange={setLateAfter}
            problem={lateProblem}
            min={LATE_AFTER.min}
            max={LATE_AFTER.max}
          />
        </div>
        <fieldset className="grid min-w-0 gap-3 sm:grid-cols-2">
          <legend className="mb-2 text-[0.8125rem] font-semibold text-ink">Parâmetros de {triggerCondition(condition)}</legend>
          {CONDITION_NOTES[condition] && <p className={`${small} sm:col-span-2`}>{CONDITION_NOTES[condition]}</p>}
          {specs.map((p) => {
            const k = `${condition}.${p.key}`
            if (p.kind === 'number')
              return <NumberField key={k} label={p.label} hint={p.hint} value={String(effective[p.key] ?? '')} onChange={(v) => set(p.key, v)} problem={problems[p.key] ?? null} min={p.min} max={p.max} step={p.step} />
            if (p.kind === 'choice') return <Select key={k} label={p.label} value={String(effective[p.key])} onChange={(v) => set(p.key, v)} options={p.options} />
            if (p.kind === 'source')
              return (
                <Select
                  key={k}
                  label={p.label}
                  hint={p.hint}
                  value={String(effective[p.key] ?? '')}
                  onChange={(v) => set(p.key, v)}
                  options={[{ value: '', label: 'Todas as fontes' }, ...sources.list.map((x) => ({ value: x.source_id, label: `${x.name}${x.vendor ? ` (${x.vendor})` : ''}` }))]}
                />
              )
            if (p.kind === 'flag')
              return (
                <label key={k} className="flex min-h-11 cursor-pointer items-center gap-2.5 self-end text-[0.875rem] text-ink">
                  <input type="checkbox" className="h-5 w-5 shrink-0 accent-[#5e7c00]" checked={Boolean(effective[p.key])} onChange={(e) => set(p.key, e.target.checked)} />
                  {p.label}
                </label>
              )
            if (p.kind === 'trait')
              return traitRules.status === 'error' ? (
                <ErrorState key={k} error={traitRules.error} onRetry={traitRules.reload} compact />
              ) : (
                <Select
                  key={k}
                  label={p.label}
                  hint={traitRules.status === 'success' && traits.length === 0 ? 'Nenhuma regra de padrão ligada neste espaço: o aviso de padrão precisa de uma.' : p.hint}
                  value={chosenTrait}
                  onChange={(v) => set(p.key, v)}
                  disabled={traits.length === 0}
                  options={
                    traits.length === 0
                      ? [{ value: '', label: traitRules.status === 'success' ? 'Nenhuma regra de padrão' : 'Carregando…' }]
                      : traits.map((t) => ({ value: String(t.rule_id), label: `${String(t.rule_id)} (${traitName(String(t.catalog ?? ''))})` }))
                  }
                />
              )
            return <TextInput key={k} label={p.label} hint={p.hint} value={String(effective[p.key] ?? '')} onChange={(v) => set(p.key, v)} />
          })}
          {Object.keys(extra).length > 0 && (
            <p className={`${small} sm:col-span-2`}>
              Parâmetros fora do formulário, mantidos como estão: <span className={mono}>{Object.keys(extra).join(', ')}</span>.
            </p>
          )}
        </fieldset>
        {Boolean(test.error) && <ErrorState error={test.error} compact />}
        <div>
          <button type="submit" className={btnPrimary} disabled={test.busy || !ok}>
            {test.busy ? 'Testando…' : 'Testar a regra'}
          </button>
        </div>
      </form>
      {r && (
        <div className="mt-5 grid gap-3 border-t border-line pt-4" role="status">
          {!tested && <p className={`${small} font-semibold`}>Este resultado é da regra antes da última mudança. Teste de novo.</p>}
          <p className="text-ink">
            <strong>{fmtInt(r.would_fire)}</strong>{' '}
            {testedCondition && SOURCE_CONDITIONS.has(testedCondition)
              ? `avisos sairiam agora, de ${fmtInt(r.subjects_evaluated)} fontes do espaço.`
              : `de ${fmtInt(r.subjects_evaluated)} perfis ativos nos últimos ${r.window_days} dias disparariam a regra agora.`}
          </p>
          {r.target_problem && (
            <p className={`text-[0.875rem] font-semibold ${danger.text}`}>
              O endpoint não pode receber este aviso: {targetProblem(r.target_problem)}
            </p>
          )}
          {r.sample.length > 0 && (
            <ul className="divide-y divide-line2 rounded-xl border border-line">
              {r.sample.map((s) => (
                <li key={s.subject_id} className="flex flex-wrap items-baseline justify-between gap-2 px-4 py-2.5">
                  {testedCondition && SOURCE_CONDITIONS.has(testedCondition) ? (
                    <Link to={paths.agent(space.space_id, s.subject_id)} className="font-semibold text-ink underline decoration-accent decoration-2 underline-offset-4">
                      {s.pseudonym ?? s.subject_id}
                    </Link>
                  ) : (
                    <Link to={paths.profile(space.space_id, s.subject_id)} className="font-mono font-semibold text-ink underline decoration-accent decoration-2 underline-offset-4">
                      {s.pseudonym ?? s.subject_id}
                    </Link>
                  )}
                  <span className={`${mono} text-ink2`}>{s.evidence.length} evidências</span>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
      {canPropose && (
        <div className="mt-5 grid gap-3 border-t border-line pt-4">
          <h3 className="text-[0.9375rem] font-semibold text-ink">Enviar para aprovação</h3>
          {sent ? (
            <>
              <DiffSent diff={sent.diff} title={`Regra ${sent.rule.rule_id}, versão ${sent.rule.version}, enviada para aprovação.`} />
              <div>
                <button type="button" className={btnQuiet} onClick={onNew}>
                  Começar outra regra
                </button>
              </div>
            </>
          ) : (
            <>
              <p className={small}>
                {tested
                  ? tested.target_problem && tested.target_problem !== 'endpoint_disabled'
                    ? 'Escolha outro endpoint ou mude a regra: do jeito que está, ela nunca chegaria ao endpoint.'
                    : 'A regra entra como mudança de configuração e só vale depois de aprovada em Configuração.'
                  : 'Teste a regra do jeito que ela vai sair antes de enviar.'}
              </p>
              <DiffReason value={reason} onChange={setReason} />
              {Boolean(send.error) && <ErrorState error={send.error} compact />}
              <div>
                <button
                  type="button"
                  className={btnPrimary}
                  onClick={submitSend}
                  disabled={send.busy || !ok || !tested || Boolean(tested.target_problem && tested.target_problem !== 'endpoint_disabled') || !diffReasonOk(reason)}
                >
                  {send.busy ? 'Enviando…' : 'Enviar para aprovação'}
                </button>
              </div>
            </>
          )}
        </div>
      )}
    </Section>
  )
}

function Rules({ onEdit }: { onEdit: (rule: TriggerRuleDoc | null) => void }) {
  const { control, space } = useSpace()
  const sources = useSources()
  const canPropose = useCanPropose('trigger-rules', ['integration'])
  const rules = useResource((signal) => control.configDocument(space.space_id, 'trigger-rules', { signal }), [space.space_id, control])
  const [toggling, setToggling] = useState<TriggerRuleDoc | null>(null)
  const list = Array.isArray(rules.data?.document) ? (rules.data!.document as TriggerRuleDoc[]) : null
  const create = canPropose ? (
    <button type="button" className={btnPrimary} onClick={() => onEdit(null)}>
      Nova regra
    </button>
  ) : undefined
  return (
    <Section
      title="Regras de aviso em uso"
      description="Cada mudança vira um diff com motivo, que outra pessoa aprova em Configuração. A regra nova ou ajustada só vale depois de aprovada."
      actions={list && list.length > 0 ? create : undefined}
    >
      {rules.status === 'loading' && <Loading label="Carregando as regras…" />}
      {rules.status === 'error' && <ErrorState error={rules.error} onRetry={rules.reload} compact />}
      {rules.status === 'success' && rules.data && !list && <JsonView value={rules.data.document} label="Documento das regras de aviso" />}
      {list && list.length === 0 && (
        <Empty title="Nenhuma regra de aviso ligada." action={create}>
          {canPropose ? 'Monte a primeira a partir do catálogo de condições.' : 'Quem tem o papel Integração cria as regras.'}
        </Empty>
      )}
      {list && list.length > 0 && (
        <>
          <p className={`${small} mb-2`}>Versão {rules.data!.version} da configuração.</p>
          <ul className="divide-y divide-line2 rounded-xl border border-line">
            {list.map((rule, i) => (
              <li key={String(rule.rule_id ?? i)} className="grid gap-2 px-4 py-3 sm:flex sm:items-start sm:justify-between sm:gap-3">
                <div className="min-w-0 sm:flex-1">
                  <p className="text-ink">
                    <span className={`${mono} break-all font-semibold`}>{String(rule.rule_id)}</span> · {triggerCondition(String(rule.condition ?? ''))}{' '}
                    {rule.enabled === false ? <Chip>Desligada</Chip> : <Chip tone="ok">Ligada</Chip>}
                  </p>
                  <p className="mt-0.5 text-[0.8125rem] text-ink2">{CONDITION_PARAMS[rule.condition] ? describeRule(rule, (id) => sources.byId.get(id)?.name ?? id) : JSON.stringify(rule.params ?? {})}</p>
                  <p className={`${mono} mt-0.5 break-all text-ink2`}>
                    endpoint {String(rule.endpoint_id ?? 'não informado')} · versão {String(rule.version ?? '')}
                  </p>
                </div>
                {canPropose && CONDITION_PARAMS[rule.condition] && (
                  <div className="flex shrink-0 flex-wrap gap-1">
                    <button type="button" className={btnSecondary} onClick={() => onEdit(rule)} aria-label={`Ajustar a regra ${rule.rule_id}`}>
                      Ajustar
                    </button>
                    <button type="button" className={btnQuiet} onClick={() => setToggling(rule)} aria-label={`${rule.enabled === false ? 'Ligar' : 'Desligar'} a regra ${rule.rule_id}`}>
                      {rule.enabled === false ? 'Ligar' : 'Desligar'}
                    </button>
                  </div>
                )}
              </li>
            ))}
          </ul>
        </>
      )}
      {toggling && <ToggleRule type="trigger-rules" what="regra de aviso" ruleId={toggling.rule_id} enabled={toggling.enabled !== false} onClose={() => setToggling(null)} onSent={rules.reload} />}
    </Section>
  )
}
