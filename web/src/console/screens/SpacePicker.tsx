import { useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { describeError } from '../api/describe'
import type { MeOut, ProjectOut, Space } from '../api/types'
import { useResource } from '../lib/useResource'
import { role } from '../lib/labels'
import { paths } from '../paths'
import { RegionNotConfigured, useConsole } from '../session'
import { Chip } from '../ui/bits'
import { Icon } from '../ui/Icon'
import { Centered, useTitle } from '../ui/layout'
import { Empty, ErrorState, Loading } from '../ui/States'
import { btnQuiet, danger, h1, h2, mono, small } from '../ui/styles'

/**
 * The spaces the person can open come from /v1/users/me, with the roles held in each.
 * /v1/projects adds the project name and the cell, which picks the data API (config.ts).
 */
function spacesFrom(me: MeOut, projects: ProjectOut[] | null): Space[] {
  const byProject = new Map((projects ?? []).map((p) => [p.project_id, p]))
  return me.spaces.map((s) => {
    const p = byProject.get(s.project_id)
    const cell = p?.spaces.find((x) => x.space_id === s.space_id)?.cell_id ?? ''
    return { ...s, cell_id: cell, project_name: p?.name ?? s.slug }
  })
}

export async function loadSpaces(control: ReturnType<typeof useConsole>['control'], signal: AbortSignal): Promise<{ me: MeOut; spaces: Space[] }> {
  const [me, projects] = await Promise.all([control.me({ signal }), control.projects({ signal }).catch(() => null)])
  return { me, spaces: spacesFrom(me, projects) }
}

export const spaceLabel = (s: Space) => (s.environment === 'sandbox' ? 'Sandbox' : 'Produção')

/** Spaces grouped by project: each project has a sandbox and a production space. */
export function SpacePicker() {
  useTitle('Escolher espaço')
  const { control, auth, enterSpace, signOut } = useConsole()
  const navigate = useNavigate()
  const loaded = useResource((signal) => loadSpaces(control, signal), [control])
  const [opening, setOpening] = useState<string | null>(null)
  const [openError, setOpenError] = useState<{ id: string; message: string } | null>(null)

  async function open(space: Space) {
    setOpening(space.space_id)
    setOpenError(null)
    try {
      await enterSpace(space)
      navigate(paths.customers(space.space_id))
    } catch (err) {
      const message =
        err instanceof RegionNotConfigured
          ? `A região ${err.region} não tem endereço de API no config.json do Console. Quem publica o Console precisa incluir essa região.`
          : `${describeError(err).title} ${describeError(err).detail}`
      setOpenError({ id: space.space_id, message })
    } finally {
      setOpening(null)
    }
  }

  const groups = new Map<string, { name: string; spaces: Space[] }>()
  for (const s of loaded.data?.spaces ?? []) {
    const g = groups.get(s.project_id) ?? { name: s.project_name, spaces: [] }
    g.spaces.push(s)
    groups.set(s.project_id, g)
  }
  const withSpaces = [...groups.entries()].map(([project_id, g]) => ({ project_id, ...g }))

  return (
    <Centered wide>
      <div className="flex items-start justify-between gap-4">
        <div className="min-w-0">
          <h1 className={h1}>Escolha o espaço</h1>
          <p className={`${small} mt-1.5`}>
            {loaded.data?.me.email ?? auth?.email}
          </p>
        </div>
        <div className="flex shrink-0 flex-col items-end gap-1 sm:flex-row sm:items-center">
          <Link to={paths.account} state={{ from: paths.spaces }} className={btnQuiet}>
            <Icon name="person" size={16} />
            Sua conta
          </Link>
          <button type="button" className={btnQuiet} onClick={() => signOut()}>
            <Icon name="exit" size={16} />
            Sair
          </button>
        </div>
      </div>

      <div className="mt-6">
        {loaded.status === 'loading' && <Loading label="Buscando os espaços liberados para você…" />}
        {loaded.status === 'error' && <ErrorState error={loaded.error} onRetry={loaded.reload} />}
        {loaded.status === 'success' && withSpaces.length === 0 && (
          <Empty title="Nenhum espaço liberado para você.">Quem administra o Console da sua empresa pode dar acesso a um projeto.</Empty>
        )}
        {loaded.status === 'success' && withSpaces.length > 0 && (
          <div className="grid gap-6">
            {withSpaces.map((p) => (
              <section key={p.project_id} aria-labelledby={`p-${p.project_id}`}>
                <h2 id={`p-${p.project_id}`} className={`${h2} mb-2`}>
                  {p.name}
                </h2>
                <ul className="divide-y divide-line2 overflow-hidden rounded-xl border border-line">
                  {p.spaces.map((s) => (
                    <li key={s.space_id}>
                      <button
                        type="button"
                        onClick={() => open(s)}
                        disabled={opening !== null}
                        aria-label={`${p.name}, ${spaceLabel(s)}, região ${s.region}`}
                        className="flex min-h-14 w-full items-center gap-3 bg-surface px-4 py-3 text-left transition-colors hover:bg-bg disabled:cursor-wait"
                      >
                        <span className="min-w-0 flex-1">
                          <span className="flex flex-wrap items-center gap-2">
                            <span className="font-semibold text-ink">{spaceLabel(s)}</span>
                            {s.environment === 'sandbox' && <Chip tone="dark">dados sintéticos</Chip>}
                          </span>
                          <span className={`${mono} mt-0.5 block text-ink2`}>
                            {s.slug} · região {s.region}
                          </span>
                          <span className="mt-0.5 block text-[0.8125rem] text-ink2">{s.roles.map(role).join(', ') || 'sem papel'}</span>
                        </span>
                        <span className="text-[0.8125rem] font-semibold text-ink2">{opening === s.space_id ? 'Abrindo…' : ''}</span>
                        <Icon name="chevron" className="text-ink2" />
                      </button>
                      {openError?.id === s.space_id && (
                        <p role="alert" className={`border-t border-line2 bg-surface px-4 py-2.5 text-[0.8125rem] font-semibold ${danger.text}`}>
                          {openError.message}
                        </p>
                      )}
                    </li>
                  ))}
                </ul>
              </section>
            ))}
          </div>
        )}
      </div>
    </Centered>
  )
}
