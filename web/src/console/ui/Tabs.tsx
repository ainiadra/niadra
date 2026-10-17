import { useId, useRef, type KeyboardEvent, type ReactNode } from 'react'

/** ARIA tabs: arrows move between tabs, Home and End jump, only the active tab is in the Tab order. */
export function Tabs<K extends string>({
  label,
  tabs,
  active,
  onChange,
  children,
}: {
  label: string
  tabs: { key: K; label: string }[]
  active: K
  onChange: (k: K) => void
  children: ReactNode
}) {
  const base = useId()
  const refs = useRef<(HTMLButtonElement | null)[]>([])

  const onKey = (e: KeyboardEvent, i: number) => {
    let next = -1
    if (e.key === 'ArrowRight') next = (i + 1) % tabs.length
    else if (e.key === 'ArrowLeft') next = (i - 1 + tabs.length) % tabs.length
    else if (e.key === 'Home') next = 0
    else if (e.key === 'End') next = tabs.length - 1
    if (next < 0) return
    e.preventDefault()
    onChange(tabs[next]!.key)
    refs.current[next]?.focus()
  }

  return (
    <div>
      <div role="tablist" aria-label={label} className="-mx-1 flex gap-1 overflow-x-auto border-b border-line px-1">
        {tabs.map((t, i) => {
          const on = t.key === active
          return (
            <button
              key={t.key}
              ref={(el) => {
                refs.current[i] = el
              }}
              role="tab"
              type="button"
              id={`${base}-tab-${t.key}`}
              aria-selected={on}
              aria-controls={`${base}-panel`}
              tabIndex={on ? 0 : -1}
              onClick={() => onChange(t.key)}
              onKeyDown={(e) => onKey(e, i)}
              className={`-mb-px inline-flex h-11 shrink-0 items-center whitespace-nowrap border-b-2 px-3 text-[0.875rem] font-semibold transition-colors lg:h-10 ${
                on ? 'border-ink text-ink' : 'border-transparent text-ink2 hover:text-ink'
              }`}
            >
              {t.label}
            </button>
          )
        })}
      </div>
      <div role="tabpanel" id={`${base}-panel`} aria-labelledby={`${base}-tab-${active}`} className="pt-5">
        {children}
      </div>
    </div>
  )
}
