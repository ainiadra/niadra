import { DOCS, useT } from '../i18n'
import { ExternalArrow, Link } from '../i18n/link'
import { Code, ContextDoc, HistorySearch } from '../components/Artifacts'
import { CallsTable } from '../components/Diagrams'
import { Icon } from '../components/Icon'
import { PlatformMap } from '../components/Platform'
import { McpTools, MemoryRecord } from '../components/ProductParts'
import { Bullets, Faq, H2, Lede, Sec } from '../components/Shell'
import { btnGhost, btnPrimary, contactTo, h1cls, h3cls, lede, textLink, wrap } from '../ui'

/**
 * Memory, the main product. The hero brings the key text and, beside it, Marina's
 * memory at 14:07. Right below, what the agent receives: the section does not
 * repeat the hero, it shows the context and says where each line came from. Then
 * history search, how the memory forms, how an agent connects (the SDK is not a
 * product: it is the integration, with the docs beside it) and the questions.
 */
export function Memory() {
  const { t } = useT()
  const m = t.products.memory
  const hist = t.products.history

  return (
    <>
      <section className={`${wrap} pb-12 pt-8 sm:pt-12 lg:pb-20 lg:pt-14`}>
        <div className="grid items-center gap-10 lg:grid-cols-[minmax(0,0.95fr)_minmax(0,1.05fr)] lg:gap-12">
          <div>
            <h1 className={h1cls}>
              {m.h1a} <span className="text-ink3">{m.h1b}</span>
            </h1>
            <p className={`${lede} mt-6`}>{m.sub}</p>
            <div className="mt-8 grid gap-3 sm:flex">
              <Link className={btnPrimary} to={contactTo()}>{t.nav.cta}</Link>
              <a className={btnGhost} href="#contexto">{m.secondary}</a>
            </div>
          </div>
          <MemoryRecord />
        </div>
      </section>

      <Sec id="contexto" tone="soft">
        <H2>{m.ctxH2}</H2>
        <Lede>{m.ctxSub}</Lede>
        <div className="mt-9 grid gap-7 lg:grid-cols-2 lg:items-start lg:gap-6">
          <ContextDoc />
          <Code />
        </div>
      </Sec>

      {/* Beyond the context: the agent browses the customer's whole history. */}
      <Sec id="historico">
        <H2>{m.histH2}</H2>
        <Lede>{m.histSub}</Lede>
        <div className="mt-9 grid gap-8 lg:grid-cols-[1.25fr_1fr] lg:items-start lg:gap-12">
          <HistorySearch />
          <div>
            <h3 className={`${h3cls} text-[1.12rem]`}>{hist.asksTitle}</h3>
            <p className="mt-2 text-[0.95rem] leading-relaxed text-ink2">{hist.asksNote}</p>
            <ul className="mt-4 border-t border-line">
              {hist.asks.map((q) => (
                <li key={q} className="flex items-start gap-3 border-b border-line py-3.5 text-[1rem] leading-snug text-ink">
                  <Icon name="search" size={17} className="mt-0.5 text-accent" />
                  {q}
                </li>
              ))}
            </ul>
            <p className="mt-5 text-[0.95rem] leading-relaxed text-ink2">{hist.anyLlm}</p>
          </div>
        </div>
      </Sec>

      {/* How the memory forms: the rules that hold for every item. */}
      <Sec tone="soft">
        <H2>{m.rulesH2}</H2>
        <Lede>{m.body}</Lede>
        <Bullets items={m.points} />
      </Sec>

      <Sec id="sdk">
        <H2>{m.sdkH2}</H2>
        <Lede>{m.sdkSub}</Lede>
        <div className="mt-9 grid grid-cols-[minmax(0,1fr)] gap-6 lg:grid-cols-2 lg:items-start">
          <CallsTable />
          <div className="grid gap-5">
            <Bullets items={m.sdkFacts} single />
            <McpTools />
          </div>
        </div>
        <Link to={`${DOCS}/quickstart`} className={`${textLink} mt-6 gap-1`}>{m.sdkCta}<ExternalArrow to={DOCS} /></Link>
      </Sec>

      <Sec tone="soft">
        <H2>{m.faqH2}</H2>
        <Faq items={m.faq} />
      </Sec>

      <Sec>
        <H2>{t.products.moreH2}</H2>
        <PlatformMap current="memoria" />
      </Sec>
    </>
  )
}
