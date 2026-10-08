// Canvas painter for the 3D Neural / Obsidian Graph Visualization.
// Renders 3D spherical nodes, radiant blooms for hubs, glowing connective filaments,
// volumetric depth, and interactive hover / focus states.

import { nodeRadius } from './force'
import { getNodeColorSet, rgba } from './palette'
import type { GLink, GNode, GraphPalette, Viewport } from './types'

export interface DrawInput {
  adjacency: Map<string, Set<string>>
  births: Map<string, number>
  ctx: CanvasRenderingContext2D
  dpr: number
  hoverId: null | string
  links: GLink[]
  nodes: GNode[]
  now: number
  palette: GraphPalette
  query: string
  selectedId: null | string
  size: { h: number; w: number }
  vp: Viewport
}

const BIRTH_MS = 600

function endpoint(v: GNode | string, byId: Map<string, GNode>): GNode | null {
  return typeof v === 'string' ? (byId.get(v) ?? null) : v
}

const easeOut = (t: number): number => 1 - (1 - t) ** 3

export function drawGraph(input: DrawInput): boolean {
  const { adjacency, births, ctx, dpr, hoverId, links, nodes, now, palette, query, selectedId, size, vp } = input
  const { h, w } = size

  if (w <= 0 || h <= 0) return false

  const byId = new Map<string, GNode>()
  for (const n of nodes) {
    byId.set(n.id, n)
  }

  const focusId = hoverId ?? selectedId
  const focusSet = focusId ? (adjacency.get(focusId) ?? new Set<string>()) : null

  let animating = false

  ctx.setTransform(dpr, 0, 0, dpr, 0, 0)

  // 1. Deep Space Background with radial vignette
  const bgGrad = ctx.createRadialGradient(w / 2, h / 2, Math.min(w, h) * 0.1, w / 2, h / 2, Math.max(w, h) * 0.75)
  bgGrad.addColorStop(0, '#0c1020') // Deep subtle navy center
  bgGrad.addColorStop(0.5, '#070913')
  bgGrad.addColorStop(1, '#020306') // Jet dark vignette at edges
  ctx.fillStyle = bgGrad
  ctx.fillRect(0, 0, w, h)

  const sx = (wx: number) => wx * vp.k + vp.x + w / 2
  const sy = (wy: number) => wy * vp.k + vp.y + h / 2

  const bornOf = (id: string): number => {
    const start = births.get(id)
    if (start == null) return 1
    const t = (now - start) / BIRTH_MS
    if (t >= 1) {
      births.delete(id)
      return 1
    }
    animating = true
    return easeOut(t)
  }

  const isMatch = (n: GNode): boolean => (query ? n.label.toLowerCase().includes(query) : false)

  // 2. Render Outer Hub Glows (Pre-pass for cluster blooms)
  for (const n of nodes) {
    const degree = n.degree ?? 0
    if (degree < 3 && !n.pinned) continue

    const born = bornOf(n.id)
    if (born < 0.05) continue

    const X = sx(n.x)
    const Y = sy(n.y)
    const r = nodeRadius(n) * vp.k * (0.5 + 0.5 * born)

    // Cull off-screen blooms
    if (X < -150 || X > w + 150 || Y < -150 || Y > h + 150) continue

    const colorSet = getNodeColorSet(n)
    const bloomRadius = r * (degree >= 8 ? 6 : 4)
    const bloomAlpha = Math.min(0.35, 0.08 + degree * 0.02) * born

    const bloom = ctx.createRadialGradient(X, Y, 0, X, Y, bloomRadius)
    bloom.addColorStop(0, rgba(colorSet.glow, bloomAlpha))
    bloom.addColorStop(0.5, rgba(colorSet.glow, bloomAlpha * 0.35))
    bloom.addColorStop(1, rgba(colorSet.glow, 0))

    ctx.fillStyle = bloom
    ctx.beginPath()
    ctx.arc(X, Y, bloomRadius, 0, Math.PI * 2)
    ctx.fill()
  }

  // 3. Render Filament Edges
  for (const link of links) {
    const s = endpoint(link.source, byId)
    const t = endpoint(link.target, byId)
    if (!s || !t) continue

    const lit = !!focusId && (s.id === focusId || t.id === focusId || (!!focusSet && focusSet.has(s.id) && focusSet.has(t.id)))
    const born = Math.min(bornOf(s.id), bornOf(t.id))
    if (born < 0.05) continue

    const sX = sx(s.x)
    const sY = sy(s.y)
    const tX = sx(t.x)
    const tY = sy(t.y)

    // Quick viewport cull
    if (
      (sX < -50 && tX < -50) ||
      (sX > w + 50 && tX > w + 50) ||
      (sY < -50 && tY < -50) ||
      (sY > h + 50 && tY > h + 50)
    ) {
      continue
    }

    const sColors = getNodeColorSet(s)
    const tColors = getNodeColorSet(t)

    // Glowing gradient line
    const grad = ctx.createLinearGradient(sX, sY, tX, tY)
    const baseAlpha = lit ? 0.85 : focusId ? 0.04 : 0.22
    const edgeAlpha = baseAlpha * born

    grad.addColorStop(0, rgba(sColors.glow, edgeAlpha))
    grad.addColorStop(1, rgba(tColors.glow, edgeAlpha))

    ctx.strokeStyle = grad
    ctx.lineWidth = lit ? 1.8 : 0.85
    ctx.beginPath()
    ctx.moveTo(sX, sY)
    ctx.lineTo(tX, tY)
    ctx.stroke()
  }

  // 4. Render 3D Spherical Nodes
  const showAllLabels = vp.k >= 1.4
  const showFocusLabels = vp.k >= 0.7

  for (const n of nodes) {
    const born = bornOf(n.id)
    if (born < 0.02) continue

    const X = sx(n.x)
    const Y = sy(n.y)
    const r = Math.max(1.8, nodeRadius(n) * vp.k * (0.3 + 0.7 * born))

    // Cull off-screen nodes
    if (X < -80 || X > w + 80 || Y < -80 || Y > h + 80) continue

    const isFocus = n.id === focusId
    const isNeighbor = !!focusSet && focusSet.has(n.id)
    const isSelected = n.id === selectedId
    const matched = isMatch(n)

    let alpha = 1
    if (focusId) {
      alpha = isFocus || isNeighbor ? 1 : 0.12
    }
    if (query && !matched && !isFocus && !isNeighbor) {
      alpha *= 0.25
    }

    const colorSet = getNodeColorSet(n)

    // Birth Halo Effect
    if (born < 1) {
      ctx.globalAlpha = (1 - born) * 0.6 * alpha
      ctx.strokeStyle = rgba(colorSet.highlight, 1)
      ctx.lineWidth = 1.5
      ctx.beginPath()
      ctx.arc(X, Y, r + (1 - born) * 20, 0, Math.PI * 2)
      ctx.stroke()
    }

    if (n.kind === 'ghost') {
      // Hollow dashed sphere for unresolved wikilinks
      ctx.globalAlpha = alpha * 0.65
      ctx.strokeStyle = rgba(colorSet.glow, 0.9)
      ctx.lineWidth = 1.2
      ctx.setLineDash([2, 3])
      ctx.beginPath()
      ctx.arc(X, Y, r, 0, Math.PI * 2)
      ctx.stroke()
      ctx.setLineDash([])
    } else {
      // 3D Spherical Orb Gradient
      ctx.globalAlpha = alpha
      // Off-center highlight (top-left) for 3D sphere illusion
      const sphereGrad = ctx.createRadialGradient(
        X - r * 0.35,
        Y - r * 0.35,
        r * 0.05,
        X,
        Y,
        r
      )
      sphereGrad.addColorStop(0, rgba(colorSet.highlight, 1))
      sphereGrad.addColorStop(0.35, rgba(colorSet.glow, 0.95))
      sphereGrad.addColorStop(0.85, rgba(colorSet.base, 0.9))
      sphereGrad.addColorStop(1, rgba({ r: Math.round(colorSet.base.r * 0.4), g: Math.round(colorSet.base.g * 0.4), b: Math.round(colorSet.base.b * 0.4) }, 1))

      ctx.fillStyle = sphereGrad
      ctx.beginPath()
      ctx.arc(X, Y, r, 0, Math.PI * 2)
      ctx.fill()
    }

    // Outer Selection / Focus Ring
    if (isSelected || isFocus) {
      ctx.globalAlpha = 1
      ctx.strokeStyle = rgba(colorSet.highlight, 0.95)
      ctx.lineWidth = 1.6
      ctx.beginPath()
      ctx.arc(X, Y, r + 4, 0, Math.PI * 2)
      ctx.stroke()

      // Subtle pulse halo
      ctx.globalAlpha = 0.25
      ctx.fillStyle = rgba(colorSet.glow, 0.3)
      ctx.beginPath()
      ctx.arc(X, Y, r + 7, 0, Math.PI * 2)
      ctx.fill()
    }

    // Node Labels
    const wantLabel = (showAllLabels || (showFocusLabels && (isFocus || isNeighbor || matched))) && (alpha > 0.25 || matched)

    if (wantLabel) {
      ctx.globalAlpha = Math.min(1, alpha + 0.2) * (showAllLabels ? 0.95 : 1)
      ctx.font = `${isFocus ? '600 ' : '500 '} ${Math.round(11 * Math.max(1, Math.min(1.5, vp.k)))}px "Inter", -apple-system, system-ui, sans-serif`
      ctx.textAlign = 'center'
      ctx.textBaseline = 'top'

      const label = n.label.length > 36 ? `${n.label.slice(0, 35)}…` : n.label
      
      // Crisp text shadow for readability over dense nodes
      ctx.shadowColor = 'rgba(2, 4, 10, 0.9)'
      ctx.shadowBlur = 4
      ctx.shadowOffsetX = 0
      ctx.shadowOffsetY = 1

      ctx.fillStyle = isFocus ? rgba(colorSet.highlight, 1) : rgba(palette.fg, 0.92)
      ctx.fillText(label, X, Y + r + 4)

      ctx.shadowColor = 'transparent'
      ctx.shadowBlur = 0
    }
  }

  ctx.globalAlpha = 1
  return animating
}

/** Minimap painter for overview HUD */
export function drawMinimap(
  ctx: CanvasRenderingContext2D,
  w: number,
  h: number,
  nodes: GNode[],
  vp: Viewport,
  mainSize: { w: number; h: number }
) {
  if (w <= 0 || h <= 0 || !nodes.length) return

  ctx.clearRect(0, 0, w, h)
  ctx.fillStyle = 'rgba(6, 10, 20, 0.85)'
  ctx.fillRect(0, 0, w, h)

  let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity
  for (const n of nodes) {
    minX = Math.min(minX, n.x)
    minY = Math.min(minY, n.y)
    maxX = Math.max(maxX, n.x)
    maxY = Math.max(maxY, n.y)
  }

  const spanX = Math.max(100, maxX - minX + 80)
  const spanY = Math.max(100, maxY - minY + 80)
  const cx = (minX + maxX) / 2
  const cy = (minY + maxY) / 2

  const scale = Math.min((w - 12) / spanX, (h - 12) / spanY)

  const toMiniX = (wx: number) => (wx - cx) * scale + w / 2
  const toMiniY = (wy: number) => (wy - cy) * scale + h / 2

  // Draw node points
  for (const n of nodes) {
    const colorSet = getNodeColorSet(n)
    ctx.fillStyle = rgba(colorSet.base, 0.8)
    ctx.beginPath()
    ctx.arc(toMiniX(n.x), toMiniY(n.y), Math.max(1.2, nodeRadius(n) * scale * 0.8), 0, Math.PI * 2)
    ctx.fill()
  }

  // Draw Viewport Box
  const viewLeft = (-vp.x - mainSize.w / 2) / vp.k
  const viewTop = (-vp.y - mainSize.h / 2) / vp.k
  const viewRight = viewLeft + mainSize.w / vp.k
  const viewBottom = viewTop + mainSize.h / vp.k

  const vx1 = toMiniX(viewLeft)
  const vy1 = toMiniY(viewTop)
  const vx2 = toMiniX(viewRight)
  const vy2 = toMiniY(viewBottom)

  ctx.strokeStyle = 'rgba(0, 210, 255, 0.8)'
  ctx.lineWidth = 1
  ctx.strokeRect(vx1, vy1, vx2 - vx1, vy2 - vy1)
  ctx.fillStyle = 'rgba(0, 210, 255, 0.08)'
  ctx.fillRect(vx1, vy1, vx2 - vx1, vy2 - vy1)
}
