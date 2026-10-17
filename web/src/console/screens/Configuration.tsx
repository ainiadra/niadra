import { useState, type FormEvent } from 'react'
import { useTab } from '../lib/tabs'
import { useSearchParams } from 'react-router-dom'
import type { AssistRunOut, AssistTask, ConfigType, DiffOut, DiffStatus, SecretRefOut, SecretWritten } from '../api/types'
import { ASSIST_TASKS } from '../api/types'
import { fmtDateTime } from '../lib/format'
import { isObserved, observedDocument, provenanceOf, type Provenance } from '../lib/provenance'
import { assistStatus, assistTask, CONFIG_LABELS, diffStatus, role } from '../lib/labels'
import { usePaged, useResource } from '../lib/useResource'
import { useSpace } from '../session'
import { Chip, JsonView, PageHeader, PersonName, useSources } from '../ui/bits'
import { Dialog } from '../ui/Dialog'
import { JsonField, jsonError, ReasonField, reasonOk, Section, Select, TextInput, useAction } from '../ui/forms'
import { useTitle } from '../ui/layout'
import { Empty, ErrorState, LoadMore, Loading } from '../ui/States'
import { Tabs } from '../ui/Tabs'
import { btnDanger, btnPrimary, btnQuiet, btnSecondary, card, danger, hint, input, label, mono, small, td, th } from '../ui/styles'

type TabKey = 'documents' | 'diffs' | 'history' | 'secrets' | 'assist'
const TAB_KEYS: TabKey[] = ['documents', 'diffs', 'history', 'secrets', 'assist']

/**
 * Versioned configuration: every change is a diff with
 * author, reason and approval, and the cells apply the signed snapshot. The
 * assistant only proposes diffs; a person approves them.
 */
export function Configuration() {
  const { space } = useSpace()
  useTitle('Configuração', space.environment === 'sandbox')
  // `?aba=diffs` opens on that tab: the Data screen links here after it sends a diff. `?documento=purposes`
  // opens the documents on that one: the screens that need a document point here.
  const [params] = useSearchParams()
  const [tab, setTab] = useTab<TabKey>(TAB_KEYS, 'documents')
  const document = params.get('documento')
  return (
    <>
      <PageHeader
        screen="config"
      />
      <div data-tour="config.tabs">
      <Tabs
        label="Configuração"
        active={tab}
        onChange={setTab}
        tabs={[
          { key: 'documents', label: 'Documentos' },
          { key: 'diffs', label: 'Diffs' },
          { key: 'history', label: 'Histórico' },
          { key: 'secrets', label: 'Segredos' },
          { key: 'assist', label: 'Assistente' },
        ]}
      >
        <div data-tour={`config.${tab}`}>
          {tab === 'documents' && <Documents initial={document && /^[a-z-]+$/.test(document) ? document : 'policies'} onSubmitted={() => setTab('diffs')} />}
          {tab === 'diffs' && <Diffs />}
          {tab === 'history' && <History />}
          {tab === 'secrets' && <Secrets />}
          {tab === 'assist' && <Assist />}
        </div>
      </Tabs>
      </div>
    </>
  )
}

const typeLabel = (t: string) => CONFIG_LABELS[t as ConfigType] ?? t

/** The editable document types, from GET /v1/config/types. */
function useConfigTypes() {
  const { control } = useSpace()
  return useResource((signal) => control.configTypes({ signal }), [control])
}

function Documents({ initial, onSubmitted }: { initial: ConfigType; onSubmitted: () => void }) {
  const { control, space } = useSpace()
  const types = useConfigTypes()
  const [type, setType] = useState<ConfigType>(initial)
  const doc = useResource((signal) => control.configDocument(space.space_id, type, { signal }), [type, space.space_id, control])
  const applied = useResource((signal) => control.diffs(space.space_id, 'applied', { signal }), [space.space_id, control])
  const origin = applied.status === 'success' && applied.data ? provenanceOf(type, applied.data) : null
  // What Niadra inferred is kept as `niadra:observed` diffs, never as the declared document: show it, not the
  // empty default the declared read returns in a space nobody configured.
  const observed = applied.status === 'success' && applied.data ? observedDocument(type, applied.data) : null
  const [editing, setEditing] = useState(false)
  const info = types.data?.find((t) => t.type === type)
  const options = (types.data ?? [{ type, description: '', roles: [] }]).map((t) => ({ value: t.type as ConfigType, label: typeLabel(t.type) }))
  return (
    <Section
      title={typeLabel(type)}
      description={
        <>
          {info?.description && <span className="block">{info.description}</span>}
          {info && <span className="block">Quem escreve e aprova, além da Administração: {info.roles.map(role).join(', ') || 'só a Administração'}.</span>}
          {doc.data && doc.data.version > 0 && <span className="block">Versão {doc.data.version} em vigor neste espaço.</span>}
          {origin && <OriginNote origin={origin} />}
        </>
      }
      actions={
        <div className="flex flex-wrap items-end gap-2">
          <div className="w-60">
            <Select label="Documento" value={type} onChange={setType} options={options} disabled={types.status !== 'success'} />
          </div>
          <button type="button" className={btnSecondary} onClick={() => setEditing(true)} disabled={doc.status !== 'success'}>
            Ajustar (opcional)
          </button>
        </div>
      }
    >
      {types.status === 'error' && <ErrorState error={types.error} onRetry={types.reload} compact />}
      {doc.status === 'loading' && <Loading label="Carregando o documento…" />}
      {doc.status === 'error' && <ErrorState error={doc.error} onRetry={doc.reload} compact />}
      {doc.status === 'success' && doc.data && origin?.kind === 'inferred' && observed ? (
        <JsonView value={observed.document} label={`O que a Niadra inferiu: ${typeLabel(type)}`} />
      ) : (
        doc.status === 'success' && doc.data && <JsonView value={doc.data.document} label={`Documento ${typeLabel(type)}`} />
      )}
      {origin?.kind === 'declared' && observed && (
        <details className="mt-4">
          <summary className={`${small} cursor-pointer font-semibold text-ink`}>O que a Niadra inferiu do tráfego, por baixo da declaração</summary>
          <div className="mt-2">
            <JsonView value={observed.document} label={`O que a Niadra inferiu: ${typeLabel(type)}`} />
          </div>
        </details>
      )}
      {editing && doc.data && (
        <ProposeDialog
          type={type}
          current={doc.data.document}
          onClose={() => setEditing(false)}
          onDone={() => {
            setEditing(false)
            onSubmitted()
          }}
        />
      )}
    </Section>
  )
}

/** Where the document in force came from, as a note: never a task. */
function OriginNote({ origin }: { origin: Provenance }) {
  if (origin.kind === 'default')
    return (
      <span className="mt-1 flex flex-wrap items-center gap-2">
        <Chip>Padrão da Niadra</Chip>
        <span>Funciona como está. Ajustar é opcional.</span>
      </span>
    )
  if (origin.kind === 'inferred')
    return (
      <span className="mt-1 flex flex-wrap items-center gap-2">
        <Chip>Inferido pela Niadra</Chip>
        <span>Lido do tráfego em {fmtDateTime(origin.at)}. Um ajuste seu vale por cima do que foi inferido.</span>
      </span>
    )
  return (
    <span className="mt-1 flex flex-wrap items-center gap-2">
      <Chip>Declarado</Chip>
      <span>
        Por <DiffAuthor diff={origin.diff} /> em {fmtDateTime(origin.diff.created_at)}. O que a declaração não cobre, a Niadra infere do tráfego.
      </span>
    </span>
  )
}

function ProposeDialog({ type, current, onClose, onDone }: { type: ConfigType; current: unknown; onClose: () => void; onDone: () => void }) {
  const { control, space } = useSpace()
  const [text, setText] = useState(JSON.stringify(current ?? {}, null, 2))
  const [reason, setReason] = useState('')
  const act = useAction<DiffOut>()
  const submit = (e: FormEvent) => {
    e.preventDefault()
    act.run(() => control.submitDiff(space.space_id, type, JSON.parse(text), reason.trim()), onDone)
  }
  return (
    <Dialog open onClose={onClose} title={`Ajustar: ${typeLabel(type)}`} wide>
      <form onSubmit={submit} className="grid gap-4">
        <p className={small}>Opcional: a Niadra já funciona com o padrão e com o que infere do tráfego. Um ajuste vale por cima deles.</p>
        <JsonField label="Documento proposto (JSON)" value={text} onChange={setText} rows={16} hint="O documento inteiro, como deve ficar. O plano de controle valida o esquema antes de aceitar o diff." />
        <ReasonField value={reason} onChange={setReason} />
        {Boolean(act.error) && <ErrorState error={act.error} compact />}
        <div className="flex flex-wrap justify-end gap-2">
          <button type="button" className={btnSecondary} onClick={onClose}>
            Cancelar
          </button>
          <button type="submit" className={btnPrimary} disabled={act.busy || Boolean(jsonError(text)) || !text.trim() || !reasonOk(reason)}>
            {act.busy ? 'Enviando…' : 'Enviar para aprovação'}
          </button>
        </div>
      </form>
    </Dialog>
  )
}

const DIFF_TONE: Record<DiffStatus, 'ok' | 'neutral' | 'danger'> = { applied: 'ok', pending: 'neutral', proposed: 'neutral', rejected: 'danger' }

function Diffs() {
  const { control, space } = useSpace()
  const [status, setStatus] = useState<'' | DiffStatus>('')
  const diffs = useResource((signal) => control.diffs(space.space_id, status || null, { signal }), [status, space.space_id, control])
  const [rollback, setRollback] = useState<DiffOut | null>(null)
  const [busy, setBusy] = useState<string | null>(null)
  const [error, setError] = useState<unknown>(null)

  const decide = async (d: DiffOut, how: 'approve' | 'reject') => {
    setBusy(d.diff_id)
    setError(null)
    try {
      await (how === 'approve' ? control.approveDiff(d.diff_id) : control.rejectDiff(d.diff_id))
      diffs.reload()
    } catch (e) {
      setError(e)
    } finally {
      setBusy(null)
    }
  }

  return (
    <div className="grid gap-4">
      <div className="w-full sm:w-64">
        <Select
          label="Estado"
          value={status}
          onChange={setStatus}
          options={[
            { value: '', label: 'Todos' },
            { value: 'pending', label: diffStatus('pending') },
            { value: 'proposed', label: diffStatus('proposed') },
            { value: 'applied', label: diffStatus('applied') },
            { value: 'rejected', label: diffStatus('rejected') },
          ]}
        />
      </div>
      {Boolean(error) && <ErrorState error={error} compact />}
      {diffs.status === 'loading' && <Loading label="Carregando os diffs…" />}
      {diffs.status === 'error' && <ErrorState error={diffs.error} onRetry={diffs.reload} />}
      {diffs.status === 'success' && diffs.data && diffs.data.length === 0 && <Empty title="Nenhum diff com esse estado." />}
      {diffs.status === 'success' && diffs.data && diffs.data.length > 0 && (
        <ul className="grid gap-3">
          {diffs.data.map((d) => (
            <li key={d.diff_id} className={`${card} px-4 py-3`}>
              <div className="flex flex-wrap items-baseline justify-between gap-2">
                <p className="text-ink">
                  <strong>{typeLabel(d.type)}</strong> <Chip tone={DIFF_TONE[d.status]}>{diffStatus(d.status)}</Chip>
                  {isObserved(d) && <Chip>Inferido</Chip>}
                  {d.rollback_of && <span className="ml-1 text-[0.8125rem] text-ink2">desfaz {d.rollback_of}</span>}
                </p>
                <div className="flex flex-wrap gap-1">
                  {(d.status === 'pending' || d.status === 'proposed') && (
                    <>
                      <button type="button" className={btnQuiet} disabled={busy === d.diff_id} onClick={() => decide(d, 'approve')} aria-label={`Aprovar o diff ${d.diff_id}`}>
                        Aprovar
                      </button>
                      <button type="button" className={`${btnQuiet} ${danger.text}`} disabled={busy === d.diff_id} onClick={() => decide(d, 'reject')} aria-label={`Recusar o diff ${d.diff_id}`}>
                        Recusar
                      </button>
                    </>
                  )}
                  {d.status === 'applied' && (
                    <button type="button" className={btnQuiet} onClick={() => setRollback(d)} aria-label={`Desfazer o diff ${d.diff_id}`}>
                      Desfazer
                    </button>
                  )}
                </div>
              </div>
              <p className="mt-1 text-ink">{d.reason}</p>
              <p className={`${mono} mt-1 text-ink2`}>
                <DiffAuthor diff={d} /> · {fmtDateTime(d.created_at)} · sobre a versão {d.base_version}
                {d.applied_version !== null ? ` · virou a versão ${d.applied_version}` : ''}
                {d.decided_at && (
                  <>
                    {' '}
                    · {d.status === 'rejected' ? 'recusado' : 'aprovado'}
                    {d.decided_by && (
                      <>
                        {' '}
                        por <PersonName id={d.decided_by} label={d.decided_by_label} />
                      </>
                    )}{' '}
                    em {fmtDateTime(d.decided_at)}
                  </>
                )}
              </p>
              <details className="mt-2">
                <summary className="inline-flex min-h-11 cursor-pointer items-center text-[0.8125rem] font-semibold text-ink lg:min-h-8">Documento proposto</summary>
                <div className="mt-2">
                  <JsonView value={d.document} maxHeight="max-h-80" />
                </div>
              </details>
            </li>
          ))}
        </ul>
      )}
      {rollback && (
        <RollbackDialog
          diff={rollback}
          onClose={() => setRollback(null)}
          onDone={() => {
            setRollback(null)
            diffs.reload()
          }}
        />
      )}
    </div>
  )
}

/** Who proposed the diff: the person, by the e-mail in `author_label`, or the assistant or the control plane itself. */
function DiffAuthor({ diff: d }: { diff: DiffOut }) {
  if (d.author_id) return <PersonName id={d.author_id} label={d.author_label} />
  if (isObserved(d)) return <span>Niadra, pelo tráfego</span>
  if (d.author_label === 'control_plane') return <span>Plano de controle</span>
  if (d.author_label?.startsWith('assistant@')) return <span>Assistente de configuração</span>
  if (d.author_label) return <span>{d.author_label}</span>
  return <span>autor não registrado</span>
}

function RollbackDialog({ diff, onClose, onDone }: { diff: DiffOut; onClose: () => void; onDone: () => void }) {
  const { control } = useSpace()
  const [reason, setReason] = useState('')
  const act = useAction<DiffOut>()
  return (
    <Dialog open onClose={onClose} title="Desfazer a mudança">
      <form
        className="grid gap-4"
        onSubmit={(e) => {
          e.preventDefault()
          act.run(() => control.rollbackDiff(diff.diff_id, reason.trim()), onDone)
        }}
      >
        <p className="text-ink">
          O documento {typeLabel(diff.type)} volta para a versão anterior. O desfazer também é um diff, com o seu nome e o motivo, e passa pela mesma aprovação: se outra pessoa pode aprovar este documento, ele espera por ela na lista.
        </p>
        <ReasonField value={reason} onChange={setReason} />
        {Boolean(act.error) && <ErrorState error={act.error} compact />}
        <div className="flex flex-wrap justify-end gap-2">
          <button type="button" className={btnSecondary} onClick={onClose}>
            Cancelar
          </button>
          <button type="submit" className={btnDanger} disabled={act.busy || !reasonOk(reason)}>
            {act.busy ? 'Desfazendo…' : 'Desfazer a mudança'}
          </button>
        </div>
      </form>
    </Dialog>
  )
}

function History() {
  const { control, space } = useSpace()
  const types = useConfigTypes()
  const [type, setType] = useState<'' | ConfigType>('')
  const history = useResource((signal) => control.history(space.space_id, type || null, { signal }), [type, space.space_id, control])
  return (
    <div className="grid gap-4">
      <div className="w-full sm:w-64">
        <Select label="Documento" value={type} onChange={setType} options={[{ value: '' as const, label: 'Todos' }, ...(types.data ?? []).map((t) => ({ value: t.type as ConfigType, label: typeLabel(t.type) }))]} />
      </div>
      {history.status === 'loading' && <Loading label="Carregando o histórico…" />}
      {history.status === 'error' && <ErrorState error={history.error} onRetry={history.reload} />}
      {history.status === 'success' && history.data && history.data.length === 0 && <Empty title="Nenhuma versão ainda." />}
      {history.status === 'success' && history.data && history.data.length > 0 && (
        <ol className={`${card} divide-y divide-line2`}>
          {history.data.map((v) => (
            <li key={`${v.type}:${v.version}`} className="px-4 py-3">
              <p className="text-ink">
                <strong>
                  {typeLabel(v.type)}, versão {v.version}
                </strong>
              </p>
              <p className={`${mono} mt-0.5 text-ink2`}>
                {fmtDateTime(v.created_at)} · diff {v.diff_id}
              </p>
              <details className="mt-2">
                <summary className="inline-flex min-h-11 cursor-pointer items-center text-[0.8125rem] font-semibold text-ink lg:min-h-8">Documento desta versão</summary>
                <div className="mt-2">
                  <JsonView value={v.document} maxHeight="max-h-80" />
                </div>
              </details>
            </li>
          ))}
        </ol>
      )}
    </div>
  )
}

function Secrets() {
  const { control, cell, space, hasRole } = useSpace()
  const refs = useResource((signal) => control.secretRefs(space.space_id, { signal }), [space.space_id, control])
  const [kind, setKind] = useState<'webhook' | 'model'>('webhook')
  const [id, setId] = useState('')
  const [value, setValue] = useState('')
  const act = useAction<{ written: SecretWritten; ref: SecretRefOut | null; refError: unknown }>()
  const submit = (e: FormEvent) => {
    e.preventDefault()
    const secret = value
    setValue('')
    act.run(async () => {
      const written = await cell.putSecret(kind, id.trim(), secret)
      try {
        const ref = await control.putSecretRef(space.space_id, id.trim(), kind, written.version)
        return { written, ref, refError: null }
      } catch (refError) {
        return { written, ref: null, refError }
      }
    }, refs.reload)
  }
  return (
    <div className="grid gap-6">
      <Section title="Segredos registrados" description="O plano de controle guarda só qual segredo existe, a versão e quem girou. O valor fica no cofre da região e nunca volta para a tela.">
        {refs.status === 'loading' && <Loading label="Carregando os segredos…" />}
        {refs.status === 'error' && <ErrorState error={refs.error} onRetry={refs.reload} compact />}
        {refs.status === 'success' && refs.data && refs.data.length === 0 && <Empty title="Nenhum segredo registrado." />}
        {refs.status === 'success' && refs.data && refs.data.length > 0 && (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[36rem] border-collapse">
              <thead>
                <tr>
                  <th scope="col" className={th}>Referência</th>
                  <th scope="col" className={th}>Tipo</th>
                  <th scope="col" className={`${th} text-right`}>Versão</th>
                  <th scope="col" className={th}>Girado</th>
                </tr>
              </thead>
              <tbody>
                {refs.data.map((r) => (
                  <tr key={r.ref}>
                    <td className={`${td} ${mono}`}>{r.ref}</td>
                    <td className={td}>{r.kind === 'webhook' ? 'Assinatura de webhook' : r.kind === 'collector_token' ? 'Token do coletor do SIEM' : r.kind === 'model' ? 'Credencial de modelo' : r.kind === 'export' ? 'Destino de exportação' : r.kind}</td>
                    <td className={`${td} text-right tabular-nums`}>{r.version}</td>
                    <td className={`${td} text-[0.8125rem]`}>
                      {fmtDateTime(r.rotated_at)}
                      <span className={`${mono} block text-ink2`}>{r.rotated_by}</span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Section>

      <Section title="Gravar ou girar um segredo" description="A assinatura dos webhooks (dois valores valem durante a rotação) ou a credencial do modelo de IA da empresa.">
        {hasRole('security', 'integration') ? (
          <form onSubmit={submit} className="grid gap-4">
            <div className="grid gap-3 sm:grid-cols-2">
              <Select
                label="Tipo"
                value={kind}
                onChange={setKind}
                options={[
                  { value: 'webhook', label: 'Assinatura de webhook' },
                  { value: 'model', label: 'Credencial de modelo' },
                ]}
              />
              <TextInput label="Referência" value={id} onChange={setId} mono hint="O nome usado na configuração, como em secret_refs do endpoint." />
            </div>
            <div>
              <label htmlFor="secret-value" className={label}>
                Valor
              </label>
              <textarea id="secret-value" className={`${input} h-auto min-h-20 py-2 font-mono`} value={value} onChange={(e) => setValue(e.target.value)} autoComplete="off" spellCheck={false} />
              <p className={hint}>Vai só para o cofre da região. Depois de gravado, não aparece de novo.</p>
            </div>
            {Boolean(act.error) && <ErrorState error={act.error} compact />}
            {act.result && (
              <p role="status" className="rounded-lg border border-accent/40 bg-accent-wash px-3 py-2.5 text-[0.875rem] text-ink">
                <strong>Gravado no cofre, versão {act.result.written.version}.</strong>{' '}
                {act.result.ref ? 'A versão foi registrada no plano de controle.' : 'O plano de controle não registrou a versão: grave de novo para registrar.'}
              </p>
            )}
            <div>
              <button type="submit" className={btnPrimary} disabled={act.busy || !id.trim() || !value}>
                {act.busy ? 'Gravando…' : 'Gravar o segredo'}
              </button>
            </div>
          </form>
        ) : (
          <p className={small}>Exige o papel Segurança e privacidade ou Integração.</p>
        )}
      </Section>
    </div>
  )
}

/** The example must be one JSON object: the assistant maps an event, not a list or a value. */
function jsonObjectError(text: string): string | null {
  const problem = jsonError(text)
  if (problem) return problem
  const value: unknown = JSON.parse(text)
  return value !== null && typeof value === 'object' && !Array.isArray(value) ? null : 'O evento de exemplo precisa ser um objeto JSON, entre chaves.'
}

function Assist() {
  const { cell, hasRole } = useSpace()
  const sources = useSources()
  const [task, setTask] = useState<AssistTask>('webhook_mapping')
  const [sourceId, setSourceId] = useState('')
  const [description, setDescription] = useState('')
  const [example, setExample] = useState('')
  const act = useAction<AssistRunOut>()
  const runs = usePaged((cursor, signal) => cell.assistRuns(cursor, { signal }), [cell])
  const needsSource = task !== 'trigger_or_trait_rule'
  const needsDescription = task === 'source_policy' || task === 'trigger_or_trait_rule'
  const chosenSource = sourceId || sources.list[0]?.source_id || ''
  const withExample = task === 'webhook_mapping'
  const exampleProblem = withExample && example.trim() ? jsonObjectError(example) : null
  const ready = (!needsSource || chosenSource) && (!needsDescription || description.trim().length >= 3) && !exampleProblem

  return (
    <div className="grid gap-6">
      <Section title="Pedir uma proposta" description="O assistente lê amostras de eventos deste espaço e propõe um documento de configuração. A proposta vira um diff que uma pessoa aprova; a amostra vai mascarada, sem retenção, ao provedor de IA listado no contrato.">
        {hasRole('integration') ? (
          <form
            className="grid gap-4"
            onSubmit={(e) => {
              e.preventDefault()
              act.run(
                () =>
                  cell.assist({
                    task,
                    ...(needsSource ? { source_id: chosenSource } : {}),
                    ...(description.trim() ? { description: description.trim() } : {}),
                    ...(withExample && example.trim() ? { example_payload: JSON.parse(example) as Record<string, unknown> } : {}),
                  }),
                runs.reload,
              )
            }}
          >
            <div className="grid gap-3 sm:grid-cols-2">
              <Select label="O que propor" value={task} onChange={setTask} options={ASSIST_TASKS.map((t) => ({ value: t, label: assistTask(t) }))} />
              {needsSource && (
                <Select
                  label="Fonte"
                  value={chosenSource}
                  onChange={setSourceId}
                  disabled={sources.list.length === 0}
                  options={sources.list.length === 0 ? [{ value: '', label: 'Nenhuma fonte' }] : sources.list.map((s) => ({ value: s.source_id, label: s.name }))}
                />
              )}
            </div>
            <div>
              <label htmlFor="assist-desc" className={label}>
                {needsDescription ? 'Descrição' : 'Descrição (opcional)'}
              </label>
              <textarea id="assist-desc" className={`${input} h-auto min-h-20 py-2`} value={description} onChange={(e) => setDescription(e.target.value)} maxLength={2000} />
              <p className={hint}>Para que serve o agente, ou a regra em palavras. Dado pessoal é mascarado antes de qualquer uso.</p>
            </div>
            {withExample && (
              <JsonField
                label="Evento de exemplo (opcional)"
                value={example}
                onChange={setExample}
                rows={8}
                hint="Cole um evento do sistema como ele vai chegar, para uma fonte que ainda não mandou nenhum. Ele é mascarado na chegada: ficam as chaves e o tipo do evento; nomes, contatos, documentos e números saem antes de qualquer uso, e só a cópia mascarada é guardada."
              />
            )}
            {exampleProblem && <p className={`${small} font-semibold`}>{exampleProblem}</p>}
            {Boolean(act.error) && <ErrorState error={act.error} compact />}
            <div>
              <button type="submit" className={btnPrimary} disabled={act.busy || !ready}>
                {act.busy ? 'Pedindo…' : 'Pedir a proposta'}
              </button>
            </div>
          </form>
        ) : (
          <p className={small}>Exige o papel Integração.</p>
        )}
      </Section>

      <Section
        title="Propostas"
        actions={
          <button type="button" className={btnQuiet} onClick={runs.reload}>
            Atualizar
          </button>
        }
      >
        {(runs.status === 'loading' || runs.status === 'idle') && <Loading label="Carregando as propostas…" />}
        {runs.status === 'error' && <ErrorState error={runs.error} onRetry={runs.reload} compact />}
        {runs.status === 'success' && runs.items.length === 0 && <Empty title="Nenhuma proposta do assistente neste espaço." />}
        {runs.status === 'success' && runs.items.length > 0 && (
          <>
            <ul className="grid gap-3">
              {runs.items.map((r) => (
                <li key={r.run_id} className={`${card} px-4 py-3`}>
                  <p className="text-ink">
                    <strong>{assistTask(r.task)}</strong> <Chip tone={r.status === 'failed' || r.status === 'rejected' ? 'danger' : r.status === 'delivered' ? 'ok' : 'neutral'}>{assistStatus(r.status)}</Chip>
                  </p>
                  <p className={`${mono} mt-0.5 text-ink2`}>
                    {r.run_id} · {fmtDateTime(r.created_at)}
                    {r.diff_id ? ` · diff ${r.diff_id}, veja em Diffs` : ''}
                  </p>
                  {r.problems.length > 0 && (
                    <ul className="mt-2 grid gap-1">
                      {r.problems.map((p) => (
                        <li key={p} className={`text-[0.8125rem] ${danger.text}`}>
                          {p}
                        </li>
                      ))}
                    </ul>
                  )}
                  {r.document !== null && r.document !== undefined && (
                    <details className="mt-2">
                      <summary className="inline-flex min-h-11 cursor-pointer items-center text-[0.8125rem] font-semibold text-ink lg:min-h-8">
                        Documento proposto{r.config_type ? `: ${typeLabel(r.config_type)}` : ''}
                      </summary>
                      <div className="mt-2 grid gap-2">
                        <JsonView value={r.document} maxHeight="max-h-80" />
                        {Object.keys(r.checks).length > 0 && <JsonView value={r.checks} label="Validação contra as amostras" maxHeight="max-h-48" />}
                      </div>
                    </details>
                  )}
                </li>
              ))}
            </ul>
            <LoadMore hasMore={runs.hasMore} loading={runs.loadingMore} error={runs.moreError} onClick={runs.loadMore} />
          </>
        )}
      </Section>
    </div>
  )
}
