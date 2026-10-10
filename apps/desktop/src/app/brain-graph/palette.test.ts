import { describe, expect, it } from 'vitest'

import { DEEP_SPACE_BG, LIGHT_PAPER_BG, luminance, mix, paletteForColors, rgba } from './palette'

describe('palette', () => {
  describe('rgba', () => {
    it('formats an RGB+alpha string', () => {
      expect(rgba({ b: 30, g: 20, r: 10 }, 0.5)).toBe('rgba(10,20,30,0.5)')
    })
  })

  describe('mix', () => {
    it('mixes two colours at t=0', () => {
      const a = { b: 0, g: 0, r: 0 }
      const b = { b: 100, g: 100, r: 100 }

      expect(mix(a, b, 0)).toEqual(a)
    })

    it('mixes two colours at t=1', () => {
      const a = { b: 0, g: 0, r: 0 }
      const b = { b: 100, g: 100, r: 100 }

      expect(mix(a, b, 1)).toEqual(b)
    })

    it('mixes two colours at t=0.5', () => {
      const a = { b: 0, g: 0, r: 0 }
      const b = { b: 100, g: 100, r: 100 }
      const m = mix(a, b, 0.5)

      expect(m.r).toBe(50)
      expect(m.g).toBe(50)
      expect(m.b).toBe(50)
    })

    it('clamps t below 0', () => {
      const a = { b: 0, g: 0, r: 0 }
      const b = { b: 100, g: 100, r: 100 }

      expect(mix(a, b, -0.5)).toEqual(a)
    })

    it('clamps t above 1', () => {
      const a = { b: 0, g: 0, r: 0 }
      const b = { b: 100, g: 100, r: 100 }

      expect(mix(a, b, 1.5)).toEqual(b)
    })
  })

  describe('luminance', () => {
    it('returns 0 for black', () => {
      expect(luminance({ b: 0, g: 0, r: 0 })).toBe(0)
    })

    it('returns 1 for white', () => {
      expect(luminance({ b: 255, g: 255, r: 255 })).toBeCloseTo(1)
    })

    it('returns a mid value for grey', () => {
      const l = luminance({ b: 128, g: 128, r: 128 })

      expect(l).toBeGreaterThan(0.4)
      expect(l).toBeLessThan(0.6)
    })
  })

  describe('computePalette theme-awareness', () => {
    // The canvas background must follow the themed --background, not a hardcoded dark fill, so a
    // light theme paints a light canvas (readable dark-on-light) and a dark theme stays deep-space.
    // paletteForColors is the pure core computePalette delegates to after resolving CSS vars.
    it('picks a light canvas for a light theme background', () => {
      const p = paletteForColors({ r: 255, g: 255, b: 255 }, { r: 17, g: 17, b: 17 })
      expect(p.dark).toBe(false)
      expect(p.bg).toEqual(LIGHT_PAPER_BG)
      expect(luminance(p.bg)).toBeGreaterThan(0.8)
    })

    it('picks the deep-space canvas for a dark theme background', () => {
      const p = paletteForColors({ r: 6, g: 8, b: 15 }, { r: 240, g: 244, b: 255 })
      expect(p.dark).toBe(true)
      expect(p.bg).toEqual(DEEP_SPACE_BG)
      expect(luminance(p.bg)).toBeLessThan(0.2)
    })
  })
})
