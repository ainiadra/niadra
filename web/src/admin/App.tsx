/**
 * The admin panel (admin.niadra.com): the founder's private view of the site, in two tabs.
 *   Contatos  everyone who sent the contact form
 *   Acessos   requests per page and per day: people, AIs by name, search engines, scanners, other robots
 * One account, signed in with e-mail and password; the session lives in an HttpOnly cookie for 12 hours.
 */
import { useCallback, useEffect, useId, useState, type FormEvent, type MouseEvent } from 'react'
import { Logo } from '../components/Mark'
import { btnPrimary, btnQuiet, danger, h1, input, label, small } from '../console/ui/styles'
import { Access } from './Access'
import { AdminError, api, type Session } from './api'
import { Contacts } from './Contacts'
import { Loading } from './ui'

type Tab = 'contatos' | 'acessos'
const TABS: { key: Tab; label: string; path: string }[] = [
  { key: 'contatos', label: 'Contatos', path: '/contatos' },
  { key: 'acessos', label: 'Acessos', path: '/acessos' },
]
const tabOf = (path: string): Tab => (path.startsWith('/acessos') ? 'acessos' : 'contatos')

type State = { kind: 'checking' } | { kind: 'out'; notice?: string } | { kind: 'in'; session: Session }

export function AdminApp() {
  const [state, setState] = useState<State>({ kind: 'checking' })
  const [tab, setTab] = useState<Tab>(() => tabOf(window.location.pathname))

  useEffect(() => {
    api.session().then(
      (session) => setState({ kind: 'in', session }),
      (err: unknown) => setState({ kind: 'out', notice: err instanceof AdminError && err.status !== 401 ? 'Não foi possível conferir a sessão. Entre de novo.' : undefined }),
    )
    const onPop = () => setTab(tabOf(window.location.pathname))
    window.addEventListener('popstate', onPop)
    return () => window.removeEventListener('popstate', onPop)
  }, [])

  const expired = useCallback(() => setState({ kind: 'out', notice: 'Sua sessão terminou. Entre de novo.' }), [])

  // The session ends on the server at expiresAt; the panel follows at the same moment.
  useEffect(() => {
    if (state.kind !== 'in') return
    const left = new Date(state.session.expiresAt).getTime() - Date.now()
    const timer = window.setTimeout(expired, Math.max(0, Math.min(left, 2 ** 31 - 1)))
    return () => window.clearTimeout(timer)
  }, [state, expired])

  useEffect(() => {
    document.title = state.kind === 'in' ? `${tab === 'contatos' ? 'Contatos' : 'Acessos'} · Painel da Niadra` : 'Entrar · Painel da Niadra'
  }, [state.kind, tab])

  if (state.kind === 'checking') {
    return (
      <main className="flex min-h-dvh items-center justify-center px-4">
        <Loading label="Conferindo a sessão…" />
      </main>
    )
  }
  if (state.kind === 'out') return <Login notice={state.notice} onIn={(session) => setState({ kind: 'in', session })} />

  const go = (e: MouseEvent<HTMLAnchorElement>, t: (typeof TABS)[number]) => {
    if (e.metaKey || e.ctrlKey || e.shiftKey || e.button !== 0) return
    e.preventDefault()
    window.history.pushState({}, '', t.path)
    setTab(t.key)
  }
  const signOut = async () => {
    await api.logout().catch(() => {})
    setState({ kind: 'out', notice: 'Você saiu do painel.' })
  }

  return (
    <div className="min-h-dvh">
      <header className="sticky top-0 z-20 border-b border-line bg-surface">
        <div className="mx-auto flex max-w-[1240px] items-center gap-3 px-4 sm:gap-6 sm:px-6 lg:px-8">
          <a href="/contatos" onClick={(e) => go(e, TABS[0]!)} className="flex min-h-11 items-center gap-2 rounded-md" aria-label="Painel da Niadra, contatos">
            <Logo size="text-[1.15rem]" />
            <span className="hidden text-[0.8125rem] font-semibold text-ink2 sm:inline">Painel</span>
          </a>
          <nav aria-label="Seções do painel" className="flex h-14 flex-1 items-stretch gap-1">
            {TABS.map((t) => {
              const on = t.key === tab
              return (
                <a
                  key={t.key}
                  href={t.path}
                  onClick={(e) => go(e, t)}
                  aria-current={on ? 'page' : undefined}
                  className={`-mb-px inline-flex items-center border-b-2 px-3 text-[0.9rem] font-semibold transition-colors ${on ? 'border-ink text-ink' : 'border-transparent text-ink2 hover:text-ink'}`}
                >
                  {t.label}
                </a>
              )
            })}
          </nav>
          <span className={`${small} hidden md:inline`}>{state.session.email}</span>
          <button type="button" className={btnQuiet} onClick={signOut}>
            Sair
          </button>
        </div>
      </header>
      <main className="mx-auto max-w-[1240px] px-4 pb-16 pt-6 sm:px-6 sm:pt-8 lg:px-8">
        {tab === 'contatos' ? <Contacts onExpired={expired} /> : <Access onExpired={expired} />}
      </main>
    </div>
  )
}

const LOGIN_ERRORS: Record<string, (minutes: number) => string> = {
  credentials: () => 'E-mail ou senha incorretos.',
  rate: (m) => `Muitas tentativas deste endereço. Tente de novo em ${m} min.`,
  locked: (m) => `A entrada está bloqueada por excesso de tentativas. Tente de novo em ${m} min.`,
}

function Login({ notice, onIn }: { notice?: string; onIn: (s: Session) => void }) {
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const ids = { email: useId(), password: useId(), error: useId() }

  const submit = async (e: FormEvent) => {
    e.preventDefault()
    setBusy(true)
    setError(null)
    try {
      onIn(await api.login(email, password))
    } catch (err) {
      const a = err instanceof AdminError ? err : null
      const minutes = Math.max(1, Math.ceil((a?.retryAfter ?? 60) / 60))
      setError(a && LOGIN_ERRORS[a.code] ? LOGIN_ERRORS[a.code]!(minutes) : a?.status === 0 ? 'Sem conexão com o servidor. Confira a internet e tente de novo.' : 'O servidor não conseguiu responder. Tente de novo em alguns segundos.')
      setPassword('')
    } finally {
      setBusy(false)
    }
  }

  return (
    <main className="flex min-h-dvh items-start justify-center px-4 py-12 sm:items-center">
      <div className="w-full max-w-[420px]">
        <div className="mb-6">
          <Logo size="text-[1.2rem]" />
        </div>
        <form onSubmit={submit} className="rounded-xl border border-line bg-surface px-5 py-6 sm:px-7 sm:py-8" noValidate>
          <h1 className={h1}>Painel da Niadra</h1>
          <p className={`${small} mt-1.5`}>Contatos do site e acessos por página. Só para o fundador.</p>
          {notice && !error && (
            <p role="status" className="mt-5 rounded-lg border border-line bg-bg px-3 py-2.5 text-[0.875rem] text-ink">
              {notice}
            </p>
          )}
          <div className="mt-6 grid gap-4">
            <div>
              <label htmlFor={ids.email} className={label}>
                E-mail
              </label>
              <input
                id={ids.email}
                type="email"
                className={input}
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                autoComplete="username"
                inputMode="email"
                required
                aria-describedby={error ? ids.error : undefined}
                aria-invalid={error ? true : undefined}
              />
            </div>
            <div>
              <label htmlFor={ids.password} className={label}>
                Senha
              </label>
              <input
                id={ids.password}
                type="password"
                className={input}
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                autoComplete="current-password"
                required
                aria-describedby={error ? ids.error : undefined}
                aria-invalid={error ? true : undefined}
              />
            </div>
          </div>
          {error && (
            <p id={ids.error} role="alert" className={`mt-4 text-[0.875rem] font-semibold ${danger.text}`}>
              {error}
            </p>
          )}
          <button type="submit" className={`${btnPrimary} mt-6 w-full`} disabled={busy || !email.trim() || !password}>
            {busy ? 'Entrando…' : 'Entrar'}
          </button>
        </form>
      </div>
    </main>
  )
}
