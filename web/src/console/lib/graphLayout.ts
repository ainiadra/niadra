/**
 * The layout of the relationship graph: a small force simulation, written here so the Console adds no
 * dependency for one screen. It is deterministic: the same nodes and edges, in the same order, with the same
 * options, land in the same places, because the initial positions come from a seeded generator and the
 * simulation runs a fixed number of steps. Nodes added later (an expansion) start at the node they came from
 * and settle out from it, which is the one motion of the screen.
 *
 * Forces, per step: every pair of nodes repels (Coulomb-like, capped at close range), every edge pulls its
 * ends to a rest length (a spring, stronger for the ties that make a cluster: a person and their company, an
 * identifier and its profile; weaker for the lines of an agent or a system, which touch everyone), every node
 * is pulled gently to its anchor (the origin, or the corner where customers of no company gather), and two
 * nodes whose marks and labels would overlap are pushed apart. Velocities decay each step. The nodes of a
 * ring (agents and systems on the map of the space) sit on a frame just outside the box of the rest, at the
 * angle of their neighbors, so their many lines frame the drawing instead of knotting it.
 */
import type { GraphEdgeKind } from '../api/graph'

interface LayoutNode {
  id: string
  x: number
  y: number
  vx: number
  vy: number
  /** Pinned by a drag: the simulation leaves it where the person put it. */
  fixed: boolean
}

export interface LayoutEdge {
  from: string
  to: string
  kind?: GraphEdgeKind
}

export interface Layout {
  nodes: Map<string, LayoutNode>
  /** How many steps are still owed before the layout is at rest. */
  stepsLeft: number
}

/** The size of a node with its label, in layout units at 1:1, for the collision between neighbors. */
export interface NodeBox {
  width: number
  height: number
}

export interface LayoutOptions {
  /** The canvas width over its height: a wide canvas gets a wide drawing. */
  aspect?: number
  /** The nodes the simulation moves and weighs; the others (hidden by a filter) stay where they are and pull nothing. */
  only?: ReadonlySet<string>
  /** Nodes kept on a ring around the rest (agents and systems on the map of the space). */
  ring?: ReadonlySet<string>
  /** Nodes with no tie to anything drawn: they gather loosely to one side instead of piling on the middle. */
  loose?: ReadonlySet<string>
  /** The box of each node with its label, so neighbors never sit on each other's label. */
  boxes?: ReadonlyMap<string, NodeBox>
}

/** The steps a fresh layout runs; an expansion runs fewer, starting warm. */
const FULL_STEPS = 240
export const EXPAND_STEPS = 160

const REPULSION = 5200
const SPRING = 0.04
const REST_LENGTH = 125
const GRAVITY = 0.008
const LOOSE_GRAVITY = 0.02
const DAMPING = 0.82
const MIN_DISTANCE = 18
const MAX_SPEED = 28
const BOX_GAP = 8
const SEPARATION_ROUNDS = 3
const COLLIDE = 0.6
/** How far outside the drawing's box the ring of agents and systems sits. */
const RING_MARGIN_X = 90
const RING_MARGIN_Y = 70

/** How much each kind of tie pulls, and how long it rests: the ties of a cluster short and firm, a source's loose. */
const TIE: Partial<Record<GraphEdgeKind, { strength: number; rest: number }>> = {
  member_of: { strength: 1.5, rest: 90 },
  partner_of: { strength: 1.2, rest: 100 },
  parent_of: { strength: 1.2, rest: 110 },
  identifies: { strength: 1.4, rest: 85 },
  owns: { strength: 1.1, rest: 100 },
  relates_to: { strength: 0.8, rest: 110 },
  suggested_same: { strength: 0.6, rest: 130 },
  talked_with: { strength: 0.25, rest: 160 },
  acted_on: { strength: 0.25, rest: 160 },
  reported: { strength: 0.25, rest: 160 },
  handed_off: { strength: 0.5, rest: 140 },
}

/** mulberry32: a small seeded generator, enough to spread the first positions reproducibly. */
export function seeded(seed: number): () => number {
  let a = seed >>> 0
  return () => {
    a = (a + 0x6d2b79f5) >>> 0
    let t = a
    t = Math.imul(t ^ (t >>> 15), t | 1)
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61)
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296
  }
}

/** A stable number from a string, so a node's first position depends on its id, not on its index. */
export function hashSeed(s: string): number {
  let h = 2166136261
  for (let i = 0; i < s.length; i += 1) {
    h ^= s.charCodeAt(i)
    h = Math.imul(h, 16777619)
  }
  return h >>> 0
}

/** The starting layout: the center at the origin, every other node on a ring, in a reproducible order. */
export function initialLayout(ids: readonly string[], center: string | null): Layout {
  const nodes = new Map<string, LayoutNode>()
  const others = ids.filter((id) => id !== center)
  const radius = Math.max(120, 14 * others.length)
  others.forEach((id, i) => {
    const r = seeded(hashSeed(id))
    const angle = (i / Math.max(1, others.length)) * Math.PI * 2 + r() * 0.3
    const d = radius * (0.6 + 0.4 * r())
    nodes.set(id, { id, x: Math.cos(angle) * d, y: Math.sin(angle) * d, vx: 0, vy: 0, fixed: false })
  })
  if (center) nodes.set(center, { id: center, x: 0, y: 0, vx: 0, vy: 0, fixed: false })
  return { nodes, stepsLeft: FULL_STEPS }
}

/**
 * Adds the nodes an expansion brought, each starting at the node it was expanded from (with a small
 * reproducible offset so they do not sit on top of each other), and reopens the simulation.
 */
export function placeNew(layout: Layout, ids: readonly string[], fromId: string): Layout {
  const origin = layout.nodes.get(fromId)
  const nodes = new Map(layout.nodes)
  let added = 0
  for (const id of ids) {
    if (nodes.has(id)) continue
    const r = seeded(hashSeed(id))
    const angle = r() * Math.PI * 2
    const d = 10 + r() * 14
    nodes.set(id, {
      id,
      x: (origin?.x ?? 0) + Math.cos(angle) * d,
      y: (origin?.y ?? 0) + Math.sin(angle) * d,
      vx: 0,
      vy: 0,
      fixed: false,
    })
    added += 1
  }
  return { nodes, stepsLeft: added > 0 ? EXPAND_STEPS : layout.stepsLeft }
}

/** One step of the simulation, in place. Returns the same layout for chaining. */
export function step(layout: Layout, edges: readonly LayoutEdge[], opts: LayoutOptions = {}): Layout {
  const aspect = opts.aspect ?? 1
  const gx = GRAVITY / aspect
  const gy = GRAVITY
  const ring = opts.ring
  const only = opts.only
  const list = [...layout.nodes.values()].filter((n) => !only || only.has(n.id))
  const core = ring ? list.filter((n) => !ring.has(n.id)) : list
  const n = list.length
  for (let i = 0; i < n; i += 1) {
    const a = list[i]!
    for (let j = i + 1; j < n; j += 1) {
      const b = list[j]!
      let dx = b.x - a.x
      let dy = b.y - a.y
      if (dx * dx + dy * dy < 1e-6) {
        // Two nodes on the same spot: a reproducible nudge, from the ids.
        const r = seeded(hashSeed(a.id + b.id))
        dx = r() - 0.5
        dy = r() - 0.5
      }
      // Pair forces act in a space squeezed sideways by the aspect: at rest a disc there is an ellipse as wide
      // as the canvas here, so the drawing uses the width it has.
      const dxs = dx / aspect
      const d = Math.sqrt(dxs * dxs + dy * dy)
      const dd = Math.max(d, MIN_DISTANCE)
      const f = REPULSION / (dd * dd)
      const fx = (dxs / d) * f * aspect
      const fy = (dy / d) * f
      a.vx -= fx
      a.vy -= fy
      b.vx += fx
      b.vy += fy
      // Marks with their labels never overlap: the pair is pushed apart along the shorter way out.
      const ba = opts.boxes?.get(a.id)
      const bb = opts.boxes?.get(b.id)
      if (ba && bb) {
        const ox = (ba.width + bb.width) / 2 + BOX_GAP - Math.abs(dx)
        const oy = (ba.height + bb.height) / 2 + BOX_GAP - Math.abs(dy)
        if (ox > 0 && oy > 0) {
          if (ox < oy) {
            const s = Math.sign(dx || 1) * ox * COLLIDE
            a.vx -= s
            b.vx += s
          } else {
            const s = Math.sign(dy || 1) * oy * COLLIDE
            a.vy -= s
            b.vy += s
          }
        }
      }
    }
  }
  for (const e of edges) {
    if (only && (!only.has(e.from) || !only.has(e.to))) continue
    // A line to a node of the ring is drawn, not pulled: the ring is far, and a spring that long would drag
    // everyone it touches into a knot.
    if (ring && (ring.has(e.from) || ring.has(e.to))) continue
    const a = layout.nodes.get(e.from)
    const b = layout.nodes.get(e.to)
    if (!a || !b) continue
    const tie = (e.kind && TIE[e.kind]) || { strength: 1, rest: REST_LENGTH }
    const dxs = (b.x - a.x) / aspect
    const dy = b.y - a.y
    const d = Math.max(Math.sqrt(dxs * dxs + dy * dy), 1)
    const f = (d - tie.rest) * SPRING * tie.strength
    const fx = (dxs / d) * f * aspect
    const fy = (dy / d) * f
    a.vx += fx
    a.vy += fy
    b.vx -= fx
    b.vy -= fy
  }
  // The loose customers gather in a block to the left of the drawing, each to its own spot of a small grid,
  // so they read as a group of their own and never pile on one point.
  let reach = 120
  let leftmost = 0
  for (const c of core) {
    if (opts.loose?.has(c.id)) continue
    reach = Math.max(reach, Math.abs(c.x) / aspect, Math.abs(c.y))
    leftmost = Math.min(leftmost, c.x)
  }
  const looseIds = opts.loose ? [...opts.loose].filter((id) => layout.nodes.has(id) && (!only || only.has(id))) : []
  const cols = Math.max(1, Math.ceil(Math.sqrt(looseIds.length * 1.5)))
  const rows = Math.ceil(looseIds.length / cols)
  // The spots are as far apart as the widest and tallest box in the block, so nothing there is jostled.
  let colW = 95
  let rowH = 75
  for (const id of looseIds) {
    const b = opts.boxes?.get(id)
    if (!b) continue
    colW = Math.max(colW, b.width + BOX_GAP)
    rowH = Math.max(rowH, b.height + BOX_GAP)
  }
  // Just left of the leftmost tied node, so no empty band opens between the block and the clusters.
  const looseX = leftmost - (cols * colW) / 2 - 70
  const spot = new Map(looseIds.map((id, i) => [id, { x: looseX + ((i % cols) - (cols - 1) / 2) * colW, y: (Math.floor(i / cols) - (rows - 1) / 2) * rowH }]))
  for (const a of list) {
    const home = spot.get(a.id)
    if (home) {
      a.vx -= (a.x - home.x) * LOOSE_GRAVITY
      a.vy -= (a.y - home.y) * LOOSE_GRAVITY
    } else {
      a.vx -= a.x * gx
      a.vy -= a.y * gy
    }
    a.vx *= DAMPING
    a.vy *= DAMPING
    const speed = Math.sqrt(a.vx * a.vx + a.vy * a.vy)
    if (speed > MAX_SPEED) {
      a.vx = (a.vx / speed) * MAX_SPEED
      a.vy = (a.vy / speed) * MAX_SPEED
    }
    if (a.fixed) {
      a.vx = 0
      a.vy = 0
      continue
    }
    a.x += a.vx
    a.y += a.vy
  }
  // After the move, any two boxes still overlapping are set apart by half the overlap each, the shorter way:
  // a pile of repulsion can balance a push, but not a move. A few rounds, because a pair set apart may land on
  // a third.
  for (let round = 0; opts.boxes && round < SEPARATION_ROUNDS; round += 1) {
    for (let i = 0; i < n; i += 1) {
      const a = list[i]!
      const ba = opts.boxes.get(a.id)
      if (!ba) continue
      for (let j = i + 1; j < n; j += 1) {
        const b = list[j]!
        const bb = opts.boxes.get(b.id)
        if (!bb) continue
        const dx = b.x - a.x
        const dy = b.y - a.y
        const ox = (ba.width + bb.width) / 2 + BOX_GAP - Math.abs(dx)
        const oy = (ba.height + bb.height) / 2 + BOX_GAP - Math.abs(dy)
        if (ox <= 0 || oy <= 0) continue
        const share = a.fixed || b.fixed ? 1 : 0.5
        if (ox < oy) {
          const s = Math.sign(dx || 1) * ox * share
          if (!a.fixed) a.x -= s
          if (!b.fixed) b.x += s
        } else {
          const s = Math.sign(dy || 1) * oy * share
          if (!a.fixed) a.y -= s
          if (!b.fixed) b.y += s
        }
      }
    }
  }
  if (ring && ring.size > 0) {
    // The ring hugs the drawing as it stands after this step's moves: its box, with a margin on every side,
    // whatever the canvas shape. Each ring node sits on that frame at the angle of the mean of its neighbors,
    // so a system whose customers sit in the middle lands just outside them, never across the canvas.
    let minX = Infinity
    let maxX = -Infinity
    let minY = Infinity
    let maxY = -Infinity
    for (const c of core) {
      minX = Math.min(minX, c.x)
      maxX = Math.max(maxX, c.x)
      minY = Math.min(minY, c.y)
      maxY = Math.max(maxY, c.y)
    }
    if (minX === Infinity) minX = maxX = minY = maxY = 0
    const cx = (minX + maxX) / 2
    const cy = (minY + maxY) / 2
    const hx = Math.max(120, (maxX - minX) / 2) + RING_MARGIN_X
    const hy = Math.max(120, (maxY - minY) / 2) + RING_MARGIN_Y
    /** The point of the frame in a direction: where the ray from the center leaves the inflated box. */
    const onFrame = (angle: number) => {
      const dx = Math.cos(angle)
      const dy = Math.sin(angle)
      const t = Math.min(hx / Math.max(Math.abs(dx), 1e-6), hy / Math.max(Math.abs(dy), 1e-6))
      return { x: cx + dx * t, y: cy + dy * t }
    }
    const sum = new Map<string, { x: number; y: number; n: number }>()
    for (const e of edges) {
      for (const [self, other] of [
        [e.from, e.to],
        [e.to, e.from],
      ] as const) {
        if (!ring.has(self) || ring.has(other) || (only && !only.has(other))) continue
        const o = layout.nodes.get(other)
        if (!o) continue
        const s = sum.get(self) ?? { x: 0, y: 0, n: 0 }
        s.x += o.x
        s.y += o.y
        s.n += 1
        sum.set(self, s)
      }
    }
    const placed: LayoutNode[] = []
    for (const a of list) {
      if (!ring.has(a.id) || a.fixed) continue
      const s = sum.get(a.id)
      const angle = s && s.n > 0 ? Math.atan2((s.y / s.n - cy) / hy, (s.x / s.n - cx) / hx) : Math.atan2((a.y - cy) / hy, (a.x - cx) / hx)
      const q = onFrame(angle)
      a.x = q.x
      a.y = q.y
      a.vx = 0
      a.vy = 0
      placed.push(a)
    }
    // Two ring nodes at the same angle would sit on each other: slide them apart along the ring, the seam
    // at the left included (the last one wraps around to the first).
    const angles = placed.map((p) => ({ p, a: Math.atan2((p.y - cy) / hy, (p.x - cx) / hx) })).sort((u, v) => u.a - v.a)
    const minAngle = Math.min(0.5, (Math.PI * 2) / Math.max(angles.length * 1.5, 1))
    for (let i = 1; i < angles.length; i += 1) if (angles[i]!.a - angles[i - 1]!.a < minAngle) angles[i]!.a = angles[i - 1]!.a + minAngle
    if (angles.length > 1 && angles[0]!.a + Math.PI * 2 - angles[angles.length - 1]!.a < minAngle) angles[angles.length - 1]!.a = angles[0]!.a + Math.PI * 2 - minAngle
    for (const { p, a } of angles) {
      const q = onFrame(a)
      p.x = q.x
      p.y = q.y
    }
  }
  layout.stepsLeft = Math.max(0, layout.stepsLeft - 1)
  return layout
}

/** Runs the layout to rest, synchronously: the tests, and the screen when motion is reduced. */
export function settle(layout: Layout, edges: readonly LayoutEdge[], opts: LayoutOptions = {}): Layout {
  while (layout.stepsLeft > 0) step(layout, edges, opts)
  return layout
}

export interface Box {
  x: number
  y: number
  width: number
  height: number
}

/** The reach of a node beyond its position, in layout units: its mark and its label. */
export interface Extent {
  left: number
  right: number
  top: number
  bottom: number
}

/** The box around the given nodes, with a margin and, when given, each node's own extent; one node gets a box around itself. */
export function bounds(layout: Layout, ids?: ReadonlySet<string>, margin = 48, extent?: (id: string) => Extent): Box {
  let minX = Infinity
  let minY = Infinity
  let maxX = -Infinity
  let maxY = -Infinity
  for (const n of layout.nodes.values()) {
    if (ids && !ids.has(n.id)) continue
    const e = extent?.(n.id) ?? { left: 0, right: 0, top: 0, bottom: 0 }
    if (n.x - e.left < minX) minX = n.x - e.left
    if (n.y - e.top < minY) minY = n.y - e.top
    if (n.x + e.right > maxX) maxX = n.x + e.right
    if (n.y + e.bottom > maxY) maxY = n.y + e.bottom
  }
  if (minX === Infinity) return { x: -margin, y: -margin, width: margin * 2, height: margin * 2 }
  return { x: minX - margin, y: minY - margin, width: maxX - minX + margin * 2, height: maxY - minY + margin * 2 }
}

export interface View {
  /** The translation applied after the scale, in screen pixels. */
  tx: number
  ty: number
  scale: number
}

/**
 * The view that shows the whole box inside a canvas of the given size, within the zoom bounds. The marks keep
 * their screen size past 1.3x (GraphCanvas), so a small graph may fill the canvas without giant shapes.
 */
export function fitView(box: Box, width: number, height: number): View {
  const scale = Math.min(4, Math.max(0.05, Math.min(width / Math.max(box.width, 1), height / Math.max(box.height, 1))))
  return { scale, tx: width / 2 - (box.x + box.width / 2) * scale, ty: height / 2 - (box.y + box.height / 2) * scale }
}

/** Zooms around a screen point (the pointer, or the middle for the buttons), bounded. */
export function zoomAt(view: View, factor: number, px: number, py: number): View {
  const scale = Math.min(4, Math.max(0.05, view.scale * factor))
  const k = scale / view.scale
  return { scale, tx: px - (px - view.tx) * k, ty: py - (py - view.ty) * k }
}

/** The nearest node in a direction (for the arrow keys): the smallest distance among those ahead. */
export function nearestInDirection(layout: Layout, fromId: string, dir: 'up' | 'down' | 'left' | 'right', ids?: ReadonlySet<string>): string | null {
  const from = layout.nodes.get(fromId)
  if (!from) return null
  let best: string | null = null
  let bestScore = Infinity
  for (const n of layout.nodes.values()) {
    if (n.id === fromId || (ids && !ids.has(n.id))) continue
    const dx = n.x - from.x
    const dy = n.y - from.y
    const ahead = dir === 'right' ? dx : dir === 'left' ? -dx : dir === 'down' ? dy : -dy
    if (ahead <= 0) continue
    const side = dir === 'right' || dir === 'left' ? Math.abs(dy) : Math.abs(dx)
    // Favor what is straight ahead: sideways distance weighs double.
    const score = ahead + side * 2
    if (score < bestScore) {
      bestScore = score
      best = n.id
    }
  }
  return best
}
