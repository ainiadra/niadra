import { useEffect, useId, useRef, type ReactNode, type RefObject } from 'react'
import { Icon } from './Icon'
import { h2 } from './styles'

/**
 * Modal dialog: focus moves in and is trapped, Escape closes, focus returns to
 * the control that opened it. The only layer that floats, so the only shadow.
 * Focus lands on `initialFocus` when given (the field the dialog is for), else on
 * the first control.
 */
export function Dialog({
  open,
  onClose,
  title,
  children,
  wide = false,
  initialFocus,
}: {
  open: boolean
  onClose: () => void
  title: string
  children: ReactNode
  wide?: boolean
  initialFocus?: RefObject<HTMLElement | null>
}) {
  const panel = useRef<HTMLDivElement>(null)
  const titleId = useId()
  const closeRef = useRef(onClose)
  closeRef.current = onClose

  useEffect(() => {
    if (!open) return
    const opener = document.activeElement as HTMLElement | null
    const el = panel.current
    const focusables = () =>
      Array.from(
        el?.querySelectorAll<HTMLElement>('a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])') ?? [],
      )
    ;(initialFocus?.current ?? focusables()[0] ?? el)?.focus()

    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        e.preventDefault()
        closeRef.current()
        return
      }
      if (e.key !== 'Tab') return
      const f = focusables()
      if (f.length === 0) return
      const first = f[0]!
      const last = f[f.length - 1]!
      if (e.shiftKey && document.activeElement === first) {
        e.preventDefault()
        last.focus()
      } else if (!e.shiftKey && document.activeElement === last) {
        e.preventDefault()
        first.focus()
      }
    }
    document.addEventListener('keydown', onKey)
    const root = document.documentElement
    const prevOverflow = root.style.overflow
    root.style.overflow = 'hidden'
    return () => {
      document.removeEventListener('keydown', onKey)
      root.style.overflow = prevOverflow
      opener?.focus?.()
    }
  }, [open])

  if (!open) return null
  return (
    <div className="fixed inset-0 z-[80] flex items-end justify-center bg-ink/45 p-0 sm:items-center sm:p-6" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div
        ref={panel}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        tabIndex={-1}
        className={`max-h-[92dvh] w-full overflow-y-auto rounded-t-xl border border-line bg-surface shadow-[0_1px_2px_rgba(24,28,18,0.05),0_24px_56px_-28px_rgba(24,28,18,0.35)] sm:rounded-xl ${
          wide ? 'sm:max-w-2xl' : 'sm:max-w-lg'
        }`}
      >
        <div className="flex items-start justify-between gap-4 border-b border-line px-5 py-4">
          <h2 id={titleId} className={h2}>
            {title}
          </h2>
          <button type="button" onClick={onClose} className="-m-2 inline-flex h-11 w-11 items-center justify-center rounded-lg text-ink2 hover:bg-bg hover:text-ink lg:h-9 lg:w-9" aria-label="Fechar">
            <Icon name="close" />
          </button>
        </div>
        <div className="px-5 py-5">{children}</div>
      </div>
    </div>
  )
}
