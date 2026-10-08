// §8 Visual Design v3 — Flagship Neural Graph
// Deep space cosmic theme, animated spreading activation, bloom effects, heatmap brightness

import type { Rgb } from './types'
import { rgba } from './color'

// §8.1 Core Visual Language
export const DEEP_SPACE_BG = { from: '#0a0e27', to: '#000000' }
export const BLOOM_INTENSITY = 0.6
export const PARTICLE_SIZE = 2
export const ACTIVATION_DURATION = 800 // ms
export const PULSE_DURATION = 2000 // ms

// §8.2 3D Depth Illusion — z-layer blur amounts
export const DEPTH_BLUR = {
  distant: 2, // gaussian blur +2px for back nodes
  mid: 0,
  foreground: 0
}

// §8.3 Animated Spreading Activation — particle flow timing
export const SPREAD_TIMING = {
  cueGlow: 200, // ms
  hopDelay: 400, // ms between hops
  particleSpeed: 800 // ms to travel edge
}

// §8.5 Heatmap Brightness Encoding
export function heatmapBrightness(accessCount: number, maxAccess: number): number {
  if (maxAccess === 0) return 0.4
  return 0.4 + (accessCount / maxAccess) * 0.6
}

// Bloom effect via CSS filter (applied to canvas)
export function bloomFilter(intensity: number): string {
  const blur = intensity * 8
  const brightness = 1 + intensity * 0.4
  return `blur(${blur}px) brightness(${brightness})`
}

// Deep space gradient background
export function drawDeepSpaceBackground(
  ctx: CanvasRenderingContext2D,
  w: number,
  h: number
): void {
  const grad = ctx.createLinearGradient(0, 0, 0, h)
  grad.addColorStop(0, DEEP_SPACE_BG.from)
  grad.addColorStop(1, DEEP_SPACE_BG.to)
  ctx.fillStyle = grad
  ctx.fillRect(0, 0, w, h)
}

// §8.3 Particle system for spreading activation visualization
export interface Particle {
  id: string
  x1: number
  y1: number
  x2: number
  y2: number
  startTime: number
  duration: number
  color: Rgb
}

export class ParticleSystem {
  private particles: Particle[] = []

  addParticle(p: Particle): void {
    this.particles.push(p)
  }

  update(now: number): void {
    this.particles = this.particles.filter(p => now - p.startTime < p.duration)
  }

  draw(ctx: CanvasRenderingContext2D, now: number): void {
    for (const p of this.particles) {
      const elapsed = now - p.startTime
      const t = Math.min(1, elapsed / p.duration)
      if (t >= 1) continue

      const x = p.x1 + (p.x2 - p.x1) * t
      const y = p.y1 + (p.y2 - p.y1) * t
      const alpha = Math.sin(t * Math.PI) * 0.8 // fade in/out

      ctx.save()
      ctx.globalAlpha = alpha
      ctx.fillStyle = rgba(p.color, 1)
      ctx.shadowBlur = 8
      ctx.shadowColor = rgba(p.color, 0.8)
      ctx.beginPath()
      ctx.arc(x, y, PARTICLE_SIZE, 0, Math.PI * 2)
      ctx.fill()
      ctx.restore()
    }
  }

  clear(): void {
    this.particles = []
  }
}

// §8.5 Node pulse effect (birth animation)
export function drawPulseEffect(
  ctx: CanvasRenderingContext2D,
  x: number,
  y: number,
  r: number,
  color: Rgb,
  progress: number // 0→1 over PULSE_DURATION
): void {
  if (progress >= 1) return

  const scale = 1 + progress * 0.5
  const alpha = (1 - progress) * 0.4

  ctx.save()
  ctx.globalAlpha = alpha
  ctx.strokeStyle = rgba(color, 1)
  ctx.lineWidth = 2
  ctx.beginPath()
  ctx.arc(x, y, r * scale, 0, Math.PI * 2)
  ctx.stroke()
  ctx.restore()
}

// Enhanced bloom for active/hovered nodes
export function drawBloomGlow(
  ctx: CanvasRenderingContext2D,
  x: number,
  y: number,
  r: number,
  color: Rgb,
  intensity: number // 0→1
): void {
  if (intensity < 0.01) return

  const glowR = r + 12 * intensity
  const grad = ctx.createRadialGradient(x, y, r * 0.5, x, y, glowR)
  grad.addColorStop(0, rgba(color, intensity * 0.3))
  grad.addColorStop(0.5, rgba(color, intensity * 0.15))
  grad.addColorStop(1, rgba(color, 0))

  ctx.save()
  ctx.fillStyle = grad
  ctx.beginPath()
  ctx.arc(x, y, glowR, 0, Math.PI * 2)
  ctx.fill()
  ctx.restore()
}

// Z-layer depth assignment based on node age/recency
export function depthLayer(recency: number): 'distant' | 'mid' | 'foreground' {
  if (recency < 0.3) return 'distant'
  if (recency < 0.7) return 'mid'
  return 'foreground'
}

// Parallax motion multiplier for drag (distant nodes move slower)
export function parallaxSpeed(layer: 'distant' | 'mid' | 'foreground'): number {
  return layer === 'distant' ? 0.3 : layer === 'mid' ? 0.7 : 1
}

// Gold glow for cue nodes (recall trigger)
export const CUE_GOLD: Rgb = { r: 255, g: 215, b: 0 }

// Activation state tracking for live spreading-activation viz
export interface ActivationState {
  cueNodes: Set<string>
  activatedNodes: Map<string, number> // nodeId → activation score
  topK: Set<string> // recalled nodes (stay bright)
  startTime: number
}

export function drawActivationGlow(
  ctx: CanvasRenderingContext2D,
  x: number,
  y: number,
  r: number,
  score: number, // 0→1
  isCue: boolean,
  now: number,
  startTime: number
): void {
  const elapsed = now - startTime
  if (elapsed > ACTIVATION_DURATION) return

  const color = isCue ? CUE_GOLD : { r: 100, g: 150, b: 255 }
  const t = Math.min(1, elapsed / ACTIVATION_DURATION)
  const intensity = score * (1 - t * 0.5) // fade slowly

  drawBloomGlow(ctx, x, y, r, color, intensity * 0.8)
}
