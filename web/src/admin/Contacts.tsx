/** Contatos: everyone who sent the site's contact form (/enterprise), newest first. */
import { useDeferredValue, useId, useMemo, useState } from 'react'
import { fmtDateTime, fmtInt } from '../console/lib/format'
import { Icon } from '../console/ui/Icon'
import { btnSecondary, card, h1, input, small, td, th } from '../console/ui/styles'
import { api, type Contact } from './api'
import { contactsCsv, download } from './csv'
import { FORMS, HEARD, INTERESTS, sourceName } from './labels'
import { Empty, ErrorBox, Loading, useLoad } from './ui'

/** Lower case without accents, so "joao" finds "João". */
const fold = (s: string) => s.normalize('NFD').replace(/\p{Diacritic}/gu, '').toLowerCase()

function matches(c: Contact, query: string): boolean {
  const q = fold(query.trim())
  if (!q) return true
  return fold([c.name, c.company, c.email, c.message, c.email_domain ?? ''].join('\n')).includes(q)
}

export function Contacts({ onExpired }: { onExpired: () => void }) {
  const { state, reload } = useLoad(api.contacts, 'contacts', onExpired)
  const [query, setQuery] = useState('')
  const deferred = useDeferredValue(query)
  const searchId = useId()
  const rows = state.status === 'ok' ? state.data : []
  const shown = useMemo(() => rows.filter((c) => matches(c, deferred)), [rows, deferred])

  const exportCsv = () => {
    const stamp = new Date().toISOString().slice(0, 10)
    download(`contatos-niadra-${stamp}.csv`, contactsCsv(shown, fmtDateTime))
  }

  return (
    <section aria-labelledby="contatos-titulo">
      <header className="mb-5 flex flex-col gap-4 lg:flex-row lg:items-end lg:justify-between">
        <div className="min-w-0">
          <h1 id="contatos-titulo" className={h1}>
            Contatos
          </h1>
          <p className={`${small} mt-1`} aria-live="polite">
            {state.status !== 'ok'
              ? 'Pedidos enviados pelo formulário do site.'
              : shown.length === rows.length
                ? `${fmtInt(rows.length)} ${rows.length === 1 ? 'pedido' : 'pedidos'}, do mais novo para o mais antigo.`
                : `${fmtInt(shown.length)} de ${fmtInt(rows.length)} pedidos.`}
          </p>
        </div>
        <div className="flex flex-col gap-2 sm:flex-row sm:items-end">
          <div className="min-w-0 sm:w-72">
            <label htmlFor={searchId} className="mb-1.5 block text-[0.8125rem] font-semibold text-ink">
              Buscar
            </label>
            <input
              id={searchId}
              type="search"
              className={input}
              placeholder="Nome, empresa, e-mail ou mensagem"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              disabled={state.status !== 'ok'}
            />
          </div>
          <button type="button" className={btnSecondary} onClick={exportCsv} disabled={state.status !== 'ok' || !shown.length}>
            <Icon name="download" size={16} />
            Exportar CSV
          </button>
        </div>
      </header>

      {state.status === 'loading' && <Loading label="Carregando os contatos…" />}
      {state.status === 'error' && <ErrorBox error={state.error} onRetry={reload} />}
      {state.status === 'ok' && rows.length === 0 && (
        <Empty title="Nenhum pedido de contato ainda.">Quando alguém enviar o formulário de /enterprise, ou o formulário curto do benchmark, das integrações e dos posts, o pedido aparece aqui, com a mensagem inteira, a origem da visita e a página de onde saiu.</Empty>
      )}
      {state.status === 'ok' && rows.length > 0 && shown.length === 0 && (
        <Empty title={`Nenhum contato com “${query.trim()}”.`}>A busca olha nome, empresa, e-mail e mensagem, sem diferenciar acentos.</Empty>
      )}
      {shown.length > 0 && (
        <>
          <div className={`${card} hidden overflow-hidden lg:block`}>
            <table className="w-full table-fixed text-[0.875rem]">
              <caption className="sr-only">Pedidos de contato, do mais novo para o mais antigo</caption>
              <colgroup>
                <col className="w-[9.5rem]" />
                <col className="w-[11rem]" />
                <col className="w-[11rem]" />
                <col className="w-[15rem]" />
                <col />
              </colgroup>
              <thead>
                <tr>
                  <th scope="col" className={th}>Data</th>
                  <th scope="col" className={th}>Nome</th>
                  <th scope="col" className={th}>Empresa</th>
                  <th scope="col" className={th}>E-mail</th>
                  <th scope="col" className={th}>Mensagem</th>
                </tr>
              </thead>
              <tbody>
                {shown.map((c) => (
                  <tr key={c.id}>
                    <td className={`${td} font-mono text-[0.75rem] leading-5 text-ink2`}>{fmtDateTime(c.created_at)}</td>
                    <td className={`${td} break-words font-semibold text-ink`}>{c.name || <span className="font-normal text-ink3">sem nome</span>}</td>
                    <td className={`${td} break-words`}>
                      {c.company || c.email_domain}
                      <Meta c={c} />
                    </td>
                    <td className={`${td} break-all font-mono text-[0.75rem] leading-5`}>{c.email}</td>
                    <td className={`${td} whitespace-pre-line break-words leading-6 text-ink`}>{c.message}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <ul className="grid gap-3 lg:hidden" aria-label="Pedidos de contato, do mais novo para o mais antigo">
            {shown.map((c) => (
              <li key={c.id} className={`${card} p-4`}>
                <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
                  <p className="font-semibold text-ink">{c.name || <span className="font-normal text-ink3">sem nome</span>}</p>
                  <p className="font-mono text-[0.75rem] text-ink2">{fmtDateTime(c.created_at)}</p>
                </div>
                <p className="mt-0.5 text-[0.875rem] text-ink">{c.company || c.email_domain}</p>
                <p className="mt-1 break-all font-mono text-[0.75rem] text-ink2">{c.email}</p>
                <Meta c={c} />
                <p className="mt-3 whitespace-pre-line break-words border-t border-line2 pt-3 text-[0.875rem] leading-6 text-ink">{c.message}</p>
              </li>
            ))}
          </ul>
        </>
      )}
    </section>
  )
}

/**
 * Interest, which form, where the visit came from, which page the form was sent from and what the person
 * said about how they heard of Niadra: the context of the request, in one line. It is what says which
 * page and which channel convert.
 */
function Meta({ c }: { c: Contact }) {
  const parts = [
    INTERESTS[c.interest] ?? c.interest,
    c.form === 'quick' ? FORMS.quick : null,
    `origem: ${sourceName(c.source)}`,
    c.page && c.page !== '/enterprise' && c.page !== '/en/enterprise' ? `página: ${c.page}` : null,
    c.heard_from ? `conheceu: ${HEARD[c.heard_from] ?? c.heard_from}` : null,
  ]
  return <p className="mt-1 text-[0.8125rem] leading-5 text-ink2">{parts.filter(Boolean).join(' · ')}</p>
}
