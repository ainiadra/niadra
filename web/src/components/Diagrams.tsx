import { useT } from '../i18n'
import { ExampleTag } from './Artifacts'
import { Icon, type IconName } from './Icon'
import { Mark } from './Mark'
import { VENDOR_OF_NAME, VendorLogo } from './VendorLogo'
import { h3cls, panel } from '../ui'

// Up (the context reaches the agent), there and back (the search), down (the conversation or the action enters the memory).
const ARROWS = ['M12 20V4M6 10l6-6 6 6', 'M8 4v16M4 8l4-4 4 4M16 20V4M12 16l4 4 4-4', 'M12 4v16M6 14l6 6 6-6']

// In the order of `arch.parts` in pt.ts/en.ts.
const PART_ICONS: IconName[] = ['archive', 'user', 'layers', 'sliders', 'search', 'eye']

/**
 * The architecture in a single block, modeled on Agno's AgentOS diagram: the callers
 * on top (customer service agents and internal agents), the three calls, the
 * company systems that feed the memory, the engine with its compartments, and the
 * strip showing where it runs at the bottom. The engine is the dark moment; the
 * rest of the panel is white, because the panel sits in a pale lime section.
 */
export function Architecture() {
  const { t } = useT()
  const a = t.home.arch
  return (
    <div className={`${panel} mt-10 overflow-hidden`}>
      <div className="p-4 sm:p-6">
        <div className="text-[0.82rem] font-semibold text-ink3">{a.agentsLabel}</div>
        <div className="mt-3 grid grid-cols-2 gap-2.5 lg:grid-cols-3">
          {a.agents.map((g) => (
            <div key={g.n} className="rounded-lg border border-line bg-bg px-3 py-2.5">
              <div className="text-[0.92rem] font-semibold leading-snug text-ink">{g.n}</div>
              <div className="font-mono text-[0.7rem] text-ink3">{g.v}</div>
            </div>
          ))}
        </div>
      </div>

      {/* In time order: before, during and after the answer. */}
      <div className="grid border-y border-line bg-bg md:grid-cols-3">
        {a.calls.map((c, i) => (
          <div key={c.call} className={`flex items-start gap-3 px-4 py-3.5 sm:px-6 ${i ? 'border-t border-line md:border-l md:border-t-0' : ''}`}>
            <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" className="mt-0.5 shrink-0 text-ink2">
              <path d={ARROWS[i]} />
            </svg>
            <div>
              <code className="font-mono text-[0.8rem] font-semibold text-ink">{c.call}</code>
              <p className="mt-0.5 text-[0.92rem] leading-snug text-ink2">{c.text}</p>
            </div>
          </div>
        ))}
      </div>

      {/* The company systems: they come in by event, without going through the three calls. */}
      <div className="border-b border-line p-4 sm:px-6">
        <div className="flex flex-wrap items-center gap-2">
          <span className="mr-1 text-[0.82rem] font-semibold text-ink3">{a.systemsLabel}</span>
          {a.systems.map((x) => (
            <span key={x} className="rounded-md border border-line bg-bg px-2.5 py-1 text-[0.82rem] font-medium text-ink">
              {x}
            </span>
          ))}
        </div>
        <p className="mt-2.5 text-[0.92rem] leading-snug text-ink2">{a.systemsText}</p>
      </div>

      <div className="invert-block p-4 sm:p-6">
        <div className="flex items-center gap-2">
          <Mark className="h-5 w-auto text-brand" />
          <span className="font-display text-[1.1rem] font-semibold tracking-[-0.04em]">niadra</span>
        </div>
        <div className="mt-4 grid gap-2.5 sm:grid-cols-2 lg:grid-cols-3">
          {a.parts.map((p, i) => (
            <div key={p.n} className="rounded-lg border border-line bg-surface px-3.5 py-3">
              <div className="flex items-center gap-2 text-[0.94rem] font-semibold text-accent">
                <Icon name={PART_ICONS[i]} size={17} />
                {p.n}
              </div>
              <div className="mt-1.5 text-[0.86rem] leading-snug text-ink2">{p.d}</div>
            </div>
          ))}
        </div>
        <p className="mt-4 max-w-[80ch] text-[0.9rem] leading-relaxed text-ink2">{a.async}</p>
      </div>

      <div className="flex items-center gap-2.5 border-t border-line bg-surface px-4 py-3 text-[0.9rem] font-semibold text-ink sm:px-6">
        <Icon name="cloud" size={17} className="text-accent" />
        {a.base}
      </div>
    </div>
  )
}

// Down into the memory (the events), out to the readers (the context).
const FLOW_ARROW = 'M12 4v16M6 14l6 6 6-6'

/**
 * The positioning in one picture: many sources on the left, one memory in the
 * middle, many readers on the right. Text chips carry the real integration names
 * (the site holds no third-party logos, and none are fetched). Three columns on
 * desktop; on mobile the columns stack and the two arrows point down, so the
 * reading order is the data path. The memory is the dark moment of the panel.
 */
export function SourcesFlow() {
  const { t } = useT()
  const f = t.home.flow
  const arrow = (
    <div className="flex items-center justify-center py-1 lg:hidden" aria-hidden="true">
      <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" className="text-ink3">
        <path d={FLOW_ARROW} />
      </svg>
    </div>
  )
  return (
    <div className={`${panel} mt-10 grid overflow-hidden lg:grid-cols-[minmax(0,1.15fr)_minmax(0,0.95fr)_minmax(0,0.9fr)]`}>
      {/* ---------- the sources ---------- */}
      <div className="p-4 sm:p-6">
        <div className="text-[0.82rem] font-semibold text-ink3">{f.inLabel}</div>
        <div className="mt-3 grid gap-4">
          {f.groups.map((g) => (
            <div key={g.t}>
              <div className="text-[0.86rem] font-semibold text-ink">{g.t}</div>
              <ul className="mt-1.5 flex flex-wrap gap-1.5">
                {/* A platform the site has the mark of shows it before its name; the others stay as text. */}
                {g.items.map((x) => {
                  const vendor = VENDOR_OF_NAME[x]
                  return (
                    <li key={x} className={`flex items-center gap-1.5 rounded-md border border-line bg-bg py-1 pr-2 text-[0.8rem] font-medium leading-snug text-ink ${vendor ? 'pl-1' : 'pl-2'}`}>
                      {vendor && <VendorLogo vendor={vendor} size="sm" />}
                      {x}
                    </li>
                  )
                })}
              </ul>
            </div>
          ))}
        </div>
        <p className="mt-4 text-[0.9rem] leading-snug text-ink2">{f.eventLabel}</p>
        {arrow}
      </div>

      {/* ---------- the memory ---------- */}
      <div className="invert-block flex flex-col p-4 sm:p-6">
        <div className="flex items-center gap-2">
          <Mark className="h-5 w-auto text-brand" />
          <span className="font-display text-[1.1rem] font-semibold tracking-[-0.04em]">niadra</span>
        </div>

        {/* Identity made visible: the identifiers the events carry, then the one person they resolve to. Synthetic, masked values. */}
        <div className="mt-4 text-[0.82rem] font-semibold text-ink3">{f.idLabel}</div>
        <ul className="mt-2 flex flex-wrap gap-1.5">
          {f.ids.map((x) => (
            <li key={x} className="rounded-md border border-line bg-surface px-2 py-1 font-mono text-[0.74rem] leading-snug text-ink">
              {x}
            </li>
          ))}
        </ul>
        <div className="mt-3 flex items-center gap-2.5">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" className="shrink-0 text-brand">
            <path d={FLOW_ARROW} />
          </svg>
          <span className="rounded-md bg-brand px-2 py-1 text-[0.86rem] font-semibold leading-snug text-on-brand">{f.idOne}</span>
        </div>
        <p className="mt-2 text-[0.84rem] leading-snug text-ink2">{f.idNote}</p>

        <div className={`${h3cls} mt-5 border-t border-line pt-4 text-[1.2rem] leading-snug text-ink`}>{f.memLabel}</div>
        <ul className="mt-4 grid gap-2">
          {f.memItems.map((x) => (
            <li key={x} className="flex items-start gap-2.5 text-[0.92rem] leading-snug text-ink2">
              <span className="mt-1 flex h-4 w-4 shrink-0 items-center justify-center rounded-full bg-brand text-on-brand">
                <Icon name="check" size={10} className="[stroke-width:3.2]" />
              </span>
              {x}
            </li>
          ))}
        </ul>
        <div className="mt-auto pt-4 text-ink2">{arrow}</div>
      </div>

      {/* ---------- the readers ---------- */}
      <div className="border-t border-line p-4 sm:p-6 lg:border-l lg:border-t-0">
        <div className="text-[0.82rem] font-semibold text-ink3">{f.outLabel}</div>
        <ul className="mt-3 grid gap-2">
          {f.readers.map((r) => (
            <li key={r.n} className="rounded-lg border border-line bg-bg px-3 py-2.5">
              <div className="text-[0.92rem] font-semibold leading-snug text-ink">{r.n}</div>
              <div className="font-mono text-[0.7rem] text-ink3">{r.v}</div>
            </li>
          ))}
        </ul>
        <p className="mt-4 text-[0.9rem] leading-snug text-ink2">{f.readNote}</p>
      </div>
    </div>
  )
}

/** The three SDK calls: which, when and what it does. Becomes stacked blocks on mobile. */
export function CallsTable() {
  const { t } = useT()
  const s = t.demo.calls
  return (
    <div className={`${panel} overflow-hidden`}>
      <h3 className={`${h3cls} border-b border-line bg-surface px-4 py-3 text-[0.98rem] sm:px-5`}>{s.title}</h3>
      <div className="hidden grid-cols-[6.5rem_9.5rem_1fr] gap-4 border-b border-line px-5 py-2 text-[0.8rem] text-ink3 sm:grid">
        {s.cols.map((c) => (
          <span key={c}>{c}</span>
        ))}
      </div>
      {s.rows.map((c) => (
        <div key={c.call} className="grid gap-1 border-b border-line px-4 py-3.5 last:border-b-0 sm:grid-cols-[6.5rem_9.5rem_1fr] sm:items-baseline sm:gap-4 sm:px-5">
          <code className="font-mono text-[0.84rem] font-semibold text-ink">{c.call}</code>
          <span className="text-[0.9rem] font-semibold text-ink">{c.when}</span>
          <span className="text-[0.94rem] leading-snug text-ink2">{c.does}</span>
        </div>
      ))}
    </div>
  )
}

/** Five identifiers, one person. The 4-step scale is the weight of each one. */
export function Identity() {
  const { t } = useT()
  const c = t.products.identity
  return (
    <div className={`${panel} overflow-hidden`}>
      <div className="flex items-center justify-between gap-3 border-b border-line bg-surface px-4 py-3 sm:px-5">
        <h3 className={`${h3cls} text-[0.98rem]`}>{c.idTitle}</h3>
        <ExampleTag />
      </div>
      {c.idHandles.map((h, i) => (
        <div key={h.h} className="grid gap-1.5 border-b border-line px-4 py-3.5 sm:grid-cols-[1.1fr_1fr] sm:items-center sm:gap-4 sm:px-5">
          <span className="font-mono text-[0.84rem] text-ink">{h.h}</span>
          <span className="flex flex-wrap items-center gap-x-2.5 gap-y-1">
            <span className="flex gap-0.5" aria-hidden="true">
              {[0, 1, 2, 3].map((b) => (
                <span key={b} className={`h-3.5 w-1.5 rounded-sm ${b <= i ? 'bg-accent' : 'bg-line'}`} />
              ))}
            </span>
            <span className="text-[0.88rem] font-semibold text-ink">{h.level}</span>
            <span className="text-[0.84rem] text-ink3">{h.note}</span>
          </span>
        </div>
      ))}
      <p className="px-4 py-4 text-[0.92rem] leading-relaxed text-ink2 sm:px-5">{c.idNote}</p>
    </div>
  )
}

/**
 * The Console's read trail. The denied row says "denied" in text: a state
 * conveyed only by color disappears for people who cannot tell hues apart.
 */
export function AuditLog() {
  const { t } = useT()
  const c = t.products.console
  return (
    <div className={`${panel} overflow-hidden`}>
      <div className="flex items-center justify-between gap-3 border-b border-line bg-surface px-4 py-3 sm:px-5">
        <h3 className={`${h3cls} text-[0.98rem]`}>{c.auditTitle}</h3>
        <ExampleTag />
      </div>
      <div className="hidden grid-cols-[4rem_1.3fr_0.7fr_1.5fr] gap-4 border-b border-line px-5 py-2 text-[0.8rem] text-ink3 md:grid">
        {c.auditCols.map((col) => (
          <span key={col}>{col}</span>
        ))}
      </div>
      {c.auditRows.map((r) => (
        <div
          key={r.time}
          className={`grid gap-1 border-b border-line px-4 py-3.5 last:border-b-0 sm:px-5 md:grid-cols-[4rem_1.3fr_0.7fr_1.5fr] md:items-baseline md:gap-4 ${
            r.denied ? 'bg-bg' : ''
          }`}
        >
          <span className="font-mono text-[0.8rem] text-ink3">{r.time}</span>
          <span className="text-[0.95rem] font-semibold text-ink">{r.who}</span>
          <span className="text-[0.88rem] text-ink3">{r.why}</span>
          <span className={`text-[0.92rem] ${r.denied ? 'font-semibold text-ink' : 'text-ink2'}`}>{r.result}</span>
        </div>
      ))}
    </div>
  )
}

