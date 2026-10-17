import { useId, useState } from 'react'
import { Select } from '../../components/Select'
import type { AgreementReport, ReviewCases, ReviewSample, VerdictIn, VerdictsResponse } from '../api/types'
import { fmtDateTime, fmtInt, fmtPct } from '../lib/format'
import { agreementTarget, category, channel, predicate, purpose, sampleCondition, signal, SIGNALS, traitName } from '../lib/labels'
import { useResource } from '../lib/useResource'
import { useSpace } from '../session'
import { Chip, PageHeader, SourceName } from '../ui/bits'
import { Section, useAction } from '../ui/forms'
import { useTitle } from '../ui/layout'
import { Empty, ErrorState, Loading } from '../ui/States'
import { btnPrimary, card, mono, select, small, td, th } from '../ui/styles'

const FACT_VERDICTS = [
  { value: 'correct', label: 'Correto' },
  { value: 'wrong', label: 'Errado' },
  { value: 'missing', label: 'Faltou' },
]
const SIGNAL_VERDICTS = [
  { value: 'holds', label: 'Confere' },
  { value: 'does_not_hold', label: 'Não confere' },
]

/**
 * Review by sample: the tenant's own people judge what the
 * memory extracted and measured, with the mask of each session's purpose. The
 * agreement says how far the measurement can be trusted; nothing here serves a customer.
 */
export function Review() {
  const { space, cell, hasRole } = useSpace()
  useTitle('Revisão', space.environment === 'sandbox')
  const [days, setDays] = useState(90)
  const periodId = useId()
  const queue = useResource((signal) => cell.reviewQueue({ signal }), [cell], hasRole('review'))
  const agreement = useResource((signal) => cell.agreement(days, { signal }), [days, cell], hasRole('review'))
  const cases = useResource((signal) => cell.reviewCases(days, { signal }), [days, cell], hasRole('review'))

  if (!hasRole('review'))
    return (
      <>
        <PageHeader screen="review" />
        <Empty title="A revisão por amostra exige o papel Revisão.">Quem administra o Console da sua empresa pode dar o papel.</Empty>
      </>
    )

  return (
    <>
      <PageHeader
        screen="review"
      />
      <div className="grid grid-cols-1 gap-6">
        <Section tour="review.queue" title="Amostras para revisar">
          {(queue.status === 'loading' || queue.status === 'idle') && <Loading label="Carregando a fila de amostras…" />}
          {queue.status === 'error' && <ErrorState error={queue.error} onRetry={queue.reload} />}
          {queue.status === 'success' && queue.data && queue.data.items.length === 0 && <Empty title="Nenhuma amostra esperando revisão.">
              A amostragem sorteia conversas encerradas pela taxa configurada em Configuração, no documento Amostragem de revisão. Cada amostra traz os fatos, as pendências e os sinais de uma conversa, para o seu time marcar certo, errado ou faltou.
            </Empty>}
          {queue.status === 'success' && queue.data && queue.data.items.length > 0 && <Queue items={queue.data.items} onDone={agreement.reload} />}
        </Section>

        <Section
          tour="review.agreement"
          title="Concordância"
          description="Quanto o seu time concorda com o que o sistema extraiu e mediu, por tipo de item e por sinal da medição, e quanto os revisores concordam entre si."
          actions={
            <div className="flex items-center gap-2">
              <label htmlFor={periodId} className="text-[0.8125rem] font-semibold text-ink">
                Período
              </label>
              <div className="w-32">
                <Select
                  id={periodId}
                  triggerClassName={select}
                  value={String(days) as '30' | '90' | '365'}
                  onChange={(d) => setDays(Number(d))}
                  options={[
                    { value: '30', label: '30 dias' },
                    { value: '90', label: '90 dias' },
                    { value: '365', label: '365 dias' },
                  ]}
                />
              </div>
            </div>
          }
        >
          {(agreement.status === 'loading' || agreement.status === 'idle') && <Loading label="Calculando a concordância…" />}
          {agreement.status === 'error' && <ErrorState error={agreement.error} onRetry={agreement.reload} compact />}
          {agreement.status === 'success' && agreement.data && <AgreementTable report={agreement.data} />}
        </Section>

        <Section
          tour="review.cases"
          title="Casos de teste"
          description="Cada veredito do período roda contra a memória e a medição de hoje. Um extrator novo ou regras novas de medição mostram aqui, na hora, os casos julgados que agora respondem diferente."
        >
          {(cases.status === 'loading' || cases.status === 'idle') && <Loading label="Rodando os casos…" />}
          {cases.status === 'error' && <ErrorState error={cases.error} onRetry={cases.reload} compact />}
          {cases.status === 'success' && cases.data && <CasesTable report={cases.data} />}
        </Section>
      </div>
    </>
  )
}

/** Whether the sample has anything this person can mark: a fact, a pending item, a pattern or a signal. */
const judgeable = (s: ReviewSample) => s.facts.length + s.open_items.length + s.traits.length + s.signals.length > 0

/**
 * The queue: the samples with something to judge, each a card. The API leaves out a sample with nothing to
 * judge for anyone, so the ones that arrive empty hold only items this person may not see: they go in one
 * line, since a card with no item and a button for zero verdicts would only mislead.
 */
function Queue({ items, onDone }: { items: ReviewSample[]; onDone: () => void }) {
  const ready = items.filter(judgeable)
  const hidden = items.filter((s) => !judgeable(s))
  return (
    <div className="grid gap-4">
      {ready.length === 0 && (
        <Empty title="Nenhuma amostra com algo para você julgar agora.">
          {fmtInt(hidden.length)} {hidden.length === 1 ? 'conversa sorteada só tem' : 'conversas sorteadas só têm'} itens fora da sua vista, pela finalidade da amostra ou pela política do espaço. Quem pode ver esses itens julga essas amostras.
        </Empty>
      )}
      {ready.length > 0 && (
        <ul className="grid gap-4">
          {ready.map((s) => (
            <SampleCard key={s.sample_id} sample={s} onDone={onDone} />
          ))}
        </ul>
      )}
      {hidden.length > 0 && ready.length > 0 && <HiddenSamples samples={hidden} />}
    </div>
  )
}

function HiddenSamples({ samples }: { samples: ReviewSample[] }) {
  return (
    <details className={`${card} px-4 py-3`}>
      <summary className="inline-flex min-h-11 cursor-pointer items-center text-[0.875rem] font-semibold text-ink lg:min-h-8">
        {fmtInt(samples.length)} {samples.length === 1 ? 'amostra só com itens fora da sua vista' : 'amostras só com itens fora da sua vista'}
      </summary>
      <p className={`${small} mt-1 max-w-[72ch]`}>
        A finalidade da amostra ou a política do espaço não libera estes itens para você. Quem pode ver esses itens julga essas amostras, e nenhum veredito seu fica em falta por elas.
      </p>
      <ul className="mt-3 grid gap-1.5" aria-label="Amostras só com itens fora da sua vista">
        {samples.map((s) => (
          <li key={s.sample_id} className="text-[0.8125rem] text-ink2">
            <span className="text-ink">
              <SourceName id={s.source_id} withVendor />
            </span>{' '}
            · {channel(s.channel)}
            {s.purpose ? ` · finalidade ${purpose(s.purpose)}` : ''} · {sampleCondition(s.condition)} · {fmtDateTime(s.created_at)} · {fmtInt(s.withheld)}{' '}
            {s.withheld === 1 ? 'item fora da sua vista' : 'itens fora da sua vista'}
          </li>
        ))}
      </ul>
    </details>
  )
}

function SampleCard({ sample: s, onDone }: { sample: ReviewSample; onDone: () => void }) {
  const { cell } = useSpace()
  const [verdicts, setVerdicts] = useState<Record<string, VerdictIn>>({})
  const act = useAction<VerdictsResponse>()
  const set = (v: VerdictIn) => setVerdicts((m) => ({ ...m, [`${v.target}:${v.target_id}`]: v }))
  const chosen = Object.values(verdicts)

  if (act.result)
    return (
      <li className={`${card} px-4 py-3`} role="status">
        <p className="text-ink">
          <strong>{fmtInt(act.result.recorded)} vereditos gravados</strong> para a amostra <span className={mono}>{s.sample_id}</span> em {fmtDateTime(act.result.reviewed_at)}.
        </p>
      </li>
    )

  return (
    <li className={`${card} px-4 py-4`}>
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <p className="text-ink">
          <SourceName id={s.source_id} withVendor /> · {channel(s.channel)}
          {s.purpose && <span className="text-ink2"> · finalidade {purpose(s.purpose)}</span>}
        </p>
        <span className="text-[0.8125rem] text-ink2">
          {sampleCondition(s.condition)} · {fmtDateTime(s.created_at)}
        </span>
      </div>
      {s.withheld > 0 && <p className={`${small} mt-1`}>{fmtInt(s.withheld)} itens ficam fora da sua vista pela finalidade desta sessão.</p>}

      {s.facts.length > 0 && (
        <fieldset className="mt-4">
          <legend className="mb-2 text-[0.8125rem] font-semibold text-ink">Fatos extraídos</legend>
          <ul className="grid gap-3">
            {s.facts.map((f) => (
              <li key={f.fact_id} className="border-t border-line2 pt-3">
                <p className="text-ink">
                  <span className="text-ink2" title={predicate(f.predicate).raw ?? undefined}>{predicate(f.predicate).label}</span> <strong>{f.value}</strong> {f.sensitive && <Chip tone="dark">Categoria sensível</Chip>}
                </p>
                <p className={`${small} mt-0.5`}>
                  {category(f.category).label} · {f.evidence_event_ids.length} {f.evidence_event_ids.length === 1 ? 'evento de origem' : 'eventos de origem'}
                </p>
                <Choice name={`fact-${f.fact_id}`} options={FACT_VERDICTS} value={verdicts[`fact:${f.fact_id}`]?.verdict} onChange={(v) => set({ target: 'fact', target_id: f.fact_id, verdict: v })} label={`Veredito sobre ${f.predicate}`} />
              </li>
            ))}
          </ul>
        </fieldset>
      )}

      {s.open_items.length > 0 && (
        <fieldset className="mt-4">
          <legend className="mb-2 text-[0.8125rem] font-semibold text-ink">Pendências e promessas</legend>
          <ul className="grid gap-3">
            {s.open_items.map((i) => (
              <li key={i.item_id} className="border-t border-line2 pt-3">
                <p className="text-ink">
                  <strong>{i.description}</strong> {i.promise ? <Chip tone="dark">Promessa da empresa</Chip> : <Chip>Pendência do cliente</Chip>}
                </p>
                <p className={`${small} mt-0.5`}>
                  {i.due_at ? `prazo ${fmtDateTime(i.due_at)} · ` : ''}
                  {i.evidence_event_ids.length} evento(s) de origem
                </p>
                <Choice name={`item-${i.item_id}`} options={FACT_VERDICTS} value={verdicts[`open_item:${i.item_id}`]?.verdict} onChange={(v) => set({ target: 'open_item', target_id: i.item_id, verdict: v })} label={`Veredito sobre ${i.description}`} />
              </li>
            ))}
          </ul>
        </fieldset>
      )}

      {s.traits.length > 0 && (
        <fieldset className="mt-4">
          <legend className="mb-2 text-[0.8125rem] font-semibold text-ink">Padrões que se apoiam nesta conversa</legend>
          <ul className="grid gap-3">
            {s.traits.map((t) => (
              <li key={t.trait_id} className="border-t border-line2 pt-3">
                <p className="text-ink">
                  <span className="text-ink2">{traitName(t.name)}</span> <strong>{t.value}</strong>
                </p>
                <p className={`${small} mt-0.5`}>
                  desde {fmtDateTime(t.first_seen)} · confiança {fmtPct(t.confidence)}
                </p>
                <Choice name={`trait-${t.trait_id}`} options={SIGNAL_VERDICTS} value={verdicts[`trait:${t.trait_id}`]?.verdict} onChange={(v) => set({ target: 'trait', target_id: t.trait_id, verdict: v })} label={`Veredito sobre ${t.name}`} />
              </li>
            ))}
          </ul>
        </fieldset>
      )}

      {s.signals.length > 0 && (
        <fieldset className="mt-4">
          <legend className="mb-2 text-[0.8125rem] font-semibold text-ink">Sinais da medição</legend>
          <ul className="grid gap-3">
            {s.signals.map((g) => (
              <li key={g.target_id} className="border-t border-line2 pt-3">
                <p className="text-ink">
                  <strong>{signal(g.signal)}</strong> <span className={`${mono} text-ink2`}>item {g.item_id}</span>
                </p>
                <Choice name={`signal-${g.target_id}`} options={SIGNAL_VERDICTS} value={verdicts[`signal:${g.target_id}`]?.verdict} onChange={(v) => set({ target: 'signal', target_id: g.target_id, verdict: v })} label={`Veredito sobre ${signal(g.signal)}`} />
              </li>
            ))}
          </ul>
        </fieldset>
      )}

      {Boolean(act.error) && (
        <div className="mt-4">
          <ErrorState error={act.error} compact />
        </div>
      )}
      <div className="mt-4 flex flex-wrap items-center gap-3">
        <button type="button" className={btnPrimary} disabled={act.busy || chosen.length === 0} onClick={() => act.run(() => cell.verdicts(s.sample_id, chosen), onDone)}>
          {act.busy ? 'Gravando…' : chosen.length === 0 ? 'Gravar os vereditos' : `Gravar ${chosen.length} ${chosen.length === 1 ? 'veredito' : 'vereditos'}`}
        </button>
      </div>
    </li>
  )
}

function Choice({ name, options, value, onChange, label: text }: { name: string; options: { value: string; label: string }[]; value?: string; onChange: (v: string) => void; label: string }) {
  return (
    <div role="radiogroup" aria-label={text} className="mt-2 inline-flex flex-wrap rounded-lg border border-edge bg-surface p-0.5">
      {options.map((o) => (
        <label
          key={o.value}
          className={`inline-flex min-h-10 cursor-pointer items-center rounded-md px-3 text-[0.875rem] font-semibold has-[:focus-visible]:outline has-[:focus-visible]:outline-2 has-[:focus-visible]:outline-ink lg:min-h-8 ${
            value === o.value ? 'bg-ink text-bg' : 'text-ink2 hover:text-ink'
          }`}
        >
          <input type="radio" className="sr-only" name={name} checked={value === o.value} onChange={() => onChange(o.value)} />
          {o.label}
        </label>
      ))}
    </div>
  )
}

/** The order a person reads the rows in: facts, pending items and promises, signals and each signal under them, patterns, then all together. */
const ROW_ORDER = ['fact', 'open_item', 'signal', ...SIGNALS.map((s) => `signal:${s}`), 'trait', 'all']
const rowRank = (target: string) => {
  const known = ROW_ORDER.indexOf(target)
  if (known >= 0) return known
  // A signal or a kind the Console does not know yet: after its own group, before the total.
  return target.startsWith('signal:') ? ROW_ORDER.indexOf('trait') - 0.5 : ROW_ORDER.indexOf('all') - 0.5
}

const CASE_NOW: Record<string, string> = {
  marked: 'marcado hoje',
  not_marked: 'não marcado hoje',
  active: 'ainda vale',
  gone: 'saiu da memória',
  not_measured: 'sem medição',
  unknown: 'sem comparação',
}

function CasesTable({ report }: { report: ReviewCases }) {
  if (report.tallies.length === 0) return <Empty title="Nenhum caso no período.">Os casos nascem dos vereditos gravados acima.</Empty>
  const rows = [...report.tallies].sort((a, b) => rowRank(a.target) - rowRank(b.target))
  return (
    <div className="grid gap-4">
      <div className="overflow-x-auto">
        <table className="w-full min-w-[32rem] border-collapse">
          <caption className="sr-only">Casos de teste desde {fmtDateTime(report.since)}</caption>
          <thead>
            <tr>
              <th scope="col" className={th}>O que foi julgado</th>
              <th scope="col" className={`${th} text-right`}>Casos</th>
              <th scope="col" className={`${th} text-right`}>Passam</th>
              <th scope="col" className={`${th} text-right`}>Falham</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((t) => (
              <tr key={t.target}>
                <th scope="row" className={`${td} text-left font-normal`}>{agreementTarget(t.target)}</th>
                <td className={`${td} text-right tabular-nums`}>{fmtInt(t.cases)}</td>
                <td className={`${td} text-right tabular-nums`}>{fmtInt(t.passing)}</td>
                <td className={`${td} text-right tabular-nums ${t.failing > 0 ? 'font-semibold text-ink' : ''}`}>{fmtInt(t.failing)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {report.failing.length > 0 && (
        <ul className="grid gap-2" aria-label="Casos que falham hoje">
          {report.failing.map((c) => (
            <li key={`${c.sample_id}:${c.target}:${c.target_id}`} className={`${card} px-4 py-3`}>
              <p className="text-ink">
                {agreementTarget(c.target === 'signal' ? `signal:${c.target_id.split(':').pop()}` : c.target)} · veredito <strong>{c.verdict}</strong>, {CASE_NOW[c.now] ?? c.now}
              </p>
              <p className={`${small} mt-0.5`}>
                amostra <span className={mono}>{c.sample_id}</span>
              </p>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

function AgreementTable({ report }: { report: AgreementReport }) {
  if (report.targets.length === 0) return <Empty title="Nenhum veredito no período." />
  const rows = [...report.targets].sort((a, b) => rowRank(a.target) - rowRank(b.target))
  const rate = (r: { value: number; low: number; high: number; n: number } | null) =>
    r ? (
      <>
        <span className="font-semibold text-ink">{fmtPct(r.value)}</span>
        <span className="block text-[0.75rem] text-ink2">
          {fmtPct(r.low)} a {fmtPct(r.high)} · n {fmtInt(r.n)}
        </span>
      </>
    ) : (
      <span className="text-ink2">não medido</span>
    )
  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[40rem] border-collapse">
        <caption className="sr-only">Concordância desde {fmtDateTime(report.since)}</caption>
        <thead>
          <tr>
            <th scope="col" className={th}>O que foi julgado</th>
            <th scope="col" className={`${th} text-right`}>Julgados</th>
            <th scope="col" className={`${th} text-right`}>Revisor concorda com o sistema</th>
            <th scope="col" className={`${th} text-right`}>Pares de revisores</th>
            <th scope="col" className={`${th} text-right`}>Revisores concordam entre si</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((t) => (
            <tr key={t.target}>
              <th scope="row" className={`${td} text-left font-normal`}>
                {agreementTarget(t.target)}
              </th>
              <td className={`${td} text-right tabular-nums`}>{fmtInt(t.judged)}</td>
              <td className={`${td} text-right tabular-nums`}>{rate(t.system_agreement)}</td>
              <td className={`${td} text-right tabular-nums`}>{fmtInt(t.reviewer_pairs)}</td>
              <td className={`${td} text-right tabular-nums`}>{rate(t.reviewer_agreement)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
