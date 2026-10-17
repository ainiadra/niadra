import { DOCS, useT } from '../i18n'
import { ExternalArrow, Link } from '../i18n/link'
import { GROUPS, PRODUCTS, productBySlug, productPath, type ProductSlug } from '../products'
import { Icon, IconChip } from './Icon'
import { panel } from '../ui'

const bySlug = Object.fromEntries(PRODUCTS.map((p) => [p.slug, p])) as Record<ProductSlug, (typeof PRODUCTS)[number]>

// The dividers between the groups: one column on mobile, two on tablet, four on desktop.
const EDGES = [
  '',
  'border-t sm:border-l sm:border-t-0',
  'border-t lg:border-l lg:border-t-0',
  'border-t sm:border-l lg:border-t-0',
]

/**
 * The whole platform in one panel: the four groups in the order of the data path
 * (what comes in, what the memory knows, what each agent receives, what your team
 * controls), with the products of each. On a product's page, that product appears
 * marked and without a link. The SDK comes in as a note under "Connect": it is not a
 * product, it is how any agent connects, and it leads to the docs.
 */
export function PlatformMap({ current }: { current?: ProductSlug }) {
  const { t } = useT()
  const pr = t.products
  return (
    <div className={`${panel} mt-9 grid overflow-hidden sm:grid-cols-2 lg:grid-cols-4`}>
      {GROUPS.map((g, gi) => (
        <div key={g.key} className={`flex flex-col border-line p-4 sm:p-5 ${EDGES[gi]}`}>
          <div className="text-[0.78rem] font-semibold text-ink3">{pr.groups[g.key].label}</div>
          <div className="mt-0.5 text-[0.96rem] font-semibold leading-snug text-ink">{pr.groups[g.key].note}</div>
          <ul className="mt-4 grid gap-1">
            {g.slugs.map((slug) => {
              const p = bySlug[slug]
              const c = pr[p.key]
              const body = (
                <>
                  <IconChip name={p.icon} tone="plain" />
                  <span className="min-w-0">
                    <span className="flex flex-wrap items-center gap-x-2 text-[1rem] font-semibold text-ink">
                      {c.name}
                      {slug === current && <span className="text-[0.74rem] font-semibold text-accent">{pr.here}</span>}
                    </span>
                    <span className="mt-0.5 block text-[0.88rem] leading-snug text-ink2">{c.menu}</span>
                  </span>
                </>
              )
              return (
                <li key={slug}>
                  {slug === current ? (
                    <span aria-current="page" className="-mx-2 flex gap-3 rounded-lg px-2 py-2.5">
                      {body}
                    </span>
                  ) : (
                    <Link to={productPath(slug)} className="-mx-2 flex gap-3 rounded-lg px-2 py-2.5 transition-colors hover:bg-bg2">
                      {body}
                    </Link>
                  )}
                </li>
              )
            })}
          </ul>
          {g.key === 'connect' && (
            <div className="mt-4 rounded-lg border border-dashed border-edge px-3.5 py-3 lg:mt-auto">
              <div className="flex items-center gap-2 text-[0.92rem] font-semibold text-ink">
                <Icon name="code" size={17} className="text-accent" />
                {pr.sdkNote.t}
              </div>
              <p className="mt-1 text-[0.86rem] leading-snug text-ink2">{pr.sdkNote.d}</p>
              <Link to={DOCS} className="mt-1 inline-flex min-h-11 items-center gap-1 text-[0.9rem] font-semibold text-ink underline decoration-accent decoration-2 underline-offset-4 hover:decoration-ink">
                {pr.sdkNote.cta}
                <ExternalArrow to={DOCS} />
              </Link>
            </div>
          )}
        </div>
      ))}
    </div>
  )
}

/** The products of one front, as links: the name of each, its page. A slug outside the catalog does not become a link. */
export function ProductLinks({ slugs }: { slugs: readonly string[] }) {
  const { t } = useT()
  const products = slugs.map(productBySlug).filter((p) => p !== undefined)
  return (
    <ul className="flex flex-wrap gap-2">
      {products.map((p) => (
        <li key={p.slug}>
          <Link
            to={productPath(p.slug)}
            className="inline-flex min-h-11 items-center gap-2 rounded-lg border border-line bg-surface px-3 text-[0.92rem] font-semibold text-ink transition-colors hover:border-edge"
          >
            <Icon name={p.icon} size={17} className="text-accent" />
            {t.products[p.key].name}
          </Link>
        </li>
      ))}
    </ul>
  )
}
