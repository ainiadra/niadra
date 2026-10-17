import { Link } from '../i18n/link'
import { useT } from '../i18n'
import { Architecture, SourcesFlow } from '../components/Diagrams'
import { Icon, IconChip, type IconName } from '../components/Icon'
import { PlatformMap, ProductLinks } from '../components/Platform'
import { Stage } from '../components/Stage'
import { Facts, Faq, H2, Lede, Sec } from '../components/Shell'
import { btnGhost, btnPrimary, contactTo, display, h1cls, h3cls, lede, textLink, wrap } from '../ui'

// In the order of `home.secItems` in pt.ts/en.ts.
const SEC_ICONS: IconName[] = ['lock', 'key', 'target', 'eye', 'doc', 'globe']

// In the order of `home.solutions`: customer service (channels that become one), operations
// (connected systems), agent management (the measurement) and security.
const SOLUTION_ICONS: IconName[] = ['hub', 'plug', 'chart', 'shield']

/**
 * The company page: the view of the whole. The hero states the category (the shared
 * omnichannel memory for every AI agent), the stage shows the effect (events from four
 * sources became one context), the flow shows the mechanism (many sources, one memory,
 * every agent), each team sees what it gains, the engine shows how it works, speed and
 * cost come as proof, and each product has its own page, in the map at the end.
 * What used to be the home became the Memory page.
 */
export function Home() {
  const { t } = useT()
  const h = t.home

  return (
    <>
      <section className={`${wrap} pb-12 pt-8 sm:pt-12 lg:pb-16 lg:pt-14`}>
        <div className="grid items-center gap-10 lg:grid-cols-[minmax(0,0.9fr)_minmax(0,1.1fr)] lg:gap-12">
          <div>
            <h1 className={h1cls}>
              {h.h1a} <span className="text-ink3">{h.h1b}</span>
            </h1>
            <p className={`${lede} mt-6`}>{h.sub}</p>
            <div className="mt-8 grid gap-3 sm:flex sm:flex-wrap">
              <Link className={btnPrimary} to={contactTo()}>{h.primary}</Link>
              <a className={btnGhost} href="#solucoes">{h.secondary}</a>
            </div>
          </div>
          <Stage />
        </div>
      </section>

      <Facts />

      {/* The problem in the buyer's words, before any mechanism: three causes in one sentence each, and the line that closes them. */}
      <Sec id="problema" className="!pt-0">
        <H2>{h.problemH2}</H2>
        <Lede>{h.problemSub}</Lede>
        <ul className="mt-9 grid gap-x-10 gap-y-7 border-t border-edge pt-7 sm:grid-cols-3">
          {h.problems.map((p) => (
            <li key={p.t}>
              <h3 className={`${h3cls} text-[1.12rem] leading-snug text-ink`}>{p.t}</h3>
              <p className="mt-2 text-[0.96rem] leading-relaxed text-ink2">{p.d}</p>
            </li>
          ))}
        </ul>
        <p className={`${display} mt-10 max-w-[30ch] text-[clamp(1.35rem,4.4vw,1.95rem)] leading-[1.1]`}>{h.problemClose}</p>
      </Sec>

      {/* The positioning in one picture: the sources that send events, the one memory Niadra builds, the agents that read it. */}
      <Sec id="fontes" tone="soft">
        <H2>{h.flowH2}</H2>
        <Lede>{h.flowSub}</Lede>
        <SourcesFlow />
      </Sec>

      {/* One front per row: who gains, the sentence that sums up the gain and the products that deliver it. */}
      <Sec id="solucoes">
        <H2>{h.solH2}</H2>
        <Lede>{h.solSub}</Lede>
        <div className="mt-10 border-t border-edge">
          {h.solutions.map((s, i) => (
            <div key={s.team} className="grid gap-4 border-b border-edge py-7 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.15fr)] lg:gap-12">
              <div>
                <div className="flex items-center gap-2 text-[0.9rem] font-semibold text-ink2">
                  <Icon name={SOLUTION_ICONS[i]} size={18} className="text-accent" />
                  {s.team}
                </div>
                <h3 className={`${display} mt-2 text-[clamp(1.35rem,4.4vw,1.95rem)] leading-[1.1]`}>{s.h}</h3>
              </div>
              <div>
                <p className="leading-relaxed text-ink2">{s.d}</p>
                <div className="mt-4">
                  <ProductLinks slugs={s.products} />
                </div>
              </div>
            </div>
          ))}
        </div>
      </Sec>

      <Sec id="motor" tone="soft">
        <H2>{h.howH2}</H2>
        <Lede>{h.howSub}</Lede>
        <Architecture />
      </Sec>

      {/* Positioning by category, without naming a competitor. */}
      <Sec>
        <H2>{h.fitH2}</H2>
        <Lede>{h.fitSub}</Lede>
        <div className="mt-10 border-t border-edge">
          <div className="hidden grid-cols-[0.9fr_1.2fr_1.2fr] gap-8 border-b border-edge py-3 text-[0.86rem] font-semibold text-ink2 lg:grid">
            <span />
            <span>{h.fitThem}</span>
            <span>{h.fitUs}</span>
          </div>
          {h.fitRows.map((r) => (
            <div key={r.who} className="grid gap-3 border-b border-edge py-6 lg:grid-cols-[0.9fr_1.2fr_1.2fr] lg:gap-8">
              <h3 className={`${h3cls} text-[1.14rem]`}>{r.who}</h3>
              <div>
                <span className="mb-1 block text-[0.82rem] font-semibold text-ink3 lg:hidden">{h.fitThem}</span>
                <p className="leading-relaxed text-ink2">{r.them}</p>
              </div>
              <div>
                <span className="mb-1 block text-[0.82rem] font-semibold text-ink3 lg:hidden">{h.fitUs}</span>
                <p className="font-semibold leading-relaxed text-ink">{r.us}</p>
              </div>
            </div>
          ))}
        </div>
      </Sec>

      {/* Neutrality in large type: it is what lets Niadra sit above every vendor. */}
      <Sec tone="invert">
        <H2>{h.neutralH2}</H2>
        <Lede>{h.neutralSub}</Lede>
        <ul className="mt-10 border-t border-edge">
          {h.neutralItems.map((i) => (
            <li key={i.big} className="grid gap-2 border-b border-edge py-6 lg:grid-cols-[1.2fr_1.3fr] lg:items-baseline lg:gap-12">
              <span className={`${display} text-[clamp(1.35rem,4.8vw,2.15rem)] leading-[1.1]`}>{i.big}</span>
              <span className="leading-relaxed text-ink2">{i.small}</span>
            </li>
          ))}
        </ul>
      </Sec>

      {/* Speed, cost and integration as proof that the layer is well made, after the positioning, never before it. */}
      <Sec tone="soft">
        <H2>{h.perfH2}</H2>
        <Lede>{h.perfSub}</Lede>
        <div className="mt-10 grid gap-8 sm:grid-cols-2 sm:gap-6 lg:grid-cols-4 lg:gap-8">
          {h.perf.map((n) => (
            <div key={n.label} className="border-t border-edge pt-5">
              <div className={`${display} text-[clamp(3rem,11vw,4.4rem)] leading-[0.9] tabular-nums`}>
                {n.n}
                {n.unit && <span className="ml-1 text-[0.32em] font-semibold tracking-normal">{n.unit}</span>}
              </div>
              <p className="mt-4 max-w-[30ch] text-[0.96rem] leading-relaxed text-ink2">{n.label}</p>
            </div>
          ))}
        </div>
        <p className="mt-10 max-w-[72ch] border-t border-edge pt-5 text-[0.96rem] leading-relaxed text-ink2">{h.perfNote}</p>
        {/* The benchmark is Niadra's own; the answer to that is reproducibility, said where the numbers are. */}
        <Link to="/benchmark" className={`${textLink} mt-4`}>{h.perfLink}</Link>
      </Sec>

      {/* What the buyer's security and compliance team needs to read. */}
      <Sec id="seguranca">
        <H2>{h.secH2}</H2>
        <Lede>{h.secSub}</Lede>

        {/* The badges: the standard or the regulation by name, with the shield in front. */}
        <ul className="mt-7 flex flex-wrap gap-2">
          {h.secBadges.map((b) => (
            <li key={b} className="inline-flex items-center gap-1.5 rounded-md border border-line bg-surface px-2.5 py-1.5 text-[0.84rem] font-semibold text-ink">
              <Icon name="shield" size={16} className="text-accent" />
              {b}
            </li>
          ))}
        </ul>

        <div className="mt-10 grid gap-x-10 gap-y-9 sm:grid-cols-2 lg:grid-cols-3">
          {h.secItems.map((s, i) => (
            // On mobile the icon goes beside the text: six stacked blocks with the icon on top make the page too long.
            <div key={s.t} className="flex gap-4 sm:block">
              <IconChip name={SEC_ICONS[i]} />
              <div>
                <h3 className={`${h3cls} text-[1.08rem] sm:mt-4`}>{s.t}</h3>
                <p className="mt-2 text-[0.96rem] leading-relaxed text-ink2">{s.d}</p>
              </div>
            </div>
          ))}
        </div>

        {/* The absolute guarantees, in short sentences. Structure of the Minutes privacy screen (appllama). */}
        <ul className="invert-block mt-10 grid gap-4 rounded-xl p-5 sm:p-6 lg:grid-cols-3 lg:gap-8">
          {h.secNever.map((g) => (
            <li key={g} className="flex items-start gap-3 text-[0.98rem] font-semibold leading-snug text-ink">
              <span className="mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-brand text-on-brand">
                <Icon name="check" size={12} className="[stroke-width:3]" />
              </span>
              {g}
            </li>
          ))}
        </ul>

        <Link to="/recursos#seguranca" className={`${textLink} mt-6`}>{h.secLink}</Link>
      </Sec>

      {/* The thirteen products, each with its own page. The white panel sits well on the pale lime section. */}
      <Sec id="produtos" tone="soft">
        <H2>{h.prodH2}</H2>
        <Lede>{h.prodSub}</Lede>
        <PlatformMap />
      </Sec>

      <Sec>
        <H2>{h.faqH2}</H2>
        <Faq items={h.faq} />
      </Sec>
    </>
  )
}
