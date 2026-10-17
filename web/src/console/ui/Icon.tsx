/**
 * Console icons, drawn on the site's grid (24, stroke 1.75, round caps; see
 * src/components/Icon.tsx). Each one stands for the job of its screen:
 * magnifier = find a customer, receipt = who read what, key = agent keys,
 * gauge = usage and context use. No generic "AI" glyphs.
 */
const PATHS = {
  search: 'M10.5 4a6.5 6.5 0 1 0 0 13 6.5 6.5 0 0 0 0-13ZM15.5 15.5 20 20',
  receipt: 'M6 3h12v18l-2.5-1.5L13 21l-2.5-1.5L8 21l-2-1.2V3ZM9 8h6M9 12h6M9 16h3',
  key: 'M7.5 13a3.5 3.5 0 1 0 0 7 3.5 3.5 0 0 0 0-7ZM10 14 20 4M17 7l3 3M14 10l2 2',
  gauge: 'M4 16a8 8 0 1 1 16 0M12 16l4-5M3.5 20h17',
  exit: 'M14 4h4a1 1 0 0 1 1 1v14a1 1 0 0 1-1 1h-4M10 8l-4 4 4 4M6 12h10',
  swap: 'M7 4 4 7l3 3M4 7h13M17 20l3-3-3-3M20 17H7',
  chevron: 'M9 6l6 6-6 6',
  back: 'M15 6l-6 6 6 6',
  close: 'M6 6l12 12M18 6 6 18',
  copy: 'M9 9h10v11H9zM5 15V4h10',
  check: 'M4.5 12.5l5 5L20 7',
  alert: 'M12 4 2.8 19.5h18.4L12 4ZM12 10v4.5M12 17.2v.3',
  block: 'M12 3.5a8.5 8.5 0 1 0 0 17 8.5 8.5 0 0 0 0-17ZM6 6l12 12',
  eye: 'M2.5 12S6 5.5 12 5.5 21.5 12 21.5 12 18 18.5 12 18.5 2.5 12 2.5 12ZM12 9.5a2.5 2.5 0 1 0 0 5 2.5 2.5 0 0 0 0-5Z',
  layers: 'M12 3.5 3.5 8 12 12.5 20.5 8 12 3.5ZM3.5 12 12 16.5 20.5 12M3.5 16 12 20.5 20.5 16',
  menu: 'M3.5 7h17M3.5 12h17M3.5 17h17',
  ring: 'M12 4.5a7.5 7.5 0 1 0 0 15 7.5 7.5 0 0 0 0-15Z',
  // Two identifiers joined into one customer: identity.
  link: 'M10 14a4 4 0 0 0 5.66 0l3-3a4 4 0 0 0-5.66-5.66l-1.2 1.2M14 10a4 4 0 0 0-5.66 0l-3 3a4 4 0 0 0 5.66 5.66l1.2-1.2',
  lock: 'M7.5 11V8a4.5 4.5 0 0 1 9 0v3M6 11h12a1 1 0 0 1 1 1v7a1 1 0 0 1-1 1H6a1 1 0 0 1-1-1v-7a1 1 0 0 1 1-1Z',
  // A system plugged into the memory: mappings and webhooks.
  plug: 'M9 3v5M15 3v5M6.5 8h11v3a5.5 5.5 0 0 1-11 0V8ZM12 16.5V21',
  bell: 'M6 16V11a6 6 0 0 1 12 0v5l1.5 2h-15L6 16ZM10 20.5a2 2 0 0 0 4 0',
  sliders: 'M4 7h9M17 7h3M4 17h3M11 17h9M15 5v4M9 15v4',
  download: 'M12 4v11M7.5 10.5 12 15l4.5-4.5M5 19.5h14',
  // A turn as its steps, one after the other: what an agent read, called and decided.
  steps: 'M4 5.5h3M4 12h3M4 18.5h3M10 5.5h10M10 12h10M10 18.5h6M5.5 7v3.5M5.5 13.5V17',
  // The signed-in person: their account and second factor.
  person: 'M12 4a4 4 0 1 0 0 8 4 4 0 0 0 0-8ZM4.5 20.5a7.5 7.5 0 0 1 15 0',
  plus: 'M12 5v14M5 12h14',
  // Several people of the tenant: people and roles.
  people: 'M9 5a3.5 3.5 0 1 0 0 7 3.5 3.5 0 0 0 0-7ZM2.5 19.5a6.5 6.5 0 0 1 13 0M16 5.5a3 3 0 0 1 0 6M18 14a5.5 5.5 0 0 1 3.5 5.5',
  // A door with a key: the company's single sign-on.
  door: 'M5 21V4.5A1.5 1.5 0 0 1 6.5 3h8A1.5 1.5 0 0 1 16 4.5V21M3 21h18M12.5 12h.01M19 9v5M17 11.5h4',
  // A question in a ring: the help of a screen and the tutorials.
  help: 'M12 3.5a8.5 8.5 0 1 0 0 17 8.5 8.5 0 0 0 0-17ZM9.6 9.4a2.4 2.4 0 1 1 3.4 2.2c-.7.4-1 .8-1 1.6M12 16.4v.1',
  // The first screen of a space: the house.
  home: 'M4 11 12 4l8 7M6 10v10h4.5v-5h3v5H18V10',
  // An open book: a guided walkthrough.
  book: 'M12 6.5c-1.5-1.3-3.5-2-6-2v13c2.5 0 4.5.7 6 2 1.5-1.3 3.5-2 6-2v-13c-2.5 0-4.5.7-6 2ZM12 6.5v13',
  // A bar that fills: a checklist step done or not.
  flag: 'M6 21V4M6 4h11l-2.5 4L17 12H6',
  // Three items joined by lines: the map of relationships.
  graph: 'M12 5a2.5 2.5 0 1 0 0 5 2.5 2.5 0 0 0 0-5ZM5.5 15a2.5 2.5 0 1 0 0 5 2.5 2.5 0 0 0 0-5ZM18.5 15a2.5 2.5 0 1 0 0 5 2.5 2.5 0 0 0 0-5ZM10.8 9.6l-3.9 5.4M13.2 9.6l3.9 5.4M8 17.5h8',
  // A frame with arrows out of its corners: fit everything in view.
  fit: 'M4 9V4h5M15 4h5v5M20 15v5h-5M9 20H4v-5M9.5 9.5 4.5 4.5M14.5 9.5l5-5M14.5 14.5l5 5M9.5 14.5l-5 5',
  // A table of rows: the list view of the same information.
  list: 'M4 6.5h16M4 12h16M4 17.5h16',
  minus: 'M5 12h14',
} as const

export type IconName = keyof typeof PATHS

export function Icon({ name, size = 18, className = '' }: { name: IconName; size?: number; className?: string }) {
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
