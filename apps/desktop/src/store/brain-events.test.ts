import { beforeEach, describe, expect, it, vi } from 'vitest'

import type { StarmapGraph } from '@/types/pulse'

import { applyBrainEvent, replayPendingBrainEvents } from './brain-events'
import { $starmapActivity, $starmapGraph } from './starmap'

vi.mock('@/pulse', () => ({ getStarmapGraph: vi.fn() }))

const graph: StarmapGraph = {
  clusters: [],
  edges: [],
  memory: [],
  nodes: [
    {
      category: 'concept',
      createdBy: null,
      id: 'memory:a',
      kind: 'memory',
      label: 'Alpha',
      pinned: false,
      state: 'active',
      useCount: 1,
      vaultId: 'concept/a'
    },
    {
      category: 'concept',
      createdBy: null,
      id: 'memory:b',
      kind: 'memory',
      label: 'Beta',
      pinned: false,
      state: 'active',
      useCount: 0,
      vaultId: 'concept/b'
    }
  ],
  stats: {}
}

describe('brain event graph updates', () => {
  beforeEach(() => {
    $starmapGraph.set(graph)
    $starmapActivity.set([])
  })

  it('updates memory data and emits an activity pulse immediately', () => {
    applyBrainEvent({
      kind: 'MEMORY_REINFORCED',
      node_id: 'concept/a',
      payload: { access_count: 2 },
      seq: 1
    })

    expect($starmapGraph.get()?.nodes[0].useCount).toBe(2)
    expect($starmapActivity.get()).toHaveLength(1)
    expect($starmapActivity.get()[0].nodeId).toBe('concept/a')
  })

  it('adds a relationship as soon as its event arrives', () => {
    applyBrainEvent({
      kind: 'RELATIONSHIP_CREATED',
      node_id: 'concept/a',
      payload: { rel: 'wikilink', target: 'concept/b' },
      seq: 2
    })

    expect($starmapGraph.get()?.edges).toEqual([
      { kind: 'wikilink', provenance: 'asserted', source: 'memory:a', target: 'memory:b' }
    ])
    expect($starmapActivity.get()[0].targetId).toBe('concept/b')
  })

  it('replays events that arrive before the first graph snapshot', () => {
    $starmapGraph.set(null)
    applyBrainEvent({
      kind: 'MEMORY_CREATED',
      node_id: 'concept/new',
      payload: { category: 'concept', title: 'New memory' },
      seq: 3,
      timestamp: 1_798_000_000
    })

    expect($starmapActivity.get()).toHaveLength(0)
    $starmapGraph.set(graph)
    replayPendingBrainEvents()

    expect($starmapGraph.get()?.nodes.some(node => node.vaultId === 'concept/new')).toBe(true)
    expect($starmapActivity.get()).toHaveLength(1)
  })
})
