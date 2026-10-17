import { Link } from '../i18n/link'
import { useT } from '../i18n'
import { Faq, H2, PageHead, Sec } from '../components/Shell'
import { btnGhost, btnPrimary, card, contactTo, display, h3cls, panel } from '../ui'

function Checks({ items }: { items: string[] }) {
  return (
    <ul className="grid content-start gap-2.5">
      {items.map((i) => (
        <li key={i} className="flex gap-3 text-[0.96rem] text-ink2">
          <span className="mt-0.5 flex h-[18px] w-[18px] shrink-0 items-center justify-center rounded-full bg-brand text-on-brand">
            <svg width="10" height="10" viewBox="0 0 24 24" fill="none" aria-hidden="true">
              <path d="M4 12.5l5 5L20 6.5" stroke="currentColor" strokeWidth="3.2" strokeLinecap="round" strokeLinejoin="round" />
            </svg>
          </span>
          {i}
        </li>
      ))}
    </ul>
  )
}

/**
 * Two plans, because there are two, and both fully managed by Niadra:
 * Production with isolation per company, Regulated with a dedicated environment. No
 * forcing three columns with the middle one highlighted. Both lead to the early-access
 * request, with the interest already selected.
 */
export function Pricing() {
  const { t } = useT()
  const p = t.pricing

  return (
    <>
      <PageHead a={p.h1a} b={p.h1b} sub={p.sub} />

      <Sec>
        <div className="grid gap-6 md:grid-cols-2">
          {p.plans.map((pl, i) => (
            <div key={pl.name} className={`${panel} flex flex-col p-6 sm:p-8`}>
              <div className="flex flex-wrap items-baseline justify-between gap-x-6 gap-y-1">
                <h2 className={`${h3cls} text-[1.3rem]`}>{pl.name}</h2>
                <div className={`${display} text-[clamp(1.7rem,5vw,2.2rem)] leading-none tabular-nums`}>{pl.price}</div>
              </div>
              <p className="mt-2 text-[0.92rem] text-ink3">{pl.unit}</p>
              <p className="mt-5 text-[1.04rem] leading-relaxed text-ink2">{pl.blurb}</p>
              <div className="mt-6 flex-1">
                <Checks items={pl.items} />
              </div>
              <Link className={`${i === 0 ? btnPrimary : btnGhost} mt-8 w-full`} to={contactTo(pl.interest)}>
                {pl.cta}
              </Link>
            </div>
          ))}
        </div>
      </Sec>

      {/* On mobile the table becomes one block per row: no sideways scrolling. */}
      <Sec tone="soft">
        <H2>{p.tableH2}</H2>
        <div className={`${card} mt-8 overflow-hidden`}>
          <div className="hidden grid-cols-[1.2fr_1fr_1fr] gap-4 border-b border-line bg-surface px-5 py-3 text-[0.88rem] font-semibold text-ink md:grid">
            <span />
            {p.tableCols.map((c) => (
              <span key={c}>{c}</span>
            ))}
          </div>
          {p.rows.map((r) => (
            <div key={r.label} className="border-b border-line px-5 py-4 last:border-b-0 md:grid md:grid-cols-[1.2fr_1fr_1fr] md:items-baseline md:gap-4">
              <div className="text-[0.96rem] font-semibold text-ink">{r.label}</div>
              {r.v.map((v, i) => (
                <div key={p.tableCols[i]} className="mt-1.5 flex justify-between gap-4 text-[0.93rem] md:mt-0 md:block">
                  <span className="text-ink3 md:hidden">{p.tableCols[i]}</span>
                  <span className="text-right text-ink2 md:text-left">{v}</span>
                </div>
              ))}
            </div>
          ))}
        </div>
      </Sec>

      <Sec>
        <H2>{p.faqH2}</H2>
        <Faq items={p.faq} />
      </Sec>
    </>
  )
}
