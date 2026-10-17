import { describe, expect, it } from 'vitest'
import { fromResponse, visibleEdges, visibleIds } from '../lib/graph'
import { bounds, EXPAND_STEPS, fitView, initialLayout, nearestInDirection, placeNew, settle, zoomAt, type Layout, type LayoutEdge, type LayoutOptions } from '../lib/graphLayout'
import { boxGrowth, labelBoxOn, labelPlan, labelWidth, markScale, nodeBox, screenBoxes, textScale, weight, NODE_RADIUS } from '../lib/graphView'
import { demoLike } from './graphDemo'

/** A synthetic graph of `n` nodes: a hub with spokes, plus chains hanging off the spokes. */
function synthetic(n: number): { ids: string[]; edges: LayoutEdge[] } {
  const ids = Array.from({ length: n }, (_, i) => `n${i}`)
  const edges: LayoutEdge[] = []
  for (let i = 1; i < n; i += 1) edges.push({ from: `n${i}`, to: i < 20 ? 'n0' : `n${i % 19 + 1}` })
  return { ids, edges }
}

describe('graph layout', () => {
  it('lays the same graph out the same way every time', () => {
    const { ids, edges } = synthetic(40)
    const a = settle(initialLayout(ids, 'n0'), edges)
    const b = settle(initialLayout(ids, 'n0'), edges)
    for (const id of ids) {
      expect(a.nodes.get(id)!.x).toBe(b.nodes.get(id)!.x)
      expect(a.nodes.get(id)!.y).toBe(b.nodes.get(id)!.y)
    }
    expect(a.stepsLeft).toBe(0)
  })

  it('keeps the center at the middle and spreads three hundred nodes apart, within the step budget', () => {
    const { ids, edges } = synthetic(300)
    const started = performance.now()
    const layout = settle(initialLayout(ids, 'n0'), edges)
    const elapsed = performance.now() - started
    const center = layout.nodes.get('n0')!
    expect(Math.hypot(center.x, center.y)).toBeLessThan(60)
    let closest = Infinity
    const list = [...layout.nodes.values()]
    for (const n of list) {
      expect(Number.isFinite(n.x) && Number.isFinite(n.y)).toBe(true)
      for (const m of list) {
        if (m === n) continue
        closest = Math.min(closest, Math.hypot(n.x - m.x, n.y - m.y))
      }
    }
    // No two nodes on top of each other: a reader can tell them apart.
    expect(closest).toBeGreaterThan(8)
    // Two frames of a slow phone at most for the whole settle; the browser spreads it over frames anyway.
    expect(elapsed).toBeLessThan(3000)
  })

  it('places expanded nodes at the node they came from, and leaves the rest where it was', () => {
    const { ids, edges } = synthetic(12)
    const layout = settle(initialLayout(ids, 'n0'), edges)
    const before = new Map([...layout.nodes].map(([id, n]) => [id, { x: n.x, y: n.y }]))
    const next = placeNew(layout, ['x1', 'x2', 'n3'], 'n5')
    const from = layout.nodes.get('n5')!
    for (const id of ['x1', 'x2']) {
      const n = next.nodes.get(id)!
      expect(Math.hypot(n.x - from.x, n.y - from.y)).toBeLessThan(30)
    }
    expect(next.nodes.size).toBe(14)
    for (const [id, p] of before) {
      expect(next.nodes.get(id)!.x).toBe(p.x)
      expect(next.nodes.get(id)!.y).toBe(p.y)
    }
    expect(next.stepsLeft).toBeGreaterThan(0)
  })

  it('finds the nearest node in a direction, for the arrow keys', () => {
    const layout = initialLayout(['c', 'r', 'l', 'u'], 'c')
    layout.nodes.get('r')!.x = 100
    layout.nodes.get('r')!.y = 0
    layout.nodes.get('l')!.x = -100
    layout.nodes.get('l')!.y = -10
    layout.nodes.get('u')!.x = 5
    layout.nodes.get('u')!.y = -80
    expect(nearestInDirection(layout, 'c', 'right')).toBe('r')
    expect(nearestInDirection(layout, 'c', 'left')).toBe('l')
    expect(nearestInDirection(layout, 'c', 'up')).toBe('u')
    expect(nearestInDirection(layout, 'c', 'down')).toBeNull()
    // Hidden nodes are skipped.
    expect(nearestInDirection(layout, 'c', 'right', new Set(['c', 'l']))).toBeNull()
  })

  it('fits the whole graph in the canvas and zooms within bounds', () => {
    const { ids, edges } = synthetic(30)
    const layout = settle(initialLayout(ids, 'n0'), edges)
    const box = bounds(layout)
    const view = fitView(box, 800, 600)
    expect(view.scale).toBeGreaterThan(0.2)
    expect(view.scale).toBeLessThanOrEqual(1.25)
    // Every node lands inside the canvas after the fit.
    for (const n of layout.nodes.values()) {
      const sx = n.x * view.scale + view.tx
      const sy = n.y * view.scale + view.ty
      expect(sx).toBeGreaterThanOrEqual(0)
      expect(sx).toBeLessThanOrEqual(800)
      expect(sy).toBeGreaterThanOrEqual(0)
      expect(sy).toBeLessThanOrEqual(600)
    }
    let z = view
    for (let i = 0; i < 20; i += 1) z = zoomAt(z, 2, 400, 300)
    expect(z.scale).toBe(4)
    for (let i = 0; i < 20; i += 1) z = zoomAt(z, 0.5, 400, 300)
    expect(z.scale).toBe(0.05)
  })
})

/** The explorer's inputs to the simulation for a graph with the given kinds hidden, as GraphExplorer builds them. */
function demoSetup(hidden: Set<'agent' | 'system'>, aspect: number) {
  const graph = fromResponse(demoLike())
  const visible = visibleIds(graph, hidden)
  const edges = visibleEdges(graph, visible)
  const degrees = new Map<string, number>()
  for (const e of edges) {
    degrees.set(e.from, (degrees.get(e.from) ?? 0) + 1)
    degrees.set(e.to, (degrees.get(e.to) ?? 0) + 1)
  }
  const boxes = new Map<string, { width: number; height: number }>()
  const ring = new Set<string>()
  const loose = new Set<string>()
  for (const n of graph.nodes.values()) {
    if (!visible.has(n.id)) continue
    boxes.set(n.id, nodeBox(n, weight(n, degrees.get(n.id) ?? 0, false)))
    if (n.kind === 'agent' || n.kind === 'system') ring.add(n.id)
    else if (!degrees.has(n.id)) loose.add(n.id)
  }
  const opts: LayoutOptions = { aspect, only: visible, boxes, ring, loose }
  const simEdges: LayoutEdge[] = edges.map((e) => ({ from: e.from, to: e.to, kind: e.kind }))
  return { graph, visible, edges, degrees, opts, simEdges, ring, loose }
}

/** The explorer's settle: once, and again with the boxes grown when the first fit lands far out. */
function settleLike(s: ReturnType<typeof demoSetup>, width: number, height: number): Layout {
  const layout = settle(initialLayout([...s.graph.nodes.keys()], null), s.simEdges, s.opts)
  const growth = boxGrowth(fitView(bounds(layout, s.visible, 0), width, height).scale)
  if (growth > 1.05) {
    const boxes = new Map([...s.opts.boxes!].map(([id, b]) => [id, { width: b.width * growth, height: b.height * growth }]))
    layout.stepsLeft = EXPAND_STEPS
    settle(layout, s.simEdges, { ...s.opts, boxes })
  }
  return layout
}

/** The explorer's fit: a first pass for the zoom, then the marks and labels reserved at that zoom. */
function fitLike(layout: Layout, s: ReturnType<typeof demoSetup>, width: number, height: number) {
  let view = fitView(bounds(layout, s.visible, 0), width, height)
  for (let pass = 0; pass < 2; pass += 1) {
    const at = view.scale
    const ks = markScale(at) * at
    const ts = textScale(at) * at
    const extent = (id: string) => {
      const n = s.graph.nodes.get(id)!
      const r = ((NODE_RADIUS[n.kind] + 6) * ks) / at
      const lw = (labelWidth(n) * ts) / at
      const lh = (20 * ts) / at
      return { left: Math.max(r, lw / 2), right: r + lw, top: r + lh, bottom: r + lh }
    }
    view = fitView(bounds(layout, s.visible, 16 / at, extent), width, height)
  }
  return view
}

describe('the map at the scale of a real space', () => {
  const HIDDEN = new Set<'agent' | 'system'>(['agent', 'system'])

  it('lays eighty nodes out the same way twice, with the people around their company', () => {
    const s = demoSetup(HIDDEN, 2)
    const a = settle(initialLayout([...s.graph.nodes.keys()], null), s.simEdges, s.opts)
    const b = settle(initialLayout([...s.graph.nodes.keys()], null), s.simEdges, s.opts)
    for (const id of s.visible) {
      expect(a.nodes.get(id)!.x).toBe(b.nodes.get(id)!.x)
      expect(a.nodes.get(id)!.y).toBe(b.nodes.get(id)!.y)
    }
    // A member sits nearer to its own company than to the average company.
    const companies = [...s.graph.nodes.values()].filter((n) => n.kind === 'account')
    let nearer = 0
    let members = 0
    for (const e of s.edges) {
      if (e.kind !== 'member_of') continue
      members += 1
      const p = a.nodes.get(e.from)!
      const own = a.nodes.get(e.to)!
      const dOwn = Math.hypot(p.x - own.x, p.y - own.y)
      const dAll = companies.reduce((acc, c) => acc + Math.hypot(p.x - a.nodes.get(c.id)!.x, p.y - a.nodes.get(c.id)!.y), 0) / companies.length
      if (dOwn < dAll) nearer += 1
    }
    expect(nearer / members).toBeGreaterThan(0.9)
    // The customers of no company gather to one side, away from the middle.
    for (const id of s.loose) expect(a.nodes.get(id)!.x).toBeLessThan(0)
  })

  it('writes no label on another label or on a mark, at 1440 and at 390, and the fit holds every mark and label', () => {
    for (const [width, height, aspect] of [
      [1104, 412, 2.2],
      [358, 420, 1],
    ] as const) {
      const s = demoSetup(HIDDEN, aspect)
      const layout = settleLike(s, width, height)
      const view = fitLike(layout, s, width, height)
      const plan = labelPlan({ nodes: s.graph.nodes.values(), layout, visible: s.visible, view, degrees: s.degrees, center: null, activeId: null, hoverId: null, neighbors: new Set(), matches: new Set() })
      // On a desktop the companies are named (all but at most two, where the greedy plan finds no free side)
      // and the most connected people: they carry the structure. On a phone the canvas is a quarter of that
      // and the list is the default; what is written there still never overlaps.
      expect(plan.shown.size).toBeGreaterThan(width > 600 ? 20 : -1)
      const companies = [...s.graph.nodes.values()].filter((n) => n.kind === 'account')
      if (width > 600) expect(companies.filter((n) => plan.shown.has(n.id)).length).toBeGreaterThanOrEqual(companies.length - 2)
      const boxes = [...s.graph.nodes.values()]
        .filter((n) => s.visible.has(n.id))
        .map((n) => {
          const p = layout.nodes.get(n.id)!
          return { n, ...screenBoxes(n, weight(n, s.degrees.get(n.id) ?? 0, false), p.x, p.y, view) }
        })
      const overlaps = (a: { x: number; y: number; w: number; h: number }, b: { x: number; y: number; w: number; h: number }) => a.x < b.x + b.w && a.x + a.w > b.x && a.y < b.y + b.h && a.y + a.h > b.y
      const named = boxes.filter((b) => plan.shown.has(b.n.id)).map((b) => ({ ...b, written: labelBoxOn(b, plan.shown.get(b.n.id)!) }))
      for (let i = 0; i < named.length; i += 1) {
        for (let j = i + 1; j < named.length; j += 1) expect(overlaps(named[i]!.written, named[j]!.written), `${width}: ${named[i]!.n.label} over ${named[j]!.n.label}`).toBe(false)
        for (const b of boxes) if (b.n.id !== named[i]!.n.id) expect(overlaps(named[i]!.written, b.mark), `${width}: ${named[i]!.n.label} over the mark of ${b.n.label}`).toBe(false)
      }
      // The fit: every mark and every written label inside the canvas.
      for (const b of boxes) {
        expect(b.mark.x, `${width}: ${b.n.label}`).toBeGreaterThanOrEqual(0)
        expect(b.mark.y).toBeGreaterThanOrEqual(0)
        expect(b.mark.x + b.mark.w).toBeLessThanOrEqual(width)
        expect(b.mark.y + b.mark.h).toBeLessThanOrEqual(height)
        const side = plan.shown.get(b.n.id)
        if (side) {
          const l = labelBoxOn(b, side)
          expect(l.x).toBeGreaterThanOrEqual(0)
          expect(l.y).toBeGreaterThanOrEqual(0)
          expect(l.x + l.w).toBeLessThanOrEqual(width)
          expect(l.y + l.h).toBeLessThanOrEqual(height)
        }
      }
    }
  })

  it('keeps the agents and systems on a frame just outside everyone else, fitted to the drawing, when they are shown', () => {
    const s = demoSetup(new Set(), 2)
    const layout = settle(initialLayout([...s.graph.nodes.keys()], null), s.simEdges, s.opts)
    // The drawing's own box: the ring follows it, centered on it, not the canvas shape nor the origin.
    const core = [...s.visible].filter((id) => !s.ring.has(id)).map((id) => layout.nodes.get(id)!)
    const minX = Math.min(...core.map((p) => p.x))
    const maxX = Math.max(...core.map((p) => p.x))
    const minY = Math.min(...core.map((p) => p.y))
    const maxY = Math.max(...core.map((p) => p.y))
    const cx = (minX + maxX) / 2
    const cy = (minY + maxY) / 2
    const bx = (maxX - minX) / 2
    const by = (maxY - minY) / 2
    for (const id of s.ring) {
      const p = layout.nodes.get(id)!
      // On the frame: outside the box on at least one axis, and never farther than the margin on either.
      expect(Math.abs(p.x - cx) > bx || Math.abs(p.y - cy) > by, id).toBe(true)
      expect(Math.abs(p.x - cx)).toBeLessThanOrEqual(bx + 91)
      expect(Math.abs(p.y - cy)).toBeLessThanOrEqual(by + 71)
    }
    // Hidden nodes stay put: a kind turned off pulls nothing and goes nowhere.
    const hidden = demoSetup(HIDDEN, 2)
    const before = initialLayout([...hidden.graph.nodes.keys()], null)
    const start = new Map([...before.nodes].map(([id, n]) => [id, { x: n.x, y: n.y }]))
    settle(before, hidden.simEdges, hidden.opts)
    for (const id of hidden.ring) {
      expect(before.nodes.get(id)!.x).toBe(start.get(id)!.x)
      expect(before.nodes.get(id)!.y).toBe(start.get(id)!.y)
    }
  })
})
