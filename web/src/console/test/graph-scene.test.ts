import { describe, expect, it } from 'vitest'
import type { GraphEdge } from '../api/graph'
import { fromResponse, visibleEdges, visibleIds } from '../lib/graph'
import { initialLayout, settle, type Layout } from '../lib/graphLayout'
import { breath, controlPoint, convexHull, curvePoint, distanceToCurve, edgeAt, glideDone, glideView, neighborLight, neighborhood, nodeAt, pulses, regions, ripple, TIMING, viewOnNode } from '../lib/graphScene'
import { placeEdgeLabels } from '../lib/graphView'
import { demoLike } from './graphDemo'

/** The demo-like space, settled, with the sources hidden as the map opens. */
function scene() {
  const graph = fromResponse(demoLike())
  const visible = visibleIds(graph, new Set(['agent', 'system']))
  const edges = visibleEdges(graph, visible)
  const layout = settle(initialLayout([...graph.nodes.keys()], null), edges.map((e) => ({ from: e.from, to: e.to, kind: e.kind })), { only: visible, aspect: 2 })
  return { graph, visible, edges, layout }
}

describe('the curves of the lines', () => {
  it('bow each line to its own side, the same way every time, and start and end on the nodes', () => {
    const a = { x: 0, y: 0 }
    const b = { x: 200, y: 0 }
    const c1 = controlPoint(a, b, 'e1')
    const c2 = controlPoint(a, b, 'e1')
    expect(c1).toEqual(c2)
    expect(Math.abs(c1.y)).toBeGreaterThan(5)
    expect(curvePoint(a, c1, b, 0)).toEqual(a)
    expect(curvePoint(a, c1, b, 1)).toEqual(b)
    // A short tie stays nearly straight; a long line arcs, up to the cap.
    expect(Math.abs(controlPoint(a, { x: 30, y: 0 }, 'e1').y)).toBeLessThan(12)
    expect(Math.abs(controlPoint(a, { x: 2000, y: 0 }, 'e1').y)).toBe(40)
    // Over many ids the sides balance, so a hub's lines fan out instead of all bowing one way.
    const sides = Array.from({ length: 200 }, (_, i) => Math.sign(controlPoint(a, b, `edge-${i}`).y))
    const up = sides.filter((s) => s > 0).length
    expect(up).toBeGreaterThan(60)
    expect(up).toBeLessThan(140)
  })

  it('finds the line under the pointer, and the node before the line', () => {
    const { graph, visible, edges, layout } = scene()
    const view = { tx: 0, ty: 0, scale: 1 }
    const e = edges.find((x) => x.kind === 'member_of')!
    const a = layout.nodes.get(e.from)!
    const b = layout.nodes.get(e.to)!
    const mid = curvePoint(a, controlPoint(a, b, e.id), b, 0.5)
    expect(distanceToCurve(mid, a, controlPoint(a, b, e.id), b)).toBeLessThan(2)
    // The nearest node to the middle of the line is not within a mark's reach, so the line answers.
    const near = nodeAt(layout, view, visible, mid.x, mid.y, () => 10)
    if (near === null) expect(edgeAt(edges, layout, view, mid.x, mid.y)).not.toBeNull()
    // On a node, the node answers.
    expect(nodeAt(layout, view, visible, a.x + 3, a.y - 2, () => 20)).toBe(e.from)
    // Hidden nodes are never hit.
    expect(nodeAt(layout, view, new Set(), a.x, a.y, () => 20)).toBeNull()
    expect(graph.nodes.has(e.from)).toBe(true)
  })
})

describe('the life of the drawing', () => {
  const edges: GraphEdge[] = [
    { id: 'e1', from: 'a', to: 'b', kind: 'member_of', meta: {} },
    { id: 'e2', from: 'c', to: 'a', kind: 'talked_with', meta: { count: 3 } },
    { id: 'e3', from: 'a', to: 'd', kind: 'owns', meta: {} },
  ]
  const layout: Layout = {
    nodes: new Map([
      ['a', { id: 'a', x: 0, y: 0, vx: 0, vy: 0, fixed: false }],
      ['b', { id: 'b', x: 100, y: 0, vx: 0, vy: 0, fixed: false }],
      ['c', { id: 'c', x: 0, y: 300, vx: 0, vy: 0, fixed: false }],
      ['d', { id: 'd', x: -50, y: 0, vx: 0, vy: 0, fixed: false }],
    ]),
    stepsLeft: 0,
  }

  it('breathes each node to its own phase, and not at all when motion is reduced', () => {
    const a = breath('a', 1000, false)
    const b = breath('b', 1000, false)
    expect(a).not.toEqual(b)
    expect(Math.abs(a.size - 1)).toBeLessThanOrEqual(0.035)
    expect(Math.abs(a.dx)).toBeLessThanOrEqual(1.4)
    expect(breath('a', 1000, false)).toEqual(a)
    expect(breath('a', 1000, true)).toEqual({ size: 1, glow: 1, dx: 0, dy: 0 })
    // One breath later it is back where it was.
    expect(breath('a', 1000 + TIMING.breath, false).size).toBeCloseTo(a.size, 6)
  })

  it('sends a pulse along each line of the activated node, longest line first, and then keeps a faint one circulating', () => {
    const act = { id: 'a', at: 10_000 }
    // Nothing has left yet at the instant of activation but the first pulse.
    const start = pulses(act, edges, layout, 10_000, false)
    expect(start.map((p) => p.edge.id)).toEqual(['e2'])
    expect(start[0]!.t).toBe(0)
    // Halfway through the burst all three are in flight, the longest furthest along.
    const mid = pulses(act, edges, layout, 10_000 + TIMING.pulse / 2, false)
    expect(mid.map((p) => p.edge.id)).toEqual(['e2', 'e1', 'e3'])
    expect(mid[0]!.t).toBeGreaterThan(mid[1]!.t)
    expect(mid[1]!.t).toBeGreaterThan(mid[2]!.t)
    expect(mid.every((p) => p.alpha === 1 && p.t > 0 && p.t < 1)).toBe(true)
    // After the burst, every line keeps a faint pulse, each at its own phase.
    const later = pulses(act, edges, layout, 10_000 + TIMING.pulse + 2 * TIMING.stagger + 900, false)
    expect(later).toHaveLength(3)
    expect(later.every((p) => p.alpha < 1 && p.t >= 0 && p.t < 1)).toBe(true)
    expect(new Set(later.map((p) => p.t.toFixed(3))).size).toBe(3)
    // Reduced motion: no pulses, ever.
    expect(pulses(act, edges, layout, 10_000 + 300, true)).toEqual([])
  })

  it('lights each neighbor when its pulse arrives, with a flash that settles, and at once when motion is reduced', () => {
    const act = { id: 'a', at: 0 }
    const before = neighborLight(act, edges, layout, TIMING.pulse - 1, false)
    expect(before.get('c')).toBe(0)
    expect(before.get('b')).toBe(0)
    const arrived = neighborLight(act, edges, layout, TIMING.pulse + 1, false)
    expect(arrived.get('c')).toBeGreaterThan(1.8)
    // The second line leaves a stagger later, so its neighbor is still dark.
    expect(arrived.get('b')).toBe(0)
    const settled = neighborLight(act, edges, layout, TIMING.pulse + 2 * TIMING.stagger + TIMING.flash + 1, false)
    expect(settled.get('c')).toBe(1)
    expect(settled.get('b')).toBe(1)
    expect(settled.get('d')).toBe(1)
    const reduced = neighborLight(act, edges, layout, 0, true)
    expect([...reduced.values()]).toEqual([1, 1, 1])
  })

  it('ripples once from the activated node and is gone after, never when motion is reduced', () => {
    const act = { id: 'a', at: 0 }
    const early = ripple(act, 50, false)!
    const late = ripple(act, TIMING.ripple - 50, false)!
    expect(early.grow).toBeLessThan(late.grow)
    expect(early.alpha).toBeGreaterThan(late.alpha)
    expect(ripple(act, TIMING.ripple + 1, false)).toBeNull()
    expect(ripple(act, 50, true)).toBeNull()
  })

  it('glides the camera to a node, settling exactly on it, and keeps the node clear of the card', () => {
    const from = { tx: 0, ty: 0, scale: 0.5 }
    const to = viewOnNode(from, 200, 100, 1000, 500, true)
    // Far out it zooms in a little, never out; the node lands left of the middle, where the card does not cover it.
    expect(to.scale).toBe(0.85)
    expect(200 * to.scale + to.tx).toBeCloseTo(400)
    expect(100 * to.scale + to.ty).toBeCloseTo(250)
    // A phone zooms in less, and keeps the node in the middle, since the card sits below.
    const phone = viewOnNode(from, 200, 100, 358, 420, false)
    expect(phone.scale).toBe(0.7)
    expect(200 * phone.scale + phone.tx).toBeCloseTo(179)
    expect(100 * phone.scale + phone.ty).toBeCloseTo(84)
    const zoomedIn = viewOnNode({ ...from, scale: 1.2 }, 200, 100, 1000, 500, true)
    expect(zoomedIn.scale).toBe(1.2)
    expect(200 * zoomedIn.scale + zoomedIn.tx).toBeCloseTo(400)
    const g = { from, to, at: 0 }
    expect(glideView(g, 0)).toEqual(from)
    const half = glideView(g, TIMING.glide / 2)
    expect(half.scale).toBeGreaterThan(from.scale)
    expect(half.scale).toBeLessThan(to.scale)
    expect(glideView(g, TIMING.glide)).toEqual(to)
    expect(glideDone(g, TIMING.glide - 1)).toBe(false)
    expect(glideDone(g, TIMING.glide)).toBe(true)
  })

  it('names the neighborhood of a node, itself included', () => {
    expect([...neighborhood('a', edges)].sort()).toEqual(['a', 'b', 'c', 'd'])
    expect([...neighborhood('b', edges)].sort()).toEqual(['a', 'b'])
    expect(neighborhood(null, edges).size).toBe(0)
  })
})

describe('the lobes and the words on the lines', () => {
  it('draws the hull of a group counterclockwise, with the inner points left out', () => {
    const hull = convexHull([
      { x: 0, y: 0 },
      { x: 10, y: 0 },
      { x: 10, y: 10 },
      { x: 0, y: 10 },
      { x: 5, y: 5 },
    ])
    expect(hull).toEqual([
      { x: 0, y: 0 },
      { x: 10, y: 0 },
      { x: 10, y: 10 },
      { x: 0, y: 10 },
    ])
    expect(convexHull([{ x: 1, y: 1 }])).toEqual([{ x: 1, y: 1 }])
  })

  it('draws a region behind each company with people around it, centered on the group, and none for a company alone', () => {
    const { graph, visible, layout } = scene()
    const lobes = regions(graph, layout, visible)
    const hubs = [...graph.nodes.values()].filter((n) => n.kind === 'account' || n.kind === 'partner')
    const tied = (kind: GraphEdge['kind']) => kind === 'member_of' || kind === 'partner_of' || kind === 'parent_of'
    const withMembers = hubs.filter((c) => [...graph.edges.values()].some((e) => tied(e.kind) && e.to === c.id))
    expect(lobes.map((r) => r.id).sort()).toEqual(withMembers.map((c) => c.id).sort())
    expect(withMembers.length).toBeLessThan(hubs.length)
    for (const r of lobes) {
      const hub = layout.nodes.get(r.id)!
      // The region reaches the company and every member of it, and its hull is a convex outline of them.
      expect(Math.hypot(hub.x - r.x, hub.y - r.y)).toBeLessThan(r.radius)
      expect(r.hull.length).toBeGreaterThanOrEqual(1)
      expect(r.hull.some((q) => q.x === hub.x && q.y === hub.y) || r.hull.length >= 3).toBe(true)
      for (const e of graph.edges.values()) {
        if (e.kind !== 'member_of' || e.to !== r.id || !visible.has(e.from)) continue
        const m = layout.nodes.get(e.from)!
        expect(Math.hypot(m.x - r.x, m.y - r.y)).toBeLessThanOrEqual(r.radius)
      }
    }
    // The same input, the same regions, in the same order.
    expect(regions(graph, layout, visible)).toEqual(lobes)
    // Hide the companies and the lobes go.
    expect(regions(graph, layout, visibleIds(graph, new Set(['account', 'partner', 'agent', 'system'])))).toEqual([])
  })

  it('writes the words of one node on its lines, none on top of another, and the whole sentence for the line under the pointer', () => {
    const { graph, edges, layout } = scene()
    const hub = [...graph.nodes.values()].find((n) => n.kind === 'account' && edges.filter((e) => e.to === n.id).length >= 3)!
    const view = { tx: 400, ty: 300, scale: 1 }
    const pos = (id: string) => layout.nodes.get(id)
    const words = placeEdgeLabels(edges, hub.id, false, pos, graph.nodes, view, 1, { width: 2000, height: 2000 }, null, [])
    expect(words.length).toBeGreaterThan(0)
    expect(words.every((w) => w.edge.from === hub.id || w.edge.to === hub.id)).toBe(true)
    expect(words.every((w) => w.text === 'faz parte de' || w.text === 'é matriz de')).toBe(true)
    const box = (w: (typeof words)[number]) => ({ x: w.x * view.scale + view.tx - (w.width * view.scale) / 2, y: w.y * view.scale + view.ty - 10, w: w.width * view.scale, h: 20 })
    for (let i = 0; i < words.length; i += 1)
      for (let j = i + 1; j < words.length; j += 1) {
        const a = box(words[i]!)
        const b = box(words[j]!)
        expect(a.x < b.x + b.w && a.x + a.w > b.x && a.y < b.y + b.h && a.y + a.h > b.y).toBe(false)
      }
    const hovered = edges.find((e) => e.to === hub.id)!
    const strong = placeEdgeLabels(edges, null, false, pos, graph.nodes, view, 1, { width: 2000, height: 2000 }, hovered.id, [])
    expect(strong).toHaveLength(1)
    expect(strong[0]!.strong).toBe(true)
    expect(strong[0]!.text).toMatch(/ (faz parte de|é matriz de) /)
  })
})
