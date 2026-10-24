import { useStore } from '@nanostores/react'
import { useEffect } from 'react'

import { PageLoader } from '@/components/page-loader'
import { useI18n } from '@/i18n'
import { subscribeBrainEvents } from '@/store/brain-events'
import { $starmapError, $starmapGraph, $starmapLoading, loadStarmapGraph } from '@/store/starmap'

import { BrainGraph } from '../brain-graph/brain-graph'
import { Panel, PanelEmpty } from '../overlays/panel'

// Memory overlay: the live, Obsidian-style force graph of everything PULSE has
// learned for a profile — every note is a node, every [[wikilink]] an edge.
// §14/§16: real-time Brain events drive graph updates; the 4s poll is removed.
export function StarmapView({ onClose }: { onClose: () => void }) {
  const { t } = useI18n()
  const graph = useStore($starmapGraph)
  const loading = useStore($starmapLoading)
  const error = useStore($starmapError)

  useEffect(() => {
    void loadStarmapGraph()
  }, [])

  // §14: subscribe to real-time Brain events on mount; unsubscribe on unmount.
  useEffect(() => {
    const unsubscribe = subscribeBrainEvents()

    return unsubscribe
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
