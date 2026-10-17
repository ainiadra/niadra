import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { browser } from '../lib/ssoTab'
import { baseRoutes, CODES, CONTROL, mockApi, problem, renderAt, tokenOut, type Req } from './harness'

const AUTHORIZE = 'https://login.empresa-idp.test/authorize?state=abc'
const KEY = 'niadra.sso.tab'

async function typeEmail(email: string) {
  const user = userEvent.setup()
  renderAt('/console/entrar')
  await user.type(await screen.findByLabelText('E-mail corporativo', {}, { timeout: 5000 }), email)
  await user.click(screen.getByRole('button', { name: 'Continuar' }))
  return user
}

/** The browser coming back from the identity provider, in the tab that started: the fragment and the kept value. */
function comeBack(fragment: string, kept: Record<string, unknown> | null = { verifier: 'v'.repeat(43), email: 'rita@empresa.com', from: null }) {
  if (kept) sessionStorage.setItem(KEY, JSON.stringify({ ...kept, at: Date.now() }))
  renderAt(`/console/sso#${fragment}`)
}

afterEach(() => sessionStorage.clear())

describe('sign-in through the tenant SSO', () => {
  it('asks for the e-mail first and offers SSO for a domain that has it', async () => {
    const go = vi.spyOn(browser, 'go').mockImplementation(() => {})
    const api = mockApi(
      baseRoutes({
        [`POST ${CONTROL}/v1/auth/sso/discover`]: { sso: true, protocol: 'oidc', enforced: false },
        [`POST ${CONTROL}/v1/auth/sso/start`]: { authorize_url: AUTHORIZE, expires_at: '2026-09-24T12:10:00Z' },
      }),
    )
    const user = await typeEmail('Rita@Empresa.com')
    expect(api.find('POST', '/v1/auth/sso/discover')[0]!.body).toEqual({ email: 'Rita@Empresa.com' })
    expect(await screen.findByRole('heading', { name: 'Entrar no Console' })).toHaveFocus()
    expect(screen.getByText('Rita@Empresa.com')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Entrar com senha' })).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Entrar com SSO' }))
    await waitFor(() => expect(go).toHaveBeenCalledWith(AUTHORIZE))
    const start = api.find('POST', '/v1/auth/sso/start')[0]!.body
    expect(start.email).toBe('Rita@Empresa.com')
    expect(start.binding).toMatch(/^[A-Za-z0-9_-]{43}$/)
    // The tab keeps the value whose SHA-256 went as the binding; never a token.
    const kept = JSON.parse(sessionStorage.getItem(KEY)!)
    expect(kept.email).toBe('Rita@Empresa.com')
    const digest = new Uint8Array(await crypto.subtle.digest('SHA-256', new TextEncoder().encode(kept.verifier)))
    const b64 = btoa(String.fromCharCode(...digest)).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '')
    expect(b64).toBe(start.binding)
  })

  it('leaves the password to admins when the domain enforces SSO', async () => {
    mockApi(baseRoutes({ [`POST ${CONTROL}/v1/auth/sso/discover`]: { sso: true, protocol: 'saml', enforced: true } }))
    const user = await typeEmail('ana@empresa.com')
    await user.click(await screen.findByRole('button', { name: 'Entrar com senha (administração)' }))
    expect(screen.getByLabelText('Senha')).toHaveFocus()
    expect(screen.getByRole('button', { name: 'Voltar ao login pelo SSO' })).toBeInTheDocument()
  })

  it('goes to the password when the SSO check fails, and says so', async () => {
    mockApi(baseRoutes({ [`POST ${CONTROL}/v1/auth/sso/discover`]: problem(503, 'unavailable') }))
    await typeEmail('ana@empresa.com')
    expect(await screen.findByRole('status')).toHaveTextContent('Não foi possível conferir agora se o seu domínio entra pelo SSO.')
    expect(screen.getByLabelText('Senha')).toBeInTheDocument()
  })

  it('turns a password sign-in that the domain refuses into the SSO choice', async () => {
    mockApi(baseRoutes({ [`POST ${CONTROL}/v1/auth/login`]: problem(401, 'unauthenticated', 'single sign-on required') }))
    const user = await typeEmail('rev@empresa.com')
    await user.type(await screen.findByLabelText('Senha'), 'uma senha qualquer')
    await user.click(screen.getByRole('button', { name: 'Entrar' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Este e-mail entra pelo SSO da empresa.')
    expect(screen.getByRole('button', { name: 'Entrar com SSO' })).toBeInTheDocument()
  })

  it('exchanges the code from the fragment with the kept value, then forgets both', async () => {
    const api = mockApi(baseRoutes({ [`POST ${CONTROL}/v1/auth/sso/exchange`]: tokenOut() }))
    comeBack('code=ONE-TIME-SIGN-IN-CODE-0123456789')
    expect(await screen.findByRole('heading', { name: 'Escolha o espaço' })).toBeInTheDocument()
    expect(api.find('POST', '/v1/auth/sso/exchange')).toHaveLength(1)
    expect(api.find('POST', '/v1/auth/sso/exchange')[0]!.body).toEqual({ code: 'ONE-TIME-SIGN-IN-CODE-0123456789', verifier: 'v'.repeat(43) })
    // Nothing of the sign-in stays: not the code in the address, not the value in the tab.
    expect(window.location.href).not.toContain('ONE-TIME')
    expect(sessionStorage.getItem(KEY)).toBeNull()
  })

  it('asks for the Niadra second factor after the provider when the tenant requires it', async () => {
    let attempt = 0
    const api = mockApi(
      baseRoutes({
        [`POST ${CONTROL}/v1/auth/sso/exchange`]: (r: Req) =>
          ++attempt === 1 ? problem(401, 'unauthenticated', 'second factor enrollment required') : r.body.totp ? { ...tokenOut(), recovery_codes: CODES } : problem(401, 'unauthenticated', 'invalid second factor'),
        [`POST ${CONTROL}/v1/auth/sso/second-factor`]: { secret: 'JBSWY3DPEHPK3PXP', totp_uri: 'otpauth://totp/Niadra:rita@empresa.com?secret=JBSWY3DPEHPK3PXP&issuer=Niadra' },
      }),
    )
    const user = userEvent.setup()
    comeBack('code=SIGN-IN-CODE-THAT-NEEDS-A-SECOND-FACTOR')
    expect(await screen.findByRole('heading', { name: 'Conecte o aplicativo autenticador' })).toBeInTheDocument()
    expect(api.find('POST', '/v1/auth/sso/second-factor')[0]!.body).toEqual({ code: 'SIGN-IN-CODE-THAT-NEEDS-A-SECOND-FACTOR', verifier: 'v'.repeat(43) })
    await user.click(screen.getByRole('button', { name: 'Já adicionei, continuar' }))
    await user.keyboard('123456')
    await user.click(screen.getByRole('button', { name: 'Ativar e entrar' }))
    expect(await screen.findByRole('heading', { name: 'Guarde os códigos de recuperação' })).toBeInTheDocument()
    expect(api.find('POST', '/v1/auth/sso/exchange').at(-1)!.body).toMatchObject({ totp: '123456', verifier: 'v'.repeat(43) })
  })

  it('says why the provider sign-in was refused', async () => {
    mockApi(baseRoutes())
    comeBack('error=no_role')
    expect(await screen.findByRole('heading', { name: 'O login pelo SSO não foi concluído' })).toBeInTheDocument()
    expect(screen.getByRole('alert')).toHaveTextContent('Nenhum grupo seu no provedor dá um papel no Console')
    expect(screen.getByRole('link', { name: 'Voltar ao login' })).toBeInTheDocument()
  })

  it('refuses an answer that comes back to a tab that did not start the sign-in', async () => {
    const api = mockApi(baseRoutes())
    comeBack('code=A-CODE-FROM-ANOTHER-TAB-0123456', null)
    expect(await screen.findByRole('heading', { name: 'Este login pelo SSO não começou nesta aba' })).toBeInTheDocument()
    expect(api.find('POST', '/v1/auth/sso/exchange')).toHaveLength(0)
  })

  it('starts over at the e-mail when the code expired', async () => {
    mockApi(baseRoutes({ [`POST ${CONTROL}/v1/auth/sso/exchange`]: problem(401, 'unauthenticated', 'the sign-in code is invalid or expired') }))
    comeBack('code=AN-EXPIRED-SIGN-IN-CODE-012345')
    expect(await screen.findByRole('alert')).toHaveTextContent('O login pelo SSO expirou ou já foi usado. Entre de novo.')
    expect(within(document.body).getByLabelText('E-mail corporativo')).toHaveValue('rita@empresa.com')
  })
})
