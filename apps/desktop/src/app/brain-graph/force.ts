// Force-directed layout — the same family of forces Obsidian's graph uses:
// repulsion (charge) so nodes spread, link attraction so linked notes pull
// together, a gentle centering pull, and collision so discs never overlap.
//
// Nothing here is radial or time-anchored. The layout is emergent: run the
// simulation and the graph finds its own shape, then keeps reacting as nodes
// are added, dragged, or recalled.

import { forceCollide, forceLink, forceManyBody, forceSimulation, forceX, forceY, type Simulation } from 'd3-force'

import type { StarmapGraph, StarmapNode } from '@/types/pulse'

import type { ForceParams, GLink, GNode } from './types'

export interface BuiltGraph {
  byId: Map<string, GNode>
  links: GLink[]
  nodes: GNode[]
  sim: Simulation<GNode, GLink>
}

/** Disc radius, Obsidian-style: a small base that grows with link count. */
export function nodeRadius(n: StarmapNode & { degree?: number }): number {
  const degree = n.degree ?? 0

  if (n.kind === 'ghost') {
    return 2.4
  }

  return 3 + Math.sqrt(degree) * 2.1 + (n.pinned ? 1.5 : 0)
}

/** Build the simulation. `seed` carries forward world positions from a previous
 *  build (keyed by id) so a live refresh keeps every settled node in place and
 *  only the genuinely new ones fall in from the centre. */
export function buildGraph(
  graph: StarmapGraph,
  seed: Map<string, { x: number; y: number }>,
  onTick: () => void
): BuiltGraph {
  const adjacency = new Map<string, number>()

  for (const e of graph.edges) {
    adjacency.set(e.source, (adjacency.get(e.source) ?? 0) + 1)
    adjacency.set(e.target, (adjacency.get(e.target) ?? 0) + 1)
  }

  const known = new Set(graph.nodes.map(n => n.id))

  const nodes: GNode[] = graph.nodes.map((n, i) => {
    const prev = seed.get(n.id)
    // New nodes spawn on a golden-angle spiral around the centroid so they
    // don't all stack at (0,0) before the first tick.
    const angle = i * 2.399963
    const radius = 8 + Math.sqrt(i) * 7

    return {
      ...n,
      degree: adjacency.get(n.id) ?? 0,
      x: prev?.x ?? Math.cos(angle) * radius,
      y: prev?.y ?? Math.sin(angle) * radius
    }
  })

  const byId = new Map(nodes.map(n => [n.id, n]))

  const links: GLink[] = graph.edges
    .filter(e => byId.has(e.source) && byId.has(e.target) && known.has(e.source) && known.has(e.target))
    .map(e => ({ source: e.source, target: e.target }))

  const sim = forceSimulation<GNode, GLink>(nodes)
    .alphaDecay(0.0228)
    .velocityDecay(0.4)
    .on('tick', onTick)

  applyForces(sim, links, {
    center: 0.05,
    linkDistance: 64,
    linkStrength: 0.45,
    repel: 200
  })

  return { byId, links, nodes, sim }
}

/** (Re)wire the forces from the panel's current values. */
export function applyForces(sim: Simulation<GNode, GLink>, links: GLink[], params: ForceParams): void {
  sim
    .force('charge', forceManyBody<GNode>().strength(-params.repel).distanceMax(900).distanceMin(4))
    .force(
      'link',
      forceLink<GNode, GLink>(links)
        .id(n => n.id)
        .distance(params.linkDistance)
        .strength(params.linkStrength)
    )
    .force('collide', forceCollide<GNode>().radius(n => nodeRadius(n) + 3).iterations(2))
    .force('x', forceX<GNode>(0).strength(params.center))
    .force('y', forceY<GNode>(0).strength(params.center))
}

/** Settle the layout synchronously so the first frame is already tidy, then
 *  leave a little alpha so it can breathe. */
export function warmUp(sim: Simulation<GNode, GLink>, ticks = 140): void {
  sim.stop()

  for (let i = 0; i < ticks; i += 1) {
    sim.tick()
  }

  sim.alpha(0.12)
}

/** World-space bounding box of the node cloud. */
export function bounds(nodes: GNode[]): { maxX: number; maxY: number; minX: number; minY: number } {
  if (!nodes.length) {
    return { maxX: 0, maxY: 0, minX: 0, minY: 0 }
  }

  let minX = Infinity
  let minY = Infinity
  let maxX = -Infinity
  let maxY = -Infinity

  for (const n of nodes) {
    const r = nodeRadius(n)

    minX = Math.min(minX, n.x - r)
    minY = Math.min(minY, n.y - r)
    maxX = Math.max(maxX, n.x + r)
    maxY = Math.max(maxY, n.y + r)
  }

  return { maxX, maxY, minX, minY }
}
