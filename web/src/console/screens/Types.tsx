import type { ReactNode } from 'react'
import { ApiError } from '../api/http'
import type { DataIssue, FieldDecl, ObjectCoverage, ObjectTypeDecl, ObjectTypeRegistry, Ownership, TypeCoverage } from '../api/objectTypes'
import type { Page } from '../api/types'
import { fmtDateTime, fmtInt, fmtPct } from '../lib/format'
import { fieldType } from '../lib/labels'
import { observedDocument } from '../lib/provenance'
import { useResource, type Resource } from '../lib/useResource'
import { useSpace } from '../session'
import { Chip, PageHeader, Props } from '../ui/bits'
import { Section, useAction } from '../ui/forms'
import { useTitle } from '../ui/layout'
import { Empty, ErrorState, Loading } from '../ui/States'
import { btnSecondary, card, mono, small, td, th } from '../ui/styles'

/**
 * The type registry: the objects the company's systems keep and the memory follows, each as
 * the company declared it or as a deriving tool proposed it from the company's own database. Beside each
 * type, how much of each field its objects carry and how old it is, and whether the system it mirrors drifted
 * from the declaration, by the tool's fingerprint or by the traffic itself. Names and counts only: a value of
 * a customer's object never reaches this screen.
 */

const OWNERSHIP: Record<Ownership, string> = {
  subject: 'Do cliente',
  shared: 'Compartilhado',
  agent: 'Memória de trabalho de agente',
}
const ORIGIN = {
  declaration: 'Declarado',
  introspection: 'Derivado do esquema',
  observation: 'Derivado do tráfego',
} as const

/** 42 s, 5 min, 3 h, 2 d: the age of a value, rounded to its unit. */
export function fmtAge(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined) return 'sem dado'
  if (seconds < 60) return `${fmtInt(Math.round(seconds))} s`
  if (seconds < 3600) return `${fmtInt(Math.round(seconds / 60))} min`
  if (seconds < 86400) return `${fmtInt(Math.round(seconds / 3600))} h`
  return `${fmtInt(Math.round(seconds / 86400))} d`
}

const isOff = (error: unknown) => error instanceof ApiError && error.status === 404

export function Types() {
  const { space, cell, control, hasRole } = useSpace()
  useTitle('Tipos', space.environment === 'sandbox')
  const integration = hasRole('integration')
  const registry = useResource(
    (signal) => control.configDocument(space.space_id, 'object-types', { signal }),
    [control, space.space_id],
    integration,
  )
  // What Niadra inferred from the traffic is recorded as diffs by `niadra:observed`, never as the declared
  // document: a space nobody configured has its types only there.
  const applied = useResource((signal) => control.diffs(space.space_id, 'applied', { signal }), [control, space.space_id], integration)
  const coverage = useResource((signal) => cell.objectTypes.coverage({ signal }), [cell], integration)
  const drift = useResource((signal) => cell.objectTypes.issues('drift', { signal }), [cell], integration)
  const undeclared = useResource((signal) => cell.objectTypes.issues('type_undeclared', { signal }), [cell], integration)
  const held = useResource((signal) => cell.objectTypes.issues('field_held', { signal }), [cell], integration)

  if (!integration)
    return (
      <>
        <PageHeader screen="types" />
        <Empty title="Esta tela é do papel Integração.">Peça o papel a quem administra o espaço, em Pessoas e papéis.</Empty>
      </>
    )

  return (
    <>
      <PageHeader
        screen="types"
      />
      <div className="grid min-w-0 gap-6 [&>*]:min-w-0">
        {registry.status === 'loading' || registry.status === 'idle' ? (
          <Loading label="Carregando os tipos do espaço…" />
        ) : registry.status === 'error' || !registry.data ? (
          <ErrorState error={registry.error} onRetry={registry.reload} />
        ) : (
          <Registry
            registry={withInferred(
              (registry.data.document ?? {}) as ObjectTypeRegistry,
              applied.status === 'success' && applied.data ? observedDocument('object-types', applied.data)?.document : undefined,
            )}
            coverage={coverage}
            drift={drift}
            held={held}
          />
        )}
        <Section
          tour="types.undeclared"
          title="Tipos novos que a Niadra passou a acompanhar"
          description="Um sistema ou uma ferramenta mostrou objetos de um tipo que ninguém declarou. A Niadra cria o tipo a partir do que vê e serve os campos aos agentes. Declarar é opcional e só ajusta rótulos e acesso."
        >
          <Issues res={undeclared} loading="Procurando tipos novos…" none="Nenhum tipo novo apareceu.">
            {(items) => <UndeclaredList items={items} />}
          </Issues>
        </Section>
        <Section
          tour="types.held"
          title="Campos retidos até alguém declarar"
          description="Um campo novo que parece dado pessoal (documento, telefone, e-mail, endereço, nome de pessoa) ou que é texto livre longo, como a ordem de um juiz, fica guardado e não chega aos agentes enquanto ninguém o declara. É a única coisa que a Niadra segura sozinha; declarar o campo, com a proteção ou a triagem que ele pede, libera."
        >
          <Issues res={held} loading="Procurando campos retidos…" none="Nenhum campo retido." optional>
            {(items) => <HeldList items={items} />}
          </Issues>
        </Section>
      </div>
    </>
  )
}

/** The declared types, then each type Niadra inferred from the traffic that no declaration names. */
function withInferred(declared: ObjectTypeRegistry, observed: unknown): ObjectTypeRegistry {
  const own = declared.types ?? []
  const names = new Set(own.map((t) => t.type))
  const inferred = ((observed as ObjectTypeRegistry | undefined)?.types ?? []).filter((t) => t && typeof t.type === 'string' && !names.has(t.type))
  return { ...declared, types: [...own, ...inferred] }
}

function Registry({
  registry,
  coverage,
  drift,
  held,
}: {
  registry: ObjectTypeRegistry
  coverage: Resource<ObjectCoverage>
  drift: Resource<Page<DataIssue>>
  held: Resource<Page<DataIssue>>
}) {
  const types = registry.types ?? []
  if (types.length === 0)
    return (
      <Section title="Tipos declarados e derivados">
        <Empty title="Nenhum tipo ainda.">
          Os tipos aparecem sozinhos quando os sistemas da empresa enviam eventos: a Niadra lê os campos, a chave e os estados do que chega. Declarar ou derivar do banco com{' '}
          <span className={mono}>niadra types derive</span> é opcional.
        </Empty>
      </Section>
    )
  const byType = new Map((coverage.data?.types ?? []).map((t) => [t.type, t]))
  const issues = drift.data?.items ?? []
  const inferred = types.filter((t) => t.mirror_of?.derived_by === 'observation').length
  const heldFields = new Set((held.data?.items ?? []).map((i) => `${i.object_type}:${i.field}`))
  const drifting = new Set(issues.map((i) => i.object_type)).size
  return (
    <>
      <Section tour="types.summary" title="Resumo" description="Os tipos que a memória acompanha, declarados, derivados do esquema ou lidos do tráfego. Quando o tráfego traz um campo ou um estado novo, a Niadra passa a servi-lo e deixa uma nota.">
        <dl className="grid gap-3 sm:grid-cols-3">
          <Fact label="Tipos no espaço" value={fmtInt(types.length)} />
          <Fact label="Inferidos do tráfego" value={fmtInt(inferred)} />
          <Fact label="Com campos novos" value={drift.status === 'success' ? fmtInt(drifting) : 'sem dado'} />
        </dl>
      </Section>
      <Section
        tour="types.drift"
        title="Campos novos no tráfego"
        description="O sistema de origem passou a enviar um campo ou um estado que a declaração não tem. Um campo novo chega aos agentes sem esperar ninguém, a não ser que pareça dado pessoal ou texto livre; um estado novo de um tipo que lista os estados fica guardado e aparece aqui. É uma nota, que também sai no webhook type.drift."
      >
        <Issues res={drift} loading="Procurando campos novos…" none="Nenhum campo novo desde a declaração.">
          {(items) => <DriftList items={items} onDone={drift.reload} />}
        </Issues>
      </Section>
      <Section tour="types.list" title="Tipos declarados e derivados" description="A cobertura é medida nos objetos de cada tipo que mudaram por último, só pelos carimbos: quando cada fonte viu cada campo.">
        {coverage.status === 'error' && !isOff(coverage.error) && <ErrorState error={coverage.error} onRetry={coverage.reload} compact />}
        {coverage.status === 'error' && isOff(coverage.error) && (
          <p className={`${small} mb-4`}>A leitura por tipo está desligada neste espaço: a cobertura aparece quando o recurso state é ligado em Configuração.</p>
        )}
        <ul className="grid gap-4" aria-label="Tipos do espaço">
          {types.map((t) => (
            <TypeCard
              key={t.type}
              decl={t}
              coverage={byType.get(t.type)}
              loading={coverage.status === 'loading'}
              drifting={issues.some((i) => i.object_type === t.type)}
              held={heldFields}
            />
          ))}
        </ul>
      </Section>
    </>
  )
}

function Fact({ label, value }: { label: string; value: string }) {
  return (
    <div className={`${card} px-4 py-3`}>
      <dt className={small}>{label}</dt>
      <dd className="mt-0.5 font-display text-[1.5rem] font-semibold tabular-nums text-ink">{value}</dd>
    </div>
  )
}

function Issues({
  res,
  loading,
  none,
  optional = false,
  children,
}: {
  res: Resource<Page<DataIssue>>
  loading: string
  none: string
  /** A kind a cell one version behind does not know yet: its refusal reads as none. */
  optional?: boolean
  children: (items: DataIssue[]) => ReactNode
}) {
  if (res.status === 'loading' || res.status === 'idle') return <Loading label={loading} />
  if (res.status === 'error' || !res.data) {
    if (optional && res.error instanceof ApiError && res.error.status === 422) return <Empty title={none} />
    if (isOff(res.error))
      return <Empty title="Os avisos de dado estão desligados neste espaço.">Eles se ligam com o recurso state, em Configuração.</Empty>
    return <ErrorState error={res.error} onRetry={res.reload} compact />
  }
  if (res.data.items.length === 0) return <Empty title={none} />
  return <>{children(res.data.items)}</>
}

function DriftList({ items, onDone }: { items: DataIssue[]; onDone: () => void }) {
  return (
    <ul className="grid gap-3" aria-label="Campos novos no tráfego">
      {items.map((i) => (
        <DriftItem key={i.issue_id} issue={i} onDone={onDone} />
      ))}
    </ul>
  )
}

function DriftItem({ issue: i, onDone }: { issue: DataIssue; onDone: () => void }) {
  const { cell } = useSpace()
  const ack = useAction<unknown>()
  const how = i.source ? `no tráfego de ${i.source}` : 'pela impressão digital do catálogo'
  return (
    <li className={`${card} px-4 py-3`}>
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <p className="min-w-0 font-semibold text-ink">
          <span className={mono}>{i.object_type}</span>
          {i.field ? (
            <>
              {' '}
              · campo <span className={mono}>{i.field}</span>
            </>
          ) : (
            ' · vários campos'
          )}
        </p>
        <Chip>{i.occurrences === 1 ? 'Visto 1 vez' : `Visto ${fmtInt(i.occurrences)} vezes`}</Chip>
      </div>
      <p className={`${small} mt-1`}>
        Visto {how}, desde {fmtDateTime(i.opened_at)}; a última vez em {fmtDateTime(i.last_seen_at)}.
      </p>
      {i.refs.length > 0 && <p className={`${mono} mt-1.5 break-all text-ink2`}>{i.refs.slice(0, 3).join(' · ')}{i.refs.length > 3 ? ` e mais ${fmtInt(i.refs.length - 3)}` : ''}</p>}
      <div className="mt-3 flex flex-wrap items-center gap-3">
        <button type="button" className={btnSecondary} disabled={ack.busy} onClick={() => ack.run(() => cell.objectTypes.acknowledge(i.issue_id), onDone)}>
          {ack.busy ? 'Arquivando…' : 'Arquivar a nota'}
        </button>
        <span className={small}>Uma ocorrência nova abre outra nota.</span>
      </div>
      {ack.error !== null && <ErrorState error={ack.error} compact />}
    </li>
  )
}

function UndeclaredList({ items }: { items: DataIssue[] }) {
  return (
    <div className={`${card} overflow-x-auto`}>
      <table className="w-full min-w-[36rem] border-collapse">
        <caption className="sr-only">Tipos novos que a Niadra acompanha</caption>
        <thead>
          <tr>
            {['Tipo', 'Ferramenta', 'Ocorrências', 'Objetos citados', 'Visto pela última vez'].map((h) => (
              <th key={h} scope="col" className={th}>
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {items.map((i) => (
            <tr key={i.issue_id}>
              <td className={td}>
                <span className={mono}>{i.object_type}</span>
              </td>
              <td className={td}>
                <span className={mono}>{i.source ?? 'sem nome'}</span>
              </td>
              <td className={`${td} tabular-nums`}>{fmtInt(i.occurrences)}</td>
              <td className={`${td} tabular-nums`}>{fmtInt(i.refs.length)}</td>
              <td className={`${td} whitespace-nowrap`}>{fmtDateTime(i.last_seen_at)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function HeldList({ items }: { items: DataIssue[] }) {
  return (
    <div className={`${card} overflow-x-auto`}>
      <table className="w-full min-w-[36rem] border-collapse">
        <caption className="sr-only">Campos retidos até alguém declarar</caption>
        <thead>
          <tr>
            {['Tipo', 'Campo', 'Fonte', 'Ocorrências', 'Visto pela última vez'].map((h) => (
              <th key={h} scope="col" className={th}>
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {items.map((i) => (
            <tr key={i.issue_id}>
              <td className={td}>
                <span className={mono}>{i.object_type ?? 'sem tipo'}</span>
              </td>
              <td className={td}>
                <span className={mono}>{i.field ?? 'vários campos'}</span>
              </td>
              <td className={td}>
                <span className={mono}>{i.source ?? 'sem nome'}</span>
              </td>
              <td className={`${td} tabular-nums`}>{fmtInt(i.occurrences)}</td>
              <td className={`${td} whitespace-nowrap`}>{fmtDateTime(i.last_seen_at)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function TypeCard({
  decl,
  coverage,
  loading,
  drifting,
  held,
}: {
  decl: ObjectTypeDecl
  coverage?: TypeCoverage
  loading: boolean
  drifting: boolean
  /** `type:field` of the fields held back until someone declares them. */
  held: ReadonlySet<string>
}) {
  const mirror = decl.mirror_of
  const origin = ORIGIN[mirror?.derived_by ?? 'declaration']
  const shares = new Map((coverage?.fields ?? []).map((f) => [f.field, f]))
  // A field the system sends and the type does not declare is served all the same (or held): list it too.
  const fields: [string, FieldDecl | null][] = [
    ...Object.entries(decl.fields ?? {}),
    ...[...shares.keys()].filter((name) => !(decl.fields ?? {})[name]).map((name): [string, null] => [name, null]),
  ]
  return (
    <li className={`${card} min-w-0 px-4 py-4`}>
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h3 className={`${mono} text-[0.9375rem] font-semibold text-ink`}>{decl.type}</h3>
        <span className="flex flex-wrap gap-1.5">
          <Chip>{OWNERSHIP[decl.ownership]}</Chip>
          <Chip tone={mirror?.derived_by === 'introspection' ? 'ok' : 'neutral'}>{origin}</Chip>
          {drifting && <Chip>Campo novo</Chip>}
        </span>
      </div>
      <div className="mt-3">
        <Props
          items={[
            ['Sistema de origem', mirror ? <span className={mono}>{mirror.system}</span> : 'nenhum: memória de trabalho de um agente'],
            ...(mirror?.fingerprint ? ([['Impressão digital', <span className={`${mono} break-all`}>{mirror.fingerprint.slice(0, 19)}…</span>]] as [string, ReactNode][]) : []),
            ...(mirror ? ([['Campos novos', mirror.drift === 'ignore' ? 'Servidos sem nota' : 'Servidos, com nota aqui e no webhook']] as [string, ReactNode][]) : []),
            ['Estados', decl.states?.length ? decl.states.join(', ') : 'sem ciclo de vida declarado'],
            ['Objetos medidos', coverage ? fmtInt(coverage.objects) : loading ? 'medindo…' : 'sem dado'],
          ]}
        />
      </div>
      {fields.length === 0 ? (
        <p className={`${small} mt-3`}>Nenhum campo visto ainda.</p>
      ) : (
        <div className="mt-4 overflow-x-auto">
          <table className="w-full min-w-[34rem] border-collapse">
            <caption className="sr-only">Campos de {decl.type} e a cobertura de cada um</caption>
            <thead>
              <tr>
                {['Campo', 'Tipo', 'Cobertura', 'Idade mediana', 'Proteção'].map((h) => (
                  <th key={h} scope="col" className={th}>
                    {h}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {fields.map(([name, spec]) => {
                const share = shares.get(name)
                return (
                  <tr key={name}>
                    <td className={td}>
                      <span className={mono}>{name}</span>
                    </td>
                    <td className={`${td} ${small}`}>{spec ? fieldType(spec.type) : 'não declarado'}</td>
                    <td className={td}>{share ? <Coverage value={share.coverage} /> : <span className={small}>sem dado</span>}</td>
                    <td className={`${td} tabular-nums`}>{share ? fmtAge(share.age_p50_s) : <span className={small}>sem dado</span>}</td>
                    <td className={td}>
                      {spec ? <Protection spec={spec} /> : held.has(`${decl.type}:${name}`) ? <Chip>Retido até declarar</Chip> : <span className={small}>servido sem declaração</span>}
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}
    </li>
  )
}

function Coverage({ value }: { value: number }) {
  return (
    <span className="flex items-center gap-2">
      <span className="relative h-1.5 w-20 shrink-0 overflow-hidden rounded-full bg-line" aria-hidden="true">
        <span className="absolute inset-y-0 left-0 rounded-full bg-accent" style={{ width: `${Math.round(value * 100)}%` }} />
      </span>
      <span className="tabular-nums">{fmtPct(value)}</span>
    </span>
  )
}

function Protection({ spec }: { spec: FieldDecl }) {
  const marks: string[] = []
  if (spec.sensitivity && spec.sensitivity !== 'none') marks.push('Sensível')
  if (spec.pii) marks.push('Dado pessoal')
  if (spec.access?.length) marks.push('Acesso por agente')
  if (marks.length === 0) return <span className={small}>nenhuma</span>
  return (
    <span className="flex flex-wrap gap-1">
      {marks.map((m) => (
        <Chip key={m}>{m}</Chip>
      ))}
    </span>
  )
}
