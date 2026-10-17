import type { GraphNodeKind } from '../../api/graph'
import { GROUND, KIND_STYLE } from '../../lib/graphTheme'

/**
 * The swatch of the legend, the card and the list: the mark of a kind as the canvas draws it, on a chip of
 * the canvas's own dark ground, so the legend and the drawing speak the same language (lib/graphTheme). One
 * shape per kind, so a reader tells them apart in black and white: a person is a cell, a company a rounded
 * square, a partner a diamond outline, an identifier a small ring, an object an outlined record, an agent a
 * hexagon, a system an outlined rectangle.
 */

const HEX = (r: number) =>
  Array.from({ length: 6 }, (_, i) => {
    const a = (Math.PI / 3) * i - Math.PI / 6
    return `${(Math.cos(a) * r).toFixed(2)},${(Math.sin(a) * r).toFixed(2)}`
  }).join(' ')

/** The mark of a node, centered on (0, 0), at the swatch's size. */
function NodeShape({ kind }: { kind: GraphNodeKind }) {
  const { color, hollow, glow } = KIND_STYLE[kind]
  const halo = glow > 0 ? <circle r={10} fill={color} opacity={0.18} /> : null
  switch (kind) {
    case 'person':
      return (
        <>
          {halo}
          <circle r={5.5} fill={color} />
          <circle cx={-1} cy={-1} r={2.2} fill="#ffffff" opacity={0.55} />
        </>
      )
    case 'account':
      return (
        <>
          {halo}
          <rect x={-5.5} y={-5.5} width={11} height={11} rx={1.5} fill={color} />
        </>
      )
    case 'partner':
      return (
        <>
          {halo}
          <rect x={-4.5} y={-4.5} width={9} height={9} rx={1} transform="rotate(45)" fill={hollow ? 'none' : color} stroke={color} strokeWidth={1.75} />
        </>
      )
    case 'handle':
      return <circle r={3.5} fill={GROUND} stroke={color} strokeWidth={1.75} />
    case 'object':
      return <rect x={-6} y={-4} width={12} height={8} rx={1.5} fill={GROUND} stroke={color} strokeWidth={1.5} />
    case 'agent':
      return (
        <>
          {halo}
          <polygon points={HEX(6)} fill={color} />
        </>
      )
    case 'system':
      return (
        <>
          {halo}
          <rect x={-6} y={-4.5} width={12} height={9} rx={1.5} fill={GROUND} stroke={color} strokeWidth={1.75} />
        </>
      )
  }
}

export function KindSwatch({ kind }: { kind: GraphNodeKind }) {
  return (
    <svg width="22" height="22" viewBox="-11 -11 22 22" aria-hidden="true" className="shrink-0">
      <rect x={-11} y={-11} width={22} height={22} rx={5} fill={GROUND} />
      <NodeShape kind={kind} />
    </svg>
  )
}
