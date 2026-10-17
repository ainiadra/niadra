import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { I18n, dicts } from '../i18n'
import { Enterprise } from './Enterprise'

const e = dicts.pt.enterprise

function renderForm() {
  const user = userEvent.setup()
  render(
    <MemoryRouter initialEntries={['/enterprise']}>
      <I18n.Provider value={{ lang: 'pt', t: dicts.pt, setLang: () => {} }}>
        <Enterprise />
      </I18n.Provider>
    </MemoryRouter>,
  )
  return user
}

async function fill(user: ReturnType<typeof userEvent.setup>, email: string) {
  await user.type(screen.getByLabelText(e.name), 'Marina Souza')
  await user.type(screen.getByLabelText(e.email), email)
  await user.type(screen.getByLabelText(e.company), 'Acme')
  await user.type(screen.getByLabelText(e.what), 'Queremos a memória para o agente de voz.')
  await user.click(screen.getByRole('button', { name: e.submit }))
}

describe('early-access form: work e-mail only', () => {
  afterEach(() => vi.unstubAllGlobals())

  it('says so and sends nothing when the e-mail is personal', async () => {
    const fetch = vi.fn()
    vi.stubGlobal('fetch', fetch)
    const user = renderForm()
    await fill(user, 'marina@gmail.com')
    expect(await screen.findByText(e.errCorporate)).toBeInTheDocument()
    expect(fetch).not.toHaveBeenCalled()
  })

  it('shows why the API refused the domain', async () => {
    const fetch = vi.fn().mockResolvedValue(new Response(JSON.stringify({ error: 'fields', fields: ['email'], reasons: { email: 'domain' } }), { status: 400 }))
    vi.stubGlobal('fetch', fetch)
    const user = renderForm()
    await fill(user, 'marina@acme-nao-existe.com.br')
    expect(await screen.findByText(e.errDomain)).toBeInTheDocument()
    const [url, init] = fetch.mock.calls[0]!
    expect(url).toBe('/api/contato')
    expect(JSON.parse(init.body)).toMatchObject({ email: 'marina@acme-nao-existe.com.br', company: 'Acme', interest: 'conversa', lang: 'pt' })
  })

  it('confirms a request the API kept', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({ ok: true }), { status: 201 })))
    const user = renderForm()
    await fill(user, 'marina@acme.com.br')
    expect(await screen.findByText(e.sentTitle)).toBeInTheDocument()
  })
})
