import { render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { BrainInspector } from './brain-inspector'

// Mock the API so the inspector's data path is exercised without a backend. The component must
// render exactly what the canonical model returns — evidence, confidence justification, history.
vi.mock('@/api/skills', () => ({
  getBrainNodeDetail: vi.fn()
}))

import { getBrainNodeDetail } from '@/api/skills'

const mocked = getBrainNodeDetail as unknown as ReturnType<typeof vi.fn>

afterEach(() => {
  mocked.mockReset()
})

describe('BrainInspector', () => {
  it('renders nothing when no node is selected', () => {
    const { container } = render(<BrainInspector onClose={() => {}} vaultId={null} />)
    expect(container.firstChild).toBeNull()
  })

  it('shows a belief with evidence, confidence justification and revision history', async () => {
    mocked.mockResolvedValue({
      ok: true,
      id: 'belief/x',
      label: 'The service runs on GCP',
      kind: 'belief',
      belief_status: 'active',
      content: 'The service was migrated to the GCP cloud region',
      confidence: 0.73,
      confidence_justification: 'inferred from 2 independent piece(s) of evidence → 0.73 (active).',
      evidence: [
        { id: 'concept/a', label: 'migrated to GCP', missing: false, kind: 'supports' },
        { id: 'concept/b', label: 'hosted on GCP', missing: false, kind: 'supports' }
      ],
      counter_evidence: [{ id: 'concept/c', label: 'not on GCP', missing: false, kind: 'contradicts' }],
      revisions: [{ at: 1, reason: 'contradiction', from: 0.89, to: 0.58 }],
      timestamps: { created_at: 1_700_000_000 }
    })
    render(<BrainInspector label="X" onClose={() => {}} vaultId="belief/x" />)

    await waitFor(() => expect(screen.getByText('The service was migrated to the GCP cloud region')).toBeTruthy())
    expect(screen.getByText(/inferred from 2 independent/)).toBeTruthy()
    expect(screen.getByText('migrated to GCP')).toBeTruthy()
    expect(screen.getByText(/Supporting evidence \(2\)/)).toBeTruthy()
    expect(screen.getByText(/Counter-evidence \(1\)/)).toBeTruthy()
    expect(screen.getByText(/Revision history/)).toBeTruthy()
    expect(screen.getByText('contradiction')).toBeTruthy()
  })

  it('shows a procedure with known limitations and track record', async () => {
    mocked.mockResolvedValue({
      ok: true,
      id: 'procedure/deploy',
      label: 'Procedure: Deploy',
      kind: 'procedure',
      procedure_status: 'reliable',
      goal: 'Deploy the web service',
      success_rate: 0.75,
      counts: { success: 3, failure: 1, partial: 0 },
      failure_modes: ['in-place restart causes 502s'],
      confidence_justification: '3 success / 1 failure outcome(s) → success rate 0.75.',
      outcomes: [{}, {}]
    })
    render(<BrainInspector onClose={() => {}} vaultId="procedure/deploy" />)

    await waitFor(() => expect(screen.getByText('Deploy the web service')).toBeTruthy())
    expect(screen.getByText(/Known limitations/)).toBeTruthy()
    expect(screen.getByText('in-place restart causes 502s')).toBeTruthy()
    expect(screen.getByText(/success rate 75%/)).toBeTruthy()
  })

  it('shows a backend error message', async () => {
    mocked.mockResolvedValue({ ok: false, message: 'node not found' })
    render(<BrainInspector onClose={() => {}} vaultId="concept/missing" />)
    await waitFor(() => expect(screen.getByText('node not found')).toBeTruthy())
  })
})
