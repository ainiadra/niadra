import type { ReactNode } from 'react'
import { Link as RouterLink, useParams } from 'react-router-dom'
import { useT } from '../i18n'
import { ExternalArrow, Link } from '../i18n/link'
import { ContextDoc, HistorySearch } from '../components/Artifacts'
import { AuditLog, Identity } from '../components/Diagrams'
import { Icon, IconChip } from '../components/Icon'
import { PlatformMap } from '../components/Platform'
import {
  AlertHook,
  ClaimGuard,
  ConsoleBlocks,
  CoordinationCheck,
  DocsLinks,
  McpTools,
  OrgLinks,
  PatternCard,
  SdkSample,
  Signals,
  StateRead,
  SystemFlow,
  TurnRecord,
  UseTable,
  type SampleKey,
} from '../components/ProductParts'
import { Bullets, H2, Sec } from '../components/Shell'
import { productBySlug, productDocs, type Product, type ProductSlug } from '../products'
import { btnGhost, btnPrimary, contactTo, display, h3cls, lede, wrap } from '../ui'
import { Memory } from './Memory'

/** The product page headline: the product's sentence, smaller than the home's, because it is longer. */
const productH1 = `${display} mt-5 max-w-[24ch] text-[clamp(2.1rem,6.4vw,3.6rem)] leading-[1.03]`

function Hero({ product, extra }: { product: Product; extra?: ReactNode }) {
  const { t } = useT()
  const p = t.products[product.key]
  return (
    <section className={`${wrap} pb-12 pt-10 sm:pt-14 lg:pb-16 lg:pt-20`}>
      <div className="flex items-center gap-3">
        <IconChip name={product.icon} />
        <span className="text-[1.05rem] font-semibold text-ink">{p.name}</span>
      </div>
      <h1 className={productH1}>{p.line}</h1>
      <p className={`${lede} mt-6`}>{p.body}</p>
      <div className="mt-8 grid gap-3 sm:flex">
        <Link className={btnPrimary} to={contactTo()}>{t.nav.cta}</Link>
        <Link className={btnGhost} to={productDocs(product)}>{t.products.docsCta}<ExternalArrow to={productDocs(product)} /></Link>
        {extra}
      </div>
    </section>
  )
}

/** The closing of every product page: the whole platform, with this product marked. */
function More({ slug }: { slug: ProductSlug }) {
  const { t } = useT()
  return (
    <Sec>
      <H2>{t.products.moreH2}</H2>
      <PlatformMap current={slug} />
    </Sec>
  )
}

/** Artifact on one side, guarantees on the other: the layout of most pages. */
function Split({ artifact, points, flip }: { artifact: ReactNode; points: readonly string[]; flip?: boolean }) {
  return (
    <div className="grid grid-cols-[minmax(0,1fr)] gap-8 lg:grid-cols-[minmax(0,1.15fr)_minmax(0,0.85fr)] lg:items-start lg:gap-12">
      <div className={flip ? 'lg:order-2' : ''}>{artifact}</div>
      <Bullets items={points} single />
    </div>
  )
}

/** The SDK sample and the docs links of a page of the memory that operates, side by side under its artifact. */
function CodeAndDocs({ product }: { product: SampleKey }) {
  const { t } = useT()
  return (
    <div className="mt-10 grid grid-cols-[minmax(0,1fr)] gap-8 lg:grid-cols-[minmax(0,1.15fr)_minmax(0,0.85fr)] lg:items-start lg:gap-12">
      <SdkSample product={product} />
      <DocsLinks items={t.products[product].docs} />
    </div>
  )
}

/** What each product shows, between the hero and the map. Each section opens with an H2 for screen readers only. */
function Body({ slug }: { slug: ProductSlug }) {
  const { t } = useT()
  const pr = t.products
  const how = <h2 className="sr-only">{pr.how}</h2>
  switch (slug) {
    // The memory that operates: the artifact and the guarantees, then the SDK sample beside the docs pages.
    case 'estado':
      return (
        <Sec tone="soft">
          {how}
          <Split artifact={<StateRead />} points={pr.state.points} />
          <CodeAndDocs product="state" />
        </Sec>
      )
    case 'conferencia':
      return (
        <Sec tone="soft">
          {how}
          <Split artifact={<ClaimGuard />} points={pr.claims.points} />
          <CodeAndDocs product="claims" />
        </Sec>
      )
    case 'coordenacao':
      return (
        <Sec tone="soft">
          {how}
          <Split artifact={<CoordinationCheck />} points={pr.coordination.points} />
          <CodeAndDocs product="coordination" />
        </Sec>
      )
    case 'registro':
      return (
        <Sec tone="soft">
          {how}
          <Split artifact={<TurnRecord />} points={pr.turns.points} />
          <CodeAndDocs product="turns" />
        </Sec>
      )
    case 'sistemas':
      return (
        <Sec tone="soft">
          {how}
          <SystemFlow />
          <Bullets items={pr.systems.points} />
        </Sec>
      )
    case 'identidade':
      return (
        <Sec tone="soft">
          {how}
          <div className="grid grid-cols-[minmax(0,1fr)] gap-5 lg:grid-cols-2 lg:items-start">
            <Identity />
            <OrgLinks />
          </div>
          <Bullets items={pr.identity.points} />
        </Sec>
      )
    case 'padroes':
      return (
        <Sec tone="soft">
          {how}
          <Split artifact={<PatternCard />} points={pr.patterns.points} />
        </Sec>
      )
    case 'contexto':
      return (
        <Sec tone="soft">
          {how}
          <Split artifact={<ContextDoc />} points={pr.context.points} />
        </Sec>
      )
    case 'historico':
      return (
        <Sec tone="soft">
          {how}
          <Split artifact={<HistorySearch />} points={pr.history.points} />
          <div className="mt-10 grid grid-cols-[minmax(0,1fr)] gap-8 lg:grid-cols-2 lg:items-start lg:gap-12">
            <div>
              <h2 className={`${h3cls} text-[1.12rem]`}>{pr.history.asksTitle}</h2>
              <p className="mt-2 text-[0.95rem] leading-relaxed text-ink2">{pr.history.asksNote}</p>
              <ul className="mt-4 border-t border-edge">
                {pr.history.asks.map((q) => (
                  <li key={q} className="flex items-start gap-3 border-b border-edge py-3.5 text-[1rem] leading-snug text-ink">
                    <Icon name="search" size={17} className="mt-0.5 text-accent" />
                    {q}
                  </li>
                ))}
              </ul>
            </div>
            <McpTools />
          </div>
        </Sec>
      )
    case 'avisos':
      return (
        <Sec tone="soft">
          {how}
          <Split artifact={<AlertHook />} points={pr.alerts.points} flip />
        </Sec>
      )
    case 'medicao':
      // The measurement is the dark block: it is what only a neutral memory can give.
      return (
        <Sec tone="invert">
          {how}
          <UseTable />
          <Signals />
          <Bullets items={pr.measure.points} />
        </Sec>
      )
    case 'console':
      return (
        <Sec tone="soft">
          {how}
          <AuditLog />
          <ConsoleBlocks />
        </Sec>
      )
    case 'memoria':
      return null
  }
}

/**
 * /produtos/:slug. Memory has its own page (it is the main product); the others
 * follow the same form. A slug outside the catalog falls into "not found".
 */
export function ProductPage({ notFound }: { notFound: ReactNode }) {
  const { t } = useT()
  const product = productBySlug(useParams().slug)
  if (!product) return <>{notFound}</>
  if (product.slug === 'memoria') return <Memory />
  // The Console has no /en version: a link without a language prefix, like the header's Login.
  const enter =
    product.slug === 'console' ? (
      <RouterLink className={btnGhost} to="/console">
        {t.products.console.enter}
      </RouterLink>
    ) : undefined
  return (
    <>
      <Hero product={product} extra={enter} />
      <Body slug={product.slug} />
      <More slug={product.slug} />
    </>
  )
}
