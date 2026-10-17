import { useT } from '../i18n'
import { PlatformMap } from '../components/Platform'
import { PageHead, Sec } from '../components/Shell'

/** /produtos: the whole platform, with a link to each product's page. */
export function Products() {
  const { t } = useT()
  const p = t.products

  return (
    <>
      <PageHead a={p.h1a} b={p.h1b} sub={p.sub} />
      <Sec className="!pt-2 sm:!pt-4 lg:!pt-6">
        <PlatformMap />
      </Sec>
    </>
  )
}
