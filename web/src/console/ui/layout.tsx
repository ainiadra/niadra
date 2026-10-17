import { useEffect, type ReactNode } from 'react'
import { Logo } from '../../components/Mark'
import { card } from './styles'

/** Single-card screens: login, space choice, configuration and not-found. */
export function Centered({ children, wide = false }: { children: ReactNode; wide?: boolean }) {
  return (
    <main className="flex min-h-dvh items-start justify-center px-4 py-12 sm:items-center">
      <div className={`w-full ${wide ? 'max-w-[640px]' : 'max-w-[440px]'}`}>
        <div className="mb-6">
          <Logo size="text-[1.2rem]" />
        </div>
        <div className={`${card} px-5 py-6 sm:px-7 sm:py-8`}>{children}</div>
      </div>
    </main>
  )
}

/** Sets the tab title; sandbox spaces say so in the title too. */
export function useTitle(title: string, sandbox = false) {
  useEffect(() => {
    document.title = `${sandbox ? '[Sandbox] ' : ''}${title} · Console da Niadra`
  }, [title, sandbox])
}
