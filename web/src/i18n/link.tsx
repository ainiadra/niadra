import { Link as RouterLink, type LinkProps } from 'react-router-dom'
import { localize, useT } from '.'

export const isExternal = (to: string) => /^https?:\/\//.test(to)

/**
 * The site's Link: takes the Portuguese path ('/preco') and points to the
 * version in the current language ('/en/preco'). No component builds a prefix by hand.
 * An absolute address (the docs) becomes a plain <a>, with the language in the path.
 */
export function Link({ to, ...rest }: LinkProps & { to: string }) {
  const { lang } = useT()
  if (isExternal(to)) {
    const { reloadDocument, replace, state, preventScrollReset, relative, viewTransition, discover, ...a } = rest
    return <a href={localize(to, lang)} {...a} />
  }
  return <RouterLink to={localize(to, lang)} {...rest} />
}

/**
 * The arrow for leaving the site: every link to the docs (docs.niadra.com) carries
 * one, so the person knows before clicking that it goes to another address. Screen
 * readers hear the address.
 */
export function ExternalArrow({ to }: { to: string }) {
  const { t } = useT()
  return (
    <>
      <svg width="12" height="12" viewBox="0 0 24 24" fill="none" aria-hidden="true" className="shrink-0">
        <path d="M7 17 17 7M8.5 7H17v8.5" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round" />
      </svg>
      <span className="sr-only">({t.nav.opens} {new URL(to).host})</span>
    </>
  )
}
