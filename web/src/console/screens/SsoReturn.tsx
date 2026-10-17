import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { ssoFailure } from '../lib/labels'
import { dropTab, readFragment, readTab } from '../lib/ssoTab'
import { paths } from '../paths'
import { Centered, useTitle } from '../ui/layout'
import { h1, link, small } from '../ui/styles'
import { Login, type SsoLanding } from './Login'

type Landing = { kind: 'code'; sso: SsoLanding } | { kind: 'error'; reason: string; email: string | null } | { kind: 'lost' }

/** What came back in the fragment, and the value this tab kept at the start. Reading twice gives the same. */
function readLanding(): Landing {
  const fragment = readFragment(window.location.hash)
  const kept = readTab()
  if (fragment && 'error' in fragment) return { kind: 'error', reason: fragment.error, email: kept?.email ?? null }
  if (fragment && 'code' in fragment && kept) return { kind: 'code', sso: { code: fragment.code, verifier: kept.verifier, email: kept.email, from: kept.from } }
  return { kind: 'lost' }
}

/**
 * Where the tenant's identity provider sends the browser back, through the control plane:
 * `#code=` with a one-time sign-in code, or `#error=` with the reason of a refusal. The fragment never reaches
 * a server; it leaves the address bar at once, and the value kept for the tab is removed once read.
 */
export function SsoReturn() {
  useTitle('Entrar')
  const [landing] = useState(readLanding)

  useEffect(() => {
    window.history.replaceState(window.history.state, '', window.location.pathname)
    dropTab()
  }, [])

  if (landing.kind === 'code') return <Login sso={landing.sso} />
  return (
    <Centered>
      <h1 className={h1}>{landing.kind === 'error' ? 'O login pelo SSO não foi concluído' : 'Este login pelo SSO não começou nesta aba'}</h1>
      <p role="alert" className="mt-3 text-[0.875rem] leading-6 text-ink">
        {landing.kind === 'error'
          ? ssoFailure(landing.reason)
          : 'Para a sua segurança, a resposta do provedor só vale na aba que pediu o login, e por quinze minutos. Comece de novo por aqui.'}
      </p>
      {landing.kind === 'error' && landing.reason === 'no_role' && <p className={`${small} mt-2`}>Peça a quem administra o Console para mapear um grupo seu ou definir um papel padrão.</p>}
      <Link to={paths.login} state={landing.kind === 'error' && landing.email ? { email: landing.email } : undefined} className={`${link} mt-5 inline-flex min-h-11 items-center`}>
        Voltar ao login
      </Link>
    </Centered>
  )
}
