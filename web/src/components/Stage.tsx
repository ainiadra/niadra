import { useState, type ReactNode } from 'react'
import { useT } from '../i18n'
import { Icon } from './Icon'
import { hl, panelLift, srcChip } from '../ui'

/**
 * The hero stage. Structure taken from references the owner pointed to: a staged
 * conversation with the panel of what the system knows and, from appllama, the
 * speaker's bubble in the brand's full color (Sworkit) and the tab selector as a
 * full block (Minutes).
 *
 * Without Niadra: each agent in a silo, the internal billing agent included, and the
 * voice agent asks everything again.
 * With Niadra: one memory. The lime highlight marks what it already knew: what has
 * just happened on another channel, what another agent did in a system, and what the
 * customer's history teaches.
 */

const Ico = ({ d, fill }: { d: string; fill?: boolean }) => (
  <svg width="13" height="13" viewBox="0 0 24 24" fill={fill ? 'currentColor' : 'none'} stroke={fill ? 'none' : 'currentColor'} strokeWidth="2.1" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" className="shrink-0">
    <path d={d} />
  </svg>
)
const ICON = {
  chat: 'M21 12a8 8 0 0 1-11.6 7.1L4 20l1-4.6A8 8 0 1 1 21 12Z',
  app: 'M7 3h10a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2Zm4 15h2',
  phone: 'M5 4h4l2 5-2.5 1.5a11 11 0 0 0 5 5L15 13l5 2v4a2 2 0 0 1-2 2A16 16 0 0 1 3 6a2 2 0 0 1 2-2Z',
  check: 'M4 12.5l5 5L20 6.5',
  system: 'M12 3c4.4 0 8 1.3 8 3s-3.6 3-8 3-8-1.3-8-3 3.6-3 8-3ZM4 6v12c0 1.7 3.6 3 8 3s8-1.3 8-3V6M4 12c0 1.7 3.6 3 8 3s8-1.3 8-3',
}

function Pill({ icon, channel, vendor, time }: { icon: string; channel: string; vendor: string; time: string }) {
  return (
    <div className="flex flex-wrap items-center justify-between gap-x-3 gap-y-1">
      <span className="inline-flex items-center gap-1.5 rounded-full border border-edge bg-surface py-1 pl-2 pr-2.5 text-[0.76rem] text-ink">
        <Ico d={icon} />
        <span className="font-semibold">{channel}</span>
        <span className="text-ink3">· {vendor}</span>
      </span>
      <span className="font-mono text-[0.72rem] text-ink3">{time}</span>
    </div>
  )
}

const Avatar = ({ children }: { children: ReactNode }) => (
  <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-ink text-[0.62rem] font-bold text-bg">
    {children}
  </span>
)

export function Stage() {
  const { t } = useT()
  const s = t.stage
  const [withN, setWithN] = useState(true)

  const tab = (active: boolean) =>
    `min-h-11 flex-1 rounded-md px-3 text-[0.9rem] font-semibold transition-colors ${
      active ? 'bg-ink text-bg' : 'text-ink2 hover:bg-ink/5 hover:text-ink'
    }`

  return (
    <div className={`${panelLift} @container overflow-hidden`}>
      <div className="border-b border-line bg-surface p-2.5">
        <div className="flex gap-1 rounded-lg border border-edge bg-bg p-1" role="group" aria-label={s.tabsLabel}>
          <button type="button" className={tab(!withN)} aria-pressed={!withN} onClick={() => setWithN(false)}>
            {s.tabWithout}
          </button>
          <button type="button" className={tab(withN)} aria-pressed={withN} onClick={() => setWithN(true)}>
            {s.tabWith}
          </button>
        </div>
      </div>

      <div className="grid @[520px]:grid-cols-[1.2fr_1fr]">
        {/* ---------- the conversation ---------- */}
        <div className="grid content-start gap-3 p-4 sm:p-5">
          <Pill icon={ICON.chat} channel={s.wa.channel} vendor={s.wa.vendor} time={s.wa.time} />
          <div className="flex items-end justify-end gap-2">
            <p className="max-w-[85%] rounded-2xl rounded-br-md bg-brand px-3.5 py-2.5 text-[0.92rem] leading-snug text-on-brand">
              {s.wa.msg}
            </p>
            <Avatar>{s.initials}</Avatar>
          </div>

          <Pill icon={ICON.app} channel={s.app.channel} vendor={s.app.vendor} time={s.app.time} />
          <div className="flex items-center justify-between gap-3 rounded-lg border border-line bg-bg px-3.5 py-2.5">
            <span className="text-[0.9rem] text-ink">{s.app.event}</span>
            <span className="rounded-md border border-edge px-1.5 py-0.5 font-mono text-[0.66rem] text-ink2">{s.app.state}</span>
          </div>

          {/* An internal agent, which does not talk to the customer: it acts in a system. */}
          <Pill icon={ICON.system} channel={s.sys.channel} vendor={s.sys.vendor} time={s.sys.time} />
          <div className="flex items-center justify-between gap-3 rounded-lg border border-line bg-bg px-3.5 py-2.5">
            <span className="text-[0.9rem] text-ink">{s.sys.event}</span>
            <span className="rounded-md border border-edge px-1.5 py-0.5 font-mono text-[0.66rem] text-ink2">{s.sys.state}</span>
          </div>

          <div className="my-1 flex items-center gap-3" aria-hidden="true">
            <span className="h-px flex-1 bg-line" />
            <span className="rounded-full border border-line px-2.5 py-0.5 text-[0.7rem] text-ink3">{s.switchLabel}</span>
            <span className="h-px flex-1 bg-line" />
          </div>

          <Pill icon={ICON.phone} channel={s.call.channel} vendor={s.call.vendor} time={s.call.time} />
          <div aria-live="polite">
            <div className="mb-1.5 flex items-center gap-1.5 text-[0.76rem] font-semibold text-ink2">
              <span className={`h-2 w-2 rounded-full ${withN ? 'bg-brand' : 'bg-edge'}`} aria-hidden="true" />
              {s.call.agent}
            </div>
            <p className="max-w-[92%] rounded-2xl rounded-bl-md border border-line bg-bg px-3.5 py-2.5 text-[0.92rem] leading-relaxed text-ink">
              {withN
                ? s.withMsg.map((part) =>
                    part.m ? <mark key={part.t} className={hl}>{part.t}</mark> : <span key={part.t}>{part.t}</span>,
                  )
                : s.withoutMsg}
            </p>
            <p className="mt-2 font-mono text-[0.7rem] leading-relaxed text-ink3">{withN ? s.withStatus : s.withoutStatus}</p>
          </div>
        </div>

        {/* ---------- what is known about the customer ---------- */}
        <div className="border-t border-line bg-bg p-4 sm:p-5 @[520px]:border-l @[520px]:border-t-0">
          {withN ? (
            <div className="grid content-start gap-4">
              <div className="flex items-center gap-2.5">
                <Avatar>{s.initials}</Avatar>
                <div className="min-w-0">
                  <div className="text-[0.74rem] font-semibold text-ink3">{s.memTitle}</div>
                  <div className="truncate text-[0.95rem] font-semibold text-ink">{s.customer}</div>
                </div>
                <span className="ml-auto rounded-md border border-line bg-surface px-1.5 py-0.5 font-mono text-[0.62rem] text-ink3">
                  {t.meta.example}
                </span>
              </div>
              <p className="-mt-2 text-[0.82rem] text-ink2">{s.memSub}</p>

              <div>
                <div className="mb-2 text-[0.74rem] font-semibold text-ink3">{s.openLabel}</div>
                <ul className="grid gap-2">
                  {s.open.map((o) => (
                    <li key={o.t} className="rounded-lg border border-line bg-surface p-2.5">
                      <div className="flex gap-2 text-[0.86rem] font-medium leading-snug text-ink">
                        <span className="mt-[3px] h-3.5 w-3.5 shrink-0 rounded-full border-2 border-accent" aria-hidden="true" />
                        {o.t}
                      </div>
                      <div className="mt-1.5 pl-6"><span className={srcChip}>{o.src}</span></div>
                    </li>
                  ))}
                </ul>
              </div>

              {/* What another agent already did in a system: resolved, hence a check and not a ring. */}
              <div>
                <div className="mb-2 text-[0.74rem] font-semibold text-ink3">{s.doneLabel}</div>
                <ul className="grid gap-2">
                  {s.done.map((o) => (
                    <li key={o.t} className="rounded-lg border border-line bg-surface p-2.5">
                      <div className="flex gap-2 text-[0.86rem] font-medium leading-snug text-ink">
                        <span className="mt-[2px] flex h-3.5 w-3.5 shrink-0 items-center justify-center rounded-full bg-brand text-on-brand" aria-hidden="true">
                          <svg width="8" height="8" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3.6" strokeLinecap="round" strokeLinejoin="round"><path d={ICON.check} /></svg>
                        </span>
                        {o.t}
                      </div>
                      <div className="mt-1.5 pl-6"><span className={srcChip}>{o.src}</span></div>
                    </li>
                  ))}
                </ul>
              </div>

              {/* What the history teaches: the same problem, months ago, and how it was solved. */}
              <div>
                <div className="mb-2 text-[0.74rem] font-semibold text-ink3">{s.histLabel}</div>
                <ul className="grid gap-2">
                  {s.hist.map((o) => (
                    <li key={o.t} className="rounded-lg border border-line bg-surface p-2.5">
                      <div className="flex gap-2 text-[0.86rem] font-medium leading-snug text-ink">
                        <Icon name="clock" size={15} className="mt-[2px] text-accent" />
                        {o.t}
                      </div>
                      <div className="mt-1.5 pl-[23px]"><span className={srcChip}>{o.src}</span></div>
                    </li>
                  ))}
                </ul>
              </div>

              <div className="border-t border-line pt-3">
                <div className="flex items-center gap-2 text-[0.86rem] font-semibold text-ink">
                  <span className="flex h-4 w-4 shrink-0 items-center justify-center rounded-full bg-brand text-on-brand">
                    <svg width="9" height="9" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3.4" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d={ICON.check} /></svg>
                  </span>
                  {s.delivered}
                </div>
                <div className="mt-0.5 pl-6 text-[0.82rem] text-ink2">{s.deliveredMeta}</div>
              </div>
            </div>
          ) : (
            <div className="grid content-start gap-3">
              <div className="text-[0.95rem] font-semibold text-ink">{s.siloTitle}</div>
              {s.silos.map((x) => (
                <div key={x.who} className="rounded-lg border border-dashed border-edge bg-surface p-3">
                  <div className="text-[0.78rem] font-semibold text-ink3">{x.who}</div>
                  <div className="text-[0.9rem] text-ink">{x.has}</div>
                </div>
              ))}
              <p className="text-[0.86rem] leading-relaxed text-ink2">{s.siloNote}</p>
            </div>
          )}
        </div>
      </div>

      <div className="border-t border-line bg-surface px-4 py-3 text-[0.88rem] font-semibold text-ink sm:px-5">
        {withN ? s.withFoot : s.withoutFoot}
      </div>
    </div>
  )
}
