/**
 * Niadra's icons, drawn for the site (not an imported library).
 * 24 grid, 1.75 stroke, round caps and joins. Each icon exists because the
 * content beside it asks for that sign: bolt = speed, padlock = encryption,
 * eye = recorded read, magnifier = history search. No generic "AI" glyph
 * (sparkle, wand, robot).
 */
const PATHS = {
  bolt: 'M13 2.5 4.5 13.5H11l-1 8 8.5-11H12l1-8Z',
  hub: 'M12 9.5a2.5 2.5 0 1 0 0 5 2.5 2.5 0 0 0 0-5ZM5 3a2 2 0 1 0 0 4 2 2 0 0 0 0-4ZM19 3a2 2 0 1 0 0 4 2 2 0 0 0 0-4ZM12 18a2 2 0 1 0 0 4 2 2 0 0 0 0-4ZM6.4 6.4l3.8 3.8M17.6 6.4l-3.8 3.8M12 14.5V18',
  shield: 'M12 3l7 3v5.5c0 4.4-2.9 8.3-7 9.5-4.1-1.2-7-5.1-7-9.5V6l7-3ZM9 12l2.2 2.2L15.2 10',
  code: 'M8 8l-4 4 4 4M16 8l4 4-4 4M13.5 5l-3 14',
  lock: 'M7.5 11V8a4.5 4.5 0 0 1 9 0v3M6 11h12a1 1 0 0 1 1 1v7a1 1 0 0 1-1 1H6a1 1 0 0 1-1-1v-7a1 1 0 0 1 1-1ZM12 14.5v2.5',
  key: 'M7.5 13a3.5 3.5 0 1 0 0 7 3.5 3.5 0 0 0 0-7ZM10 14 20 4M17 7l3 3M14 10l2 2',
  target: 'M12 4a8 8 0 1 0 0 16 8 8 0 0 0 0-16ZM12 8.5a3.5 3.5 0 1 0 0 7 3.5 3.5 0 0 0 0-7Z',
  eye: 'M2.5 12S6 5.5 12 5.5 21.5 12 21.5 12 18 18.5 12 18.5 2.5 12 2.5 12ZM12 9.5a2.5 2.5 0 1 0 0 5 2.5 2.5 0 0 0 0-5Z',
  doc: 'M7 3h7l4 4v13a1 1 0 0 1-1 1H7a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1ZM14 3v4h4M9 14l2 2 4-4',
  globe:
    'M12 3.5a8.5 8.5 0 1 0 0 17 8.5 8.5 0 0 0 0-17ZM3.5 12h17M12 3.5c2.5 2.3 3.8 5.2 3.8 8.5s-1.3 6.2-3.8 8.5c-2.5-2.3-3.8-5.2-3.8-8.5s1.3-6.2 3.8-8.5Z',
  user: 'M10 4.5a3.5 3.5 0 1 0 0 7 3.5 3.5 0 0 0 0-7ZM3.5 20c0-3.6 2.9-6 6.5-6 1.2 0 2.3.3 3.3.7M15 17.5l2 2 4-4',
  layers: 'M12 3.5 3.5 8 12 12.5 20.5 8 12 3.5ZM3.5 12 12 16.5 20.5 12M3.5 16 12 20.5 20.5 16',
  search: 'M10.5 4a6.5 6.5 0 1 0 0 13 6.5 6.5 0 0 0 0-13ZM15.5 15.5 20 20',
  archive:
    'M4 6.5C4 5 7.6 4 12 4s8 1 8 2.5S16.4 9 12 9 4 8 4 6.5ZM4 6.5v11C4 19 7.6 20 12 20s8-1 8-2.5v-11M4 12c0 1.5 3.6 2.5 8 2.5s8-1 8-2.5',
  sliders: 'M4 7h9M17 7h3M4 17h3M11 17h9M15 5v4M9 15v4',
  clock: 'M12 3.5a8.5 8.5 0 1 0 0 17 8.5 8.5 0 0 0 0-17ZM12 7.5V12l3 2',
  cloud: 'M7 18.5a4 4 0 0 1-.6-7.96A5.5 5.5 0 0 1 17 10a4.25 4.25 0 0 1 .5 8.5H7Z',
  check: 'M4.5 12.5l5 5L20 7',
  // The products that had no sign of their own: plug = connected systems, repeat = pattern,
  // bell = alert, bars = measurement.
  plug: 'M9 3v4.5M15 3v4.5M6.5 7.5h11V11a5.5 5.5 0 0 1-11 0V7.5ZM12 16.5V21',
  repeat: 'M4.5 11V9.5a4 4 0 0 1 4-4h11M16.5 2.5l3 3-3 3M19.5 13v1.5a4 4 0 0 1-4 4h-11M7.5 21.5l-3-3 3-3',
  bell: 'M6.5 16.5V11a5.5 5.5 0 0 1 11 0v5.5l1.5 2h-14l1.5-2ZM10 20.5a2 2 0 0 0 4 0',
  chart: 'M4 20.5h16M7 16.5V12M12 16.5V7.5M17 16.5v-6',
  // Denied by purpose: the crossed-out circle says "does not reach this agent".
  block: 'M12 3.5a8.5 8.5 0 1 0 0 17 8.5 8.5 0 0 0 0-17ZM6 6l12 12',
  // The memory that operates: gauge = the state of now, with its age; claim = what the agent says,
  // checked; merge = who holds the customer, many hands into one; record = the turn record, line by line.
  gauge: 'M4.5 16.5a7.5 7.5 0 0 1 15 0M12 16.5l3.5-4.5M6.5 20.5h11',
  claim: 'M4 5.5h16v10h-8l-4.5 4v-4H4v-10ZM8.5 10.5l2.2 2.2 4.8-4.8',
  merge: 'M6 3.5v6a4 4 0 0 0 4 4h4a4 4 0 0 1 4 4v3M18 3.5v4M14.5 17.5 18 21l3.5-3.5',
  record: 'M6 3.5h12a1 1 0 0 1 1 1v15a1 1 0 0 1-1 1H6a1 1 0 0 1-1-1v-15a1 1 0 0 1 1-1ZM8.5 8h7M8.5 12h7M8.5 16h4',
} as const

export type IconName = keyof typeof PATHS

export function Icon({ name, size = 20, className = '' }: { name: IconName; size?: number; className?: string }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.75"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      className={`shrink-0 ${className}`}
    >
      <path d={PATHS[name]} />
    </svg>
  )
}

/**
 * An icon inside a frame. `soft` (pale lime frame) only directly on the section's
 * light ground. `plain` (white frame with a hairline) on a pale lime section and inside
 * a card: pale lime never goes inside a panel.
 */
export function IconChip({ name, tone = 'soft' }: { name: IconName; tone?: 'soft' | 'plain' }) {
  const box = tone === 'soft' ? 'bg-bg2 text-ink' : 'border border-line bg-surface text-ink'
  return (
    <span className={`flex h-10 w-10 shrink-0 items-center justify-center rounded-lg ${box}`}>
      <Icon name={name} />
    </span>
  )
}
