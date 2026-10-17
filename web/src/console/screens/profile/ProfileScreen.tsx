import { useState } from 'react'
import { Link, useParams, useSearchParams } from 'react-router-dom'
import { useFeatures, type Feature } from '../../lib/features'
import { fmtDate } from '../../lib/format'
import { handleStatus, handleType, handleWeight, linkRole, subjectKind, verification } from '../../lib/labels'
import { useResource } from '../../lib/useResource'
import { paths } from '../../paths'
import { useSpace } from '../../session'
import { Chip, PageHeader } from '../../ui/bits'
import { useTitle } from '../../ui/layout'
import { Empty, ErrorState, Loading } from '../../ui/States'
import { Tabs } from '../../ui/Tabs'
import { card, link, mono, small, td, th } from '../../ui/styles'
import { AgentStateTab } from './AgentStateTab'
import { ContextPanel } from './ContextPanel'
import { InferencesTab } from './Inferences'
import { EpisodesTab, FactsTab, Loaded, ObjectsTab, OpenItemsTab, TraitsTab, WithheldNote } from './MemoryTabs'
import { RelationsTab } from './RelationsTab'

type TabKey = 'facts' | 'open' | 'timeline' | 'patterns' | 'objects' | 'relations' | 'working' | 'inferences' | 'ids'
const TABS: { key: TabKey; label: string; feature?: Feature }[] = [
  { key: 'facts', label: 'Fatos' },
  { key: 'open', label: 'Pendências e promessas' },
  { key: 'timeline', label: 'Linha do tempo' },
  { key: 'patterns', label: 'Padrões' },
  { key: 'objects', label: 'Objetos e ações' },
  { key: 'relations', label: 'Relações' },
  { key: 'working', label: 'Memória de trabalho', feature: 'agent_state' },
  { key: 'inferences', label: 'Inferências', feature: 'signals' },
  { key: 'ids', label: 'Identificadores e vínculos' },
]

/** `?aba=` names a tab in the tenant's language: the map of relations and the tutorials link here with it. */
const ABA: Record<string, TabKey> = {
  fatos: 'facts',
  pendencias: 'open',
  'linha-do-tempo': 'timeline',
  padroes: 'patterns',
  objetos: 'objects',
  relacoes: 'relations',
  'memoria-de-trabalho': 'working',
  inferencias: 'inferences',
  ids: 'ids',
}

/**
 * One customer's memory, for inspection and governance. Every item shows its
 * origin; the right column shows the exact context an agent would receive.
 * Reads here pass through the same policy as the agents', with the audience of
 * the viewer's role, and leave an `admin` receipt.
 */
export function ProfileScreen() {
  const { profileId = '' } = useParams()
  const { space, cell } = useSpace()
  const profile = useResource((signal) => cell.profile(profileId, { signal }), [profileId, cell])
  const memory = useResource((signal) => cell.memory(profileId, { signal }), [profileId, cell])
  const [params] = useSearchParams()
  const [tab, setTab] = useState<TabKey>(() => ABA[params.get('aba') ?? ''] ?? 'facts')
  const features = useFeatures()
  const tabs = TABS.filter((t) => !t.feature || features.on.has(t.feature))
  const objects = useResource((signal) => cell.objects(profileId, { signal }), [profileId, cell], tab === 'objects')
  const working = useResource((signal) => cell.agentStates(profileId, { signal }), [profileId, cell], tab === 'working')
  useTitle(profile.data ? `Cliente ${profile.data.pseudonym}` : 'Cliente', space.environment === 'sandbox')

  const back = { to: paths.customers(space.space_id), label: 'Clientes' }

  if (profile.status === 'loading' || profile.status === 'idle') return <Loading label="Abrindo a memória do cliente…" />
  if (profile.status === 'error' || !profile.data)
    return (
      <>
        <PageHeader screen="profile" back={back} />
        <ErrorState error={profile.error} onRetry={profile.reload} />
      </>
    )

  const p = profile.data
  return (
    <>
      <PageHeader
        screen="profile"
        back={back}
        title={p.pseudonym}
        description={
          <>
            {subjectKind(p.kind)} · {p.handles.length} {p.handles.length === 1 ? 'identificador' : 'identificadores'}
            {p.partial && ' · conhecido só por IDs privados de canal'}
          </>
        }
      />

      <div className="grid gap-6">
        <ContextPanel profileId={p.profile_id} />

        <section aria-label="Memória do cliente" className="min-w-0">
          {memory.data && tab !== 'objects' && tab !== 'relations' && tab !== 'working' && tab !== 'inferences' && tab !== 'ids' && <WithheldNote withheld={memory.data.withheld} />}
          <div data-tour="profile.tabs">
          <Tabs label="Memória do cliente" tabs={tabs} active={tab} onChange={setTab}>
            {tab === 'facts' && <Loaded res={memory} loading="Carregando os fatos…">{(m) => <FactsTab memory={m} profileId={p.profile_id} onChanged={memory.reload} />}</Loaded>}
            {tab === 'open' && <Loaded res={memory} loading="Carregando pendências e promessas…">{(m) => <OpenItemsTab memory={m} profileId={p.profile_id} onChanged={memory.reload} />}</Loaded>}
            {tab === 'timeline' && <Loaded res={memory} loading="Carregando a linha do tempo…">{(m) => <EpisodesTab memory={m} />}</Loaded>}
            {tab === 'patterns' && (
              <Loaded res={memory} loading="Carregando os padrões…">
                {(m) => <TraitsTab traits={m.traits} profileId={p.profile_id} onChanged={memory.reload} />}
              </Loaded>
            )}
            {tab === 'objects' && <Loaded res={objects} loading="Carregando pedidos, tickets e faturas…">{(o) => <ObjectsTab objects={o} />}</Loaded>}
            {tab === 'relations' && <RelationsTab profileId={p.profile_id} pseudonym={p.pseudonym} />}
            {tab === 'working' && <AgentStateTab res={working} />}
            {tab === 'inferences' && <InferencesTab profileId={p.profile_id} />}
            {tab === 'ids' && <IdentifiersTab profile={p} />}
          </Tabs>
          </div>
        </section>
      </div>
    </>
  )
}

function IdentifiersTab({ profile: p }: { profile: import('../../api/types').ProfileOut }) {
  return (
    <div className="grid gap-6">
      {p.handles.length === 0 ? (
        <Empty title="Nenhum identificador visível para o seu papel." />
      ) : (
        <div className={`${card} overflow-x-auto`}>
          <table className="w-full min-w-[48rem] border-collapse">
            <caption className="sr-only">Identificadores do cliente, mascarados pelo seu papel</caption>
            <thead>
              <tr>
                <th scope="col" className={th}>Tipo</th>
                <th scope="col" className={th}>Valor</th>
                <th scope="col" className={th}>Peso</th>
                <th scope="col" className={th}>Nível</th>
                <th scope="col" className={th}>Estado</th>
                <th scope="col" className={th}>Visto de</th>
                <th scope="col" className={th}>ID</th>
              </tr>
            </thead>
            <tbody>
              {p.handles.map((h) => (
                <tr key={h.handle_id}>
                  <td className={td}>
                    {handleType(h.type)}
                    {h.scope && h.scope !== '-' && <span className="block text-[0.8125rem] text-ink2">{h.scope}</span>}
                  </td>
                  <td className={`${td} ${mono}`}>{h.value}</td>
                  <td className={td}>{handleWeight(h.weight)}</td>
                  <td className={`${td} ${mono}`}>{verification(h.level)}</td>
                  <td className={td}>{h.status === 'active' ? <Chip tone="ok">Ativo</Chip> : <Chip tone={h.status === 'blocked' ? 'danger' : 'neutral'}>{handleStatus(h.status)}</Chip>}</td>
                  <td className={`${td} whitespace-nowrap text-[0.8125rem]`}>
                    {fmtDate(h.first_seen)} a {fmtDate(h.last_seen)}
                  </td>
                  <td className={`${td} ${mono} text-ink2`}>{h.handle_id}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {p.kind !== 'person' && <ParentsBlock profile={p} />}
      <div>
        <h3 className="mb-2 text-[0.875rem] font-semibold text-ink">Vínculos com contas e parceiros</h3>
        {p.links.length === 0 ? (
          <p className={small}>Nenhum vínculo. Vínculos se criam em Identidade.</p>
        ) : (
          <ul className={`${card} divide-y divide-line2`}>
            {p.links.map((l) => (
              <li key={l.link_id} className="px-4 py-3">
                <p className="text-ink">
                  <strong title={linkRole(l.role).raw ?? undefined}>{linkRole(l.role).label}</strong> {l.can_see_contacts && <span className="text-ink2">· vê os contatos da organização</span>}
                </p>
                <p className={`${mono} mt-0.5 text-ink2`}>
                  pessoa {l.person_handle_id} · organização {l.org_handle_id} · de {fmtDate(l.valid_from)}
                  {l.valid_to ? ` a ${fmtDate(l.valid_to)}` : ', sem fim'}
                </p>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  )
}

/** An organization's parent and subsidiaries: one level, each a profile of its own. */
function ParentsBlock({ profile: p }: { profile: import('../../api/types').ProfileOut }) {
  const { space } = useSpace()
  const subsidiaries = p.subsidiaries ?? []
  const open = (profileId: string | null, label: string) =>
    profileId ? (
      <Link className={link} to={paths.profile(space.space_id, profileId)}>
        {label}
      </Link>
    ) : (
      <span className="text-ink2">{label}</span>
    )
  return (
    <div>
      <h3 className="mb-2 text-[0.875rem] font-semibold text-ink">Matriz e subsidiárias</h3>
      {!p.parent && subsidiaries.length === 0 ? (
        <p className={small}>Nenhuma matriz nem subsidiária. Elas se registram em Identidade.</p>
      ) : (
        <ul className={`${card} divide-y divide-line2`}>
          {p.parent && (
            <li className="px-4 py-3">
              <p className="text-ink">
                <strong>Matriz:</strong> {open(p.parent.parent_profile_id, 'abrir a matriz')}
              </p>
              <p className={`${mono} mt-0.5 text-ink2`}>
                ligação {p.parent.parent_link_id} · de {fmtDate(p.parent.valid_from)}
                {p.parent.valid_to ? ` a ${fmtDate(p.parent.valid_to)}` : ', sem fim'}
              </p>
            </li>
          )}
          {subsidiaries.map((s) => (
            <li key={s.parent_link_id} className="px-4 py-3">
              <p className="text-ink">
                <strong>Subsidiária:</strong> {open(s.org_profile_id, 'abrir a subsidiária')}
              </p>
              <p className={`${mono} mt-0.5 text-ink2`}>
                ligação {s.parent_link_id} · de {fmtDate(s.valid_from)}
                {s.valid_to ? ` a ${fmtDate(s.valid_to)}` : ', sem fim'}
              </p>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
