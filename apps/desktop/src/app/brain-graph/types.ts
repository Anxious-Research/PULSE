import type { SimulationLinkDatum, SimulationNodeDatum } from 'd3-force'

import type { StarmapGraph, StarmapNode } from '@/types/pulse'

export interface Rgb {
  b: number
  g: number
  r: number
}

export interface Viewport {
  k: number
  x: number
  y: number
}

/** A node in the live force simulation. Positions are world-space; the viewport
 *  maps them to the screen (Obsidian's infinite-canvas model). */
export interface GNode extends StarmapNode, SimulationNodeDatum {
  degree: number
  x: number
  y: number
}

export interface GLink extends SimulationLinkDatum<GNode> {
  source: GNode | string
  target: GNode | string
  /** Relationship type carried from the typed-edge model (styles the stroke). */
  kind?: string
  /** How the relationship was established ('asserted' | 'inferred_entity' | 'correction'). */
  provenance?: string
  /** Trust 0–1: drives stroke opacity so weak inferred links read as fainter. */
  confidence?: number
}

/** Obsidian-style force knobs (the "Forces" section of its graph settings). */
export interface ForceParams {
  /** Pull toward the canvas centre, 0–0.3. */
  center: number
  /** Link attraction strength, 0–1. */
  linkStrength: number
  /** Rest length of a link, in world units. */
  linkDistance: number
  /** Node repulsion (charge magnitude). */
  repel: number
}

export const DEFAULT_FORCES: ForceParams = {
  center: 0.05,
  linkDistance: 64,
  linkStrength: 0.45,
  repel: 200
}

export interface GraphPalette {
  bg: Rgb
  fg: Rgb
  dark: boolean
  /** ink for one node, already resolved for the theme */
  nodeInk: (n: StarmapNode) => Rgb
}

export type Graph = Pick<StarmapGraph, 'edges' | 'nodes'>
