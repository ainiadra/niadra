import { useEffect, useId, useRef, useState, type KeyboardEvent as ReactKeyboardEvent } from 'react'
import { Link as RouterLink, useLocation } from 'react-router-dom'
import { ExternalArrow, isExternal, Link } from '../i18n/link'
import { useT } from '../i18n'
import { Icon, type IconName } from './Icon'
import { Logo } from './Mark'
import { GROUPS, productBySlug, productPath } from '../products'
import { btnGhost, btnPrimary, btnSm, contactTo, iconBtn, wrap } from '../ui'

const GITHUB_URL = 'https://github.com/ainiadra'

/** GitHub's mark, the shape GitHub publishes for links to it; the SDKs and the benchmark live there. */
function GitHubMark() {
  return (
    <svg width="20" height="20" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
      <path d="M12 .5a11.5 11.5 0 0 0-3.64 22.41c.58.1.79-.25.79-.56v-2c-3.2.7-3.88-1.37-3.88-1.37-.53-1.34-1.29-1.7-1.29-1.7-1.05-.72.08-.7.08-.7 1.16.08 1.77 1.19 1.77 1.19 1.03 1.77 2.71 1.26 3.37.96.1-.75.4-1.26.73-1.55-2.55-.29-5.24-1.28-5.24-5.69 0-1.26.45-2.28 1.19-3.09-.12-.29-.52-1.46.11-3.05 0 0 .97-.31 3.17 1.18a11 11 0 0 1 5.77 0c2.2-1.49 3.17-1.18 3.17-1.18.63 1.59.23 2.76.11 3.05.74.81 1.19 1.83 1.19 3.09 0 4.42-2.7 5.39-5.26 5.68.41.36.78 1.06.78 2.14v3.17c0 .31.21.67.8.56A11.5 11.5 0 0 0 12 .5Z" />
    </svg>
  )
}

function Chevron({ open, className = '' }: { open?: boolean; className?: string }) {
  return (
    <svg width="11" height="11" viewBox="0 0 24 24" fill="none" aria-hidden="true" className={`transition-transform duration-150 ${open ? 'rotate-180' : ''} ${className}`}>
      <path d="M5 9l7 7 7-7" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  )
}

type Item = { title: string; desc: string; to: string; icon?: IconName }
type Group = { key: string; label: string; note: string; items: Item[] }

const navLink =
  'inline-flex h-11 items-center rounded-lg px-3 text-[0.96rem] font-medium text-ink2 transition-colors hover:bg-bg2 hover:text-ink'

/** Shared by both dropdowns: open on hover, click and keyboard; close on Escape, on a click outside and on blur. */
function useDropdown() {
  const [open, setOpen] = useState(false)
  // A tap on a tablet fires the hover and the click together: the click that arrives after
  // the hover keeps the panel open, instead of closing what the hover has just opened.
  const hovered = useRef(false)
  const box = useRef<HTMLDivElement>(null)
  const btn = useRef<HTMLButtonElement>(null)

  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        setOpen(false)
        btn.current?.focus()
      }
    }
    const onDown = (e: PointerEvent) => {
      if (!box.current?.contains(e.target as Node)) setOpen(false)
    }
    window.addEventListener('keydown', onKey)
    window.addEventListener('pointerdown', onDown)
    return () => {
      window.removeEventListener('keydown', onKey)
      window.removeEventListener('pointerdown', onDown)
    }
  }, [open])

  const boxProps = {
    ref: box,
    onMouseEnter: () => {
      hovered.current = true
      setOpen(true)
    },
    onMouseLeave: () => {
      hovered.current = false
      setOpen(false)
    },
    onBlur: (e: React.FocusEvent) => {
      if (!box.current?.contains(e.relatedTarget as Node)) setOpen(false)
    },
  }
  const onClick = () => {
    if (hovered.current) {
      hovered.current = false
      setOpen(true)
    } else setOpen((v) => !v)
  }
  return { open, setOpen, box, btn, boxProps, onClick }
}

/** The simple dropdown (Resources): one column of links. */
function Dropdown({ label, items }: { label: string; items: Item[] }) {
  const d = useDropdown()
  const panelId = useId()
  return (
    <div className="relative" {...d.boxProps}>
      <button ref={d.btn} type="button" className={`${navLink} gap-1.5`} aria-expanded={d.open} aria-controls={panelId} onClick={d.onClick}>
        {label}
        <Chevron open={d.open} />
      </button>
      <div id={panelId} hidden={!d.open} className="absolute left-0 top-full z-10 w-[360px] pt-2">
        {/* The shadow here has a reason: it is the only layer that floats over the page. */}
        <div className="rounded-xl border border-line bg-surface p-2 shadow-[0_1px_2px_rgba(24,28,18,0.05),0_16px_40px_-20px_rgba(24,28,18,0.22)]">
          <div className="grid gap-0.5">
            {items.map((i) => (
              <Link key={i.title} to={i.to} onClick={() => d.setOpen(false)} className="rounded-lg px-3 py-2.5 transition-colors hover:bg-bg2">
                <span className="flex items-center gap-1.5 text-[0.96rem] font-semibold text-ink">
                  {i.title}
                  {isExternal(i.to) && <ExternalArrow to={i.to} />}
                </span>
                <span className="mt-0.5 block text-[0.86rem] leading-snug text-ink2">{i.desc}</span>
              </Link>
            ))}
          </div>
        </div>
      </div>
    </div>
  )
}

/**
 * The products menu, in two panes (founder, 01/10/2026: thirteen products in one list cut off on
 * a laptop screen). The four groups stand on the left, in the order of the data path; the products
 * of the group under the pointer, or the one chosen with the keyboard, open on the right. The panel
 * is as tall as the longest group, five products, so it never runs past a 720 px screen.
 *
 * Keyboard: the group buttons are a tablist (Up and Down move between groups, Right or Enter goes to
 * the first product, Left comes back), Escape closes and returns the focus to the trigger.
 */
function ProductsMenu({ label, groups, all }: { label: string; groups: Group[]; all: { label: string; to: string } }) {
  const { t } = useT()
  const d = useDropdown()
  const panelId = useId()
  const [active, setActive] = useState(0)
  const tabs = useRef<(HTMLButtonElement | null)[]>([])
  const pane = useRef<HTMLDivElement>(null)
  // The product links of the open group, in order: the site's Link takes no ref, so the pane is queried.
  const linkAt = (n: number) => pane.current?.querySelectorAll<HTMLAnchorElement>('a')[n]

  const focusTab = (n: number) => {
    const next = (n + groups.length) % groups.length
    setActive(next)
    tabs.current[next]?.focus()
  }
  const onTabKey = (e: ReactKeyboardEvent, n: number) => {
    if (e.key === 'ArrowDown') {
      e.preventDefault()
      focusTab(n + 1)
    } else if (e.key === 'ArrowUp') {
      e.preventDefault()
      focusTab(n - 1)
    } else if (e.key === 'ArrowRight' || e.key === 'Enter' || e.key === ' ') {
      e.preventDefault()
      setActive(n)
      // The pane of the group renders on the next frame when the group changes; the focus waits for it.
      window.setTimeout(() => linkAt(0)?.focus(), 0)
    } else if (e.key === 'Home') {
      e.preventDefault()
      focusTab(0)
    } else if (e.key === 'End') {
      e.preventDefault()
      focusTab(groups.length - 1)
    }
  }
  const onLinkKey = (e: ReactKeyboardEvent, n: number) => {
    const count = groups[active].items.length
    if (e.key === 'ArrowDown') {
      e.preventDefault()
      linkAt((n + 1) % count)?.focus()
    } else if (e.key === 'ArrowUp') {
      e.preventDefault()
      linkAt((n - 1 + count) % count)?.focus()
    } else if (e.key === 'ArrowLeft') {
      e.preventDefault()
      tabs.current[active]?.focus()
    }
  }
  const onTriggerKey = (e: ReactKeyboardEvent) => {
    if (e.key === 'ArrowDown') {
      e.preventDefault()
      d.setOpen(true)
      // The panel renders on the next frame; the focus waits for it.
      window.setTimeout(() => tabs.current[active]?.focus(), 0)
    }
  }

  const group = groups[active]
  return (
    <div className="relative" {...d.boxProps}>
      <button ref={d.btn} type="button" className={`${navLink} gap-1.5`} aria-expanded={d.open} aria-controls={panelId} aria-haspopup="true" onClick={d.onClick} onKeyDown={onTriggerKey}>
        {label}
        <Chevron open={d.open} />
      </button>

      <div id={panelId} hidden={!d.open} className="absolute left-0 top-full z-10 w-[640px] pt-2">
        <div className="overflow-hidden rounded-xl border border-line bg-surface shadow-[0_1px_2px_rgba(24,28,18,0.05),0_16px_40px_-20px_rgba(24,28,18,0.22)]">
          <div className="grid grid-cols-[14.5rem_minmax(0,1fr)]">
            {/* Left: the four groups. The active one is marked by the lime rail and the pale background. */}
            <div role="tablist" aria-label={t.nav.groupsLabel} aria-orientation="vertical" className="border-r border-line bg-bg p-2">
              {groups.map((g, n) => {
                const on = n === active
                return (
                  <button
                    key={g.key}
                    ref={(el) => {
                      tabs.current[n] = el
                    }}
                    type="button"
                    role="tab"
                    id={`${panelId}-tab-${g.key}`}
                    aria-selected={on}
                    aria-controls={`${panelId}-panel-${g.key}`}
                    tabIndex={on ? 0 : -1}
                    onMouseEnter={() => setActive(n)}
                    onFocus={() => setActive(n)}
                    onClick={() => setActive(n)}
                    onKeyDown={(e) => onTabKey(e, n)}
                    className={`flex w-full items-center justify-between gap-3 rounded-lg border-l-2 px-3 py-2.5 text-left transition-colors ${
                      on ? 'border-brand bg-bg2 text-ink' : 'border-transparent text-ink2 hover:bg-bg2 hover:text-ink'
                    }`}
                  >
                    <span className="min-w-0">
                      <span className="block text-[0.96rem] font-semibold">{g.label}</span>
                      <span className="mt-0.5 block text-[0.82rem] leading-snug text-ink2">{g.note}</span>
                    </span>
                    <Chevron className="shrink-0 -rotate-90 text-ink3" />
                  </button>
                )
              })}
              <Link to={all.to} onClick={() => d.setOpen(false)} className="mt-1 flex min-h-11 items-center rounded-lg border-t border-line px-3 text-[0.92rem] font-semibold text-ink transition-colors hover:bg-bg2">
                {all.label}
              </Link>
            </div>

            {/* Right: the products of the active group. Only one group is in the DOM at a time. */}
            <div ref={pane} role="tabpanel" id={`${panelId}-panel-${group.key}`} aria-labelledby={`${panelId}-tab-${group.key}`} className="p-2">
              {group.items.map((i, n) => (
                <Link
                  key={i.title}
                  to={i.to}
                  onClick={() => d.setOpen(false)}
                  onKeyDown={(e) => onLinkKey(e, n)}
                  className="flex gap-3 rounded-lg px-3 py-2.5 transition-colors hover:bg-bg2"
                >
                  {i.icon && <Icon name={i.icon} size={20} className="mt-0.5 shrink-0 text-accent" />}
                  <span className="min-w-0">
                    <span className="block text-[0.96rem] font-semibold text-ink">{i.title}</span>
                    <span className="mt-0.5 block text-[0.86rem] leading-snug text-ink2">{i.desc}</span>
                  </span>
                </Link>
              ))}
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}

/** On the phone, each product group folds: the label opens its products, so the list never runs past the screen. */
function MobileGroup({ group, row }: { group: Group; row: string }) {
  const [open, setOpen] = useState(false)
  const id = useId()
  return (
    <div>
      <button type="button" aria-expanded={open} aria-controls={id} onClick={() => setOpen((v) => !v)} className={`${row} w-full justify-between`}>
        <span>
          {group.label}
          <span className="ml-2 text-[0.86rem] font-normal text-ink2">{group.note}</span>
        </span>
        <Chevron open={open} />
      </button>
      <div id={id} hidden={!open} className="border-b border-line pb-2 pl-4">
        {group.items.map((i) => (
          <Link key={i.title} to={i.to} className="flex min-h-11 items-center gap-2 text-[1rem] text-ink">
            {i.icon && <Icon name={i.icon} size={18} className="shrink-0 text-accent" />}
            {i.title}
          </Link>
        ))}
      </div>
    </div>
  )
}

export function Nav({ scrolled }: { scrolled: boolean }) {
  const { t } = useT()
  const [open, setOpen] = useState(false)
  const loc = useLocation()
  const menuBtn = useRef<HTMLButtonElement>(null)

  useEffect(() => setOpen(false), [loc.pathname, loc.hash])

  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        setOpen(false)
        menuBtn.current?.focus()
      }
    }
    window.addEventListener('keydown', onKey)
    const root = document.documentElement
    const prev = root.style.overflow
    root.style.overflow = 'hidden'
    return () => {
      window.removeEventListener('keydown', onKey)
      root.style.overflow = prev
    }
  }, [open])

  // The menu's products, in catalog order: each one leads to its page.
  const products: Group[] = GROUPS.map((g) => ({
    key: g.key,
    label: t.products.groups[g.key].label,
    note: t.products.groups[g.key].note,
    items: g.slugs.map((slug) => {
      const p = productBySlug(slug)!
      const c = t.products[p.key]
      return { title: c.name, desc: c.menu, to: productPath(slug), icon: p.icon }
    }),
  }))
  const all = { label: t.nav.allProducts, to: '/produtos' }
  const row = 'flex min-h-12 items-center gap-2 border-b border-line text-[1.05rem] font-medium text-ink'

  return (
    <header
      className={`sticky top-0 z-50 border-b bg-bg/95 backdrop-blur-sm pt-[env(safe-area-inset-top,0px)] transition-colors ${
        scrolled || open ? 'border-line' : 'border-transparent'
      }`}
    >
      <div className={`${wrap} flex h-16 items-center justify-between gap-4`}>
        <Link to="/" aria-label="Niadra" className="inline-flex h-11 items-center">
          <Logo />
        </Link>

        <nav className="hidden items-center gap-0.5 lg:flex" aria-label={t.nav.menu}>
          <ProductsMenu label={t.nav.products} groups={products} all={all} />
          <Dropdown label={t.nav.resources} items={t.nav.resourceItems} />
          <Link to="/preco" className={navLink}>{t.nav.pricing}</Link>
          <Link to="/blog" className={navLink}>{t.nav.blog}</Link>
        </nav>

        {/* Login leads to the Console, which has no /en version: a link without a language prefix. */}
        <div className="flex items-center gap-2 max-lg:hidden">
          <a className={`${iconBtn} border-transparent bg-transparent px-0 text-ink2 hover:text-ink`} href={GITHUB_URL} target="_blank" rel="noopener noreferrer" aria-label={t.nav.github}>
            <GitHubMark />
          </a>
          <RouterLink className={`${btnGhost} ${btnSm}`} to="/console">{t.nav.login}</RouterLink>
          <Link className={`${btnPrimary} ${btnSm}`} to={contactTo()}>{t.nav.cta}</Link>
        </div>

        {/* On mobile the menu has a label: a silent hamburger hides the navigation. */}
        <button ref={menuBtn} type="button" className={`${iconBtn} lg:hidden`} aria-expanded={open} aria-controls="mobile-menu" onClick={() => setOpen((v) => !v)}>
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" aria-hidden="true">
            {open ? (
              <path d="M5 5l14 14M19 5L5 19" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" />
            ) : (
              <path d="M3.5 7h17M3.5 12h17M3.5 17h17" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" />
            )}
          </svg>
          <span className="text-[0.92rem] font-semibold">{open ? t.nav.close : t.nav.menu}</span>
        </button>
      </div>

      {/*
        The menu fills the whole screen below the header. This way it covers the fixed
        action bar at the bottom, and there is only one "Request early access" on screen: the menu's.
      */}
      {open && (
        <div
          id="mobile-menu"
          className="h-[calc(100dvh-4rem-env(safe-area-inset-top,0px))] overflow-y-auto border-t border-line bg-surface lg:hidden"
        >
          <nav className={`${wrap} flex min-h-full flex-col gap-6 pb-[calc(1.25rem+env(safe-area-inset-bottom,0px))] pt-5`} aria-label={t.nav.menu}>
            <div>
              <h2 className="mb-1 text-[0.82rem] font-semibold text-ink">{t.nav.products}</h2>
              {products.map((g) => (
                <MobileGroup key={g.key} group={g} row={row} />
              ))}
              <Link to={all.to} className={row}>{all.label}</Link>
            </div>
            <div>
              <h2 className="mb-1 text-[0.82rem] font-semibold text-ink">{t.nav.resources}</h2>
              {t.nav.resourceItems.map((i) => (
                <Link key={i.title} to={i.to} className={row}>
                  {i.title}
                  {isExternal(i.to) && <ExternalArrow to={i.to} />}
                </Link>
              ))}
            </div>
            <div>
              <Link to="/preco" className={row}>{t.nav.pricing}</Link>
              <Link to="/blog" className={row}>{t.nav.blog}</Link>
            </div>

            <div className="mt-auto grid gap-3">
              <a className={`${btnGhost} w-full gap-2`} href={GITHUB_URL} target="_blank" rel="noopener noreferrer" aria-label={t.nav.github}>
                <GitHubMark />
                GitHub
              </a>
              <RouterLink className={`${btnGhost} w-full`} to="/console">{t.nav.login}</RouterLink>
              <Link className={`${btnPrimary} w-full`} to={contactTo()}>{t.nav.cta}</Link>
            </div>
          </nav>
        </div>
      )}
    </header>
  )
}
