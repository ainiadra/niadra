import { ApiError } from '../../api/http'
import type { AgentStateMeta, AgentStateMetaPage } from '../../api/types'
import { fmtDateTime, fmtInt } from '../../lib/format'
import type { Resource } from '../../lib/useResource'
import { Chip, SourceName } from '../../ui/bits'
import { Empty, ErrorState, Loading } from '../../ui/States'
import { card, mono, small } from '../../ui/styles'

const SCOPES: Record<AgentStateMeta['scope_kind'], string> = {
  conversation: 'Conversa',
  task: 'Tarefa',
  object: 'Objeto',
  subject: 'Cliente',
}

/** A working state is kept whole, whatever its size: the size reads in bytes below one KB, in KB above. */
export function fmtSize(bytes: number): string {
  if (bytes < 1024) return `${fmtInt(bytes)} B`
  return `${new Intl.NumberFormat('pt-BR', { maximumFractionDigits: 1 }).format(bytes / 1024)} KB`
}

/**
 * The working states agents keep about this customer, one per agent and scope. Written by the agent's code,
 * never by a model, and never shown: the Console lists their size, version and dates only (24.3.9).
 */
export function AgentStateTab({ res }: { res: Resource<AgentStateMetaPage> }) {
  if (res.status === 'loading' || res.status === 'idle') return <Loading label="Carregando a memória de trabalho dos agentes…" />
  if (res.status === 'error' || !res.data) {
    if (res.error instanceof ApiError && res.error.status === 404)
      return (
        <Empty title="A memória de trabalho dos agentes está desligada neste espaço.">
          Ela se liga em Configuração, no documento de recursos, com o recurso de memória de trabalho.
        </Empty>
      )
    return <ErrorState error={res.error} onRetry={res.reload} />
  }
  const items = res.data.items
  return (
    <div className="grid gap-3">
      <p className={small}>O conteúdo é do agente e não aparece aqui: a Niadra mostra o tamanho, a versão e as datas de cada estado.</p>
      {items.length === 0 ? (
        <Empty title="Nenhum agente guardou memória de trabalho sobre este cliente." />
      ) : (
        <ul className={`${card} divide-y divide-line2`} aria-label="Memória de trabalho dos agentes sobre este cliente, sem o conteúdo">
          {items.map((s) => (
            <li key={`${s.source_id}:${s.agent}:${s.scope_kind}:${s.updated_at}`} className="px-4 py-3">
              <div className="flex flex-wrap items-baseline justify-between gap-2">
                <div className="flex min-w-0 flex-wrap items-baseline gap-x-2 gap-y-1">
                  <span className={`${mono} font-semibold text-ink`}>{s.agent}</span>
                  <Chip>{SCOPES[s.scope_kind]}</Chip>
                </div>
                <p className="text-ink tabular-nums">
                  {fmtSize(s.bytes)} <span className="text-ink2">· versão {fmtInt(s.version)}</span>
                </p>
              </div>
              <p className={`${small} mt-0.5`}>
                Atualizada em <span className="whitespace-nowrap">{fmtDateTime(s.updated_at)}</span> ·{' '}
                {s.valid_until ? (
                  <>
                    vale até <span className="whitespace-nowrap">{fmtDateTime(s.valid_until)}</span>
                  </>
                ) : (
                  'guardada até o tipo do agente definir uma retenção'
                )}
              </p>
              <p className={`${small} mt-0.5`}>
                Guardada por <SourceName id={s.source_id} withVendor />
              </p>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
