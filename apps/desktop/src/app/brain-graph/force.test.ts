import { describe, expect, it } from 'vitest'

import type { StarmapGraph } from '@/types/pulse'

import { bounds, buildGraph, nodeRadius } from './force'

const mockGraph: StarmapGraph = {
  clusters: [],
  edges: [
    { source: 'a', target: 'b' },
    { source: 'b', target: 'c' }
  ],
  memory: [],
  nodes: [
    { category: 'concept', createdBy: null, id: 'a', kind: 'memory', label: 'Alpha', pinned: false, state: 'active', useCount: 3 },
    { category: 'user', createdBy: null, id: 'b', kind: 'memory', label: 'Beta', pinned: false, state: 'active', useCount: 1 },
    { category: 'self', createdBy: null, id: 'c', kind: 'ghost', label: 'Gamma', pinned: false, state: 'active', useCount: 0 }
  ],
  stats: {}
}

describe('force', () => {
  describe('nodeRadius', () => {
    it('returns a small disc for ghosts', () => {
      expect(nodeRadius({ degree: 0, id: 'x', kind: 'ghost', label: 'X' } as never)).toBe(2.4)
    })

    it('grows with degree', () => {
      const r1 = nodeRadius({ degree: 0, id: 'a', kind: 'memory', label: 'A', pinned: false } as never)
      const r2 = nodeRadius({ degree: 4, id: 'b', kind: 'memory', label: 'B', pinned: false } as never)

      expect(r2).toBeGreaterThan(r1)
    })

    it('adds a boost for pinned nodes', () => {
      const r1 = nodeRadius({ degree: 2, id: 'a', kind: 'memory', label: 'A', pinned: false } as never)
      const r2 = nodeRadius({ degree: 2, id: 'b', kind: 'memory', label: 'B', pinned: true } as never)

      expect(r2).toBe(r1 + 1.5)
    })
  })

  describe('buildGraph', () => {
    it('builds a simulation with nodes and links', () => {
      const { byId, links, nodes, sim } = buildGraph(mockGraph, new Map(), () => {})

      expect(nodes).toHaveLength(3)
      expect(links).toHaveLength(2)
      expect(byId.size).toBe(3)
      expect(sim).toBeTruthy()
    })

    it('assigns degree from adjacency', () => {
      const { nodes } = buildGraph(mockGraph, new Map(), () => {})

      const b = nodes.find(n => n.id === 'b')

      expect(b?.degree).toBe(2)
    })

    it('seeds world positions from a prior build', () => {
      const seed = new Map([
        ['a', { x: 50, y: 100 }],
        ['b', { x: 200, y: 300 }]
      ])

      const { nodes } = buildGraph(mockGraph, seed, () => {})

      const a = nodes.find(n => n.id === 'a')
      const b = nodes.find(n => n.id === 'b')

      expect(a?.x).toBe(50)
      expect(a?.y).toBe(100)
      expect(b?.x).toBe(200)
      expect(b?.y).toBe(300)
    })

    it('filters links to existing nodes', () => {
      const graphWithDangling: StarmapGraph = {
        ...mockGraph,
        edges: [...mockGraph.edges, { source: 'a', target: 'missing' }]
      }

      const { links } = buildGraph(graphWithDangling, new Map(), () => {})

      expect(links).toHaveLength(2)
    })

    it('carries the typed-edge contract (kind/provenance/confidence) onto links', () => {
      const typedGraph: StarmapGraph = {
        ...mockGraph,
        edges: [
          { confidence: 1, kind: 'wikilink', provenance: 'asserted', source: 'a', target: 'b' },
          { confidence: 0.5, kind: 'mentions', provenance: 'inferred_entity', source: 'b', target: 'c' }
        ]
      }

      const { links } = buildGraph(typedGraph, new Map(), () => {})

      const idOf = (x: unknown): string => (typeof x === 'string' ? x : (x as { id: string }).id)
      const ab = links.find(l => idOf(l.source) === 'a' && idOf(l.target) === 'b')
      const bc = links.find(l => idOf(l.source) === 'b' && idOf(l.target) === 'c')
      expect(ab?.kind).toBe('wikilink')
      expect(ab?.provenance).toBe('asserted')
      expect(ab?.confidence).toBe(1)
      expect(bc?.kind).toBe('mentions')
      expect(bc?.confidence).toBe(0.5)
    })
  })

  describe('bounds', () => {
    it('returns zeroes for an empty cloud', () => {
      const b = bounds([])

      expect(b).toEqual({ maxX: 0, maxY: 0, minX: 0, minY: 0 })
    })

    it('computes the bounding box', () => {
      const nodes = [
        { degree: 0, id: 'a', kind: 'memory', label: 'A', pinned: false, x: 10, y: 20 },
        { degree: 1, id: 'b', kind: 'memory', label: 'B', pinned: false, x: 50, y: 80 }
      ] as never[]

      const b = bounds(nodes)

      expect(b.minX).toBeLessThan(10)
      expect(b.maxX).toBeGreaterThan(50)
      expect(b.minY).toBeLessThan(20)
      expect(b.maxY).toBeGreaterThan(80)
    })
  })
})
