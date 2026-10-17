import { useEffect, useId, useRef, useState, type FormEvent, type ReactNode } from 'react'
import { useTab } from '../lib/tabs'
import { Link } from 'react-router-dom'
import { ApiError } from '../api/http'
import type { DiffOut, ErasureOut, ExportPackageOut, ExportRunOut, ExportScheduleDoc, ForgetTarget, HandleIn, PurposesDoc, Role, SecretRefOut, SecretWritten } from '../api/types'
import { fmtDateTime, fmtInt, shortHash } from '../lib/format'
import { CONFIG_LABELS, erasureStatus, exportRunStatus, legalBasis, role } from '../lib/labels'
import { usePaged, useResource } from '../lib/useResource'
import { paths } from '../paths'
import { useSpace } from '../session'
import { Chip, PageHeader, Props } from '../ui/bits'
import { Dialog } from '../ui/Dialog'
import { cleanHandle, emptyHandle, Field, handleOk, HandleInput, Section, Select, TextInput, useAction } from '../ui/forms'
import { Icon } from '../ui/Icon'
import { useTitle } from '../ui/layout'
import { Empty, ErrorState, LoadMore, Loading } from '../ui/States'
import { Tabs } from '../ui/Tabs'
import { btnDanger, btnPrimary, btnQuiet, btnSecondary, card, danger, hint, input, label, mono, small, td, th } from '../ui/styles'

type TabKey = 'forget' | 'export' | 'continuous'

/**
 * Data-subject rights and exports. Receipts,
 * the other half of "who read what", live in the Receipts screen.
 */
export function Data() {
  const { space, hasRole } = useSpace()
  useTitle('Dados', space.environment === 'sandbox')
  const [tab, setTab] = useTab<TabKey>(['forget', 'export', 'continuous'], 'forget')
  return (
    <>
      <PageHeader
        screen="data"
      />
      {!hasRole('security') && <p className={`${small} mb-4`}>As ações desta tela exigem o papel Segurança e privacidade.</p>}
      <div data-tour="data.tabs">
        <Tabs
          label="Dados"
          active={tab}
          onChange={setTab}
          tabs={[
            { key: 'forget', label: 'Apagamento' },
            { key: 'export', label: 'Arquivo do titular' },
            { key: 'continuous', label: 'Exportação contínua' },
          ]}
        >
          <div data-tour={`data.${tab}`}>
            {tab === 'forget' && <Forget />}
            {tab === 'export' && <ExportPackage />}
            {tab === 'continuous' && <Continuous />}
          </div>
        </Tabs>
      </div>
    </>
  )
}

const ERASURE_TONE: Record<ErasureOut['status'], 'ok' | 'neutral' | 'danger'> = { completed: 'ok', pending: 'neutral', running: 'neutral', failed: 'danger' }

function ErasureRow({ e }: { e: ErasureOut }) {
  const erased = Object.entries(e.erased)
  return (
    <li className="px-4 py-3">
      <p className="flex flex-wrap items-baseline gap-2 text-ink">
        <Chip tone={ERASURE_TONE[e.status]}>{erasureStatus(e.status)}</Chip>
        <span>{e.target_kind === 'profile' ? 'Perfil' : e.target_kind === 'handle' ? 'Identificador' : 'Conversa'}</span>
        <span className={mono}>{e.target_id}</span>
      </p>
      <p className={`${small} mt-1`}>
        pedido em {fmtDateTime(e.requested_at)}
        {e.completed_at ? ` · concluído em ${fmtDateTime(e.completed_at)}` : ''} · <span className={mono}>{e.request_id}</span>
      </p>
      {erased.length > 0 && <p className={`${mono} mt-1 text-ink2`}>{erased.map(([table, n]) => `${table}: ${fmtInt(n)}`).join(' · ')}</p>}
      {e.export_run_ids.length > 0 && <p className={`${small} mt-1`}>Já tinha saído nas execuções de exportação {e.export_run_ids.join(', ')}: o apagamento segue para o bucket da empresa.</p>}
      {e.receipt_hash && <p className={`${mono} mt-1 text-ink2`}>comprovante do apagamento {shortHash(e.receipt_hash, 24)}</p>}
    </li>
  )
}

function Forget() {
  const { cell, hasRole } = useSpace()
  const [target, setTarget] = useState<ForgetTarget>('profile')
  const [profileId, setProfileId] = useState('')
  const [handle, setHandle] = useState<HandleIn>(emptyHandle())
  const [conversation, setConversation] = useState('')
  const [sure, setSure] = useState(false)
  const act = useAction<ErasureOut>()
  const [status, setStatus] = useState<'' | ErasureOut['status']>('')
  const page = usePaged((cursor, signal) => cell.erasures({ status: status || undefined, cursor }, { signal }), [status, cell])

  const ready = sure && (target === 'profile' ? profileId.trim() : target === 'handle' ? handleOk(handle) : conversation.trim())
  const submit = (e: FormEvent) => {
    e.preventDefault()
    const body =
      target === 'profile'
        ? { target, profile_id: profileId.trim() }
        : target === 'handle'
          ? { target, handle: cleanHandle(handle) }
          : { target, conversation_id: conversation.trim() }
    act.run(
      () => cell.forget(body),
      () => {
        setSure(false)
        page.reload()
      },
    )
  }

  return (
    <div className="grid gap-6">
      <Section tour="data.forget" title="Apagar um cliente" description="O apagamento segue a linhagem: evento, menções, fatos sem outra evidência, contextos, vetores, arquivos, objetos, ações, padrões, vínculos e o que já saiu na exportação. Fica uma lápide mínima e um comprovante.">
        {hasRole('security') ? (
          <form onSubmit={submit} className="grid gap-4">
            <div className="sm:w-72">
              <Select
                label="O que apagar"
                value={target}
                onChange={setTarget}
                options={[
                  { value: 'profile', label: 'Um perfil inteiro' },
                  { value: 'handle', label: 'Um identificador e o que veio por ele' },
                  { value: 'conversation', label: 'Uma conversa' },
                ]}
              />
            </div>
            {target === 'profile' && <TextInput label="ID do perfil" value={profileId} onChange={setProfileId} mono />}
            {target === 'handle' && <HandleInput legend="Identificador" value={handle} onChange={setHandle} />}
            {target === 'conversation' && <TextInput label="ID da conversa" value={conversation} onChange={setConversation} mono />}
            <label className="flex min-h-11 cursor-pointer items-center gap-2.5 text-[0.875rem] text-ink">
              <input type="checkbox" className="h-5 w-5 accent-[#5e7c00]" checked={sure} onChange={(e) => setSure(e.target.checked)} />
              Entendo que o apagamento não se desfaz
            </label>
            {Boolean(act.error) && <ErrorState error={act.error} compact />}
            {act.result && (
              <p role="status" className="rounded-lg border border-accent/40 bg-accent-wash px-3 py-2.5 text-[0.875rem] text-ink">
                <strong>Pedido registrado.</strong> <span className={mono}>{act.result.request_id}</span> · {erasureStatus(act.result.status)}
              </p>
            )}
            <div>
              <button type="submit" className={btnDanger} disabled={act.busy || !ready}>
                {act.busy ? 'Pedindo…' : 'Pedir o apagamento'}
              </button>
            </div>
          </form>
        ) : (
          <p className={small}>Exige o papel Segurança e privacidade.</p>
        )}
      </Section>

      <Section
        tour="data.requests"
        title="Pedidos de apagamento"
        actions={
          <div className="flex items-end gap-2">
            <div className="w-48">
              <Select
                label="Estado"
                value={status}
                onChange={setStatus}
                options={[
                  { value: '', label: 'Todos' },
                  { value: 'pending', label: erasureStatus('pending') },
                  { value: 'running', label: erasureStatus('running') },
                  { value: 'completed', label: erasureStatus('completed') },
                  { value: 'failed', label: erasureStatus('failed') },
                ]}
              />
            </div>
            <button type="button" className={btnQuiet} onClick={page.reload}>
              Atualizar
            </button>
          </div>
        }
      >
        {(page.status === 'loading' || page.status === 'idle') && <Loading label="Carregando os pedidos…" />}
        {page.status === 'error' && <ErrorState error={page.error} onRetry={page.reload} compact />}
        {page.status === 'success' && page.items.length === 0 && <Empty title="Nenhum pedido de apagamento com esse estado." />}
        {page.status === 'success' && page.items.length > 0 && (
          <>
            <ul className={`${card} divide-y divide-line2`}>
              {page.items.map((e) => (
                <ErasureRow key={e.request_id} e={e} />
              ))}
            </ul>
            <LoadMore hasMore={page.hasMore} loading={page.loadingMore} error={page.moreError} onClick={page.loadMore} />
          </>
        )}
      </Section>
      {hasRole('security') && <Objection onDone={page.reload} />}
      {hasRole('security') && <StuckWork onReplayed={page.reload} />}
    </div>
  )
}

/**
 * The data subject's objection to one purpose (13, 6.6; LGPD art. 18, GDPR arts. 7(3) and 21): what memory
 * collected only for it is deleted, an item held for other purposes too keeps them, and what is written later
 * leaves it out. Whether the objection applies is the company's decision; the cell carries it out at once.
 */
function Objection({ onDone }: { onDone: () => void }) {
  const { cell, control, space } = useSpace()
  const doc = useResource((signal) => control.configDocument(space.space_id, 'purposes', { signal }), [space.space_id, control])
  const [target, setTarget] = useState<'profile' | 'handle'>('profile')
  const [profileId, setProfileId] = useState('')
  const [handle, setHandle] = useState<HandleIn>(emptyHandle())
  const [purpose, setPurpose] = useState('')
  const [sure, setSure] = useState(false)
  const act = useAction<ErasureOut>()
  const declared = ((doc.data?.document as PurposesDoc | undefined)?.purposes ?? []).filter((p) => p.name)
  const ready = sure && purpose && (target === 'profile' ? profileId.trim() : handleOk(handle))

  const submit = (e: FormEvent) => {
    e.preventDefault()
    const body = target === 'profile' ? { target, profile_id: profileId.trim(), purpose } : { target, handle: cleanHandle(handle), purpose }
    act.run(
      () => cell.forget(body),
      () => {
        setSure(false)
        onDone()
      },
    )
  }

  return (
    <Section
      title="Oposição a uma finalidade"
      description="O titular se opõe a uma finalidade ou retira o consentimento dela. Sai o que a memória colheu só para essa finalidade; um item que também serve a outra fica, sem ela. O que chegar depois já vem sem essa finalidade. As conversas ficam como registro."
    >
      {(doc.status === 'loading' || doc.status === 'idle') && <Loading label="Lendo as finalidades do espaço…" />}
      {doc.status === 'error' && <ErrorState error={doc.error} onRetry={doc.reload} compact />}
      {doc.status === 'success' && declared.length === 0 && (
        <Empty
          title="A oposição precisa de uma finalidade declarada."
          action={
            <Link to={`${paths.config(space.space_id)}?documento=purposes`} className={btnSecondary}>
              Declarar as finalidades em Configuração
            </Link>
          }
          example={<>Um perfil · finalidade vendas, interesse legítimo · saem 9 fatos e 2 padrões colhidos só para vendas; 3 fatos ficam, sem essa finalidade</>}
        >
          O titular se opõe a uma finalidade pelo nome, e cada item da memória sai ou fica conforme a finalidade que carrega. Este espaço ainda não declara nenhuma no documento {CONFIG_LABELS.purposes}, então os itens não carregam finalidade nem base legal e não há a que se opor. A mudança entra como diff e vale depois de aprovada.
        </Empty>
      )}
      {doc.status === 'success' && declared.length > 0 && (
        <form onSubmit={submit} className="grid gap-4" aria-label="Oposição a uma finalidade">
          <div className="grid gap-4 sm:grid-cols-2">
            <Select
              label="Quem se opõe"
              value={target}
              onChange={setTarget}
              options={[
                { value: 'profile', label: 'Um perfil' },
                { value: 'handle', label: 'Um identificador' },
              ]}
            />
            <Select
              label="Finalidade"
              value={purpose}
              onChange={setPurpose}
              options={[{ value: '', label: 'Escolha a finalidade' }, ...declared.map((p) => ({ value: p.name, label: `${p.name} · ${legalBasis(p.legal_basis)}` }))]}
            />
          </div>
          {target === 'profile' ? <TextInput label="ID do perfil" value={profileId} onChange={setProfileId} mono /> : <HandleInput legend="Identificador" value={handle} onChange={setHandle} />}
          <label className="flex min-h-11 cursor-pointer items-center gap-2.5 text-[0.875rem] text-ink">
            <input type="checkbox" className="h-5 w-5 accent-[#5e7c00]" checked={sure} onChange={(e) => setSure(e.target.checked)} />
            Entendo que o que foi colhido só para essa finalidade sai de vez
          </label>
          {Boolean(act.error) && <ErrorState error={act.error} compact />}
          {act.result && (
            <p role="status" className="rounded-lg border border-accent/40 bg-accent-wash px-3 py-2.5 text-[0.875rem] text-ink">
              <strong>Oposição registrada.</strong> {objectionSummary(act.result.erased)}
            </p>
          )}
          <div>
            <button type="submit" className={btnDanger} disabled={act.busy || !ready}>
              {act.busy ? 'Registrando…' : 'Registrar a oposição'}
            </button>
          </div>
        </form>
      )}
    </Section>
  )
}

function objectionSummary(erased: Record<string, number>): string {
  const narrowed = erased.purpose_narrowed ?? 0
  const removed = Object.entries(erased)
    .filter(([table]) => table !== 'purpose_narrowed')
    .reduce((sum, [, n]) => sum + n, 0)
  return `${fmtInt(removed)} registros apagados · ${fmtInt(narrowed)} itens continuam para outras finalidades.`
}

/** What the name of a buried task means to the person who replays it. */
const TASK_LABELS: Record<string, string> = {
  erase: 'Apagamento de um titular',
  erase_cold: 'Apagamento no arquivo frio',
  export_run: 'Exportação contínua',
  extract: 'Extração de uma conversa',
  compile: 'Compilação do contexto',
  deliver_webhook: 'Entrega de webhook',
}

/**
 * Work that exhausted its attempts (a region away, a destination refusing). A failed erasure is resumed
 * here: every step of it is idempotent, so sending it again finishes what stopped.
 */
function StuckWork({ onReplayed }: { onReplayed: () => void }) {
  const { cell } = useSpace()
  const dead = useResource((signal) => cell.deadTasks({ signal }), [cell])
  const act = useAction<{ replayed: number }>()
  const counts = new Map<string, number>()
  for (const t of dead.data?.items ?? []) counts.set(t.name, (counts.get(t.name) ?? 0) + 1)
  return (
    <Section
      title="Tarefas que esgotaram as tentativas"
      description="Trabalho deste espaço que falhou todas as vezes, como um apagamento que parou no meio. Mandar de novo retoma de onde parou, e a ação fica num comprovante."
    >
      {(dead.status === 'loading' || dead.status === 'idle') && <Loading label="Carregando as tarefas…" />}
      {dead.status === 'error' && <ErrorState error={dead.error} onRetry={dead.reload} compact />}
      {dead.status === 'success' && counts.size === 0 && <Empty title="Nenhuma tarefa parada neste espaço." />}
      {counts.size > 0 && (
        <ul className={`${card} divide-y divide-line2`} aria-label="Tarefas paradas">
          {[...counts.entries()].map(([name, n]) => (
            <li key={name} className="flex flex-wrap items-center justify-between gap-3 px-4 py-3">
              <span className="text-ink">
                {TASK_LABELS[name] ?? name} <span className={`${mono} text-ink2`}>({n})</span>
              </span>
              <button
                type="button"
                className={btnSecondary}
                disabled={act.busy}
                onClick={() =>
                  act.run(
                    () => cell.replayDeadTasks(name),
                    () => {
                      dead.reload()
                      onReplayed()
                    },
                  )
                }
              >
                Mandar de novo
              </button>
            </li>
          ))}
        </ul>
      )}
      {act.result && (
        <p role="status" className={`${small} mt-3`}>
          {act.result.replayed === 1 ? '1 tarefa voltou para a fila.' : `${act.result.replayed} tarefas voltaram para a fila.`}
        </p>
      )}
      {Boolean(act.error) && (
        <div className="mt-3">
          <ErrorState error={act.error} compact />
        </div>
      )}
    </Section>
  )
}

/**
 * A package holds the subject's data decrypted, so it lives for a day (app/privacy.py PACKAGE_TTL): then the
 * download answers 404 and the retention sweep deletes it. An erasure that reaches the subject deletes its
 * packages at once.
 */
const PACKAGE_LIFE =
  'O arquivo vale por 24 horas: depois disso, o download para de funcionar e o arquivo sai do armazenamento. Um apagamento que toca o titular apaga na hora os arquivos dele.'

/** The runs of the packages asked for share this schedule id (domain/privacy/export.py ON_DEMAND). */
const ON_DEMAND = 'on_demand'

/** Where a package asked for stands, from its run's manifest: deleted, past its day, or still downloadable. */
function packageState(manifest: ExportRunOut['manifest'], now = Date.now()): string {
  const deleted = typeof manifest?.deleted_at === 'string' ? manifest.deleted_at : null
  if (deleted) return `apagado em ${fmtDateTime(deleted)}`
  const expires = typeof manifest?.expires_at === 'string' ? manifest.expires_at : null
  if (!expires) return 'vale por 24 horas'
  return new Date(expires).getTime() <= now ? `expirou em ${fmtDateTime(expires)}` : `disponível até ${fmtDateTime(expires)}`
}

function ExportPackage() {
  const { cell, hasRole } = useSpace()
  const [by, setBy] = useState<'profile' | 'handle'>('profile')
  const [profileId, setProfileId] = useState('')
  const [handle, setHandle] = useState<HandleIn>(emptyHandle())
  const act = useAction<ExportPackageOut>()
  const download = useAction<Blob>()
  const ready = by === 'profile' ? profileId.trim() : handleOk(handle)

  const save = (runId: string) =>
    download.run(
      () => cell.downloadExport(runId),
      (blob) => {
        const url = URL.createObjectURL(blob)
        const a = document.createElement('a')
        a.href = url
        a.download = `export-${runId}.zip`
        a.click()
        setTimeout(() => URL.revokeObjectURL(url), 1000)
      },
    )

  const r = act.result
  return (
    <Section tour="data.package" title="Arquivo com os dados de um cliente" description={`Tudo o que a memória guarda sobre o titular, em JSON por tipo de registro, num arquivo zip com o SHA-256 de cada parte. ${PACKAGE_LIFE}`}>
      {hasRole('security') ? (
        <form
          className="grid gap-4"
          onSubmit={(e) => {
            e.preventDefault()
            act.run(() => cell.exportPackage(by === 'profile' ? { profile_id: profileId.trim() } : { handle: cleanHandle(handle) }))
          }}
        >
          <div className="sm:w-72">
            <Select
              label="Encontrar o titular por"
              value={by}
              onChange={setBy}
              options={[
                { value: 'profile', label: 'ID do perfil' },
                { value: 'handle', label: 'Identificador' },
              ]}
            />
          </div>
          {by === 'profile' ? <TextInput label="ID do perfil" value={profileId} onChange={setProfileId} mono /> : <HandleInput legend="Identificador" value={handle} onChange={setHandle} />}
          {Boolean(act.error) && <ErrorState error={act.error} compact />}
          <div>
            <button type="submit" className={btnPrimary} disabled={act.busy || !ready}>
              {act.busy ? 'Montando o arquivo…' : 'Montar o arquivo'}
            </button>
          </div>
        </form>
      ) : (
        <p className={small}>Exige o papel Segurança e privacidade.</p>
      )}
      {r && (
        <div className="mt-5 grid gap-3 border-t border-line pt-4" role="status">
          <p className="text-ink">
            <strong>Arquivo pronto.</strong> Perfil <span className={mono}>{r.profile_id}</span> · execução <span className={mono}>{r.run_id}</span> · vale por 24 horas a partir de agora
          </p>
          <p className={`${mono} break-all text-ink2`}>SHA-256 do zip {r.sha256}</p>
          <ul className="grid gap-1">
            {Object.entries(r.files).map(([name, sha]) => (
              <li key={name} className={`${mono} text-ink2`}>
                {name} · {shortHash(sha, 16)}
              </li>
            ))}
          </ul>
          <div className="flex flex-wrap items-center gap-3">
            <button type="button" className={btnSecondary} onClick={() => save(r.run_id)} disabled={download.busy}>
              <Icon name="download" size={16} />
              {download.busy ? 'Baixando…' : 'Baixar o arquivo'}
            </button>
            {Boolean(download.error) && (
              <span className={`text-[0.8125rem] font-semibold ${danger.text}`}>
                {download.error instanceof ApiError && download.error.code === 'not_found'
                  ? 'O arquivo não existe mais: passou das 24 horas ou saiu com um apagamento do titular. Monte um novo.'
                  : 'O download falhou. Tente de novo.'}
              </span>
            )}
          </div>
          <p className={hint}>O arquivo vem pela sua sessão. O link do arquivo não abre sem ela.</p>
        </div>
      )}
    </Section>
  )
}

/** config/models.py ExportSchedule, with the S3 endpoint the shared type leaves out. */
type Schedule = ExportScheduleDoc & { endpoint_url?: string | null }

/** The tables the continuous export writes, in the server's order (domain/privacy/export.py EXPORT_TABLES). */
const EXPORT_TABLES: { name: string; label: string }[] = [
  { name: 'events', label: 'Eventos' },
  { name: 'conversations', label: 'Conversas' },
  { name: 'episodes', label: 'Episódios' },
  { name: 'facts', label: 'Fatos' },
  { name: 'fact_mentions', label: 'Menções de fatos' },
  { name: 'open_items', label: 'Pendências e promessas' },
  { name: 'business_objects', label: 'Objetos de negócio' },
  { name: 'object_events', label: 'Eventos dos objetos' },
  { name: 'agent_actions', label: 'Ações de agentes' },
  { name: 'traits', label: 'Padrões' },
  { name: 'trait_evidence', label: 'Evidências dos padrões' },
  { name: 'receipts', label: 'Comprovantes' },
  { name: 'context_use', label: 'Aproveitamento do contexto' },
  { name: 'handles', label: 'Identificadores' },
  { name: 'profile_membership', label: 'Identificadores de cada perfil' },
  { name: 'profiles', label: 'Perfis' },
  { name: 'profile_links', label: 'Vínculos' },
  { name: 'org_parents', label: 'Matrizes e subsidiárias' },
  { name: 'assertions', label: 'Asserções de identidade' },
  { name: 'merge_log', label: 'Uniões e separações' },
]
const TABLE_NAMES = EXPORT_TABLES.map((t) => t.name)

/** Who may write the document besides the Administration, in words: "o papel Segurança e privacidade". */
const writersText = (writers: Role[]) => (writers.length > 0 ? `o papel ${writers.map(role).join(' ou ')}` : 'a Administração')

function Continuous() {
  const { cell, control, space, hasRole } = useSpace()
  const schedules = useResource((signal) => control.configDocument(space.space_id, 'export-schedules', { signal }), [space.space_id, control])
  const types = useResource((signal) => control.configTypes({ signal }), [control])
  const list = Array.isArray(schedules.data?.document) ? (schedules.data!.document as Schedule[]) : []
  // The control plane says who writes the document besides the Administration (app/control_config.py);
  // until the list arrives, or if it fails, the Console assumes Security, and the server decides anyway.
  const writers: Role[] = types.data?.find((t) => t.type === 'export-schedules')?.roles ?? ['security']
  const canPropose = hasRole(...writers)
  const [scheduleFilter, setScheduleFilter] = useState('')
  const runs = usePaged((cursor, signal) => cell.exportRuns({ schedule_id: scheduleFilter || undefined, cursor }, { signal }), [scheduleFilter, cell])
  const [credFor, setCredFor] = useState<Schedule | null>(null)
  const [creating, setCreating] = useState(false)
  const create = canPropose ? (
    <button type="button" className={btnPrimary} onClick={() => setCreating(true)}>
      Novo agendamento
    </button>
  ) : undefined

  return (
    // One track of minmax(0, 1fr): the wide tables scroll inside their box instead of widening the page on a phone.
    <div className="grid grid-cols-1 gap-6">
      <Section
        tour="data.schedules"
        title="Destinos"
        description="Cada agendamento escreve Parquet incremental no bucket da empresa. A credencial vai direto para o cofre da região e nunca volta para a tela."
        actions={schedules.status === 'success' && list.length > 0 ? create : undefined}
      >
        {schedules.status === 'loading' && <Loading label="Carregando os agendamentos…" />}
        {schedules.status === 'error' && <ErrorState error={schedules.error} onRetry={schedules.reload} compact />}
        {schedules.status === 'success' && list.length === 0 && (
          <Empty
            title="Nenhuma exportação contínua configurada."
            action={create}
            example={
              <>
                <span className={mono}>bi-vendas</span> · S3, bucket <span className={mono}>empresa-dados</span>, prefixo <span className={mono}>niadra/</span> · a cada 24 horas · eventos, conversas, fatos e desfechos, em Parquet · pseudonimizada
              </>
            }
          >
            A exportação contínua escreve Parquet incremental no bucket da empresa, na região do espaço, a cada execução. Ela precisa de um bucket que já exista, de uma credencial com permissão de escrita nele, que vai direto para o cofre da região, e da lista de tabelas.{' '}
            {canPropose
              ? 'O agendamento entra como mudança de configuração e começa a exportar depois de aprovado em Configuração.'
              : `Criar um agendamento exige ${writersText(writers)}.`}
          </Empty>
        )}
        {schedules.status === 'success' && list.length > 0 && (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[52rem] border-collapse">
              <caption className="sr-only">Agendamentos de exportação contínua</caption>
              <thead>
                <tr>
                  <th scope="col" className={th}>Agendamento</th>
                  <th scope="col" className={th}>Destino</th>
                  <th scope="col" className={th}>Tabelas</th>
                  <th scope="col" className={th}>A cada</th>
                  <th scope="col" className={th}>Credencial</th>
                  {hasRole('security') && <th scope="col" className={`${th} text-right`}>Ação</th>}
                </tr>
              </thead>
              <tbody>
                {list.map((s) => (
                  <tr key={s.schedule_id}>
                    <td className={`${td} ${mono}`}>
                      {s.schedule_id}
                      {/* domain/privacy/export.py pseudonymized: only the handles table loses a column, the value. */}
                      {s.pseudonymize && <span className="block font-sans text-[0.8125rem] text-ink2">Identificadores sem o valor, só com o hash com chave.</span>}
                    </td>
                    <td className={`${td} ${mono}`}>
                      {s.destination_kind}://{s.bucket}/{s.prefix ?? ''}
                      <span className="block text-ink2">{s.region}</span>
                      {s.endpoint_url && <span className="block break-all text-ink2">{s.endpoint_url}</span>}
                    </td>
                    <td className={`${td} text-[0.8125rem]`}>{s.tables.join(', ')}</td>
                    <td className={td}>{s.interval_hours} h</td>
                    <td className={`${td} ${mono} break-all text-ink2`}>{s.credential_ref}</td>
                    {hasRole('security') && (
                      <td className={`${td} text-right`}>
                        <button type="button" className={btnQuiet} onClick={() => setCredFor(s)} aria-label={`Gravar a credencial de ${s.schedule_id}`}>
                          Gravar credencial
                        </button>
                      </td>
                    )}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Section>

      <Section
        title="Execuções"
        actions={
          list.length > 0 ? (
            <div className="w-56">
              <Select label="Agendamento" value={scheduleFilter} onChange={setScheduleFilter} options={[{ value: '', label: 'Todos' }, ...list.map((s) => ({ value: s.schedule_id, label: s.schedule_id }))]} />
            </div>
          ) : undefined
        }
      >
        {(runs.status === 'loading' || runs.status === 'idle') && <Loading label="Carregando as execuções…" />}
        {runs.status === 'error' && <ErrorState error={runs.error} onRetry={runs.reload} compact />}
        {runs.status === 'success' && runs.items.length === 0 && <Empty title="Nenhuma execução ainda." />}
        {runs.status === 'success' && runs.items.length > 0 && (
          <>
            <div className="overflow-x-auto">
              <table className="w-full min-w-[46rem] border-collapse">
                <caption className="sr-only">Execuções da exportação contínua</caption>
                <thead>
                  <tr>
                    <th scope="col" className={th}>Início</th>
                    <th scope="col" className={th}>Agendamento</th>
                    <th scope="col" className={th}>Estado</th>
                    <th scope="col" className={th}>Mudanças</th>
                    <th scope="col" className={th}>Fim</th>
                  </tr>
                </thead>
                <tbody>
                  {runs.items.map((x) => (
                    <tr key={x.run_id}>
                      <td className={`${td} ${mono} whitespace-nowrap`}>{fmtDateTime(x.started_at)}</td>
                      {/* A package asked for in "Arquivo do titular" runs here too, and says how long it lives. */}
                      <td className={`${td} ${x.schedule_id === ON_DEMAND ? '' : mono}`}>{x.schedule_id === ON_DEMAND ? 'Arquivo do titular' : x.schedule_id}</td>
                      <td className={td}>
                        <Chip tone={x.status === 'completed' ? 'ok' : x.status === 'failed' ? 'danger' : 'neutral'}>{exportRunStatus(x.status)}</Chip>
                        {x.schedule_id === ON_DEMAND && <span className="block text-[0.8125rem] text-ink2">{packageState(x.manifest)}</span>}
                        {x.error && <span className={`block text-[0.8125rem] ${danger.text}`}>{x.error}</span>}
                      </td>
                      <td className={`${td} ${mono} text-ink2`}>
                        {x.schedule_id === ON_DEMAND ? (
                          <span className="font-sans">não se aplica</span>
                        ) : (
                          <>
                            {fmtInt(x.from_seq)} a {fmtInt(x.to_seq)}
                          </>
                        )}
                      </td>
                      <td className={`${td} ${mono} whitespace-nowrap text-ink2`}>{x.finished_at ? fmtDateTime(x.finished_at) : 'em curso'}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <LoadMore hasMore={runs.hasMore} loading={runs.loadingMore} error={runs.moreError} onClick={runs.loadMore} />
          </>
        )}
      </Section>

      {credFor && <CredentialDialog schedule={credFor} onClose={() => setCredFor(null)} />}
      {creating && <NewScheduleDialog known={list} onClose={() => setCreating(false)} />}
    </div>
  )
}

/* The new schedule: the fields of config/models.py ExportSchedule, checked here and again by the server. */

type DestinationKind = 's3' | 'gcs' | 'azure'
const DESTINATION_KINDS: { value: DestinationKind; label: string }[] = [
  { value: 's3', label: 'Amazon S3 ou compatível com S3' },
  { value: 'gcs', label: 'Google Cloud Storage' },
  { value: 'azure', label: 'Azure Blob Storage' },
]

interface ScheduleDraft {
  kind: DestinationKind
  id: string
  hours: string
  bucket: string
  prefix: string
  endpoint: string
  tables: string[]
  pseudonymize: boolean
  reason: string
}
type DraftKey = keyof ScheduleDraft
/** The order of the fields on screen: the first one with a problem gets the focus. */
const DRAFT_ORDER: DraftKey[] = ['id', 'hours', 'bucket', 'prefix', 'endpoint', 'tables', 'reason']

const SCHEDULE_ID = /^[a-z][a-z0-9_.:-]{0,63}$/
/** S3 bucket names: 3 to 63 lowercase letters, digits, dots and hyphens, a letter or digit at each end. */
const BUCKET = /^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$/
const PREFIX = /^[A-Za-z0-9._/-]*$/

const trimSlashes = (p: string) => p.replace(/^\/+|\/+$/g, '')
const endpointKey = (u: string | null | undefined) => (u ?? '').trim().replace(/\/+$/, '').toLowerCase()

function isHttpsURL(value: string): boolean {
  try {
    const u = new URL(value)
    return u.protocol === 'https:' && u.hostname !== ''
  } catch {
    return false
  }
}

/** What stops the draft from going out, per field, in the words the person reads. */
function draftProblems(d: ScheduleDraft, existing: Schedule[]): Partial<Record<DraftKey, string>> {
  const p: Partial<Record<DraftKey, string>> = {}
  const id = d.id.trim()
  const bucket = d.bucket.trim()
  const prefix = d.prefix.trim()
  const endpoint = d.endpoint.trim()
  if (!id) p.id = 'Dê um nome ao agendamento.'
  else if (!SCHEDULE_ID.test(id)) p.id = 'Use só letras minúsculas, números, ponto, dois-pontos, hífen e sublinhado, começando por letra, até 64 caracteres.'
  else if (existing.some((s) => s.schedule_id === id)) p.id = 'Já existe um agendamento com esse nome.'
  if (!/^\d+$/.test(d.hours.trim()) || Number(d.hours) < 1) p.hours = 'Use um número inteiro de horas, a partir de 1.'
  if (!bucket) p.bucket = 'Informe o bucket.'
  else if (!BUCKET.test(bucket) || bucket.includes('..'))
    p.bucket = 'Nome de bucket inválido: de 3 a 63 caracteres, com letras minúsculas, números, ponto e hífen, começando e terminando com letra ou número.'
  if (!PREFIX.test(prefix) || prefix.startsWith('/') || prefix.includes('//'))
    p.prefix = 'Use letras, números, ponto, hífen, sublinhado e barra, sem barra no começo e sem duas barras seguidas.'
  if (endpoint && !isHttpsURL(endpoint)) p.endpoint = 'Use um endereço https completo, como https://s3.exemplo.com.'
  if (!p.bucket && !p.prefix && !p.endpoint) {
    // One schedule per destination: two schedules on the same prefix would write over each other's folders.
    const taken = existing.find((s) => s.bucket === bucket && trimSlashes(s.prefix ?? '') === trimSlashes(prefix) && endpointKey(s.endpoint_url) === endpointKey(endpoint))
    if (taken) p.prefix = `O agendamento ${taken.schedule_id} já grava neste bucket e prefixo. Escolha outro prefixo.`
  }
  if (d.tables.length === 0) p.tables = 'Escolha pelo menos uma tabela.'
  if (d.reason.trim().length < 3) p.reason = 'Escreva o motivo, com pelo menos 3 caracteres.'
  return p
}

function scheduleOf(d: ScheduleDraft, spaceId: string, region: string): Schedule {
  const id = d.id.trim()
  return {
    schedule_id: id,
    destination_kind: d.kind,
    bucket: d.bucket.trim(),
    prefix: d.prefix.trim(),
    // The control plane refuses a schedule outside the space's region, and the cell fails its runs (app/privacy.py).
    region,
    endpoint_url: d.endpoint.trim() || null,
    tables: TABLE_NAMES.filter((n) => d.tables.includes(n)),
    interval_hours: Number(d.hours.trim()),
    pseudonymize: d.pseudonymize,
    // Every secret of a cell is named after its space (adapters/outbound/secrets.py), so two spaces never share one.
    credential_ref: `spaces/${spaceId}/export/${id}`,
  }
}

/** A field of the schedule form: its hint, or its problem once the person left the field or tried to send. */
function DraftField({
  id,
  label: text,
  hint: help,
  problem,
  mono: monoValue = true,
  children,
}: {
  id: string
  label: string
  hint?: ReactNode
  problem: string | null
  mono?: boolean
  children: (a: { 'aria-invalid'?: true; 'aria-describedby'?: string; className: string }) => ReactNode
}) {
  const describedBy = problem ? `${id}-problem` : help ? `${id}-hint` : undefined
  // The red border replaces the field's own border color: two border colors on one element leave the winner to the cascade.
  const look = problem ? input.replace('border-edge', danger.border) : input
  return (
    <Field id={id} label={text}>
      {() => (
        <>
          {children({ 'aria-invalid': problem ? true : undefined, 'aria-describedby': describedBy, className: `${look} ${monoValue ? 'font-mono' : ''}` })}
          {problem ? (
            <p id={`${id}-problem`} className={`mt-1.5 text-[0.8125rem] font-semibold ${danger.text}`}>
              {problem}
            </p>
          ) : (
            help && (
              <p id={`${id}-hint`} className={hint}>
                {help}
              </p>
            )
          )}
        </>
      )}
    </Field>
  )
}

/**
 * Adds a schedule without writing JSON: the entry goes at the end of the document as it is now on the
 * control plane, as a diff with a reason, which another person approves in Configuração.
 */
function NewScheduleDialog({ known, onClose }: { known: Schedule[]; onClose: () => void }) {
  const { control, space } = useSpace()
  const base = useId()
  const fid = (k: DraftKey) => (k === 'tables' ? `${base}-table-${TABLE_NAMES[0]}` : `${base}-${k}`)
  const [draft, setDraft] = useState<ScheduleDraft>({ kind: 's3', id: '', hours: '24', bucket: '', prefix: '', endpoint: '', tables: TABLE_NAMES, pseudonymize: false, reason: '' })
  const [latest, setLatest] = useState(known)
  const [tried, setTried] = useState(false)
  const [left, setLeft] = useState<Partial<Record<DraftKey, boolean>>>({})
  const act = useAction<{ diff: DiffOut; schedule: Schedule } | null>()
  const [refocus, setRefocus] = useState(0)
  const firstField = useRef<HTMLInputElement>(null)
  const done = useRef<HTMLParagraphElement>(null)
  const problems = draftProblems(draft, latest)
  // Errors show once the person leaves a field they typed in, or after the first try to send.
  const shown = (k: DraftKey): string | null => {
    const typed = k === 'tables' || (typeof draft[k] === 'string' && (draft[k] as string).trim() !== '')
    return (tried || (left[k] && typed) ? problems[k] : undefined) ?? null
  }
  const set = <K extends DraftKey>(k: K, v: ScheduleDraft[K]) => setDraft((d) => ({ ...d, [k]: v }))
  const leave = (k: DraftKey) => () => setLeft((l) => ({ ...l, [k]: true }))
  const focusFirst = (p: Partial<Record<DraftKey, string>>) => {
    const first = DRAFT_ORDER.find((k) => p[k])
    if (first) document.getElementById(fid(first))?.focus()
  }

  useEffect(() => {
    if (act.result) done.current?.focus()
  }, [act.result])
  // After the check against the document of now found a clash, the field with the problem gets the focus.
  useEffect(() => {
    if (refocus) focusFirst(problems)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [refocus])

  const submit = (e: FormEvent) => {
    e.preventDefault()
    setTried(true)
    if (Object.keys(problems).length > 0) {
      focusFirst(problems)
      return
    }
    act.run(async () => {
      // The document as it is now, so a change approved while the form was open is not dropped.
      const now = await control.configDocument(space.space_id, 'export-schedules')
      if (!Array.isArray(now.document)) throw new Error('the export-schedules document is not a list')
      const current = now.document as Schedule[]
      if (Object.keys(draftProblems(draft, current)).length > 0) {
        setLatest(current)
        setRefocus((n) => n + 1)
        return null
      }
      const schedule = scheduleOf(draft, space.space_id, space.region)
      const diff = await control.submitDiff(space.space_id, 'export-schedules', [...current, schedule], draft.reason.trim())
      return { diff, schedule }
    })
  }

  const sent = act.result
  const allTables = draft.tables.length === TABLE_NAMES.length
  const tablesProblem = shown('tables')
  return (
    <Dialog open onClose={onClose} title="Novo agendamento de exportação" wide initialFocus={firstField}>
      {sent ? (
        <div className="grid gap-3">
          <p ref={done} tabIndex={-1} className="font-semibold text-ink outline-none">
            Agendamento {sent.schedule.schedule_id} enviado para aprovação.
          </p>
          <p className={small}>
            A exportação começa depois que a mudança for aprovada em Configuração, na aba Diffs. Pela regra dos quatro olhos, outra pessoa aprova; numa empresa com uma só pessoa na
            Administração, ela mesma aprova.
          </p>
          <p className={small}>Aprovada a mudança, grave a credencial do destino em Gravar credencial, na linha do agendamento.</p>
          <p className={`${mono} break-all text-ink2`}>diff {sent.diff.diff_id}</p>
          <div className="flex flex-wrap justify-end gap-2">
            <Link to={`${paths.config(space.space_id)}?aba=diffs`} className={btnSecondary}>
              Abrir os diffs em Configuração
            </Link>
            <button type="button" className={btnPrimary} onClick={onClose}>
              Concluir
            </button>
          </div>
        </div>
      ) : (
        <form onSubmit={submit} noValidate className="grid gap-4">
          <div className={`${small} grid gap-1`}>
            <p>
              O destino é um bucket do Amazon S3 (ou compatível com S3), do Google Cloud Storage ou um contêiner do Azure Blob Storage, na região deste espaço:{' '}
              <span className={`${mono} whitespace-nowrap text-ink`}>{space.region}</span>. A configuração recusa um destino em outra região e um segundo agendamento no mesmo bucket e prefixo.
            </p>
            <p>O agendamento entra como mudança de configuração e só vale depois de aprovado em Configuração.</p>
          </div>
          <Select label="Destino" value={draft.kind} onChange={(v: DestinationKind) => set('kind', v)} options={DESTINATION_KINDS} />
          <div className="grid gap-4 sm:grid-cols-2">
            <DraftField id={fid('id')} label="Nome do agendamento" hint="Letras minúsculas, números, ponto, dois-pontos, hífen e sublinhado, começando por letra." problem={shown('id')}>
              {(a) => <input id={fid('id')} ref={firstField} value={draft.id} onChange={(e) => set('id', e.target.value)} onBlur={leave('id')} autoComplete="off" spellCheck={false} {...a} />}
            </DraftField>
            <DraftField id={fid('hours')} label="A cada quantas horas" hint="1 para toda hora, 24 para uma vez por dia." problem={shown('hours')}>
              {(a) => <input id={fid('hours')} type="number" inputMode="numeric" min={1} step={1} value={draft.hours} onChange={(e) => set('hours', e.target.value)} onBlur={leave('hours')} {...a} />}
            </DraftField>
            <DraftField id={fid('bucket')} label={draft.kind === 'azure' ? 'Contêiner' : 'Bucket'} hint="Só o nome, sem prefixo de protocolo e sem barra." problem={shown('bucket')}>
              {(a) => <input id={fid('bucket')} value={draft.bucket} onChange={(e) => set('bucket', e.target.value)} onBlur={leave('bucket')} autoComplete="off" spellCheck={false} {...a} />}
            </DraftField>
            <DraftField id={fid('prefix')} label="Prefixo (opcional)" hint="A pasta dentro do bucket, como niadra/. Vazio grava na raiz." problem={shown('prefix')}>
              {(a) => <input id={fid('prefix')} value={draft.prefix} onChange={(e) => set('prefix', e.target.value)} onBlur={leave('prefix')} autoComplete="off" spellCheck={false} {...a} />}
            </DraftField>
            <div className="sm:col-span-2">
              <DraftField
                id={fid('endpoint')}
                label="Endpoint (opcional)"
                hint={
                  draft.kind === 's3'
                    ? `Só para armazenamento compatível com S3 fora da AWS. Vazio usa o S3 da AWS em ${space.region}.`
                    : draft.kind === 'gcs'
                      ? 'Vazio usa a API do Google Cloud Storage compatível com S3.'
                      : 'O endereço da conta, como https://minhaconta.blob.core.windows.net. Vazio aceita qualquer conta do Azure.'
                }
                problem={shown('endpoint')}
              >
                {(a) => (
                  <input id={fid('endpoint')} type="url" inputMode="url" value={draft.endpoint} onChange={(e) => set('endpoint', e.target.value)} onBlur={leave('endpoint')} autoComplete="off" spellCheck={false} {...a} />
                )}
              </DraftField>
            </div>
          </div>
          <fieldset className="min-w-0" aria-describedby={tablesProblem ? `${base}-tables-problem` : `${base}-tables-hint`}>
            <legend className={label}>Tabelas</legend>
            {tablesProblem ? (
              <p id={`${base}-tables-problem`} className={`text-[0.8125rem] font-semibold ${danger.text}`}>
                {tablesProblem}
              </p>
            ) : (
              <p id={`${base}-tables-hint`} className="text-[0.8125rem] leading-5 text-ink2">
                Cada tabela sai numa pasta com o nome dela, em arquivos Parquet.
              </p>
            )}
            {/* Each table in two lines, its name and the folder it becomes, so every item has the same shape. */}
            <div className="mt-2 grid gap-x-4 gap-y-1 sm:grid-cols-2 lg:grid-cols-3">
              {EXPORT_TABLES.map((t) => (
                <label key={t.name} className="flex min-h-11 cursor-pointer items-start gap-2.5 py-1 text-[0.875rem] leading-5 text-ink">
                  <input
                    id={`${base}-table-${t.name}`}
                    type="checkbox"
                    className="mt-px h-5 w-5 shrink-0 accent-[#5e7c00]"
                    checked={draft.tables.includes(t.name)}
                    onChange={(e) => {
                      set('tables', e.target.checked ? [...draft.tables, t.name] : draft.tables.filter((n) => n !== t.name))
                      setLeft((l) => ({ ...l, tables: true }))
                    }}
                  />
                  <span className="min-w-0">
                    {t.label} <span className={`${mono} block text-ink2`}>{t.name}</span>
                  </span>
                </label>
              ))}
            </div>
            <button
              type="button"
              className={`${btnQuiet} mt-1 -ml-2`}
              onClick={() => {
                set('tables', allTables ? [] : TABLE_NAMES)
                setLeft((l) => ({ ...l, tables: true }))
              }}
            >
              {allTables ? 'Desmarcar todas' : 'Marcar todas'}
            </button>
          </fieldset>
          <label className="flex min-h-11 cursor-pointer items-start gap-2.5 py-1 text-[0.875rem] leading-5 text-ink">
            <input
              type="checkbox"
              className="mt-px h-5 w-5 shrink-0 accent-[#5e7c00]"
              checked={draft.pseudonymize}
              onChange={(e) => set('pseudonymize', e.target.checked)}
              aria-describedby={`${base}-pseudonymize-hint`}
            />
            <span className="min-w-0">
              Tirar o valor dos identificadores
              <span id={`${base}-pseudonymize-hint`} className="mt-0.5 block text-[0.8125rem] text-ink2">
                A tabela de identificadores sai sem o valor de cada telefone, e-mail, documento e id de sistema, só com o hash com chave, e as linhas continuam se ligando entre
                arquivos e execuções. As outras tabelas saem como estão.
              </span>
            </span>
          </label>
          <DraftField id={fid('reason')} label="Motivo" hint="Quem aprova lê o motivo. Ele fica no diff, com o seu nome." problem={shown('reason')} mono={false}>
            {(a) => (
              <textarea
                id={fid('reason')}
                value={draft.reason}
                onChange={(e) => set('reason', e.target.value)}
                onBlur={leave('reason')}
                maxLength={500}
                {...a}
                className={`${a.className} h-auto min-h-20 py-2`}
              />
            )}
          </DraftField>
          {Boolean(act.error) && <ErrorState error={act.error} compact />}
          <div className="flex flex-wrap justify-end gap-2">
            <button type="button" className={btnSecondary} onClick={onClose}>
              Cancelar
            </button>
            <button type="submit" className={btnPrimary} disabled={act.busy}>
              {act.busy ? 'Enviando…' : 'Enviar para aprovação'}
            </button>
          </div>
        </form>
      )}
    </Dialog>
  )
}

/**
 * The value goes to the cell vault only (PUT, write-only); the control plane
 * records which secret exists and its version, never the value. The S3
 * destination reads it as JSON with the two halves of an access key
 * (adapters/outbound/objects/destinations.py), so the person types the halves.
 */
function CredentialDialog({ schedule, onClose }: { schedule: Schedule; onClose: () => void }) {
  const { cell, control, space } = useSpace()
  const [keyId, setKeyId] = useState('')
  const [secret, setSecret] = useState('')
  const [sas, setSas] = useState('')
  const act = useAction<{ written: SecretWritten; ref: SecretRefOut | null; refError: unknown }>()
  const firstField = useRef<HTMLInputElement>(null)
  const azure = schedule.destination_kind === 'azure'
  const ready = azure ? sas.trim() !== '' : keyId.trim() !== '' && secret.trim() !== ''
  const submit = (e: FormEvent) => {
    e.preventDefault()
    const value = azure ? JSON.stringify({ sas_url: sas.trim() }) : JSON.stringify({ access_key_id: keyId.trim(), secret_access_key: secret.trim() })
    act.run(async () => {
      const written = await cell.putDestinationCredential(schedule.schedule_id, value)
      try {
        const ref = await control.putSecretRef(space.space_id, schedule.credential_ref, 'export', written.version)
        return { written, ref, refError: null }
      } catch (refError) {
        return { written, ref: null, refError }
      }
    })
    setKeyId('')
    setSecret('')
    setSas('')
  }
  const r = act.result
  return (
    <Dialog open onClose={onClose} title={`Credencial de ${schedule.schedule_id}`} initialFocus={firstField}>
      {r ? (
        <div role="status" className="grid gap-3">
          <p className="font-semibold text-ink">Credencial gravada no cofre da região, versão {r.written.version}.</p>
          {r.ref ? (
            <Props items={[['Referência', <span className={`${mono} break-all`}>{r.ref.ref}</span>], ['Registrada em', fmtDateTime(r.ref.rotated_at)]]} />
          ) : (
            <p className={`text-[0.875rem] ${danger.text}`}>O plano de controle não registrou a nova versão. A credencial vale; tente registrar de novo em Configuração, Segredos.</p>
          )}
          <div className="flex justify-end">
            <button type="button" className={btnPrimary} onClick={onClose}>
              Concluir
            </button>
          </div>
        </div>
      ) : (
        <form onSubmit={submit} className="grid gap-4">
          <p className={small}>
            Destino{' '}
            <span className={`${mono} break-all text-ink`}>
              {schedule.destination_kind}://{schedule.bucket}/{schedule.prefix ?? ''}
            </span>
            .{' '}
            {azure
              ? 'Use uma SAS do contêiner que só crie e grave (permissões c e w): a região recusa uma que leia, liste ou apague.'
              : schedule.destination_kind === 'gcs'
                ? 'Use uma chave HMAC de uma conta de serviço que só grave nesse prefixo: a região recusa uma chave que liste o bucket.'
                : 'Use uma chave de acesso que só grave nesse prefixo: a região recusa uma chave que liste o bucket.'}{' '}
            A credencial substitui a atual e não aparece de novo em lugar nenhum.
          </p>
          {azure ? (
            <Field label="URL da SAS do contêiner">
              {(id) => <input id={id} ref={firstField} type="password" className={`${input} font-mono`} value={sas} onChange={(e) => setSas(e.target.value)} autoComplete="off" spellCheck={false} />}
            </Field>
          ) : (
            <>
              <Field label={schedule.destination_kind === 'gcs' ? 'ID de acesso da chave HMAC' : 'ID da chave de acesso'}>
                {(id) => <input id={id} ref={firstField} className={`${input} font-mono`} value={keyId} onChange={(e) => setKeyId(e.target.value)} autoComplete="off" spellCheck={false} />}
              </Field>
              <Field label="Chave secreta de acesso">
                {(id) => <input id={id} type="password" className={`${input} font-mono`} value={secret} onChange={(e) => setSecret(e.target.value)} autoComplete="off" spellCheck={false} />}
              </Field>
            </>
          )}
          {Boolean(act.error) && <ErrorState error={act.error} compact />}
          <div className="flex flex-wrap justify-end gap-2">
            <button type="button" className={btnSecondary} onClick={onClose}>
              Cancelar
            </button>
            <button type="submit" className={btnPrimary} disabled={act.busy || !ready}>
              {act.busy ? 'Gravando…' : 'Gravar credencial'}
            </button>
          </div>
        </form>
      )}
    </Dialog>
  )
}
