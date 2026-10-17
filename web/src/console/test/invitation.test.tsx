import { screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'
import { baseRoutes, CONTROL, mockApi, problem, renderAt } from './harness'

const TOKEN = 'INVITATION-TOKEN-0123456789-ABCDEFGHIJ'

describe('an invitation', () => {
  it('lets the person choose a password, then signs in with the e-mail already typed', async () => {
    const api = mockApi(
      baseRoutes({
        [`POST ${CONTROL}/v1/auth/invitation/preview`]: { email: 'nina@empresa.com', tenant_name: 'Empresa Exemplo', expires_at: '2026-09-29T12:00:00Z' },
        [`POST ${CONTROL}/v1/auth/invitation`]: { email: 'nina@empresa.com' },
      }),
    )
    const user = userEvent.setup()
    renderAt(`/console/convite#${TOKEN}`)
    expect(await screen.findByRole('heading', { name: 'Crie a sua senha' })).toBeInTheDocument()
    expect(screen.getByText('Empresa Exemplo')).toBeInTheDocument()
    // The token left the address bar and travels only in bodies.
    expect(window.location.href).not.toContain(TOKEN)
    expect(api.find('POST', '/v1/auth/invitation/preview')[0]!.body).toEqual({ token: TOKEN })

    await user.type(screen.getByLabelText('Senha'), 'curta')
    await user.type(screen.getByLabelText('A mesma senha de novo'), 'outra')
    await user.click(screen.getByRole('button', { name: 'Criar a senha' }))
    expect(screen.getByText('As duas senhas não são iguais.')).toBeInTheDocument()
    expect(screen.getByLabelText('Senha')).toHaveAttribute('aria-invalid', 'true')
    expect(api.find('POST', '/v1/auth/invitation')).toHaveLength(0)

    await user.clear(screen.getByLabelText('Senha'))
    await user.clear(screen.getByLabelText('A mesma senha de novo'))
    await user.type(screen.getByLabelText('Senha'), 'uma frase longa de senha')
    await user.type(screen.getByLabelText('A mesma senha de novo'), 'uma frase longa de senha')
    await user.click(screen.getByRole('button', { name: 'Criar a senha' }))
    expect(await screen.findByRole('status')).toHaveTextContent('Senha criada. Entre para configurar o aplicativo autenticador.')
    expect(api.find('POST', '/v1/auth/invitation')[0]!.body).toEqual({ token: TOKEN, password: 'uma frase longa de senha' })
    expect(screen.getByLabelText('E-mail corporativo')).toHaveValue('nina@empresa.com')
  })

  it('says when the link expired or was used', async () => {
    mockApi(baseRoutes({ [`POST ${CONTROL}/v1/auth/invitation/preview`]: problem(401, 'unauthenticated', 'the invitation is invalid or expired') }))
    renderAt(`/console/convite#${TOKEN}`)
    expect(await screen.findByRole('heading', { name: 'O convite venceu ou já foi usado' })).toBeInTheDocument()
  })

  it('says when the link is incomplete', async () => {
    const api = mockApi(baseRoutes())
    renderAt('/console/convite')
    expect(await screen.findByRole('heading', { name: 'Este link de convite está incompleto' })).toBeInTheDocument()
    expect(api.find('POST', '/v1/auth/invitation/preview')).toHaveLength(0)
  })
})
