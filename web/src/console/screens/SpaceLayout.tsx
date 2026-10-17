import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { Link, Navigate, NavLink, Route, Routes, useLocation, useParams } from 'react-router-dom'
import { Logo } from '../../components/Mark'
import type { Role } from '../api/types'
import { FeaturesCtx, loadFeatures, useFeatures, type Feature, type Features } from '../lib/features'
import { Retrying, useResource } from '../lib/useResource'
import { role } from '../lib/labels'
import { NAV_GROUPS, SCREENS, type NavGroup, type ScreenId } from '../lib/screens'
import { paths } from '../paths'
import { RegionNotConfigured, useConsole, useSpace } from '../session'
import { Chip, SourcesCtx, type SourcesDirectory } from '../ui/bits'
import { Icon } from '../ui/Icon'
import { Centered } from '../ui/layout'
import { Empty, ErrorState, Loading } from '../ui/States'
import { btnPrimary, btnSecondary, h1, link, mono, pad, small } from '../ui/styles'
import { loadSpaces, spaceLabel } from './SpacePicker'
import { Coordination } from './Coordination'
import { Customers } from './Customers'
import { ProfileScreen } from './profile/ProfileScreen'
import { RelationMap } from './RelationMap'
import { Identity } from './Identity'
import { Patterns } from './Patterns'
import { AgentMemory } from './AgentMemory'
import { Receipts } from './Receipts'
import { ReceiptDetail } from './ReceiptDetail'
import { TurnDetail, Turns } from './Turns'
import { Usage } from './Usage'
import { Measure } from './Measure'
import { Types } from './Types'
import { Review } from './Review'
import { Gaps } from './Gaps'
import { Data } from './Data'
import { Agents } from './Agents'
import { AgentDetail } from './AgentDetail'
import { Systems } from './Systems'
import { Alerts } from './Alerts'
import { Configuration } from './Configuration'
import { People } from './People'
import { SsoSettings } from './SsoSettings'
import { Home } from './Home'
import { Tutorials } from './Tutorials'
import { Dialog } from '../ui/Dialog'
import { HelpDialog } from '../ui/HelpDialog'
import { TutorialPlayer, startTutorial } from '../ui/TutorialPlayer'
import { setWelcomeSeen, syncPreferences, welcomeSeen } from '../lib/tutorialProgress'
import { useNavigate } from 'react-router-dom'

class SpaceNotListed extends Error {}

/** Enters the space in the URL (token for that space) when the person arrives by link or switches spaces. */
export function SpaceLayout() {
  const { spaceId = '' } = useParams()
  const { control, space, enterSpace } = useConsole()
  const ready = space?.space_id === spaceId

  const entering = useResource(
    async (signal) => {
      const found = (await loadSpaces(control, signal)).spaces.find((s) => s.space_id === spaceId)
      if (!found) throw new SpaceNotListed()
      await enterSpace(found)
    },
    [spaceId],
    !ready,
  )

  if (ready) return <SpaceReady />
  if (entering.status === 'error') {
    const err = entering.error
    return (
      <Centered>
        <h1 className={h1}>{err instanceof Retrying ? 'Abrindo o espaço' : 'Não foi possível abrir este espaço.'}</h1>
        {err instanceof SpaceNotListed ? (
          <p className={`${small} mt-2`}>Ele não está entre os espaços liberados para você.</p>
        ) : err instanceof RegionNotConfigured ? (
          <p className={`${small} mt-2`}>A região {err.region} não tem endereço de API no config.json do Console.</p>
        ) : (
          <div className="mt-4">
            <ErrorState error={err} onRetry={entering.reload} />
          </div>
        )}
        <Link to={paths.spaces} className={`${link} mt-4 inline-flex min-h-11 items-center`}>
          Escolher outro espaço
        </Link>
      </Centered>
    )
  }
  return <Loading label="Abrindo o espaço…" className="min-h-dvh justify-center" />
}

/** Whether the rail shows a screen: the person's roles and the space's features allow it. */
function shown(id: ScreenId, features: Features, hasRole: (...roles: Role[]) => boolean): boolean {
  const s = SCREENS[id]
  return (!s.roles || hasRole(...s.roles)) && (!s.feature || features.on.has(s.feature))
}

function SpaceReady() {
  const { space, control, auth } = useSpace()
  const navigate = useNavigate()
  const [helpOpen, setHelpOpen] = useState(false)
  // The welcome, once per person: it offers the tour and then stays out of the way. It waits for what the
  // control plane keeps of the person, so someone who saw it in another browser does not see it again.
  const [welcome, setWelcome] = useState(false)
  useEffect(() => {
    const abort = new AbortController()
    void syncPreferences(auth.claims.sub, control, abort.signal).then(() => {
      if (!abort.signal.aborted) setWelcome(!welcomeSeen(auth.claims.sub))
    })
    return () => abort.abort()
  }, [auth.claims.sub, control])
  const closeWelcome = () => {
    setWelcomeSeen(auth.claims.sub, control)
    setWelcome(false)
  }
  const sources = useResource((signal) => control.sources(space.space_id, { signal }), [space.space_id, control])
  const loaded = useResource((signal) => loadFeatures(control, space.space_id, signal), [space.space_id, control])
  const features = useMemo<Features>(
    () => (loaded.data ? { status: 'ready', on: loaded.data } : { status: loaded.status === 'error' ? 'ready' : 'loading', on: new Set() }),
    [loaded.data, loaded.status],
  )
  const directory = useMemo<SourcesDirectory>(() => {
    const list = sources.data ?? []
    return {
      status: sources.status === 'idle' ? 'loading' : sources.status,
      list,
      byId: new Map(list.map((s) => [s.source_id, s])),
      error: sources.error,
      reload: sources.reload,
    }
  }, [sources.data, sources.status, sources.error, sources.reload])

  const [menuOpen, setMenuOpen] = useState(false)
  const loc = useLocation()
  const menuBtn = useRef<HTMLButtonElement>(null)

  useEffect(() => {
    setMenuOpen(false)
    window.scrollTo(0, 0)
  }, [loc.pathname])

  useEffect(() => {
    if (!menuOpen) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        setMenuOpen(false)
        menuBtn.current?.focus()
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [menuOpen])

  const sandbox = space.environment === 'sandbox'
  const home = paths.home(space.space_id)

  return (
    <FeaturesCtx.Provider value={features}>
    <SourcesCtx.Provider value={directory}>
      <a
        href="#console-main"
        className="sr-only focus:not-sr-only focus:fixed focus:left-3 focus:top-3 focus:z-[90] focus:rounded-lg focus:border focus:border-edge focus:bg-surface focus:px-4 focus:py-3"
      >
        Pular para o conteúdo
      </a>
      <div className="min-h-dvh lg:grid lg:grid-cols-[264px_minmax(0,1fr)]">
        <aside className="invert-block sticky top-0 hidden h-dvh flex-col overflow-y-auto border-r border-line lg:flex" aria-label="Console">
          <Rail onHelp={() => setHelpOpen(true)} />
        </aside>

        <div className="invert-block sticky top-0 z-40 flex h-14 items-center justify-between gap-3 border-b border-line px-4 lg:hidden">
          <Link to={home} aria-label="Console da Niadra" className="inline-flex h-11 items-center">
            <Logo onDark size="text-[1.1rem]" />
          </Link>
          <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={() => setHelpOpen(true)}
            aria-label="Ajuda desta tela"
            className="inline-flex h-11 w-11 items-center justify-center rounded-lg border border-edge text-ink"
          >
            <Icon name="help" size={18} />
          </button>
          <button
            ref={menuBtn}
            type="button"
            aria-expanded={menuOpen}
            aria-controls="console-menu"
            onClick={() => setMenuOpen((v) => !v)}
            className="inline-flex h-11 items-center gap-2 rounded-lg border border-edge px-3 text-[0.875rem] font-semibold text-ink"
          >
            <Icon name={menuOpen ? 'close' : 'menu'} size={16} />
            {menuOpen ? 'Fechar' : 'Menu'}
          </button>
          </div>
        </div>
        {menuOpen && (
          <div id="console-menu" className="invert-block fixed inset-x-0 bottom-0 top-14 z-40 flex flex-col overflow-y-auto lg:hidden">
            <Rail onHelp={() => setHelpOpen(true)} />
          </div>
        )}

        <div className="min-w-0">
          {sandbox && (
            <div role="note" className="invert-block border-b border-line">
              <p className={`${pad} flex flex-wrap items-baseline gap-x-2 gap-y-0.5 py-2.5 text-[0.875rem]`}>
                <strong className="font-semibold text-brand">Sandbox: apenas dados sintéticos.</strong>
                <span className="text-ink2">Chaves, comprovantes e medições deste espaço não valem para produção.</span>
              </p>
            </div>
          )}
          <main id="console-main" tabIndex={-1} className={`${pad} mx-auto w-full max-w-[1320px] py-6 outline-none lg:py-8`}>
            <Routes>
              <Route index element={<Navigate to="inicio" replace />} />
              <Route path="inicio" element={<Home />} />
              <Route path="tutoriais" element={<Tutorials />} />
              <Route path="clientes" element={<Customers />} />
              <Route path="clientes/:profileId" element={<ProfileScreen />} />
              <Route path="relacoes" element={<RelationMap />} />
              <Route path="identidade" element={<Identity />} />
              <Route path="padroes" element={<Patterns />} />
              <Route path="memoria-dos-agentes" element={<AgentMemory />} />
              <Route path="recibos" element={<Receipts />} />
              <Route path="recibos/:receiptId" element={<ReceiptDetail />} />
              <Route path="turnos" element={<Gate feature="turns"><Turns /></Gate>} />
              <Route path="turnos/:turnId" element={<Gate feature="turns"><TurnDetail /></Gate>} />
              <Route path="coordenacao" element={<Gate feature="coordination"><Coordination /></Gate>} />
              <Route path="uso" element={<Usage />} />
              <Route path="medicao" element={<Gate feature="measurement"><Measure /></Gate>} />
              <Route path="revisao" element={<Review />} />
              <Route path="lacunas" element={<Gaps />} />
              <Route path="dados" element={<Data />} />
              <Route path="agentes" element={<Agents />} />
              <Route path="agentes/:sourceId" element={<AgentDetail />} />
              <Route path="sistemas" element={<Systems />} />
              <Route path="tipos" element={<Gate feature="state"><Types /></Gate>} />
              <Route path="avisos" element={<Alerts />} />
              <Route path="configuracao" element={<Configuration />} />
              <Route path="pessoas" element={<People />} />
              <Route path="sso" element={<SsoSettings />} />
              <Route
                path="*"
                element={
                  <div>
                    <h1 className={h1}>Esta tela não existe no Console.</h1>
                    <Link to={home} className={`${link} mt-3 inline-flex min-h-11 items-center`}>
                      Ir para o Início
                    </Link>
                  </div>
                }
              />
            </Routes>
          </main>
        </div>
      </div>
      <TutorialPlayer />
      <HelpDialog open={helpOpen} onClose={() => setHelpOpen(false)} />
      <Dialog open={welcome} onClose={closeWelcome} title="Bem-vindo ao Console da Niadra">
        <p className="text-[0.875rem] leading-5 text-ink">
          O Console é onde a sua empresa governa a memória dos agentes de IA: o que cada agente sabe de cada cliente, quem leu o quê, e o que cada agente fez com o que recebeu. Não é uma
          tela de atendimento: ninguém responde a um cliente por aqui.
        </p>
        <p className={`${small} mt-3`}>Um tour de 5 minutos passa pelas telas principais. Depois, o botão de ajuda no menu reabre o guia da tela em que você estiver.</p>
        <div className="mt-5 flex flex-wrap justify-end gap-2">
          <button type="button" className={btnSecondary} onClick={closeWelcome}>
            Explorar sozinho
          </button>
          <button
            type="button"
            className={btnPrimary}
            onClick={() => {
              closeWelcome()
              startTutorial(navigate, space.space_id, 'tour')
            }}
          >
            <Icon name="book" size={16} />
            Fazer o tour de 5 minutos
          </button>
        </div>
      </Dialog>
    </SourcesCtx.Provider>
    </FeaturesCtx.Provider>
  )
}

const railItem =
  'flex min-h-11 w-full items-center gap-2.5 rounded-lg px-2.5 text-[0.875rem] font-semibold text-ink2 transition-colors hover:bg-surface hover:text-ink lg:min-h-9'

const ADVANCED_KEY = 'niadra.console.rail.advanced'

/** Whether the person left the advanced group open; the browser may refuse storage, and then it starts closed. */
function readAdvanced(): boolean {
  try {
    return localStorage.getItem(ADVANCED_KEY) === 'open'
  } catch {
    return false
  }
}

/**
 * The left rail, as in Agno's dashboard: who and where on top, the jobs in the middle, the person at the
 * bottom. The groups follow the job to be done (lib/screens.ts), each with its one-line purpose; the technical
 * screens sit under "Avançado", closed until the person opens it or lands on one of them.
 */
function Rail({ onHelp }: { onHelp: () => void }) {
  const { auth, me, signOut, control } = useConsole()
  const { space, hasRole } = useSpace()
  const features = useFeatures()
  const loc = useLocation()
  const tenant = useResource((signal) => control.tenant(auth!.claims.tenant, { signal }), [auth?.claims.tenant, control], Boolean(auth))
  const [advancedOpen, setAdvancedOpen] = useState(readAdvanced)
  const groups = NAV_GROUPS.filter((g) => !g.admin || hasRole('admin'))
    .map((g) => ({ ...g, screens: g.screens.filter((id) => shown(id, features, hasRole)) }))
    .filter((g) => g.screens.length > 0)
  const advanced = groups.find((g) => g.advanced)
  const onAdvanced = Boolean(advanced?.screens.some((id) => loc.pathname.startsWith(SCREENS[id].to(space.space_id))))
  const toggleAdvanced = () => {
    const next = !(advancedOpen || onAdvanced)
    setAdvancedOpen(next)
    try {
      localStorage.setItem(ADVANCED_KEY, next ? 'open' : 'closed')
    } catch {
      // The rail still opens; it just will not remember.
    }
  }

  return (
    <div className="flex min-h-full flex-col gap-5 px-3 py-4">
      <Link to={paths.home(space.space_id)} className="hidden h-11 items-center gap-2 px-2 lg:inline-flex" aria-label="Console da Niadra">
        <Logo onDark size="text-[1.15rem]" />
        <span className="text-[0.8125rem] font-semibold text-ink2">Console</span>
      </Link>

      <div className="rounded-lg border border-line bg-surface px-3 py-3">
        {tenant.data && <p className="text-[0.75rem] text-ink2">{tenant.data.name}</p>}
        <p className="mt-0.5 font-semibold text-ink">{space.project_name}</p>
        <p className={`${mono} mt-0.5 text-ink2`}>
          {space.slug} · {space.region}
        </p>
        <div className="mt-2 flex items-center justify-between gap-2">
          {space.environment === 'sandbox' ? <Chip tone="dark">{spaceLabel(space)}</Chip> : <Chip>{spaceLabel(space)}</Chip>}
          <Link to={paths.spaces} className="inline-flex min-h-11 items-center gap-1.5 text-[0.8125rem] font-semibold text-ink underline decoration-accent decoration-2 underline-offset-4 lg:min-h-8">
            <Icon name="swap" size={15} />
            Trocar
          </Link>
        </div>
      </div>

      <nav aria-label="Seções do Console" className="grid gap-4">
        {groups.map((g) => (
          <RailGroup key={g.id} group={g} open={!g.advanced || advancedOpen || onAdvanced} onToggle={g.advanced ? toggleAdvanced : undefined} />
        ))}
      </nav>

      <div className="mt-auto border-t border-line pt-4">
        <p className={`${mono} break-all px-2 text-ink`}>{me?.email ?? auth?.email}</p>
        <p className="mt-1 px-2 text-[0.75rem] text-ink2">{auth?.claims.roles.map(role).join(', ')}</p>
        <div className="mt-3 grid gap-0.5">
          <button type="button" onClick={onHelp} className={railItem}>
            <Icon name="help" />
            Ajuda desta tela
          </button>
          <Link to={paths.account} state={{ from: loc.pathname + loc.search }} className={railItem}>
            <Icon name="person" />
            Sua conta
          </Link>
          <button type="button" onClick={() => signOut()} className={railItem}>
            <Icon name="exit" />
            Sair
          </button>
        </div>
      </div>
    </div>
  )
}

/** One group of the rail: the name, the job in one line and the screens. The advanced group opens and closes. */
function RailGroup({ group, open, onToggle }: { group: NavGroup; open: boolean; onToggle?: () => void }) {
  const { space } = useSpace()
  const listId = `rail-${group.id}`
  const head = (
    <>
      <span className="block text-[0.75rem] font-semibold text-ink2">{group.label}</span>
      <span className="block text-[0.6875rem] leading-4 text-ink3">{group.short}</span>
    </>
  )
  return (
    <div>
      {onToggle ? (
        <button
          type="button"
          aria-expanded={open}
          aria-controls={listId}
          onClick={onToggle}
          className="flex min-h-11 w-full items-center justify-between gap-2 rounded-lg px-2.5 py-1 text-left transition-colors hover:bg-surface lg:min-h-9"
        >
          <span>{head}</span>
          <Icon name="chevron" size={16} className={`shrink-0 text-ink2 transition-transform ${open ? 'rotate-90' : ''}`} />
        </button>
      ) : (
        <p className="px-2.5 pb-1">{head}</p>
      )}
      {open && (
        <ul id={listId} className="grid gap-0.5">
          {group.screens.map((id) => {
            const s = SCREENS[id]
            return (
              <li key={id}>
                <NavLink
                  to={s.to(space.space_id)}
                  end={false}
                  className={({ isActive }) =>
                    `flex min-h-11 items-center gap-2.5 rounded-lg px-2.5 text-[0.875rem] font-semibold transition-colors lg:min-h-9 ${
                      isActive ? 'bg-surface text-ink' : 'text-ink2 hover:bg-surface hover:text-ink'
                    }`
                  }
                >
                  {({ isActive }) => (
                    <>
                      <Icon name={s.icon} className={isActive ? 'text-accent' : ''} />
                      {s.label}
                    </>
                  )}
                </NavLink>
              </li>
            )
          })}
        </ul>
      )}
    </div>
  )
}

/** What each agent feature is called when it is off, for the screen reached by address. */
const FEATURE_OFF: Record<Feature, string> = {
  turns: 'Os registros de turno estão desligados neste espaço.',
  coordination: 'A coordenação está desligada neste espaço.',
  measurement: 'A medição de desfechos está desligada neste espaço.',
  state: 'Os tipos de objeto estão desligados neste espaço.',
  agent_state: 'A memória de trabalho dos agentes está desligada neste espaço.',
  signals: 'Os sinais estão desligados neste espaço.',
  claims: 'O contrato de afirmação está desligado neste espaço.',
}

/** A screen of a feature the space left off, reached by address. */
function Gate({ feature, children }: { feature: Feature; children: ReactNode }) {
  const features = useFeatures()
  const { space } = useSpace()
  if (features.status === 'loading') return <Loading label="Conferindo os recursos do espaço…" />
  if (!features.on.has(feature)) {
    return (
      <Empty
        title={FEATURE_OFF[feature]}
        action={
          <Link to={paths.config(space.space_id)} className={btnSecondary}>
            Abrir Configuração
          </Link>
        }
      >
        Os recursos do núcleo de agentes vêm ligados. Este foi desligado por uma decisão registrada em Configuração, no documento de recursos (
        <span className="font-mono text-[0.8125rem] text-ink">{feature}</span>); religar é uma mudança com aprovação, como a que desligou.
      </Empty>
    )
  }
  return <>{children}</>
}
