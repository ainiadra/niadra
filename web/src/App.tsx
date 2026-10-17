import { useCallback, useEffect, useMemo } from 'react'
import { BrowserRouter, Route, Routes, useLocation, useNavigate } from 'react-router-dom'
import { I18n, detectLang, dicts, isCrawler, langOfPath, persistLang, savedLang, stripLang, useT, type Lang } from './i18n'
import { Link } from './i18n/link'
import { useInView, usePassed } from './hooks'
import { Nav } from './components/Nav'
import { Dock, FinalCTA, Footer } from './components/Shell'
import { Home } from './pages/Home'
import { Products } from './pages/Products'
import { ProductPage } from './pages/Product'
import { Resources } from './pages/Resources'
import { Pricing } from './pages/Pricing'
import { Enterprise } from './pages/Enterprise'
import { Blog } from './pages/Blog'
import { PostPage } from './pages/Post'
import { About } from './pages/About'
import { Benchmark } from './pages/Benchmark'
import { Comparison } from './pages/Comparison'
import { IntegrationPage, Integrations } from './pages/Integrations'
import { altPath, metaFor, SITE } from './seo'
import { rememberOrigin } from './lib/origin'
import { btnPrimary, h1cls, lede, wrap } from './ui'
import { consoleRoutes } from './console/routes'

/** A route change goes back to the top; an anchor in the URL goes to the section. */
function ScrollOnNav() {
  const { pathname, hash } = useLocation()
  useEffect(() => {
    if (hash) {
      const el = document.getElementById(hash.slice(1))
      if (el) {
        el.scrollIntoView({ block: 'start' })
        return
      }
    }
    window.scrollTo(0, 0)
  }, [pathname, hash])
  return null
}

/**
 * When navigating inside the site, the document's title, description, canonical and
 * language follow the page. On the first load they already come ready from the prerender.
 */
function Head() {
  const { pathname } = useLocation()
  useEffect(() => {
    const m = metaFor(pathname)
    document.documentElement.lang = m.lang === 'pt' ? 'pt-BR' : 'en'
    document.title = m.title
    document.querySelector('meta[name="description"]')?.setAttribute('content', m.description)
    document.querySelector('link[rel="canonical"]')?.setAttribute('href', `${SITE}${m.path}`)
  }, [pathname])
  return null
}

function NotFound() {
  const { t } = useT()
  return (
    <section className={`${wrap} py-20 lg:py-32`}>
      <h1 className={h1cls}>{t.notFound.h1}</h1>
      <p className={`${lede} mt-6`}>{t.notFound.body}</p>
      <Link className={`${btnPrimary} mt-8`} to="/">{t.notFound.cta}</Link>
    </section>
  )
}

const ASKS = ['/', '/produtos', '/recursos', '/preco', '/blog', '/sobre', '/benchmark', '/integracoes', '/comparativo']

function Shell() {
  const { t } = useT()
  const location = useLocation()
  const base = stripLang(location.pathname)

  // Sentinels instead of a scroll listener: one at the top (the line under the navigation),
  // another at the height where the hero is gone (the mobile action bar).
  const [topRef, scrolled] = usePassed<HTMLDivElement>()
  const [heroRef, heroGone] = usePassed<HTMLDivElement>()
  const [ctaRef, ctaInView] = useInView<HTMLElement>()

  // On Enterprise the form is already the ask: no lime block and no bar.
  const hasAsk = ASKS.includes(base) || base.startsWith('/blog/') || base.startsWith('/produtos/') || base.startsWith('/integracoes/')

  return (
    <div className="relative">
      <a
        href="#conteudo"
        className="sr-only focus:not-sr-only focus:absolute focus:left-3 focus:top-3 focus:z-[90] focus:rounded-lg focus:border focus:border-edge focus:bg-surface focus:px-5 focus:py-3"
      >
        {t.meta.skip}
      </a>
      <div ref={topRef} className="pointer-events-none absolute top-0 h-px w-px" aria-hidden="true" />
      <div ref={heroRef} className="pointer-events-none absolute top-[620px] h-px w-px" aria-hidden="true" />
      <ScrollOnNav />
      <Head />

      <Nav scrolled={scrolled} />

      <main id="conteudo">
        {/* Routes match the path without the language prefix: /en/preco and /preco are the same page. */}
        <Routes location={{ ...location, pathname: base }}>
          <Route path="/" element={<Home />} />
          <Route path="/produtos" element={<Products />} />
          <Route path="/produtos/:slug" element={<ProductPage notFound={<NotFound />} />} />
          <Route path="/recursos" element={<Resources />} />
          <Route path="/comparativo" element={<Comparison />} />
          <Route path="/preco" element={<Pricing />} />
          <Route path="/enterprise" element={<Enterprise />} />
          <Route path="/blog" element={<Blog />} />
          <Route path="/blog/:slug" element={<PostPage />} />
          <Route path="/sobre" element={<About />} />
          <Route path="/benchmark" element={<Benchmark />} />
          <Route path="/integracoes" element={<Integrations />} />
          <Route path="/integracoes/:slug" element={<IntegrationPage notFound={<NotFound />} />} />
          <Route path="*" element={<NotFound />} />
        </Routes>
      </main>

      {hasAsk && <FinalCTA innerRef={ctaRef} />}
      <Footer />
      {hasAsk && <Dock show={heroGone && !ctaInView} />}
    </div>
  )
}

/**
 * The language comes from the URL. A first-time visitor on the Portuguese version with
 * a device in English (and no saved choice to the contrary) goes to the English
 * version. Search crawlers and direct links to /en are never redirected.
 */
export function AppShell() {
  const location = useLocation()
  const navigate = useNavigate()
  const lang = langOfPath(location.pathname)

  useEffect(() => {
    rememberOrigin()
    if (lang === 'en' || isCrawler(navigator.userAgent)) return
    const want = savedLang() ?? detectLang()
    if (want === 'en') navigate(altPath(location.pathname, 'en') + location.search + location.hash, { replace: true })
    // Only on arrival: after that, the person's choice rules.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const setLang = useCallback(
    (l: Lang) => {
      persistLang(l)
      navigate(altPath(location.pathname, l))
    },
    [location.pathname, navigate],
  )

  const value = useMemo(() => ({ lang, t: dicts[lang], setLang }), [lang, setLang])

  return (
    <I18n.Provider value={value}>
      <Shell />
    </I18n.Provider>
  )
}

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        {/* The Console (src/console) has its own shell, outside the site's header and footer. */}
        {consoleRoutes}
        <Route path="*" element={<AppShell />} />
      </Routes>
    </BrowserRouter>
  )
}
