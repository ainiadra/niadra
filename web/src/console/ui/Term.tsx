import { useEffect, useId, useLayoutEffect, useRef, useState, type ReactNode } from 'react'
import { createPortal } from 'react-dom'
import { GLOSSARY, type GlossaryKey } from '../lib/glossary'

/**
 * A term of art with its definition one step away: hover or focus shows it, a tap or Enter keeps it open,
 * Escape and leaving close it. The definition is a tooltip the button describes, so a screen reader reads it
 * with the term. It floats in a layer of its own, clamped to the viewport, so a phone never cuts it.
 */
export function Term({ k, children, className = '' }: { k: GlossaryKey; children?: ReactNode; className?: string }) {
  const { term, means } = GLOSSARY[k]
  const id = useId()
  const button = useRef<HTMLButtonElement>(null)
  const [hover, setHover] = useState(false)
  const [pinned, setPinned] = useState(false)
  const [place, setPlace] = useState<{ top: number; left: number; width: number } | null>(null)
  const open = hover || pinned

  useLayoutEffect(() => {
    if (!open) return
    const measure = () => {
      const r = button.current?.getBoundingClientRect()
      if (!r) return
      const width = Math.min(352, window.innerWidth - 16)
      const left = Math.max(8, Math.min(r.left, window.innerWidth - 8 - width))
      setPlace({ top: r.bottom + 6, left, width })
    }
    measure()
    window.addEventListener('resize', measure)
    window.addEventListener('scroll', measure, true)
    return () => {
      window.removeEventListener('resize', measure)
      window.removeEventListener('scroll', measure, true)
    }
  }, [open])

  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        setPinned(false)
        setHover(false)
      }
    }
    const onDown = (e: MouseEvent) => {
      if (!button.current?.contains(e.target as Node)) setPinned(false)
    }
    document.addEventListener('keydown', onKey)
    document.addEventListener('mousedown', onDown)
    return () => {
      document.removeEventListener('keydown', onKey)
      document.removeEventListener('mousedown', onDown)
    }
  }, [open])

  return (
    <>
      <button
        ref={button}
        type="button"
        aria-describedby={open ? id : undefined}
        aria-expanded={open}
        onMouseEnter={() => setHover(true)}
        onMouseLeave={() => setHover(false)}
        onFocus={() => setHover(true)}
        onBlur={() => setHover(false)}
        onClick={() => setPinned((v) => !v)}
        className={`inline cursor-help rounded-sm text-left underline decoration-accent decoration-dotted decoration-[1.5px] underline-offset-4 outline-none focus-visible:outline focus-visible:outline-2 focus-visible:outline-ink ${className}`}
      >
        {children ?? term}
      </button>
      {open &&
        place &&
        createPortal(
          <span
            role="tooltip"
            id={id}
            style={{ position: 'fixed', top: place.top, left: place.left, width: place.width }}
            className="z-[85] block rounded-lg border border-line bg-surface px-3 py-2.5 text-left text-[0.8125rem] leading-5 text-ink shadow-[0_1px_2px_rgba(24,28,18,0.05),0_12px_32px_-16px_rgba(24,28,18,0.35)]"
          >
            <strong className="font-semibold">{term[0]!.toUpperCase() + term.slice(1)}.</strong> {means}
          </span>,
          document.body,
        )}
    </>
  )
}
