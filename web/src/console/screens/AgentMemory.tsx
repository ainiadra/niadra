import { useState, type FormEvent } from 'react'
import type { AgentMemorySettingsDoc, AgentNote, AgentNoteKind, AgentNoteProposal, AgentNoteVisibility, DiffOut } from '../api/types'
import { fmtDateTime, fmtInt } from '../lib/format'
import { useTab } from '../lib/tabs'
import { useResource } from '../lib/useResource'
import { useSpace } from '../session'
import { Chip, PageHeader, SourceName, useSources } from '../ui/bits'
import { Dialog } from '../ui/Dialog'
import { Field, Section, Select, TextInput, useAction } from '../ui/forms'
import { useTitle } from '../ui/layout'
import { DiffReason, diffReasonOk, DiffSent, useCanPropose } from '../ui/proposal'
import { Empty, ErrorState, Loading } from '../ui/States'
import { Tabs } from '../ui/Tabs'
import { btnDangerGhost, btnPrimary, btnQuiet, btnSecondary, card, input, mono, small } from '../ui/styles'

const KINDS: { value: AgentNoteKind; label: string }[] = [
  { value: 'procedure', label: 'Procedimento' },
  { value: 'tool_note', label: 'Nota de ferramenta' },
  { value: 'process_note', label: 'Nota de processo' },
  { value: 'pitfall', label: 'Armadilha' },
]
const VISIBILITIES: { value: AgentNoteVisibility; label: string }[] = [
  { value: 'source', label: 'Só este agente' },
  { value: 'vendor', label: 'Agentes do mesmo fornecedor' },
  { value: 'space', label: 'Todos os agentes do espaço' },
]
const ORIGIN = { agent: 'Escrita pelo agente', human: 'Escrita por uma pessoa', distilled: 'Destilada e aprovada' } as const
const PROBLEM: Record<string, string> = {
  nothing_to_propose: 'A conversa não tinha um procedimento reaproveitável.',
  personal_data: 'A proposta trazia dado pessoal e foi descartada.',
  no_turns: 'Não há turnos dessa conversa ou tarefa.',
}
const kindLabel = (k: AgentNoteKind) => KINDS.find((x) => x.value === k)?.label ?? k
const visibilityLabel = (v: AgentNoteVisibility) => VISIBILITIES.find((x) => x.value === v)?.label ?? v
const TAG = /^[a-z0-9][a-z0-9_.:-]{0,63}$/
const parseTags = (text: string) => [...new Set(text.split(/[\s,]+/).map((t) => t.trim().toLowerCase()).filter(Boolean))]
const tagsOk = (text: string) => parseTags(text).length <= 8 && parseTags(text).every((t) => TAG.test(t))

type TabKey = 'notes' | 'proposals' | 'settings'

/**
 * The agents' own memory (plan 10, C): procedures, notes on tools and processes, and pitfalls each agent
 * learned about the work. Never about a customer: the server refuses a note with personal data. People
 * read, write, revise and retire notes, approve what a distillation proposes, and turn the feature on by diff.
 */
export function AgentMemory() {
  const { space, hasRole } = useSpace()
  useTitle('Memória dos agentes', space.environment === 'sandbox')
  const [tab, setTab] = useTab<TabKey>(['notes', 'proposals', 'settings'], 'notes')
  const canWrite = hasRole('integration')

  return (
    <>
      <PageHeader
        screen="agentMemory"
      />
      <div data-tour="agentMemory.tabs">
        <Tabs
          label="Memória dos agentes"
          tabs={[
            { key: 'notes', label: 'Notas' },
            { key: 'proposals', label: 'Propostas' },
            { key: 'settings', label: 'Ligar e limites' },
          ]}
          active={tab}
          onChange={setTab}
        >
          <div data-tour={`agentMemory.${tab}`}>
            {tab === 'notes' && <Notes canWrite={canWrite} />}
            {tab === 'proposals' && <Proposals canWrite={canWrite} />}
            {tab === 'settings' && <Settings />}
          </div>
        </Tabs>
      </div>
    </>
  )
}

function AgentSelect({ value, onChange, label, all = false }: { value: string; onChange: (v: string) => void; label: string; all?: boolean }) {
  const { list } = useSources()
  const options = [...(all ? [{ value: '', label: 'Todos os agentes' }] : []), ...list.map((s) => ({ value: s.source_id, label: s.vendor ? `${s.name} (${s.vendor})` : s.name }))]
  return <Select label={label} value={value} onChange={onChange} options={options.length ? options : [{ value: '', label: 'Nenhum agente' }]} />
}

function Notes({ canWrite }: { canWrite: boolean }) {
  const { cell, hasRole } = useSpace()
  const [source, setSource] = useState('')
  const [status, setStatus] = useState<'active' | 'retired'>('active')
  const [creating, setCreating] = useState(false)
  const notes = useResource((signal) => cell.agentNotes({ source_id: source || null, status }, { signal }), [cell, source, status])

  return (
    <div className="grid gap-6">
      <Section
        title="Notas"
        description="Entram no prompt de cada agente num bloco próprio, antes do contexto do cliente e depois das instruções, e pela ferramenta search_agent_memory. Uma edição gera versão nova; a anterior fica aposentada e guardada."
        actions={
          canWrite && (
            <button type="button" className={btnPrimary} onClick={() => setCreating(true)}>
              Nova nota
            </button>
          )
        }
      >
        <div className="mb-4 grid gap-3 sm:grid-cols-2 lg:max-w-2xl">
          <AgentSelect label="Agente" value={source} onChange={setSource} all />
          <Select
            label="Situação"
            value={status}
            onChange={setStatus}
            options={[
              { value: 'active', label: 'Em uso' },
              { value: 'retired', label: 'Aposentadas' },
            ]}
          />
        </div>
        {(notes.status === 'loading' || notes.status === 'idle') && <Loading label="Carregando as notas…" />}
        {notes.status === 'error' && <ErrorState error={notes.error} onRetry={notes.reload} />}
        {notes.status === 'success' && notes.data && notes.data.items.length === 0 && (
          <Empty title={status === 'active' ? 'Nenhuma nota em uso.' : 'Nenhuma nota aposentada.'}>
            Os agentes escrevem com a ferramenta remember quando a chave tem o escopo agent_memory:write; você escreve aqui ou aprova uma destilação.
          </Empty>
        )}
        {notes.status === 'success' && notes.data && notes.data.items.length > 0 && (
          <ul className="grid gap-3" aria-label="Notas dos agentes">
            {notes.data.items.map((n) => (
              <NoteCard key={n.note_id} note={n} canWrite={canWrite} onChanged={notes.reload} />
            ))}
          </ul>
        )}
      </Section>
      {hasRole('admin') && source && <EraseAgent sourceId={source} onDone={notes.reload} />}
      <NoteDialog open={creating} onClose={() => setCreating(false)} onSaved={notes.reload} />
    </div>
  )
}

function NoteCard({ note: n, canWrite, onChanged }: { note: AgentNote; canWrite: boolean; onChanged: () => void }) {
  const { cell } = useSpace()
  const [editing, setEditing] = useState(false)
  const [history, setHistory] = useState(false)
  const retire = useAction<AgentNote>()
  return (
    <li className={`${card} px-4 py-3`}>
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <p className="font-semibold text-ink">{n.title}</p>
        <span className="flex flex-wrap gap-1.5">
          <Chip>{kindLabel(n.kind)}</Chip>
          <Chip tone={n.status === 'active' ? 'ok' : 'neutral'}>{n.status === 'active' ? `Versão ${n.version}` : `Aposentada, versão ${n.version}`}</Chip>
        </span>
      </div>
      <p className="mt-1.5 whitespace-pre-line text-ink">{n.body}</p>
      <p className={`${small} mt-2`}>
        <SourceName id={n.source_id} withVendor /> · {visibilityLabel(n.visibility)} · {ORIGIN[n.origin]} · {fmtDateTime(n.created_at)}
        {n.tags.length > 0 && <> · {n.tags.map((t) => `#${t}`).join(' ')}</>}
        {n.valid_until && <> · vale até {fmtDateTime(n.valid_until)}</>}
      </p>
      {n.evidence && (
        <p className={`${mono} mt-1 text-ink2`}>
          origem: {n.evidence.conversation_id ? `conversa ${n.evidence.conversation_id}` : `tarefa ${n.evidence.task_id}`}
        </p>
      )}
      {Boolean(retire.error) && (
        <div className="mt-3">
          <ErrorState error={retire.error} compact />
        </div>
      )}
      <div className="mt-3 flex flex-wrap gap-2">
        <button type="button" className={btnQuiet} onClick={() => setHistory(true)} aria-label={`Versões de ${n.title}`}>
          Versões
        </button>
        {canWrite && n.status === 'active' && (
          <>
            <button type="button" className={btnSecondary} onClick={() => setEditing(true)} aria-label={`Editar ${n.title}`}>
              Editar
            </button>
            <button type="button" className={btnDangerGhost} disabled={retire.busy} onClick={() => retire.run(() => cell.retireAgentNote(n.note_id), onChanged)} aria-label={`Aposentar ${n.title}`}>
              {retire.busy ? 'Aposentando…' : 'Aposentar'}
            </button>
          </>
        )}
      </div>
      <NoteDialog open={editing} note={n} onClose={() => setEditing(false)} onSaved={onChanged} />
      <Versions open={history} note={n} onClose={() => setHistory(false)} />
    </li>
  )
}

function Versions({ open, note, onClose }: { open: boolean; note: AgentNote; onClose: () => void }) {
  const { cell } = useSpace()
  const versions = useResource((signal) => cell.agentNoteVersions(note.note_id, { signal }), [cell, note.note_id], open)
  return (
    <Dialog open={open} onClose={onClose} title={`Versões de "${note.title}"`} wide>
      {(versions.status === 'loading' || versions.status === 'idle') && <Loading label="Carregando as versões…" />}
      {versions.status === 'error' && <ErrorState error={versions.error} compact />}
      {versions.status === 'success' && versions.data && (
        <ol className="grid gap-3">
          {versions.data.map((v) => (
            <li key={v.note_id} className="border-t border-line2 pt-3">
              <p className="font-semibold text-ink">
                Versão {v.version} · {v.status === 'active' ? 'em uso' : 'aposentada'}
              </p>
              <p className={`${small}`}>
                {fmtDateTime(v.created_at)} · {ORIGIN[v.origin]} · <span className={mono}>{v.created_by}</span>
              </p>
              <p className="mt-1 font-semibold text-ink">{v.title}</p>
              <p className="whitespace-pre-line text-ink">{v.body}</p>
            </li>
          ))}
        </ol>
      )}
    </Dialog>
  )
}

function NoteDialog({ open, note, onClose, onSaved }: { open: boolean; note?: AgentNote; onClose: () => void; onSaved: () => void }) {
  const { cell } = useSpace()
  const { list } = useSources()
  const [picked, setSource] = useState(note?.source_id ?? '')
  // The agents arrive after the dialog mounts: until one is picked, the first one is the choice.
  const source = picked || list[0]?.source_id || ''
  const [kind, setKind] = useState<AgentNoteKind>(note?.kind ?? 'procedure')
  const [visibility, setVisibility] = useState<AgentNoteVisibility>(note?.visibility ?? 'source')
  const [title, setTitle] = useState(note?.title ?? '')
  const [body, setBody] = useState(note?.body ?? '')
  const [tags, setTags] = useState(note?.tags.join(' ') ?? '')
  const save = useAction<unknown>()
  const ok = title.trim().length > 0 && title.length <= 300 && body.trim().length > 0 && body.length <= 20000 && tagsOk(tags) && Boolean(note || source)

  const submit = (e: FormEvent) => {
    e.preventDefault()
    if (!ok) return
    const fields = { kind, visibility, title: title.trim(), body: body.trim(), tags: parseTags(tags) }
    const done = () => {
      onSaved()
      onClose()
    }
    if (note) save.run(() => cell.reviseAgentNote(note.note_id, fields), done)
    else save.run(() => cell.createAgentNote({ ...fields, source_id: source }), done)
  }

  return (
    <Dialog open={open} onClose={onClose} title={note ? 'Editar nota (gera versão nova)' : 'Nova nota'} wide>
      <form className="grid gap-4" onSubmit={submit}>
        <p className={small}>Escreva como fazer o trabalho, de um jeito que sirva para qualquer cliente. Sem nomes, telefones, e-mails, documentos ou números de cliente: a Niadra recusa a nota em vez de mascarar.</p>
        {!note && <AgentSelect label="Agente dono da nota" value={source} onChange={setSource} />}
        <div className="grid gap-3 sm:grid-cols-2">
          <Select label="Tipo" value={kind} onChange={setKind} options={KINDS} />
          <Select label="Quem lê" value={visibility} onChange={setVisibility} options={VISIBILITIES} />
        </div>
        <TextInput label="Título" value={title} onChange={setTitle} maxLength={300} hint="Até 300 caracteres." />
        <Field label="Texto" hint="Até 20.000 caracteres. Passos em ordem, o que confirma que deu certo, o que evitar.">
          {(id) => <textarea id={id} className={`${input} h-auto min-h-32 py-2`} value={body} onChange={(e) => setBody(e.target.value)} maxLength={20000} />}
        </Field>
        <TextInput label="Tags" value={tags} onChange={setTags} mono hint="Até 8, separadas por espaço: tipos de objeto, operações ou sistemas, como invoice credit erp." />
        {Boolean(save.error) && <ErrorState error={save.error} compact />}
        <div className="flex flex-wrap gap-2">
          <button type="submit" className={btnPrimary} disabled={!ok || save.busy}>
            {save.busy ? 'Gravando…' : note ? 'Gravar versão nova' : 'Gravar nota'}
          </button>
          <button type="button" className={btnSecondary} onClick={onClose}>
            Cancelar
          </button>
        </div>
      </form>
    </Dialog>
  )
}

function EraseAgent({ sourceId, onDone }: { sourceId: string; onDone: () => void }) {
  const { cell } = useSpace()
  const [confirm, setConfirm] = useState(false)
  const erase = useAction<{ deleted: number }>()
  return (
    <Section title="Apagar a memória deste agente" description="Apaga todas as notas e propostas do agente, com todas as versões, e deixa um comprovante. A exportação contínua recebe o apagamento. Não desfaz.">
      {erase.result ? (
        <p role="status" className="text-ink">
          {fmtInt(erase.result.deleted)} notas apagadas de <SourceName id={sourceId} />.
        </p>
      ) : (
        <div className="grid gap-3">
          <label className="flex items-start gap-2 text-ink">
            <input type="checkbox" className="mt-1" checked={confirm} onChange={(e) => setConfirm(e.target.checked)} />
            Entendo que as notas de <SourceName id={sourceId} /> saem de vez.
          </label>
          {Boolean(erase.error) && <ErrorState error={erase.error} compact />}
          <div>
            <button type="button" className={btnDangerGhost} disabled={!confirm || erase.busy} onClick={() => erase.run(() => cell.eraseAgentNotes(sourceId), onDone)}>
              {erase.busy ? 'Apagando…' : 'Apagar a memória do agente'}
            </button>
          </div>
        </div>
      )}
    </Section>
  )
}

function Proposals({ canWrite }: { canWrite: boolean }) {
  const { cell, hasRole } = useSpace()
  const proposals = useResource((signal) => cell.agentProposals({ status: null }, { signal }), [cell])
  const items = proposals.data?.items ?? []
  const open = items.filter((p) => p.status === 'pending' || p.status === 'drafting')
  const decided = items.filter((p) => p.status !== 'pending' && p.status !== 'drafting')
  return (
    <div className="grid gap-6">
      {canWrite && <Distill onSent={proposals.reload} />}
      <Section title="Esperando uma pessoa" description="Uma destilação ou um agente num espaço que só aceita notas aprovadas propõe; quem administra aprova, edita antes de aprovar ou recusa. A aprovação passa pela checagem de dado pessoal de novo.">
        {(proposals.status === 'loading' || proposals.status === 'idle') && <Loading label="Carregando as propostas…" />}
        {proposals.status === 'error' && <ErrorState error={proposals.error} onRetry={proposals.reload} />}
        {proposals.status === 'success' && open.length === 0 && <Empty title="Nenhuma proposta esperando." />}
        {open.length > 0 && (
          <ul className="grid gap-3" aria-label="Propostas esperando">
            {open.map((p) => (
              <ProposalCard key={p.proposal_id} proposal={p} canDecide={hasRole('admin')} onDone={proposals.reload} />
            ))}
          </ul>
        )}
      </Section>
      {decided.length > 0 && (
        <Section title="Decididas">
          <ul className="grid gap-2">
            {decided.map((p) => (
              <li key={p.proposal_id} className="text-ink">
                <span className="font-semibold">{p.title || 'Sem título'}</span> · <SourceName id={p.source_id} /> ·{' '}
                {p.status === 'approved' ? 'aprovada' : p.status === 'rejected' ? 'recusada' : (PROBLEM[p.problem ?? ''] ?? 'falhou')}
              </li>
            ))}
          </ul>
        </Section>
      )}
    </div>
  )
}

function ProposalCard({ proposal: p, canDecide, onDone }: { proposal: AgentNoteProposal; canDecide: boolean; onDone: () => void }) {
  const { cell } = useSpace()
  const act = useAction<unknown>()
  if (p.status === 'drafting')
    return (
      <li className={`${card} px-4 py-3`}>
        <p className="text-ink">
          Destilando {p.evidence?.conversation_id ? `a conversa ${p.evidence.conversation_id}` : `a tarefa ${p.evidence?.task_id ?? ''}`} para <SourceName id={p.source_id} />. Os turnos vão mascarados ao modelo.
        </p>
      </li>
    )
  return (
    <li className={`${card} px-4 py-3`}>
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <p className="font-semibold text-ink">{p.title}</p>
        <Chip>{kindLabel(p.kind)}</Chip>
      </div>
      <p className="mt-1.5 whitespace-pre-line text-ink">{p.body}</p>
      <p className={`${small} mt-2`}>
        <SourceName id={p.source_id} withVendor /> · {p.origin === 'distilled' ? 'Destilada de' : 'Escrita pelo agente em'}{' '}
        {p.evidence?.conversation_id ? `conversa ${p.evidence.conversation_id}` : p.evidence?.task_id ? `tarefa ${p.evidence.task_id}` : 'trabalho sem id'} · {fmtDateTime(p.created_at)}
      </p>
      {Boolean(act.error) && (
        <div className="mt-3">
          <ErrorState error={act.error} compact />
        </div>
      )}
      {canDecide ? (
        <div className="mt-3 flex flex-wrap gap-2">
          <button type="button" className={btnPrimary} disabled={act.busy} onClick={() => act.run(() => cell.approveProposal(p.proposal_id), onDone)} aria-label={`Aprovar ${p.title}`}>
            Aprovar
          </button>
          <button type="button" className={btnDangerGhost} disabled={act.busy} onClick={() => act.run(() => cell.rejectProposal(p.proposal_id), onDone)} aria-label={`Recusar ${p.title}`}>
            Recusar
          </button>
        </div>
      ) : (
        <p className={`${small} mt-3`}>Quem tem o papel Administração aprova ou recusa.</p>
      )}
    </li>
  )
}

function Distill({ onSent }: { onSent: () => void }) {
  const { cell } = useSpace()
  const { list } = useSources()
  const [picked, setSource] = useState('')
  const source = picked || list[0]?.source_id || ''
  const [kind, setKind] = useState<'conversation' | 'task'>('conversation')
  const [id, setId] = useState('')
  const act = useAction<AgentNoteProposal>()
  const submit = (e: FormEvent) => {
    e.preventDefault()
    const target = id.trim()
    if (!source || !target) return
    act.run(() => cell.distill({ source_id: source, ...(kind === 'conversation' ? { conversation_id: target } : { task_id: target }) }), () => {
      setId('')
      onSent()
    })
  }
  return (
    <Section title="Destilar um procedimento" description="Escolha uma conversa ou tarefa em que o agente resolveu bem um caso. A Niadra manda os turnos já mascarados ao modelo, que só pode devolver uma nota no formato fixo; a proposta passa pela checagem de dado pessoal e espera aprovação.">
      <form className="grid gap-3 lg:max-w-3xl lg:grid-cols-[1fr_12rem_1fr_auto] lg:items-end" onSubmit={submit}>
        <AgentSelect label="Agente" value={source} onChange={setSource} />
        <Select
          label="Origem"
          value={kind}
          onChange={setKind}
          options={[
            { value: 'conversation', label: 'Conversa' },
            { value: 'task', label: 'Tarefa' },
          ]}
        />
        <TextInput label={kind === 'conversation' ? 'ID da conversa' : 'ID da tarefa'} value={id} onChange={setId} mono />
        <button type="submit" className={btnPrimary} disabled={act.busy || !id.trim() || !source}>
          {act.busy ? 'Pedindo…' : 'Destilar'}
        </button>
      </form>
      {act.result && (
        <p role="status" className={`${small} mt-3`}>
          Pedido aceito. A proposta aparece abaixo em alguns segundos.
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

function Settings() {
  const { control, space } = useSpace()
  const doc = useResource((signal) => control.configDocument(space.space_id, 'agent-memory', { signal }), [control, space.space_id])
  const canPropose = useCanPropose('agent-memory', ['integration'])
  const [draft, setDraft] = useState<AgentMemorySettingsDoc | null>(null)
  const [reason, setReason] = useState('')
  const send = useAction<DiffOut>()
  // Only the fields the document has: a version saved with the old notes cap proposes without it.
  const stored = doc.data?.document as Partial<AgentMemorySettingsDoc> | undefined
  const current: AgentMemorySettingsDoc = { enabled: stored?.enabled ?? false, writes: stored?.writes ?? 'agent' }
  const shown = draft ?? current

  if (doc.status === 'loading' || doc.status === 'idle') return <Loading label="Carregando a configuração…" />
  if (doc.status === 'error') return <ErrorState error={doc.error} onRetry={doc.reload} />
  if (send.result) return <DiffSent diff={send.result} title="Mudança enviada para aprovação." />

  const changed = JSON.stringify(shown) !== JSON.stringify(current)
  const submit = (e: FormEvent) => {
    e.preventDefault()
    if (!changed || !diffReasonOk(reason)) return
    send.run(() => control.submitDiff(space.space_id, 'agent-memory', shown, reason.trim()))
  }
  return (
    <Section
      title="Ligar e limites"
      description={
        current.enabled
          ? 'Ligada neste espaço: o bloco e as duas ferramentas estão disponíveis para os agentes.'
          : 'Desligada neste espaço: o bloco volta vazio e as ferramentas não aparecem. As notas já gravadas ficam guardadas.'
      }
    >
      <form className="grid gap-4 lg:max-w-2xl" onSubmit={submit}>
        <Select
          label="Memória dos agentes"
          value={shown.enabled ? 'on' : 'off'}
          onChange={(v) => setDraft({ ...shown, enabled: v === 'on' })}
          options={[
            { value: 'off', label: 'Desligada' },
            { value: 'on', label: 'Ligada' },
          ]}
          disabled={!canPropose}
        />
        <Select
          label="Quem grava"
          value={shown.writes}
          onChange={(v) => setDraft({ ...shown, writes: v })}
          options={[
            { value: 'agent', label: 'O agente grava direto; pessoas revisam depois' },
            { value: 'human_only', label: 'Cada nota de agente espera aprovação' },
          ]}
          disabled={!canPropose}
          hint="Cada agente grava até 20 notas por hora e guarda todas: o bloco leva as que cabem e diz quantas ficaram de fora, e a busca lê todas."
        />
        {canPropose ? (
          <>
            <DiffReason value={reason} onChange={setReason} />
            {Boolean(send.error) && <ErrorState error={send.error} compact />}
            <div>
              <button type="submit" className={btnPrimary} disabled={!changed || !diffReasonOk(reason) || send.busy}>
                {send.busy ? 'Enviando…' : 'Propor a mudança'}
              </button>
            </div>
          </>
        ) : (
          <p className={small}>Mudar exige o papel Integração ou Administração.</p>
        )}
      </form>
    </Section>
  )
}
