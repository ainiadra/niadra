import { screen, waitFor, within } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { API, at, baseRoutes, NO_FEATURES, CODES, CONTROL, mockApi, problem, renderAt, signInAt, SP_PROD, SP_SBX, tokenOut, type Req } from './harness'

describe('login and spaces', () => {
  it('signs in, keeps the token only in memory and lists spaces by project', async () => {
    const api = mockApi(baseRoutes())
    await signInAt('/console/entrar')
    expect(await screen.findByRole('heading', { name: 'Escolha o espaço' })).toBeInTheDocument()
    expect(await screen.findByRole('heading', { name: 'Atendimento e cobrança' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Expansão' })).toBeInTheDocument()
    expect(screen.getByText('ana.costa@empresa.com')).toBeInTheDocument()
    expect(screen.getAllByText('Administração').length).toBeGreaterThan(0)

    const login = api.find('POST', '/v1/auth/login')[0]!
    expect(login.body).toEqual({ email: 'ana@empresa.com', password: 'senha-de-teste' })
    const auth = api.find('GET', '/v1/projects')[0]!.headers.get('Authorization')!
    expect(auth).toMatch(/^Bearer ey/)
    const token = auth.slice('Bearer '.length)

    // Architecture 5.22: the token never reaches browser storage, cookies or the URL.
    expect(window.localStorage).toBeInstanceOf(Storage)
    const stored = (st: Storage) => Array.from({ length: st.length }, (_, i) => `${st.key(i)}=${st.getItem(st.key(i)!)}`).join(';')
    for (const place of [stored(window.localStorage), stored(window.sessionStorage), document.cookie, window.location.href]) expect(place).not.toContain(token)
  })

  it('asks for the authenticator code when the control plane answers "invalid second factor"', async () => {
    let attempt = 0
    const api = mockApi(baseRoutes({ [`POST ${CONTROL}/v1/auth/login`]: () => (++attempt === 1 ? problem(401, 'unauthenticated', 'invalid second factor') : tokenOut()) }))
    const { user } = await signInAt('/console/entrar')
    await user.type(await screen.findByLabelText('Código do autenticador'), '123456')
    await user.click(screen.getByRole('button', { name: 'Confirmar código' }))
    expect(await screen.findByRole('heading', { name: 'Escolha o espaço' })).toBeInTheDocument()
    expect(api.find('POST', '/v1/auth/login')[1]!.body).toMatchObject({ totp: '123456' })
  })

  it('says when the credentials do not match', async () => {
    mockApi(baseRoutes({ [`POST ${CONTROL}/v1/auth/login`]: problem(401, 'unauthenticated', 'invalid credentials') }))
    await signInAt('/console/entrar')
    expect(await screen.findByRole('alert')).toHaveTextContent('E-mail ou senha não conferem.')
  })

  it('enrolls the authenticator in three steps: the app, the first code, then the recovery codes', async () => {
    const blobs: Blob[] = []
    vi.stubGlobal('URL', Object.assign(URL, { createObjectURL: vi.fn((b: Blob) => (blobs.push(b), 'blob:console/codes')), revokeObjectURL: vi.fn() }))
    const saved: string[] = []
    vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function (this: HTMLAnchorElement) {
      saved.push(this.download)
    })
    let attempt = 0
    const api = mockApi(
      baseRoutes({
        [`POST ${CONTROL}/v1/auth/login`]: () =>
          ++attempt === 1 ? problem(401, 'unauthenticated', 'second factor enrollment required') : { ...tokenOut(), recovery_codes: CODES, recovery_codes_left: null },
        [`POST ${CONTROL}/v1/auth/second-factor`]: {
          secret: 'JBSWY3DPEHPK3PXPJBSWY3DPEHPK3PXP',
          totp_uri: 'otpauth://totp/Niadra:ana@empresa.com?secret=JBSWY3DPEHPK3PXPJBSWY3DPEHPK3PXP&issuer=Niadra',
        },
      }),
    )
    const { user } = await signInAt('/console/entrar')

    // Step 1: the app, by QR code or by the key typed in.
    expect(await screen.findByRole('heading', { name: 'Conecte o aplicativo autenticador' })).toHaveFocus()
    expect(screen.getByRole('list', { name: 'Passo 1 de 3' })).toBeInTheDocument()
    expect(screen.getByText('ana@empresa.com')).toBeInTheDocument()
    expect(screen.getByRole('img', { name: 'QR code para o aplicativo autenticador' })).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Não consegue ler o QR code? Digite a chave' }))
    expect(screen.getByText('JBSW Y3DP EHPK 3PXP JBSW Y3DP EHPK 3PXP')).toBeInTheDocument()
    expect(api.find('POST', '/v1/auth/second-factor')[0]!.body).toEqual({ email: 'ana@empresa.com', password: 'senha-de-teste' })
    await user.click(screen.getByRole('button', { name: 'Já adicionei, continuar' }))

    // Step 2: the first code confirms it.
    expect(screen.getByRole('list', { name: 'Passo 2 de 3' })).toBeInTheDocument()
    expect(screen.getByLabelText('Código do autenticador')).toHaveFocus()
    expect(screen.getByRole('button', { name: 'Ativar e entrar' })).toBeDisabled()
    await user.keyboard('654321')
    await user.click(screen.getByRole('button', { name: 'Ativar e entrar' }))
    expect(api.find('POST', '/v1/auth/login')[1]!.body).toMatchObject({ totp: '654321' })

    // Step 3: the recovery codes that login returned, the only time they appear.
    expect(await screen.findByRole('heading', { name: 'Guarde os códigos de recuperação' })).toHaveFocus()
    expect(screen.getByRole('list', { name: 'Passo 3 de 3' })).toBeInTheDocument()
    const grid = screen.getByRole('list', { name: 'Códigos de recuperação' })
    expect(within(grid).getAllByRole('listitem')).toHaveLength(10)
    for (const code of CODES) expect(within(grid).getByText(code)).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Baixar os códigos (.txt)' }))
    expect(saved).toEqual(['niadra-codigos-de-recuperacao.txt'])
    const text = await blobs[0]!.text()
    expect(text).toMatch(/^Niadra: códigos de recuperação do segundo fator\nConta: ana@empresa\.com\nData: \d{2}\/\d{2}\/\d{4} \d{2}:\d{2}\n/)
    expect(text).toContain(` 1. ${CODES[0]}\n`)
    expect(text).toContain(`10. ${CODES[9]}\n`)

    await user.click(screen.getByRole('button', { name: 'Copiar' }))
    expect(await screen.findByRole('button', { name: 'Copiado' })).toBeInTheDocument()
    expect(await navigator.clipboard.readText()).toBe(CODES.join('\n'))

    const enter = screen.getByRole('button', { name: 'Entrar no Console' })
    expect(enter).toBeDisabled()
    await user.click(screen.getByLabelText('Guardei os códigos num lugar seguro'))
    await user.click(enter)
    expect(await screen.findByRole('heading', { name: 'Escolha o espaço' })).toBeInTheDocument()
    // Like the token, the codes never reach browser storage, cookies or the URL.
    const stored = (st: Storage) => Array.from({ length: st.length }, (_, i) => `${st.key(i)}=${st.getItem(st.key(i)!)}`).join(';')
    for (const place of [stored(window.localStorage), stored(window.sessionStorage), document.cookie, window.location.href]) expect(place).not.toContain(CODES[0])
  })

  it('signs in with a recovery code when the app is lost, then says how many are left', async () => {
    let attempt = 0
    const api = mockApi(
      baseRoutes({
        [`POST ${CONTROL}/v1/auth/login`]: (r: Req) =>
          ++attempt === 1 || r.body.recovery_code === 'ZZZZZ-ZZZZZ'
            ? problem(401, 'unauthenticated', 'invalid second factor')
            : { ...tokenOut(), recovery_codes: null, recovery_codes_left: 7 },
        [`GET ${CONTROL}/v1/users/me/second-factor`]: { confirmed_at: '2026-09-20T12:00:00Z', recovery_codes_left: 7 },
      }),
    )
    const { user } = await signInAt('/console/entrar')
    expect(await screen.findByLabelText('Código do autenticador')).toHaveFocus()

    await user.click(screen.getByRole('button', { name: 'Perdeu o acesso ao aplicativo? Use um código de recuperação' }))
    expect(screen.getByRole('heading', { name: 'Use um código de recuperação' })).toBeInTheDocument()
    expect(screen.getByLabelText('Código de recuperação')).toHaveFocus()
    await user.click(screen.getByRole('button', { name: 'Usar o código do aplicativo' }))
    expect(screen.getByLabelText('Código do autenticador')).toHaveFocus()
    await user.click(screen.getByRole('button', { name: 'Perdeu o acesso ao aplicativo? Use um código de recuperação' }))

    const field = screen.getByLabelText('Código de recuperação')
    await user.keyboard('ZZZZZ-ZZZZZ')
    await user.click(screen.getByRole('button', { name: 'Entrar com o código' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('O código de recuperação não confere ou já foi usado.')
    expect(field).toHaveFocus()
    await user.clear(field)
    await user.type(field, ' 7kq2m-xa9bd ')
    await user.click(screen.getByRole('button', { name: 'Entrar com o código' }))

    expect(await screen.findByRole('heading', { name: 'Você entrou com um código de recuperação' })).toHaveFocus()
    expect(screen.getByRole('status')).toHaveTextContent('Restam 7 códigos de recuperação.')
    expect(api.find('POST', '/v1/auth/login').at(-1)!.body).toEqual({ email: 'ana@empresa.com', password: 'senha-de-teste', recovery_code: '7kq2m-xa9bd' })
    await user.click(screen.getByRole('button', { name: 'Abrir Sua conta' }))
    expect(await screen.findByRole('heading', { name: 'Sua conta' })).toBeInTheDocument()
    expect(await screen.findByText('7 disponíveis')).toBeInTheDocument()
  })

  it('goes back from the steps to the password with "Usar outra conta"', async () => {
    mockApi(
      baseRoutes({
        [`POST ${CONTROL}/v1/auth/login`]: problem(401, 'unauthenticated', 'invalid second factor'),
      }),
    )
    const { user } = await signInAt('/console/entrar')
    expect(await screen.findByRole('heading', { name: 'Digite o código de acesso' })).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Usar outra conta' }))
    expect(screen.getByLabelText('E-mail corporativo')).toHaveFocus()
    expect(screen.getByRole('heading', { name: 'Entrar no Console' })).toBeInTheDocument()
    expect(screen.queryByLabelText('Senha')).not.toBeInTheDocument()
  })

  it('shows the empty state when no space is released', async () => {
    mockApi(baseRoutes({ [`GET ${CONTROL}/v1/users/me`]: { user_id: 'u', tenant_id: 't', email: 'ana.costa@empresa.com', name: null, spaces: [] } }))
    await signInAt('/console/entrar')
    expect(await screen.findByText('Nenhum espaço liberado para você.')).toBeInTheDocument()
  })

  it('explains a missing /config.json instead of a blank page', async () => {
    mockApi({ 'GET /config.json': problem(404, 'not_found') })
    renderAt('/console/entrar')
    expect(await screen.findByRole('heading', { name: 'O Console está sem configuração.' })).toBeInTheDocument()
  })

  it('switches to a sandbox space with POST /v1/auth/token and marks every screen', async () => {
    const api = mockApi(baseRoutes())
    await signInAt(`/console/e/${SP_SBX}/clientes`)
    expect(await screen.findByRole('heading', { name: 'Clientes' })).toBeInTheDocument()
    expect(screen.getByRole('note')).toHaveTextContent('Sandbox: apenas dados sintéticos.')
    await waitFor(() => expect(document.title).toBe('[Sandbox] Clientes · Console da Niadra'))
    expect(api.find('POST', '/v1/auth/token')[0]!.body).toEqual({ space_id: SP_SBX })
  })

  it('does not ask for a new token when the login token is already for the space', async () => {
    const api = mockApi(baseRoutes())
    await signInAt(at('clientes'))
    expect(await screen.findByRole('heading', { name: 'Clientes' })).toBeInTheDocument()
    expect(screen.queryByText('Sandbox: apenas dados sintéticos.')).not.toBeInTheDocument()
    expect(api.find('POST', '/v1/auth/token')).toHaveLength(0)
    expect(await screen.findByText('Empresa Exemplo')).toBeInTheDocument()
    // The rail shows the e-mail from GET /v1/users/me, not the one typed at login.
    expect(await within(screen.getByRole('complementary', { name: 'Console' })).findByText('ana.costa@empresa.com')).toBeInTheDocument()
  })

  it('refuses a space whose region has no data API in config.json', async () => {
    mockApi(baseRoutes({ 'GET /config.json': { controlURL: CONTROL, cells: { 'sa-east-1': API } } }))
    const { user } = await signInAt('/console/entrar')
    await user.click(await screen.findByRole('button', { name: /Expansão, Produção/ }))
    expect(await screen.findByRole('alert')).toHaveTextContent('A região eu-west-1 não tem endereço de API no config.json do Console.')
  })

  it('renews the token silently before it expires', async () => {
    let n = 0
    const api = mockApi(
      baseRoutes({
        // The login token expires in 62 s; renewal happens a minute before, so in about 2 s.
        [`POST ${CONTROL}/v1/auth/login`]: tokenOut({ exp: Math.floor(Date.now() / 1000) + 62 }),
        [`POST ${CONTROL}/v1/auth/token`]: () => tokenOut({ exp: Math.floor(Date.now() / 1000) + 900 + ++n }),
      }),
    )
    await signInAt(at('clientes'))
    await screen.findByRole('heading', { name: 'Clientes' })
    await waitFor(() => expect(api.find('POST', '/v1/auth/token').length).toBeGreaterThan(0), { timeout: 4000 })
    expect(api.find('POST', '/v1/auth/token')[0]!.body).toEqual({ space_id: SP_PROD })
    expect(screen.getByRole('heading', { name: 'Clientes' })).toBeInTheDocument()
  })

  it('goes back to the login with a notice when a call answers 401', async () => {
    mockApi(baseRoutes({ [`GET ${CONTROL}/v1/sources`]: problem(401, 'unauthenticated', 'expired token') }))
    await signInAt(at('agentes'))
    expect(await screen.findByRole('heading', { name: 'Entrar no Console' })).toBeInTheDocument()
    expect(screen.getByRole('status')).toHaveTextContent('Sua sessão terminou.')
  })

  it('signs out from the rail, and keeps the query of a deep link across the login', async () => {
    mockApi(baseRoutes({ [`POST ${API}/v1/receipts/search`]: { items: [] } }))
    const { user } = await signInAt(`${at('recibos')}?kind=admin`)
    expect(await screen.findByText('Nenhum comprovante com esses filtros.')).toBeInTheDocument()
    await user.click(within(screen.getByRole('complementary', { name: 'Console' })).getByRole('button', { name: 'Sair' }))
    expect(await screen.findByRole('heading', { name: 'Entrar no Console' })).toBeInTheDocument()
  })

  it('opens and closes the mobile menu, with Escape too', async () => {
    mockApi(baseRoutes())
    const { user } = await signInAt(at('clientes'))
    const menu = await screen.findByRole('button', { name: 'Menu' })
    await user.click(menu)
    expect(document.getElementById('console-menu')).toBeInTheDocument()
    await user.keyboard('{Escape}')
    expect(document.getElementById('console-menu')).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Menu' })).toHaveFocus()
  })

  it('lists the screens in the rail, grouped by job, with the technical ones under Avançado and administration only for admins', async () => {
    mockApi(baseRoutes())
    const { user } = await signInAt(at('clientes'))
    const nav = await screen.findByRole('navigation', { name: 'Seções do Console' })
    await within(nav).findByRole('link', { name: 'Turnos dos agentes' })
    const names = () =>
      within(nav)
        .getAllByRole('link')
        .map((a) => a.textContent)
    // Each group says its job in one line.
    expect(within(nav).getByText('O que a Niadra sabe de cada cliente')).toBeInTheDocument()
    expect(within(nav).getByText('Leituras, respostas e resultados, por agente')).toBeInTheDocument()
    expect(within(nav).getByText('Por onde começar')).toBeInTheDocument()
    expect(names()).toEqual([
      'Início',
      'Tutoriais',
      'Clientes',
      'Mapa de relações',
      'Identidade',
      'Padrões',
      'Comprovantes',
      'Turnos dos agentes',
      'Uso e aproveitamento',
      'Desfechos e experimentos',
      'Revisão',
      'Lacunas e respostas prontas',
      'Dados',
      'Agentes e chaves',
      'Sistemas',
      'Avisos',
      'Pessoas e papéis',
      'Login único (SSO)',
    ])
    // The technical screens open on demand, and the choice is remembered.
    const advanced = within(nav).getByRole('button', { name: /Avançado/ })
    expect(advanced).toHaveAttribute('aria-expanded', 'false')
    await user.click(advanced)
    expect(advanced).toHaveAttribute('aria-expanded', 'true')
    expect(names().slice(16, 20)).toEqual(['Memória dos agentes', 'Coordenação', 'Tipos', 'Configuração'])
    expect(localStorage.getItem('niadra.console.rail.advanced')).toBe('open')
  })

  it('opens Avançado by itself when the person lands on one of its screens', async () => {
    mockApi(baseRoutes())
    await signInAt(at('configuracao'))
    const nav = await screen.findByRole('navigation', { name: 'Seções do Console' })
    expect(await within(nav).findByRole('link', { name: 'Configuração' })).toBeInTheDocument()
    expect(within(nav).getByRole('button', { name: /Avançado/ })).toHaveAttribute('aria-expanded', 'true')
  })

  it('keeps the administration group out of the rail for other roles, and the screens of other roles too', async () => {
    mockApi(baseRoutes({}, ['security']))
    await signInAt(at('clientes'))
    const nav = await screen.findByRole('navigation', { name: 'Seções do Console' })
    await within(nav).findByRole('link', { name: 'Turnos dos agentes' })
    for (const name of ['Pessoas e papéis', 'Revisão', 'Lacunas e respostas prontas', 'Desfechos e experimentos']) {
      expect(within(nav).queryByRole('link', { name })).not.toBeInTheDocument()
    }
    expect(within(nav).getAllByRole('link')).toHaveLength(13)
  })

  it('keeps the screens of the agent features out of a space that turned none on', async () => {
    const api = mockApi(baseRoutes(NO_FEATURES))
    const { user } = await signInAt(at('clientes'))
    const nav = await screen.findByRole('navigation', { name: 'Seções do Console' })
    await waitFor(() => expect(api.find('GET', '/v1/config/features')).toHaveLength(1))
    await new Promise((resolve) => setTimeout(resolve, 50))
    expect(within(nav).getAllByRole('link')).toHaveLength(16)
    await user.click(within(nav).getByRole('button', { name: /Avançado/ }))
    for (const name of ['Turnos dos agentes', 'Coordenação', 'Desfechos e experimentos', 'Tipos']) {
      expect(within(nav).queryByRole('link', { name })).not.toBeInTheDocument()
    }
    expect(within(nav).getByRole('link', { name: 'Dados' })).toBeInTheDocument()
    expect(within(nav).getByRole('link', { name: 'Configuração' })).toBeInTheDocument()
  })

  it('shows only the screens of the features the space turned on', async () => {
    mockApi(
      baseRoutes({
        [`GET ${CONTROL}/v1/config/features`]: { space_id: 's', type: 'features', version: 2, document: { enabled: ['turns'] } },
      }),
    )
    const { user } = await signInAt(at('clientes'))
    const nav = await screen.findByRole('navigation', { name: 'Seções do Console' })
    await within(nav).findByRole('link', { name: 'Turnos dos agentes' })
    await user.click(within(nav).getByRole('button', { name: /Avançado/ }))
    expect(within(nav).queryByRole('link', { name: 'Coordenação' })).not.toBeInTheDocument()
    expect(within(nav).queryByRole('link', { name: 'Tipos' })).not.toBeInTheDocument()
    expect(within(nav).getByRole('link', { name: 'Dados' })).toBeInTheDocument()
  })

  it('says a screen reached by address is off, in words, with the way to turn it on', async () => {
    mockApi(baseRoutes(NO_FEATURES))
    await signInAt(at('tipos'))
    expect(await screen.findByText('Os tipos de objeto estão desligados neste espaço.')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Abrir Configuração' })).toBeInTheDocument()
  })
})
