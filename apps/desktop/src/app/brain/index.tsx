import { useCallback, useEffect, useMemo, useState } from 'react'

import { getLearningNode } from '@/api/skills'
import { PageLoader } from '@/components/page-loader'
import { getStarmapGraph } from '@/pulse'
import type { StarmapGraph, StarmapNode } from '@/types/pulse'

import { Panel, PanelEmpty } from '../overlays/panel'

import { BrainExplorer } from './brain-explorer'
import type { BrainNodeDetail, BrainNodeSummary } from './types'

export function BrainView({
  onClose,
  onOpenStarmap
}: {
  onClose: () => void
  onOpenStarmap?: () => void
}) {
  const [graph, setGraph] = useState<StarmapGraph | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const [activeNodeId, setActiveNodeId] = useState<string | null>(null)
  const [activeNodeDetail, setActiveNodeDetail] = useState<BrainNodeDetail | null>(null)
  const [nodeLoading, setNodeLoading] = useState(false)

  // Load graph on mount
  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setError(null)

    getStarmapGraph()
      .then(data => {
        if (!cancelled) {
          setGraph(data)
          // Default select self/identity if present, or first brain node
          const firstBrain = data.nodes.find(n => n.kind === 'brain')
          if (firstBrain) {
            setActiveNodeId(firstBrain.id)
          }
        }
      })
      .catch(err => {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : String(err))
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })

    return () => {
      cancelled = true
    }
  }, [])

  // Load active node detail when activeNodeId changes
  const handleSelectNode = useCallback((nodeId: string) => {
    setActiveNodeId(nodeId)
    setNodeLoading(true)

    getLearningNode(nodeId)
      .then(detail => {
        const payload: BrainNodeDetail = {
          id: nodeId,
          label: detail.label,
          category: (detail as unknown as Record<string, unknown>).category as string || 'concept',
          content: detail.content,
          confidence: (detail as unknown as Record<string, unknown>).confidence as number,
          status: (detail as unknown as Record<string, unknown>).status as string,
          tags: (detail as unknown as Record<string, unknown>).tags as string[],
          wikilinks: (detail as unknown as Record<string, unknown>).wikilinks as string[],
          backlinks: (detail as unknown as Record<string, unknown>).backlinks as string[],
        }
        setActiveNodeDetail(payload)
      })
      .catch(() => {
        // Fallback detail if node fetch fails
        setActiveNodeDetail({
          id: nodeId,
          label: nodeId.split('/').pop()?.replace(/-/g, ' ') || nodeId,
          category: nodeId.split('/')[0] || 'concept',
          content: `# ${nodeId}\n\nNote referenced in knowledge graph.`,
        })
      })
      .finally(() => {
        setNodeLoading(false)
      })
  }, [])

  useEffect(() => {
    if (activeNodeId) {
      handleSelectNode(activeNodeId)
    }
  }, [activeNodeId, handleSelectNode])

  // Extract brain nodes summaries and edges from graph
  const { brainNodes, brainEdges } = useMemo(() => {
    if (!graph) return { brainNodes: [], brainEdges: [] }
    const nodes = graph.nodes
      .filter((n: StarmapNode) => n.kind === 'brain' || n.id.includes('/'))
      .map((n: StarmapNode) => ({
        id: n.id,
        label: n.label,
        category: n.category,
        confidence: n.confidence,
        status: n.state,
        tags: n.tags,
        timestamp: n.timestamp,
        path: n.path,
        wikilinksCount: n.wikilinksCount,
        backlinksCount: n.backlinksCount,
      }))
    const edges = (graph.edges || []).map((e: { source: string; target: string }) => [e.source, e.target] as [string, string])
    return { brainNodes: nodes, brainEdges: edges }
  }, [graph])

  return (
    <Panel closeLabel="Close Brain Explorer" onClose={onClose}>
      {error ? (
        <PanelEmpty description={error} icon="warning" title="Failed to load Brain Vault" />
      ) : loading && !graph ? (
        <PageLoader aria-label="Loading Brain Vault..." className="min-h-0 flex-1" />
      ) : (
        <BrainExplorer
          activeNode={activeNodeDetail}
          edges={brainEdges}
          loading={nodeLoading}
          nodes={brainNodes}
          onOpenStarmap={onOpenStarmap}
          onSelectNode={handleSelectNode}
        />
      )}
    </Panel>
  )
}
