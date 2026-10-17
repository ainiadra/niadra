import { useId, useState } from 'react'
import { Select } from '../../components/Select'
import type { GapOut, GapReview, GapReviewIn, GapsReport as GapsReportOut, ProposalOut, RecurringOut } from '../api/types'
import { fmtDateTime, fmtInt, fmtPct } from '../lib/format'
import { askedSubject, objectType } from '../lib/labels'
import { useResource } from '../lib/useResource'
import { useSpace } from '../session'
import { Chip, PageHeader } from '../ui/bits'
import { Section, useAction } from '../ui/forms'
import { useTitle } from '../ui/layout'
import { Empty, ErrorState, Loading } from '../ui/States'
import { btnQuiet, btnSecondary, card, mono, select, small, td, th } from '../ui/styles'

const PERIODS = [
  { value: '7', label: '7 dias' },
  { value: '30', label: '30 dias' },
  { value: '90', label: '90 dias' },
] as const

/**
 * Knowledge gaps and standing answers (13, 6.5): what customers ask that no system sends to memory, what
 * agents have to ask returning customers for, which source could record it, and the questions memory answers
 * every time by the same rule. Counts by subject and category only: no customer, value or text reaches the
 * screen, and a group with fewer customers than the space's minimum is withheld.
 */
export function Gaps() {
  const { space, cell, hasRole } = useSpace()
  useTitle('Lacunas e respostas prontas', space.environment === 'sandbox')
  const [days, setDays] = useState(30)
  const periodId = useId()
  const allowed = hasRole('analysis')
  const report = useResource((signal) => cell.gaps(days, { signal }), [days, cell], allowed)

  if (!allowed)
    return (
      <>
        <PageHeader screen="gaps" />
        <Empty title="As lacunas exigem o papel Análise.">Quem administra o Console da sua empresa pode dar o papel.</Empty>
      </>
    )

  return (
    <>
      <PageHeader
        screen="gaps"
        actions={
          <div className="flex items-center gap-2">
            <label htmlFor={periodId} className="text-[0.8125rem] font-semibold text-ink">
              Período
            </label>
            <div className="w-32">
              <Select id={periodId} triggerClassName={select} value={String(days) as '7' | '30' | '90'} onChange={(d) => setDays(Number(d))} options={[...PERIODS]} />
            </div>
          </div>
        }
      />
      {(report.status === 'loading' || report.status === 'idle') && <Loading label="Somando as lacunas do período…" />}
      {report.status === 'error' && <ErrorState error={report.error} onRetry={report.reload} />}
      {report.status === 'success' && report.data && (
        <GapsReport report={report.data} onReviewed={report.reload} />
      )}
    </>
  )
}

/** The gaps by kind: what memory had no record of, then the objects the conversations named that no system sent. */
function GapsReport({ report, onReviewed }: { report: GapsReportOut; onReviewed: () => void }) {
  const gaps = report.gaps.filter((g) => g.kind !== 'object_unresolved')
  const unresolved = report.gaps.filter((g) => g.kind === 'object_unresolved')
  return (
        <div className="grid grid-cols-1 gap-6">
          <Section
            tour="gaps.table"
            title="Lacunas de conhecimento"
            description="Cada linha junta um assunto e a categoria da conversa. Sem fonte que grave, a lacuna é estrutural: nenhum sistema manda esse dado para a memória. Com fonte, o sistema manda o tipo, mas não para esses clientes."
          >
            {gaps.length === 0 ? (
              <Empty
                title="Nenhuma lacuna com clientes suficientes no período."
                example={<>rastreio · entrega · 23 clientes · 31 turnos · a memória respondeu 10% · quem poderia gravar: o ERP, pelo mapeamento</>}
              >
                Uma lacuna aparece quando clientes diferentes perguntam a mesma coisa e a memória não tinha o registro, ou o agente teve de perguntar de novo a um cliente que já voltava. Só entra aqui um grupo com clientes suficientes: um grupo com menos clientes do que o mínimo do espaço fica de fora
                {report.withheld_groups > 0 ? ` (${fmtInt(report.withheld_groups)} ${report.withheld_groups === 1 ? 'grupo' : 'grupos'} neste período)` : ''}. Com poucas conversas, amplie o período para 90 dias; com muitas, as lacunas chegam com as próximas conversas medidas.
              </Empty>
            ) : (
              <GapsTable gaps={gaps} onReviewed={onReviewed} />
            )}
            {gaps.length > 0 && report.withheld_groups > 0 && (
              <p className={`${small} mt-2`}>{fmtInt(report.withheld_groups)} grupos ficaram de fora: poucos clientes, ou uma categoria que a política não abre para análise.</p>
            )}
          </Section>

          {unresolved.length > 0 && (
            <Section
              title="Objetos citados sem cadastro"
              description="Conversas que citaram um objeto pela chave do tipo (um número de processo, de pedido) que nenhum sistema mandou para este cliente. O que foi dito ficou na pessoa, sem o objeto: mandar o objeto pelo sistema de registro, ou marcar o objeto no evento, prende a memória a ele."
            >
              <UnresolvedTable gaps={unresolved} />
            </Section>
          )}

          <Section
            tour="gaps.recurring"
            title="Perguntas recorrentes"
            description="As perguntas que o espaço mais recebe e quanto a memória respondeu. Acima de 90%, a resposta sai pela regra quase sempre."
          >
            {report.recurring.length === 0 ? (
              <Empty title="Nenhuma pergunta recorrente com clientes suficientes no período." example={<>fatura · cobrança · 40 clientes · 61 turnos · a memória respondeu 95%</>}>
                Uma pergunta vira recorrente quando clientes suficientes a fazem no período. Ela chega com as conversas medidas; nada precisa ser configurado.
              </Empty>
            ) : (
              <RecurringTable items={report.recurring} />
            )}
          </Section>

          <Section
            tour="gaps.proposals"
            title="Respostas prontas propostas"
            description="Linhas escritas por regra para o seu time aprovar, como se aprova o esquema. Nada entra no contexto dos agentes sem essa aprovação."
          >
            {report.proposals.length === 0 ? (
              <Empty title="Nenhuma resposta pronta proposta no período." example={<>rastreio · entrega · "O rastreio do pedido fica no portal da transportadora; o agente informa o código quando o sistema de pedidos o enviar." · aprovar ou dispensar</>}>
                Uma resposta pronta é proposta a partir de uma lacuna que nenhuma fonte grava: a regra escreve a linha, e o seu time aprova antes de ela entrar no contexto dos agentes. Ela aparece quando uma lacuna estrutural tiver clientes suficientes.
              </Empty>
            ) : (
              <ul aria-label="Respostas prontas propostas" className="grid gap-3">
                {report.proposals.map((p) => (
                  <ProposalCard key={p.key} proposal={p} onReviewed={onReviewed} />
                ))}
              </ul>
            )}
          </Section>
        </div>
  )
}

const topic = (t: string) => (t ? t.replace(/_/g, ' ') : 'sem categoria medida')

function Sources({ gap }: { gap: GapOut }) {
  if (gap.could_record.length === 0) return <Chip tone="dark">Nenhuma fonte grava</Chip>
  return (
    <span className="flex flex-wrap gap-1">
      {gap.could_record.map((s) => (
        <Chip key={`${s.source_id}:${s.via}`}>
          {s.name} · {s.via === 'mapping' ? 'mapeamento' : 'extração'}
        </Chip>
      ))}
    </span>
  )
}

function GapsTable({ gaps, onReviewed }: { gaps: GapOut[]; onReviewed: () => void }) {
  return (
    <div className={`${card} overflow-x-auto`}>
      <table className="w-full min-w-[760px] border-collapse text-[0.875rem]" aria-label="Lacunas de conhecimento">
        <thead>
          <tr>
            <th className={th}>O que faltou</th>
            <th className={th}>Categoria</th>
            <th className={`${th} text-right`}>Clientes</th>
            <th className={`${th} text-right`}>Vezes</th>
            <th className={th}>Quem poderia gravar</th>
            <th className={th}>Decisão do time</th>
          </tr>
        </thead>
        <tbody>
          {gaps.map((g) => (
            <tr key={g.key}>
              <td className={td}>
                <p className="font-semibold text-ink">{askedSubject(g.subject)}</p>
                <p className={small}>{g.kind === 'no_record' ? 'o cliente pediu e a memória não tinha' : 'o agente precisou perguntar ao cliente'}</p>
                {g.answered_share != null && <p className={small}>respondido em {fmtPct(g.answered_share)} das vezes</p>}
              </td>
              <td className={td}>{topic(g.topic)}</td>
              <td className={`${td} text-right tabular-nums`}>{fmtInt(g.customers)}</td>
              <td className={`${td} text-right tabular-nums`}>{fmtInt(g.turns)}</td>
              <td className={td}>
                <Sources gap={g} />
              </td>
              <td className={td}>
                <Decision itemKey={g.key} review={g.review} label={askedSubject(g.subject)} onReviewed={onReviewed} />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function UnresolvedTable({ gaps }: { gaps: GapOut[] }) {
  return (
    <div className={`${card} overflow-x-auto`}>
      <table className="w-full min-w-[560px] border-collapse text-[0.875rem]" aria-label="Objetos citados sem cadastro">
        <thead>
          <tr>
            <th className={th}>Tipo de objeto</th>
            <th className={th}>Categoria</th>
            <th className={`${th} text-right`}>Clientes</th>
            <th className={`${th} text-right`}>Vezes</th>
          </tr>
        </thead>
        <tbody>
          {gaps.map((g) => (
            <tr key={g.key}>
              <td className={`${td} font-semibold text-ink`}>{objectType(g.subject)}</td>
              <td className={td}>{topic(g.topic)}</td>
              <td className={`${td} text-right tabular-nums`}>{fmtInt(g.customers)}</td>
              <td className={`${td} text-right tabular-nums`}>{fmtInt(g.turns)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function RecurringTable({ items }: { items: RecurringOut[] }) {
  return (
    <div className={`${card} overflow-x-auto`}>
      <table className="w-full min-w-[560px] border-collapse text-[0.875rem]" aria-label="Perguntas recorrentes">
        <thead>
          <tr>
            <th className={th}>Pergunta sobre</th>
            <th className={th}>Categoria</th>
            <th className={`${th} text-right`}>Clientes</th>
            <th className={`${th} text-right`}>Vezes</th>
            <th className={`${th} text-right`}>Respondida</th>
          </tr>
        </thead>
        <tbody>
          {items.map((r) => (
            <tr key={r.key}>
              <td className={`${td} font-semibold text-ink`}>{askedSubject(r.subject)}</td>
              <td className={td}>{topic(r.topic)}</td>
              <td className={`${td} text-right tabular-nums`}>{fmtInt(r.customers)}</td>
              <td className={`${td} text-right tabular-nums`}>{fmtInt(r.turns)}</td>
              <td className={`${td} text-right tabular-nums`}>{fmtPct(r.answered_share)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function ProposalCard({ proposal: p, onReviewed }: { proposal: ProposalOut; onReviewed: () => void }) {
  return (
    <li className={`${card} px-4 py-4`}>
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <p className="font-semibold text-ink">
          {askedSubject(p.subject)} <span className="font-normal text-ink2">· {topic(p.topic)}</span>
        </p>
        <Chip tone={p.kind === 'not_recorded' ? 'dark' : 'neutral'}>{p.kind === 'not_recorded' ? 'Nenhuma fonte grava' : 'Respondida pela regra'}</Chip>
      </div>
      <p className="mt-2 text-ink">{p.text}</p>
      <div className="mt-3">
        <Decision itemKey={p.key} review={p.review} label={`a resposta pronta sobre ${askedSubject(p.subject)}`} onReviewed={onReviewed} />
      </div>
    </li>
  )
}

/** The team's word, kept with who gave it; a later decision replaces it. */
function Decision({ itemKey, review, label, onReviewed }: { itemKey: string; review: GapReview | null; label: string; onReviewed: () => void }) {
  const { cell } = useSpace()
  const act = useAction<GapReview>()
  const current = act.result ?? review
  const decide = (decision: GapReviewIn['decision']) => act.run(() => cell.reviewGap({ key: itemKey, decision }), onReviewed)
  return (
    <div className="grid gap-1.5">
      {current && (
        <p className={small}>
          <Chip tone={current.decision === 'accepted' ? 'ok' : 'neutral'}>{current.decision === 'accepted' ? 'Aceita' : 'Dispensada'}</Chip>{' '}
          <span className={mono}>{current.decided_by}</span> · {fmtDateTime(current.decided_at)}
        </p>
      )}
      <div className="flex flex-wrap gap-1.5">
        <button type="button" className={btnSecondary} disabled={act.busy} onClick={() => decide('accepted')} aria-label={`Aceitar ${label}`}>
          Aceitar
        </button>
        <button type="button" className={btnQuiet} disabled={act.busy} onClick={() => decide('dismissed')} aria-label={`Dispensar ${label}`}>
          Dispensar
        </button>
      </div>
      {Boolean(act.error) && <ErrorState error={act.error} compact />}
    </div>
  )
}
