import type { ReactNode } from 'react'
import { Link, useParams } from 'react-router-dom'
import type { ReceiptLineage } from '../api/types'
import { fmtDate, fmtDateTime, fmtInt, shortHash } from '../lib/format'
import { audience, channel, eventKind, itemKind, purpose, receiptKind, surface, verification, withheldMeans, withheldReason } from '../lib/labels'
import { useResource } from '../lib/useResource'
import { paths } from '../paths'
import { useSpace } from '../session'
import { Chip, PageHeader, Props, SourceName } from '../ui/bits'
import { Icon } from '../ui/Icon'
import { ActionCredentials, ActionLine, ReceiptActor } from '../ui/ReceiptWords'
import { useTitle } from '../ui/layout'
import { Empty, ErrorState, Loading } from '../ui/States'
import { card, h2, link, mono, small } from '../ui/styles'

/**
 * "Why did the agent say X?": receipt -> manifest -> items -> origin events.
 * A conversation only appears here as evidence.
 */
export function ReceiptDetail() {
  const { receiptId = '' } = useParams()
  const { space, cell } = useSpace()
  useTitle('Comprovante', space.environment === 'sandbox')
  const lineage = useResource((signal) => cell.lineage(receiptId, { signal }), [receiptId, cell])
  const back = { to: paths.receipts(space.space_id), label: 'Comprovantes' }

  if (lineage.status === 'loading' || lineage.status === 'idle') return <Loading label="Montando a linhagem do comprovante…" />
  if (lineage.status === 'error' || !lineage.data)
    return (
      <>
        <PageHeader screen="receipt" back={back} />
        <ErrorState error={lineage.error} onRetry={lineage.reload} />
      </>
    )
  return <LineageView data={lineage.data} back={back} spaceId={space.space_id} />
}

const str = (v: unknown) => (typeof v === 'string' ? v : v === undefined || v === null ? null : String(v))

function LineageView({ data, back, spaceId }: { data: ReceiptLineage; back: { to: string; label: string }; spaceId: string }) {
  const r = data.receipt
  if (r.kind === 'admin') return <ActionView data={data} back={back} spaceId={spaceId} />
  const events = data.items.reduce((n, i) => n + i.events.length, 0)
  const steps: [string, string][] = [
    ['comprovante', 'Comprovante'],
    ['manifesto', data.manifest_found ? 'Manifesto' : 'Sem manifesto'],
    ['itens', `${fmtInt(data.items.length)} ${data.items.length === 1 ? 'item entregue' : 'itens entregues'}`],
    ['origem', `${fmtInt(events)} ${events === 1 ? 'evento de origem' : 'eventos de origem'}`],
  ]

  return (
    <>
      <PageHeader screen="receipt" back={back} title={`${receiptKind(r.kind)} de ${fmtDateTime(r.read_at)}`} description={<span className={mono}>{r.receipt_id}</span>} />

      <nav aria-label="Linhagem" className="mb-6">
        <ol className="flex flex-wrap items-center gap-x-1 gap-y-2 text-[0.875rem]">
          {steps.map(([id, text], i) => (
            <li key={id} className="flex items-center gap-1">
              {i > 0 && <Icon name="chevron" size={16} className="text-ink3" />}
              <a href={`#${id}`} className="inline-flex min-h-11 items-center rounded-md border border-line bg-surface px-2.5 font-semibold text-ink hover:bg-bg lg:min-h-8">
                {text}
              </a>
            </li>
          ))}
        </ol>
      </nav>

      <div className="grid gap-6">
        <section id="comprovante" aria-labelledby="h-receipt" className={`${card} scroll-mt-20 p-4 sm:p-5`}>
          <h2 id="h-receipt" className={`${h2} mb-4`}>
            Comprovante
          </h2>
          <Props
            items={[
              ['Quem leu', <ReceiptActor receipt={r} />],
              ['Audiência', audience(r.audience)],
              ['Finalidade', purpose(r.purpose)],
              ['O que foi lido', surface(r.surface)],
              ['Nível de verificação', verification(r.verification)],
              ['Versão da política', r.policy_version ?? 'não informada'],
              [
                'Perfil',
                r.profile_id ? (
                  <Link className={`${link} font-mono`} to={paths.profile(spaceId, r.profile_id)}>
                    {r.pseudonym ?? r.profile_id}
                  </Link>
                ) : (
                  'sem cliente'
                ),
              ],
              ['Conversa ou tarefa', <span className={mono}>{r.conversation_id ?? r.task_id ?? 'nenhuma'}</span>],
              ['Grupo do experimento', r.experiment_group ?? 'nenhum'],
              ['Itens entregues e retidos', `${fmtInt(r.item_ids.length)} entregues · ${fmtInt(r.withheld)} retidos`],
            ]}
          />
        </section>

        <section id="manifesto" aria-labelledby="h-manifest" className={`${card} scroll-mt-20 p-4 sm:p-5`}>
          <h2 id="h-manifest" className={h2}>
            Manifesto
          </h2>
          {data.manifest_found ? (
            <Props
              items={[
                ['Hash do manifesto', <span className={`${mono} break-all`}>{r.manifest_hash ?? 'não informado'}</span>],
                ['Hash do contexto entregue', <span className={`${mono} break-all`}>{r.pack_hash ?? 'não informado'}</span>],
              ]}
            />
          ) : (
            <p className={`${small} mt-2`}>
              {r.kind === 'denied'
                ? 'A política negou esta leitura e nada foi entregue. O comprovante guarda quem tentou, em qual superfície e quando, nunca o que foi pedido.'
                : 'O manifesto desta leitura não foi encontrado. Os itens abaixo vêm do próprio comprovante.'}
            </p>
          )}
        </section>

        <section id="itens" aria-labelledby="h-items" data-tour="receipt.items" className="scroll-mt-20">
          <h2 id="h-items" className={`${h2} mb-3`}>
            Itens entregues e a origem de cada um
          </h2>
          {data.items.length === 0 ? (
            <Empty title="Nenhum item entregue nesta leitura." />
          ) : (
            <ol className={`${card} divide-y divide-line2`} id="origem">
              {data.items.map((it) => (
                <li key={it.id} className="px-4 py-3">
                  <div className="flex flex-wrap items-baseline gap-2">
                    <span className="font-semibold text-ink">{itemKind(it.kind)}</span>
                    <span className={`${mono} text-ink2`}>{it.id}</span>
                  </div>
                  {it.events.length > 0 ? (
                    <ul className="mt-2 grid gap-1 border-l border-line pl-3" aria-label="Eventos de origem">
                      {it.events.map((ev) => (
                        <li key={ev.event_id}>
                          <p className={`${mono} text-ink2`}>
                            <SourceName id={ev.source_id} /> · {channel(ev.channel)} · {eventKind(ev.kind)} · {fmtDateTime(ev.occurred_at)}
                          </p>
                          <span className={`${mono} text-ink3`}>
                            {ev.event_id}
                            {ev.conversation_id ? ` · conversa ${ev.conversation_id}` : ''}
                          </span>
                        </li>
                      ))}
                    </ul>
                  ) : (
                    <p className={`${small} mt-1`}>
                      {it.evidence_event_ids.length > 0 ? `${it.evidence_event_ids.length} evento(s) de origem fora do alcance do seu papel.` : 'Sem evento de origem registrado.'}
                    </p>
                  )}
                </li>
              ))}
            </ol>
          )}
        </section>

        <section aria-labelledby="h-withheld" data-tour="receipt.withheld">
          <h2 id="h-withheld" className={`${h2} mb-3`}>
            O que ficou retido e por quê
          </h2>
          {data.excluded.length === 0 ? (
            <Empty title={r.withheld > 0 ? `${fmtInt(r.withheld)} item(ns) retido(s), sem detalhe no manifesto.` : 'Nada foi retido nesta leitura.'} />
          ) : (
            <ul className={`${card} divide-y divide-line2`}>
              {data.excluded.map((ex, i) => {
                const reason = str(ex.reason) ?? 'policy'
                return (
                  <li key={str(ex.item_id) ?? str(ex.id) ?? i} className="px-4 py-3">
                    <div className="flex flex-wrap items-baseline gap-2">
                      <Chip tone={reason === 'quarantine' ? 'danger' : 'neutral'}>{withheldReason(reason)}</Chip>
                      <span className="text-ink">{itemKind(str(ex.kind) ?? str(ex.item_kind))}</span>
                      <span className={`${mono} text-ink2`}>{str(ex.item_id) ?? str(ex.id)}</span>
                    </div>
                    <p className={`${small} mt-1`}>
                      {str(ex.detail) ?? withheldMeans(reason)}
                      {str(ex.rule) ? ` Regra ${str(ex.rule)}.` : ''}
                    </p>
                  </li>
                )
              })}
            </ul>
          )}
        </section>

        <ChainSection receipt={r} />
      </div>
    </>
  )
}

/** The receipt's place in the day's chain: the same block under a read and under an action. */
function ChainSection({ receipt: r }: { receipt: ReceiptLineage['receipt'] }) {
  return (
    <section id="cadeia" aria-labelledby="h-chain" className={`${card} scroll-mt-20 p-4 sm:p-5`}>
      <h2 id="h-chain" className={h2}>
        Encadeamento
      </h2>
      <p className={`${small} mt-1`}>Cada comprovante guarda o hash do anterior. A raiz de cada dia, contado em UTC, fica gravada no banco e numa cópia fora dele, que a conferência da cadeia também lê.</p>
      <div className="mt-3">
        <Props
          items={[
            ['Dia (UTC) e posição', `${fmtDate(r.day)} · fatia ${r.slice} · comprovante ${r.seq}`],
            ['Hash deste comprovante', <span className={`${mono} break-all`} title={r.hash}>{shortHash(r.hash, 24)}</span>],
            ['Hash do anterior', <span className={`${mono} break-all`} title={r.prev_hash}>{shortHash(r.prev_hash, 24)}</span>],
          ]}
        />
      </div>
    </section>
  )
}

/**
 * A governance action (kind `admin`, 5.22): who did what, on what, with which roles or key, and the customer
 * it touched. It delivers no context, so there is no manifest, no items and nothing withheld to show.
 */
function ActionView({ data, back, spaceId }: { data: ReceiptLineage; back: { to: string; label: string }; spaceId: string }) {
  const r = data.receipt
  const steps: [string, string][] = [
    ['comprovante', 'Comprovante'],
    ['cadeia', 'Encadeamento'],
  ]
  return (
    <>
      <PageHeader screen="receipt" back={back} title={`Ação no Console de ${fmtDateTime(r.read_at)}`} description={<span className={mono}>{r.receipt_id}</span>} />

      <nav aria-label="Linhagem" className="mb-6">
        <ol className="flex flex-wrap items-center gap-x-1 gap-y-2 text-[0.875rem]">
          {steps.map(([id, text], i) => (
            <li key={id} className="flex items-center gap-1">
              {i > 0 && <Icon name="chevron" size={16} className="text-ink3" />}
              <a href={`#${id}`} className="inline-flex min-h-11 items-center rounded-md border border-line bg-surface px-2.5 font-semibold text-ink hover:bg-bg lg:min-h-8">
                {text}
              </a>
            </li>
          ))}
        </ol>
      </nav>

      <div className="grid gap-6">
        <section id="comprovante" aria-labelledby="h-receipt" className={`${card} scroll-mt-20 p-4 sm:p-5`}>
          <h2 id="h-receipt" className={`${h2} mb-1`}>
            Comprovante
          </h2>
          <p className={`${small} mb-4 max-w-[72ch]`}>
            Uma ação feita aqui no Console, ou pelo plano de controle, fica na mesma cadeia das leituras: quem fez, o quê, sobre o quê e quando. Ela não entrega contexto a nenhum agente, por isso não tem manifesto, itens nem retidos.
          </p>
          <Props
            items={[
              ['Quem', <ReceiptActor receipt={r} />],
              ['O que fez', <ActionLine receipt={r} />],
              [r.detail?.key_id ? 'Com qual chave' : 'Com quais papéis', <ActionCredentials receipt={r} />],
              [
                'Cliente envolvido',
                r.profile_id ? (
                  <Link className={`${link} font-mono`} to={paths.profile(spaceId, r.profile_id)}>
                    {r.pseudonym ?? r.profile_id}
                  </Link>
                ) : (
                  <span className="text-ink2">nenhum: a ação não foi sobre uma pessoa</span>
                ),
              ],
              ['Audiência', audience(r.audience)],
              ...(r.conversation_id || r.task_id ? [['Conversa ou tarefa', <span className={mono}>{r.conversation_id ?? r.task_id}</span>] as [string, ReactNode]] : []),
              ...(r.purpose ? [['Finalidade', purpose(r.purpose)] as [string, ReactNode]] : []),
            ]}
          />
        </section>

        <ChainSection receipt={r} />
      </div>
    </>
  )
}
