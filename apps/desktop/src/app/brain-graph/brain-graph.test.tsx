import { render } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import type { StarmapGraph } from '@/types/pulse'

import { BrainGraph } from './brain-graph'

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

describe('BrainGraph', () => {
  it('renders a canvas', () => {
    const { container } = render(<BrainGraph graph={mockGraph} />)
    const canvas = container.querySelector('canvas')

    expect(canvas).toBeTruthy()
  })

  it('renders controls with ⌘K search', () => {
    const { getByPlaceholderText, getByText } = render(<BrainGraph graph={mockGraph} />)

    expect(getByPlaceholderText('Search memories…')).toBeTruthy()
    expect(getByText('Forces')).toBeTruthy()
  })

  it('renders legend with categories from the graph', () => {
    const { getByText } = render(<BrainGraph graph={mockGraph} />)

    expect(getByText('concept')).toBeTruthy()
    expect(getByText('user')).toBeTruthy()
  })

  it('shows live metrics in HUD card', () => {
    const { getByText } = render(<BrainGraph graph={mockGraph} />)

    expect(getByText('Memories')).toBeTruthy()
    expect(getByText('Connections')).toBeTruthy()
    expect(getByText('Clusters')).toBeTruthy()
    expect(getByText('∿ Live')).toBeTruthy()
  })
})
