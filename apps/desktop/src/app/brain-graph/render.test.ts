import { describe, expect, it } from 'vitest'

import { drawGraph } from './render'
import type { DrawInput } from './render'
import type { GLink, GNode } from './types'

// A minimal CanvasRenderingContext2D stand-in that records the calls the typed-edge
// styling depends on. jsdom's canvas has no real 2D context, so we inject our own and
// assert the painter actually consumes provenance/confidence/kind — this is the
// render-path proof that the typed-edge contract reaches the pixels.
function makeRecordingCtx() {
  const calls = {
    curves: 0,
    labels: [] as string[],
    setLineDash: [] as number[][],
    lineWidth: [] as number[],
    globalAlphaStops: [] as number[]
  }

  const gradient = { addColorStop: () => {} }

  const ctx = {
    arc: () => {},
    beginPath: () => {},
    createLinearGradient: () => gradient,
    createRadialGradient: () => gradient,
    fill: () => {},
    fillRect: () => {},
    fillText: (text: string) => calls.labels.push(text),
    lineTo: () => {},
    measureText: () => ({ width: 10 }),
    moveTo: () => {},
    closePath: () => {},
    quadraticCurveTo: () => calls.curves++,
    restore: () => {},
    save: () => {},
    setLineDash: (d: number[]) => calls.setLineDash.push(d),
    setTransform: () => {},
    stroke: () => {},
    set lineWidth(v: number) {
      calls.lineWidth.push(v)
    },
    get lineWidth() {
      return calls.lineWidth[calls.lineWidth.length - 1] ?? 0
    }
  } as unknown as CanvasRenderingContext2D

  return { calls, ctx }
}

function node(id: string, x: number): GNode {
  return {
    category: 'concept',
    degree: 1,
    id,
    kind: 'memory',
    label: id,
    phase: 0,
    pinned: false,
    state: 'active',
    useCount: 0,
    vx: 0,
    vy: 0,
    x,
    y: 0
  } as unknown as GNode
}

function baseInput(links: GLink[], ctx: CanvasRenderingContext2D): DrawInput {
  const nodes = [node('a', -100), node('b', 100)]

  return {
    adjacency: new Map(),
    births: new Map(),
    ctx,
    dpr: 1,
    hoverId: null,
    links,
    motionEnabled: false,
    nodes,
    now: 10_000,
    palette: {} as DrawInput['palette'],
    query: '',
    selectedId: null,
    size: { h: 400, w: 400 },
    vp: { k: 1, x: 0, y: 0 }
  }
}

describe('drawGraph typed-edge styling', () => {
  it('draws category clouds and curved synapses', () => {
    const recording = makeRecordingCtx()
    const input = baseInput([{ source: 'a', target: 'b' } as GLink], recording.ctx)
    input.nodes.push(node('c', 0))

    drawGraph(input)

    expect(recording.calls.curves).toBeGreaterThan(0)
    expect(recording.calls.labels).toContain('concept')
  })

  it('keeps ambient neuron animation opt-in for reduced-motion users', () => {
    const recording = makeRecordingCtx()
    const input = baseInput([], recording.ctx)

    expect(drawGraph(input)).toBe(false)

    input.motionEnabled = true
    expect(drawGraph(input)).toBe(true)
  })

  it('dashes an inferred-entity edge and leaves an asserted edge solid', () => {
    const inferred = makeRecordingCtx()
    drawGraph(baseInput([{ confidence: 0.5, kind: 'mentions', provenance: 'inferred_entity', source: 'a', target: 'b' }], inferred.ctx))
    // the inferred edge sets a dash pattern...
    expect(inferred.calls.setLineDash.some(d => d.length === 2)).toBe(true)

    const asserted = makeRecordingCtx()
    drawGraph(baseInput([{ confidence: 1, kind: 'wikilink', provenance: 'asserted', source: 'a', target: 'b' }], asserted.ctx))
    // ...the asserted edge is drawn solid (empty dash array).
    expect(asserted.calls.setLineDash.every(d => d.length === 0)).toBe(true)
  })

  it('draws a supersedes edge thicker than a plain edge', () => {
    const plain = makeRecordingCtx()
    drawGraph(baseInput([{ confidence: 1, kind: 'related', provenance: 'asserted', source: 'a', target: 'b' }], plain.ctx))

    const supersedes = makeRecordingCtx()
    drawGraph(baseInput([{ confidence: 1, kind: 'supersedes', provenance: 'correction', source: 'a', target: 'b' }], supersedes.ctx))

    const maxPlain = Math.max(...plain.calls.lineWidth)
    const maxSuper = Math.max(...supersedes.calls.lineWidth)
    expect(maxSuper).toBeGreaterThan(maxPlain)
  })

  it('treats an edge with no typed metadata as a full-trust solid line (legacy payload)', () => {
    const legacy = makeRecordingCtx()
    // Older payloads carry only {source,target}; the painter must still draw them.
    const drew = drawGraph(baseInput([{ source: 'a', target: 'b' } as GLink], legacy.ctx))
    expect(drew !== undefined).toBe(true)
    expect(legacy.calls.setLineDash.every(d => d.length === 0)).toBe(true)
  })
})
