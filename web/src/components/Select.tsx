import { useCallback, useEffect, useId, useLayoutEffect, useRef, useState, type KeyboardEvent, type ReactNode } from 'react'
import { createPortal } from 'react-dom'

export type SelectOption<T extends string> = { value: T; label: string }

/**
 * The one dropdown of the site and the Console, in place of the browser's <select>: the same
 * panel as the menu (rounded, bordered, the only shadow on the page), options that wrap instead of
 * being cut, and a check on the chosen one.
 *
 * It follows the W3C pattern for a select-only combobox (APG): the focus stays on the button and
 * aria-activedescendant points at the option in view. Arrows, Home, End and Page keys move; Enter,
 * Space or a click choose; Escape and a click outside close without choosing; typing jumps to the
 * option that starts with the letters, accents ignored. A <label htmlFor> names it, like any field.
 *
 * The list opens in a layer of its own (a portal with fixed position), so a dialog with its own
 * scroll never cuts it, and it opens upward when there is no room below. Escape inside the list
 * closes the list only, not the dialog around it.
 */
export function Select<T extends string>({
  id,
  value,
  onChange,
  options,
  disabled = false,
  name,
  triggerClassName,
  'aria-describedby': describedBy,
  'aria-invalid': invalid,
}: {
  id?: string
  value: T
  onChange: (value: T) => void
  options: readonly SelectOption<T>[]
  disabled?: boolean
  /** A hidden input with this name carries the value, for forms read with FormData. */
  name?: string
  /** The field look of the place: the Console's input or the site's form field. */
  triggerClassName: string
  'aria-describedby'?: string
  'aria-invalid'?: boolean
}) {
  const own = useId()
  const buttonId = id ?? `${own}-button`
  const listId = `${own}-list`
  const optionId = (i: number) => `${own}-opt-${i}`
  const button = useRef<HTMLButtonElement>(null)
  const list = useRef<HTMLUListElement>(null)
  const [open, setOpen] = useState(false)
  const [active, setActive] = useState(0)
  const [place, setPlace] = useState<{ left: number; top?: number; bottom?: number; minWidth: number; maxHeight: number } | null>(null)
  const typed = useRef({ text: '', at: 0 })

  const selectedIndex = Math.max(
    0,
    options.findIndex((o) => o.value === value),
  )
  const selected = options.find((o) => o.value === value)

  const measure = useCallback(() => {
    const el = button.current
    if (!el) return
    const r = el.getBoundingClientRect()
    const gap = 6
    const below = window.innerHeight - r.bottom - gap - 8
    const above = r.top - gap - 8
    const wanted = Math.min(288, options.length * 44 + 10)
    const up = below < wanted && above > below
    // Wider than the button when an option is long: kept inside the screen on the right.
    const width = Math.max(r.width, list.current?.offsetWidth ?? 0)
    const left = Math.max(8, Math.min(r.left, window.innerWidth - 8 - width))
    setPlace({
      left,
      minWidth: r.width,
      maxHeight: Math.max(120, Math.min(288, up ? above : below)),
      ...(up ? { bottom: window.innerHeight - r.top + gap } : { top: r.bottom + gap }),
    })
  }, [options.length])

  const openList = (at = selectedIndex) => {
    if (disabled || options.length === 0) return
    setActive(at)
    measure()
    setOpen(true)
  }
  const close = () => setOpen(false)
  const choose = (i: number) => {
    const o = options[i]
    if (o && o.value !== value) onChange(o.value)
    close()
    button.current?.focus()
  }

  // While open: follow the button when the page or a dialog scrolls, and close on a click outside.
  useLayoutEffect(() => {
    if (!open) return
    measure()
    const follow = () => measure()
    const outside = (e: PointerEvent) => {
      const t = e.target as Node
      if (!button.current?.contains(t) && !list.current?.contains(t)) close()
    }
    window.addEventListener('resize', follow)
    window.addEventListener('scroll', follow, true)
    document.addEventListener('pointerdown', outside)
    return () => {
      window.removeEventListener('resize', follow)
      window.removeEventListener('scroll', follow, true)
      document.removeEventListener('pointerdown', outside)
    }
  }, [open, measure])

  // The option in view stays visible when the arrows run past the edge of the list.
  useEffect(() => {
    if (open) document.getElementById(`${own}-opt-${active}`)?.scrollIntoView?.({ block: 'nearest' })
  }, [open, active, own])

  const jump = (key: string) => {
    const now = Date.now()
    const text = (now - typed.current.at > 700 ? '' : typed.current.text) + key
    typed.current = { text, at: now }
    const plain = (s: string) => s.normalize('NFD').replace(/\p{M}/gu, '').toLowerCase()
    const from = open ? active : selectedIndex
    const order = [...options.keys()].map((k) => (from + (text.length === 1 ? 1 : 0) + k) % options.length)
    const hit = order.find((i) => plain(options[i]!.label).startsWith(plain(text)))
    if (hit === undefined) return
    if (open) setActive(hit)
    else openList(hit)
  }

  const onKeyDown = (e: KeyboardEvent<HTMLButtonElement>) => {
    const last = options.length - 1
    const move = (to: number) => {
      e.preventDefault()
      if (!open) openList(Math.min(last, Math.max(0, to)))
      else setActive(Math.min(last, Math.max(0, to)))
    }
    switch (e.key) {
      case 'ArrowDown':
        return e.altKey ? (e.preventDefault(), openList()) : move(open ? active + 1 : selectedIndex)
      case 'ArrowUp':
        return move(open ? active - 1 : selectedIndex)
      case 'Home':
        return move(0)
      case 'End':
        return move(last)
      case 'PageDown':
        return open ? move(active + 10) : undefined
      case 'PageUp':
        return open ? move(active - 10) : undefined
      case 'Enter':
      case ' ':
        e.preventDefault()
        if (open) choose(active)
        else openList()
        return
      case 'Escape':
        if (open) {
          // The list closes; the dialog around it stays.
          e.preventDefault()
          e.stopPropagation()
          close()
        }
        return
      case 'Tab':
        if (open) close()
        return
      default:
        if (e.key.length === 1 && !e.ctrlKey && !e.metaKey && !e.altKey) {
          e.preventDefault()
          jump(e.key)
        }
    }
  }

  return (
    <>
      <button
        ref={button}
        id={buttonId}
        type="button"
        role="combobox"
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-controls={open ? listId : undefined}
        aria-activedescendant={open ? optionId(active) : undefined}
        aria-describedby={describedBy}
        aria-invalid={invalid || undefined}
        disabled={disabled}
        onClick={() => (open ? close() : openList())}
        onKeyDown={onKeyDown}
        className={`${triggerClassName} flex items-center justify-between gap-2 text-left disabled:cursor-not-allowed`}
      >
        <span className="min-w-0 truncate" title={selected?.label}>
          {selected?.label ?? ''}
        </span>
        <Chevron up={open} />
      </button>
      {name && <input type="hidden" name={name} value={value} />}
      {open &&
        place &&
        createPortal(
          <ul
            ref={list}
            id={listId}
            role="listbox"
            aria-labelledby={buttonId}
            tabIndex={-1}
            style={{ position: 'fixed', left: place.left, top: place.top, bottom: place.bottom, minWidth: place.minWidth, maxHeight: place.maxHeight }}
            className="z-[90] w-max max-w-[min(28rem,calc(100vw-16px))] overflow-y-auto overscroll-contain rounded-xl border border-line bg-surface p-1 shadow-[0_1px_2px_rgba(24,28,18,0.05),0_16px_40px_-20px_rgba(24,28,18,0.22)]"
          >
            {options.map((o, i) => {
              const isSelected = o.value === value
              return (
                <li
                  key={o.value}
                  id={optionId(i)}
                  role="option"
                  aria-selected={isSelected}
                  data-value={o.value}
                  onMouseDown={(e) => e.preventDefault()}
                  onMouseMove={() => active !== i && setActive(i)}
                  onClick={() => choose(i)}
                  className={`flex min-h-11 cursor-pointer items-center justify-between gap-3 rounded-lg px-3 py-2 text-[0.9375rem] leading-snug text-ink lg:min-h-9 lg:text-[0.875rem] ${
                    i === active ? 'bg-bg2' : ''
                  } ${isSelected ? 'font-semibold' : ''}`}
                >
                  <span>{o.label}</span>
                  <span aria-hidden="true" className={`shrink-0 text-accent ${isSelected ? '' : 'invisible'}`}>
                    <Check />
                  </span>
                </li>
              )
            })}
          </ul>,
          document.body,
        )}
    </>
  )
}

function Chevron({ up }: { up: boolean }): ReactNode {
  return (
    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" aria-hidden="true" className={`shrink-0 text-ink2 transition-transform duration-150 ${up ? 'rotate-180' : ''}`}>
      <path d="M5 9l7 7 7-7" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  )
}

function Check(): ReactNode {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none">
      <path d="M4.5 12.5l5 5L20 7" stroke="currentColor" strokeWidth="2.6" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  )
}
