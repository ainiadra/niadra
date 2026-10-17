import { Retrying } from '../../lib/useResource'
import { useEffect, useId, useState, type FormEvent } from 'react'
import { Select } from '../../../components/Select'
import type { SimulatedLive, SimulateResponse, Verification } from '../../api/types'
import { fmtDateTime, fmtInt } from '../../lib/format'
import { audience, category, channel, effect, itemKind, speaker, VERIFICATIONS, VIEWS, verification, withheldMeans, withheldReason } from '../../lib/labels'
import { useSpace } from '../../session'
import { Chip, useSources } from '../../ui/bits'
import { Icon } from '../../ui/Icon'
import { ErrorState, Loading } from '../../ui/States'
import { btnPrimary, btnQuiet, card, danger, h2, hint, input, label, mono, select, small } from '../../ui/styles'

const TASK = '__task__'
const TASK_RE = /^[a-z0-9_]{1,40}$/

/**
 * "What would agent X see of customer Y at level V1?". The
 * policy simulation shows the context the agent would receive and the effect of
 * the policy on every item, and delivers nothing to any agent.
 */
export function ContextPanel({ profileId }: { profileId: string }) {
  const { cell } = useSpace()
  const sources = useSources()
  const readers = sources.list
  const [sourceId, setSourceId] = useState('')
  const [view, setView] = useState('chat')
  const [task, setTask] = useState('')
  const [level, setLevel] = useState<Verification>('V1')
  const [state, setState] = useState<{ status: 'idle' | 'loading' | 'success' | 'error'; data?: SimulateResponse; error?: unknown; asked?: string }>({ status: 'idle' })
  const ids = { source: useId(), view: useId(), task: useId(), level: useId(), body: useId() }
  const [open, setOpen] = useState(true)

  useEffect(() => {
    if (!sourceId && readers.length > 0) setSourceId((readers.find((s) => !s.revoked_at) ?? readers[0]!).source_id)
  }, [readers, sourceId])

  const effectiveView = view === TASK ? `task:${task.trim()}` : view
  const taskInvalid = view === TASK && !TASK_RE.test(task.trim())

  async function simulate(e: FormEvent) {
    e.preventDefault()
    if (!sourceId || taskInvalid) return
    const src = sources.byId.get(sourceId)
    const viewLabel = view === TASK ? `tarefa ${task.trim()}` : (VIEWS.find((v) => v.value === view)?.label ?? view)
    const asked = `${src?.name ?? sourceId} · visão ${viewLabel} · pedido ${verification(level)}`
    setState({ status: 'loading', asked })
    try {
      const data = await cell.simulate({ profile_id: profileId, source_id: sourceId, view: effectiveView, verification: level })
      setState({ status: 'success', data, asked })
    } catch (error) {
      setState({ status: 'error', error, asked })
    }
  }

  return (
    <section id="contexto-do-agente" aria-labelledby="ctx-title" data-tour="profile.context" className="scroll-mt-20">
      <div className={`${card} p-4 sm:p-5`}>
        <div className="flex flex-wrap items-start justify-between gap-x-4 gap-y-2">
          <div className="min-w-0">
            <h2 id="ctx-title" className={h2}>
              O contexto que o agente recebe
            </h2>
            {open && <p className={`${small} mt-1`}>Escolha o agente, a visão e o nível de verificação da sessão. A política da empresa decide o que entra e o que fica retido.</p>}
          </div>
          <button type="button" className={`${btnQuiet} ml-auto shrink-0`} aria-expanded={open} aria-controls={ids.body} onClick={() => setOpen((v) => !v)}>
            {open ? 'Recolher' : 'Mostrar'}
          </button>
        </div>
        <div id={ids.body} hidden={!open}>

        {sources.status === 'error' && (
          <p role="alert" className={`mt-4 text-[0.8125rem] font-semibold ${danger.text}`}>
            {sources.error instanceof Retrying ? 'A região pediu um instante para a lista de agentes; o Console tenta de novo.' : 'A lista de agentes não carregou.'}{' '}
            <button type="button" className="min-h-11 underline decoration-2 underline-offset-4 lg:min-h-0" onClick={sources.reload}>
              Tentar de novo
            </button>
          </p>
        )}

        <form onSubmit={simulate} className="mt-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-[minmax(0,2fr)_minmax(0,1fr)_minmax(0,1fr)_auto] lg:items-end">
          <div className="min-w-0">
            <label htmlFor={ids.source} className={label}>
              Agente
            </label>
            <Select
              id={ids.source}
              triggerClassName={select}
              value={sourceId}
              onChange={setSourceId}
              disabled={readers.length === 0}
              options={
                readers.length === 0
                  ? [{ value: '', label: sources.status === 'loading' ? 'Carregando agentes…' : 'Nenhuma fonte neste espaço' }]
                  : readers.map((s) => ({
                      value: s.source_id,
                      label: `${s.name}${s.vendor ? ` (${s.vendor})` : ''}${s.revoked_at ? ', acesso cortado' : ''}`,
                    }))
              }
            />
          </div>
          <div className="contents">
            <div className="min-w-0">
              <label htmlFor={ids.view} className={label}>
                Visão
              </label>
              <Select
                id={ids.view}
                triggerClassName={select}
                value={view}
                onChange={setView}
                options={[...VIEWS.map((v) => ({ value: v.value as string, label: v.label })), { value: TASK, label: 'Tarefa de agente interno' }]}
              />
            </div>
            <div className="min-w-0">
              <label htmlFor={ids.level} className={label}>
                Nível de verificação
              </label>
              <Select
                id={ids.level}
                triggerClassName={select}
                value={level}
                onChange={setLevel}
                options={VERIFICATIONS.map((v) => ({ value: v, label: verification(v) }))}
              />
            </div>
          </div>
          {view === TASK && (
            <div className="sm:col-span-2 lg:col-span-3">
              <label htmlFor={ids.task} className={label}>
                Nome da tarefa
              </label>
              <input
                id={ids.task}
                className={`${input} font-mono`}
                placeholder="billing"
                value={task}
                onChange={(e) => setTask(e.target.value)}
                aria-invalid={task.length > 0 && taskInvalid}
                aria-describedby={`${ids.task}-hint`}
                autoComplete="off"
              />
              <p id={`${ids.task}-hint`} className={task.length > 0 && taskInvalid ? `mt-1.5 text-[0.8125rem] font-semibold ${danger.text}` : hint}>
                O nome da visão de tarefa configurada na empresa: letras minúsculas, números e sublinhado.
              </p>
            </div>
          )}
          <button type="submit" className={`${btnPrimary} sm:col-span-2 sm:justify-self-start lg:col-span-1 lg:col-start-4 lg:row-start-1`} disabled={!sourceId || taskInvalid || state.status === 'loading'}>
            <Icon name="eye" size={16} />
            Ver o que o agente recebe
          </button>
        </form>

        <div className={state.status === 'idle' ? '' : 'mt-5'} aria-live="polite">
          {state.status === 'loading' && <Loading label="Montando o contexto com a política atual…" className="py-4" />}
          {state.status === 'error' && <ErrorState error={state.error} compact />}
          {state.status === 'success' && state.data && <SimulationResult data={state.data} asked={state.asked ?? ''} requested={level} />}
        </div>
        <p className={`${small} mt-3`}>Simulação: nada é entregue a nenhum agente. A consulta fica registrada como comprovante do Console em seu nome.</p>
        </div>
      </div>
    </section>
  )
}

function SimulationResult({ data, asked, requested }: { data: SimulateResponse; asked: string; requested: Verification }) {
  const lowered = data.verification !== requested
  const withheld = Object.entries(data.withheld).filter(([, n]) => n > 0)
  const total = withheld.reduce((n, [, v]) => n + v, 0)
  const notDelivered = data.items.filter((i) => i.effect === 'deny' || !i.delivered)

  return (
    <div className="grid gap-4 border-t border-line pt-4 lg:grid-cols-[minmax(0,3fr)_minmax(0,2fr)]">
      <div className="lg:col-span-2">
        <p className="text-[0.8125rem] text-ink2">{asked}</p>
        <p className="mt-1 font-semibold text-ink">
          Entregue em {verification(data.verification)}
          {lowered && <span className="font-normal text-ink2"> (pedido {verification(requested)}, acima do que a sessão ou a fonte permite)</span>}
        </p>
        <p className={`${mono} mt-1 text-ink2`}>
          audiência {audience(data.audience)} · política {data.policy_version} · {fmtInt(data.items.length)} itens avaliados
        </p>
      </div>

      <figure>
        <figcaption className="mb-1.5 text-[0.8125rem] font-semibold text-ink">Exatamente o que o agente lê</figcaption>
        {data.text.trim() ? (
          <pre className="invert-block max-h-[28rem] overflow-auto whitespace-pre-wrap break-words rounded-xl p-4 font-mono text-[0.75rem] leading-[1.2rem]" tabIndex={0}>
            {data.text}
          </pre>
        ) : (
          <p className={`${small} rounded-xl border border-line bg-bg p-4`}>O contexto sai vazio para este agente, nesta visão e neste nível.</p>
        )}
      </figure>

      <div className="grid content-start gap-4">
      <div>
        <h3 className="text-[0.875rem] font-semibold text-ink">{total === 0 ? 'Nada retido' : `Retidos: ${fmtInt(total)}`}</h3>
        {total === 0 ? (
          <p className={`${small} mt-0.5`}>A política liberou tudo o que era relevante para este agente neste nível.</p>
        ) : (
          <ul className="mt-2 grid gap-2">
            {withheld.map(([reason, n]) => (
              <li key={reason} className="rounded-lg border border-line px-3 py-2.5">
                <p className="text-[0.875rem] font-semibold text-ink">
                  {withheldReason(reason)} <span className="font-normal text-ink2">({fmtInt(n)})</span>
                </p>
                {withheldMeans(reason) && <p className={`${small} mt-0.5`}>{withheldMeans(reason)}</p>}
              </li>
            ))}
          </ul>
        )}
      </div>

      <LiveBlock live={data.live} />
      </div>

      {data.items.length > 0 && (
        <details className="lg:col-span-2">
          <summary className="inline-flex min-h-11 cursor-pointer items-center text-[0.8125rem] font-semibold text-ink lg:min-h-8">
            Efeito da política em cada item ({notDelivered.length} fora do contexto)
          </summary>
          <ul className="mt-2 grid gap-1.5 border-t border-line2 pt-2">
            {data.items.map((i) => (
              <li key={i.id} className="text-[0.8125rem] text-ink">
                <span className="flex flex-wrap items-baseline gap-1.5">
                  <Chip tone={i.effect === 'deny' ? 'danger' : i.delivered ? 'ok' : 'neutral'}>{i.effect === 'allow' && !i.delivered ? (i.reason === 'unused' ? 'Não usado' : 'Não coube') : effect(i.effect)}</Chip>
                  {itemKind(i.kind)} · {category(i.category).label}
                </span>
                <span className={`${mono} block text-ink2`}>
                  {i.id}
                  {i.reason ? ` · ${withheldReason(i.reason)}` : ''}
                  {i.rule ? ` · regra ${i.rule}` : ''}
                </span>
              </li>
            ))}
          </ul>
        </details>
      )}
    </div>
  )
}

/**
 * The `live` block of the same read: recent turns of the customer's other
 * conversations, through the same policy as the pack. What the agent would get is shown as it would
 * get it; what the policy withholds shows only when, where and why, never the text.
 */
function LiveBlock({ live }: { live: SimulatedLive }) {
  const withheld = Object.entries(live.withheld).filter(([, n]) => n > 0)
  const held = withheld.reduce((n, [, v]) => n + v, 0)
  return (
    <div>
      <h3 className="text-[0.875rem] font-semibold text-ink">Falas recentes de outros canais</h3>
      <p className={`${small} mt-0.5`}>
        O bloco ao vivo: o que o cliente disse em outras conversas depois deste contexto, pela mesma política. {fmtInt(live.delivered)} entregues
        {held > 0 ? `, ${fmtInt(held)} retidas.` : '.'}
      </p>
      {!live.complete && <p className={`${small} mt-1`}>A camada recente não pôde ser lida inteira agora; a lista pode estar curta.</p>}
      {live.turns.length === 0 ? (
        live.complete && <p className={`${small} mt-2 rounded-xl border border-line bg-bg p-3`}>Nenhuma fala recente de outros canais depois deste contexto.</p>
      ) : (
        <ul className="mt-2 grid gap-1.5" aria-label="Falas recentes de outros canais">
          {live.turns.map((t, i) => (
            <li key={`${t.at}-${i}`} className="rounded-lg border border-line px-3 py-2 text-[0.8125rem]">
              <span className="flex flex-wrap items-baseline gap-1.5">
                <Chip tone={t.delivered ? 'ok' : 'danger'}>{t.delivered ? effect(t.effect) : 'Retida'}</Chip>
                <span className="text-ink2">
                  {channel(t.channel)} · {speaker(t.speaker)} · {fmtDateTime(t.at)}
                </span>
              </span>
              {t.delivered ? (
                <span className="mt-1 block whitespace-pre-wrap break-words text-ink">{t.text}</span>
              ) : (
                <span className={`${mono} mt-1 block text-ink2`}>
                  {withheldReason(t.reason ?? 'policy')}
                  {t.rule ? ` · regra ${t.rule}` : ''}
                </span>
              )}
            </li>
          ))}
        </ul>
      )}
      {withheld.length > 0 && (
        <p className={`${small} mt-2`}>
          Retidas por motivo: {withheld.map(([reason, n]) => `${withheldReason(reason)} (${fmtInt(n)})`).join(', ')}.
        </p>
      )}
    </div>
  )
}
