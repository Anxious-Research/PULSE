import type { Simulation } from 'd3-force'
import {
  Activity,
  Compass,
  Minus,
  Plus,
  RotateCcw,
  Search,
  Sliders,
  Sparkles
} from 'lucide-react'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'

import { Tip } from '@/components/ui/tooltip'
import { useThemeEpoch } from '@/hooks/use-theme-epoch'
import { createRendererLoopPauseController } from '@/lib/renderer-loop-pause'
import { cn } from '@/lib/utils'
import type { StarmapGraph } from '@/types/pulse'

import { registerStarMapContextMenu } from '../starmap/context-menu-handle'
import { NodeContextMenu, type NodeMenuTarget } from '../starmap/node-context-menu'

import { applyForces, bounds, buildGraph, nodeRadius, warmUp } from './force'
import { CATEGORY_COLORS, computePalette, resolveRgb, rgba } from './palette'
import { drawGraph, drawMinimap } from './render'
import { DEFAULT_FORCES, type ForceParams, type GLink, type GNode, type GraphPalette, type Viewport } from './types'

const ZOOM_MIN = 0.1
const ZOOM_MAX = 6.0

function clamp(v: number, lo: number, hi: number): number {
  return Math.max(lo, Math.min(hi, v))
}

function Slider({
  label,
  max,
  min,
  onChange,
  step,
  value
}: {
  label: string
  max: number
  min: number
  onChange: (v: number) => void
  step: number
  value: number
}) {
  return (
    <label className="flex items-center justify-between gap-2 text-[0.68rem]">
      <span className="w-24 shrink-0 text-slate-400">{label}</span>
      <input
        className="h-1 flex-1 cursor-pointer appearance-none rounded-full bg-slate-800 accent-cyan-400"
        max={max}
        min={min}
        onChange={e => onChange(Number(e.target.value))}
        step={step}
        type="range"
        value={value}
      />
    </label>
  )
}

export function BrainGraph({
  graph,
  onRefresh
}: {
  graph: StarmapGraph
  onRefresh?: () => void
}) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null)
  const minimapCanvasRef = useRef<HTMLCanvasElement | null>(null)
  const wrapRef = useRef<HTMLDivElement | null>(null)
  const searchInputRef = useRef<HTMLInputElement | null>(null)

  const simRef = useRef<null | Simulation<GNode, GLink>>(null)
  const nodesRef = useRef<GNode[]>([])
  const linksRef = useRef<GLink[]>([])
  const byIdRef = useRef(new Map<string, GNode>())
  const adjacencyRef = useRef(new Map<string, Set<string>>())
  const posRef = useRef(new Map<string, { x: number; y: number }>())
  const birthsRef = useRef(new Map<string, number>())
  const seenRef = useRef(new Set<string>())

  const vpRef = useRef<Viewport>({ k: 1, x: 0, y: 0 })
  const hoverRef = useRef<null | string>(null)
  const selectedIdRef = useRef<null | string>(null)
  const queryRef = useRef('')
  const paletteRef = useRef<null | GraphPalette>(null)
  const themeDirtyRef = useRef(true)
  const sizeRef = useRef({ h: 0, w: 0 })
  const dprRef = useRef(1)
  const dirtyRef = useRef(true)
  const invalidateRef = useRef<() => void>(() => {})
  const paramsRef = useRef<ForceParams>(DEFAULT_FORCES)
  const sigRef = useRef('')

  const dragRef = useRef<{
    id: null | string
    mode: 'none' | 'node' | 'pan'
    moved: boolean
    sx: number
    sy: number
    vp: Viewport
  }>({ id: null, mode: 'none', moved: false, sx: 0, sy: 0, vp: { k: 1, x: 0, y: 0 } })

  const [selectedId, setSelectedId] = useState<null | string>(null)
  const [menuTarget, setMenuTarget] = useState<NodeMenuTarget | null>(null)
  const [size, setSize] = useState({ h: 0, w: 0 })
  const [params, setParams] = useState<ForceParams>(DEFAULT_FORCES)
  const [showForcesPanel, setShowForcesPanel] = useState(false)
  const [query, setQuery] = useState('')
  const [hiddenCategories, setHiddenCategories] = useState<Set<string>>(new Set())
  const themeEpoch = useThemeEpoch()

  const invalidate = useCallback(() => invalidateRef.current(), [])

  // Keyboard shortcut: ⌘ K to focus search
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key === 'k') {
        e.preventDefault()
        searchInputRef.current?.focus()
      }
    }

    window.addEventListener('keydown', handleKeyDown)

    return () => window.removeEventListener('keydown', handleKeyDown)
  }, [])

  const legendCategories = useMemo(() => {
    const present = new Set<string>()

    for (const node of graph.nodes) {
      if (node.kind === 'memory' && node.category) {
        present.add(node.category)
      }
    }

    return [...present].sort()
  }, [graph])

  useEffect(() => {
    if (hiddenCategories.size === 0) {return}
    const present = new Set(legendCategories)
    setHiddenCategories(prev => {
      const stale = [...prev].filter(c => !present.has(c))

      return stale.length ? new Set([...prev].filter(c => present.has(c))) : prev
    })
  }, [legendCategories, hiddenCategories.size])

  const toggleCategory = useCallback((category: string) => {
    setHiddenCategories(prev => {
      const next = new Set(prev)
      next.has(category) ? next.delete(category) : next.add(category)

      return next
    })
  }, [])

  const visibleGraph = useMemo(() => {
    if (hiddenCategories.size === 0) {return graph}

    const hidden = new Set(
      graph.nodes.filter(n => n.kind === 'memory' && n.category && hiddenCategories.has(n.category)).map(n => n.id)
    )

    if (hidden.size === 0) {return graph}

    return {
      ...graph,
      edges: graph.edges.filter(e => !hidden.has(e.source) && !hidden.has(e.target)),
      nodes: graph.nodes.filter(n => !hidden.has(n.id))
    }
  }, [graph, hiddenCategories])

  const clusterCount = useMemo(() => {
    if (graph.clusters && graph.clusters.length > 0) {return graph.clusters.length}

    return legendCategories.length || 1
  }, [graph, legendCategories])

  useEffect(() => {
    const el = wrapRef.current

    if (!el) {return}
    const sync = () => setSize({ h: el.clientHeight, w: el.clientWidth })
    const ro = new ResizeObserver(sync)
    ro.observe(el)
    sync()

    return () => ro.disconnect()
  }, [])

  const fitView = useCallback(
    (animate = false) => {
      const { h, w } = sizeRef.current
      const nodes = nodesRef.current

      if (w <= 0 || h <= 0 || !nodes.length) {return}

      const b = bounds(nodes)
      const bw = Math.max(50, b.maxX - b.minX)
      const bh = Math.max(50, b.maxY - b.minY)
      const cx = (b.minX + b.maxX) / 2
      const cy = (b.minY + b.maxY) / 2
      const k = clamp(Math.min((w - 120) / bw, (h - 120) / bh), ZOOM_MIN, 1.8)
      const target: Viewport = { k, x: -cx * k, y: -cy * k }

      if (animate) {
        const from = { ...vpRef.current }
        const start = performance.now()

        const step = (t: number) => {
          const p = Math.min(1, (t - start) / 350)
          const e = 1 - (1 - p) ** 3
          vpRef.current = {
            k: from.k + (target.k - from.k) * e,
            x: from.x + (target.x - from.x) * e,
            y: from.y + (target.y - from.y) * e
          }
          invalidate()

          if (p < 1) {requestAnimationFrame(step)}
        }

        requestAnimationFrame(step)

        return
      }

      vpRef.current = target
      invalidate()
    },
    [invalidate]
  )

  const zoomBy = useCallback(
    (factor: number) => {
      const { h, w } = sizeRef.current
      const vp = vpRef.current
      const newK = clamp(vp.k * factor, ZOOM_MIN, ZOOM_MAX)
      const cx = w / 2
      const cy = h / 2
      const wx = (cx - w / 2 - vp.x) / vp.k
      const wy = (cy - h / 2 - vp.y) / vp.k

      vpRef.current = { k: newK, x: cx - w / 2 - wx * newK, y: cy - h / 2 - wy * newK }
      invalidate()
    },
    [invalidate]
  )

  useEffect(() => {
    sizeRef.current = size

    if (size.w === 0 || size.h === 0) {return}

    const signature = `${visibleGraph.nodes.length}:${visibleGraph.nodes.map(n => n.id).join(',')}`

    if (signature === sigRef.current && simRef.current) {return}

    const firstBuild = sigRef.current === ''
    sigRef.current = signature
    simRef.current?.stop()

    const { byId, links, nodes, sim } = buildGraph(visibleGraph, posRef.current, invalidate)
    const now = performance.now()

    if (!firstBuild) {
      for (const n of nodes) {
        if (!seenRef.current.has(n.id)) {
          birthsRef.current.set(n.id, now)
        }
      }
    }

    for (const n of nodes) {
      seenRef.current.add(n.id)
    }

    const adjacency = new Map<string, Set<string>>()

    for (const id of byId.keys()) {
      adjacency.set(id, new Set())
    }

    for (const e of visibleGraph.edges) {
      adjacency.get(e.source)?.add(e.target)
      adjacency.get(e.target)?.add(e.source)
    }

    simRef.current = sim
    nodesRef.current = nodes
    linksRef.current = links
    byIdRef.current = byId
    adjacencyRef.current = adjacency

    warmUp(sim)

    for (const n of nodes) {
      posRef.current.set(n.id, { x: n.x, y: n.y })
    }

    sim.on('tick', () => {
      for (const n of nodes) {
        posRef.current.set(n.id, { x: n.x, y: n.y })
      }

      invalidate()
    })

    if (firstBuild) {
      fitView(false)
    } else {
      sim.alpha(0.5).restart()
      invalidate()
    }

    if (selectedIdRef.current && !byId.has(selectedIdRef.current)) {
      selectedIdRef.current = null
      setSelectedId(null)
    }

    return () => {
      sim.stop()

      if (simRef.current === sim) {
        simRef.current = null
      }
    }
  }, [visibleGraph, size, invalidate, fitView])

  const toWorld = useCallback((cssX: number, cssY: number): { x: number; y: number } => {
    const { h, w } = sizeRef.current
    const vp = vpRef.current

    return { x: (cssX - w / 2 - vp.x) / vp.k, y: (cssY - h / 2 - vp.y) / vp.k }
  }, [])

  const pickNode = useCallback((cssX: number, cssY: number): null | GNode => {
    const { h, w } = sizeRef.current
    const vp = vpRef.current
    let best: null | GNode = null
    let bestD = Infinity

    for (const n of nodesRef.current) {
      const X = n.x * vp.k + vp.x + w / 2
      const Y = n.y * vp.k + vp.y + h / 2
      const r = nodeRadius(n) * vp.k + 8
      const d = (X - cssX) ** 2 + (Y - cssY) ** 2

      if (d < r * r && d < bestD) {
        bestD = d
        best = n
      }
    }

    return best
  }, [])

  const localXY = (e: React.MouseEvent): { x: number; y: number } => {
    const rect = canvasRef.current?.getBoundingClientRect()

    return { x: e.clientX - (rect?.left ?? 0), y: e.clientY - (rect?.top ?? 0) }
  }

  const openNodeMenuAt = useCallback(
    (clientX: number, clientY: number): boolean => {
      const rect = canvasRef.current?.getBoundingClientRect()
      const node = pickNode(clientX - (rect?.left ?? 0), clientY - (rect?.top ?? 0))

      if (!node) {
        setMenuTarget(null)

        return false
      }

      selectedIdRef.current = node.id
      setSelectedId(node.id)
      setMenuTarget({ id: node.id, kind: node.kind, label: node.label, x: clientX, y: clientY })

      return true
    },
    [pickNode]
  )

  useEffect(() => {
    const canvas = canvasRef.current

    return canvas ? registerStarMapContextMenu(canvas, { openNodeMenuAt }) : undefined
  }, [openNodeMenuAt])

  const onMouseDown = (e: React.MouseEvent<HTMLCanvasElement>) => {
    if (e.button !== 0) {return}
    const { x, y } = localXY(e)
    const node = pickNode(x, y)
    const sim = simRef.current

    if (node && sim) {
      node.fx = node.x
      node.fy = node.y
      sim.alphaTarget(0.25).restart()
    }

    dragRef.current = {
      id: node?.id ?? null,
      mode: node ? 'node' : 'pan',
      moved: false,
      sx: e.clientX,
      sy: e.clientY,
      vp: vpRef.current
    }
  }

  const onMouseMove = (e: React.MouseEvent<HTMLCanvasElement>) => {
    const drag = dragRef.current

    if (drag.mode === 'none') {
      const { x, y } = localXY(e)
      const id = pickNode(x, y)?.id ?? null

      if (id !== hoverRef.current) {
        hoverRef.current = id
        invalidate()
      }

      if (canvasRef.current) {
        canvasRef.current.style.cursor = id ? 'pointer' : 'default'
      }

      return
    }

    const dx = e.clientX - drag.sx
    const dy = e.clientY - drag.sy

    if (Math.abs(dx) > 3 || Math.abs(dy) > 3) {
      drag.moved = true
    }

    if (drag.mode === 'node' && drag.id) {
      const node = byIdRef.current.get(drag.id)

      if (node) {
        const rect = canvasRef.current?.getBoundingClientRect()
        const world = toWorld(e.clientX - (rect?.left ?? 0), e.clientY - (rect?.top ?? 0))
        node.fx = world.x
        node.fy = world.y
        invalidate()
      }

      return
    }

    vpRef.current = { ...drag.vp, x: drag.vp.x + dx, y: drag.vp.y + dy }
    invalidate()
  }

  const endDrag = () => {
    const drag = dragRef.current
    const sim = simRef.current

    if (drag.mode === 'node' && drag.id) {
      const node = byIdRef.current.get(drag.id)

      if (node && !drag.moved) {
        node.fx = null
        node.fy = null
        selectedIdRef.current = selectedIdRef.current === drag.id ? null : drag.id
        setSelectedId(selectedIdRef.current)
      } else if (node) {
        node.fx = node.x
        node.fy = node.y
      }

      sim?.alphaTarget(0)
    } else if (drag.mode === 'pan' && !drag.moved) {
      selectedIdRef.current = null
      setSelectedId(null)
    }

    dragRef.current = { id: null, mode: 'none', moved: false, sx: 0, sy: 0, vp: vpRef.current }
    invalidate()
  }

  const onMouseLeave = () => {
    hoverRef.current = null
    invalidate()
    endDrag()
  }

  const onWheel = (e: React.WheelEvent<HTMLCanvasElement>) => {
    const rect = canvasRef.current?.getBoundingClientRect()

    if (!rect) {return}

    const { h, w } = sizeRef.current
    const px = e.clientX - rect.left
    const py = e.clientY - rect.top
    const vp = vpRef.current
    const k = clamp(vp.k * (e.deltaY > 0 ? 0.88 : 1.14), ZOOM_MIN, ZOOM_MAX)

    const wx = (px - w / 2 - vp.x) / vp.k
    const wy = (py - h / 2 - vp.y) / vp.k

    vpRef.current = { k, x: px - w / 2 - wx * k, y: py - h / 2 - wy * k }
    invalidate()
  }

  const onDoubleClick = (e: React.MouseEvent<HTMLCanvasElement>) => {
    const { x, y } = localXY(e)

    if (pickNode(x, y)) {
      openNodeMenuAt(e.clientX, e.clientY)
    } else {
      fitView(true)
    }
  }

  const onContextMenu = (e: React.MouseEvent<HTMLCanvasElement>) => {
    if (openNodeMenuAt(e.clientX, e.clientY)) {
      e.preventDefault()
    }
  }

  const updateParam = useCallback(
    (key: keyof ForceParams, value: number) => {
      setParams(prev => {
        const next = { ...prev, [key]: value }
        paramsRef.current = next
        const sim = simRef.current

        if (sim) {
          applyForces(sim, linksRef.current, next)
          sim.alpha(0.6).restart()
          invalidate()
        }

        return next
      })
    },
    [invalidate]
  )

  useEffect(() => {
    sizeRef.current = size
    dprRef.current = Math.min(2, window.devicePixelRatio || 1)
    const canvas = canvasRef.current

    if (canvas && size.w > 0 && size.h > 0) {
      canvas.width = Math.round(size.w * dprRef.current)
      canvas.height = Math.round(size.h * dprRef.current)
      canvas.style.width = `${size.w}px`
      canvas.style.height = `${size.h}px`
    }

    invalidate()
  }, [invalidate, size])

  useEffect(() => {
    themeDirtyRef.current = true
    invalidate()
  }, [invalidate, themeEpoch])

  useEffect(() => {
    selectedIdRef.current = selectedId
    invalidate()
  }, [invalidate, selectedId])

  useEffect(() => {
    queryRef.current = query.trim().toLowerCase()
    invalidate()
  }, [invalidate, query])

  useEffect(() => {
    let raf = 0
    let paused = false
    let pauseController: ReturnType<typeof createRendererLoopPauseController>

    const schedule = () => {
      if (!paused && !raf) {
        raf = requestAnimationFrame(frame)
      }
    }

    const paint = () => {
      const canvas = canvasRef.current
      const ctx = canvas?.getContext('2d')

      if (!canvas || !ctx) {return false}

      if (themeDirtyRef.current || !paletteRef.current) {
        paletteRef.current = computePalette(canvas)
        themeDirtyRef.current = false
      }

      const palette = paletteRef.current

      if (!palette) {return false}

      const animating = drawGraph({
        adjacency: adjacencyRef.current,
        births: birthsRef.current,
        ctx,
        dpr: dprRef.current,
        hoverId: hoverRef.current,
        links: linksRef.current,
        nodes: nodesRef.current,
        now: performance.now(),
        palette,
        query: queryRef.current,
        selectedId: selectedIdRef.current,
        size: sizeRef.current,
        vp: vpRef.current
      })

      const miniCanvas = minimapCanvasRef.current
      const miniCtx = miniCanvas?.getContext('2d')

      if (miniCanvas && miniCtx) {
        drawMinimap(miniCtx, miniCanvas.width, miniCanvas.height, nodesRef.current, vpRef.current, sizeRef.current)
      }

      return animating
    }

    const frame = () => {
      raf = 0
      const animating = paint()
      dirtyRef.current = animating

      if (dirtyRef.current) {
        schedule()
      }
    }

    invalidateRef.current = () => {
      dirtyRef.current = true
      schedule()
    }

    const onActivity = () => {
      const next = pauseController.isPaused()

      if (next === paused) {return}
      paused = next

      if (paused) {
        if (raf) {
          cancelAnimationFrame(raf)
          raf = 0
        }
      } else {
        dirtyRef.current = true
        schedule()
      }
    }

    pauseController = createRendererLoopPauseController(onActivity)
    paused = pauseController.isPaused()
    schedule()

    return () => {
      cancelAnimationFrame(raf)
      pauseController.dispose()

      invalidateRef.current = () => {}
    }
  }, [])

  const nodeCount = graph.nodes.length
  const edgeCount = graph.edges.length

  return (
    <div className="relative flex h-full w-full min-h-0 flex-1 overflow-hidden bg-[#020307] select-none font-sans" ref={wrapRef}>
      <canvas
        className="block touch-none select-none text-foreground"
        onContextMenu={onContextMenu}
        onDoubleClick={onDoubleClick}
        onMouseDown={onMouseDown}
        onMouseLeave={onMouseLeave}
        onMouseMove={onMouseMove}
        onMouseUp={endDrag}
        onWheel={onWheel}
        ref={canvasRef}
      />

      <NodeContextMenu
        onClose={() => setMenuTarget(null)}
        onNodeRemoved={() => {
          setMenuTarget(null)
          selectedIdRef.current = null
          setSelectedId(null)
        }}
        target={menuTarget}
      />

      {/* TOP BAR */}
      <div className="pointer-events-auto absolute top-3 left-1/2 -translate-x-1/2 z-20 flex items-center gap-2 rounded-full border border-white/10 bg-[#0c111e]/75 px-3 py-1.5 shadow-[0_8px_32px_rgba(0,0,0,0.6)] backdrop-blur-xl [-webkit-app-region:no-drag]">
        <div className="flex h-7 w-7 items-center justify-center rounded-full bg-cyan-500/15 text-cyan-400">
          <Sparkles className="h-3.5 w-3.5" />
        </div>

        <div className="relative flex items-center">
          <Search className="absolute left-2.5 h-3.5 w-3.5 text-slate-400" />
          <input
            className="w-56 rounded-full border border-white/10 bg-slate-900/60 pl-8 pr-12 py-1 text-xs text-slate-200 outline-none placeholder:text-slate-500 focus:border-cyan-500/50 focus:ring-1 focus:ring-cyan-500/30 transition-all"
            onChange={e => setQuery(e.target.value)}
            placeholder="Search memories…"
            ref={searchInputRef}
            value={query}
          />
          <kbd className="pointer-events-none absolute right-2 rounded border border-white/10 bg-white/5 px-1.5 py-0.5 text-[0.6rem] font-medium text-slate-400">
            ⌘K
          </kbd>
        </div>

        <div className="h-4 w-[1px] bg-white/10 mx-0.5" />

        <Tip label="Force Parameters">
          <button
            aria-label="Force Parameters"
            className={cn(
              'flex h-7 items-center gap-1.5 rounded-full px-2.5 text-xs transition-colors',
              showForcesPanel ? 'bg-cyan-500/20 text-cyan-300' : 'text-slate-400 hover:bg-white/5 hover:text-slate-200'
            )}
            onClick={() => setShowForcesPanel(v => !v)}
            type="button"
          >
            <Sliders className="h-3.5 w-3.5" />
            <span className="text-[0.7rem] font-medium">Forces</span>
          </button>
        </Tip>

        {onRefresh && (
          <Tip label="Refresh Vault Graph">
            <button
              aria-label="Refresh Vault Graph"
              className="flex h-7 w-7 items-center justify-center rounded-full text-slate-400 hover:bg-white/5 hover:text-slate-200 transition-colors"
              onClick={onRefresh}
              type="button"
            >
              <RotateCcw className="h-3.5 w-3.5" />
            </button>
          </Tip>
        )}
      </div>

      {/* FORCES POPUP */}
      {showForcesPanel && (
        <div className="pointer-events-auto absolute top-14 left-1/2 -translate-x-1/2 z-20 flex w-72 flex-col gap-2.5 rounded-2xl border border-white/10 bg-[#0c111e]/90 p-3.5 shadow-[0_16px_40px_rgba(0,0,0,0.7)] backdrop-blur-xl [-webkit-app-region:no-drag]">
          <div className="flex items-center justify-between border-b border-white/10 pb-2">
            <span className="text-xs font-semibold text-slate-200">Force Simulation</span>
            <button
              className="text-[0.65rem] text-cyan-400 hover:underline"
              onClick={() => {
                setParams(DEFAULT_FORCES)
                paramsRef.current = DEFAULT_FORCES
                const sim = simRef.current

                if (sim) {
                  applyForces(sim, linksRef.current, DEFAULT_FORCES)
                  sim.alpha(0.7).restart()
                  invalidate()
                }
              }}
              type="button"
            >
              Reset
            </button>
          </div>
          <div className="flex flex-col gap-2">
            <Slider label="Center force" max={0.3} min={0} onChange={v => updateParam('center', v)} step={0.01} value={params.center} />
            <Slider label="Repulsion" max={600} min={20} onChange={v => updateParam('repel', v)} step={10} value={params.repel} />
            <Slider label="Link strength" max={1} min={0} onChange={v => updateParam('linkStrength', v)} step={0.05} value={params.linkStrength} />
            <Slider label="Link distance" max={260} min={20} onChange={v => updateParam('linkDistance', v)} step={4} value={params.linkDistance} />
          </div>
        </div>
      )}

      {/* BOTTOM-LEFT METRICS HUD */}
      <div className="pointer-events-auto absolute bottom-4 left-4 z-20 flex flex-col gap-2.5 rounded-2xl border border-white/10 bg-[#0c111e]/80 p-3.5 shadow-[0_12px_36px_rgba(0,0,0,0.6)] backdrop-blur-xl min-w-[165px] [-webkit-app-region:no-drag]">
        <div className="flex flex-col gap-2 text-[0.72rem]">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-1.5 text-slate-400">
              <span className="h-1.5 w-1.5 rounded-full bg-slate-500" />
              <span>Memories</span>
            </div>
            <span className="font-semibold text-slate-200 font-mono">{nodeCount.toLocaleString()}</span>
          </div>

          <div className="flex items-center justify-between">
            <div className="flex items-center gap-1.5 text-slate-400">
              <span className="h-1.5 w-1.5 rounded-full bg-cyan-400" />
              <span>Connections</span>
            </div>
            <span className="font-semibold text-slate-200 font-mono">{edgeCount.toLocaleString()}</span>
          </div>

          <div className="flex items-center justify-between">
            <div className="flex items-center gap-1.5 text-slate-400">
              <span className="h-1.5 w-1.5 rounded-full bg-purple-400" />
              <span>Clusters</span>
            </div>
            <span className="font-semibold text-slate-200 font-mono">{clusterCount}</span>
          </div>
        </div>

        <div className="flex items-center justify-between border-t border-white/10 pt-2 text-[0.68rem]">
          <div className="flex items-center gap-1.5 text-emerald-400 font-medium">
            <Activity className="h-3 w-3 animate-pulse" />
            <span>∿ Live</span>
          </div>
          <span className="text-[0.6rem] text-slate-500">Autonomous</span>
        </div>

        <div className="flex flex-wrap gap-1 border-t border-white/10 pt-2">
          {legendCategories.map(cat => {
            const colors = CATEGORY_COLORS[cat] ?? CATEGORY_COLORS.concept
            const isHidden = hiddenCategories.has(cat)

            return (
              <button
                className={cn(
                  'flex items-center gap-1 rounded-full border px-2 py-0.5 text-[0.62rem] transition-all',
                  isHidden
                    ? 'border-white/5 bg-transparent text-slate-500 opacity-40'
                    : 'border-white/10 bg-white/5 text-slate-300 hover:border-white/20 hover:text-white'
                )}
                key={cat}
                onClick={() => toggleCategory(cat)}
                type="button"
              >
                <span
                  className="h-1.5 w-1.5 rounded-full"
                  style={{ backgroundColor: isHidden ? '#64748b' : rgba(colors.glow, 1) }}
                />
                <span className={cn(isHidden && 'line-through')}>{cat}</span>
              </button>
            )
          })}
        </div>
      </div>

      {/* BOTTOM-RIGHT MINIMAP & CONTROLS */}
      <div className="pointer-events-auto absolute bottom-4 right-4 z-20 flex items-end gap-2.5 [-webkit-app-region:no-drag]">
        <div className="relative h-28 w-40 overflow-hidden rounded-xl border border-white/10 bg-[#0c111e]/85 shadow-[0_8px_32px_rgba(0,0,0,0.6)] backdrop-blur-xl">
          <canvas
            className="h-full w-full"
            height={112}
            ref={minimapCanvasRef}
            width={160}
          />
          <span className="absolute bottom-1 right-2 text-[0.55rem] font-medium text-slate-500 uppercase tracking-wider">
            Minimap
          </span>
        </div>

        <div className="flex flex-col items-center rounded-2xl border border-white/10 bg-[#0c111e]/85 p-1 shadow-[0_8px_32px_rgba(0,0,0,0.6)] backdrop-blur-xl">
          <Tip label="Recenter Camera">
            <button
              aria-label="Recenter Camera"
              className="flex h-7 w-7 items-center justify-center rounded-xl text-slate-400 hover:bg-white/10 hover:text-cyan-300 transition-colors"
              onClick={() => fitView(true)}
              type="button"
            >
              <Compass className="h-3.5 w-3.5" />
            </button>
          </Tip>
          <div className="my-1 h-[1px] w-4 bg-white/10" />
          <Tip label="Zoom In">
            <button
              aria-label="Zoom In"
              className="flex h-7 w-7 items-center justify-center rounded-xl text-slate-400 hover:bg-white/10 hover:text-cyan-300 transition-colors"
              onClick={() => zoomBy(1.25)}
              type="button"
            >
              <Plus className="h-3.5 w-3.5" />
            </button>
          </Tip>
          <Tip label="Zoom Out">
            <button
              aria-label="Zoom Out"
              className="flex h-7 w-7 items-center justify-center rounded-xl text-slate-400 hover:bg-white/10 hover:text-cyan-300 transition-colors"
              onClick={() => zoomBy(0.8)}
              type="button"
            >
              <Minus className="h-3.5 w-3.5" />
            </button>
          </Tip>
        </div>
      </div>
    </div>
  )
}

export { resolveRgb }
