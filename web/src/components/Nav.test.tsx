import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { I18n, dicts } from '../i18n'
import { GROUPS, productBySlug } from '../products'
import { Nav } from './Nav'

const t = dicts.pt
const name = (slug: string) => t.products[productBySlug(slug)!.key].name

function mount() {
  return render(
    <I18n.Provider value={{ lang: 'pt', t, setLang: () => {} }}>
      <MemoryRouter>
        <Nav scrolled={false} />
      </MemoryRouter>
    </I18n.Provider>,
  )
}

afterEach(() => vi.restoreAllMocks())

/**
 * The products menu in two panes: the groups on the left, the products of the chosen group on the
 * right, reachable by click, by hover and by keyboard, and closed by Escape. On the phone, each group
 * folds. The founder's complaint of 01/10/2026: thirteen products in one list cut off on a laptop.
 */
describe('the products menu', () => {
  it('opens on click with the first group chosen and its products on the right', async () => {
    const user = userEvent.setup()
    mount()
    await user.click(screen.getByRole('button', { name: t.nav.products }))
    const tabs = screen.getAllByRole('tab')
    expect(tabs).toHaveLength(GROUPS.length)
    expect(tabs[0]).toHaveAttribute('aria-selected', 'true')
    const pane = screen.getByRole('tabpanel')
    for (const slug of GROUPS[0].slugs) expect(within(pane).getByRole('link', { name: new RegExp(name(slug)) })).toBeInTheDocument()
    // Only the chosen group is in the DOM: the panel never grows past the longest group.
    expect(within(pane).queryByRole('link', { name: new RegExp(name('memoria')) })).toBeNull()
  })

  it('switches group on hover and on click', async () => {
    const user = userEvent.setup()
    mount()
    await user.click(screen.getByRole('button', { name: t.nav.products }))
    const remember = screen.getByRole('tab', { name: new RegExp(t.products.groups.remember.label) })
    await user.hover(remember)
    expect(remember).toHaveAttribute('aria-selected', 'true')
    expect(within(screen.getByRole('tabpanel')).getByRole('link', { name: new RegExp(name('memoria')) })).toBeInTheDocument()
    await user.click(screen.getByRole('tab', { name: new RegExp(t.products.groups.govern.label) }))
    expect(within(screen.getByRole('tabpanel')).getByRole('link', { name: new RegExp(name('console')) })).toBeInTheDocument()
  })

  it('moves between groups with the arrow keys, into the products with Right, and closes on Escape', async () => {
    const user = userEvent.setup()
    mount()
    const trigger = screen.getByRole('button', { name: t.nav.products })
    trigger.focus()
    await user.keyboard('{ArrowDown}')
    await vi.waitFor(() => expect(screen.getAllByRole('tab')[0]).toHaveFocus())
    await user.keyboard('{ArrowDown}')
    const tabs = screen.getAllByRole('tab')
    expect(tabs[1]).toHaveFocus()
    expect(tabs[1]).toHaveAttribute('aria-selected', 'true')
    await user.keyboard('{ArrowRight}')
    await vi.waitFor(() => expect(within(screen.getByRole('tabpanel')).getAllByRole('link')[0]).toHaveFocus())
    await user.keyboard('{ArrowDown}')
    expect(within(screen.getByRole('tabpanel')).getAllByRole('link')[1]).toHaveFocus()
    await user.keyboard('{ArrowLeft}')
    expect(screen.getAllByRole('tab')[1]).toHaveFocus()
    await user.keyboard('{Escape}')
    expect(trigger).toHaveAttribute('aria-expanded', 'false')
    expect(trigger).toHaveFocus()
  })

  it('folds each group on the phone', async () => {
    const user = userEvent.setup()
    mount()
    await user.click(screen.getByRole('button', { name: t.nav.menu }))
    const menu = document.getElementById('mobile-menu')!
    const groupBtn = within(menu).getByRole('button', { name: new RegExp(t.products.groups.deliver.label) })
    expect(groupBtn).toHaveAttribute('aria-expanded', 'false')
    expect(within(menu).queryByRole('link', { name: name('contexto') })).toBeNull()
    await user.click(groupBtn)
    expect(groupBtn).toHaveAttribute('aria-expanded', 'true')
    expect(within(menu).getByRole('link', { name: name('contexto') })).toBeInTheDocument()
  })
})

/** One ask only (founder, 02/10/2026): the early-access button, and no Enterprise link beside it. */
describe('the header asks once', () => {
  it('shows the early-access button and no Enterprise link', () => {
    mount()
    expect(screen.getAllByRole('link', { name: t.nav.cta })).toHaveLength(1)
    expect(screen.queryByRole('link', { name: /enterprise/i })).toBeNull()
    expect(screen.queryByRole('link', { name: t.nav.enterprise })).toBeNull()
  })
})
