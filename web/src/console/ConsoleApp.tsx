import { useEffect, type ReactNode } from 'react'
import { Link, Navigate, Route, Routes, useLocation } from 'react-router-dom'
import { ConfigError, loadConfig, CONFIG_PATH } from './config'
import { useResource } from './lib/useResource'
import { paths } from './paths'
import { Account } from './screens/Account'
import { Invitation } from './screens/Invitation'
import { Login } from './screens/Login'
import { SsoReturn } from './screens/SsoReturn'
import { SpacePicker } from './screens/SpacePicker'
import { SpaceLayout } from './screens/SpaceLayout'
import { ConsoleProvider, useConsole } from './session'
import { Centered } from './ui/layout'
import { Loading } from './ui/States'
import { body, h1, link, mono, small } from './ui/styles'

export default function ConsoleApp() {
  const cfg = useResource((signal) => loadConfig(signal), [])

  useEffect(() => {
    // The Console speaks the tenant's language, whatever page of the site came before.
    const prev = document.documentElement.lang
    document.documentElement.lang = 'pt-BR'
    return () => {
      document.documentElement.lang = prev
    }
  }, [])

  if (cfg.status === 'error' || (cfg.status === 'success' && !cfg.data)) {
    const why = cfg.error instanceof ConfigError ? cfg.error.message : 'erro desconhecido'
    return (
      <Centered>
        <h1 className={h1}>O Console está sem configuração.</h1>
        <p className={`${small} mt-2`}>
          O arquivo <span className={`${mono} text-ink`}>{CONFIG_PATH}</span> não pôde ser lido: {why}. Quem opera o site escreve esse arquivo no deploy, com o
          endereço do plano de controle e o de cada região.
        </p>
        <button type="button" className={`${link} mt-4 min-h-11`} onClick={cfg.reload}>
          Tentar de novo
        </button>
      </Centered>
    )
  }
  if (cfg.status !== 'success' || !cfg.data) return <Loading label="Abrindo o Console…" className="min-h-dvh justify-center" />

  return (
    <ConsoleProvider config={cfg.data}>
      <div className={`${body} min-h-dvh bg-bg text-ink`}>
        <Routes>
          <Route index element={<Navigate to={paths.login} replace />} />
          <Route path="entrar" element={<Login />} />
          <Route path="sso" element={<SsoReturn />} />
          <Route path="convite" element={<Invitation />} />
          <Route
            path="espacos"
            element={
              <RequireAuth>
                <SpacePicker />
              </RequireAuth>
            }
          />
          <Route
            path="conta"
            element={
              <RequireAuth>
                <Account />
              </RequireAuth>
            }
          />
          <Route
            path="e/:spaceId/*"
            element={
              <RequireAuth>
                <SpaceLayout />
              </RequireAuth>
            }
          />
          <Route path="*" element={<NotFound />} />
        </Routes>
      </div>
    </ConsoleProvider>
  )
}

function RequireAuth({ children }: { children: ReactNode }) {
  const { auth } = useConsole()
  const loc = useLocation()
  // Keep the query too: a shared link to filtered receipts must survive the login.
  if (!auth) return <Navigate to={paths.login} replace state={{ from: loc.pathname + loc.search }} />
  return <>{children}</>
}

function NotFound() {
  const { auth } = useConsole()
  return (
    <Centered>
      <h1 className={h1}>Esta tela não existe no Console.</h1>
      <p className={`${small} mt-2`}>O endereço pode ter mudado. Volte para a escolha de espaço.</p>
      <Link className={`${link} mt-4 inline-flex min-h-11 items-center`} to={auth ? paths.spaces : paths.login}>
        {auth ? 'Escolher um espaço' : 'Entrar no Console'}
      </Link>
    </Centered>
  )
}
