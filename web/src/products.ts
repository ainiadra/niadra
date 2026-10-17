/**
 * The product catalog: each product has a page at /produtos/<slug>, the key of its
 * text in `t.products`, the icon and the docs page that explains it
 * (at docs.niadra.com).
 * Menu, footer, platform map, routes and prerender all come from here.
 *
 * The SDK is not a product: it is how any agent connects to the memory, and it lives
 * in the docs.
 */
import type { IconName } from './components/Icon'
import { DOCS } from './i18n'

export const PRODUCTS = [
  { slug: 'sistemas', key: 'systems', icon: 'plug', docs: '/concepts/systems' },
  { slug: 'memoria', key: 'memory', icon: 'archive', docs: '/quickstart' },
  { slug: 'identidade', key: 'identity', icon: 'user', docs: '/concepts/identity' },
  { slug: 'padroes', key: 'patterns', icon: 'repeat', docs: '/concepts/patterns' },
  { slug: 'estado', key: 'state', icon: 'gauge', docs: '/concepts/object-types' },
  { slug: 'contexto', key: 'context', icon: 'layers', docs: '/concepts/context' },
  { slug: 'historico', key: 'history', icon: 'clock', docs: '/concepts/history' },
  { slug: 'avisos', key: 'alerts', icon: 'bell', docs: '/concepts/triggers-and-webhooks' },
  // Named "Conferência" since 01/10/2026 (the founder could not tell what "Afirmação" did); the key and the docs page keep "claims".
  { slug: 'conferencia', key: 'claims', icon: 'claim', docs: '/concepts/claims' },
  { slug: 'coordenacao', key: 'coordination', icon: 'merge', docs: '/concepts/coordination' },
  { slug: 'medicao', key: 'measure', icon: 'chart', docs: '/concepts/context-use' },
  { slug: 'registro', key: 'turns', icon: 'record', docs: '/concepts/turn-records' },
  { slug: 'console', key: 'console', icon: 'eye', docs: '/concepts/receipts' },
] as const satisfies readonly { slug: string; key: string; icon: IconName; docs: string }[]

/** The address of the docs page that explains the product. */
export const productDocs = (p: { docs: string }) => `${DOCS}${p.docs}`

export type Product = (typeof PRODUCTS)[number]
export type ProductSlug = Product['slug']

/** The order of the data path: what comes in, what the memory knows, what each agent receives, what your team controls. */
export const GROUPS = [
  { key: 'connect', slugs: ['sistemas'] },
  { key: 'remember', slugs: ['memoria', 'identidade', 'padroes', 'estado'] },
  { key: 'deliver', slugs: ['contexto', 'historico', 'avisos', 'conferencia', 'coordenacao'] },
  { key: 'govern', slugs: ['medicao', 'registro', 'console'] },
] as const satisfies readonly { key: string; slugs: readonly ProductSlug[] }[]

export const productPath = (slug: ProductSlug) => `/produtos/${slug}`
export const PRODUCT_PATHS = PRODUCTS.map((p) => productPath(p.slug))

export const productBySlug = (slug: string | undefined): Product | undefined => PRODUCTS.find((p) => p.slug === slug)
