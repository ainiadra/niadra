import { Link, useLocation, useNavigate } from 'react-router-dom'
import { GLOSSARY } from '../lib/glossary'
import { SCREENS, screenOf } from '../lib/screens'
import { tutorialsFor } from '../lib/tutorials'
import { paths } from '../paths'
import { useSpace } from '../session'
import { Dialog } from './Dialog'
import { Icon } from './Icon'
import { btnSecondary, small } from './styles'
import { startTutorial } from './TutorialPlayer'

/**
 * The "?" of the rail: the help of the screen the person is on (what it is, how to read it, its words) and
 * the tutorials that pass through it, each one step away. The same registry the page header reads.
 */
export function HelpDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const { space } = useSpace()
  const loc = useLocation()
  const navigate = useNavigate()
  const id = screenOf(loc.pathname)
  const s = id ? SCREENS[id] : null
  const passing = id ? tutorialsFor(id) : []
  return (
    <Dialog open={open} onClose={onClose} title={s ? `Ajuda: ${s.label}` : 'Ajuda do Console'} wide>
      {s ? (
        <div className="grid gap-5">
          <p className="text-[0.875rem] leading-5 text-ink">{s.what}</p>
          <div>
            <h3 className="text-[0.8125rem] font-semibold text-ink">O que você está vendo</h3>
            <ul className="mt-1.5 grid gap-1.5">
              {s.read.map((line) => (
                <li key={line} className={`${small} flex gap-2 text-ink`}>
                  <span aria-hidden="true" className="mt-[0.55rem] h-1.5 w-1.5 shrink-0 rounded-full bg-accent" />
                  <span>{line}</span>
                </li>
              ))}
            </ul>
          </div>
          {s.next && (
            <div>
              <h3 className="text-[0.8125rem] font-semibold text-ink">Por onde começar</h3>
              <p className={`${small} mt-1 text-ink`}>{s.next}</p>
            </div>
          )}
          {passing.length > 0 && (
            <div>
              <h3 className="text-[0.8125rem] font-semibold text-ink">Tutoriais que passam por aqui</h3>
              <ul className="mt-1.5 grid gap-2">
                {passing.map((t) => {
                  const at = t.steps.findIndex((st) => st.screen === id)
                  return (
                    <li key={t.id} className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-line px-3 py-2">
                      <span className="text-[0.875rem] font-semibold text-ink">{t.title}</span>
                      <button
                        type="button"
                        className={btnSecondary}
                        onClick={() => {
                          onClose()
                          startTutorial(navigate, space.space_id, t.id, Math.max(0, at))
                        }}
                      >
                        <Icon name="book" size={16} />
                        Me leve lá
                      </button>
                    </li>
                  )
                })}
              </ul>
            </div>
          )}
          <div>
            <h3 className="text-[0.8125rem] font-semibold text-ink">As palavras desta tela</h3>
            <dl className="mt-1.5 grid gap-2 sm:grid-cols-2">
              {s.terms.map((k) => (
                <div key={k}>
                  <dt className="text-[0.8125rem] font-semibold text-ink">{GLOSSARY[k].term[0]!.toUpperCase() + GLOSSARY[k].term.slice(1)}</dt>
                  <dd className={small}>{GLOSSARY[k].means}</dd>
                </div>
              ))}
            </dl>
          </div>
        </div>
      ) : (
        <p className="text-[0.875rem] leading-5 text-ink">Esta tela não tem ajuda própria. Os tutoriais passam por cada tela do espaço, passo a passo.</p>
      )}
      <div className="mt-5 flex justify-end">
        <Link to={paths.tutorials(space.space_id)} onClick={onClose} className={btnSecondary}>
          Todos os tutoriais
        </Link>
      </div>
    </Dialog>
  )
}
