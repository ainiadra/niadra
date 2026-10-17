import { useMemo } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { useFeatures } from '../lib/features'
import { NAV_GROUPS, SCREENS } from '../lib/screens'
import { useProgress } from '../lib/tutorialProgress'
import { TUTORIALS } from '../lib/tutorials'
import { useResource, type Resource } from '../lib/useResource'
import { paths } from '../paths'
import { useSpace } from '../session'
import { PageHeader, useSources } from '../ui/bits'
import { Icon, type IconName } from '../ui/Icon'
import { useTitle } from '../ui/layout'
import { btnPrimary, btnSecondary, card, h2, small } from '../ui/styles'
import { startTutorial } from '../ui/TutorialPlayer'
import { Progress } from './Tutorials'

/** The first screen of a space: what the memory is already doing, the jobs of the Console and the tutorials. */
export function Home() {
  const { space, auth } = useSpace()
  useTitle('Início', space.environment === 'sandbox')
  const navigate = useNavigate()
  const progress = useProgress(auth.claims.sub)
  const done = TUTORIALS.filter((t) => progress.done[t.id]).length
  return (
    <>
      <PageHeader screen="home" />
      <div className="grid gap-6">
        <section aria-labelledby="h-tutorials" className={`${card} p-4 sm:p-5`}>
          <div className="flex flex-wrap items-end justify-between gap-3">
            <div className="min-w-0">
              <h2 id="h-tutorials" className={h2}>
                Tutoriais
              </h2>
              <p className={`${small} mt-0.5 max-w-[60ch]`}>Um passo a passo para cada trabalho, com o guia ao lado da tela. O tour de 5 minutos passa pelas telas principais.</p>
            </div>
            <div className="flex flex-wrap gap-2">
              <button type="button" className={btnPrimary} onClick={() => startTutorial(navigate, space.space_id, 'tour')}>
                <Icon name="book" size={16} />
                Fazer o tour de 5 minutos
              </button>
              <Link to={paths.tutorials(space.space_id)} className={btnSecondary}>
                Todos os tutoriais
              </Link>
            </div>
          </div>
          <div className="mt-4">
            <Progress done={done} total={TUTORIALS.length} />
          </div>
        </section>

        <MemoryStatus />

        <section aria-labelledby="h-jobs">
          <h2 id="h-jobs" className={`${h2} mb-3`}>
            Os trabalhos do Console
          </h2>
          <ul className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
            {NAV_GROUPS.filter((g) => !g.admin && g.id !== 'start' && !g.advanced).map((g) => (
              <li key={g.id} className={`${card} p-4`}>
                <p className="font-semibold text-ink">{g.label}</p>
                <p className={`${small} mt-0.5`}>{g.short}</p>
                <ul className="mt-3 grid gap-1">
                  {g.screens.map((id) => (
                    <li key={id}>
                      <Link to={SCREENS[id].to(space.space_id)} className="inline-flex min-h-11 items-center gap-1.5 text-[0.875rem] font-semibold text-ink underline decoration-accent decoration-2 underline-offset-4 hover:decoration-ink lg:min-h-8">
                        <Icon name={SCREENS[id].icon} size={16} className="text-accent" />
                        {SCREENS[id].label}
                      </Link>
                      <span className={`${small} block pl-[1.4rem]`}>{SCREENS[id].short}</span>
                    </li>
                  ))}
                </ul>
              </li>
            ))}
          </ul>
        </section>
      </div>
    </>
  )
}

type Known = 'yes' | 'no' | 'unknown' | 'loading'

interface Fact {
  title: string
  icon: IconName
  state: Known
  to: string
}

/** A fact as a resource answers it: loading, or the answer, or unknown when the read failed. */
function knownOf<T>(r: Resource<T>, yes: (data: T) => boolean): Known {
  if (r.status === 'loading' || r.status === 'idle') return 'loading'
  if (r.status === 'error' || r.data === undefined) return 'unknown'
  return yes(r.data) ? 'yes' : 'no'
}

/**
 * What the memory is already doing in this space (zero touch): agents connected, events arriving,
 * contexts delivered, each read from what the space records and each a tile that opens the screen behind it.
 * Nothing here is a task: the only manual act is a key for an agent, offered once while no agent is connected.
 */
function MemoryStatus() {
  const { space, cell, control, hasRole } = useSpace()
  const sources = useSources()
  const features = useFeatures()
  const live = useMemo(() => sources.list.filter((s) => !s.revoked_at), [sources.list])
  const admin = hasRole('admin')
  const keys = useResource(
    async (signal) => {
      const lists = await Promise.all(live.slice(0, 5).map((s) => control.keys(s.source_id, { signal })))
      return lists.some((l) => l.some((k) => k.status === 'active'))
    },
    [live, control],
    admin && sources.status === 'success' && live.length > 0,
  )
  const arrived = useResource((signal) => cell.eventsArrived(90, { signal }), [cell])
  const reads = useResource((signal) => cell.searchReceipts({ kind: 'read', limit: 1 }, { signal }), [cell])

  const events = knownOf(arrived, (a) => a.arrived)
  const connected: Known =
    events === 'yes'
      ? 'yes'
      : sources.status === 'loading'
        ? 'loading'
        : sources.status === 'error'
          ? 'unknown'
          : live.length === 0
            ? 'no'
            : admin
              ? knownOf(keys, (any) => any)
              : events
  const contexts = knownOf(reads, (p) => p.items.length > 0)

  const facts: Fact[] = [
    { title: 'Agentes conectados', icon: 'key', state: connected, to: paths.agents(space.space_id) },
    { title: 'Eventos chegando', icon: 'plug', state: events, to: paths.systems(space.space_id) },
    { title: 'Contexto entregue', icon: 'receipt', state: contexts, to: paths.receipts(space.space_id) },
  ]

  return (
    <section aria-label="Estado da memória" data-tour="home.status" className="grid gap-3">
      <ul className="grid gap-3 md:grid-cols-3">
        {facts.map((f) => (
          <li key={f.title} className="min-w-0">
            <Link
              to={f.to}
              aria-label={`${f.title}: ${STATE_WORDS[f.state]}`}
              className="group flex h-full min-h-[4.5rem] items-center gap-3 rounded-xl border border-line bg-surface px-4 py-3.5 transition-colors duration-150 hover:border-edge hover:bg-bg"
            >
              <span aria-hidden="true" className="grid h-9 w-9 shrink-0 place-items-center rounded-lg border border-line2 bg-bg text-accent transition-colors group-hover:bg-surface">
                <Icon name={f.icon} size={18} />
              </span>
              <span className="min-w-0 flex-1">
                <span className="block text-[0.875rem] font-semibold leading-5 text-ink">{f.title}</span>
                <KnownState state={f.state} />
              </span>
              <Icon name="chevron" size={16} className="shrink-0 text-ink3 transition-colors group-hover:text-ink md:max-xl:hidden" />
            </Link>
          </li>
        ))}
      </ul>
      {connected === 'no' && (
        <div className={`${card} flex flex-col gap-3 px-4 py-3.5 sm:flex-row sm:items-center sm:justify-between sm:gap-4`}>
          <p className="min-w-0 max-w-[64ch] text-[0.875rem] leading-5 text-ink">
            Para começar, um agente precisa de uma chave: crie a chave e passe ao SDK. É o único passo manual; o resto a Niadra lê do que chega.
          </p>
          <Link to={paths.agents(space.space_id)} className={`${btnSecondary} self-start sm:self-auto`}>
            <Icon name="key" size={16} />
            Criar uma chave
          </Link>
        </div>
      )}
      {features.status === 'ready' && features.on.size === 0 && <p className={small}>Os recursos do núcleo de agentes foram desligados neste espaço por uma decisão em Configuração.</p>}
    </section>
  )
}

const STATE_WORDS: Record<Known, string> = { yes: 'Sim', no: 'Ainda não', unknown: 'Não conferido', loading: 'Conferindo…' }

/** The answer of a fact, in words, with a mark that repeats it: a check when done, a hollow ring while not yet. */
function KnownState({ state }: { state: Known }) {
  if (state === 'loading') return <span className="mt-0.5 block text-[0.8125rem] leading-5 text-ink3">{STATE_WORDS.loading}</span>
  const [icon, tone]: [IconName, string] = state === 'yes' ? ['check', 'text-accent'] : state === 'no' ? ['ring', 'text-ink3'] : ['minus', 'text-ink3']
  return (
    <span className="mt-0.5 flex items-center gap-1.5 text-[0.8125rem] leading-5 text-ink2">
      <Icon name={icon} size={14} className={`shrink-0 ${tone}`} />
      <span className={state === 'yes' ? 'font-semibold text-ink' : undefined}>{STATE_WORDS[state]}</span>
    </span>
  )
}
