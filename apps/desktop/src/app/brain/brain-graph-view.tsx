import {
  forceCenter,
  forceCollide,
  forceLink,
  forceManyBody,
  forceSimulation,
  type Simulation,
  type SimulationLinkDatum,
  type SimulationNodeDatum,
} from 'd3-force'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'

import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'

import type { BrainCategory, BrainNodeSummary } from './types'

export interface GraphLinkDatum extends SimulationLinkDatum<GraphNodeDatum> {
  source: string | GraphNodeDatum
  target: string | GraphNodeDatum
}

export interface GraphNodeDatum extends SimulationNodeDatum {
  id: string
  label: string
  category: string
  radius: number
  degree: number
  tags?: string[]
  status?: string
  confidence?: number
}

const CATEGORY_COLORS: Record<string, { fill: string; stroke: string; glow: string }> = {
  self: { fill: '#a855f7', stroke: '#c084fc', glow: 'rgba(168, 85, 247, 0.4)' },
  user: { fill: '#06b6d4', stroke: '#22d3ee', glow: 'rgba(6, 182, 212, 0.4)' },
  concept: { fill: '#f59e0b', stroke: '#fbbf24', glow: 'rgba(245, 158, 11, 0.4)' },
  belief: { fill: '#10b981', stroke: '#34d399', glow: 'rgba(16, 185, 129, 0.4)' },
  project: { fill: '#f43f5e', stroke: '#fb7185', glow: 'rgba(244, 63, 94, 0.4)' },
  default: { fill: '#8b5cf6', stroke: '#a78bfa', glow: 'rgba(139, 92, 246, 0.4)' },
}

interface BrainGraphViewProps {
  nodes: BrainNodeSummary[]
  edges: Array<[string, string]>
  activeNodeId: string | null
  onSelectNode: (nodeId: string) => void
  searchQuery?: string
  selectedCategory?: BrainCategory
  className?: string
}

export function BrainGraphView({
  nodes,
  edges,
  activeNodeId,
  onSelectNode,
  searchQuery = '',
  selectedCategory = 'all',
  className,
}: BrainGraphViewProps) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null)
  const containerRef = useRef<HTMLDivElement | null>(null)

  // Viewport transform (pan/zoom)
  const [zoom, setZoom] = useState(1.0)
  const [pan, setPan] = useState({ x: 0, y: 0 })
  const [hoveredNode, setHoveredNode] = useState<GraphNodeDatum | null>(null)
  const [localMode, setLocalMode] = useState(false)

  // Simulation references
  const simRef = useRef<Simulation<GraphNodeDatum, GraphLinkDatum> | null>(null)
  const simNodesRef = useRef<GraphNodeDatum[]>([])
  const simLinksRef = useRef<GraphLinkDatum[]>([])

  // Drag state
  const dragRef = useRef<{
    active: boolean
    node: GraphNodeDatum | null
    startX: number
    startY: number
    isPanning: boolean
  }>({
    active: false,
    node: null,
    startX: 0,
    startY: 0,
    isPanning: false,
  })

  // Calculate degrees & filter nodes based on category and local mode
  const { filteredNodes, filteredEdges, nodeDegreeMap } = useMemo(() => {
    const degMap = new Map<string, number>()
    edges.forEach(([src, tgt]) => {
      degMap.set(src, (degMap.get(src) || 0) + 1)
      degMap.set(tgt, (degMap.get(tgt) || 0) + 1)
    })

    // Compute 1-hop and 2-hop neighbors for local mode
    const localNeighborSet = new Set<string>()
    if (localMode && activeNodeId) {
      localNeighborSet.add(activeNodeId)
      edges.forEach(([s, t]) => {
        if (s === activeNodeId) localNeighborSet.add(t)
        if (t === activeNodeId) localNeighborSet.add(s)
      })
    }

    const nList = nodes.filter(n => {
      if (localMode && activeNodeId && !localNeighborSet.has(n.id)) return false
      if (selectedCategory !== 'all' && n.category !== selectedCategory) return false
      return true
    })

    const nodeIds = new Set(nList.map(n => n.id))
    const eList = edges.filter(([s, t]) => nodeIds.has(s) && nodeIds.has(t))

    return { filteredNodes: nList, filteredEdges: eList, nodeDegreeMap: degMap }
  }, [nodes, edges, selectedCategory, localMode, activeNodeId])

  // Neighbor adjacency map for fast hover spotlights
  const adjacencyMap = useMemo(() => {
    const map = new Map<string, Set<string>>()
    filteredEdges.forEach(([s, t]) => {
      if (!map.has(s)) map.set(s, new Set())
      if (!map.has(t)) map.set(t, new Set())
      map.get(s)!.add(t)
      map.get(t)!.add(s)
    })
    return map
  }, [filteredEdges])

  // Initialize or update D3 force simulation
  useEffect(() => {
    if (!containerRef.current) return

    const width = containerRef.current.clientWidth || 800
    const height = containerRef.current.clientHeight || 600

    // Build sim nodes, preserving previous coordinates if they existed
    const prevPositions = new Map<string, { x?: number; y?: number; vx?: number; vy?: number }>()
    simNodesRef.current.forEach(n => {
      prevPositions.set(n.id, { x: n.x, y: n.y, vx: n.vx, vy: n.vy })
    })

    const simNodes: GraphNodeDatum[] = filteredNodes.map(n => {
      const prev = prevPositions.get(n.id)
      const degree = nodeDegreeMap.get(n.id) || 0
      const radius = Math.max(5, Math.min(16, 6 + degree * 1.8))

      return {
        id: n.id,
        label: n.label,
        category: n.category || 'concept',
        radius,
        degree,
        tags: n.tags,
        status: n.status,
        confidence: n.confidence,
        x: prev?.x ?? width / 2 + (Math.random() - 0.5) * 300,
        y: prev?.y ?? height / 2 + (Math.random() - 0.5) * 300,
        vx: prev?.vx ?? 0,
        vy: prev?.vy ?? 0,
      }
    })

    const nodeMap = new Map(simNodes.map(n => [n.id, n]))
    const simLinks: GraphLinkDatum[] = filteredEdges
      .filter(([s, t]) => nodeMap.has(s) && nodeMap.has(t))
      .map(([s, t]) => ({
        source: s,
        target: t,
      }))

    simNodesRef.current = simNodes
    simLinksRef.current = simLinks

    if (simRef.current) {
      simRef.current.stop()
    }

    const sim = forceSimulation<GraphNodeDatum>(simNodes)
      .force(
        'link',
        forceLink<GraphNodeDatum, GraphLinkDatum>(simLinks)
          .id(d => d.id)
          .distance(70)
          .strength(0.6)
      )
      .force('charge', forceManyBody().strength(-140).distanceMax(450))
      .force('collide', forceCollide().radius(d => (d as GraphNodeDatum).radius + 8))
      .force('center', forceCenter(width / 2, height / 2).strength(0.08))
      .alphaDecay(0.02)

    simRef.current = sim

    return () => {
      sim.stop()
    }
  }, [filteredNodes, filteredEdges, nodeDegreeMap])

  // Fit to screen helper
  const handleFitScreen = useCallback(() => {
    if (!containerRef.current || simNodesRef.current.length === 0) return
    const width = containerRef.current.clientWidth || 800
    const height = containerRef.current.clientHeight || 600

    let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity
    simNodesRef.current.forEach(n => {
      if (n.x !== undefined && n.y !== undefined) {
        minX = Math.min(minX, n.x)
        maxX = Math.max(maxX, n.x)
        minY = Math.min(minY, n.y)
        maxY = Math.max(maxY, n.y)
      }
    })

    if (minX !== Infinity) {
      const graphW = Math.max(100, maxX - minX + 120)
      const graphH = Math.max(100, maxY - minY + 120)
      const scale = Math.min(1.6, Math.max(0.4, Math.min(width / graphW, height / graphH)))
      const midX = (minX + maxX) / 2
      const midY = (minY + maxY) / 2

      setZoom(scale)
      setPan({
        x: width / 2 - midX * scale,
        y: height / 2 - midY * scale,
      })
    }
  }, [])

  // Render loop
  useEffect(() => {
    let animId: number
    const canvas = canvasRef.current
    if (!canvas) return
    const ctx = canvas.getContext('2d')
    if (!ctx) return

    const render = () => {
      const dpr = window.devicePixelRatio || 1
      const width = canvas.clientWidth
      const height = canvas.clientHeight

      if (canvas.width !== width * dpr || canvas.height !== height * dpr) {
        canvas.width = width * dpr
        canvas.height = height * dpr
      }

      ctx.save()
      ctx.scale(dpr, dpr)
      ctx.clearRect(0, 0, width, height)

      // Apply Pan and Zoom
      ctx.translate(pan.x, pan.y)
      ctx.scale(zoom, zoom)

      const nodes = simNodesRef.current
      const links = simLinksRef.current
      const queryLower = searchQuery.toLowerCase().trim()

      const focusNode = hoveredNode || (activeNodeId ? nodes.find(n => n.id === activeNodeId) : null)
      const focusNeighbors = focusNode ? adjacencyMap.get(focusNode.id) || new Set() : null

      // Draw Edges / Links
      links.forEach(link => {
        const src = typeof link.source === 'object' ? link.source : nodes.find(n => n.id === link.source)
        const tgt = typeof link.target === 'object' ? link.target : nodes.find(n => n.id === link.target)
        if (!src || !tgt || src.x === undefined || src.y === undefined || tgt.x === undefined || tgt.y === undefined) return

        let isHighlighted = false
        let isDimmed = false

        if (focusNode) {
          if (
            (src.id === focusNode.id && focusNeighbors?.has(tgt.id)) ||
            (tgt.id === focusNode.id && focusNeighbors?.has(src.id))
          ) {
            isHighlighted = true
          } else {
            isDimmed = true
          }
        }

        ctx.beginPath()
        ctx.moveTo(src.x, src.y)
        ctx.lineTo(tgt.x, tgt.y)

        if (isHighlighted) {
          ctx.strokeStyle = 'rgba(168, 85, 247, 0.8)'
          ctx.lineWidth = 2.2
        } else if (isDimmed) {
          ctx.strokeStyle = 'rgba(148, 163, 184, 0.08)'
          ctx.lineWidth = 0.8
        } else {
          ctx.strokeStyle = 'rgba(148, 163, 184, 0.28)'
          ctx.lineWidth = 1.2
        }
        ctx.stroke()
      })

      // Draw Nodes
      nodes.forEach(node => {
        if (node.x === undefined || node.y === undefined) return

        const isHovered = hoveredNode?.id === node.id
        const isActive = activeNodeId === node.id
        const isNeighbor = focusNode && focusNeighbors?.has(node.id)
        const isMatchSearch = queryLower && (
          node.label.toLowerCase().includes(queryLower) ||
          node.id.toLowerCase().includes(queryLower) ||
          node.tags?.some(t => t.toLowerCase().includes(queryLower))
        )

        let opacity = 1.0
        if (focusNode) {
          if (node.id === focusNode.id || isNeighbor) {
            opacity = 1.0
          } else {
            opacity = 0.15
          }
        }

        const colors = CATEGORY_COLORS[node.category] || CATEGORY_COLORS.default

        // Outer glow halo for active / hovered / matched nodes
        if (isActive || isHovered || isMatchSearch) {
          ctx.beginPath()
          ctx.arc(node.x, node.y, node.radius + (isActive ? 7 : 5), 0, Math.PI * 2)
          ctx.fillStyle = colors.glow
          ctx.fill()
        }

        // Main Node Circle
        ctx.beginPath()
        ctx.arc(node.x, node.y, node.radius, 0, Math.PI * 2)
        ctx.fillStyle = `rgba(${hexToRgb(colors.fill)}, ${opacity})`
        ctx.fill()
        ctx.strokeStyle = `rgba(${hexToRgb(colors.stroke)}, ${opacity})`
        ctx.lineWidth = isActive ? 2.5 : 1.5
        ctx.stroke()

        // Text Labels (Show if zoomed in, or if active, hovered, neighbor, or large node)
        const shouldShowLabel =
          zoom > 0.8 ||
          isActive ||
          isHovered ||
          isNeighbor ||
          isMatchSearch ||
          node.degree >= 3

        if (shouldShowLabel && opacity > 0.3) {
          ctx.font = `${isActive ? '600' : '500'} ${Math.max(10, Math.min(13, 11 / Math.sqrt(zoom)))}px -apple-system, BlinkMacSystemFont, sans-serif`
          ctx.fillStyle = `rgba(240, 240, 245, ${opacity * 0.95})`
          ctx.textAlign = 'center'
          ctx.fillText(node.label, node.x, node.y + node.radius + 12)
        }
      })

      ctx.restore()
      animId = requestAnimationFrame(render)
    }

    animId = requestAnimationFrame(render)
    return () => cancelAnimationFrame(animId)
  }, [pan, zoom, hoveredNode, activeNodeId, searchQuery, adjacencyMap])

  // Mouse & Pointer Interactions
  const getCanvasCoords = (clientX: number, clientY: number) => {
    if (!canvasRef.current) return { x: 0, y: 0 }
    const rect = canvasRef.current.getBoundingClientRect()
    const screenX = clientX - rect.left
    const screenY = clientY - rect.top
    return {
      x: (screenX - pan.x) / zoom,
      y: (screenY - pan.y) / zoom,
      screenX,
      screenY,
    }
  }

  const findNodeAt = (graphX: number, graphY: number): GraphNodeDatum | null => {
    const nodes = simNodesRef.current
    for (let i = nodes.length - 1; i >= 0; i--) {
      const n = nodes[i]
      if (n.x !== undefined && n.y !== undefined) {
        const dx = n.x - graphX
        const dy = n.y - graphY
        const hitRadius = (n.radius + 6)
        if (dx * dx + dy * dy <= hitRadius * hitRadius) {
          return n
        }
      }
    }
    return null
  }

  const handlePointerDown = (e: React.PointerEvent<HTMLCanvasElement>) => {
    const { x, y, screenX, screenY } = getCanvasCoords(e.clientX, e.clientY)
    const hit = findNodeAt(x, y)

    if (hit) {
      dragRef.current = {
        active: true,
        node: hit,
        startX: screenX,
        startY: screenY,
        isPanning: false,
      }
      hit.fx = hit.x
      hit.fy = hit.y
      if (simRef.current) simRef.current.alphaTarget(0.3).restart()
    } else {
      dragRef.current = {
        active: true,
        node: null,
        startX: screenX - pan.x,
        startY: screenY - pan.y,
        isPanning: true,
      }
    }
  }

  const handlePointerMove = (e: React.PointerEvent<HTMLCanvasElement>) => {
    const { x, y, screenX, screenY } = getCanvasCoords(e.clientX, e.clientY)

    if (dragRef.current.active) {
      if (dragRef.current.node) {
        const node = dragRef.current.node
        node.fx = x
        node.fy = y
      } else if (dragRef.current.isPanning) {
        setPan({
          x: screenX - dragRef.current.startX,
          y: screenY - dragRef.current.startY,
        })
      }
    } else {
      // Hover detection
      const hit = findNodeAt(x, y)
      setHoveredNode(hit)
    }
  }

  const handlePointerUp = (e: React.PointerEvent<HTMLCanvasElement>) => {
    if (dragRef.current.active && dragRef.current.node) {
      const { screenX, screenY } = getCanvasCoords(e.clientX, e.clientY)
      const dist = Math.hypot(screenX - dragRef.current.startX, screenY - dragRef.current.startY)

      if (dist < 5) {
        // Pure click
        onSelectNode(dragRef.current.node.id)
      }

      dragRef.current.node.fx = null
      dragRef.current.node.fy = null
      if (simRef.current) simRef.current.alphaTarget(0)
    }

    dragRef.current = {
      active: false,
      node: null,
      startX: 0,
      startY: 0,
      isPanning: false,
    }
  }

  const handleWheel = (e: React.WheelEvent<HTMLCanvasElement>) => {
    e.preventDefault()
    const zoomFactor = e.deltaY < 0 ? 1.1 : 0.9
    const newZoom = Math.max(0.2, Math.min(3.5, zoom * zoomFactor))

    if (!canvasRef.current) return
    const rect = canvasRef.current.getBoundingClientRect()
    const mouseX = e.clientX - rect.left
    const mouseY = e.clientY - rect.top

    setPan(prev => ({
      x: mouseX - (mouseX - prev.x) * (newZoom / zoom),
      y: mouseY - (mouseY - prev.y) * (newZoom / zoom),
    }))
    setZoom(newZoom)
  }

  return (
    <div ref={containerRef} className={cn('relative w-full h-full overflow-hidden bg-background select-none', className)}>
      <canvas
        ref={canvasRef}
        className="w-full h-full cursor-grab active:cursor-grabbing block"
        onPointerDown={handlePointerDown}
        onPointerMove={handlePointerMove}
        onPointerUp={handlePointerUp}
        onWheel={handleWheel}
      />

      {/* Floating Graph Toolbar */}
      <div className="absolute bottom-4 right-4 flex items-center gap-1.5 bg-card/85 backdrop-blur-md border border-border p-1.5 rounded-lg shadow-lg z-10">
        <Button
          size="sm"
          variant={localMode ? 'default' : 'outline'}
          className="text-xs h-7 px-2"
          onClick={() => setLocalMode(!localMode)}
        >
          {localMode ? '🔍 Local Subgraph' : '🌐 Global Graph'}
        </Button>
        <Button
          size="sm"
          variant="outline"
          className="text-xs h-7 px-2"
          onClick={handleFitScreen}
        >
          ⤢ Fit Screen
        </Button>
        <Button
          size="sm"
          variant="outline"
          className="text-xs h-7 px-2"
          onClick={() => setZoom(z => Math.min(3.5, z * 1.2))}
        >
          +
        </Button>
        <Button
          size="sm"
          variant="outline"
          className="text-xs h-7 px-2"
          onClick={() => setZoom(z => Math.max(0.2, z * 0.8))}
        >
          -
        </Button>
      </div>

      {/* Hover Tooltip */}
      {hoveredNode && (
        <div
          className="absolute top-4 left-4 max-w-xs bg-card/90 backdrop-blur-md border border-border p-3 rounded-lg shadow-xl pointer-events-none z-10 space-y-1 animate-in fade-in duration-150"
        >
          <div className="flex items-center justify-between gap-2">
            <span className="font-semibold text-sm truncate">{hoveredNode.label}</span>
            <span
              className="text-[10px] uppercase font-bold px-1.5 py-0.5 rounded text-white"
              style={{ backgroundColor: CATEGORY_COLORS[hoveredNode.category]?.fill || '#8b5cf6' }}
            >
              {hoveredNode.category}
            </span>
          </div>
          <div className="text-xs text-muted-foreground">
            <span>ID: <code className="text-primary">{hoveredNode.id}</code></span>
          </div>
          <div className="text-xs text-muted-foreground flex gap-2">
            <span>Connections: {hoveredNode.degree}</span>
            {hoveredNode.confidence !== undefined && (
              <span>• Confidence: {Math.round(hoveredNode.confidence * 100)}%</span>
            )}
          </div>
        </div>
      )}
    </div>
  )
}

function hexToRgb(hex: string): string {
  const clean = hex.replace('#', '')
  if (clean.length === 3) {
    const r = parseInt(clean[0] + clean[0], 16)
    const g = parseInt(clean[1] + clean[1], 16)
    const b = parseInt(clean[2] + clean[2], 16)
    return `${r}, ${g}, ${b}`
  }
  const r = parseInt(clean.substring(0, 2), 16) || 140
  const g = parseInt(clean.substring(2, 4), 16) || 140
  const b = parseInt(clean.substring(4, 6), 16) || 140
  return `${r}, ${g}, ${b}`
}
