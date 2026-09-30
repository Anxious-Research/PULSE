import { describe, expect, it } from 'vitest'

import {
  normalizePulseOpenString,
  pathFromPulseDeepLink,
  pathFromOpenDeepLink,
  resolvePulseOpenPath
} from './pulse-open-target'

describe('normalizePulseOpenString', () => {
  it('accepts hash-router paths and strips a leading hash', () => {
    expect(normalizePulseOpenString('/index-network/intent/1')).toBe('/index-network/intent/1')
    expect(normalizePulseOpenString('#/index-network/intent/1')).toBe('/index-network/intent/1')
  })

  it('maps plugin-scoped pulse:// deep links to the same path', () => {
    expect(normalizePulseOpenString('pulse://index-network/intent/1')).toBe('/index-network/intent/1')
    expect(normalizePulseOpenString('pulse://index-network/intent/1?focus=true')).toBe(
      '/index-network/intent/1?focus=true'
    )
  })

  it('maps pulse://open/… deep links by stripping the open host', () => {
    expect(normalizePulseOpenString('pulse://open/index-network/intent/1')).toBe('/index-network/intent/1')
    expect(normalizePulseOpenString('pulse://open/settings/plugins')).toBe('/settings/plugins')
  })

  it('rejects reserved pulse kinds and unsafe paths', () => {
    expect(normalizePulseOpenString('pulse://blueprint/morning-brief')).toBeNull()
    expect(normalizePulseOpenString('pulse://plugin/install')).toBeNull()
    expect(normalizePulseOpenString('https://example.com/x')).toBeNull()
    expect(normalizePulseOpenString('/../etc/passwd')).toBeNull()
    expect(normalizePulseOpenString('index-network')).toBeNull()
  })
})

describe('resolvePulseOpenPath', () => {
  it('merges structured path + params', () => {
    expect(resolvePulseOpenPath({ path: '/index-network/intent/1', params: { focus: 'true' } })).toBe(
      '/index-network/intent/1?focus=true'
    )
  })

  it('resolves href the same as a bare string', () => {
    expect(resolvePulseOpenPath({ href: 'pulse://index-network/intent/1' })).toBe('/index-network/intent/1')
  })
})

describe('pathFromPulseDeepLink', () => {
  it('builds the navigate path from a plugin-scoped deep-link payload', () => {
    expect(pathFromPulseDeepLink('index-network', 'intent/1')).toBe('/index-network/intent/1')
  })

  it('builds the navigate path from pulse://open/… payloads', () => {
    expect(pathFromOpenDeepLink('index-network/intent/1')).toBe('/index-network/intent/1')
    expect(pathFromPulseDeepLink('open', 'agent/42')).toBe('/agent/42')
  })

  it('ignores reserved kinds', () => {
    expect(pathFromPulseDeepLink('blueprint', 'morning-brief')).toBeNull()
    expect(pathFromPulseDeepLink('plugin', 'install')).toBeNull()
    expect(pathFromPulseDeepLink('skill', 'install')).toBeNull()
  })
})
