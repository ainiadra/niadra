import { useId, useState, type FormEvent, type ReactNode } from 'react'
import type { CoordinationOverview, CoordinationReport } from '../api/coordination'
import { describeError } from '../api/describe'
import { ApiError } from '../api/http'
import { fmtDateTime, fmtInt } from '../lib/format'
import { purpose } from '../lib/labels'
import { Retrying, useResource } from '../lib/useResource'
import { useSpace } from '../session'
import { Chip, JsonView, PageHeader } from '../ui/bits'
import { DataTable } from '../ui/DataTable'
import { useTitle } from '../ui/layout'
import { Empty, ErrorState, Loading } from '../ui/States'
import { btnPrimary, btnSecondary, card, danger, h2, hint, input, label, small } from '../ui/styles'

/**
 * Coordination of the agents: who holds each customer now, the effects that went out once,
 * the conflicts coordination settled, the contact budgets and the shadow runs. Counts only: no customer,
 * handle or message reaches this screen.
 */

const KIND: Record<string, string> = { owner: 'Dono', case: 'Caso', task_lock: 'Trava de tarefa' }
const LEVEL: Record<string, string> = {
  closed: 'Encerrado',
  human_active: 'Pessoa atendendo',
  transfer_pending: 'Transbordo pendente',
  soft_pause: 'Pausa branda',
  agent_active: 'Agente atendendo',
}
const VIA: Record<string, string> = {
  declaration: 'Declarada pela API',
  mapping: 'Evento de sistema mapeado',
  worker: 'Relatada pela integração da empresa',
}
const HOLDER: Record<string, string> = { agent: 'Agente', human: 'Pessoa', queue: 'Fila', system: 'Sistema' }
const EFFECT_KIND: Record<string, string> = {
  delivery: 'Mensagem entregue',
  document: 'Documento',
  notice: 'Aviso',
  filing: 'Protocolo',
  paid_call: 'Chamada paga',
  external_action: 'Ação externa',
}
const EFFECT_STATE: Record<string, string> = { reserved: 'Reservado', done: 'Feito', failed: 'Falhou', unknown_outcome: 'Resultado desconhecido' }
const CONTACT_STATE: Record<string, string> = { reserved: 'Reservado', made: 'Feito', expired: 'Venceu sem sair' }
const DIRECTION: Record<string, string> = { inbound: 'Recebido', outbound: 'Enviado' }
const REPORT_STATUS: Record<string, string> = { pending: 'Rodando', done: 'Pronto', failed: 'Falhou' }
const WINDOWS = [7, 30, 90] as const

const say = (words: Record<string, string>, key: string) => words[key] ?? key

export function Coordination() {
  const { space, cell } = useSpace()
  useTitle('Coordenação', space.environment === 'sandbox')
  const [days, setDays] = useState<(typeof WINDOWS)[number]>(7)
  const overview = useResource((signal) => cell.coordination.overview(days, { signal }), [days, cell])
  const off = overview.error instanceof ApiError && overview.error.status === 404

  return (
    <>
      <PageHeader
        screen="coordination"
        actions={
          off ? undefined : (
            <div role="group" aria-label="Período" className="inline-flex rounded-lg border border-edge bg-surface p-0.5">
              {WINDOWS.map((w) => (
                <button
                  key={w}
                  type="button"
                  aria-pressed={days === w}
                  onClick={() => setDays(w)}
                  className={`h-10 rounded-md px-3 text-[0.8125rem] font-semibold lg:h-8 ${days === w ? 'bg-ink text-bg' : 'text-ink2 hover:text-ink'}`}
                >
                  {w} dias
                </button>
              ))}
            </div>
          )
        }
      />
      {off ? (
        <Empty title="A coordenação não está ligada neste espaço.">
          Ela entra quando alguém com o papel de segurança inclui <span className="font-mono text-[0.8125rem] text-ink">coordination</span> no documento de recursos do espaço,
          em Configuração. Até lá, nenhum agente é consultado sobre posse, efeitos ou contato.
        </Empty>
      ) : overview.error instanceof Retrying || overview.status === 'error' ? (
        <ErrorState error={overview.error} onRetry={overview.reload} />
      ) : overview.status !== 'success' || !overview.data ? (
        <Loading label="Lendo a coordenação do espaço…" />
      ) : (
        <Overview data={overview.data} days={days} />
      )}
      {!off && <ShadowRuns />}
    </>
  )
}

function Overview({ data, days }: { data: CoordinationOverview; days: number }) {
  const c = data.conflicts
  return (
    <div className="grid min-w-0 grid-cols-1 gap-6">
      <Section tour="coordination.conflicts" title="Conflitos resolvidos" note={`Nos últimos ${days} dias.`}>
        <dl className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-5">
          <Count term="Cliente disputado" value={c.ownership_overlaps} means="Dois detentores ao mesmo tempo: o nível mais restritivo decidiu." />
          <Count term="Compromisso superado" value={c.commitments_superseded} means="Outro compromisso do cliente valeu primeiro." />
          <Count term="Transbordo sem desfecho" value={c.handoffs_expired} means="Quem recebeu não devolveu o desfecho no prazo." />
          <Count term="Token usado duas vezes" value={c.tokens_reused} means="O gateway recebeu o mesmo token de contato de novo." />
          <Count
            term="Efeito esperando decisão"
            value={data.awaiting_decision}
            means="Resultado desconhecido: nunca é reenviado sozinho. Um evento do sistema ou uma pessoa decide."
            alert={data.awaiting_decision > 0}
          />
        </dl>
      </Section>

      <Section tour="coordination.ownership" title="Posse agora" note="Afirmações que valem neste momento, por tipo, nível e origem.">
        {data.ownership.length ? (
          <DataTable
            label="Posse agora"
            head={['Tipo', 'Nível', 'Origem', 'Detentor', 'Afirmações', 'Detentores']}
            numeric={[4, 5]}
            rows={data.ownership.map((o) => [say(KIND, o.kind), say(LEVEL, o.level), say(VIA, o.via), say(HOLDER, o.holder_kind), fmtInt(o.claims), fmtInt(o.holders)])}
          />
        ) : (
          <p className={small}>Nenhum cliente tem dono declarado ou observado agora: qualquer agente pode falar com eles.</p>
        )}
      </Section>

      <Section tour="coordination.effects" title="Efeitos" note={`Reservados nos últimos ${days} dias, pela chave que garante uma vez só.`}>
        {data.effects.length ? (
          <DataTable
            label="Efeitos"
            head={['Espécie', 'Estado', 'Quantidade']}
            numeric={[2]}
            rows={data.effects.map((e) => [
              say(EFFECT_KIND, e.kind),
              e.state === 'unknown_outcome' ? <Chip tone="danger">{say(EFFECT_STATE, e.state)}</Chip> : say(EFFECT_STATE, e.state),
              fmtInt(e.effects),
            ])}
          />
        ) : (
          <p className={small}>Nenhum efeito com chave no período.</p>
        )}
      </Section>

      <Section tour="coordination.budgets" title="Orçamentos de contato" note="Somando todos os agentes e fornecedores, por cliente, na janela de cada finalidade.">
        {data.budgets.length ? (
          <DataTable
            label="Orçamentos de contato"
            head={['Finalidade', 'Limite', 'Clientes na janela', 'No limite', 'Contatos gastos']}
            numeric={[2, 3, 4]}
            rows={data.budgets.map((b) => [purpose(b.purpose), `${fmtInt(b.limit)} a cada ${span(b.per_hours)}`, fmtInt(b.subjects), fmtInt(b.at_limit), fmtInt(b.spent)])}
          />
        ) : (
          <p className={small}>Nenhuma finalidade deste espaço tem orçamento de contato.</p>
        )}
      </Section>

      <Section title="Contatos" note={`Decididos nos últimos ${days} dias, por finalidade.`}>
        {data.contacts.length ? (
          <DataTable
            label="Contatos"
            head={['Finalidade', 'Direção', 'Estado', 'Quantidade']}
            numeric={[3]}
            rows={data.contacts.map((k) => [purpose(k.purpose), say(DIRECTION, k.direction), say(CONTACT_STATE, k.state), fmtInt(k.contacts)])}
          />
        ) : (
          <p className={small}>Nenhum contato decidido no período.</p>
        )}
      </Section>
    </div>
  )
}

function span(hours: number): string {
  if (hours % 168 === 0) return hours === 168 ? 'semana' : `${hours / 168} semanas`
  if (hours % 24 === 0) return hours === 24 ? 'dia' : `${hours / 24} dias`
  return hours === 1 ? 'hora' : `${hours} horas`
}

function Section({ title, note, children, tour }: { title: string; note?: string; children: ReactNode; tour?: string }) {
  const id = useId()
  return (
    <section aria-labelledby={id} data-tour={tour} className={`${card} min-w-0 p-4 sm:p-5`}>
      <h2 id={id} className={h2}>
        {title}
      </h2>
      {note && <p className={`${small} mt-0.5`}>{note}</p>}
      <div className="mt-3">{children}</div>
    </section>
  )
}

function Count({ term, value, means, alert = false }: { term: string; value: number; means: string; alert?: boolean }) {
  return (
    <div className={`rounded-lg border px-3 py-2.5 ${alert ? `${danger.border} ${danger.wash}` : 'border-line bg-bg'}`}>
      <dt className="text-[0.75rem] font-semibold text-ink2">{term}</dt>
      <dd className={`mt-0.5 font-display text-[1.4rem] font-bold [font-stretch:92%] ${alert ? danger.text : 'text-ink'}`}>{fmtInt(value)}</dd>
      <dd className={`${small} mt-0.5`}>{means}</dd>
    </div>
  )
}

function ShadowRuns() {
  const { cell } = useSpace()
  const runs = useResource((signal) => cell.coordination.reports({ signal }), [cell])
  const ids = { days: useId(), err: useId() }
  const [days, setDays] = useState('30')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function run(e: FormEvent) {
    e.preventDefault()
    const n = Number(days)
    if (!Number.isInteger(n) || n < 1 || n > 90) return setError('De 1 a 90 dias.')
    setBusy(true)
    setError(null)
    try {
      await cell.coordination.runShadow(n)
      runs.reload()
    } catch (err) {
      const d = describeError(err)
      setError(err instanceof ApiError && err.status === 403 ? 'Rodar em sombra pede o papel de análise ou de segurança.' : `${d.title} ${d.detail}`)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="mt-6">
      <Section
        title="Execuções em sombra"
        note="A política do espaço aplicada às mensagens de saída que já chegaram, sem impor nada: mostra o que a coordenação teria mudado antes de valer."
      >
        <form onSubmit={run} noValidate className="mb-4 flex flex-wrap items-end gap-3" aria-describedby={error ? ids.err : undefined}>
          <div className="w-32">
            <label htmlFor={ids.days} className={label}>
              Dias para trás
            </label>
            <input id={ids.days} className={input} inputMode="numeric" value={days} onChange={(e) => setDays(e.target.value)} aria-invalid={Boolean(error)} />
          </div>
          <button type="submit" className={btnPrimary} disabled={busy}>
            {busy ? 'Pedindo…' : 'Rodar em sombra'}
          </button>
          {error && (
            <p id={ids.err} role="alert" className={`basis-full text-[0.8125rem] font-semibold ${danger.text}`}>
              {error}
            </p>
          )}
        </form>
        {runs.error instanceof Retrying || runs.status === 'error' ? (
          <ErrorState error={runs.error} onRetry={runs.reload} compact />
        ) : runs.status !== 'success' || !runs.data ? (
          <Loading label="Lendo as execuções…" />
        ) : runs.data.items.length ? (
          <ul aria-label="Execuções em sombra" className="divide-y divide-line rounded-lg border border-line">
            {runs.data.items.map((r) => (
              <Run key={r.report_id} report={r} />
            ))}
          </ul>
        ) : (
          <p className={small}>Nenhuma execução ainda. A primeira leva alguns minutos para ficar pronta.</p>
        )}
        <p className={hint}>A amostra para revisão humana fica 90 dias; o relatório, 13 meses.</p>
      </Section>
    </div>
  )
}

function Run({ report }: { report: CoordinationReport }) {
  const [open, setOpen] = useState(false)
  const tone = report.status === 'done' ? 'ok' : report.status === 'failed' ? 'danger' : 'neutral'
  return (
    <li className="px-4 py-3">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
        <Chip tone={tone}>{say(REPORT_STATUS, report.status)}</Chip>
        <span className="text-[0.875rem] text-ink">{report.days} dias</span>
        <span className={small}>pedido em {fmtDateTime(report.requested_at)}</span>
        {report.finished_at && <span className={small}>pronto em {fmtDateTime(report.finished_at)}</span>}
        {report.status === 'done' && (
          <button type="button" className={`${btnSecondary} ml-auto`} aria-expanded={open} onClick={() => setOpen((o) => !o)}>
            {open ? 'Fechar o relatório' : 'Ver o relatório'}
          </button>
        )}
      </div>
      {open && (
        <div className="mt-3">
          <JsonView value={report.summary} label="Relatório da execução em sombra" maxHeight="max-h-80" />
        </div>
      )}
    </li>
  )
}
