// Neural 3D Graph Color & Palette System matching the reference visualization.
// Features vibrant luminous hues, spherical shading, and dark-space contrast.

import type { StarmapNode } from '@/types/pulse'

import type { GraphPalette, Rgb } from './types'

export const DEEP_SPACE_BG: Rgb = { r: 6, g: 8, b: 15 } // #06080f
export const FOREGROUND_TEXT: Rgb = { r: 240, g: 244, b: 255 }

let _probe: CanvasRenderingContext2D | null = null

export function resolveRgb(color: string): Rgb {
  if (!_probe) {
    const c = document.createElement('canvas')
    c.width = 1
    c.height = 1
    _probe = c.getContext('2d', { willReadFrequently: true })
  }

  if (!_probe) {
    return { r: 148, g: 163, b: 184 }
  }

  _probe.clearRect(0, 0, 1, 1)
  _probe.fillStyle = '#888888'
  _probe.fillStyle = color
  _probe.fillRect(0, 0, 1, 1)

  const d = _probe.getImageData(0, 0, 1, 1).data

  return { r: d[0] ?? 0, g: d[1] ?? 0, b: d[2] ?? 0 }
}

export function rgba(c: Rgb, a: number): string {
  return `rgba(${c.r},${c.g},${c.b},${Math.max(0, Math.min(1, a))})`
}

export function mix(a: Rgb, b: Rgb, t: number): Rgb {
  const p = Math.max(0, Math.min(1, t))

  return {
    r: Math.round(a.r + (b.r - a.r) * p),
    g: Math.round(a.g + (b.g - a.g) * p),
    b: Math.round(a.b + (b.b - a.b) * p)
  }
}

export function luminance(c: Rgb): number {
  return (0.2126 * c.r + 0.7152 * c.g + 0.0722 * c.b) / 255
}

// Category Hues matching the reference image's vibrant semantic clusters
export const CATEGORY_COLORS: Record<string, { base: Rgb; glow: Rgb; highlight: Rgb }> = {
  // Cyan / Ice Blue (Self / Core Architecture)
  self: {
    base: { r: 0, g: 180, b: 240 },
    glow: { r: 0, g: 210, b: 255 },
    highlight: { r: 180, g: 245, b: 255 }
  },
  // Neon Purple / Magenta (User / Profiles / Identity)
  user: {
    base: { r: 190, g: 75, b: 255 },
    glow: { r: 220, g: 90, b: 255 },
    highlight: { r: 250, g: 210, b: 255 }
  },
  // Warm Amber / Golden Peach (Concepts & Knowledge)
  concept: {
    base: { r: 255, g: 155, b: 35 },
    glow: { r: 255, g: 185, b: 65 },
    highlight: { r: 255, g: 235, b: 180 }
  },
  // Coral / Red-Orange (Projects & Active Work)
  project: {
    base: { r: 255, g: 85, b: 70 },
    glow: { r: 255, g: 115, b: 100 },
    highlight: { r: 255, g: 210, b: 200 }
  },
  // Emerald / Mint (Daily & Episodes)
  daily: {
    base: { r: 40, g: 215, b: 145 },
    glow: { r: 65, g: 245, b: 175 },
    highlight: { r: 205, g: 255, b: 235 }
  },
  // Violet / Rose (Beliefs & Principles)
  belief: {
    base: { r: 240, g: 70, b: 170 },
    glow: { r: 255, g: 100, b: 195 },
    highlight: { r: 255, g: 210, b: 240 }
  },
  // Silver Pearl / Ghost (Unresolved links)
  ghost: {
    base: { r: 120, g: 140, b: 170 },
    glow: { r: 160, g: 180, b: 210 },
    highlight: { r: 220, g: 230, b: 245 }
  }
}

const DEFAULT_CATEGORY = CATEGORY_COLORS.concept

export function getNodeColorSet(n: StarmapNode): { base: Rgb; glow: Rgb; highlight: Rgb } {
  if (n.kind === 'ghost') {
    return CATEGORY_COLORS.ghost
  }

  const cat = n.category?.toLowerCase()

  return (cat && CATEGORY_COLORS[cat]) ? CATEGORY_COLORS[cat] : DEFAULT_CATEGORY
}

export function computePalette(canvas: HTMLCanvasElement): GraphPalette {
  const style = getComputedStyle(canvas)
  const fg = resolveRgb(style.color || '#f0f4ff')
  const dark = luminance(fg) > 0.4

  return {
    bg: DEEP_SPACE_BG,
    dark,
    fg,
    nodeInk: (n: StarmapNode) => {
      const set = getNodeColorSet(n)

      return set.base
    }
  }
}
