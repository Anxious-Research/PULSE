import { useStore } from '@nanostores/react'
import { useEffect } from 'react'

import { PageLoader } from '@/components/page-loader'
import { useI18n } from '@/i18n'
import { $starmapError, $starmapGraph, $starmapLoading, loadStarmapGraph } from '@/store/starmap'

import { BrainGraph } from '../brain-graph/brain-graph'
import { Panel, PanelEmpty } from '../overlays/panel'

// How often the open graph re-reads the vault so newly written notes appear on
// their own. The graph diffs each fetch and only animates what actually
// changed, so this is cheap and never disturbs the layout.
const LIVE_REFRESH_MS = 4000

// Memory overlay: the live, Obsidian-style force graph of everything PULSE has
// learned for a profile — every note is a node, every [[wikilink]] an edge.
// Data is fetched on demand into the $starmap* atoms and re-read on a timer so
// the graph grows in front of you as the brain encodes new memories.
export function StarmapView({ onClose }: { onClose: () => void }) {
  const { t } = useI18n()
  const graph = useStore($starmapGraph)
  const loading = useStore($starmapLoading)
  const error = useStore($starmapError)

  useEffect(() => {
    void loadStarmapGraph()
  }, [])

  // Live reaction: poll the vault while the graph is open. loadStarmapGraph
  // de-dupes in-flight requests and the graph only moves for real changes.
  useEffect(() => {
    const timer = window.setInterval(() => void loadStarmapGraph(true), LIVE_REFRESH_MS)

    return () => window.clearInterval(timer)
  }, [])

  return (
    <Panel closeLabel={t.starmap.close} onClose={onClose}>
      {error ? (
        <PanelEmpty description={error} icon="warning" title={t.starmap.loadFailed} />
      ) : !graph && loading ? (
        <PageLoader aria-label={t.starmap.loading} className="min-h-0 flex-1" />
      ) : graph && graph.nodes.length === 0 ? (
        <PanelEmpty description={t.starmap.emptyDesc} icon="lightbulb" title={t.starmap.emptyTitle} />
      ) : graph ? (
        <BrainGraph graph={graph} onRefresh={() => void loadStarmapGraph(true)} />
      ) : null}
    </Panel>
  )
}
