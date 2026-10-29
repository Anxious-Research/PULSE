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
  motionEnabled: boolean
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

interface Point {
  x: number
  y: number
}

function convexHull(points: Point[]): Point[] {
  const sorted = [...points].sort((a, b) => a.x - b.x || a.y - b.y)

  if (sorted.length < 3) {
    return sorted
  }

  const cross = (o: Point, a: Point, b: Point) => (a.x - o.x) * (b.y - o.y) - (a.y - o.y) * (b.x - o.x)
  const lower: Point[] = []
  const upper: Point[] = []

  for (const point of sorted) {
    while (lower.length >= 2 && cross(lower[lower.length - 2], lower[lower.length - 1], point) <= 0) {
      lower.pop()
    }

    lower.push(point)
  }

  for (const point of [...sorted].reverse()) {
    while (upper.length >= 2 && cross(upper[upper.length - 2], upper[upper.length - 1], point) <= 0) {
      upper.pop()
    }

    upper.push(point)
  }

  lower.pop()
  upper.pop()

  return [...lower, ...upper]
}

function drawCategoryClouds(
  ctx: CanvasRenderingContext2D,
  nodes: GNode[],
  toScreen: (x: number, y: number) => Point,
  palette: GraphPalette,
  zoom: number,
  width: number,
  height: number,
  focusId: null | string,
  focusSet: null | Set<string>
): void {
  const groups = new Map<string, GNode[]>()

  for (const node of nodes) {
    if (node.kind !== 'memory' || !node.category) {
      continue
    }

    const members = groups.get(node.category) ?? []
    members.push(node)
    groups.set(node.category, members)
  }

  for (const [category, members] of groups) {
    if (members.length < 2) {
      continue
    }

    const points = members.map(node => toScreen(node.x, node.y))
    let centerX = 0
    let centerY = 0
    let top = Infinity

    for (const point of points) {
      centerX += point.x / points.length
      centerY += point.y / points.length
      top = Math.min(top, point.y)
    }

    const center = { x: centerX, y: centerY }
    const padding = 18 * Math.max(0.8, Math.min(zoom, 1.4))
    let radius = padding

    for (const point of points) {
      radius = Math.max(radius, Math.hypot(point.x - center.x, point.y - center.y) + padding)
    }

    if (
      center.x + radius < 0 ||
      center.x - radius > width ||
      center.y + radius < 0 ||
      center.y - radius > height
    ) {
      continue
    }

    const hull = convexHull(points)
    const color = getNodeColorSet(members[0])
    const highlighted = !focusId || members.some(node => node.id === focusId || focusSet?.has(node.id))
    const alpha = highlighted ? 1 : 0.25
    const fill = ctx.createRadialGradient(center.x, center.y, 0, center.x, center.y, radius)
    fill.addColorStop(0, rgba(color.glow, (palette.dark ? 0.07 : 0.05) * alpha))
    fill.addColorStop(1, rgba(color.glow, (palette.dark ? 0.015 : 0.01) * alpha))

    ctx.save()
    ctx.fillStyle = fill
    ctx.strokeStyle = rgba(color.glow, (palette.dark ? 0.22 : 0.16) * alpha)
    ctx.lineWidth = 1
    ctx.beginPath()

    if (hull.length < 3) {
      ctx.arc(center.x, center.y, radius, 0, Math.PI * 2)
    } else {
      const expanded = hull.map(point => {
        const dx = point.x - center.x
        const dy = point.y - center.y
        const distance = Math.hypot(dx, dy) || 1

        return { x: point.x + dx / distance * padding, y: point.y + dy / distance * padding }
      })

      for (let i = 0; i < expanded.length; i += 1) {
        const current = expanded[i]
        const next = expanded[(i + 1) % expanded.length]
        const midpoint = { x: (current.x + next.x) / 2, y: (current.y + next.y) / 2 }

        if (i === 0) {
          const previous = expanded[expanded.length - 1]
          ctx.moveTo((previous.x + current.x) / 2, (previous.y + current.y) / 2)
        }

        ctx.quadraticCurveTo(current.x, current.y, midpoint.x, midpoint.y)
      }

      ctx.closePath()
    }

    ctx.fill()
    ctx.stroke()

    if (zoom >= 0.45) {
      ctx.font = '600 10px "Inter", -apple-system, system-ui, sans-serif'
      ctx.textAlign = 'center'
      ctx.textBaseline = 'middle'
      ctx.fillStyle = rgba(palette.fg, (highlighted ? 0.76 : 0.3))
      ctx.fillText(category, center.x, top - padding - 9)
    }

    ctx.restore()
  }
}

export function drawGraph(input: DrawInput): boolean {
  const {
    adjacency,
    births,
    ctx,
    dpr,
    hoverId,
    links,
    motionEnabled,
    nodes,
    now,
    palette,
    query,
    selectedId,
    size,
    vp
  } = input
  const { h, w } = size

  if (w <= 0 || h <= 0) {return false}

  const byId = new Map<string, GNode>()

  for (const n of nodes) {
    byId.set(n.id, n)
  }

  const focusId = hoverId ?? selectedId
  const focusSet = focusId ? (adjacency.get(focusId) ?? new Set<string>()) : null

  let animating = motionEnabled

  ctx.setTransform(dpr, 0, 0, dpr, 0, 0)

  // 1. Background with radial vignette. Theme-aware: a dark theme keeps the deep-space look; a
  // light theme paints a light paper (from palette.bg, resolved from the themed `--background`),
  // so dark nodes are not drawn on a near-black canvas. Previously the fill was hardcoded dark.
  const bg = palette.bg ?? { r: 6, g: 8, b: 15 }
  const bgCenter = rgba(palette.dark ? { r: 12, g: 16, b: 32 } : { r: Math.min(255, bg.r + 4), g: Math.min(255, bg.g + 4), b: Math.min(255, bg.b + 6) }, 1)
  const bgMid = rgba(bg, 1)
  const bgEdge = rgba(palette.dark ? { r: 2, g: 3, b: 6 } : { r: Math.max(0, bg.r - 10), g: Math.max(0, bg.g - 10), b: Math.max(0, bg.b - 8) }, 1)
  const bgGrad = ctx.createRadialGradient(w / 2, h / 2, Math.min(w, h) * 0.1, w / 2, h / 2, Math.max(w, h) * 0.75)
  bgGrad.addColorStop(0, bgCenter)
  bgGrad.addColorStop(0.5, bgMid)
  bgGrad.addColorStop(1, bgEdge)
  ctx.fillStyle = bgGrad
  ctx.fillRect(0, 0, w, h)

  const sx = (wx: number) => wx * vp.k + vp.x + w / 2
  const sy = (wy: number) => wy * vp.k + vp.y + h / 2
  const toScreen = (x: number, y: number): Point => ({ x: sx(x), y: sy(y) })
  drawCategoryClouds(ctx, nodes, toScreen, palette, vp.k, w, h, focusId, focusSet)

  const bornOf = (id: string): number => {
    const start = births.get(id)

    if (start == null) {return 1}
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

    if (degree < 3 && !n.pinned) {continue}

    const born = bornOf(n.id)

    if (born < 0.05) {continue}

    const X = sx(n.x)
    const Y = sy(n.y)
    const r = nodeRadius(n) * vp.k * (0.5 + 0.5 * born)

    // Cull off-screen blooms
    if (X < -150 || X > w + 150 || Y < -150 || Y > h + 150) {continue}

    const colorSet = getNodeColorSet(n)
    const bloomRadius = r * (degree >= 8 ? 6 : 4)
    const breathe = motionEnabled ? 0.88 + 0.12 * Math.sin(now * 0.001 + n.phase) : 1
    const bloomAlpha = Math.min(0.35, 0.08 + degree * 0.02) * born * breathe

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
  for (let linkIndex = 0; linkIndex < links.length; linkIndex += 1) {
    const link = links[linkIndex]
    const s = endpoint(link.source, byId)
    const t = endpoint(link.target, byId)

    if (!s || !t) {continue}

    const lit = !!focusId && (s.id === focusId || t.id === focusId || (!!focusSet && focusSet.has(s.id) && focusSet.has(t.id)))
    const born = Math.min(bornOf(s.id), bornOf(t.id))

    if (born < 0.05) {continue}

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
    const dx = tX - sX
    const dy = tY - sY
    const controlX = (sX + tX) / 2 - dy * 0.075
    const controlY = (sY + tY) / 2 + dx * 0.075

    // Typed-edge styling (no clutter): trust (confidence) modulates opacity so a weak
    // inferred link reads fainter than an asserted one; an inferred-entity edge is dashed
    // to visually separate "derived from a shared mention" from an explicit assertion;
    // a supersedes/correction edge is drawn boldest because it carries the belief change.
    const confidence = typeof link.confidence === 'number' ? Math.max(0, Math.min(1, link.confidence)) : 1
    const inferred = link.provenance === 'inferred_entity'
    const trust = 0.45 + 0.55 * confidence // keep even weak links visible, never invisible

    // Glowing gradient line
    const grad = ctx.createLinearGradient(sX, sY, tX, tY)
    const baseAlpha = (lit ? 0.85 : focusId ? 0.04 : 0.22) * trust
    const edgeAlpha = baseAlpha * born

    grad.addColorStop(0, rgba(sColors.glow, edgeAlpha))
    grad.addColorStop(1, rgba(tColors.glow, edgeAlpha))

    ctx.save()
    ctx.strokeStyle = grad
    ctx.lineWidth = (lit ? 1.8 : 0.85) * (link.kind === 'supersedes' ? 1.6 : 1)
    ctx.setLineDash(inferred ? [4, 3] : [])
    ctx.beginPath()
    ctx.moveTo(sX, sY)
    ctx.quadraticCurveTo(controlX, controlY, tX, tY)
    ctx.stroke()
    ctx.restore()

    if (motionEnabled && (s.id === focusId || t.id === focusId)) {
      const progress = (now * 0.00024 + linkIndex * 0.173) % 1
      const u = s.id === focusId ? progress : 1 - progress
      const inverse = 1 - u
      const x = inverse * inverse * sX + 2 * inverse * u * controlX + u * u * tX
      const y = inverse * inverse * sY + 2 * inverse * u * controlY + u * u * tY

      ctx.save()
      ctx.globalAlpha = 0.9 * Math.sin(Math.PI * u)
      ctx.fillStyle = rgba(sColors.highlight, 1)
      ctx.shadowColor = rgba(sColors.glow, 1)
      ctx.shadowBlur = 8
      ctx.beginPath()
      ctx.arc(x, y, 2, 0, Math.PI * 2)
      ctx.fill()
      ctx.restore()
    }
  }

  // 4. Render 3D Spherical Nodes
  const showAllLabels = vp.k >= 1.4
  const showFocusLabels = vp.k >= 0.7

  for (const n of nodes) {
    const born = bornOf(n.id)

    if (born < 0.02) {continue}

    const X = sx(n.x)
    const Y = sy(n.y)
    const breathe = motionEnabled ? 1 + 0.1 * Math.sin(now * 0.0012 + n.phase) : 1
    const r = Math.max(1.8, nodeRadius(n) * vp.k * (0.3 + 0.7 * born) * breathe)

    // Cull off-screen nodes
    if (X < -80 || X > w + 80 || Y < -80 || Y > h + 80) {continue}

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
      
      // Crisp text shadow for readability over dense nodes — tinted to the theme so a light
      // canvas gets a light halo (a dark halo on light paper muddies the text).
      ctx.shadowColor = palette.dark ? 'rgba(2, 4, 10, 0.9)' : 'rgba(248, 250, 253, 0.92)'
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
  if (w <= 0 || h <= 0 || !nodes.length) {return}

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
