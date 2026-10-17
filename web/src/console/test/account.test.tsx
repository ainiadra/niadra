import { screen, within } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { paths } from '../paths'
import { at, baseRoutes, CODES, CONTROL, ME, mockApi, problem, signInAt, type Req } from './harness'

const STATUS = { confirmed_at: '2026-09-20T12:00:00Z', recovery_codes_left: 8 }
const SECRET = { secret: 'KRSXG5CTMVRXEZLU', totp_uri: 'otpauth://totp/Niadra:ana.costa@empresa.com?secret=KRSXG5CTMVRXEZLU&issuer=Niadra' }

describe('account and second factor', () => {
  it('opens from the account area of the rail and goes back to the space', async () => {
    mockApi(baseRoutes({ [`GET ${CONTROL}/v1/users/me/second-factor`]: STATUS }))
    const { user } = await signInAt(at('clientes'))
    await screen.findByRole('heading', { name: 'Clientes' })
    await user.click(within(screen.getByRole('complementary', { name: 'Console' })).getByRole('link', { name: 'Sua conta' }))

    expect(await screen.findByRole('heading', { name: 'Sua conta' })).toBeInTheDocument()
    expect(await screen.findByText('Ativo desde 20/09/2026 09:00')).toBeInTheDocument()
    expect(screen.getByText('8 disponíveis')).toBeInTheDocument()
    expect(screen.getByText('ana.costa@empresa.com')).toBeInTheDocument()
    await user.click(screen.getByRole('link', { name: 'Voltar ao espaço' }))
    expect(await screen.findByRole('heading', { name: 'Clientes' })).toBeInTheDocument()
  })

  it('generates new recovery codes after the password, and a wrong password keeps the session', async () => {
    let generated = false
    const api = mockApi(
      baseRoutes({
        [`GET ${CONTROL}/v1/users/me/second-factor`]: () => ({ ...STATUS, recovery_codes_left: generated ? 10 : 2 }),
        [`POST ${CONTROL}/v1/users/me/recovery-codes`]: (r: Req) =>
          r.body.password === 'senha-de-teste' ? ((generated = true), { recovery_codes: CODES }) : problem(403, 'forbidden', 'invalid password'),
      }),
    )
    const { user } = await signInAt('/console/conta')
    expect(await screen.findByText('Restam 2 códigos de recuperação. Gere dez novos antes que acabem.')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Gerar novos códigos de recuperação' }))
    const dialog = screen.getByRole('dialog', { name: 'Gerar novos códigos de recuperação' })
    const password = within(dialog).getByLabelText('Senha')
    expect(password).toHaveFocus()
    await user.type(password, 'senha-errada')
    await user.click(within(dialog).getByRole('button', { name: 'Gerar os códigos' }))
    expect(await within(dialog).findByRole('alert')).toHaveTextContent('A senha não confere.')
    // A 403 is not a 401: the person is still signed in, on the same dialog.
    expect(screen.queryByRole('heading', { name: 'Entrar no Console' })).not.toBeInTheDocument()

    await user.clear(password)
    await user.type(password, 'senha-de-teste')
    await user.click(within(dialog).getByRole('button', { name: 'Gerar os códigos' }))
    expect(await within(dialog).findByRole('heading', { name: 'Seus dez códigos novos' })).toHaveFocus()
    const grid = within(dialog).getByRole('list', { name: 'Códigos de recuperação' })
    expect(within(grid).getAllByRole('listitem')).toHaveLength(10)
    expect(within(dialog).getByRole('button', { name: 'Baixar os códigos (.txt)' })).toBeInTheDocument()
    await user.click(within(dialog).getByRole('button', { name: 'Copiar' }))
    expect(await navigator.clipboard.readText()).toBe(CODES.join('\n'))

    const done = within(dialog).getByRole('button', { name: 'Concluir' })
    expect(done).toBeDisabled()
    await user.click(within(dialog).getByLabelText('Guardei os códigos num lugar seguro'))
    await user.click(done)
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(await screen.findByText('10 disponíveis')).toBeInTheDocument()
    expect(api.find('POST', '/v1/users/me/recovery-codes').map((c) => c.body)).toEqual([{ password: 'senha-errada' }, { password: 'senha-de-teste' }])
  })

  it('changes the authenticator app in the three steps, with the QR code and the new codes', async () => {
    const api = mockApi(
      baseRoutes({
        [`GET ${CONTROL}/v1/users/me/second-factor`]: STATUS,
        [`POST ${CONTROL}/v1/users/me/second-factor`]: SECRET,
        [`POST ${CONTROL}/v1/users/me/second-factor/confirm`]: (r: Req) => (r.body.totp === '111111' ? problem(403, 'forbidden', 'invalid second factor') : { recovery_codes: CODES }),
      }),
    )
    const { user } = await signInAt('/console/conta')
    await user.click(await screen.findByRole('button', { name: 'Trocar de aplicativo autenticador' }))
    const dialog = screen.getByRole('dialog', { name: 'Trocar de aplicativo autenticador' })
    expect(within(dialog).getByText('O aplicativo atual continua valendo até o novo ser confirmado.')).toBeInTheDocument()
    await user.type(within(dialog).getByLabelText('Senha'), 'senha-de-teste')
    await user.click(within(dialog).getByRole('button', { name: 'Continuar' }))

    // Step 1: the new app, by QR code or by the key.
    expect(await within(dialog).findByRole('heading', { name: 'Conecte o aplicativo novo' })).toHaveFocus()
    expect(within(dialog).getByRole('list', { name: 'Passo 1 de 3' })).toBeInTheDocument()
    expect(within(dialog).getByRole('img', { name: 'QR code para o novo aplicativo autenticador' })).toBeInTheDocument()
    await user.click(within(dialog).getByRole('button', { name: 'Não consegue ler o QR code? Digite a chave' }))
    expect(within(dialog).getByText('KRSX G5CT MVRX EZLU')).toBeInTheDocument()
    await user.click(within(dialog).getByRole('button', { name: 'Já adicionei, continuar' }))

    // Step 2: a wrong code changes nothing and clears the field.
    expect(within(dialog).getByRole('list', { name: 'Passo 2 de 3' })).toBeInTheDocument()
    const code = within(dialog).getByLabelText('Código do autenticador')
    expect(code).toHaveFocus()
    await user.keyboard('111111')
    await user.click(within(dialog).getByRole('button', { name: 'Confirmar e trocar' }))
    expect(await within(dialog).findByRole('alert')).toHaveTextContent('O código não confere ou já venceu. Use o código que o aplicativo novo mostra agora.')
    expect(code).toHaveValue('')
    expect(code).toHaveFocus()
    await user.keyboard('222222')
    await user.click(within(dialog).getByRole('button', { name: 'Confirmar e trocar' }))

    // Step 3: the new recovery codes, with the same download and copy.
    expect(await within(dialog).findByRole('heading', { name: 'Guarde os códigos de recuperação' })).toHaveFocus()
    expect(within(dialog).getByRole('list', { name: 'Passo 3 de 3' })).toBeInTheDocument()
    expect(within(within(dialog).getByRole('list', { name: 'Códigos de recuperação' })).getAllByRole('listitem')).toHaveLength(10)
    expect(within(dialog).getByRole('button', { name: 'Baixar os códigos (.txt)' })).toBeInTheDocument()
    await user.click(within(dialog).getByLabelText('Guardei os códigos num lugar seguro'))
    await user.click(within(dialog).getByRole('button', { name: 'Concluir' }))
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()

    expect(api.find('POST', '/v1/users/me/second-factor')[0]!.body).toEqual({ password: 'senha-de-teste' })
    expect(api.find('POST', '/v1/users/me/second-factor/confirm').map((c) => c.body)).toEqual([{ totp: '111111' }, { totp: '222222' }])
    expect(api.find('GET', '/v1/users/me/second-factor').length).toBeGreaterThanOrEqual(2)
  })

  it('shows the error with a retry, and offers the setup to a person with no authenticator', async () => {
    let first = true
    mockApi(
      baseRoutes({
        [`GET ${CONTROL}/v1/users/me/second-factor`]: () => (first ? ((first = false), problem(503, 'unavailable')) : { confirmed_at: null, recovery_codes_left: 0 }),
        [`POST ${CONTROL}/v1/users/me/second-factor`]: SECRET,
      }),
    )
    const { user } = await signInAt('/console/entrar')
    await user.click(await screen.findByRole('link', { name: 'Sua conta' }))
    // A read the server asks to try later is a wait with a countdown, and the person can try now.
    expect(await screen.findByText('A região deste espaço pediu um instante.')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Tentar agora' }))

    expect(await screen.findByText('Nenhum configurado')).toBeInTheDocument()
    expect(screen.getByText('Nenhum disponível')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Gerar novos códigos de recuperação' })).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Configurar o aplicativo autenticador' }))
    const dialog = screen.getByRole('dialog', { name: 'Configurar o aplicativo autenticador' })
    await user.type(within(dialog).getByLabelText('Senha'), 'senha-de-teste')
    await user.click(within(dialog).getByRole('button', { name: 'Continuar' }))
    expect(await within(dialog).findByRole('heading', { name: 'Conecte o aplicativo autenticador' })).toHaveFocus()
    await user.click(within(dialog).getByRole('button', { name: 'Fechar' }))
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Escolher o espaço' })).toBeInTheDocument()
  })
})

describe('the account of a person the SSO created', () => {
  it('asks for the authenticator code instead of a password', async () => {
    const api = mockApi(
      baseRoutes({
        [`GET ${CONTROL}/v1/users/me`]: { ...ME(['review']), password_set: false, sso: true },
        [`GET ${CONTROL}/v1/users/me/second-factor`]: { confirmed_at: '2026-09-20T12:00:00Z', recovery_codes_left: 2 },
        [`POST ${CONTROL}/v1/users/me/recovery-codes`]: { recovery_codes: CODES },
      }, ['review']),
    )
    const { user } = await signInAt(paths.account)
    await user.click(await screen.findByRole('button', { name: 'Gerar novos códigos de recuperação' }))
    const dialog = screen.getByRole('dialog', { name: 'Gerar novos códigos de recuperação' })
    expect(within(dialog).queryByLabelText('Senha')).not.toBeInTheDocument()
    await user.type(within(dialog).getByLabelText('Código do autenticador'), '123456')
    await user.click(within(dialog).getByRole('button', { name: 'Gerar os códigos' }))
    expect(await within(dialog).findByText('Seus dez códigos novos')).toBeInTheDocument()
    expect(api.find('POST', '/v1/users/me/recovery-codes')[0]!.body).toEqual({ totp: '123456' })
  })

  it('says the provider is the second factor when there is no Niadra authenticator', async () => {
    mockApi(
      baseRoutes({
        [`GET ${CONTROL}/v1/users/me`]: { ...ME(['review']), password_set: false, sso: true },
        [`GET ${CONTROL}/v1/users/me/second-factor`]: { confirmed_at: null, recovery_codes_left: 0 },
      }, ['review']),
    )
    await signInAt(paths.account)
    expect(await screen.findByText(/o segundo fator é o do provedor de identidade/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Configurar o aplicativo autenticador' })).not.toBeInTheDocument()
  })
})
