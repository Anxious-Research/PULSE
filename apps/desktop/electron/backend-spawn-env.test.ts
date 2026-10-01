import assert from 'node:assert/strict'

import { test } from 'vitest'

import { desktopBackendSpawnEnv, guestOnboardingEnabled } from './guest-onboarding'

// Coverage for the desktop spawn env that used to live in guest-onboarding-flag.test.ts
// (deleted on main ahead of the guided-onboarding rewrite) plus the #118080 stay-alive stamp.

test('guestOnboardingEnabled: exactly "1" in env or --guest-onboarding on argv turns the free tier on', () => {
  assert.equal(guestOnboardingEnabled([], { PULSE_GUEST_ONBOARDING: '1' }), true)
  assert.equal(guestOnboardingEnabled(['electron', '.', '--guest-onboarding'], {}), true)

  assert.equal(guestOnboardingEnabled([], {}), false)
  assert.equal(guestOnboardingEnabled([], { PULSE_GUEST_ONBOARDING: 'true' }), false)
  assert.equal(guestOnboardingEnabled([], { PULSE_GUEST_ONBOARDING: '0' }), false)
  assert.equal(guestOnboardingEnabled(['electron', '.', '--local'], { PULSE_GUEST_ONBOARDING: '' }), false)
})

test('desktopBackendSpawnEnv stamps the launch decision last and never lets an inherited value leak', () => {
  const base = {
    PULSE_HOME: '/tmp/home',
    PULSE_DESKTOP: '1',
    PULSE_GUEST_ONBOARDING: '1',
    GATEWAY_ON_ALL_ADAPTERS_DOWN: 'exit',
    PATH: '/usr/bin'
  }

  const on = desktopBackendSpawnEnv({ ...base, PULSE_GUEST_ONBOARDING: '0' }, true)
  assert.equal(on.PULSE_GUEST_ONBOARDING, '1')

  const off = desktopBackendSpawnEnv(base, false)
  assert.equal(off.PULSE_GUEST_ONBOARDING, '0', 'a stray inherited "1" must not turn the free tier on')

  for (const env of [on, off]) {
    assert.equal(env.PULSE_HOME, base.PULSE_HOME)
    assert.equal(env.PULSE_DESKTOP, base.PULSE_DESKTOP)
    assert.equal(env.PATH, base.PATH)
    // The desktop spawns `pulse serve` with no supervising service manager, so the
    // child must stay alive on all-adapters-down instead of exiting EX_TEMPFAIL
    // (#118080). Stamped unconditionally — an inherited value cannot opt the child
    // back into the failure exit.
    assert.equal(env.GATEWAY_ON_ALL_ADAPTERS_DOWN, 'stay_alive')
  }
})
