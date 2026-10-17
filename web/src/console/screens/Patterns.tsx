import { useRef, useState, type FormEvent } from 'react'
import { Link } from 'react-router-dom'
import type { DiffOut, DocumentOut, ProfileMemory, TraitCatalog, TraitDryRunResponse, TraitRuleDoc } from '../api/types'
import { TRAIT_CATALOG } from '../api/types'
import { fmtDate, fmtInt } from '../lib/format'
import { traitName } from '../lib/labels'
import { useResource, type Resource } from '../lib/useResource'
import { paths } from '../paths'
import { useSpace } from '../session'
import { Chip, JsonView, PageHeader, useSources } from '../ui/bits'
import { Field, Section, Select, TextInput, useAction } from '../ui/forms'
import { useTitle } from '../ui/layout'
import { NumberField, numberProblem } from '../ui/NumberField'
import { DiffReason, diffReasonOk, DiffSent, freeId, nextVersion, ToggleRule, useCanPropose } from '../ui/proposal'
import { Empty, ErrorState, Loading } from '../ui/States'
import { btnPrimary, btnQuiet, btnSecondary, danger, hint, input, mono, small } from '../ui/styles'
import { TraitsTab } from './profile/MemoryTabs'

const RULE_ID = /^[a-z][a-z0-9_.:-]{0,63}$/

/** The catalog defaults (domain/traits/rules.py CATALOG); the form starts from them. */
const DEFAULTS: Record<TraitCatalog, Record<string, unknown>> = {
  recurring_complaint: { intent: 'complaint', min_count: 3, window_days: 90 },
  broken_company_promise: { window_days: 60, grace_hours: 0 },
  asked_to_cancel: { intent: 'cancellation', min_count: 1, window_days: 180 },
  preferred_channel: { min_share: 0.7, min_conversations: 5, window_days: 180, initiated_by: 'customer' },
  does_not_answer_calls: { min_attempts: 3, window_days: 30, event_types: ['call.unanswered'], voice_channels: ['voice'] },
  escalated_to_human: { outcome: 'escalated', min_count: 2, window_days: 90 },
  declining_sentiment: { sessions: 3, threshold: -0.3, window_days: 30 },
  overdue_invoices: { event_type: 'invoice.overdue', min_count: 2, window_days: 90 },
}

/**
 * A pattern needs repetition: the cell raises a count below 2 to 2 (rules.py `_min_count`), except in the
 * entries with single evidence, where one structured and unambiguous piece of evidence is enough.
 */
const SINGLE_EVIDENCE: ReadonlySet<string> = new Set(['broken_company_promise', 'asked_to_cancel'])
const COUNTS: ReadonlySet<string> = new Set(['min_count', 'min_conversations', 'min_attempts', 'sessions'])

/**
 * Catalog entries that count system events (rules.py EVENT_RULES). System events stay in the space for
 * `settings.events_hot_days` (90 by default), and the control plane refuses a window past that.
 */
const EVENT_RULES: ReadonlySet<string> = new Set(['does_not_answer_calls', 'overdue_invoices'])
const HOT_DAYS_DEFAULT = 90

/** A starting id for a new rule of each catalog entry; warning rules name the pattern by it. */
const SUGGESTED_ID: Record<TraitCatalog, string> = {
  recurring_complaint: 'reclamacao.recorrente',
  broken_company_promise: 'promessa.quebrada',
  asked_to_cancel: 'pediu.cancelamento',
  preferred_channel: 'canal.preferido',
  does_not_answer_calls: 'nao.atende.ligacoes',
  escalated_to_human: 'escalou.para.humano',
  declining_sentiment: 'satisfacao.em.queda',
  overdue_invoices: 'faturas.em.atraso',
}

/**
 * The parameters a person adjusts, with their names in Portuguese and the limits the cell accepts
 * (validate_rule: a window of 1 to 365 days). The others (the intent or the event a rule counts) come
 * fixed with the catalog entry and are shown, not edited. `percent` shows a 0 to 1 share as 0 to 100.
 */
const PARAMS: Record<string, { label: string; hint?: string; min: number; max: number; step?: number; integer?: boolean; percent?: boolean }> = {
  min_count: { label: 'Quantas vezes, no mínimo', min: 1, max: 100, integer: true },
  window_days: { label: 'Janela, em dias', hint: 'Conta só o que aconteceu nesse período.', min: 1, max: 365, integer: true },
  grace_hours: { label: 'Tolerância depois do prazo, em horas', min: 0, max: 720, integer: true },
  min_share: { label: 'Parte das conversas nesse canal, no mínimo (%)', min: 1, max: 100, integer: true, percent: true },
  min_conversations: { label: 'Conversas na janela, no mínimo', min: 1, max: 1000, integer: true },
  min_attempts: { label: 'Ligações sem resposta, no mínimo', min: 1, max: 100, integer: true },
  sessions: { label: 'Conversas seguidas com queda', min: 2, max: 20, integer: true },
  threshold: { label: 'Queda de sentimento, no mínimo', hint: 'De 0 a -1: -0,3 é uma queda moderada.', min: -1, max: 0, step: 0.05 },
}
type ParamSpec = (typeof PARAMS)[string]

/** `events_hot_days` of the settings document, as the cell reads it (30 to 90, 90 by default). */
function hotDaysOf(document: unknown): number {
  const days = document && typeof document === 'object' ? (document as { events_hot_days?: unknown }).events_hot_days : undefined
  return typeof days === 'number' && Number.isInteger(days) && days >= 30 && days <= HOT_DAYS_DEFAULT ? days : HOT_DAYS_DEFAULT
}

/** A parameter as the cell counts it for this catalog entry: the minimum a count takes, the window it reaches. */
function spec(catalog: string, key: string, hotDays: number): ParamSpec {
  const base = PARAMS[key]!
  if (COUNTS.has(key) && !SINGLE_EVIDENCE.has(catalog) && base.min < 2) {
    return { ...base, min: 2, hint: 'Um padrão pede repetição: no mínimo 2.' }
  }
  if (key === 'window_days' && EVENT_RULES.has(catalog)) {
    return { ...base, max: hotDays, hint: `Eventos de sistema ficam ${hotDays} dias no espaço, e a janela não passa disso.` }
  }
  return base
}

const FIXED: Record<string, string> = {
  intent: 'Intenção contada',
  outcome: 'Desfecho contado',
  event_type: 'Evento contado',
  event_types: 'Eventos contados',
  voice_channels: 'Canais de voz',
  initiated_by: 'Quem começou a conversa',
}
const shown = (v: unknown) => (Array.isArray(v) ? v.join(', ') : String(v))

/** The typed value of a number parameter, as the field shows it. */
const typed = (key: string, v: unknown) => {
  const n = typeof v === 'number' ? v : Number(v)
  if (!Number.isFinite(n)) return ''
  return String(PARAMS[key]?.percent ? Math.round(n * 100) : n)
}

/** The number the cell runs instead of a stored one it raises (a count) or cuts (a window over events); null when it runs the stored one. */
function effective(catalog: string, key: string, value: unknown, hotDays: number): number | null {
  const n = Number(value)
  if (!Number.isFinite(n)) return null
  if (COUNTS.has(key) && !SINGLE_EVIDENCE.has(catalog) && n < 2) return 2
  if (key === 'window_days' && EVENT_RULES.has(catalog) && n > hotDays) return hotDays
  return null
}

/** A rule of the document in words: the catalog entry and its numbers, as the cell runs them. */
function describePattern(rule: TraitRuleDoc, hotDays: number): string {
  const params = { ...(DEFAULTS[rule.catalog] ?? {}), ...(rule.params ?? {}) }
  const numbers = Object.entries(params)
    .filter(([k]) => PARAMS[k])
    .map(([k, v]) => {
      const said = `${PARAMS[k]!.label}: ${typed(k, v)}`
      const runs = effective(rule.catalog, k, v, hotDays)
      return runs === null ? said : `${said} (a célula conta ${runs})`
    })
  const others = Object.entries(rule.params ?? {})
    .filter(([k]) => !PARAMS[k] && !FIXED[k])
    .map(([k, v]) => `${k}: ${shown(v)}`)
  const purposes = rule.purposes && rule.purposes.length > 0 ? [`Finalidades: ${rule.purposes.join(', ')}`] : []
  return [...numbers, ...others, ...purposes].join(' · ') || 'Parâmetros do catálogo'
}

/**
 * Patterns: signals the memory derives with a rule, evidence
 * and an expiry. Never a label without origin, never a score about the person.
 * Rules are built from the catalog, tested and sent for approval here, never written as JSON.
 */
export function Patterns() {
  const { space, control } = useSpace()
  useTitle('Padrões', space.environment === 'sandbox')
  const rules = useResource((signal) => control.configDocument(space.space_id, 'trait-rules', { signal }), [space.space_id, control])
  // How long system events stay in the space: a rule over them never counts further back.
  const settings = useResource((signal) => control.configDocument(space.space_id, 'settings', { signal }), [space.space_id, control])
  const hotDays = hotDaysOf(settings.data?.document)
  // What the editor starts from: a new rule, or a rule in use to adjust. The key starts it over.
  const [start, setStart] = useState<{ key: number; rule: TraitRuleDoc | null }>({ key: 0, rule: null })
  const editor = useRef<HTMLDivElement>(null)
  const edit = (rule: TraitRuleDoc | null) => {
    setStart((cur) => ({ key: cur.key + 1, rule }))
    // The editor is further down the page: bring it into view with the focus on its heading.
    window.setTimeout(() => {
      editor.current?.scrollIntoView?.({ block: 'start' })
      editor.current?.querySelector<HTMLElement>('h2')?.focus()
    }, 0)
  }

  return (
    <>
      <PageHeader
        screen="patterns"
      />
      <div className="grid gap-6">
        <RuleList rules={rules} hotDays={hotDays} onEdit={edit} />
        <div ref={editor} data-tour="patterns.editor" className="scroll-mt-20">
          <PatternEditor
            key={start.key}
            start={start.rule}
            inUse={Array.isArray(rules.data?.document) ? (rules.data!.document as TraitRuleDoc[]) : []}
            hotDays={hotDays}
            onSent={rules.reload}
            onNew={() => edit(null)}
          />
        </div>
        <OneCustomer />
      </div>
    </>
  )
}

function RuleList({ rules, hotDays, onEdit }: { rules: Resource<DocumentOut>; hotDays: number; onEdit: (rule: TraitRuleDoc | null) => void }) {
  const canPropose = useCanPropose('trait-rules', ['integration'])
  const [toggling, setToggling] = useState<TraitRuleDoc | null>(null)
  const list = Array.isArray(rules.data?.document) ? (rules.data!.document as TraitRuleDoc[]) : null
  const create = canPropose ? (
    <button type="button" className={btnPrimary} onClick={() => onEdit(null)}>
      Nova regra
    </button>
  ) : undefined
  return (
    <Section
      tour="patterns.rules"
      title="Regras em uso"
      description="As regras de padrão deste espaço. Cada mudança vira um diff com motivo, que outra pessoa aprova em Configuração."
      actions={list && list.length > 0 ? create : undefined}
    >
      {rules.status === 'loading' && <Loading label="Carregando as regras…" />}
      {rules.status === 'error' && <ErrorState error={rules.error} onRetry={rules.reload} compact />}
      {rules.status === 'success' && rules.data && !list && <JsonView value={rules.data.document} label="Documento das regras de padrão" />}
      {list && list.length === 0 && (
        <Empty title="Nenhuma regra de padrão ligada." action={create}>
          Sem regra, a memória não deriva padrões neste espaço.
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
                    <span className={`${mono} break-all font-semibold`}>{String(rule.rule_id ?? 'sem id')}</span> · {traitName(String(rule.catalog ?? ''))}{' '}
                    {rule.enabled === false ? <Chip>Desligada</Chip> : <Chip tone="ok">Ligada</Chip>}
                  </p>
                  <p className="mt-0.5 text-[0.8125rem] text-ink2">{describePattern(rule, hotDays)}</p>
                  <p className={`${mono} mt-0.5 text-ink2`}>versão {String(rule.version ?? '')}</p>
                </div>
                {canPropose && DEFAULTS[rule.catalog] && (
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
      {toggling && <ToggleRule type="trait-rules" what="regra de padrão" ruleId={toggling.rule_id} enabled={toggling.enabled !== false} onClose={() => setToggling(null)} onSent={rules.reload} />}
    </Section>
  )
}

/**
 * Builds a pattern rule from the catalog, tests it against the last 90 days and sends it for approval as
 * a diff of the `trait-rules` document. The control plane refuses a rule the
 * catalog or the sensitive-category barrier would make the cell ignore.
 */
function PatternEditor({ start, inUse, hotDays, onSent, onNew }: { start: TraitRuleDoc | null; inUse: TraitRuleDoc[]; hotDays: number; onSent: () => void; onNew: () => void }) {
  const { cell, control, space, hasRole } = useSpace()
  const sources = useSources()
  const canPropose = useCanPropose('trait-rules', ['integration'])
  const initial = start?.catalog ?? 'recurring_complaint'
  // Null until the person types: a new rule then takes its id from the catalog entry.
  const [ruleId, setRuleId] = useState<string | null>(start?.rule_id ?? null)
  const [catalog, setCatalog] = useState<TraitCatalog>(initial)
  const [params, setParams] = useState<Record<string, unknown>>(() => ({ ...DEFAULTS[initial], ...(start?.params ?? {}) }))
  const [purposes, setPurposes] = useState<string[]>(start?.purposes ?? [])
  const [reason, setReason] = useState('')
  const [idTaken, setIdTaken] = useState<string | null>(null)
  const test = useAction<{ draft: string; result: TraitDryRunResponse }>()
  const send = useAction<{ diff: DiffOut; rule: TraitRuleDoc } | null>()
  const known = [...new Set(sources.list.flatMap((x) => x.purposes ?? []))].sort()
  const offered = [...new Set([...known, ...purposes])]
  const id = ruleId ?? freeId(SUGGESTED_ID[catalog], inUse.map((r) => r.rule_id))
  const numbers = Object.entries(params).filter(([k]) => PARAMS[k])
  const problems = Object.fromEntries(numbers.map(([k, v]) => [k, numberProblem(typed(k, v), spec(catalog, k, hotDays))]))
  const idProblem = !RULE_ID.test(id)
    ? 'Letras minúsculas, números, ponto, dois-pontos, hífen e sublinhado, começando por letra, até 64.'
    : !start && (inUse.some((r) => r.rule_id === id) || idTaken === id)
      ? 'Outra regra já usa este ID. Escolha outro, ou ajuste a regra em Regras em uso.'
      : null
  const ok = !idProblem && Object.values(problems).every((x) => !x)
  const draft = { rule_id: id, catalog, params }
  const key = JSON.stringify({ ...draft, purposes })
  const tested = test.result && test.result.draft === key ? test.result.result : null
  const extras = Object.keys(params).filter((k) => !PARAMS[k] && !FIXED[k])
  const r = test.result?.result

  const submitTest = (e: FormEvent) => {
    e.preventDefault()
    const ran = key
    test.run(async () => ({ draft: ran, result: await cell.dryRunTraitRule(draft) }))
  }
  const submitSend = () =>
    send.run(async () => {
      // The document as it is now, so a change approved while the form was open is kept.
      const now = await control.configDocument(space.space_id, 'trait-rules')
      if (!Array.isArray(now.document)) throw new Error('the trait-rules document is not a list')
      const current = now.document as TraitRuleDoc[]
      const existing = current.find((x) => x.rule_id === id)
      if (existing && !start) {
        setIdTaken(id)
        return null
      }
      const rule: TraitRuleDoc = { rule_id: id, catalog, version: existing ? nextVersion(existing.version) : '1', enabled: existing?.enabled ?? true, params, purposes }
      const document = existing ? current.map((x) => (x.rule_id === id ? rule : x)) : [...current, rule]
      return { diff: await control.submitDiff(space.space_id, 'trait-rules', document, reason.trim()), rule }
    }, onSent)

  const sent = send.result
  return (
    <Section
      title={start ? `Ajustar a regra ${start.rule_id}` : 'Criar e testar uma regra'}
      description="A regra roda contra os últimos 90 dias e mostra quantos clientes teriam o padrão, com exemplos. Nada é gravado no teste. Depois, envie para aprovação."
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
      {!hasRole('integration', 'analysis') && <p className={`${small} mb-3`}>O teste exige o papel Integração ou Análise.</p>}
      <form className="grid gap-4" onSubmit={submitTest} noValidate>
        <div className="grid gap-3 sm:grid-cols-2">
          {start ? (
            <TextInput label="ID da regra" value={start.rule_id} onChange={() => undefined} mono readOnly hint="O ID de uma regra em uso não muda: os padrões e os avisos são ligados a ele." />
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
                    {idProblem ?? 'É o nome do padrão nos perfis e nas regras de aviso.'}
                  </p>
                </>
              )}
            </Field>
          )}
          <Select
            label="Padrão do catálogo"
            value={catalog}
            onChange={(c) => {
              setCatalog(c)
              setParams(start && start.catalog === c ? { ...DEFAULTS[c], ...(start.params ?? {}) } : DEFAULTS[c])
            }}
            options={TRAIT_CATALOG.map((c) => ({ value: c, label: traitName(c) }))}
          />
        </div>
        <fieldset className="grid gap-3 sm:grid-cols-2">
          <legend className="mb-2 text-[0.8125rem] font-semibold text-ink">Parâmetros</legend>
          {numbers.map(([k]) => {
            const p = spec(catalog, k, hotDays)
            return (
              <NumberField
                key={`${catalog}.${k}`}
                label={p.label}
                hint={p.hint}
                value={typed(k, params[k])}
                onChange={(raw) => {
                  const v = raw === '' ? NaN : Number(raw)
                  setParams((cur) => ({ ...cur, [k]: p.percent ? v / 100 : v }))
                }}
                problem={problems[k] ?? null}
                min={p.min}
                max={p.max}
                step={p.step}
              />
            )
          })}
        </fieldset>
        {Object.keys(params).some((k) => FIXED[k]) && (
          <p className={small}>
            Fixo no catálogo:{' '}
            {Object.entries(params)
              .filter(([k]) => FIXED[k])
              .map(([k, v]) => `${FIXED[k]}: ${shown(v)}`)
              .join(' · ')}
          </p>
        )}
        {extras.length > 0 && (
          <p className={small}>
            Parâmetros fora do formulário, mantidos como estão: <span className={mono}>{extras.map((k) => `${k}: ${shown(params[k])}`).join(', ')}</span>.
          </p>
        )}
        {offered.length > 0 && (
          <fieldset className="min-w-0">
            <legend className="text-[0.8125rem] font-semibold text-ink">Finalidades que recebem o padrão (opcional)</legend>
            <p className={hint}>Vazio: vale a regra do catálogo (faturas em atraso chegam só à cobrança). Um aviso de padrão só chega a um endpoint de fonte com uma destas finalidades.</p>
            <div className="mt-1 flex flex-wrap gap-x-4">
              {offered.map((purpose) => (
                <label key={purpose} className="flex min-h-11 cursor-pointer items-center gap-2.5 text-[0.875rem] text-ink lg:min-h-9">
                  <input
                    type="checkbox"
                    className="h-5 w-5 shrink-0 accent-[#5e7c00]"
                    checked={purposes.includes(purpose)}
                    onChange={(e) => setPurposes((cur) => (e.target.checked ? [...cur, purpose].sort() : cur.filter((x) => x !== purpose)))}
                  />
                  <span className={mono}>{purpose}</span>
                </label>
              ))}
            </div>
          </fieldset>
        )}
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
            <strong>{fmtInt(r.profiles_matching)}</strong> de {fmtInt(r.profiles_evaluated)} clientes teriam o padrão nos últimos {r.window_days} dias.
          </p>
          {r.sample.length > 0 && (
            <ul className="divide-y divide-line2 rounded-xl border border-line">
              {r.sample.map((x) => (
                <li key={x.profile_id} className="flex flex-wrap items-baseline justify-between gap-2 px-4 py-2.5">
                  <span>
                    <Link to={paths.profile(space.space_id, x.profile_id)} className="font-mono font-semibold text-ink underline decoration-accent decoration-2 underline-offset-4">
                      {x.pseudonym}
                    </Link>{' '}
                    <span className="text-ink">{x.value}</span>
                  </span>
                  <span className={`${mono} text-ink2`}>
                    {x.evidence.length} evidências · vale até {fmtDate(x.expires_at)}
                  </span>
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
              <p className={small}>{tested ? 'A regra entra como mudança de configuração e só vale depois de aprovada em Configuração.' : 'Teste a regra do jeito que ela vai sair antes de enviar.'}</p>
              <DiffReason value={reason} onChange={setReason} />
              {Boolean(send.error) && <ErrorState error={send.error} compact />}
              <div>
                <button type="button" className={btnPrimary} onClick={submitSend} disabled={send.busy || !ok || !tested || !diffReasonOk(reason)}>
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

function OneCustomer() {
  const { cell } = useSpace()
  const [id, setId] = useState('')
  const [state, setState] = useState<{ status: 'idle' | 'loading' | 'success' | 'error'; data?: ProfileMemory; error?: unknown }>({ status: 'idle' })
  const load = async () => {
    setState({ status: 'loading' })
    try {
      setState({ status: 'success', data: await cell.memory(id.trim()) })
    } catch (error) {
      setState({ status: 'error', error })
    }
  }
  return (
    <Section tour="patterns.customer" title="Padrões de um cliente" description="Retire um padrão errado ou tire o cliente dos padrões quando ele se opuser. A retirada deixa uma lápide, e o padrão só volta com evidência nova.">
      <form
        className="flex flex-wrap items-end gap-3"
        onSubmit={(e) => {
          e.preventDefault()
          if (id.trim()) load()
        }}
      >
        <div className="min-w-[16rem] flex-1">
          <TextInput label="ID do perfil" value={id} onChange={setId} mono />
        </div>
        <button type="submit" className={btnSecondary} disabled={!id.trim() || state.status === 'loading'}>
          Carregar padrões
        </button>
      </form>
      <div className="mt-4">
        {state.status === 'loading' && <Loading label="Carregando os padrões do cliente…" />}
        {state.status === 'error' && <ErrorState error={state.error} compact />}
        {state.status === 'success' && state.data && <TraitsTab traits={state.data.traits} profileId={state.data.profile_id} onChanged={load} />}
      </div>
    </Section>
  )
}
