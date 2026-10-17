import type { ReactNode, Ref } from 'react'
import { ExternalArrow, isExternal, Link } from '../i18n/link'
import { useT } from '../i18n'
import { Icon, type IconName } from './Icon'
import { Logo } from './Mark'
import { btnDark, btnPrimary, contactTo, h1cls, h2cls, lede, sec, wrap } from '../ui'

export function Sec({
  id,
  tone,
  className = '',
  children,
}: {
  id?: string
  tone?: 'soft' | 'invert'
  className?: string
  children: ReactNode
}) {
  // soft = pale lime (the secondary of the brand reference); invert = near-black block.
  const bg = tone === 'invert' ? 'invert-block' : tone === 'soft' ? 'bg-bg2' : ''
  return (
    <section id={id} className={`${sec} ${bg} ${className}`}>
      <div className={wrap}>{children}</div>
    </section>
  )
}

export const H2 = ({ children }: { children: ReactNode }) => <h2 className={h2cls}>{children}</h2>
export const Lede = ({ children }: { children: ReactNode }) => <p className={lede}>{children}</p>

/** Header of the inner pages: the second half of the headline in a lower tone. */
export function PageHead({ a, b, sub }: { a: string; b: string; sub: string }) {
  return (
    <section className={`${wrap} pt-10 sm:pt-14 lg:pt-20`}>
      <h1 className={h1cls}>
        {a} <span className="text-ink3">{b}</span>
      </h1>
      <p className={`${lede} mt-6`}>{sub}</p>
    </section>
  )
}

// In the order of `facts` in pt.ts/en.ts: sources that feed the memory, neutrality, security, open SDK.
const FACT_ICONS: IconName[] = ['plug', 'hub', 'lock', 'code']

/**
 * The facts strip right below the hero. It has a bottom margin so it does not touch
 * the next section: it closes the hero, it does not open the block below.
 */
export function Facts() {
  const { t } = useT()
  return (
    <div className={`${wrap} mb-14 sm:mb-20 lg:mb-24`}>
      <ul className="grid gap-x-8 gap-y-4 border-y border-line py-5 text-[0.95rem] font-medium text-ink sm:grid-cols-2 xl:flex xl:justify-between xl:py-6">
        {t.facts.map((f, i) => (
          <li key={f} className="flex items-center gap-2.5">
            <Icon name={FACT_ICONS[i]} size={19} className="text-accent" />
            {f}
          </li>
        ))}
      </ul>
    </div>
  )
}

/** A list of guarantees or capabilities: a hairline above each item and the check sign. */
export function Bullets({ items, single }: { items: readonly string[]; single?: boolean }) {
  return (
    <ul className={`grid gap-x-10 gap-y-3 ${single ? '' : 'mt-8 sm:grid-cols-2'}`}>
      {items.map((f) => (
        <li key={f} className="flex items-start gap-3 border-t border-line pt-3 text-[0.96rem] leading-snug text-ink2">
          <Icon name="check" size={16} className="mt-0.5 text-accent [stroke-width:2.4]" />
          {f}
        </li>
      ))}
    </ul>
  )
}

export function Faq({ items }: { items: { q: string; a: string }[] }) {
  return (
    <div className="mt-8 border-t border-line">
      {items.map((f) => (
        <details key={f.q} className="group border-b border-line">
          <summary className="flex min-h-14 cursor-pointer list-none items-center justify-between gap-6 py-3 text-[1.04rem] font-semibold marker:content-none">
            {f.q}
            <span
              className="relative h-3 w-3 shrink-0 before:absolute before:left-0 before:top-1/2 before:h-0.5 before:w-3 before:-translate-y-1/2 before:bg-ink before:content-[''] after:absolute after:left-1/2 after:top-0 after:h-3 after:w-0.5 after:-translate-x-1/2 after:bg-ink after:transition-transform after:duration-150 after:content-[''] group-open:after:scale-y-0"
              aria-hidden="true"
            />
          </summary>
          <p className="max-w-[70ch] pb-6 leading-relaxed text-ink2">{f.a}</p>
        </details>
      ))}
    </div>
  )
}

/** The closing: full lime block, near-black text, one dark button. */
export function FinalCTA({ innerRef }: { innerRef?: Ref<HTMLElement> }) {
  const { t } = useT()
  return (
    <section ref={innerRef} className="brand-block py-14 sm:py-20 lg:py-24">
      <div className={`${wrap} grid gap-8 lg:grid-cols-[1.4fr_1fr] lg:items-end`}>
        <div>
          <h2 className={`${h2cls} max-w-[18ch] !text-[clamp(2rem,7vw,3.4rem)]`}>{t.cta.h2}</h2>
          <p className="mt-4 max-w-[44ch] text-[1.05rem] font-medium">{t.cta.sub}</p>
        </div>
        <div className="lg:justify-self-end">
          <Link className={`${btnDark} w-full sm:w-auto`} to={contactTo()}>{t.cta.primary}</Link>
        </div>
      </div>
    </section>
  )
}

export function Footer() {
  const { t, lang, setLang } = useT()
  const year = new Date().getFullYear()
  return (
    <footer className="invert-block pb-[calc(6rem+env(safe-area-inset-bottom,0px))] pt-12 lg:pb-10">
      <div className={wrap}>
        <div className="grid gap-10 md:grid-cols-[1.3fr_2fr]">
          <div>
            <Link to="/" aria-label="Niadra" className="inline-flex h-11 items-center">
              <Logo onDark />
            </Link>
            <p className="mt-2 max-w-[32ch] text-[0.95rem] text-ink2">{t.footer.tagline}</p>
          </div>
          <nav className="grid grid-cols-2 gap-x-8 gap-y-8 sm:grid-cols-3" aria-label={t.nav.menu}>
            {t.footer.groups.map((g) => (
              <div key={g.head}>
                <h2 className="mb-1 text-[0.82rem] font-semibold text-ink3">{g.head}</h2>
                <ul>
                  {g.links.map((l) => (
                    <li key={l.label}>
                      <Link to={l.to} className="inline-flex min-h-11 min-w-11 items-center gap-1 text-[0.96rem] text-ink2 transition-colors hover:text-accent">
                        {l.label}
                        {isExternal(l.to) && <ExternalArrow to={l.to} />}
                      </Link>
                    </li>
                  ))}
                </ul>
              </div>
            ))}
          </nav>
        </div>
        {/*
          The language is detected on its own (saved choice, device language, time zone). The
          manual switch stays here, discreet, for when the detection is wrong.
        */}
        <div className="mt-10 flex flex-wrap items-center justify-between gap-x-8 gap-y-1.5 border-t border-line pt-4 text-[0.84rem] text-ink3">
          <span>© {year} Niadra. {t.footer.rights}</span>
          <span className="flex flex-wrap items-center gap-x-6 gap-y-1.5">
            <span>{t.footer.note}</span>
            <button
              type="button"
              lang={lang === 'pt' ? 'en' : 'pt-BR'}
              aria-label={t.meta.switchLang}
              onClick={() => setLang(lang === 'pt' ? 'en' : 'pt')}
              className="inline-flex min-h-11 items-center gap-1.5 text-ink2 underline decoration-line underline-offset-4 transition-colors hover:text-ink hover:decoration-accent"
            >
              {t.meta.otherLang}
            </button>
          </span>
        </div>
      </div>
    </footer>
  )
}

/**
 * The mobile action bar. It appears after the hero, goes away when the lime block of
 * the closing comes into view, and the footer reserves its height so nothing is covered.
 */
export function Dock({ show }: { show: boolean }) {
  const { t } = useT()
  return (
    <div
      data-dock
      className={`fixed inset-x-0 bottom-0 z-40 border-t border-line bg-bg px-5 pb-[calc(0.75rem+env(safe-area-inset-bottom,0px))] pt-3 transition-transform duration-200 lg:hidden ${
        show ? 'translate-y-0' : 'translate-y-full'
      }`}
      aria-hidden={!show}
    >
      <Link className={`${btnPrimary} w-full`} to={contactTo()} tabIndex={show ? 0 : -1}>
        {t.nav.cta}
      </Link>
    </div>
  )
}
