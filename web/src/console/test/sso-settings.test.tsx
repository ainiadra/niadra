import { screen, within } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import type { SsoConnectionOut, SsoOut } from '../api/types'
import { at, baseRoutes, choose, CONTROL, mockApi, problem, signInAt, type Req } from './harness'

const SP = {
  oidc_redirect_uri: 'https://control.test/v1/auth/sso/oidc/callback',
  saml_entity_id: 'urn:niadra:sso:0192a000-0000-7000-8000-000000000001',
  saml_acs_url: 'https://control.test/v1/auth/sso/saml/0192a000-0000-7000-8000-000000000001/acs',
  saml_metadata_url: 'https://control.test/v1/auth/sso/saml/0192a000-0000-7000-8000-000000000001/metadata',
}
const EMPTY: SsoOut = { connection: null, service_provider: SP }
const PEM = '-----BEGIN CERTIFICATE-----\nMIIBszCCAVmgAwIBAgIUB0...\n-----END CERTIFICATE-----'
const SAML: SsoConnectionOut = {
  connection_id: 'c1',
  protocol: 'saml',
  domains: ['empresa.com', 'empresa.com.br'],
  enabled: true,
  enforced: false,
  default_role: 'review',
  role_mappings: [{ group: 'niadra-admins', role: 'admin' }],
  groups_attribute: 'groups',
  require_second_factor: false,
  oidc: null,
  saml: {
    idp_entity_id: 'https://idp.empresa.com/metadata',
    idp_sso_url: 'https://idp.empresa.com/sso',
    certificate: { subject: 'CN=idp.empresa.com', not_after: '2030-01-01T00:00:00Z', sha256_fingerprint: 'AB:CD', pem: PEM },
  },
  created_at: '2026-09-20T12:00:00Z',
  updated_at: '2026-09-21T12:00:00Z',
  updated_by: '0192a000-0000-7000-8000-0000000000u1',
}

describe('Single sign-on settings', () => {
  it('shows what to paste into the provider for the chosen protocol', async () => {
    mockApi(baseRoutes({ [`GET ${CONTROL}/v1/sso`]: EMPTY }))
    const { user } = await signInAt(at('sso'))
    expect(await screen.findByText(SP.oidc_redirect_uri)).toBeInTheDocument()
    expect(screen.queryByText(SP.saml_acs_url)).not.toBeInTheDocument()
    await user.click(screen.getByLabelText('SAML 2.0'))
    expect(screen.getByText(SP.saml_entity_id)).toBeInTheDocument()
    expect(screen.getByText(SP.saml_acs_url)).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Copiar: URL do ACS (HTTP-POST)' }))
    expect(await navigator.clipboard.readText()).toBe(SP.saml_acs_url)
    // A new connection has nothing to test or remove yet.
    expect(screen.queryByRole('button', { name: 'Testar o login' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Remover o SSO' })).not.toBeInTheDocument()
  })

  it('creates an OIDC connection saved off, with groups that give roles', async () => {
    const api = mockApi(
      baseRoutes({
        [`GET ${CONTROL}/v1/sso`]: EMPTY,
        [`PUT ${CONTROL}/v1/sso`]: (r: Req) => ({
          connection: { ...SAML, protocol: 'oidc', saml: null, enabled: r.body.enabled, domains: r.body.domains, oidc: { issuer: r.body.oidc.issuer, client_id: 'app', scopes: ['openid', 'email', 'profile', 'groups'], client_secret_set: true } },
          service_provider: SP,
        }),
      }),
    )
    const { user } = await signInAt(at('sso'))
    await user.click(await screen.findByRole('button', { name: 'Criar a conexão' }))
    expect(screen.getByRole('button', { name: 'Criar a conexão' })).toBeDisabled()
    await user.type(screen.getByLabelText('Motivo'), 'SSO da empresa')
    await user.click(screen.getByRole('button', { name: 'Criar a conexão' }))
    expect(await screen.findByText('Revise os campos marcados antes de salvar.')).toBeInTheDocument()
    expect(screen.getByText('Informe ao menos um domínio de e-mail da empresa.')).toBeInTheDocument()
    expect(api.find('PUT', '/v1/sso')).toHaveLength(0)

    await user.type(screen.getByLabelText('Emissor (issuer)'), 'https://login.microsoftonline.com/abc/v2.0')
    await user.type(screen.getByLabelText('Client ID'), 'app')
    await user.type(screen.getByLabelText('Client secret'), 'um-segredo-do-provedor')
    await user.type(screen.getByLabelText('Escopos a mais (opcional)'), 'groups')
    await user.type(screen.getByLabelText('Domínios de e-mail'), 'Empresa.com{enter}@empresa.com.br')
    await user.click(screen.getByRole('button', { name: 'Adicionar grupo' }))
    await user.type(screen.getByLabelText('Grupo 1'), 'a1b2-grupo-admins')
    await choose(user, screen.getByLabelText('Papel do grupo 1'), 'admin')
    await choose(user, screen.getByLabelText('Papel padrão'), 'review')
    await user.click(screen.getByLabelText('Pedir também o código da Niadra'))
    await user.click(screen.getByRole('button', { name: 'Criar a conexão' }))

    expect(await screen.findByRole('status')).toHaveTextContent('Está desligado: teste o login e depois ligue.')
    expect(api.find('PUT', '/v1/sso')[0]!.body).toEqual({
      protocol: 'oidc',
      domains: ['empresa.com', 'empresa.com.br'],
      enabled: false,
      enforced: false,
      default_role: 'review',
      role_mappings: [{ group: 'a1b2-grupo-admins', role: 'admin' }],
      groups_attribute: 'groups',
      require_second_factor: true,
      oidc: { issuer: 'https://login.microsoftonline.com/abc/v2.0', client_id: 'app', client_secret: 'um-segredo-do-provedor', scopes: ['groups'] },
      saml: null,
      reason: 'SSO da empresa',
    })
  })

  it('keeps the stored SAML certificate and says why a save was refused', async () => {
    let attempt = 0
    const api = mockApi(
      baseRoutes({
        [`GET ${CONTROL}/v1/sso`]: { connection: SAML, service_provider: SP },
        [`PUT ${CONTROL}/v1/sso`]: () =>
          ++attempt === 1 ? problem(409, 'conflict', 'another tenant already uses one of the domains') : { connection: { ...SAML, enforced: true, updated_at: '2026-09-24T12:00:00Z' }, service_provider: SP },
      }),
    )
    const { user } = await signInAt(at('sso'))
    expect(await screen.findByText('Ligado · SAML 2.0')).toBeInTheDocument()
    expect(screen.getByLabelText('Certificado de assinatura')).toHaveValue(PEM)
    expect(screen.getByLabelText('Domínios de e-mail')).toHaveValue('empresa.com\nempresa.com.br')
    await user.click(screen.getByLabelText('Só pelo SSO'))
    await user.type(screen.getByLabelText('Motivo'), 'obrigar o SSO')
    await user.click(screen.getByRole('button', { name: 'Salvar a conexão' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Um dos domínios já é de outra empresa na Niadra.')
    await user.click(screen.getByRole('button', { name: 'Salvar a conexão' }))
    expect(api.find('PUT', '/v1/sso').at(-1)!.body).toMatchObject({
      protocol: 'saml',
      enforced: true,
      saml: { idp_entity_id: 'https://idp.empresa.com/metadata', idp_sso_url: 'https://idp.empresa.com/sso', idp_certificate: PEM },
      oidc: null,
    })
  })

  it('tests the sign-in in another tab and shows what the provider sent', async () => {
    let polls = 0
    mockApi(
      baseRoutes({
        [`GET ${CONTROL}/v1/sso`]: { connection: SAML, service_provider: SP },
        [`POST ${CONTROL}/v1/sso/test`]: { test_id: 't1', authorize_url: 'https://idp.empresa.com/sso?SAMLRequest=x', expires_at: '2099-01-01T00:00:00Z' },
        [`GET ${CONTROL}/v1/sso/tests/t1`]: () =>
          ++polls === 1
            ? { test_id: 't1', status: 'pending', created_at: '2026-09-24T12:00:00Z', completed_at: null, ok: null, failure: null, detail: null, email: null, groups: [], roles: [], person_exists: null }
            : { test_id: 't1', status: 'completed', created_at: '2026-09-24T12:00:00Z', completed_at: '2026-09-24T12:01:00Z', ok: true, failure: null, detail: null, email: 'rita@empresa.com', groups: ['niadra-admins', 'x'], roles: ['admin'], person_exists: false },
      }),
    )
    const { user } = await signInAt(at('sso'))
    await user.click(await screen.findByRole('button', { name: 'Testar o login' }))
    const link = await screen.findByRole('link', { name: /Abrir o provedor em outra aba/ })
    expect(link).toHaveAttribute('href', 'https://idp.empresa.com/sso?SAMLRequest=x')
    expect(link).toHaveAttribute('target', '_blank')
    expect(link).toHaveAttribute('rel', 'noopener noreferrer')
    expect(await screen.findByText(/Esperando a resposta do provedor/)).toBeInTheDocument()
    const result = await screen.findByText('O login funcionaria.', {}, { timeout: 4000 })
    const box = result.closest('[role="status"]') as HTMLElement
    expect(box).toHaveTextContent('rita@empresa.com')
    expect(box).toHaveTextContent('niadra-admins, x')
    expect(box).toHaveTextContent('Administração')
    expect(box).toHaveTextContent('Pessoa nova: seria criada no primeiro login')
  })

  it('says why a test sign-in would be refused', async () => {
    mockApi(
      baseRoutes({
        [`GET ${CONTROL}/v1/sso`]: { connection: SAML, service_provider: SP },
        [`POST ${CONTROL}/v1/sso/test`]: { test_id: 't2', authorize_url: 'https://idp.empresa.com/sso?x', expires_at: '2099-01-01T00:00:00Z' },
        [`GET ${CONTROL}/v1/sso/tests/t2`]: { test_id: 't2', status: 'failed', created_at: '2026-09-24T12:00:00Z', completed_at: '2026-09-24T12:01:00Z', ok: false, failure: 'invalid', detail: 'the assertion is for another service provider', email: null, groups: [], roles: [], person_exists: null },
      }),
    )
    const { user } = await signInAt(at('sso'))
    await user.click(await screen.findByRole('button', { name: 'Testar o login' }))
    expect(await screen.findByText('O login seria recusado.')).toBeInTheDocument()
    expect(screen.getByText(/não passou na conferência da Niadra/)).toBeInTheDocument()
    expect(screen.getByText('the assertion is for another service provider')).toBeInTheDocument()
  })

  it('removes the connection with a reason', async () => {
    const api = mockApi(baseRoutes({ [`GET ${CONTROL}/v1/sso`]: { connection: SAML, service_provider: SP }, [`DELETE ${CONTROL}/v1/sso`]: new Response(null, { status: 204 }) }))
    const { user } = await signInAt(at('sso'))
    await user.click(await screen.findByRole('button', { name: 'Remover o SSO' }))
    const dialog = screen.getByRole('dialog', { name: 'Remover o SSO' })
    await user.type(within(dialog).getByLabelText('Motivo'), 'trocamos de provedor')
    await user.click(within(dialog).getByRole('button', { name: 'Remover o SSO' }))
    expect(api.find('DELETE', '/v1/sso')[0]!.body).toEqual({ reason: 'trocamos de provedor' })
  })

  it('explains the second factor with SSO and is for admins only', async () => {
    mockApi(baseRoutes({ [`GET ${CONTROL}/v1/sso`]: EMPTY }))
    await signInAt(at('sso'))
    expect(await screen.findByRole('heading', { name: 'Como fica o segundo fator com SSO' })).toBeInTheDocument()
    expect(screen.getByText(/passa pelo MFA do provedor de identidade da empresa/)).toBeInTheDocument()
  })

  it('shows nothing of the connection to other roles', async () => {
    const api = mockApi(baseRoutes({}, ['integration']))
    await signInAt(at('sso'))
    expect(await screen.findByText('Esta tela exige o papel Administração.')).toBeInTheDocument()
    expect(api.find('GET', '/v1/sso')).toHaveLength(0)
  })
})
