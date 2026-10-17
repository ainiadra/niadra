import { useState, type ReactNode } from 'react'
import { Link } from 'react-router-dom'
import { ApiError } from '../../api/http'
import type { ActionOut, BatchResult, CorrectionRequest, FactOut, OpenItemOut, ProfileMemory, ProfileObjects, TraitOut, TraitView } from '../../api/types'
import { fmtDate, fmtDateTime, fmtInt, fmtPct } from '../../lib/format'
import { actionStatus, category, channel, factStatus, intent, itemKind, objectType, openItemStatus, operation, outcome, predicate, purpose, receiptKind, stateValue, traitName, withheldReason } from '../../lib/labels'
import { useNextPages, useResource, type Resource } from '../../lib/useResource'
import { paths } from '../../paths'
import { useSpace } from '../../session'
import { Chip, OriginLine, SourceName } from '../../ui/bits'
import { Dialog } from '../../ui/Dialog'
import { ReasonField, reasonOk, TextInput, useAction } from '../../ui/forms'
import { Empty, ErrorState, LoadMore, Loading } from '../../ui/States'
import { btnDanger, btnQuiet, btnSecondary, card, danger, h2, mono, small } from '../../ui/styles'
import { ObjectMemory } from './ObjectMemory'
import { ObjectState, Raw } from './ObjectState'

/** Loading and error around one of the two profile reads; each tab only describes its rows. */
export function Loaded<T>({ res, loading, children }: { res: Resource<T>; loading: string; children: (data: T) => ReactNode }) {
  if (res.status === 'loading' || res.status === 'idle') return <Loading label={loading} />
  if (res.status === 'error' || !res.data) return <ErrorState error={res.error} onRetry={res.reload} />
  return <>{children(res.data)}</>
}

/** A row that came through an identifier nobody confirmed yet: shown to the roles that confirm or split links. */
function UnconfirmedChip({ item }: { item: { unconfirmed_link?: boolean } }) {
  return item.unconfirmed_link ? <Chip tone="dark">Vínculo não confirmado</Chip> : null
}

/** Items the policy of the reader's role left out entirely: only counted, by reason. */
export function WithheldNote({ withheld }: { withheld: Record<string, number> }) {
  const entries = Object.entries(withheld).filter(([, n]) => n > 0)
  if (entries.length === 0) return null
  const total = entries.reduce((n, [, v]) => n + v, 0)
  return (
    <p className={`${small} mb-3`}>
      {fmtInt(total)} {total === 1 ? 'item fica' : 'itens ficam'} fora da sua vista pela política do seu papel: {entries.map(([r, n]) => `${withheldReason(r).toLowerCase()} ${fmtInt(n)}`).join(', ')}.
    </p>
  )
}

const Masked = () => <span className="italic text-ink2">conteúdo mascarado para o seu papel</span>

export function FactsTab({ memory, profileId, onChanged }: { memory: ProfileMemory; profileId: string; onChanged: () => void }) {
  const { hasRole } = useSpace()
  const [viewers, setViewers] = useState<FactOut | null>(null)
  const [history, setHistory] = useState<FactOut | null>(null)
  const [correcting, setCorrecting] = useState<{ fact: FactOut; action: 'correct_fact' | 'retract_fact' } | null>(null)
  const [restoring, setRestoring] = useState<FactOut | null>(null)
  const canCorrect = hasRole('security')
  if (memory.facts.length === 0) return <Empty title="Nenhum fato guardado sobre este cliente.">Fatos nascem das conversas, dos eventos de sistema e das ações dos agentes.</Empty>
  return (
    <>
      <ul className={`${card} divide-y divide-line2`}>
        {memory.facts.map((f) => (
          <li key={f.id} className="px-4 py-3">
            <div className="flex flex-wrap items-baseline justify-between gap-2">
              <div className="flex min-w-0 flex-wrap items-baseline gap-x-2 gap-y-1">
                <Raw value={predicate(f.predicate)} className="text-ink2" />
                {f.masked || f.value === null ? <Masked /> : <span className="font-semibold text-ink">{f.value}</span>}
                {f.status !== 'active' && <Chip tone={f.status === 'quarantined' ? 'danger' : 'neutral'}>{factStatus(f.status)}</Chip>}
                {f.sensitivity !== 'normal' && <Chip tone="dark">{f.sensitivity === 'sensitive' ? 'Categoria sensível' : 'Restrito'}</Chip>}
                <UnconfirmedChip item={f} />
              </div>
              <div className="flex flex-wrap items-center gap-1">
                {canCorrect && f.status === 'active' && !f.masked && (
                  <button type="button" className={btnQuiet} onClick={() => setCorrecting({ fact: f, action: 'correct_fact' })} aria-label={`Corrigir o fato ${predicate(f.predicate).label}`}>
                    Corrigir
                  </button>
                )}
                {canCorrect && f.status === 'active' && (
                  <button type="button" className={`${btnQuiet} ${danger.text}`} onClick={() => setCorrecting({ fact: f, action: 'retract_fact' })} aria-label={`Retirar o fato ${predicate(f.predicate).label}`}>
                    Retirar
                  </button>
                )}
                {/* A row of an unconfirmed link never comes back through the confirmed memory. */}
                {canCorrect && f.status === 'retracted' && !f.unconfirmed_link && (
                  <button type="button" className={btnQuiet} onClick={() => setRestoring(f)} aria-label={`Restaurar o fato ${predicate(f.predicate).label}`}>
                    Restaurar
                  </button>
                )}
                <button type="button" className={btnQuiet} onClick={() => setHistory(f)} aria-label={`Histórico do fato ${predicate(f.predicate).label}`}>
                  Histórico
                </button>
                <button type="button" className={btnQuiet} onClick={() => setViewers(f)} aria-label={`Quem leu o fato ${predicate(f.predicate).label}`}>
                  Quem leu
                </button>
              </div>
            </div>
            <p className={`${small} mt-0.5`}>
              {[category(f.category).label, `confiança ${fmtPct(f.confidence)}`, f.times_seen > 1 ? `visto ${f.times_seen} vezes` : null, `vale desde ${fmtDate(f.valid_at)}`, `confirmado em ${fmtDate(f.last_confirmed_at)}`]
                .filter(Boolean)
                .join(' · ')}
            </p>
            <OriginLine origin={f.origin} className="mt-1" />
          </li>
        ))}
      </ul>
      {viewers && <ViewersDialog fact={viewers} onClose={() => setViewers(null)} />}
      {history && <HistoryDialog fact={history} profileId={profileId} onClose={() => setHistory(null)} />}
      {correcting && (
        <CorrectionDialog
          title={correcting.action === 'correct_fact' ? `Corrigir ${predicate(correcting.fact.predicate).label}` : `Retirar ${predicate(correcting.fact.predicate).label}`}
          request={{ profile_id: profileId, action: correcting.action, fact_id: correcting.fact.id }}
          current={correcting.fact.value}
          onClose={() => setCorrecting(null)}
          onDone={onChanged}
        />
      )}
      {restoring && <RestoreDialog fact={restoring} profileId={profileId} onClose={() => setRestoring(null)} onDone={onChanged} />}
    </>
  )
}

/** Why the cell refused a restore, in the Console's words; `null` for any other failure. */
function restoreRefusal(error: unknown): string | null {
  if (!(error instanceof ApiError) || error.code !== 'conflict') return null
  const detail = error.detail ?? ''
  if (detail.includes('unconfirmed link')) return 'O fato veio por um identificador que ninguém confirmou. Confirme o vínculo em Identidade antes de restaurar.'
  if (detail.includes('erased')) return 'A evidência deste fato foi apagada a pedido do titular. Um fato apagado não volta.'
  if (detail.includes('merged')) return 'A consolidação uniu este fato a outro, que já guarda o valor. Corrija aquele, se for o caso.'
  if (detail.includes('retracted')) return 'Só um fato retirado pode ser restaurado. Recarregue a tela e confira o estado atual.'
  return null
}

/** Undoing a retraction or a correction: the cell records it as an event and memory brings the fact back. */
function RestoreDialog({ fact, profileId, onClose, onDone }: { fact: FactOut; profileId: string; onClose: () => void; onDone: () => void }) {
  const { cell } = useSpace()
  const act = useAction<BatchResult>()
  const [reason, setReason] = useState('')
  const refusal = restoreRefusal(act.error)
  return (
    <Dialog open onClose={onClose} title={`Restaurar ${predicate(fact.predicate).label}`}>
      {act.result ? (
        <div role="status">
          <p className="font-semibold text-ink">
            {act.result.duplicates
              ? 'Este pedido já tinha sido registrado.'
              : 'Restauração registrada. A memória aplica em alguns segundos, e o contexto dos agentes é refeito com o fato.'}
          </p>
          <div className="mt-5 flex justify-end">
            <button
              type="button"
              className={btnSecondary}
              onClick={() => {
                onDone()
                onClose()
              }}
            >
              Concluir
            </button>
          </div>
        </div>
      ) : (
        <form
          className="grid gap-4"
          onSubmit={(e) => {
            e.preventDefault()
            if (reasonOk(reason)) void act.run(() => cell.restoreFact(fact.id, { profile_id: profileId, reason: reason.trim() }))
          }}
        >
          <p className="text-ink">
            O fato volta a valer como era para todos os agentes, e a correção que o substituiu sai do contexto. O histórico do fato guarda cada passo, com o seu pedido como evidência.
          </p>
          <ReasonField value={reason} onChange={setReason} />
          {refusal ? (
            <p role="alert" className={`font-semibold ${danger.text}`}>
              {refusal} Nada mudou na memória.
            </p>
          ) : (
            Boolean(act.error) && <ErrorState error={act.error} compact />
          )}
          <div className="flex flex-wrap justify-end gap-2">
            <button type="button" className={btnSecondary} onClick={onClose}>
              Cancelar
            </button>
            <button type="submit" className={btnSecondary} disabled={act.busy || !reasonOk(reason)}>
              {act.busy ? 'Restaurando…' : 'Restaurar o fato'}
            </button>
          </div>
        </form>
      )}
    </Dialog>
  )
}

const CORRECTION_COPY: Record<CorrectionRequest['action'], { intro: string; button: string; busy: string; done: string }> = {
  correct_fact: {
    intro: 'O valor novo passa a valer para todos os agentes. O antigo sai do contexto e fica no histórico do fato como retirado, com o seu pedido como evidência.',
    button: 'Corrigir o fato',
    busy: 'Corrigindo…',
    done: 'Correção registrada. A memória aplica em alguns segundos, e o contexto dos agentes é refeito sem o valor antigo.',
  },
  retract_fact: {
    intro: 'O fato sai do contexto de todos os agentes e fica no histórico como retirado, com o seu pedido como evidência. Ele só volta se o cliente o afirmar de novo.',
    button: 'Retirar o fato',
    busy: 'Retirando…',
    done: 'Retirada registrada. A memória aplica em alguns segundos, e o contexto dos agentes é refeito sem o fato.',
  },
  resolve_open_item: {
    intro: 'A pendência fecha para todos os agentes, com o seu pedido como evidência, e o aviso de pendência fechada sai para os endpoints que o assinam.',
    button: 'Dar como resolvida',
    busy: 'Fechando…',
    done: 'Registrado. A memória fecha a pendência em alguns segundos.',
  },
}

/** Correction as a data subject's right: the cell records it as an event and memory applies it. */
function CorrectionDialog({
  title,
  request,
  current,
  onClose,
  onDone,
}: {
  title: string
  request: CorrectionRequest
  current?: string | null
  onClose: () => void
  onDone: () => void
}) {
  const { cell } = useSpace()
  const act = useAction<BatchResult>()
  const [value, setValue] = useState(current ?? '')
  const [reason, setReason] = useState('')
  const copy = CORRECTION_COPY[request.action]
  const needsValue = request.action === 'correct_fact'
  const ready = reasonOk(reason) && (!needsValue || (value.trim() !== '' && value.trim() !== (current ?? '').trim()))
  const refused = act.result?.errors[0]
  return (
    <Dialog open onClose={onClose} title={title}>
      {act.result && !refused ? (
        <div role="status">
          <p className="font-semibold text-ink">{act.result.duplicates ? 'Este pedido já tinha sido registrado.' : copy.done}</p>
          <div className="mt-5 flex justify-end">
            <button
              type="button"
              className={btnSecondary}
              onClick={() => {
                onDone()
                onClose()
              }}
            >
              Concluir
            </button>
          </div>
        </div>
      ) : (
        <form
          className="grid gap-4"
          onSubmit={(e) => {
            e.preventDefault()
            if (ready) void act.run(() => cell.correct({ ...request, reason: reason.trim(), ...(needsValue ? { value: value.trim() } : {}) }))
          }}
        >
          <p className="text-ink">{copy.intro}</p>
          {needsValue && <TextInput label="Valor correto" value={value} onChange={setValue} maxLength={2000} required />}
          <ReasonField value={reason} onChange={setReason} />
          {Boolean(act.error) && <ErrorState error={act.error} compact />}
          {refused && (
            <p role="alert" className={`font-semibold ${danger.text}`}>
              A célula recusou o pedido ({refused.code}). Nada mudou na memória.
            </p>
          )}
          <div className="flex flex-wrap justify-end gap-2">
            <button type="button" className={btnSecondary} onClick={onClose}>
              Cancelar
            </button>
            <button type="submit" className={request.action === 'retract_fact' ? btnDanger : btnSecondary} disabled={act.busy || !ready}>
              {act.busy ? copy.busy : copy.button}
            </button>
          </div>
        </form>
      )}
    </Dialog>
  )
}

/** "Who saw fact F?": every receipt that delivered it. */
function ViewersDialog({ fact, onClose }: { fact: FactOut; onClose: () => void }) {
  const { cell, space } = useSpace()
  const res = useResource((signal) => cell.factViewers(fact.id, { signal }), [fact.id, cell])
  return (
    <Dialog open onClose={onClose} title={`Quem leu ${predicate(fact.predicate).label}`} wide>
      {(res.status === 'loading' || res.status === 'idle') && <Loading label="Buscando os comprovantes deste fato…" />}
      {res.status === 'error' && <ErrorState error={res.error} compact />}
      {res.status === 'success' && res.data && res.data.receipts.length === 0 && <p className={small}>Nenhuma leitura entregou este fato.</p>}
      {res.status === 'success' && res.data && res.data.receipts.length > 0 && (
        <ul className="divide-y divide-line2">
          {res.data.receipts.map((r) => (
            <li key={r.receipt_id} className="flex flex-wrap items-baseline justify-between gap-2 py-2.5">
              <span>
                <span className="font-semibold text-ink">{receiptKind(r.kind)}</span> <span className="text-ink2">por</span> <SourceName id={r.source_id} withVendor />
                <span className={`${mono} block text-ink2`}>{fmtDateTime(r.read_at)}</span>
              </span>
              <Link to={paths.receipt(space.space_id, r.receipt_id)} className="text-[0.8125rem] font-semibold text-ink underline decoration-accent decoration-2 underline-offset-4">
                Abrir comprovante
              </Link>
            </li>
          ))}
        </ul>
      )}
    </Dialog>
  )
}

const RELATION_WORDS: Record<string, string> = { supersedes: 'substituiu', contradicts: 'contradiz' }

/** Every value the fact's slot held, oldest first, under the policy of the reader's role. */
function HistoryDialog({ fact, profileId, onClose }: { fact: FactOut; profileId: string; onClose: () => void }) {
  const { cell } = useSpace()
  const res = useResource((signal) => cell.factHistory(profileId, fact.id, { signal }), [profileId, fact.id, cell])
  const byId = new Map((res.data?.versions ?? []).map((v) => [v.id, v]))
  const edges = (res.data?.relations ?? []).filter((r) => byId.has(r.from_fact_id) && byId.has(r.to_fact_id))
  return (
    <Dialog open onClose={onClose} title={`Histórico de ${predicate(fact.predicate).label}`} wide>
      {(res.status === 'loading' || res.status === 'idle') && <Loading label="Buscando os valores deste fato…" />}
      {res.status === 'error' && <ErrorState error={res.error} compact />}
      {res.status === 'success' && res.data && (
        <>
          <p className={`${small} mb-3`}>Cada valor que o fato teve, do mais antigo ao mais recente, com a origem de cada um. Nada é apagado por uma correção: o valor antigo fica aqui como retirado.</p>
          <WithheldNote withheld={res.data.withheld} />
          <ol className="divide-y divide-line2">
            {res.data.versions.map((v) => (
              <li key={v.id} className="py-2.5">
                <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
                  {v.masked || v.value === null ? <Masked /> : <span className="font-semibold text-ink">{v.value}</span>}
                  <Chip tone={v.status === 'active' ? 'ok' : v.status === 'quarantined' ? 'danger' : 'neutral'}>{factStatus(v.status)}</Chip>
                  {v.id === fact.id && <Chip tone="dark">Este</Chip>}
                </div>
                <p className={`${small} mt-0.5`}>
                  {[`vale desde ${fmtDate(v.valid_at)}`, v.invalid_at ? `até ${fmtDate(v.invalid_at)}` : null, v.times_seen > 1 ? `visto ${v.times_seen} vezes` : null, `confiança ${fmtPct(v.confidence)}`]
                    .filter(Boolean)
                    .join(' · ')}
                </p>
                <OriginLine origin={v.origin} className="mt-1" />
              </li>
            ))}
          </ol>
          {edges.length > 0 && (
            <ul className={`${small} mt-3 space-y-1`}>
              {edges.map((r) => (
                <li key={`${r.from_fact_id}-${r.type}-${r.to_fact_id}`}>
                  “{byId.get(r.from_fact_id)?.value ?? 'mascarado'}” {RELATION_WORDS[r.type] ?? r.type} “{byId.get(r.to_fact_id)?.value ?? 'mascarado'}”
                </li>
              ))}
            </ul>
          )}
        </>
      )}
    </Dialog>
  )
}

const itemTone = (s: string) => (s === 'overdue' ? 'danger' : s === 'resolved' ? 'ok' : 'neutral')

export function OpenItemsTab({ memory, profileId, onChanged }: { memory: ProfileMemory; profileId: string; onChanged: () => void }) {
  const { hasRole } = useSpace()
  const [resolving, setResolving] = useState<OpenItemOut | null>(null)
  const canCorrect = hasRole('security')
  if (memory.open_items.length === 0)
    return <Empty title="Nenhuma pendência ou promessa registrada.">Quando a empresa ou o cliente se compromete com algo, o item aparece aqui até ser fechado por uma ação.</Empty>
  return (
    <>
      <OpenItemList items={memory.open_items} onResolve={canCorrect ? setResolving : undefined} />
      {resolving && (
        <CorrectionDialog
          title="Dar a pendência como resolvida"
          request={{ profile_id: profileId, action: 'resolve_open_item', open_item_id: resolving.id }}
          onClose={() => setResolving(null)}
          onDone={onChanged}
        />
      )}
    </>
  )
}

function OpenItemList({ items, onResolve }: { items: OpenItemOut[]; onResolve?: (item: OpenItemOut) => void }) {
  return (
    <ul className={`${card} divide-y divide-line2`}>
      {items.map((i) => (
        <li key={i.id} className="px-4 py-3">
          <div className="flex flex-wrap items-start justify-between gap-2">
            <p className="font-semibold text-ink">{i.masked || i.description === null ? <Masked /> : i.description}</p>
            <div className="flex flex-wrap items-center gap-2">
              {onResolve && i.status !== 'resolved' && (
                <button type="button" className={btnQuiet} onClick={() => onResolve(i)} aria-label={`Dar como resolvida: ${i.masked || !i.description ? 'pendência' : i.description}`}>
                  Dar como resolvida
                </button>
              )}
              <Chip tone={itemTone(i.status)}>{openItemStatus(i.status)}</Chip>
              <UnconfirmedChip item={i} />
            </div>
          </div>
          <p className={`${small} mt-0.5`}>
            {i.promise ? 'Prometido pela empresa' : i.owner === 'customer' ? 'Combinado com o cliente' : 'Pendência da empresa'}
            {i.due_at && (
              <>
                {' · prazo '}
                <span className={i.status === 'overdue' ? `font-semibold ${danger.text}` : ''}>{fmtDateTime(i.due_at)}</span>
              </>
            )}
            {i.object && ` · ${objectType(i.object.type)} ${i.object.id}`}
            {i.expected_operation && ` · fecha com ${operation(i.expected_operation).label}`}
          </p>
          {i.closed_by_action_id && (
            <p className={`${mono} mt-0.5 text-ink2`}>
              fechada pela ação {i.closed_by_action_id}
              {i.closed_at ? ` em ${fmtDateTime(i.closed_at)}` : ''}
            </p>
          )}
          <OriginLine origin={i.origin} className="mt-1" />
        </li>
      ))}
    </ul>
  )
}

export function EpisodesTab({ memory }: { memory: ProfileMemory }) {
  const { cell } = useSpace()
  const more = useNextPages(
    memory,
    memory.timeline_next,
    (cursor) => cell.memory(memory.profile_id, {}, { timeline_cursor: cursor }),
    (page) => page.timeline_next,
  )
  const timeline = [...memory.timeline, ...more.pages.flatMap((page) => page.timeline)]
  if (timeline.length === 0) return <Empty title="Nenhuma conversa ou tarefa processada ainda.">Cada conversa encerrada e cada tarefa de agente interno viram um episódio.</Empty>
  return (
    <div>
    <ol className={`${card} divide-y divide-line2`}>
      {timeline.map((e) => (
        <li key={e.id} className="grid gap-x-4 gap-y-1 px-4 py-3 sm:grid-cols-[8.5rem_minmax(0,1fr)]">
          <div className={`${mono} text-ink2`}>
            <time dateTime={e.started_at}>{fmtDateTime(e.started_at)}</time>
            <span className="block">{channel(e.origin.channel)}</span>
          </div>
          <div className="min-w-0">
            <p className="text-ink">
              {e.masked || e.summary === null ? <Masked /> : e.summary} <UnconfirmedChip item={e} />
            </p>
            <p className={`${small} mt-0.5`}>
              {[category(e.category).label, `pedido: ${intent(e.intent).label.toLowerCase()}`, `desfecho: ${outcome(e.outcome).label.toLowerCase()}`, e.resolution && `solução: ${e.resolution}`].filter(Boolean).join(' · ')}
            </p>
            <OriginLine origin={e.origin} className="mt-1" />
          </div>
        </li>
      ))}
    </ol>
    <LoadMore hasMore={more.hasMore} loading={more.loading} error={more.error} onClick={more.loadMore} />
    </div>
  )
}

/** Patterns with their rule, expiry and evidence; a wrong one is retracted with a tombstone (20). */
export function TraitsTab({ traits, profileId, onChanged }: { traits: TraitOut[]; profileId: string; onChanged: () => void }) {
  const { cell, hasRole } = useSpace()
  const [retracting, setRetracting] = useState<TraitOut | null>(null)
  const [optOut, setOptOut] = useState(false)
  const canCurate = hasRole('security', 'integration')
  return (
    <div className="grid gap-3">
      {traits.length === 0 ? (
        <Empty title="Nenhum padrão para este cliente.">Um padrão nasce de uma regra com evidências, como a terceira reclamação sobre o mesmo assunto em 90 dias, e expira no prazo da regra.</Empty>
      ) : (
        <ul className="grid gap-3">
          {traits.map((t) => (
            <TraitCard key={t.id} trait={t} action={canCurate && t.active ? () => setRetracting(t) : undefined} />
          ))}
        </ul>
      )}
      {hasRole('security') && (
        <div className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-line px-4 py-3">
          <p className={small}>O cliente se opôs aos padrões? A memória para de derivar e retira os que existem.</p>
          <button type="button" className={btnSecondary} onClick={() => setOptOut(true)}>
            Tirar dos padrões
          </button>
        </div>
      )}
      {retracting && (
        <RetractTraitDialog
          trait={retracting}
          onClose={() => setRetracting(null)}
          onDone={() => {
            setRetracting(null)
            onChanged()
          }}
          retract={() => cell.retractTrait(retracting.id)}
        />
      )}
      {optOut && <OptOutDialog profileId={profileId} onClose={() => setOptOut(false)} onDone={onChanged} />}
    </div>
  )
}

function TraitCard({ trait: t, action }: { trait: TraitOut; action?: () => void }) {
  return (
    <li className={`${card} px-4 py-3`}>
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
          <span className="font-semibold text-ink">{traitName(t.name)}</span>
          {t.masked ? <Masked /> : t.value && <span className="text-ink">{t.value}</span>}
          <Chip>{t.layer === 'stable' ? 'Estável' : 'Volátil'}</Chip>
          {t.retracted_at ? <Chip tone="danger">Retirado em {fmtDate(t.retracted_at)}</Chip> : !t.active && <Chip>Expirado</Chip>}
          <UnconfirmedChip item={t} />
        </div>
        {action && (
          <button type="button" className={`${btnQuiet} ${danger.text}`} onClick={action} aria-label={`Retirar o padrão ${traitName(t.name)}`}>
            Retirar
          </button>
        )}
      </div>
      <p className={`${small} mt-0.5`}>
        regra {t.rule_version} · primeira evidência {fmtDate(t.first_seen)} · última {fmtDate(t.last_evidence_at)} · vale até {fmtDate(t.expires_at)} · confiança {fmtPct(t.confidence)}
      </p>
      <details className="mt-2">
        <summary className="inline-flex min-h-11 cursor-pointer items-center text-[0.8125rem] font-semibold text-ink lg:min-h-8">
          {t.evidence.length === 1 ? '1 evidência' : `${t.evidence.length} evidências`}
        </summary>
        <ul className="mt-1 grid gap-1.5 border-t border-line2 pt-2">
          {t.evidence.map((ev) => (
            <li key={`${ev.kind}:${ev.id}`}>
              <span className={`${mono} text-ink`}>
                {itemKind(ev.kind)} {ev.id}
              </span>
              {ev.origin && <OriginLine origin={ev.origin} />}
            </li>
          ))}
        </ul>
      </details>
    </li>
  )
}

function RetractTraitDialog({ trait, onClose, onDone, retract }: { trait: TraitOut; onClose: () => void; onDone: () => void; retract: () => Promise<TraitView> }) {
  const act = useAction<TraitView>()
  return (
    <Dialog open onClose={onClose} title="Retirar o padrão">
      <p className="text-ink">
        <strong>{traitName(trait.name)}</strong> sai do contexto de todos os agentes. A retirada deixa uma lápide, e o padrão só volta com evidência nova.
      </p>
      {Boolean(act.error) && (
        <div className="mt-4">
          <ErrorState error={act.error} compact />
        </div>
      )}
      <div className="mt-5 flex flex-wrap justify-end gap-2">
        <button type="button" className={btnSecondary} onClick={onClose}>
          Cancelar
        </button>
        <button type="button" className={btnDanger} disabled={act.busy} onClick={() => act.run(retract, onDone)}>
          {act.busy ? 'Retirando…' : 'Retirar o padrão'}
        </button>
      </div>
    </Dialog>
  )
}

function OptOutDialog({ profileId, onClose, onDone }: { profileId: string; onClose: () => void; onDone: () => void }) {
  const { cell } = useSpace()
  const act = useAction<{ profile_id: string; opted_out_at: string }>()
  return (
    <Dialog open onClose={onClose} title="Tirar o cliente dos padrões">
      {act.result ? (
        <div role="status">
          <p className="font-semibold text-ink">Feito em {fmtDateTime(act.result.opted_out_at)}.</p>
          <p className={`${small} mt-1`}>Nenhum padrão novo nasce para este cliente, e os existentes saem do contexto.</p>
          <div className="mt-5 flex justify-end">
            <button
              type="button"
              className={btnSecondary}
              onClick={() => {
                onDone()
                onClose()
              }}
            >
              Concluir
            </button>
          </div>
        </div>
      ) : (
        <>
          <p className="text-ink">A memória para de derivar padrões para este cliente e retira os que existem. Use quando o titular se opõe a esse tratamento.</p>
          {Boolean(act.error) && (
            <div className="mt-4">
              <ErrorState error={act.error} compact />
            </div>
          )}
          <div className="mt-5 flex flex-wrap justify-end gap-2">
            <button type="button" className={btnSecondary} onClick={onClose}>
              Cancelar
            </button>
            <button type="button" className={btnDanger} disabled={act.busy} onClick={() => act.run(() => cell.optOutOfTraits(profileId))}>
              {act.busy ? 'Aplicando…' : 'Tirar dos padrões'}
            </button>
          </div>
        </>
      )}
    </Dialog>
  )
}

function ActionRow({ a }: { a: ActionOut }) {
  return (
    <li className="px-4 py-3">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <p className="font-semibold text-ink">
          <Raw value={operation(a.operation)} />
        </p>
        <Chip tone={a.status === 'confirmed' ? 'ok' : a.status === 'declared' ? 'neutral' : 'danger'}>{actionStatus(a.status)}</Chip>
      </div>
      <p className={`${small} mt-0.5`}>
        {[a.masked ? 'resultado mascarado para o seu papel' : a.result && `resultado: ${a.result}`, a.purpose && `finalidade: ${purpose(a.purpose)}`, a.closes_item_id && `fecha a pendência ${a.closes_item_id}`]
          .filter(Boolean)
          .join(' · ') || 'sem resultado informado'}
      </p>
      <OriginLine origin={a.origin} className="mt-1" />
    </li>
  )
}

const objectName = (o: { ref: { type: string; id: string } }) => `${objectType(o.ref.type)} ${o.ref.id}`

/** The state the system sends says where the object is, when it names a state or a status. */
const stateOf = (state: Record<string, unknown> | null) => {
  const v = state?.state ?? state?.status
  return typeof v === 'string' ? stateValue(v) : null
}

/**
 * The objects and the actions on no object of the pages read so far: a later page of objects brings their
 * own actions, a later page of actions goes on the objects already listed or, if on none, among the loose.
 */
function merged(first: ProfileObjects, objectPages: ProfileObjects[], actionPages: ProfileObjects[]): ProfileObjects {
  const listed = new Map<string, ProfileObjects['objects'][number]>()
  for (const o of [...first.objects, ...objectPages.flatMap((page) => page.objects)]) if (!listed.has(o.id)) listed.set(o.id, o)
  const seen = new Set([...listed.values()].flatMap((o) => o.actions.map((a) => a.id)).concat(first.actions.map((a) => a.id)))
  const loose = [...first.actions]
  for (const page of actionPages) {
    for (const o of page.objects) {
      const mine = listed.get(o.id)
      const fresh = o.actions.filter((a) => !seen.has(a.id))
      fresh.forEach((a) => seen.add(a.id))
      if (mine) listed.set(o.id, { ...mine, actions: [...mine.actions, ...fresh] })
      else loose.push(...fresh)
    }
    for (const a of page.actions) {
      if (seen.has(a.id)) continue
      seen.add(a.id)
      loose.push(a)
    }
  }
  return { ...first, objects: [...listed.values()], actions: loose }
}

export function ObjectsTab({ objects: first }: { objects: ProfileObjects }) {
  const { cell } = useSpace()
  const moreObjects = useNextPages(
    first,
    first.objects_next,
    (cursor) => cell.objects(first.profile_id, {}, { objects_cursor: cursor }),
    (page) => page.objects_next,
  )
  const moreActions = useNextPages(
    first,
    first.actions_next,
    (cursor) => cell.objects(first.profile_id, {}, { actions_cursor: cursor }),
    (page) => page.actions_next,
  )
  const objects = merged(first, moreObjects.pages, moreActions.pages)
  const loose = objects.actions
  return (
    <div className="grid grid-cols-1 gap-6">
      <WithheldNote withheld={objects.withheld} />
      {objects.objects.length === 0 && loose.length === 0 && <Empty title="Nenhum pedido, ticket, fatura ou ação de agente ligado a este cliente." />}
      {objects.objects.length > 0 && (
        <section aria-labelledby="obj-title">
          <h3 id="obj-title" className={`${h2} mb-3`}>
            Objetos de negócio
          </h3>
          <ul className="grid min-w-0 grid-cols-1 gap-3" aria-label="Objetos de negócio ligados ao cliente">
            {objects.objects.map((o) => {
              const where = o.masked ? null : stateOf(o.state)
              return (
                <li key={o.id} className={`${card} min-w-0 p-4`}>
                  <div className="flex flex-wrap items-start justify-between gap-x-4 gap-y-2">
                    <div className="min-w-0">
                      <p className="flex flex-wrap items-center gap-2 font-semibold text-ink">
                        <span title={`${o.ref.type} ${o.ref.namespace}/${o.ref.id}`}>{objectName(o)}</span>
                        {where && <Chip tone={/cancel|fail|lost|overdue|disput/.test(where.raw ?? where.label) ? 'danger' : /deliver|paid|done|complet|resolv|credit/.test(where.raw ?? '') ? 'ok' : 'neutral'}>{where.label}</Chip>}
                        <Chip>{o.role === 'owner' ? 'Titular' : 'Participante'}</Chip>
                        <UnconfirmedChip item={o} />
                      </p>
                      <p className={`${small} mt-0.5`}>
                        Atualizado em {fmtDateTime(o.as_of)} · fonte <SourceName id={o.source_id} />
                        {o.actions.length > 0 && ` · ${fmtInt(o.actions.length)} ${o.actions.length === 1 ? 'ação de agente' : 'ações de agentes'}`}
                      </p>
                    </div>
                    <span className={`${mono} text-ink2`}>
                      {o.ref.namespace}/{o.ref.id}
                    </span>
                  </div>
                  <div className="mt-3 border-t border-line2 pt-3">{o.masked ? <Masked /> : o.state ? <ObjectState state={o.state} name={objectName(o)} /> : <p className={small}>O sistema ainda não mandou o estado deste objeto.</p>}</div>
                  <ObjectMemory memory={o.memory} profileId={objects.profile_id} />
                </li>
              )
            })}
          </ul>
          <LoadMore hasMore={moreObjects.hasMore} loading={moreObjects.loading} error={moreObjects.error} onClick={moreObjects.loadMore} />
        </section>
      )}
      {(objects.objects.some((o) => o.actions.length > 0) || loose.length > 0) && (
        <section aria-labelledby="act-title">
          <h3 id="act-title" className={`${h2} mb-3`}>
            Ações de agentes
          </h3>
          <div className="grid gap-4">
            {objects.objects
              .filter((o) => o.actions.length > 0)
              .map((o) => (
                <div key={o.id}>
                  <p className="mb-1.5 text-[0.8125rem] font-semibold text-ink">
                    Em {objectName(o)} <span className={`${mono} font-normal text-ink2`}>{o.ref.namespace}/{o.ref.id}</span>
                  </p>
                  <ul className={`${card} divide-y divide-line2`}>
                    {o.actions.map((a) => (
                      <ActionRow key={a.id} a={a} />
                    ))}
                  </ul>
                </div>
              ))}
            {loose.length > 0 && (
              <div>
                <p className="mb-1.5 text-[0.8125rem] font-semibold text-ink">Sem objeto</p>
                <ul className={`${card} divide-y divide-line2`}>
                  {loose.map((a) => (
                    <ActionRow key={a.id} a={a} />
                  ))}
                </ul>
              </div>
            )}
          </div>
          <LoadMore hasMore={moreActions.hasMore} loading={moreActions.loading} error={moreActions.error} onClick={moreActions.loadMore} />
        </section>
      )}
    </div>
  )
}
