import assert from 'node:assert/strict'

import { test } from 'vitest'

import { hasWindowsPathPrefix, isExternalVenvHolder, isPulseOwnedVenvDaemon } from './venv-holder-select'

const SCRIPTS = 'C:\\Pulse\\venv\\Scripts'

test('matches the hindsight daemon shim (exe under venv Scripts + hindsight cmdline)', () => {
  assert.equal(
    isPulseOwnedVenvDaemon(
      'C:\\Pulse\\venv\\Scripts\\pythonw.exe',
      'C:\\Pulse\\venv\\Scripts\\pythonw.exe -m hindsight_api.main --daemon --idle-timeout 300 --port 9177',
      SCRIPTS
    ),
    true
  )
})

test('Windows path prefix match is ordinal case-insensitive', () => {
  assert.equal(
    isPulseOwnedVenvDaemon(
      'c:\\pulse\\venv\\scripts\\python.exe',
      'python.exe -m hindsight_api.main --daemon',
      'C:\\Pulse\\venv\\Scripts'
    ),
    true
  )
})

test('excludes external venv holders that are not the hindsight daemon', () => {
  // a user terminal running the pulse CLI from the venv — must NOT be killed
  assert.equal(isPulseOwnedVenvDaemon('C:\\Pulse\\venv\\Scripts\\pulse.exe', 'pulse chat -q "hi"', SCRIPTS), false)
  // an unrelated python script using the venv interpreter
  assert.equal(
    isPulseOwnedVenvDaemon('C:\\Pulse\\venv\\Scripts\\python.exe', 'python C:\\tools\\import.py', SCRIPTS),
    false
  )
})

test('excludes exes outside the venv even when the cmdline mentions hindsight', () => {
  assert.equal(
    isPulseOwnedVenvDaemon('C:\\Other\\pythonw.exe', 'pythonw -m hindsight_api.main --daemon', SCRIPTS),
    false
  )
})

test('prefix boundary: sibling dirs (ScriptsX) do not match', () => {
  assert.equal(hasWindowsPathPrefix('C:\\Pulse\\venv\\ScriptsX\\python.exe', SCRIPTS), false)
  assert.equal(hasWindowsPathPrefix('C:\\Pulse\\venv\\Scripts\\python.exe', SCRIPTS), true)
})

test('null/undefined fields never match', () => {
  assert.equal(isPulseOwnedVenvDaemon(null, 'x', SCRIPTS), false)
  assert.equal(isPulseOwnedVenvDaemon('C:\\Pulse\\venv\\Scripts\\pythonw.exe', null, SCRIPTS), false)
  assert.equal(isPulseOwnedVenvDaemon(undefined, undefined, SCRIPTS), false)
})

// --- isExternalVenvHolder (#62311) ------------------------------------------

test('matches the autostart gateway shim (pulse.exe under venv Scripts)', () => {
  assert.equal(
    isExternalVenvHolder(
      'C:\\Pulse\\venv\\Scripts\\pulse.exe',
      '"C:\\Pulse\\venv\\Scripts\\pulse.exe" gateway run --external-supervisor',
      SCRIPTS
    ),
    true
  )
})

test('matches the dashboard scheduled task (python -m pulse_cli / -m pulse)', () => {
  assert.equal(
    isExternalVenvHolder(
      'C:\\Pulse\\venv\\Scripts\\python.exe',
      '"C:\\Pulse\\venv\\Scripts\\python.exe" -m pulse_cli.main dashboard',
      SCRIPTS
    ),
    true
  )
  assert.equal(
    isExternalVenvHolder('C:\\Pulse\\venv\\Scripts\\pythonw.exe', 'pythonw.exe -m pulse serve', SCRIPTS),
    true
  )
})

test('never matches an unrelated process that merely borrows the venv interpreter', () => {
  // a user's own script running on the venv python — NOT Pulse, must NOT be killed
  assert.equal(
    isExternalVenvHolder('C:\\Pulse\\venv\\Scripts\\python.exe', 'python C:\\tools\\import.py', SCRIPTS),
    false
  )
  // hindsight daemon is selected by isPulseOwnedVenvDaemon, not here
  assert.equal(
    isExternalVenvHolder('C:\\Pulse\\venv\\Scripts\\pythonw.exe', 'pythonw -m hindsight_api.main --daemon', SCRIPTS),
    false
  )
})

test('never matches a process outside the venv, even with pulse in the cmdline', () => {
  // an editor / shell whose command line mentions the install root (#62445 regression guard)
  assert.equal(
    isExternalVenvHolder('C:\\Windows\\System32\\cmd.exe', 'cmd /c cd C:\\Pulse\\venv\\Scripts && dir', SCRIPTS),
    false
  )
  assert.equal(isExternalVenvHolder('C:\\Other\\pulse.exe', 'pulse gateway run', SCRIPTS), false)
})

test('sibling-dir and boundary safety for the external selector', () => {
  assert.equal(isExternalVenvHolder('C:\\Pulse\\venv\\ScriptsX\\pulse.exe', 'pulse gateway run', SCRIPTS), false)
  assert.equal(isExternalVenvHolder(null, 'pulse gateway run', SCRIPTS), false)
  assert.equal(isExternalVenvHolder('C:\\Pulse\\venv\\Scripts\\pulse.exe', null, SCRIPTS), false)
})
