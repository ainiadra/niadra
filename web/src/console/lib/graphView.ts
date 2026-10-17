/**
 * What the canvas writes and how big it is: the size of a node's label, the box of a node with its label for
 * the layout, and the plan of which labels show at a given zoom. At fifty nodes every label fits; at three
 * hundred they would cover each other, so labels go to the nodes that carry the structure first (the
 * customer of the screen, the selection and its neighbors, the search hits, companies and partners, agents
 * and systems, the most connected people) and to the rest only where there is room, or when the zoom makes
 * room. Pure functions: the canvas draws the plan and the tests check it.
 */
import type { GraphEdge, GraphNode, GraphNodeKind } from '../api/graph'
import { displayLabel, edgeSentence, edgeShortLabel } from './graphLabels'
import type { Layout, NodeBox, View } from './graphLayout'
import { controlPoint, curvePoint, type Point } from './graphScene'

/** The radius that encloses each kind's shape (screens/graph/shapes.tsx), for the rings, the label offset and the hit area. */
export const NODE_RADIUS: Record<GraphNodeKind, number> = {
  person: 14,
  account: 14,
  partner: 15,
  handle: 8,
  object: 15,
  agent: 14,
  system: 15,
}

/** Labels in the code face (pseudonyms, identifiers) are wider than the ones in the text face. */
export const monoLabel = (n: GraphNode) => n.kind === 'handle' || n.kind === 'person' || n.kind === 'account' || n.kind === 'partner'

/** The width of a label on screen at mark scale 1, with the halo around the letters. */
export const labelWidth = (n: GraphNode) => displayLabel(n).length * (monoLabel(n) ? 7.2 : 6.6) + 8

const LABEL_HEIGHT = 16
/** Where the label starts under the mark, from the mark's center, in mark units. */
const LABEL_GAP = 4

/**
 * How big a node draws, relative to its kind's radius: the customer of the screen biggest, then people and
 * companies by how connected they are, then objects, agents and systems, and identifiers smallest.
 */
export function weight(n: GraphNode, degree: number, isCenter: boolean): number {
  if (isCenter) return 1.5
  if (n.kind === 'person' || n.kind === 'account' || n.kind === 'partner') return 1 + Math.min(degree, 8) * 0.05
  if (n.kind === 'handle') return 0.75
  return 0.9
}

/** The box of a node with its label at 1:1, for the layout's collision between neighbors. */
export function nodeBox(n: GraphNode, w: number): NodeBox {
  const r = NODE_RADIUS[n.kind] * w
  // Wider than 1:1: when the fit zooms out, the marks stop shrinking at 0.6x and take more room. A company
  // or a partner keeps twice that, so its label, which the map always writes, has clear ground around it.
  const room = n.kind === 'account' || n.kind === 'partner' ? 2.2 : 1.3
  return { width: Math.max(labelWidth(n), r * 2) * room, height: (r * 2 + LABEL_GAP + LABEL_HEIGHT) * room }
}

/**
 * Marks keep a readable size on screen: they follow the zoom only between 0.6x and 1.3x, so a small graph can
 * fill the canvas without the shapes growing with it, and a large one can zoom out without the shapes vanishing.
 * Very far out (a phone holding the whole map) they go down to 0.4x, so a block of dots stays a block of dots.
 */
export const markScale = (scale: number) => Math.min(1.3, Math.max(scale < 0.25 ? 0.4 : 0.6, scale)) / scale

/** Labels stop shrinking sooner than marks: under 0.85x the letters would be too small to read. */
export const textScale = (scale: number) => Math.min(1.3, Math.max(0.85, scale)) / scale

/**
 * How much to grow the layout's boxes once the fit scale is known: the boxes assume labels at 1.3x at most,
 * and a far zoom draws them bigger than that against the drawing. One when nothing needs to grow; capped, because
 * a phone canvas cannot hold a hundred labels whatever the layout does, and the plan drops the rest.
 */
export const boxGrowth = (fitScale: number) => Math.min(3.5, Math.max(1, (textScale(fitScale) * fitScale) / fitScale / 1.3))

/** How many of the most connected people get a label on a crowded map. */
const TOP_PEOPLE = 12

export interface ScreenBox {
  x: number
  y: number
  w: number
  h: number
}

export interface LabelPlanInput {
  nodes: Iterable<GraphNode>
  layout: Layout
  visible: ReadonlySet<string>
  view: View
  /** The visible degree of each node. */
  degrees: ReadonlyMap<string, number>
  center: string | null
  activeId: string | null
  hoverId: string | null
  /** The neighbors of the active or hovered node. */
  neighbors: ReadonlySet<string>
  matches: ReadonlySet<string>
}

/** Where a label is written against its mark: under it; over it when under is taken; to its right as the last resort. */
export type LabelSide = 'below' | 'above' | 'right'

export interface LabelPlan {
  /** The nodes whose label is written, and on which side of the mark. */
  shown: Map<string, LabelSide>
  /** The label boxes written, on screen, and the mark boxes of every visible node: what the words on the lines avoid. */
  blocks: ScreenBox[]
}

const overlaps = (a: ScreenBox, b: ScreenBox) => a.x < b.x + b.w && a.x + a.w > b.x && a.y < b.y + b.h && a.y + a.h > b.y

/** The box a label takes on the side the plan chose. */
export const labelBoxOn = (b: { label: ScreenBox; labelAbove: ScreenBox; labelRight: ScreenBox }, side: LabelSide) => (side === 'below' ? b.label : side === 'above' ? b.labelAbove : b.labelRight)

/** The box of a node's mark on screen, and the boxes its label may take, for a mark scale and a view. */
export function screenBoxes(n: GraphNode, w: number, x: number, y: number, view: View): { mark: ScreenBox; label: ScreenBox; labelAbove: ScreenBox; labelRight: ScreenBox } {
  const ks = markScale(view.scale) * view.scale
  const ts = textScale(view.scale) * view.scale
  const r = NODE_RADIUS[n.kind] * w * ks
  const cx = x * view.scale + view.tx
  const cy = y * view.scale + view.ty
  const lw = labelWidth(n) * ts
  const lh = LABEL_HEIGHT * ts
  return {
    mark: { x: cx - r - 2, y: cy - r - 2, w: (r + 2) * 2, h: (r + 2) * 2 },
    label: { x: cx - lw / 2, y: cy + r + LABEL_GAP * ts, w: lw, h: lh },
    labelAbove: { x: cx - lw / 2, y: cy - r - LABEL_GAP * ts - lh, w: lw, h: lh },
    labelRight: { x: cx + r + LABEL_GAP * ts, y: cy - lh / 2, w: lw, h: lh },
  }
}

/**
 * Which labels to write: by priority, each one only where it covers no mark and no label already written.
 * The selection, the hovered node and the customer of the screen always come first, so what the person is
 * looking at is never the label that gets dropped.
 */
export function labelPlan(input: LabelPlanInput): LabelPlan {
  const { layout, visible, view, degrees, center, activeId, hoverId, neighbors, matches } = input
  const list = [...input.nodes].filter((n) => visible.has(n.id) && layout.nodes.has(n.id))
  const boxes = new Map<string, { mark: ScreenBox; label: ScreenBox; labelAbove: ScreenBox; labelRight: ScreenBox }>()
  for (const n of list) {
    const p = layout.nodes.get(n.id)!
    boxes.set(n.id, screenBoxes(n, weight(n, degrees.get(n.id) ?? 0, n.id === center), p.x, p.y, view))
  }
  const topPeople = new Set(
    list
      .filter((n) => n.kind === 'person')
      .sort((a, b) => (degrees.get(b.id) ?? 0) - (degrees.get(a.id) ?? 0) || a.id.localeCompare(b.id))
      .slice(0, TOP_PEOPLE)
      .map((n) => n.id),
  )
  const priority = (n: GraphNode): number => {
    if (n.id === center) return 0
    if (n.id === activeId || n.id === hoverId) return 1
    if (neighbors.has(n.id)) return 2
    if (matches.has(n.id)) return 3
    if (n.kind === 'account' || n.kind === 'partner') return 4
    if (n.kind === 'agent' || n.kind === 'system') return 5
    if (n.kind === 'object' || n.kind === 'handle') return 6
    if (topPeople.has(n.id)) return 7
    // Everyone else is a candidate too: on a map with room they are all written, on a crowded one they yield.
    return 8
  }
  const ordered = list
    .map((n) => ({ n, p: priority(n) }))
    .sort((a, b) => a.p - b.p || (degrees.get(b.n.id) ?? 0) - (degrees.get(a.n.id) ?? 0) || a.n.id.localeCompare(b.n.id))
  const marks = list.map((n) => boxes.get(n.id)!.mark)
  const written: ScreenBox[] = []
  const shown = new Map<string, LabelSide>()
  const free = (box: ScreenBox, self: string) => !written.some((w) => overlaps(box, w)) && !marks.some((m, i) => list[i]!.id !== self && overlaps(box, m))
  for (const { n, p } of ordered) {
    const b = boxes.get(n.id)!
    // What the person points at is written even over something. Everything else yields: under the mark first,
    // over it second, to its right last. Companies and partners come first in line, and the layout keeps
    // ground around them, so on a desktop map nearly all of them are written.
    const must = p <= 1
    const side: LabelSide | null = must || free(b.label, n.id) ? 'below' : free(b.labelAbove, n.id) ? 'above' : free(b.labelRight, n.id) ? 'right' : null
    if (!side) continue
    written.push(side === 'below' ? b.label : side === 'above' ? b.labelAbove : b.labelRight)
    shown.set(n.id, side)
  }
  return { shown, blocks: [...marks, ...written] }
}

export interface EdgeLabel {
  edge: GraphEdge
  text: string
  /** The middle of the pill, in layout units. */
  x: number
  y: number
  /** The pill's width at mark scale 1. */
  width: number
  /** The line under the pointer: its whole sentence, in the lime pill. */
  strong: boolean
}

/** The width of a pill at mark scale 1: the text in the text face, with its padding. */
const pillWidth = (text: string) => text.length * 6.3 + 14

/**
 * The words on the lines: of one node (the focused one, else the customer of the screen), or of every line
 * when `every` (a map small enough to read whole). Each sits on its line, longest lines first, and a word
 * that would cover another, a mark or a label is left out (the card and the list still say it). The line
 * under the pointer always gets its whole sentence. Positions come from `pos`, so the words follow the
 * drawing, drift included.
 */
export function placeEdgeLabels(
  edges: readonly GraphEdge[],
  nodeId: string | null,
  every: boolean,
  pos: (id: string) => Point | undefined,
  nodes: ReadonlyMap<string, GraphNode>,
  view: View,
  k: number,
  size: { width: number; height: number },
  hoverEdge: string | null,
  blocks: readonly ScreenBox[],
): EdgeLabel[] {
  const out: EdgeLabel[] = []
  const taken: ScreenBox[] = [...blocks]
  const candidates = edges
    .filter((e) => every || e.id === hoverEdge || (nodeId !== null && (e.from === nodeId || e.to === nodeId)))
    .map((e) => {
      const a = pos(e.from)
      const b = pos(e.to)
      if (!a || !b) return null
      return { e, a, b, len: Math.hypot(b.x - a.x, b.y - a.y) * view.scale }
    })
    .filter((c): c is NonNullable<typeof c> => c !== null)
    .sort((p, q) => (p.e.id === hoverEdge ? -1 : q.e.id === hoverEdge ? 1 : q.len - p.len))
  for (const c of candidates) {
    const strong = c.e.id === hoverEdge
    const text = strong ? edgeSentence(c.e, nodes) : edgeShortLabel(c.e)
    const width = pillWidth(text)
    const sw = width * k * view.scale
    const sh = 20 * k * view.scale
    if (!strong && c.len < sw * 0.9) continue
    const ctrl = controlPoint(c.a, c.b, c.e.id)
    // The middle of the curve first; when something sits there, a little toward either end.
    for (const t of strong ? [0.5] : [0.5, 0.38, 0.62, 0.28, 0.72]) {
      const { x, y } = curvePoint(c.a, ctrl, c.b, t)
      const sx = x * view.scale + view.tx
      const sy = y * view.scale + view.ty
      if (size.width > 0 && (sx < -sw || sy < -sh || sx > size.width + sw || sy > size.height + sh)) break
      const box = { x: sx - sw / 2, y: sy - sh / 2, w: sw, h: sh }
      if (!strong && taken.some((o) => overlaps(box, o))) continue
      taken.push(box)
      out.push({ edge: c.e, text, x, y, width, strong })
      break
    }
  }
  return out
}
