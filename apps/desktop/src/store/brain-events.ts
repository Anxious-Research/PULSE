/**
 * Real-time Brain event subscriber (§14, §16).
 *
 * Connects to `/api/events?channel=brain` on starmap mount, applies mutations to the cached
 * graph atom, catches up after reconnect via `/api/brain/events/recent?since=seq`, and
 * reconciles with authoritative state when the gap is too large (§15).
 */
import { profileScoped } from '@/api/client'
import { activeConnection, getApiRequestProfile, getStarmapGraph, pulseApi } from '@/pulse'
import type { StarmapActivity, StarmapNode } from '@/types/pulse'

import { $starmapActivity, $starmapGraph } from './starmap'

interface BrainEvent {
  seq: number
  kind: string
  node_id: string
  payload?: Record<string, any>
  timestamp?: number | string
}

const MAX_ACTIVITY_EVENTS = 64
const pendingEvents: BrainEvent[] = []
let ws: WebSocket | null = null
let lastSeq = 0
let activityId = 0
let catchingUp = false
let bufferedEvents: BrainEvent[] = []
let reconnectTimer: ReturnType<typeof setTimeout> | null = null
let isSubscribed = false

export async function resolveBrainEventsWsUrl(): Promise<string | null> {
  if (typeof window === 'undefined') {
    return null
  }

  // 1. Electron Desktop mode: route through the main process to the active backend
  if (window.pulseDesktop) {
    try {
      const profile = getApiRequestProfile()

      // When getGatewayWsUrl is available, mint fresh WS URL (handles token & oauth tickets)
      if (typeof window.pulseDesktop.getGatewayWsUrl === 'function') {
        const res = await window.pulseDesktop.getGatewayWsUrl(profile)
        const wsUrl = typeof res === 'string' ? res : res?.ok ? res.wsUrl : null

        if (wsUrl) {
          const url = new URL(wsUrl)
          url.pathname = url.pathname.replace(/\/(api\/)?ws$/, '/api/events')
          url.searchParams.set('channel', 'brain')

          if (profile && !url.searchParams.has('profile')) {
            url.searchParams.set('profile', profile)
          }

          return url.toString()
        }
      }

      // Fallback: active connection baseUrl + token
      const conn = await activeConnection().catch(() => null)

      if (conn?.baseUrl) {
        const base = conn.baseUrl.replace(/^http/, 'ws')
        const tokenParam = conn.token ? `&token=${encodeURIComponent(conn.token)}` : ''
        const profileParam = profile ? `&profile=${encodeURIComponent(profile)}` : ''

        return `${base}/api/events?channel=brain${tokenParam}${profileParam}`
      }
    } catch (err) {
      console.warn('[brain-events] failed to resolve desktop WS URL:', err)
    }
  }

  // 2. Web / Dashboard / Vite Dev fallback
  const apiBase = import.meta.env.VITE_API_BASE || ''

  if (apiBase) {
    const base = apiBase.replace(/^http/, 'ws')

    return `${base}/api/events?channel=brain`
  }

  if (typeof window !== 'undefined' && window.location?.origin) {
    const loc = window.location
    const proto = loc.protocol === 'https:' ? 'wss:' : 'ws:'

    return `${proto}//${loc.host}/api/events?channel=brain`
  }

  return null
}

export async function fetchRecentEvents(since: number): Promise<{ seq: number; events: BrainEvent[] } | null> {
  if (typeof window !== 'undefined' && typeof window.pulseDesktop?.api === 'function') {
    try {
      return await pulseApi<{ seq: number; events: BrainEvent[] }>({
        ...profileScoped(),
        path: `/api/brain/events/recent?since=${since}`
      })
    } catch (err) {
      console.warn('[brain-events] pulseApi catch-up failed:', err)

      return null
    }
  }

  try {
    const apiBase = import.meta.env.VITE_API_BASE || ''
    const res = await fetch(`${apiBase}/api/brain/events/recent?since=${since}`)

    if (res.ok) {
      return await res.json()
    }
  } catch (err) {
    console.warn('[brain-events] fetch catch-up failed:', err)
  }

  return null
}

/**
 * Subscribe to real-time Brain events. Call once on starmap mount; idempotent.
 */
export function subscribeBrainEvents(): () => void {
  if (isSubscribed) {
    return unsubscribe
  }

  isSubscribed = true

  void connect()

  return unsubscribe
}

async function connect(): Promise<void> {
  if (ws || !isSubscribed) {
    return
  }

  catchingUp = true
  bufferedEvents = []
  const wsUrl = await resolveBrainEventsWsUrl()

  if (!wsUrl || !isSubscribed) {
    catchingUp = false

    if (isSubscribed && !reconnectTimer) {
      reconnectTimer = setTimeout(() => {
        reconnectTimer = null
        void connect()
      }, 2000)
    }

    return
  }

  try {
    const socket = new WebSocket(wsUrl)
    ws = socket

    socket.onopen = async () => {
      if (ws !== socket) {
        socket.close()

        return
      }

      console.log('[brain-events] connected')

      if (reconnectTimer) {
        clearTimeout(reconnectTimer)
        reconnectTimer = null
      }

      // Catch up on missed events (§14: handle reconnect)
      try {
        const recent = await fetchRecentEvents(lastSeq)

        if (recent) {
          const { seq, events } = recent

          for (const event of events || []) {
            consumeEvent(event)
          }

          lastSeq = Math.max(lastSeq, seq || lastSeq)
        }
      } catch (err) {
        console.warn('[brain-events] catch-up failed:', err)
      } finally {
        catchingUp = false
        const pending = bufferedEvents
        bufferedEvents = []

        for (const event of pending) {
          consumeEvent(event)
        }
      }
    }

    socket.onmessage = msg => {
      try {
        const event: BrainEvent = JSON.parse(msg.data)

        if (catchingUp) {
          bufferedEvents.push(event)
        } else {
          consumeEvent(event)
        }
      } catch (err) {
        console.warn('[brain-events] parse error:', err)
      }
    }

    socket.onerror = err => {
      console.error('[brain-events] error:', err)
    }

    socket.onclose = () => {
      console.log('[brain-events] closed')

      if (ws === socket) {
        ws = null
      }

      if (isSubscribed && !reconnectTimer) {
        // Auto-reconnect with exponential backoff (§14)
        reconnectTimer = setTimeout(() => {
          reconnectTimer = null
          void connect()
        }, 2000)
      }
    }
  } catch (err) {
    console.error('[brain-events] socket init failed:', err)
    ws = null
    catchingUp = false

    if (isSubscribed && !reconnectTimer) {
      reconnectTimer = setTimeout(() => {
        reconnectTimer = null
        void connect()
      }, 2000)
    }
  }
}

function consumeEvent(event: BrainEvent): void {
  if (event.seq && event.seq <= lastSeq) {
    return
  }

  applyBrainEvent(event)
  lastSeq = event.seq || lastSeq
}

function unsubscribe(): void {
  isSubscribed = false

  if (reconnectTimer) {
    clearTimeout(reconnectTimer)
    reconnectTimer = null
  }

  if (ws) {
    ws.close()
    ws = null
  }
}

/** Apply events received before the initial graph fetch completed. */
export function replayPendingBrainEvents(): void {
  const pending = pendingEvents.splice(0)

  for (const event of pending) {
    applyBrainEvent(event)
  }
}

/**
 * Apply a single Brain event to the cached graph atom (§12: every graph mutation must have
 * a real source). Events arriving before the initial graph fetch are replayed once it loads.
 */
export function applyBrainEvent(event: BrainEvent): void {
  const graph = $starmapGraph.get()

  if (!graph) {
    pendingEvents.push(event)

    return
  }

  const { kind, node_id, payload } = event

  switch (kind) {
    case 'MEMORY_CREATED': {
      // Real node creation (§12)
      if (graph.nodes.some(n => n.id === node_id || n.vaultId === node_id)) {
        break // already present (duplicate event or catch-up overlap)
      }

      const newNode: StarmapNode = {
        id: `brain:${node_id}`,
        vaultId: node_id,
        label: payload?.title || node_id.split('/').pop()?.replace(/-/g, ' ') || node_id,
        kind: 'memory',
        category: payload?.category || 'concept',
        state: 'active',
        useCount: 0,
        createdBy: null,
        pinned: false,
        timestamp: typeof event.timestamp === 'number' ? event.timestamp : null
      }

      $starmapGraph.set({
        ...graph,
        nodes: [...graph.nodes, newNode]
      })

      break
    }

    case 'MEMORY_UPDATED': {
      // Real node update (§12)
      const idx = graph.nodes.findIndex(n => n.vaultId === node_id)

      if (idx === -1) {
        break
      }

      const updated = {
        ...graph.nodes[idx],
        label: payload?.title || graph.nodes[idx].label,
        category: payload?.category || graph.nodes[idx].category
      }

      const nodes = [...graph.nodes]
      nodes[idx] = updated
      $starmapGraph.set({ ...graph, nodes })

      break
    }

    case 'MEMORY_REINFORCED': {
      // Real activation state change (§12, §25)
      const idx = graph.nodes.findIndex(n => n.vaultId === node_id)

      if (idx === -1) {
        break
      }

      const reinforced = {
        ...graph.nodes[idx],
        useCount: (graph.nodes[idx].useCount || 0) + 1
      }

      const nodes = [...graph.nodes]
      nodes[idx] = reinforced
      $starmapGraph.set({ ...graph, nodes })

      break
    }

    case 'MEMORY_SUPERSEDED': {
      // Real status/history update (§12)
      const idx = graph.nodes.findIndex(n => n.vaultId === node_id)

      if (idx === -1) {
        break
      }

      const superseded = {
        ...graph.nodes[idx],
        state: 'superseded'
      }

      const nodes = [...graph.nodes]
      nodes[idx] = superseded
      $starmapGraph.set({ ...graph, nodes })

      break
    }

    case 'MEMORY_ARCHIVED':
    case 'MEMORY_DECAYED': {
      const idx = graph.nodes.findIndex(n => n.vaultId === node_id)

      if (idx === -1) {
        break
      }

      const archived = {
        ...graph.nodes[idx],
        state: kind === 'MEMORY_ARCHIVED' ? 'archived' : 'dormant'
      }

      const nodes = [...graph.nodes]
      nodes[idx] = archived
      $starmapGraph.set({ ...graph, nodes })

      break
    }

    case 'MEMORY_DELETED': {
      // Real node removal (§12)
      $starmapGraph.set({
        ...graph,
        nodes: graph.nodes.filter(n => n.vaultId !== node_id),
        edges: graph.edges.filter(e => {
          const sourceVault = graph.nodes.find(n => n.id === e.source)?.vaultId
          const targetVault = graph.nodes.find(n => n.id === e.target)?.vaultId

          return sourceVault !== node_id && targetVault !== node_id
        })
      })

      break
    }

    case 'RELATIONSHIP_CREATED': {
      // Real edge appears (§12)
      const targetId = payload?.target

      if (!targetId) {
        break
      }

      const source = graph.nodes.find(n => n.vaultId === node_id)
      const target = graph.nodes.find(n => n.vaultId === targetId)

      if (!source || !target) {
        break
      }

      if (
        graph.edges.some(
          e => (e.source === source.id && e.target === target.id) || (e.source === target.id && e.target === source.id)
        )
      ) {
        break // already present
      }

      $starmapGraph.set({
        ...graph,
        edges: [
          ...graph.edges,
          { source: source.id, target: target.id, kind: payload?.rel || 'related', provenance: 'asserted' }
        ]
      })

      break
    }

    case 'RELATIONSHIP_UPDATED': {
      const targetId = payload?.target
      const source = graph.nodes.find(n => n.vaultId === node_id)
      const target = typeof targetId === 'string' ? graph.nodes.find(n => n.vaultId === targetId) : undefined

      if (!source || !target) {
        break
      }

      const edges = graph.edges.map(edge =>
        (edge.source === source.id && edge.target === target.id) ||
        (edge.source === target.id && edge.target === source.id)
          ? { ...edge, kind: payload?.rel || edge.kind }
          : edge
      )

      $starmapGraph.set({ ...graph, edges })

      break
    }

    case 'RELATIONSHIP_REMOVED': {
      const targetId = payload?.target
      const source = graph.nodes.find(n => n.vaultId === node_id)
      const target = typeof targetId === 'string' ? graph.nodes.find(n => n.vaultId === targetId) : undefined

      if (!source || !target) {
        break
      }

      $starmapGraph.set({
        ...graph,
        edges: graph.edges.filter(
          edge =>
            !(
              (edge.source === source.id && edge.target === target.id) ||
              (edge.source === target.id && edge.target === source.id)
            )
        )
      })

      break
    }

    case 'MEMORY_ACTIVATED':

    case 'MEMORY_REACTIVATED':

    case 'CONSOLIDATION_COMPLETED':
      break

    default:
      console.warn('[brain-events] unknown event kind:', kind)

      return
  }

  const activity: StarmapActivity = {
    id: ++activityId,
    kind,
    nodeId: node_id,
    receivedAt: performance.now(),
    ...(typeof payload?.target === 'string' ? { targetId: payload.target } : {}),
    ...(typeof payload?.activation === 'number' ? { activation: payload.activation } : {})
  }

  $starmapActivity.set([...$starmapActivity.get().slice(-(MAX_ACTIVITY_EVENTS - 1)), activity])
}

/**
 * Reconcile the cached graph with the authoritative Brain state (§15: recovery from dropped
 * events, stale cache, large sequence gap). Call this when the event stream is known-bad.
 */
export async function reconcileStarmapGraph(): Promise<void> {
  try {
    const fresh = await getStarmapGraph()
    $starmapGraph.set(fresh)
    console.log('[brain-events] reconciled with authoritative graph')
  } catch (err) {
    console.error('[brain-events] reconciliation failed:', err)
  }
}
