import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { useState } from 'react'
import { describe, expect, it, vi } from 'vitest'
import { Select } from './Select'

const OPTIONS = [
  { value: 'conversa', label: 'Uma conversa' },
  { value: 'producao', label: 'Produção' },
  { value: 'regulado', label: 'Plano Regulado' },
  { value: 'docs', label: 'Documentação' },
]

function Field({ onEscapeOutside = () => {} }: { onEscapeOutside?: () => void }) {
  const [value, setValue] = useState('conversa')
  return (
    <div
      onKeyDown={(e) => {
        if (e.key === 'Escape') onEscapeOutside()
      }}
    >
      <label htmlFor="f">Interesse</label>
      <Select id="f" triggerClassName="field" value={value} onChange={setValue} options={OPTIONS} />
      <output>{value}</output>
    </div>
  )
}

describe('Select', () => {
  it('is named by its label and shows the chosen option', () => {
    render(<Field />)
    const box = screen.getByRole('combobox', { name: 'Interesse' })
    expect(box).toHaveTextContent('Uma conversa')
    expect(box).toHaveAttribute('aria-expanded', 'false')
    expect(screen.queryByRole('listbox')).not.toBeInTheDocument()
  })

  it('opens on click, marks the chosen option and picks another', async () => {
    const user = userEvent.setup()
    render(<Field />)
    await user.click(screen.getByRole('combobox', { name: 'Interesse' }))
    expect(screen.getByRole('listbox')).toBeInTheDocument()
    expect(screen.getByRole('option', { name: 'Uma conversa' })).toHaveAttribute('aria-selected', 'true')
    await user.click(screen.getByRole('option', { name: 'Plano Regulado' }))
    expect(screen.queryByRole('listbox')).not.toBeInTheDocument()
    expect(screen.getByText('regulado')).toBeInTheDocument()
    expect(screen.getByRole('combobox')).toHaveFocus()
  })

  it('works from the keyboard: arrows, Home, End, Enter', async () => {
    const user = userEvent.setup()
    render(<Field />)
    screen.getByRole('combobox').focus()
    await user.keyboard('{ArrowDown}')
    const box = screen.getByRole('combobox')
    expect(box).toHaveAttribute('aria-expanded', 'true')
    expect(box.getAttribute('aria-activedescendant')).toBe(screen.getByRole('option', { name: 'Uma conversa' }).id)
    await user.keyboard('{ArrowDown}{ArrowDown}')
    expect(box.getAttribute('aria-activedescendant')).toBe(screen.getByRole('option', { name: 'Plano Regulado' }).id)
    await user.keyboard('{End}')
    expect(box.getAttribute('aria-activedescendant')).toBe(screen.getByRole('option', { name: 'Documentação' }).id)
    await user.keyboard('{Home}{Enter}')
    expect(screen.getByText('conversa')).toBeInTheDocument()
    await user.keyboard('{ArrowDown}{ArrowDown}{Enter}')
    expect(screen.getByText('producao')).toBeInTheDocument()
  })

  it('jumps to the option that starts with the typed letters, accents ignored', async () => {
    const user = userEvent.setup()
    render(<Field />)
    screen.getByRole('combobox').focus()
    await user.keyboard('p')
    const box = screen.getByRole('combobox')
    expect(box.getAttribute('aria-activedescendant')).toBe(screen.getByRole('option', { name: 'Produção' }).id)
    await user.keyboard('{Enter}')
    expect(screen.getByText('producao')).toBeInTheDocument()
  })

  it('closes on Escape without choosing, and the Escape stops at the list', async () => {
    const outside = vi.fn()
    // The Console's dialog listens on the document, like this.
    const onDocument = vi.fn((e: KeyboardEvent) => e.key === 'Escape')
    document.addEventListener('keydown', onDocument)
    const user = userEvent.setup()
    render(<Field onEscapeOutside={outside} />)
    await user.click(screen.getByRole('combobox'))
    await user.keyboard('{ArrowDown}{Escape}')
    expect(screen.queryByRole('listbox')).not.toBeInTheDocument()
    expect(screen.getByText('conversa')).toBeInTheDocument()
    expect(outside).not.toHaveBeenCalled()
    expect(onDocument.mock.results.filter((r) => r.value === true)).toHaveLength(0)
    // Closed, Escape goes on as usual (a dialog around it closes).
    await user.keyboard('{Escape}')
    expect(outside).toHaveBeenCalledTimes(1)
    expect(onDocument.mock.results.filter((r) => r.value === true)).toHaveLength(1)
    document.removeEventListener('keydown', onDocument)
  })

  it('closes on a click outside', async () => {
    const user = userEvent.setup()
    render(
      <>
        <Field />
        <p>fora</p>
      </>,
    )
    await user.click(screen.getByRole('combobox'))
    await user.click(screen.getByText('fora'))
    expect(screen.queryByRole('listbox')).not.toBeInTheDocument()
  })
})
