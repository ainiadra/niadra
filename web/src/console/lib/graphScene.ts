/**
 * The life of the drawing, as pure functions of time: the curve of each line, the slow breathing of the cells
 * at rest, the activation of a node (a ripple, pulses travelling along its lines, the neighbors lighting up
 * as each pulse arrives), the camera gliding to a node, and the lobes behind each company. The canvas asks
 * these at every frame and draws the answer; the tests ask them at chosen times and check the numbers. Every
 * value that depends on an id depends on it through a hash, so two frames at the same time agree and the
 * same graph breathes the same way twice.
 */
import type { GraphEdge } from '../api/graph'
import type { Graph } from './graph'
import { hashSeed, type Layout, type View } from './graphLayout'

export interface Point {
  x: number
  y: number
}

/** A number in [0, 1) from an id, the same every time. */
const unit = (id: string) => hashSeed(id) / 4294967296

/** Apple-like glide: fast out of the gate, settling softly (easeOutQuart). */
const easeOut = (t: number) => 1 - (1 - Math.min(1, Math.max(0, t))) ** 4

/**
 * Lines bow a little, each to its own side, so a bundle of lines into a hub fans out instead of stacking,
 * and the drawing reads organic. The bow grows with the length and stops at a cap, so a short tie stays nearly
 * straight and a long line to an agent on the ring arcs around the middle.
 */
export function controlPoint(a: Point, b: Point, edgeId: string): Point {
  const dx = b.x - a.x
  const dy = b.y - a.y
  const len = Math.hypot(dx, dy) || 1
  const bow = Math.min(40, 6 + len * 0.11) * (unit(edgeId) < 0.5 ? -1 : 1)
  return { x: (a.x + b.x) / 2 - (dy / len) * bow, y: (a.y + b.y) / 2 + (dx / len) * bow }
}

/** The point at `t` of the quadratic curve from `a` through `c` to `b`. */
export function curvePoint(a: Point, c: Point, b: Point, t: number): Point {
  const u = 1 - t
  return { x: u * u * a.x + 2 * u * t * c.x + t * t * b.x, y: u * u * a.y + 2 * u * t * c.y + t * t * b.y }
}

/** The distance from a point to the curve, by sampling: enough for a pointer looking for a line. */
export function distanceToCurve(p: Point, a: Point, c: Point, b: Point, samples = 12): number {
  let best = Infinity
  let prev = a
  for (let i = 1; i <= samples; i += 1) {
    const q = curvePoint(a, c, b, i / samples)
    best = Math.min(best, distanceToSegment(p, prev, q))
    prev = q
  }
  return best
}

function distanceToSegment(p: Point, a: Point, b: Point): number {
  const dx = b.x - a.x
  const dy = b.y - a.y
  const l2 = dx * dx + dy * dy
  const t = l2 === 0 ? 0 : Math.min(1, Math.max(0, ((p.x - a.x) * dx + (p.y - a.y) * dy) / l2))
  return Math.hypot(p.x - (a.x + dx * t), p.y - (a.y + dy * t))
}

/** The nearest node to a screen point within `radius` screen pixels, or null. */
export function nodeAt(layout: Layout, view: View, ids: ReadonlySet<string>, px: number, py: number, radius: (id: string) => number): string | null {
  let best: string | null = null
  let bestD = Infinity
  for (const n of layout.nodes.values()) {
    if (!ids.has(n.id)) continue
    const d = Math.hypot(n.x * view.scale + view.tx - px, n.y * view.scale + view.ty - py)
    if (d <= radius(n.id) && d < bestD) {
      bestD = d
      best = n.id
    }
  }
  return best
}

/** The nearest line to a screen point within `tolerance` screen pixels, or null. */
export function edgeAt(edges: readonly GraphEdge[], layout: Layout, view: View, px: number, py: number, tolerance = 7): GraphEdge | null {
  const p = { x: (px - view.tx) / view.scale, y: (py - view.ty) / view.scale }
  let best: GraphEdge | null = null
  let bestD = tolerance / view.scale
  for (const e of edges) {
    const a = layout.nodes.get(e.from)
    const b = layout.nodes.get(e.to)
    if (!a || !b) continue
    const d = distanceToCurve(p, a, controlPoint(a, b, e.id), b)
    if (d < bestD) {
      bestD = d
      best = e
    }
  }
  return best
}

/** How long, in ms, each part of the life of the drawing takes. */
export const TIMING = {
  /** One breath of a cell at rest. */
  breath: 3600,
  /** The camera's glide to a node. */
  glide: 650,
  /** The ripple leaving an activated node. */
  ripple: 700,
  /** One pulse from the node to a neighbor. */
  pulse: 620,
  /** The gap between the pulses of one node's lines, so they leave one after another. */
  stagger: 38,
  /** How long a neighbor stays lit after the pulse reaches it, before settling into "a neighbor". */
  flash: 520,
  /** The slow circulation on a selected node's lines, once the burst is over. */
  circulate: 2400,
  /** The card sliding in. */
  card: 220,
} as const

export interface Breath {
  /** Multiplies the radius of the mark. */
  size: number
  /** Multiplies the halo's alpha. */
  glow: number
  /** A drift of the drawn position, in layout units. */
  dx: number
  dy: number
}

const STILL: Breath = { size: 1, glow: 1, dx: 0, dy: 0 }

/**
 * The breathing of a cell at time `t`: a slow swell of the mark and its halo and a drift of a pixel or two,
 * each node to its own phase. Nothing when motion is reduced, and the layout itself never moves: this is
 * a drawing offset, so the positions the tests and the keyboard use stay put.
 */
export function breath(id: string, t: number, reduced: boolean): Breath {
  if (reduced) return STILL
  const phase = unit(id) * Math.PI * 2
  const w = (t / TIMING.breath) * Math.PI * 2
  return {
    size: 1 + 0.035 * Math.sin(w + phase),
    glow: 1 + 0.18 * Math.sin(w + phase),
    dx: 1.4 * Math.sin(w * 0.47 + phase),
    dy: 1.4 * Math.cos(w * 0.41 + phase * 1.3),
  }
}

/** A pulse on its way along a line: where it is, and how bright. */
export interface Pulse {
  edge: GraphEdge
  /** 0 at the activated node, 1 at the neighbor. */
  t: number
  alpha: number
  /** The pulse's trail: positions a few frames back, for a comet tail. */
  trail: number[]
}

export interface Activation {
  id: string
  /** When it was activated, in the clock of `now`. */
  at: number
}

/**
 * The burst of an activation at time `now`: the pulses in flight along the node's lines, the longest lines
 * first (they take longest to arrive), each leaving a little after the one before. After the burst, while the
 * node stays selected, one faint pulse keeps circulating on each line, so the selection reads as alive.
 */
export function pulses(act: Activation, edges: readonly GraphEdge[], layout: Layout, now: number, reduced: boolean): Pulse[] {
  if (reduced) return []
  const mine = edges.filter((e) => e.from === act.id || e.to === act.id)
  const withLen = mine
    .map((e) => {
      const a = layout.nodes.get(e.from)
      const b = layout.nodes.get(e.to)
      return { e, len: a && b ? Math.hypot(b.x - a.x, b.y - a.y) : 0 }
    })
    .sort((p, q) => q.len - p.len || p.e.id.localeCompare(q.e.id))
  const out: Pulse[] = []
  const elapsed = now - act.at
  withLen.forEach(({ e }, i) => {
    const start = Math.min(i * TIMING.stagger, 420)
    const local = elapsed - start
    if (local < 0) return
    if (local <= TIMING.pulse) {
      const t = easeOut(local / TIMING.pulse)
      out.push({ edge: e, t, alpha: 1, trail: [0.06, 0.12, 0.18].map((d) => Math.max(0, t - d)) })
      return
    }
    // The circulation: a faint pulse per line, each at its own phase so they do not march in step.
    const phase = ((local - TIMING.pulse) / TIMING.circulate + unit(e.id)) % 1
    out.push({ edge: e, t: phase, alpha: 0.45, trail: [0.03, 0.06].map((d) => Math.max(0, phase - d)) })
  })
  return out
}

/**
 * How lit a neighbor is at `now`: nothing until the pulse arrives, a flash at arrival, then the steady light
 * of a neighbor. The pulse index follows the same order as `pulses`.
 */
export function neighborLight(act: Activation, edges: readonly GraphEdge[], layout: Layout, now: number, reduced: boolean): Map<string, number> {
  const out = new Map<string, number>()
  const mine = edges.filter((e) => e.from === act.id || e.to === act.id)
  const withLen = mine
    .map((e) => {
      const a = layout.nodes.get(e.from)
      const b = layout.nodes.get(e.to)
      return { e, len: a && b ? Math.hypot(b.x - a.x, b.y - a.y) : 0 }
    })
    .sort((p, q) => q.len - p.len || p.e.id.localeCompare(q.e.id))
  const elapsed = now - act.at
  withLen.forEach(({ e }, i) => {
    const other = e.from === act.id ? e.to : e.from
    const arrival = reduced ? 0 : Math.min(i * TIMING.stagger, 420) + TIMING.pulse
    const since = elapsed - arrival
    let light = 0
    if (since >= 0) light = reduced ? 1 : 1 + 0.9 * Math.max(0, 1 - since / TIMING.flash)
    out.set(other, Math.max(out.get(other) ?? 0, light))
  })
  return out
}

/** The ripple leaving an activated node: its radius beyond the mark and its alpha, or null once it is gone. */
export function ripple(act: Activation, now: number, reduced: boolean): { grow: number; alpha: number } | null {
  if (reduced) return null
  const t = (now - act.at) / TIMING.ripple
  if (t < 0 || t >= 1) return null
  const k = easeOut(t)
  return { grow: 6 + 42 * k, alpha: 0.85 * (1 - k) }
}

export interface Glide {
  from: View
  to: View
  at: number
}

/** The view at `now` of a glide from one view to another. */
export function glideView(g: Glide, now: number): View {
  const k = easeOut((now - g.at) / TIMING.glide)
  return { scale: g.from.scale + (g.to.scale - g.from.scale) * k, tx: g.from.tx + (g.to.tx - g.from.tx) * k, ty: g.from.ty + (g.to.ty - g.from.ty) * k }
}

export const glideDone = (g: Glide, now: number) => now - g.at >= TIMING.glide

/**
 * The view that lights a node: it comes to the middle of the canvas, and the drawing zooms in a little when
 * it was far out, never out (less on a phone, whose canvas holds the whole map at a small scale and would
 * lose it). The card floats over the right of the canvas from 640 px, so the node sits a little left of the
 * middle there, where the card does not cover it.
 */
export function viewOnNode(view: View, x: number, y: number, width: number, height: number, cardOnRight: boolean): View {
  const scale = Math.min(1.3, Math.max(view.scale, width < 640 ? 0.7 : 0.85))
  const cx = cardOnRight ? width * 0.4 : width / 2
  // On a phone the card is a sheet over the lower half, so the node sits in the upper part.
  const cy = cardOnRight ? height / 2 : height * 0.2
  return { scale, tx: cx - x * scale, ty: cy - y * scale }
}

export interface Region {
  id: string
  x: number
  y: number
  radius: number
  /** The outline of the group, counterclockwise: the convex hull of the company and its people. */
  hull: Point[]
}

/** The convex hull of a few points (Andrew's monotone chain), counterclockwise; a point or a pair as is. */
export function convexHull(points: readonly Point[]): Point[] {
  const pts = [...points].sort((a, b) => a.x - b.x || a.y - b.y)
  if (pts.length < 3) return pts
  const cross = (o: Point, a: Point, b: Point) => (a.x - o.x) * (b.y - o.y) - (a.y - o.y) * (b.x - o.x)
  const lower: Point[] = []
  for (const q of pts) {
    while (lower.length >= 2 && cross(lower[lower.length - 2]!, lower[lower.length - 1]!, q) <= 0) lower.pop()
    lower.push(q)
  }
  const upper: Point[] = []
  for (let i = pts.length - 1; i >= 0; i -= 1) {
    const q = pts[i]!
    while (upper.length >= 2 && cross(upper[upper.length - 2]!, upper[upper.length - 1]!, q) <= 0) upper.pop()
    upper.push(q)
  }
  lower.pop()
  upper.pop()
  return [...lower, ...upper]
}

/**
 * The lobes: behind each company or partner and the people tied to it, a soft region, the hull of the
 * group, rounded and blurred by the canvas, so the map reads as regions at a glance. A company with nobody
 * tied to it on the canvas gets no region; a person in two companies belongs to both lobes, which is what
 * two overlapping lobes say.
 */
export function regions(graph: Graph, layout: Layout, visible: ReadonlySet<string>): Region[] {
  const members = new Map<string, string[]>()
  for (const e of graph.edges.values()) {
    if (e.kind !== 'member_of' && e.kind !== 'partner_of' && e.kind !== 'parent_of') continue
    if (!visible.has(e.from) || !visible.has(e.to)) continue
    const hub = graph.nodes.get(e.to)
    if (!hub || (hub.kind !== 'account' && hub.kind !== 'partner')) continue
    const list = members.get(e.to) ?? []
    list.push(e.from)
    members.set(e.to, list)
  }
  const out: Region[] = []
  for (const [hub, list] of members) {
    const h = layout.nodes.get(hub)
    if (!h) continue
    const pts = [h, ...list.map((id) => layout.nodes.get(id)).filter((p): p is NonNullable<typeof p> => Boolean(p))]
    const x = pts.reduce((s, p) => s + p.x, 0) / pts.length
    const y = pts.reduce((s, p) => s + p.y, 0) / pts.length
    const reach = pts.reduce((m, p) => Math.max(m, Math.hypot(p.x - x, p.y - y)), 0)
    out.push({ id: hub, x, y, radius: reach + 32, hull: convexHull(pts.map((p) => ({ x: p.x, y: p.y }))) })
  }
  return out.sort((a, b) => a.id.localeCompare(b.id))
}

/** The neighbors of a node among the visible edges, the node included. */
export function neighborhood(id: string | null, edges: readonly GraphEdge[]): Set<string> {
  const out = new Set<string>()
  if (!id) return out
  out.add(id)
  for (const e of edges) {
    if (e.from === id) out.add(e.to)
    else if (e.to === id) out.add(e.from)
  }
  return out
}

