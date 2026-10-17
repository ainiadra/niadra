import { encode } from 'uqr'

/**
 * A QR code as an SVG of our own: uqr only computes the modules, nothing is injected as HTML.
 * Dark modules on white with a quiet zone, the contrast authenticator apps read in any theme.
 */
export function QrCode({ value, label, size = 184 }: { value: string; label: string; size?: number }) {
  const { data } = encode(value, { border: 0, ecc: 'M' })
  const n = data.length
  const quiet = 4
  const path = data
    .flatMap((row, y) => row.map((on, x) => (on ? `M${x + quiet} ${y + quiet}h1v1h-1z` : '')))
    .join('')
  return (
    <svg
      role="img"
      aria-label={label}
      width={size}
      height={size}
      viewBox={`0 0 ${n + quiet * 2} ${n + quiet * 2}`}
      shapeRendering="crispEdges"
      className="rounded-lg border border-line"
    >
      <rect width="100%" height="100%" fill="#ffffff" />
      <path d={path} fill="#111113" />
    </svg>
  )
}
