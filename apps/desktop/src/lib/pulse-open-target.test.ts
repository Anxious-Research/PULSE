import { describe, expect, it } from 'vitest'

import {
  normalizePULSEOpenString,
  pathFromPULSEDeepLink,
  pathFromOpenDeepLink,
  resolvePULSEOpenPath
} from './pulse-open-target'

describe('normalizePULSEOpenString', () => {
  it('accepts hash-router paths and strips a leading hash', () => {
    expect(normalizePULSEOpenString('/index-network/intent/1')).toBe('/index-network/intent/1')
    expect(normalizePULSEOpenString('#/index-network/intent/1')).toBe('/index-network/intent/1')
  })

  it('maps plugin-scoped pulse:// deep links to the same path', () => {
    expect(normalizePULSEOpenString('pulse://index-network/intent/1')).toBe('/index-network/intent/1')
    expect(normalizePULSEOpenString('pulse://index-network/intent/1?focus=true')).toBe(
      '/index-network/intent/1?focus=true'
    )
  })

  it('maps pulse://open/… deep links by stripping the open host', () => {
    expect(normalizePULSEOpenString('pulse://open/index-network/intent/1')).toBe('/index-network/intent/1')
    expect(normalizePULSEOpenString('pulse://open/settings/plugins')).toBe('/settings/plugins')
  })

  it('rejects reserved pulse kinds and unsafe paths', () => {
    expect(normalizePULSEOpenString('pulse://blueprint/morning-brief')).toBeNull()
    expect(normalizePULSEOpenString('pulse://plugin/install')).toBeNull()
    expect(normalizePULSEOpenString('https://example.com/x')).toBeNull()
    expect(normalizePULSEOpenString('/../etc/passwd')).toBeNull()
    expect(normalizePULSEOpenString('index-network')).toBeNull()
  })
})

describe('resolvePULSEOpenPath', () => {
  it('merges structured path + params', () => {
    expect(resolvePULSEOpenPath({ path: '/index-network/intent/1', params: { focus: 'true' } })).toBe(
      '/index-network/intent/1?focus=true'
    )
  })

  it('resolves href the same as a bare string', () => {
    expect(resolvePULSEOpenPath({ href: 'pulse://index-network/intent/1' })).toBe('/index-network/intent/1')
  })
})

describe('pathFromPULSEDeepLink', () => {
  it('builds the navigate path from a plugin-scoped deep-link payload', () => {
    expect(pathFromPULSEDeepLink('index-network', 'intent/1')).toBe('/index-network/intent/1')
  })

  it('builds the navigate path from pulse://open/… payloads', () => {
    expect(pathFromOpenDeepLink('index-network/intent/1')).toBe('/index-network/intent/1')
    expect(pathFromPULSEDeepLink('open', 'agent/42')).toBe('/agent/42')
  })

  it('ignores reserved kinds', () => {
    expect(pathFromPULSEDeepLink('blueprint', 'morning-brief')).toBeNull()
    expect(pathFromPULSEDeepLink('plugin', 'install')).toBeNull()
    expect(pathFromPULSEDeepLink('skill', 'install')).toBeNull()
  })
})
