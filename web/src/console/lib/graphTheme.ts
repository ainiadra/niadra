/**
 * The colors and sizes of the relationship graph's surface. The graph is the one dark moment of the Console,
 * as the brand allows ("the dark block inside the light theme"): the memory of a space is drawn as
 * a living network, and a glow only reads against a dark ground. Every value here is a token of the inverted
 * block of `src/index.css` or derives from one; the three families are the subjects of the memory (pale ink
 * for people, lime for companies and partners, the lobes of the drawing), the records (identifiers and
 * objects, dim pale), and the actors outside the memory (agents and systems, a cool sky, so a reader tells at
 * a glance who is remembered from who talked). Text on this ground is checked at 4.5:1 or better
 * (contrast-check.py: #eff1eb 16.5:1, #a9ada3 8.2:1, #8e9288 5.9:1, #a2d500 10.8:1, #9dd6ff 12.1:1).
 */
import type { GraphEdgeKind, GraphNodeKind } from '../api/graph'

export const GROUND = '#10130a'
/** The faint radial light behind the drawing: the ground is not flat, the network sits in a space. */
export const GROUND_GLOW = '#1c2015'
export const INK = '#eff1eb'
export const INK_SOFT = '#a9ada3'
const INK_DIM = '#8e9288'
export const LIME = '#a2d500'
const SKY = '#9dd6ff'
export const PILL_BG = '#1c2015'
export const PILL_LINE = '#31362a'
export const ON_LIME = '#10130a'

export interface KindStyle {
  /** The fill or stroke of the mark. */
  color: string
  /** Solid marks are cells; hollow ones are records or actors drawn as outlines. */
  hollow: boolean
  /** How strong the halo around the mark is at rest (0 for none): the cells of the memory glow, the records do not. */
  glow: number
}

export const KIND_STYLE: Record<GraphNodeKind, KindStyle> = {
  person: { color: INK, hollow: false, glow: 0.4 },
  account: { color: LIME, hollow: false, glow: 0.8 },
  partner: { color: LIME, hollow: true, glow: 0.45 },
  handle: { color: INK_DIM, hollow: true, glow: 0 },
  object: { color: INK_SOFT, hollow: true, glow: 0 },
  agent: { color: SKY, hollow: false, glow: 0.5 },
  system: { color: SKY, hollow: true, glow: 0.3 },
}

/** The ties of the structure are drawn firmer than the lines of an agent or a system, which touch everyone. */
export const EDGE_ALPHA: Record<GraphEdgeKind, number> = {
  identifies: 0.55,
  member_of: 0.55,
  partner_of: 0.55,
  parent_of: 0.55,
  owns: 0.5,
  relates_to: 0.45,
  suggested_same: 0.5,
  acted_on: 0.22,
  talked_with: 0.22,
  reported: 0.22,
  handed_off: 0.35,
}

/** What recedes when one node is lit: the rest of the drawing, not gone, just behind. */
export const DIM = {
  /** Receded marks stay at about 3:1 on the ground (0.4 of pale ink), so the whole drawing is still legible. */
  node: 0.4,
  edge: 0.07,
  /** A label at 0.62 of pale ink over the ground still reads at about 6:1. */
  label: 0.62,
  region: 0.35,
}

/** The lime of a company's lobe behind its people: the soft strokes and the fill of its hull add up to about this at the core. */
export const REGION_ALPHA = 0.045
