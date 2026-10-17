import { forwardRef, useCallback, useEffect, useImperativeHandle, useRef, useState, type KeyboardEvent, type MutableRefObject, type PointerEvent as ReactPointerEvent } from 'react'
import type { GraphEdge, GraphNode } from '../../api/graph'
import { edgesOf, type Graph } from '../../lib/graph'
import { displayLabel, edgeDashed, nodeKind } from '../../lib/graphLabels'
import { bounds, fitView, nearestInDirection, settle, step, zoomAt, EXPAND_STEPS, type Layout, type LayoutEdge, type LayoutOptions, type View } from '../../lib/graphLayout'
import {
  breath,
  controlPoint,
  curvePoint,
  edgeAt,
  glideDone,
  glideView,
  neighborLight,
  neighborhood,
  nodeAt,
  pulses,
  regions,
  ripple,
  viewOnNode,
  type Activation,
  type Glide,
  type Point,
} from '../../lib/graphScene'
import { DIM, EDGE_ALPHA, GROUND, GROUND_GLOW, INK, INK_SOFT, KIND_STYLE, LIME, ON_LIME, PILL_BG, PILL_LINE, REGION_ALPHA } from '../../lib/graphTheme'
import { boxGrowth, labelPlan, labelWidth, markScale, monoLabel, NODE_RADIUS, placeEdgeLabels, textScale, weight } from '../../lib/graphView'

/** What the simulation reads at every step; the explorer rebuilds it when the visible graph changes. */
export interface Simulation {
  edges: LayoutEdge[]
  options: LayoutOptions
  /** The canvas width over its height, reported by the canvas once it has a size. */
  aspect: number
  /** Fit the drawing to the canvas once the layout rests (a new drawing, an expansion, a filter). */
  fitWhenSettled: boolean
  /** Once per drawing: when the first fit lands far out, the boxes grow to the labels' real size and the layout settles again. */
  grown: boolean
}

/** What the explorer asks of the camera, from its buttons. */
export interface GraphCamera {
  fit: () => void
  zoom: (factor: number) => void
}

export interface CanvasProps {
  graph: Graph
  layout: MutableRefObject<Layout>
  sim: MutableRefObject<Simulation>
  visible: ReadonlySet<string>
  edges: readonly GraphEdge[]
  /** The selected node: lit, its lines pulsing, its card open; the rest recedes. */
  activeId: string | null
  onActive: (id: string | null) => void
  onHover: (id: string | null) => void
  onExpand: (id: string) => void
  /** Nodes the search matched: drawn with a dashed lime ring. */
  matches: ReadonlySet<string>
  label: string
  reduced: boolean
}

const DRAG_THRESHOLD = 4
const HOVER_FADE_MS = 150
/** A map with this many lines or fewer carries the word of every line at rest. */
const EVERY_LINE_UP_TO = 24
/** The simulation takes at most this long of a frame, so a large graph settles over more frames instead of stalling them. */
const STEP_BUDGET_MS = 9
/** From 640 px the card floats over the right of the canvas; below, it sits under it. */
const CARD_FLOATS_FROM = 640

const HEX_ANGLES = Array.from({ length: 6 }, (_, i) => (Math.PI / 3) * i - Math.PI / 6)

/**
 * The drawing: a canvas under a pan-and-zoom camera, redrawn every frame by its own loop (the simulation
 * steps, the cells breathe, the pulses travel, the camera glides), and, inside the same application
 * element, one hidden button per node, so the keyboard and a screen reader walk the same graph: Tab lands on
 * the selected node (or the customer), the arrows move by direction, Enter brings a node's relations in,
 * Escape leaves the selection. On the canvas, a pointer on the background pans, the wheel and a pinch zoom,
 * a pointer on a node drags it (and pins it), a click lights it and a double click brings its relations in.
 * The card and the list live outside, in the explorer.
 */
export const GraphCanvas = forwardRef<GraphCamera, CanvasProps>(function GraphCanvas(p, ref) {
  const { graph, layout, sim, visible, edges, activeId, onActive, onHover, onExpand, matches, label, reduced } = p
  const hostRef = useRef<HTMLDivElement>(null)
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const buttonRefs = useRef(new Map<string, HTMLButtonElement>())
  const [hoverId, setHoverIdState] = useState<string | null>(null)
  const [hoverEdge, setHoverEdge] = useState<string | null>(null)
  const [focusedId, setFocusedId] = useState<string | null>(null)
  const size = useRef({ width: 0, height: 0 })
  // The camera: the view, and the glide it is on, if any. Refs, so a frame never waits for a render.
  const view = useRef<View>({ tx: 0, ty: 0, scale: 1 })
  const glide = useRef<Glide | null>(null)
  const activation = useRef<Activation | null>(null)
  const pointers = useRef(new Map<number, { x: number; y: number }>())
  const gesture = useRef<{ kind: 'pan' | 'drag'; id: string | null; startX: number; startY: number; moved: boolean; view: View; pinch?: number } | null>(null)
  const lastClick = useRef<{ id: string; at: number }>({ id: '', at: 0 })
  // What the pointer just left, so its lines fade out over a moment instead of vanishing.
  const hoverGone = useRef<{ id: string; at: number } | null>(null)
  const lastSync = useRef(0)
  const frameMs = useRef(0)
  // The drawing context, asked for once: a document without canvas drawing (a test's) answers null, and the
  // loop then only runs the simulation and the camera.
  const context = useRef<CanvasRenderingContext2D | null | undefined>(undefined)
  // The latest props, for the loop.
  const latest = useRef({ graph, visible, edges, activeId, hoverId, hoverEdge, focusedId, matches, reduced })
  latest.current = { graph, visible, edges, activeId, hoverId, hoverEdge, focusedId, matches, reduced }

  const now = () => (typeof performance !== 'undefined' ? performance.now() : Date.now())

  const setHover = useCallback(
    (id: string | null) => {
      setHoverIdState((cur) => {
        if (cur === id) return cur
        if (cur && !id) hoverGone.current = { id: cur, at: now() }
        return id
      })
      onHover(id)
    },
    [onHover],
  )

  /** The view that holds the whole visible drawing above the strip of controls, with room for marks and labels. */
  const fitted = useCallback((): View => {
    const { width, height } = size.current
    const g = latest.current.graph
    const ids = latest.current.visible
    let v = fitView(bounds(layout.current, ids, 0), width, height)
    for (let pass = 0; pass < 2; pass += 1) {
      const at = v.scale
      const ks = markScale(at) * at
      const ts = textScale(at) * at
      const extent = (id: string) => {
        const n = g.nodes.get(id)
        if (!n) return { left: 0, right: 0, top: 0, bottom: 0 }
        const r = ((NODE_RADIUS[n.kind] + 6) * ks) / at
        const lw = (labelWidth(n) * ts) / at
        const lh = (20 * ts) / at
        return { left: Math.max(r, lw / 2), right: r + lw, top: r + lh, bottom: r + lh }
      }
      v = fitView(bounds(layout.current, ids, 16 / at, extent), width, height)
    }
    return v
  }, [layout])

  const moveTo = useCallback(
    (to: View, animated: boolean) => {
      if (!animated || latest.current.reduced) {
        view.current = to
        glide.current = null
      } else glide.current = { from: view.current, to, at: now() }
    },
    [],
  )

  useImperativeHandle(
    ref,
    () => ({
      fit: () => {
        if (size.current.width > 0) moveTo(fitted(), true)
      },
      zoom: (factor) => moveTo(zoomAt(view.current, factor, size.current.width / 2, size.current.height / 2), true),
    }),
    [fitted, moveTo],
  )

  // The size of the canvas, in CSS pixels and device pixels; the aspect goes to the simulation.
  useEffect(() => {
    const host = hostRef.current
    const canvas = canvasRef.current
    if (!host || !canvas) return
    const report = () => {
      const r = host.getBoundingClientRect()
      size.current = { width: r.width, height: r.height }
      const dpr = Math.min(2, typeof window !== 'undefined' ? window.devicePixelRatio || 1 : 1)
      canvas.width = Math.max(1, Math.round(r.width * dpr))
      canvas.height = Math.max(1, Math.round(r.height * dpr))
      if (r.height > 0) sim.current.aspect = Math.min(2.2, Math.max(1, r.width / r.height))
    }
    report()
    if (typeof ResizeObserver === 'undefined') return
    const ro = new ResizeObserver(report)
    ro.observe(host)
    return () => ro.disconnect()
  }, [sim])

  // The wheel zooms around the pointer; a native listener, so the page does not scroll under the canvas.
  useEffect(() => {
    const el = hostRef.current
    if (!el) return
    const onWheel = (e: WheelEvent) => {
      e.preventDefault()
      const r = el.getBoundingClientRect()
      moveTo(zoomAt(glide.current ? glideView(glide.current, now()) : view.current, Math.exp(-e.deltaY * 0.0015), e.clientX - r.left, e.clientY - r.top), false)
    }
    el.addEventListener('wheel', onWheel, { passive: false })
    return () => el.removeEventListener('wheel', onWheel)
  }, [moveTo])

  // A selection is an activation: the node lights up, and the camera glides to it.
  const lastActive = useRef<string | null>(null)
  useEffect(() => {
    if (activeId === lastActive.current) return
    lastActive.current = activeId
    if (!activeId || !visible.has(activeId)) {
      activation.current = null
      return
    }
    activation.current = { id: activeId, at: now() }
    const n = layout.current.nodes.get(activeId)
    if (n && size.current.width > 0) moveTo(viewOnNode(view.current, n.x, n.y, size.current.width, size.current.height, size.current.width >= CARD_FLOATS_FROM), true)
  }, [activeId, visible, layout, moveTo])

  /**
   * The loop: a few steps of the simulation while it owes any (all of them at once when motion is reduced),
   * the fit it owes once at rest, the camera's glide, then the frame. It runs while the canvas is on screen:
   * at rest the cells breathe, and when motion is reduced the frame is cheap.
   */
  useEffect(() => {
    const raf = typeof requestAnimationFrame === 'function' ? requestAnimationFrame : (cb: () => void) => window.setTimeout(cb, 16)
    const cancel = typeof cancelAnimationFrame === 'function' ? cancelAnimationFrame : window.clearTimeout
    let handle = 0
    let stopped = false
    const tick = () => {
      if (stopped) return
      const t0 = now()
      const s = sim.current
      const l = layout.current
      const opts = { ...s.options, aspect: s.aspect }
      if (l.stepsLeft > 0) {
        if (latest.current.reduced) settle(l, s.edges, opts)
        else {
          // At least one step a frame, more while the budget allows: a small graph settles in a second, a
          // large one keeps the frame rate and takes a few seconds.
          let steps = 0
          do {
            step(l, s.edges, opts)
            steps += 1
          } while (l.stepsLeft > 0 && steps < 6 && now() - t0 < STEP_BUDGET_MS)
        }
      }
      if (l.stepsLeft === 0 && s.fitWhenSettled && size.current.width > 0) {
        let again = false
        if (!s.grown) {
          s.grown = true
          const first = fitView(bounds(l, latest.current.visible, 0), size.current.width, size.current.height)
          const growth = boxGrowth(first.scale)
          if (growth > 1.05 && s.options.boxes) {
            s.options = { ...s.options, boxes: new Map([...s.options.boxes].map(([id, b]) => [id, { width: b.width * growth, height: b.height * growth }])) }
            l.stepsLeft = EXPAND_STEPS
            again = true
          }
        }
        if (!again) {
          s.fitWhenSettled = false
          moveTo(fitted(), true)
        }
      }
      if (glide.current) {
        const g = glide.current
        const t = now()
        view.current = glideView(g, t)
        if (glideDone(g, t)) {
          view.current = g.to
          glide.current = null
        }
      }
      draw(t0)
      frameMs.current = now() - t0
      handle = raf(tick)
    }
    handle = raf(tick)
    return () => {
      stopped = true
      cancel(handle)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const toCanvas = (clientX: number, clientY: number) => {
    const r = hostRef.current?.getBoundingClientRect()
    return { x: clientX - (r?.left ?? 0), y: clientY - (r?.top ?? 0) }
  }
  const toWorld = (sx: number, sy: number): Point => ({ x: (sx - view.current.tx) / view.current.scale, y: (sy - view.current.ty) / view.current.scale })

  const hitRadius = (id: string) => {
    const n = latest.current.graph.nodes.get(id)
    if (!n) return 0
    const ks = markScale(view.current.scale) * view.current.scale
    return Math.max((NODE_RADIUS[n.kind] * 1.5 + 6) * ks, 22)
  }

  const onPointerDown = (e: ReactPointerEvent<HTMLDivElement>) => {
    if (e.button !== 0 && e.pointerType === 'mouse') return
    pointers.current.set(e.pointerId, { x: e.clientX, y: e.clientY })
    const host = hostRef.current
    if (host && typeof host.setPointerCapture === 'function') host.setPointerCapture(e.pointerId)
    if (pointers.current.size === 2) {
      const [a, b] = [...pointers.current.values()]
      gesture.current = { kind: 'pan', id: null, startX: 0, startY: 0, moved: true, view: view.current, pinch: Math.hypot(a!.x - b!.x, a!.y - b!.y) }
      return
    }
    const pt = toCanvas(e.clientX, e.clientY)
    const id = nodeAt(layout.current, view.current, visible, pt.x, pt.y, hitRadius)
    gesture.current = { kind: id ? 'drag' : 'pan', id, startX: e.clientX, startY: e.clientY, moved: false, view: view.current }
    glide.current = null
  }

  const onPointerMove = (e: ReactPointerEvent<HTMLDivElement>) => {
    const g = gesture.current
    const pt = toCanvas(e.clientX, e.clientY)
    if (!g || !pointers.current.has(e.pointerId)) {
      // No gesture: a preview of what is under the pointer.
      const id = nodeAt(layout.current, view.current, visible, pt.x, pt.y, hitRadius)
      setHover(id)
      const edge = id ? null : edgeAt(edges, layout.current, view.current, pt.x, pt.y)
      setHoverEdge((cur) => (cur === (edge?.id ?? null) ? cur : (edge?.id ?? null)))
      return
    }
    pointers.current.set(e.pointerId, { x: e.clientX, y: e.clientY })
    if (pointers.current.size === 2 && g.pinch) {
      const [a, b] = [...pointers.current.values()]
      const d = Math.hypot(a!.x - b!.x, a!.y - b!.y)
      const mid = toCanvas((a!.x + b!.x) / 2, (a!.y + b!.y) / 2)
      moveTo(zoomAt(view.current, d / g.pinch, mid.x, mid.y), false)
      g.pinch = d
      return
    }
    const dx = e.clientX - g.startX
    const dy = e.clientY - g.startY
    if (!g.moved && Math.hypot(dx, dy) < DRAG_THRESHOLD) return
    g.moved = true
    if (g.kind === 'pan') {
      moveTo({ scale: g.view.scale, tx: g.view.tx + dx, ty: g.view.ty + dy }, false)
    } else if (g.id) {
      const n = layout.current.nodes.get(g.id)
      if (!n) return
      const w = toWorld(pt.x, pt.y)
      n.x = w.x
      n.y = w.y
      n.fixed = true
      n.vx = 0
      n.vy = 0
    }
  }

  const onPointerUp = (e: ReactPointerEvent<HTMLDivElement>) => {
    const g = gesture.current
    pointers.current.delete(e.pointerId)
    if (pointers.current.size > 0) return
    gesture.current = null
    if (!g) return
    if (g.kind === 'drag' && g.id && !g.moved) {
      // A click, not a drag: light it. A second click within half a second brings its relations in.
      const t = now()
      const twice = lastClick.current.id === g.id && t - lastClick.current.at < 500
      lastClick.current = { id: g.id, at: t }
      onActive(g.id)
      buttonRefs.current.get(g.id)?.focus({ preventScroll: true })
      if (twice) onExpand(g.id)
    } else if (g.kind === 'pan' && !g.moved) {
      onActive(null)
    }
  }

  const onPointerLeave = () => {
    if (!gesture.current) {
      setHover(null)
      setHoverEdge(null)
    }
  }

  const firstVisible = () => {
    if (graph.center && visible.has(graph.center)) return graph.center
    for (const id of graph.nodes.keys()) if (visible.has(id)) return id
    return null
  }

  const focusNode = (id: string) => {
    onActive(id)
    // After the render that gives the node's button tabIndex 0.
    setTimeout(() => buttonRefs.current.get(id)?.focus({ preventScroll: true }), 0)
  }

  const onKeyDown = (e: KeyboardEvent<HTMLDivElement>) => {
    const current = activeId && visible.has(activeId) ? activeId : null
    const dir = e.key === 'ArrowUp' ? 'up' : e.key === 'ArrowDown' ? 'down' : e.key === 'ArrowLeft' ? 'left' : e.key === 'ArrowRight' ? 'right' : null
    if (dir) {
      e.preventDefault()
      const next = current ? nearestInDirection(layout.current, current, dir, visible) : firstVisible()
      if (next) focusNode(next)
      return
    }
    if (e.key === 'Home') {
      e.preventDefault()
      const home = graph.center && visible.has(graph.center) ? graph.center : firstVisible()
      if (home) focusNode(home)
      return
    }
    if (e.key === 'Escape') {
      e.preventDefault()
      onActive(null)
      hostRef.current?.focus({ preventScroll: true })
      return
    }
    if (e.key === '+' || e.key === '=') moveTo(zoomAt(view.current, 1.25, size.current.width / 2, size.current.height / 2), true)
    if (e.key === '-') moveTo(zoomAt(view.current, 0.8, size.current.width / 2, size.current.height / 2), true)
  }

  /** The hidden buttons carry the screen position of their node, for tools that drive the canvas (the shots). */
  const syncButtons = (pos: (id: string) => Point | undefined, v: View) => {
    for (const [id, el] of buttonRefs.current) {
      const q = pos(id)
      if (!q) continue
      el.dataset.sx = String(Math.round(q.x * v.scale + v.tx))
      el.dataset.sy = String(Math.round(q.y * v.scale + v.ty))
    }
  }

  const draw = (t: number) => {
    const canvas = canvasRef.current
    if (!canvas) return
    if (context.current === undefined) context.current = getContext(canvas)
    const ctx = context.current
    if (!ctx) return
    const { width, height } = size.current
    if (width === 0 || height === 0) return
    const { graph: g, visible: ids, edges: lines, activeId: active, hoverId: hover, hoverEdge: hoverLine, focusedId: focused, matches: hits, reduced: still } = latest.current
    const v = view.current
    const dpr = canvas.width / width
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
    // The ground: dark, with a faint light behind the middle so the network sits in a space.
    ctx.fillStyle = GROUND
    ctx.fillRect(0, 0, width, height)
    const light = ctx.createRadialGradient(width / 2, height / 2, 0, width / 2, height / 2, Math.max(width, height) * 0.6)
    light.addColorStop(0, GROUND_GLOW)
    light.addColorStop(1, GROUND)
    ctx.fillStyle = light
    ctx.globalAlpha = 0.6
    ctx.fillRect(0, 0, width, height)
    ctx.globalAlpha = 1

    const k = markScale(v.scale)
    const ts = textScale(v.scale)
    const ks = k * v.scale
    const txs = ts * v.scale
    const l = layout.current
    // Where each node draws: its place, with its breath.
    const breaths = new Map<string, ReturnType<typeof breath>>()
    const drawn = new Map<string, Point>()
    for (const id of ids) {
      const n = l.nodes.get(id)
      if (!n) continue
      const b = breath(id, t, still)
      breaths.set(id, b)
      drawn.set(id, { x: n.x + b.dx, y: n.y + b.dy })
    }
    const pos = (id: string) => drawn.get(id)
    const toScreen = (q: Point) => ({ x: q.x * v.scale + v.tx, y: q.y * v.scale + v.ty })
    const degrees = new Map<string, number>()
    for (const e of lines) {
      degrees.set(e.from, (degrees.get(e.from) ?? 0) + 1)
      degrees.set(e.to, (degrees.get(e.to) ?? 0) + 1)
    }
    const focus = hover ?? active
    const neighbors = neighborhood(focus && ids.has(focus) ? focus : null, lines)
    // The lines of what the pointer just left, fading out over 150 ms (hover in at once, out gently).
    const gone = hoverGone.current && !hover && hoverGone.current.id !== active && ids.has(hoverGone.current.id) ? hoverGone.current : null
    const goneAlpha = gone ? Math.max(0, 1 - (t - gone.at) / HOVER_FADE_MS) : 0
    if (gone && goneAlpha === 0) hoverGone.current = null
    const act = activation.current && ids.has(activation.current.id) ? activation.current : null
    const lit = act ? neighborLight(act, lines, l, t, still) : new Map<string, number>()
    const dimming = act !== null
    const activeSet = neighborhood(act?.id ?? null, lines)

    // The lobes: the hull of each company and its people, padded by round strokes of growing width and
    // falling alpha, a soft field behind the group. A grouping cue, not a drawing: lime at a few percent,
    // and no canvas filter, which cost the frame rate at fifty companies.
    const pad = Math.max(12, 26 * v.scale)
    ctx.lineJoin = 'round'
    ctx.lineCap = 'round'
    for (const r of regions(g, l, ids)) {
      const dim = dimming && !activeSet.has(r.id) ? DIM.region : 1
      ctx.beginPath()
      r.hull.forEach((q, i) => {
        const sq = toScreen(q)
        if (i === 0) ctx.moveTo(sq.x, sq.y)
        else ctx.lineTo(sq.x, sq.y)
      })
      ctx.closePath()
      for (const [k, a] of [
        [2.2, 0.35],
        [1.5, 0.45],
        [0.9, 0.6],
      ] as const) {
        ctx.strokeStyle = rgba(LIME, REGION_ALPHA * a * dim)
        ctx.lineWidth = pad * 2 * k
        ctx.stroke()
      }
      if (r.hull.length >= 3) {
        ctx.fillStyle = rgba(LIME, REGION_ALPHA * 0.6 * dim)
        ctx.fill()
      }
    }

    // The lines: the ones at rest first, then the lit ones over them.
    ctx.lineCap = 'round'
    const strokeEdge = (e: GraphEdge, on: boolean, onAlpha = 0.85) => {
      const a = pos(e.from)
      const b = pos(e.to)
      if (!a || !b) return
      const from = g.nodes.get(e.from)
      const sa = toScreen(a)
      const sb = toScreen(b)
      const sc = toScreen(controlPoint(a, b, e.id))
      const base = on ? LIME : from ? KIND_STYLE[from.kind].color : INK_SOFT
      // A long line of an agent or a system (it reaches across the drawing) fades with its length on screen,
      // so the ties of the structure stay the ones the eye follows.
      const len = Math.hypot(sb.x - sa.x, sb.y - sa.y)
      const far = EDGE_ALPHA[e.kind] < 0.3 ? Math.max(0.35, 1 - Math.max(0, len - 160) / 600) : 1
      const alpha = on ? onAlpha : EDGE_ALPHA[e.kind] * far * (dimming ? DIM.edge / 0.22 : 1)
      ctx.strokeStyle = rgba(base, Math.min(1, alpha))
      ctx.lineWidth = Math.max(1, (on ? 2 : 1.25) * ks)
      ctx.setLineDash(edgeDashed(e.kind) ? [6 * ks, 5 * ks] : [])
      ctx.beginPath()
      ctx.moveTo(sa.x, sa.y)
      ctx.quadraticCurveTo(sc.x, sc.y, sb.x, sb.y)
      ctx.stroke()
    }
    const onLines: GraphEdge[] = []
    const fading: GraphEdge[] = []
    for (const e of lines) {
      const on = focus !== null && (e.from === focus || e.to === focus)
      if (on) onLines.push(e)
      else {
        strokeEdge(e, false)
        if (gone && goneAlpha > 0 && (e.from === gone.id || e.to === gone.id)) fading.push(e)
      }
    }
    for (const e of fading) strokeEdge(e, true, 0.85 * goneAlpha)
    for (const e of onLines) strokeEdge(e, true)
    ctx.setLineDash([])

    // The pulses: the signal leaving the activated node along its lines.
    if (act) {
      for (const pu of pulses(act, lines, l, t, still)) {
        const a = pos(pu.edge.from)
        const b = pos(pu.edge.to)
        if (!a || !b) continue
        const forward = pu.edge.from === act.id
        const c = controlPoint(a, b, pu.edge.id)
        const at = (tt: number) => toScreen(curvePoint(a, c, b, forward ? tt : 1 - tt))
        pu.trail.forEach((tt, i) => {
          const q = at(tt)
          ctx.fillStyle = rgba(LIME, pu.alpha * (0.5 - i * 0.14))
          ctx.beginPath()
          ctx.arc(q.x, q.y, (2.6 - i * 0.6) * k, 0, Math.PI * 2)
          ctx.fill()
        })
        const head = at(pu.t)
        drawGlow(ctx, head.x, head.y, 14 * k, LIME, pu.alpha)
        ctx.fillStyle = rgba(INK, pu.alpha)
        ctx.beginPath()
        ctx.arc(head.x, head.y, 3.2 * k, 0, Math.PI * 2)
        ctx.fill()
      }
    }

    // The nodes.
    const plan = labelPlan({ nodes: g.nodes.values(), layout: l, visible: ids, view: v, degrees, center: g.center, activeId: active, hoverId: hover, neighbors, matches: hits })
    const marks: { n: GraphNode; s: Point; r: number; dim: boolean }[] = []
    for (const id of ids) {
      const n = g.nodes.get(id)
      const q = pos(id)
      const b = breaths.get(id)
      if (!n || !q || !b) continue
      const isCenter = id === g.center
      const w = weight(n, degrees.get(id) ?? 0, isCenter)
      const r = NODE_RADIUS[n.kind] * w * ks * b.size
      const s = toScreen(q)
      const style = KIND_STYLE[n.kind]
      const dim = dimming && !activeSet.has(id)
      const glow = lit.get(id) ?? (act && id === act.id ? 1.6 : 1)
      const halo = style.glow * b.glow * (dim ? 0 : glow) + (isCenter ? 0.35 : 0) + (hover === id ? 0.3 : 0)
      if (halo > 0) drawGlow(ctx, s.x, s.y, r * 2.8 * Math.min(1.6, 0.8 + glow * 0.4), style.color, Math.min(1, halo * 0.55))
      ctx.globalAlpha = dim ? DIM.node : 1
      // The one lime mark of a customer's screen: the customer it is about, ringed.
      if (isCenter) {
        ctx.strokeStyle = LIME
        ctx.lineWidth = 3 * k
        ring(ctx, s.x, s.y, r + 7 * k)
        ctx.lineWidth = 1 * k
        ctx.strokeStyle = rgba(LIME, 0.6)
        ring(ctx, s.x, s.y, r + 11 * k)
      }
      if (hits.has(id) && !isCenter) {
        ctx.strokeStyle = LIME
        ctx.lineWidth = 2.5 * k
        ctx.setLineDash([3 * k, 3 * k])
        ring(ctx, s.x, s.y, r + 7 * k)
        ctx.setLineDash([])
      }
      if (act && id === act.id) {
        const rp = ripple(act, t, still)
        if (rp) {
          ctx.strokeStyle = rgba(LIME, rp.alpha)
          ctx.lineWidth = 2 * k
          ring(ctx, s.x, s.y, r + rp.grow * k)
        }
        ctx.strokeStyle = rgba(INK, 0.9)
        ctx.lineWidth = 1.5 * k
        ring(ctx, s.x, s.y, r + 10 * k)
      }
      drawMark(ctx, n.kind, s.x, s.y, r, style.color, style.hollow, Math.max(1.5, 1.75 * k))
      if (focused === id) {
        ctx.strokeStyle = LIME
        ctx.lineWidth = 2 * k
        ring(ctx, s.x, s.y, r + 14 * k)
      }
      ctx.globalAlpha = 1
      marks.push({ n, s, r, dim })
    }

    // The labels, where the plan put them.
    ctx.textBaseline = 'middle'
    for (const m of marks) {
      const side = plan.shown.get(m.n.id)
      if (!side) continue
      const isCenter = m.n.id === g.center
      const strong = isCenter || m.n.id === active || m.n.id === hover
      const px = (m.n.kind === 'handle' ? 11 : isCenter ? 13 : 12) * txs
      ctx.font = `${strong ? 700 : 600} ${px}px ${monoLabel(m.n) ? '"JetBrains Mono", ui-monospace, monospace' : '"Instrument Sans", system-ui, sans-serif'}`
      ctx.textAlign = side === 'right' ? 'left' : 'center'
      const gap = 4 * txs
      const x = side === 'right' ? m.s.x + m.r + gap : m.s.x
      const y = side === 'above' ? m.s.y - m.r - gap - px * 0.55 : side === 'right' ? m.s.y : m.s.y + m.r + gap + px * 0.6
      ctx.globalAlpha = m.dim ? DIM.label : 1
      ctx.lineJoin = 'round'
      // The halo is on screen, whatever the zoom: a translucent ground around the letters, enough to lift them
      // off a line, thin enough not to read as a box when the text is small.
      ctx.lineWidth = 2.5
      ctx.strokeStyle = rgba(GROUND, 0.8)
      const text = displayLabel(m.n)
      ctx.strokeText(text, x, y)
      ctx.fillStyle = INK
      ctx.fillText(text, x, y)
      ctx.globalAlpha = 1
    }

    // The words on the lines.
    const labelled = focus && ids.has(focus) ? focus : g.center
    const every = labelled === null && lines.length <= EVERY_LINE_UP_TO
    ctx.textAlign = 'center'
    for (const lb of placeEdgeLabels(lines, labelled, every, pos, g.nodes, v, k, size.current, hoverLine, plan.blocks)) {
      const s = toScreen({ x: lb.x, y: lb.y })
      const w = lb.width * ks
      const h = 20 * ks
      ctx.fillStyle = lb.strong ? LIME : PILL_BG
      ctx.strokeStyle = lb.strong ? LIME : PILL_LINE
      ctx.lineWidth = 1
      roundRect(ctx, s.x - w / 2, s.y - h / 2, w, h, 5 * ks)
      ctx.fill()
      ctx.stroke()
      ctx.font = `600 ${11 * ks}px "Instrument Sans", system-ui, sans-serif`
      ctx.fillStyle = lb.strong ? ON_LIME : INK_SOFT
      ctx.fillText(lb.text, s.x, s.y + 0.5)
    }

    if (t - lastSync.current > 200 || l.stepsLeft > 0) {
      lastSync.current = t
      syncButtons((id) => l.nodes.get(id), v)
    }
  }

  const tabStop = activeId && visible.has(activeId) ? activeId : firstVisible()

  return (
    <div
      ref={hostRef}
      role="application"
      aria-label={label}
      aria-describedby="graph-keys"
      tabIndex={-1}
      className="relative h-full w-full touch-none select-none overflow-hidden rounded-xl outline-none [cursor:grab] active:[cursor:grabbing]"
      style={{ backgroundColor: GROUND }}
      onPointerDown={onPointerDown}
      onPointerMove={onPointerMove}
      onPointerUp={onPointerUp}
      onPointerCancel={onPointerUp}
      onPointerLeave={onPointerLeave}
      onDoubleClick={(e) => e.preventDefault()}
      onKeyDown={onKeyDown}
    >
      <p id="graph-keys" className="sr-only">
        Setas movem entre os itens, Enter traz as relações do item, Escape sai da seleção, mais e menos aproximam e afastam.
      </p>
      <canvas ref={canvasRef} aria-hidden="true" className="block h-full w-full" />
      <ul className="sr-only" aria-label="Itens do mapa">
        {[...graph.nodes.values()].map((n) => {
          if (!visible.has(n.id)) return null
          const isCenter = n.id === graph.center
          const allDegree = edgesOf(graph, n.id).length
          return (
            <li key={n.id}>
              <button
                ref={(el) => {
                  if (el) buttonRefs.current.set(n.id, el)
                  else buttonRefs.current.delete(n.id)
                }}
                type="button"
                tabIndex={n.id === tabStop ? 0 : -1}
                aria-label={`${nodeKind(n.kind)} ${displayLabel(n)}, ${allDegree === 1 ? '1 relação' : `${allDegree} relações`}${isCenter ? ', o cliente desta tela' : ''}`}
                aria-pressed={n.id === activeId}
                onFocus={() => {
                  setFocusedId(n.id)
                  if (activeId !== n.id) onActive(n.id)
                }}
                onBlur={() => setFocusedId((cur) => (cur === n.id ? null : cur))}
                onClick={() => {
                  onActive(n.id)
                  onExpand(n.id)
                }}
              />
            </li>
          )
        })}
      </ul>
    </div>
  )
})

/** A 2D context, or null where the canvas has none (a test's document). */
function getContext(canvas: HTMLCanvasElement): CanvasRenderingContext2D | null {
  try {
    return canvas.getContext('2d') ?? null
  } catch {
    return null
  }
}

const rgbaCache = new Map<string, string>()
/** A hex color with an alpha, as rgba(). */
function rgba(hex: string, alpha: number): string {
  const a = Math.round(Math.min(1, Math.max(0, alpha)) * 1000) / 1000
  const key = `${hex}/${a}`
  const hit = rgbaCache.get(key)
  if (hit) return hit
  const n = parseInt(hex.slice(1), 16)
  const out = `rgba(${(n >> 16) & 255}, ${(n >> 8) & 255}, ${n & 255}, ${a})`
  rgbaCache.set(key, out)
  return out
}

const sprites = new Map<string, HTMLCanvasElement>()
const SPRITE = 96
/** The halo of a color, drawn once into a sprite and stamped at every size: a radial falloff from the color to nothing. */
function sprite(color: string): HTMLCanvasElement | null {
  const hit = sprites.get(color)
  if (hit) return hit
  if (typeof document === 'undefined') return null
  const c = document.createElement('canvas')
  c.width = SPRITE
  c.height = SPRITE
  const ctx = getContext(c)
  if (!ctx) return null
  const grad = ctx.createRadialGradient(SPRITE / 2, SPRITE / 2, 0, SPRITE / 2, SPRITE / 2, SPRITE / 2)
  grad.addColorStop(0, rgba(color, 0.55))
  grad.addColorStop(0.25, rgba(color, 0.28))
  grad.addColorStop(0.6, rgba(color, 0.07))
  grad.addColorStop(1, rgba(color, 0))
  ctx.fillStyle = grad
  ctx.fillRect(0, 0, SPRITE, SPRITE)
  sprites.set(color, c)
  return c
}

function drawGlow(ctx: CanvasRenderingContext2D, x: number, y: number, radius: number, color: string, alpha: number) {
  const s = sprite(color)
  if (!s || alpha <= 0) return
  ctx.globalAlpha = alpha
  ctx.drawImage(s, x - radius, y - radius, radius * 2, radius * 2)
  ctx.globalAlpha = 1
}

function ring(ctx: CanvasRenderingContext2D, x: number, y: number, r: number) {
  ctx.beginPath()
  ctx.arc(x, y, r, 0, Math.PI * 2)
  ctx.stroke()
}

function roundRect(ctx: CanvasRenderingContext2D, x: number, y: number, w: number, h: number, r: number) {
  const rr = Math.min(r, w / 2, h / 2)
  ctx.beginPath()
  ctx.moveTo(x + rr, y)
  ctx.lineTo(x + w - rr, y)
  ctx.quadraticCurveTo(x + w, y, x + w, y + rr)
  ctx.lineTo(x + w, y + h - rr)
  ctx.quadraticCurveTo(x + w, y + h, x + w - rr, y + h)
  ctx.lineTo(x + rr, y + h)
  ctx.quadraticCurveTo(x, y + h, x, y + h - rr)
  ctx.lineTo(x, y + rr)
  ctx.quadraticCurveTo(x, y, x + rr, y)
  ctx.closePath()
}

/**
 * One shape per kind, so a reader tells them apart beyond color: a person is a cell (a disc with a brighter
 * core), a company a rounded square, a partner a diamond outline, an identifier a small ring, an object an
 * outlined record, an agent a hexagon and a system an outlined rectangle. `r` is the radius on screen.
 */
function drawMark(ctx: CanvasRenderingContext2D, kind: GraphNode['kind'], x: number, y: number, r: number, color: string, hollow: boolean, stroke: number) {
  ctx.fillStyle = color
  ctx.strokeStyle = color
  ctx.lineWidth = stroke
  ctx.lineJoin = 'round'
  switch (kind) {
    case 'person': {
      ctx.beginPath()
      ctx.arc(x, y, r, 0, Math.PI * 2)
      ctx.fill()
      // The core: a brighter heart, so the disc reads as a cell and not a flat dot.
      ctx.fillStyle = rgba('#ffffff', 0.55)
      ctx.beginPath()
      ctx.arc(x - r * 0.18, y - r * 0.18, r * 0.42, 0, Math.PI * 2)
      ctx.fill()
      return
    }
    case 'account':
      roundRect(ctx, x - r * 0.93, y - r * 0.93, r * 1.86, r * 1.86, r * 0.22)
      ctx.fill()
      ctx.fillStyle = rgba('#ffffff', 0.4)
      roundRect(ctx, x - r * 0.5, y - r * 0.5, r * 0.62, r * 0.62, r * 0.12)
      ctx.fill()
      return
    case 'partner':
      ctx.save()
      ctx.translate(x, y)
      ctx.rotate(Math.PI / 4)
      roundRect(ctx, -r * 0.72, -r * 0.72, r * 1.44, r * 1.44, r * 0.14)
      ctx.restore()
      if (hollow) ctx.stroke()
      else ctx.fill()
      return
    case 'handle':
      ctx.fillStyle = GROUND
      ctx.beginPath()
      ctx.arc(x, y, r, 0, Math.PI * 2)
      ctx.fill()
      ctx.stroke()
      return
    case 'object':
      ctx.fillStyle = GROUND
      roundRect(ctx, x - r, y - r * 0.66, r * 2, r * 1.32, r * 0.2)
      ctx.fill()
      ctx.stroke()
      return
    case 'agent': {
      ctx.beginPath()
      HEX_ANGLES.forEach((a, i) => {
        const px = x + Math.cos(a) * r
        const py = y + Math.sin(a) * r
        if (i === 0) ctx.moveTo(px, py)
        else ctx.lineTo(px, py)
      })
      ctx.closePath()
      ctx.fill()
      return
    }
    case 'system':
      ctx.fillStyle = GROUND
      roundRect(ctx, x - r, y - r * 0.72, r * 2, r * 1.44, r * 0.2)
      ctx.fill()
      ctx.stroke()
      return
  }
}
