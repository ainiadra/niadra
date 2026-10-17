import { useState, type ReactNode } from 'react'
import { ApiError } from '../api/http'
import {
  putBiFile,
  sha256Hex,
  type AgentRates,
  type ArmComparison,
  type AttributionReport,
  type Band,
  type CounterfactualRun,
  type ExperimentResult,
  type Finality,
  type LimitCode,
  type Method,
  type OutcomeLink,
  type ReconcileResult,
} from '../api/measure'
import { fmtDate, fmtDateTime, fmtInt, fmtPct, utcDayEndsAt, utcDayInput } from '../lib/format'
import { usePaged, useResource, type Resource } from '../lib/useResource'
import { SCREENS } from '../lib/screens'
import { useSpace } from '../session'
import { Chip, PageHeader, SourceName } from '../ui/bits'
import { Section, useAction } from '../ui/forms'
import { useTitle } from '../ui/layout'
import { Empty, ErrorState, LoadMore, Loading } from '../ui/States'
import { btnSecondary, card, input, label, mono, small, td, th } from '../ui/styles'
import { Segmented } from './Usage'

/**
 * What the agents' work led to, measured without a person on screen: outcomes attributed
 * by method, each line with its method and the gap against the company's BI file; experiments arm by arm;
 * interleaved rankings; what an element changes in a tool's answer; cost per turn against memory off; and the
 * rates of claims with evidence and of constraints broken. Every number is per agent, never per customer.
 */

const PERIODS = [
  { value: 7, label: '7 dias' },
  { value: 30, label: '30 dias' },
  { value: 90, label: '90 dias' },
]

const METHOD: Record<Method, string> = {
  line: 'Por linha',
  order: 'Por pedido',
  identity: 'Por identidade',
  assisted_handoff: 'Transbordo assistido',
}
const METHOD_MEANS: Record<Method, string> = {
  line: 'a linha do pedido trouxe o código do cartão que o agente mostrou',
  order: 'o pedido trouxe o código, ou o agente registrou a última ação nele',
  identity: 'a pessoa abriu ou viu o mesmo item dentro da janela',
  assisted_handoff: 'o pedido cita o transbordo que ajudou na venda',
}
const BAND: Record<Band, string> = { deterministic: 'Determinístico', probable: 'Provável' }
const FINALITY: Record<Finality, string> = {
  provisional: 'Provisório',
  final: 'Final',
  expired_without_outcome: 'Expirou sem desfecho',
}
const LIMITS: Record<LimitCode, string> = {
  not_quality: 'Diz se o elemento muda a lista, não se a lista ficou melhor.',
  model_reaction_not_measured: 'Não mede como o modelo reage a cada lista.',
  trivial_for_hard: 'Uma restrição dura muda a lista por construção: a mudança já era esperada.',
  few_cases: 'Menos de 30 casos completos: o número ainda é frágil.',
  noisy_tool: 'A ferramenta devolve listas diferentes para a mesma chamada; mudanças desse tamanho não são do elemento.',
  cases_skipped: 'Parte dos casos não completou: o relatório cobre só os completos.',
  dry_run: 'Parte dos casos rodou em modo de simulação, que pode devolver o que a chamada real não devolveria.',
}
const ELEMENT: Record<CounterfactualRun['element'], string> = {
  constraints: 'Bloco de restrições inteiro',
  hard: 'Restrições duras',
  size: 'Tamanho',
  exclude: 'Exclusões',
}

const money = (minor: number, currency: string) => {
  try {
    return new Intl.NumberFormat('pt-BR', { style: 'currency', currency: currency || 'BRL' }).format(minor / 100)
  } catch {
    return `${fmtInt(minor)} ${currency}`
  }
}
const num = (n: number | null | undefined, digits = 3) =>
  n === null || n === undefined ? 'sem dado' : new Intl.NumberFormat('pt-BR', { maximumFractionDigits: digits }).format(n)
const usd = (n: number | null | undefined) =>
  n === null || n === undefined ? 'sem dado' : new Intl.NumberFormat('pt-BR', { style: 'currency', currency: 'USD', maximumFractionDigits: 4 }).format(n)

/** A feature the space left off answers 404: the section says so instead of an error. */
function Off({ res, title, children }: { res: { error: unknown; reload: () => void }; title: string; children: ReactNode }) {
  if (res.error instanceof ApiError && res.error.status === 404) return <Empty title={title}>{children}</Empty>
  return <ErrorState error={res.error} onRetry={res.reload} />
}

function Loaded<T>({ res, loading, off, children }: { res: Resource<T>; loading: string; off: string; children: (data: T) => ReactNode }) {
  if (res.status === 'loading' || res.status === 'idle') return <Loading label={loading} />
  if (res.status === 'error' || res.data === undefined)
    return (
      <Off res={res} title={off}>
        A medição se liga em Configuração, no documento de recursos.
      </Off>
    )
  return <>{children(res.data)}</>
}

export function Measure() {
  const { space, cell, hasRole } = useSpace()
  useTitle('Desfechos e experimentos', space.environment === 'sandbox')
  const [days, setDays] = useState(30)
  // UTC days, as the server counts them, so the current day is never cut at night in Brazil.
  const since = utcDayInput(days - 1)
  const until = utcDayInput(0)
  const analyst = hasRole('analysis')
  const m = cell.measure

  const attribution = useResource((signal) => m.attribution({ since, until }, { signal }), [since, until, m], analyst)
  const outcomes = usePaged((cursor, signal) => m.outcomes(cursor, { signal }), [m], analyst)
  const experiments = useResource((signal) => m.experiments({ signal }), [m], analyst)
  const interleaving = useResource((signal) => m.interleaving({ since, until }, { signal }), [since, until, m], analyst)
  const runs = usePaged((cursor, signal) => m.counterfactualRuns(cursor, { signal }), [m], analyst)
  const rates = useResource((signal) => m.agentRates({ since, until }, { signal }), [since, until, m], analyst)

  if (!analyst)
    return (
      <>
        <PageHeader title="Desfechos e experimentos" />
        <Empty title="Esta tela é do papel Análise.">Peça o papel a quem administra o espaço, em Pessoas e papéis.</Empty>
      </>
    )

  return (
    <>
      <PageHeader
        screen="measure"
        description={`De ${fmtDate(since)} a ${fmtDate(until)}, em dias UTC, como o servidor conta: o dia de hoje termina às ${utcDayEndsAt()} no seu horário. ${SCREENS.measure.lead}`}
        actions={<Segmented label="Período" value={days} options={PERIODS} onChange={setDays} />}
      />
      <div className="grid min-w-0 gap-6 [&>*]:min-w-0">
        <Section
          tour="measure.attribution"
          title="Desfechos atribuídos"
          description="Cada método fica numa faixa. O determinístico e o provável nunca se somam, e nenhum valor é multiplicado por uma confiança."
        >
          <Loaded res={attribution} loading="Somando os desfechos do período…" off="A medição de desfechos está desligada neste espaço.">
            {(r) => <AttributionTotals report={r} />}
          </Loaded>
          <h3 className="mb-2 mt-6 text-[0.875rem] font-semibold text-ink">Linhas mais recentes, com o método de cada uma</h3>
          {outcomes.status === 'loading' || outcomes.status === 'idle' ? (
            <Loading label="Carregando as linhas atribuídas…" />
          ) : outcomes.status === 'error' ? (
            <Off res={outcomes} title="A medição de desfechos está desligada neste espaço.">
              A medição se liga em Configuração, no documento de recursos.
            </Off>
          ) : outcomes.items.length === 0 ? (
            <Empty title="Nenhum desfecho atribuído ainda.">Um desfecho aparece quando um pedido medido chega a um estado de sucesso.</Empty>
          ) : (
            <>
              <OutcomeLines links={outcomes.items} />
              <LoadMore hasMore={outcomes.hasMore} loading={outcomes.loadingMore} error={outcomes.moreError} onClick={outcomes.loadMore} />
            </>
          )}
          <Reconcile since={since} until={until} />
        </Section>

        <Section
          tour="measure.experiments"
          title="Experimentos"
          description="Cada braço contra o grupo de controle, pelo que as pessoas fizeram depois de sorteadas. O ajuste usa o histórico de cada pessoa antes do sorteio, e o teste pode ser lido todo dia sem inflar o falso positivo."
        >
          <Loaded res={experiments} loading="Comparando os braços…" off="Os experimentos estão desligados neste espaço.">
            {(r) =>
              r.experiments.length === 0 ? (
                <Empty title="Nenhum experimento de elemento ou de agente declarado.">Um experimento se declara no documento de medição, em Configuração.</Empty>
              ) : (
                <div className="grid min-w-0 gap-5">
                  {r.experiments.map((e) => (
                    <Experiment key={e.experiment} e={e} />
                  ))}
                </div>
              )
            }
          </Loaded>
        </Section>

        <Section title="Intercalação" description="Duas ordenações da mesma ferramenta, misturadas na mesma lista. Ganha a que contribuiu os itens que a pessoa engajou.">
          <Loaded res={interleaving} loading="Somando as impressões…" off="A intercalação está desligada neste espaço.">
            {(r) =>
              r.totals.length === 0 ? (
                <Empty
                  title="Nenhuma lista intercalada no período."
                  example={
                    <>
                      <span className={mono}>restricoes_na_busca</span> · <span className={mono}>buscar_produtos</span> · 1.240 impressões · 61 vitórias da atual, 74 da candidata · 12 empates · p = 0,27
                    </>
                  }
                >
                  A intercalação precisa de um experimento do tipo <span className={mono}>interleave</span> no documento de medição, em Configuração, com a ferramenta cujas duas ordenações serão comparadas. A partir daí, o SDK mistura as duas listas em cada turno e a Niadra conta qual contribuiu os itens que a pessoa engajou. Nada aparece até o primeiro turno com a lista misturada.
                </Empty>
              ) : (
                <Table
                  caption="Intercalação por experimento e ferramenta"
                  head={['Experimento', 'Ferramenta', 'Impressões', 'Vitórias da atual (a)', 'Vitórias da candidata (b)', 'Empates', 'Teste do sinal']}
                  rows={r.totals.map((t) => [
                    <span className={mono}>{t.experiment}</span>,
                    <span className={mono}>{t.tool}</span>,
                    fmtInt(t.impressions),
                    fmtInt(t.wins_a),
                    fmtInt(t.wins_b),
                    fmtInt(t.ties),
                    t.p_value === null ? 'sem impressões decididas' : `p = ${num(t.p_value, 4)}`,
                  ])}
                />
              )
            }
          </Loaded>
        </Section>

        <Section
          title="Contrafactual das ferramentas"
          description="Rodado no CI da empresa: cada chamada gravada volta a rodar com e sem o elemento, no mesmo instante, e a versão com o elemento roda duas vezes para medir o ruído da própria ferramenta."
        >
          {runs.status === 'loading' || runs.status === 'idle' ? (
            <Loading label="Carregando as rodadas…" />
          ) : runs.status === 'error' ? (
            <Off res={runs} title="A medição está desligada neste espaço.">
              A medição se liga em Configuração, no documento de recursos.
            </Off>
          ) : runs.items.length === 0 ? (
            <Empty
              title="Nenhuma rodada enviada ainda."
              example={
                <>
                  <span className={mono}>buscar_produtos</span> · Restrições duras · 48 casos, 46 completos · a lista mudou em 61% dos casos · ruído da ferramenta 4% · p = 0,01
                </>
              }
            >
              A rodada roda no CI da empresa, não aqui: o executor do SDK volta a chamar a ferramenta com e sem o elemento, com uma chave de API de escopo replay, e envia o relatório. O comando é{' '}
              <span className={mono}>niadra counterfactual --tools empresa.tools:TOOLS --tool buscar_produtos --element hard --scenario sc_1</span>. A primeira rodada enviada aparece aqui.
            </Empty>
          ) : (
            <div className="grid min-w-0 gap-4">
              {runs.items.map((r) => (
                <Counterfactual key={r.run_id} run={r} />
              ))}
              <LoadMore hasMore={runs.hasMore} loading={runs.loadingMore} error={runs.moreError} onClick={runs.loadMore} />
            </div>
          )}
        </Section>

        <Section tour="measure.cost" title="Custo por turno contra a memória desligada" description="O que cada agente gastou por turno, pelos registros de turno, ao lado do custo que a empresa mediu com a memória desligada.">
          <Loaded res={rates} loading="Somando os turnos…" off="A medição está desligada neste espaço.">
            {(r) => <Cost rates={r} />}
          </Loaded>
        </Section>

        <Section title="Afirmações e restrições" description="Quantas afirmações dos agentes tinham evidência no turno, e quantas vezes uma ferramenta devolveu o que a pessoa recusou.">
          <Loaded res={rates} loading="Somando os vereditos…" off="A medição está desligada neste espaço.">
            {(r) => <Rates rates={r} />}
          </Loaded>
        </Section>
      </div>
    </>
  )
}

function Table({ caption, head, rows }: { caption: string; head: string[]; rows: ReactNode[][] }) {
  return (
    <div className={`${card} overflow-x-auto`}>
      <table className="w-full min-w-[44rem] border-collapse">
        <caption className="sr-only">{caption}</caption>
        <thead>
          <tr>
            {head.map((h) => (
              <th key={h} scope="col" className={th}>
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((cells, i) => (
            <tr key={i}>
              {cells.map((c, j) => (
                <td key={j} className={`${td} ${j > 1 ? 'tabular-nums' : ''}`}>
                  {c}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function AttributionTotals({ report }: { report: AttributionReport }) {
  const { identity } = report
  return (
    <div className="grid min-w-0 gap-3">
      {report.totals.length === 0 ? (
        <Empty title="Nenhum desfecho no período." />
      ) : (
        <Table
          caption="Desfechos por método, faixa e situação"
          head={['Método', 'Faixa', 'Situação', 'Desfechos', 'Linhas', 'Valor']}
          rows={report.totals.map((t) => [
            METHOD[t.method],
            <Chip tone={t.band === 'deterministic' ? 'ok' : 'neutral'}>{BAND[t.band]}</Chip>,
            FINALITY[t.finality],
            fmtInt(t.outcomes),
            fmtInt(t.lines),
            t.currency ? money(t.value_minor, t.currency) : 'sem valor',
          ])}
        />
      )}
      <p className={small}>
        Calibração do método por identidade:{' '}
        {identity.compared === 0
          ? 'ainda sem linhas atribuídas pelos dois métodos.'
          : `acerta ${fmtPct(identity.rate ?? 0)} das ${fmtInt(identity.compared)} linhas em que o código da linha também diz a exposição.`}
      </p>
    </div>
  )
}

function OutcomeLines({ links }: { links: OutcomeLink[] }) {
  return (
    <ul className={`${card} divide-y divide-line2`} aria-label="Linhas atribuídas, com o método de cada uma">
      {links.map((l) => (
        <li key={l.link_id} className="px-4 py-3">
          <div className="flex flex-wrap items-baseline justify-between gap-2">
            <p className="min-w-0 text-ink">
              <span className={`${mono} break-all`}>{l.object}</span>
              {l.line && <span className="text-ink2"> · linha {l.line}</span>}
            </p>
            <p className="tabular-nums text-ink">{l.value_minor != null && l.currency ? money(l.value_minor, l.currency) : 'sem valor'}</p>
          </div>
          <div className="mt-1 flex flex-wrap items-center gap-1.5">
            <Chip tone={l.band === 'deterministic' ? 'ok' : 'neutral'}>{METHOD[l.method]}</Chip>
            <Chip>{FINALITY[l.finality]}</Chip>
            {l.confidence != null && <Chip>confiança {fmtPct(l.confidence)}</Chip>}
          </div>
          <p className={`${small} mt-1`}>
            {METHOD_MEANS[l.method]}
            {l.agent && <> · agente <span className={mono}>{l.agent}</span></>}
            {l.position != null && <> · posição {l.position} da lista</>} · contou em {fmtDateTime(l.valid_at)}
          </p>
        </li>
      ))}
    </ul>
  )
}

function Reconcile({ since, until }: { since: string; until: string }) {
  const { cell } = useSpace()
  const [file, setFile] = useState<File | null>(null)
  const action = useAction<ReconcileResult>()
  const run = () =>
    file &&
    action.run(async () => {
      const data = await file.arrayBuffer()
      const upload = await cell.measure.biUpload({ size_bytes: data.byteLength, sha256: await sha256Hex(data) })
      await putBiFile(upload, data)
      return cell.measure.reconcile({ since, until, upload_id: upload.upload_id })
    })
  const r = action.result
  return (
    <div className="mt-6 border-t border-line pt-5">
      <h3 className="text-[0.875rem] font-semibold text-ink" data-tour="measure.bi">Diferença contra o arquivo do BI</h3>
      <p className={`${small} mt-1 max-w-[72ch]`}>
        O CSV do BI da empresa, com as colunas order, line, value e currency, comparado linha a linha com o valor atribuído no período. O arquivo vai direto para o
        armazenamento, é lido uma vez e apagado.
      </p>
      <div className="mt-3 flex flex-wrap items-end gap-3">
        <div>
          <label htmlFor="bi-file" className={label}>
            Arquivo do BI
          </label>
          <input id="bi-file" type="file" accept=".csv,text/csv" className={`${input} pt-2`} onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
        </div>
        <button type="button" className={btnSecondary} disabled={!file || action.busy} onClick={run}>
          {action.busy ? 'Comparando…' : 'Comparar com o período'}
        </button>
      </div>
      {Boolean(action.error) && (
        <div className="mt-3">
          <ErrorState error={action.error} compact />
        </div>
      )}
      {r && (
        <div role="status" className={`${card} mt-3 px-4 py-3`}>
          <p className="text-ink">
            <strong>{r.deviation === null ? 'Nenhuma linha em comum.' : `Diferença de ${fmtPct(r.deviation)}`}</strong>{' '}
            {r.deviation !== null && (r.within_tolerance ? <Chip tone="ok">dentro da tolerância de {fmtPct(r.tolerance)}</Chip> : <Chip tone="danger">acima da tolerância de {fmtPct(r.tolerance)}</Chip>)}
          </p>
          <p className={`${small} mt-1`}>
            {fmtInt(r.matched)} linhas nos dois lados · {fmtInt(r.only_ours)} só na atribuição · {fmtInt(r.only_bi)} só no BI
          </p>
          {r.worst.length > 0 && (
            <ul className={`${small} mt-2`} aria-label="Linhas que mais diferem">
              {r.worst.map((w) => (
                <li key={`${w.order}:${w.line}`}>
                  <span className={mono}>
                    {w.order} · {w.line || 'pedido inteiro'}
                  </span>{' '}
                  · atribuído {money(w.ours_minor, r.currency ?? 'BRL')} · BI {money(w.bi_minor, r.currency ?? 'BRL')}
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </div>
  )
}

const METRIC: Record<ArmComparison['metric'], string> = { conversion: 'Conversão', value: 'Valor por pessoa' }

function Experiment({ e }: { e: ExperimentResult }) {
  const what = e.kind === 'agent' ? `agente ${e.agent ?? ''}` : `elemento ${e.element ?? ''}`
  return (
    <article className={`${card} min-w-0 p-4`} aria-label={`Experimento ${e.experiment}`}>
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h3 className={`${mono} text-[0.875rem] font-semibold text-ink`}>{e.experiment}</h3>
        <p className={small}>
          {what} · desde {fmtDate(e.starts_at)} · alfa {fmtPct(e.alpha)}
          {e.cuped_days > 0 ? ` · ajuste pelos ${e.cuped_days} dias anteriores` : ''}
        </p>
      </div>
      <ul className="mt-2 flex flex-wrap gap-2" aria-label="Braços">
        {e.arms.map((a) => (
          <li key={a.arm} className="rounded-lg border border-line px-3 py-1.5 text-[0.8125rem]">
            <span className="font-semibold text-ink">{a.arm === 'control' ? 'Controle' : a.arm}</span>{' '}
            <span className="tabular-nums text-ink2">
              {fmtInt(a.people)} pessoas · {fmtInt(a.converted)} converteram
            </span>
          </li>
        ))}
      </ul>
      {e.comparisons.length === 0 ? (
        <p className={`${small} mt-3`}>Cada braço precisa de pelo menos 50 pessoas antes da comparação.</p>
      ) : (
        <div className="mt-3">
          <Table
            caption={`Comparações do experimento ${e.experiment}`}
            head={['Braço', 'Medida', 'Controle', 'No braço', 'Diferença', 'Intervalo', 'p', 'Decisão']}
            rows={e.comparisons.map((c) => [
              c.arm,
              METRIC[c.metric],
              c.metric === 'conversion' ? fmtPct(c.control_mean) : num(c.control_mean / 100, 2),
              c.metric === 'conversion' ? fmtPct(c.arm_mean) : num(c.arm_mean / 100, 2),
              <>
                {c.metric === 'conversion' ? `${num(c.difference * 100, 2)} p.p.` : num(c.difference / 100, 2)}
                {c.relative !== null && <span className="text-ink2"> ({fmtPct(c.relative)})</span>}
              </>,
              c.metric === 'conversion' ? `${num(c.ci_low * 100, 2)} a ${num(c.ci_high * 100, 2)} p.p.` : `${num(c.ci_low / 100, 2)} a ${num(c.ci_high / 100, 2)}`,
              num(c.p_value, 4),
              c.decided ? <Chip tone="ok">Diferença real</Chip> : <Chip>Ainda sem decisão</Chip>,
            ])}
          />
        </div>
      )}
    </article>
  )
}

function Counterfactual({ run }: { run: CounterfactualRun }) {
  const e = run.engaged
  return (
    <article className={`${card} min-w-0 p-4`} aria-label={`Rodada ${run.label ?? run.run_id}`}>
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h3 className="text-[0.875rem] font-semibold text-ink">
          <span className={mono}>{run.tool}</span> · {ELEMENT[run.element]}
        </h3>
        <p className={small}>
          {run.label && <span className={mono}>{run.label} · </span>}
          {fmtDateTime(run.created_at)}
        </p>
      </div>
      <dl className="mt-3 grid grid-cols-2 gap-x-6 gap-y-2 sm:grid-cols-4">
        <Stat k="Sobreposição com o elemento" v={num(run.overlap)} />
        <Stat k="Piso de ruído da ferramenta" v={num(run.noise_floor)} />
        <Stat k="Efeito além do ruído" v={num(run.effect)} />
        <Stat k="Casos completos" v={`${fmtInt(run.completed)} de ${fmtInt(run.cases)}`} />
      </dl>
      <p className={`${small} mt-2`}>
        {fmtInt(run.below_noise)} casos abaixo do próprio ruído, {fmtInt(run.above_noise)} acima, {fmtInt(run.ties)} empates
        {run.p_value !== null && ` · teste do sinal p = ${num(run.p_value, 4)}`}. Dos {fmtInt(e.items)} itens que a pessoa abriu, {fmtInt(e.lost)} saíram das primeiras posições sem o
        elemento e {fmtInt(e.gained)} entraram.
      </p>
      <ul className={`${small} mt-2 list-disc pl-5`} aria-label="O que o método não diz">
        {run.limits.map((l) => (
          <li key={l}>{LIMITS[l]}</li>
        ))}
      </ul>
    </article>
  )
}

function Stat({ k, v }: { k: string; v: string }) {
  return (
    <div className="min-w-0">
      <dt className="text-[0.75rem] font-semibold text-ink2">{k}</dt>
      <dd className="mt-0.5 tabular-nums text-ink">{v}</dd>
    </div>
  )
}

function Cost({ rates }: { rates: AgentRates }) {
  const rows = rates.cost ?? []
  if (!rates.cost) return <Empty title="O registro de turnos está desligado neste espaço.">O custo por turno vem dos registros de turno.</Empty>
  if (rows.length === 0) return <Empty title="Nenhum turno registrado no período." />
  return (
    <Table
      caption="Custo por turno de cada agente"
      head={['Fonte', 'Agente', 'Turnos', 'Custo por turno', 'Memória desligada', 'Diferença', 'Chamadas ao modelo', 'Tokens de entrada']}
      rows={rows.map((c) => [
        <SourceName id={c.source_id} />,
        <span className={mono}>{c.agent}</span>,
        fmtInt(c.turns),
        usd(c.cost_per_turn_usd),
        c.memory_off_cost_per_turn_usd == null ? 'não medido' : usd(c.memory_off_cost_per_turn_usd),
        c.difference_usd == null ? 'sem base' : <Chip tone={c.difference_usd > 0 ? 'danger' : 'ok'}>{c.difference_usd > 0 ? `+${usd(c.difference_usd)}` : usd(c.difference_usd)}</Chip>,
        num(c.model_calls_per_turn, 2),
        num(c.tokens_in_per_turn, 0),
      ])}
    />
  )
}

function Rates({ rates }: { rates: AgentRates }) {
  const claims = rates.claims ?? []
  const constraints = rates.constraints ?? []
  if (!rates.claims && !rates.constraints)
    return <Empty title="O contrato de afirmação e os sinais estão desligados neste espaço." />
  return (
    <div className="grid min-w-0 gap-4">
      {rates.claims &&
        (claims.length === 0 ? (
          <Empty title="Nenhuma afirmação registrada no período." />
        ) : (
          <Table
            caption="Afirmações com evidência por agente"
            head={['Fonte', 'Agente', 'Turnos', 'Afirmações', 'Com evidência', 'Taxa']}
            rows={claims.map((c) => [
              <SourceName id={c.source_id} />,
              <span className={mono}>{c.agent}</span>,
              fmtInt(c.turns),
              fmtInt(c.claims),
              fmtInt(c.evidenced),
              c.claims ? fmtPct(c.evidenced / c.claims) : 'sem afirmações',
            ])}
          />
        ))}
      {rates.constraints &&
        (constraints.length === 0 ? (
          <Empty title="Nenhuma restrição enviada às ferramentas no período." />
        ) : (
          <Table
            caption="Restrições enviadas, verificáveis e violadas por agente"
            head={['Fonte', 'Agente', 'Enviadas', 'Resultados lidos', 'Verificáveis', 'Violados', 'Por 1.000 itens']}
            rows={constraints.map((c) => [
              <SourceName id={c.source_id} />,
              <span className={mono}>{c.agent}</span>,
              fmtInt(c.sent),
              fmtInt(c.checked),
              fmtInt(c.verifiable),
              fmtInt(c.violated),
              c.violations_per_1000_items == null ? 'sem itens mostrados' : num(c.violations_per_1000_items, 1),
            ])}
          />
        ))}
    </div>
  )
}
