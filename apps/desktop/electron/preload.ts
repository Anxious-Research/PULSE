import { contextBridge, ipcRenderer, webFrame, webUtils } from 'electron'

import type { DesktopProfileRoute } from './desktop-profile'
import type { HudModifierApi, HudModifierStatus } from './hud-modifier-types'
import { customWindowControlsEnabled } from './window-controls'

// Which translucency the OS can back. Asked synchronously because the renderer
// needs it before its first paint, and answered by main because deciding it
// needs `os.release()` — a sandboxed preload may only require electron, events,
// timers and url, so importing node:os here throws before contextBridge runs
// and takes the ENTIRE bridge down with it (window.pulseDesktop undefined =>
// "Desktop IPC bridge is unavailable"). No reply means no glass, which degrades
// to an ordinary opaque window rather than a page thinned over nothing.
const translucencySupport = ipcRenderer.sendSync('pulse:translucency:support')
const hudWindowing = ipcRenderer.sendSync('pulse:hud:windowing')
const hudNativeDrag = hudWindowing?.nativeDrag === true

const launchFlags: { localModels?: boolean; guestOnboarding?: boolean } | undefined =
  ipcRenderer.sendSync('pulse:feature-flags')

// Local, sanitized skin payload for the first renderer theme paint. This does
// not wait on `gateway.ready`, so an unreachable remote primary cannot force
// the built-in palette over the skin configured on this machine.
const localSkin = ipcRenderer.sendSync('pulse:skin:local')

import { unwrapExpectedNotFound } from './api-expected-404'

contextBridge.exposeInMainWorld('pulseDesktop', {
  glassSupported: translucencySupport?.glass === true,
  translucencySupported: translucencySupport?.translucency === true,
  // Launch-flag fact: the app was started with --local, so the renderer may
  // show the local-models surfaces. Static for the window's lifetime.
  localModelsEnabled: launchFlags?.localModels === true,
  // Launch-flag fact: the Nous free tier is on for this launch
  // (PULSE_GUEST_ONBOARDING=1 or --guest-onboarding). Read-only; the same
  // decision is stamped onto every backend the app spawns.
  guestOnboardingEnabled: launchFlags?.guestOnboarding === true,
  localSkin: localSkin && typeof localSkin === 'object' ? localSkin : null,
  getConnection: (profile, opts) => ipcRenderer.invoke('pulse:connection', profile, opts),
  // Registry-scoped backend resolution: { connectionId, profile } → descriptor.
  getConnectionFor: payload => ipcRenderer.invoke('pulse:connection:for', payload),
  getProfileRoutes: profiles => ipcRenderer.invoke('pulse:plugin-profile-routes', profiles),
  revalidateConnection: () => ipcRenderer.invoke('pulse:connection:revalidate'),
  touchBackend: (profile, options) => ipcRenderer.invoke('pulse:backend:touch', profile, options),
  getPoolLimits: () => ipcRenderer.invoke('pulse:pool-limits:get'),
  setPoolLimits: limits => ipcRenderer.invoke('pulse:pool-limits:set', limits),
  getGatewayWsUrl: profile => ipcRenderer.invoke('pulse:gateway:ws-url', profile),
  // Registry-scoped fresh WS URL: { connectionId, profile } → result shape of
  // getGatewayWsUrl, minted against that connection's backend.
  getGatewayWsUrlFor: payload => ipcRenderer.invoke('pulse:gateway:ws-url-for', payload),
  // Union agent roster across every registered connection.
  getAgentRoster: () => ipcRenderer.invoke('pulse:agents:roster'),
  openSessionWindow: (sessionId, opts) => ipcRenderer.invoke('pulse:window:openSession', sessionId, opts),
  openSessionInTerminal: (sessionId, opts) => ipcRenderer.invoke('pulse:window:openInTerminal', sessionId, opts),
  openWindow: (options?: DesktopProfileRoute) => ipcRenderer.invoke('pulse:window:openInstance', options),
  openBrowserWindow: tabId => ipcRenderer.invoke('pulse:window:openBrowser', tabId),
  windowRelay: {
    send: payload => ipcRenderer.send('pulse:window:relay', payload),
    onMessage: callback => {
      const listener = (_event, payload) => callback(payload)
      ipcRenderer.on('pulse:window:relay', listener)

      return () => ipcRenderer.removeListener('pulse:window:relay', listener)
    }
  },
  onBrowserPopoutClosed: callback => {
    const listener = (_event, tabId) => callback(tabId)
    ipcRenderer.on('pulse:browser-popout:closed', listener)

    return () => ipcRenderer.removeListener('pulse:browser-popout:closed', listener)
  },
  claimAmbientCue: key => ipcRenderer.invoke('pulse:ambient:claim', key),
  windowControls: {
    custom: customWindowControlsEnabled(),
    minimize: () => ipcRenderer.send('pulse:window-control', 'minimize'),
    toggleMaximize: () => ipcRenderer.send('pulse:window-control', 'toggle-maximize'),
    close: () => ipcRenderer.send('pulse:window-control', 'close')
  },
  wakeIndicator: {
    getState: () => ipcRenderer.invoke('pulse:wake-indicator:get'),
    setState: state => ipcRenderer.send('pulse:wake-indicator:set', state),
    onState: callback => {
      const listener = (_event, state) => callback(state)
      ipcRenderer.on('pulse:wake-indicator:state', listener)

      return () => ipcRenderer.removeListener('pulse:wake-indicator:state', listener)
    }
  },
  chatOnboarding: {
    grow: request => ipcRenderer.send('pulse:chat-onboarding:grow', request),
    soloBoot: () => ipcRenderer.send('pulse:chat-onboarding:solo-boot')
  },
  petOverlay: {
    // Main renderer → main process: window lifecycle + drag. `request` is
    // `{ bounds, screen }`; resolves with the screen bounds it actually used.
    open: request => ipcRenderer.invoke('pulse:pet-overlay:open', request),
    close: () => ipcRenderer.invoke('pulse:pet-overlay:close'),
    setBounds: bounds => ipcRenderer.send('pulse:pet-overlay:set-bounds', bounds),
    setIgnoreMouse: ignore => ipcRenderer.send('pulse:pet-overlay:ignore-mouse', ignore),
    // Flip the overlay focusable (and focus it) while the composer needs keys.
    setFocusable: focusable => ipcRenderer.send('pulse:pet-overlay:set-focusable', focusable),
    // Main renderer → overlay (forwarded by main): push the latest pet state.
    pushState: payload => ipcRenderer.send('pulse:pet-overlay:state', payload),
    // Overlay → main renderer (forwarded by main): pop back in / composer submit.
    control: payload => ipcRenderer.send('pulse:pet-overlay:control', payload),
    // Overlay subscribes to state pushes.
    onState: callback => {
      const listener = (_event, payload) => callback(payload)
      ipcRenderer.on('pulse:pet-overlay:state', listener)

      return () => ipcRenderer.removeListener('pulse:pet-overlay:state', listener)
    },
    // Main renderer subscribes to overlay control messages.
    onControl: callback => {
      const listener = (_event, payload) => callback(payload)
      ipcRenderer.on('pulse:pet-overlay:control', listener)

      return () => ipcRenderer.removeListener('pulse:pet-overlay:control', listener)
    }
  },
  // HUD mode: the chrome-free floating chat. A full app renderer (own gateway)
  // sized as a floating bar, so it mounts the real composer. Main owns the
  // window; `onChanged` keeps every window's toggle truthful.
  hud: {
    nativeDrag: hudNativeDrag,
    windowing: {
      clientPlacement: hudWindowing?.clientPlacement !== false,
      controlDrag: hudWindowing?.controlDrag === true,
      nativeDrag: hudNativeDrag,
      solid: hudWindowing?.solid === true,
      workspaceTransfer: hudWindowing?.workspaceTransfer === true
    },
    open: request => ipcRenderer.invoke('pulse:hud:open', request),
    close: () => ipcRenderer.invoke('pulse:hud:close'),
    setIgnoreMouse: ignore => ipcRenderer.send('pulse:hud:ignore-mouse', ignore),
    beginMove: () => ipcRenderer.send('pulse:hud:begin-move'),
    endMove: () => ipcRenderer.send('pulse:hud:end-move'),
    moveBy: delta => ipcRenderer.send('pulse:hud:move-by', delta),
    setWorkspaceTransfer: transferring => ipcRenderer.send('pulse:hud:workspace-transfer', transferring),
    setBounds: bounds => ipcRenderer.send('pulse:hud:set-bounds', bounds),
    resetLayout: () => ipcRenderer.invoke('pulse:hud:reset-layout'),
    // Whether the band covers the window below the bar. Main pairs it with the
    // user's translucency setting to decide the native frost (macOS vibrancy /
    // Windows 11 DWM backdrop) — see hudFrostFor.
    setFrost: showing => ipcRenderer.invoke('pulse:hud:frost', showing),
    // The HUD tells main which session it is on; main hands that back to the
    // app window when the HUD closes, so the app can re-home onto it.
    setSession: sessionId => ipcRenderer.send('pulse:hud:session', sessionId),
    onGoto: callback => {
      const listener = (_event, sessionId) => callback(sessionId)
      ipcRenderer.on('pulse:hud:goto', listener)

      return () => ipcRenderer.removeListener('pulse:hud:goto', listener)
    },
    onChanged: callback => {
      const listener = (_event, state) => callback(state)
      ipcRenderer.on('pulse:hud:changed', listener)

      return () => ipcRenderer.removeListener('pulse:hud:changed', listener)
    },
    // Linux only, and silent elsewhere: where the cursor is, in page
    // coordinates, or null when it has left the window. Stands in for the
    // mousemove that `setIgnoreMouseEvents(true, { forward: true })` delivers on
    // macOS and Windows but not here.
    onCursor: callback => {
      const listener = (_event, point) => callback(point)
      ipcRenderer.on('pulse:hud:cursor', listener)

      return () => ipcRenderer.removeListener('pulse:hud:cursor', listener)
    },
    // Main's game-overlay watch: whether a fullscreen app (a game) is under
    // the HUD, so the renderer can step back to the low-opacity overlay
    // treatment while one owns the screen.
    onGameOverlay: callback => {
      const listener = (_event, state) => callback(state)
      ipcRenderer.on('pulse:hud:game-overlay', listener)

      return () => ipcRenderer.removeListener('pulse:hud:game-overlay', listener)
    }
  },
  hudModifier: {
    getSettings: () => ipcRenderer.invoke('pulse:hud-modifier:settings:get'),
    setEnabled: enabled => ipcRenderer.invoke('pulse:hud-modifier:settings:set', enabled),
    openPermissionSettings: () => ipcRenderer.invoke('pulse:hud-modifier:permission'),
    onStatus: callback => {
      const listener = (_event: Electron.IpcRendererEvent, status: HudModifierStatus) => callback(status)
      ipcRenderer.on('pulse:hud-modifier:status', listener)

      return () => ipcRenderer.removeListener('pulse:hud-modifier:status', listener)
    }
  } satisfies HudModifierApi,
  // macOS native screenshot gesture; captures require a main-issued request.
  screenshot:
    process.platform === 'darwin'
      ? {
          getSettings: () => ipcRenderer.invoke('pulse:screenshot:settings:get'),
          setEnabled: enabled => ipcRenderer.invoke('pulse:screenshot:settings:set', enabled),
          openPermissionSettings: kind => ipcRenderer.invoke('pulse:screenshot:permission', kind),
          capture: requestId => ipcRenderer.invoke('pulse:screenshot:capture', requestId),
          onStatus: callback => {
            const listener = (_event, status) => callback(status)
            ipcRenderer.on('pulse:screenshot:status', listener)

            return () => ipcRenderer.removeListener('pulse:screenshot:status', listener)
          },
          onRequest: callback => {
            const channel = 'pulse:screenshot:request'
            const listener = (_event, requestId) => callback(requestId)

            if (ipcRenderer.listenerCount(channel) === 0) {
              ipcRenderer.send('pulse:screenshot:subscribe', true)
            }

            ipcRenderer.on(channel, listener)

            return () => {
              ipcRenderer.removeListener(channel, listener)

              if (ipcRenderer.listenerCount(channel) === 0) {
                ipcRenderer.send('pulse:screenshot:subscribe', false)
              }
            }
          }
        }
      : undefined,
  // Quick Entry: the global-hotkey mini composer window. Main owns the OS
  // shortcut + the persisted preference; the quick window only captures text
  // and hands it back, and the primary renderer submits it through the normal
  // prompt path.
  quickEntry: {
    getSettings: () => ipcRenderer.invoke('pulse:quick-entry:settings:get'),
    setSettings: patch => ipcRenderer.invoke('pulse:quick-entry:settings:set', patch),
    // Invoke returns the delivery result so the draft is not lost (#85590).
    submit: payload => ipcRenderer.invoke('pulse:quick-entry:submit', payload),
    // Main cannot invoke the primary renderer, so it receives this ack (#85590).
    ackSubmit: (correlationId, result) => ipcRenderer.send('pulse:quick-entry:ack', { correlationId, result }),
    dismiss: () => ipcRenderer.send('pulse:quick-entry:dismiss'),
    // Primary renderer → main → quick window: gateway connection state + the
    // recent-session options the target picker offers. Main caches the latest
    // payload so a freshly spawned quick window starts from truth.
    pushState: payload => ipcRenderer.send('pulse:quick-entry:state', payload),
    // Quick window subscribes to those pushes.
    onState: callback => {
      const listener = (_event, payload) => callback(payload)
      ipcRenderer.on('pulse:quick-entry:state', listener)

      return () => ipcRenderer.removeListener('pulse:quick-entry:state', listener)
    },
    // Main → primary renderer: a submit captured by the quick window.
    onSubmit: callback => {
      const listener = (_event, payload) => callback(payload)
      ipcRenderer.on('pulse:quick-entry:submit', listener)

      return () => ipcRenderer.removeListener('pulse:quick-entry:submit', listener)
    },
    // Main → quick window: you were just summoned (reset draft + refocus).
    onShown: callback => {
      const listener = () => callback()
      ipcRenderer.on('pulse:quick-entry:shown', listener)

      return () => ipcRenderer.removeListener('pulse:quick-entry:shown', listener)
    },
    // Main → quick window: the outcome of a submit whose relay already timed
    // out. Delivery is now KNOWN — reconcile the unknown state instead of
    // leaving the user to resend a prompt that may already be delivered.
    onLateResult: callback => {
      const listener = (_event, payload) => callback(payload)
      ipcRenderer.on('pulse:quick-entry:late-result', listener)

      return () => ipcRenderer.removeListener('pulse:quick-entry:late-result', listener)
    }
  },
  getBootProgress: () => ipcRenderer.invoke('pulse:boot-progress:get'),
  getConnectionConfig: profile => ipcRenderer.invoke('pulse:connection-config:get', profile),
  saveConnectionConfig: payload => ipcRenderer.invoke('pulse:connection-config:save', payload),
  applyConnectionConfig: payload => ipcRenderer.invoke('pulse:connection-config:apply', payload),
  testConnectionConfig: payload => ipcRenderer.invoke('pulse:connection-config:test', payload),
  // Opt-in OS-keychain encryption for stored gateway secrets (default off —
  // see secret-storage-policy.ts). get never touches the OS keychain.
  getSecretStorageEncryption: () => ipcRenderer.invoke('pulse:secret-storage:get'),
  setSecretStorageEncryption: (on: boolean) => ipcRenderer.invoke('pulse:secret-storage:set', on),
  // v2 multi-connection registry: named agent sources (local / remote / cloud / ssh).
  connections: {
    list: () => ipcRenderer.invoke('pulse:connections:list'),
    save: payload => ipcRenderer.invoke('pulse:connections:save', payload),
    remove: id => ipcRenderer.invoke('pulse:connections:remove', id),
    setPrimary: id => ipcRenderer.invoke('pulse:connections:set-primary', id),
    setLaunchMode: mode => ipcRenderer.invoke('pulse:connections:set-launch-mode', mode),
    setLastUsed: id => ipcRenderer.invoke('pulse:connections:set-last-used', id),
    test: id => ipcRenderer.invoke('pulse:connections:test', id),
    updateManaged: id => ipcRenderer.invoke('pulse:connections:update-managed', id),
    // Fan out `pulse update` to every eligible registered connection.
    // Optional excludeIds skips rows the caller updates through another path.
    updateAll: options => ipcRenderer.invoke('pulse:connections:update-all', options),
    // Registry lifecycle push (main → renderer): a connection was removed or
    // materially edited, so secondaries scoped to it must be disposed (and,
    // for edits, re-dialed at the new target).
    onChanged: callback => {
      const listener = (_event, payload) => callback(payload)
      ipcRenderer.on('pulse:connections:changed', listener)

      return () => ipcRenderer.removeListener('pulse:connections:changed', listener)
    }
  },
  sshConfigHosts: () => ipcRenderer.invoke('pulse:ssh-config:hosts'),
  sshResolveHost: host => ipcRenderer.invoke('pulse:ssh-config:resolve', host),
  probeConnectionConfig: remoteUrl => ipcRenderer.invoke('pulse:connection-config:probe', remoteUrl),
  // `options` lets a registry-editor draft sign in BEFORE it is saved: the
  // main process settles the draft's connection id up front so the login
  // window writes into the per-connection cookie jar the saved entry will
  // read (not the legacy shared jar an unsaved URL would fall back to).
  oauthLoginConnectionConfig: (remoteUrl, options) =>
    ipcRenderer.invoke('pulse:connection-config:oauth-login', remoteUrl, options),
  oauthLogoutConnectionConfig: remoteUrl => ipcRenderer.invoke('pulse:connection-config:oauth-logout', remoteUrl),
  // PULSE Cloud: one portal login powers discovery + silent per-agent sign-in
  // (cloud-auto-discovery Phase 3).
  cloud: {
    status: () => ipcRenderer.invoke('pulse:cloud:status'),
    login: () => ipcRenderer.invoke('pulse:cloud:login'),
    logout: () => ipcRenderer.invoke('pulse:cloud:logout'),
    discover: org => ipcRenderer.invoke('pulse:cloud:discover', org),
    agentSignIn: dashboardUrl => ipcRenderer.invoke('pulse:cloud:agent-sign-in', dashboardUrl)
  },
  profile: {
    getDefault: () => ipcRenderer.invoke('pulse:profile:default:get'),
    setDefault: (route: DesktopProfileRoute) => ipcRenderer.invoke('pulse:profile:default:set', route),
    onDefaultChanged: (callback: (route: DesktopProfileRoute | null) => void) => {
      const listener = (_event: Electron.IpcRendererEvent, route: DesktopProfileRoute | null) => callback(route)
      ipcRenderer.on('pulse:profile:default:changed', listener)

      return () => ipcRenderer.removeListener('pulse:profile:default:changed', listener)
    },
    get: () => ipcRenderer.invoke('pulse:profile:get'),
    remember: name => ipcRenderer.invoke('pulse:profile:remember', name),
    set: name => ipcRenderer.invoke('pulse:profile:set', name)
  },
  // The handler resolves an expected 404 with a sentinel instead of rejecting
  // (Electron logs a stack for every rejected invoke). Turn it back into the
  // rejection the renderer expects — see electron/api-expected-404.ts.
  api: request => ipcRenderer.invoke('pulse:api', request).then(unwrapExpectedNotFound),
  notify: payload => ipcRenderer.invoke('pulse:notify', payload),
  claimStartupLatency: () => ipcRenderer.invoke('pulse:startup-latency:claim'),
  requestMicrophoneAccess: () => ipcRenderer.invoke('pulse:requestMicrophoneAccess'),
  readWindowBelow: () => ipcRenderer.invoke('pulse:window:readBelow'),
  readFileDataUrl: filePath => ipcRenderer.invoke('pulse:readFileDataUrl', filePath),
  readFileDataUrlForAttach: filePath => ipcRenderer.invoke('pulse:readFileDataUrlForAttach', filePath),
  dataUrlReadMax: {
    get: () => ipcRenderer.invoke('pulse:data-url-read-max:get'),
    set: maxMb => ipcRenderer.invoke('pulse:data-url-read-max:set', maxMb)
  },
  readFileText: filePath => ipcRenderer.invoke('pulse:readFileText', filePath),
  readPluginSource: (filePath: string) => ipcRenderer.invoke('pulse:readPluginSource', filePath),
  selectPaths: options => ipcRenderer.invoke('pulse:selectPaths', options),
  selectSavePath: options => ipcRenderer.invoke('pulse:selectSavePath', options),
  writeClipboard: text => ipcRenderer.invoke('pulse:writeClipboard', text),
  readClipboard: () => ipcRenderer.invoke('pulse:readClipboard'),
  saveGatewayFile: payload => ipcRenderer.invoke('pulse:saveGatewayFile', payload),
  saveImageFromUrl: url => ipcRenderer.invoke('pulse:saveImageFromUrl', url),
  contextMenuEdit: command => ipcRenderer.invoke('pulse:context-menu:edit', command),
  contextMenuCopyImage: () => ipcRenderer.invoke('pulse:context-menu:copy-image'),
  contextMenuSpellcheck: action => ipcRenderer.invoke('pulse:context-menu:spellcheck', action),
  contextMenuGuestAddWord: payload => ipcRenderer.invoke('pulse:context-menu:guest-add-word', payload),
  onContextMenuSpellcheck: callback => {
    const listener = (_event, payload) => callback(payload)
    ipcRenderer.on('pulse:context-menu-spellcheck', listener)

    return () => ipcRenderer.removeListener('pulse:context-menu-spellcheck', listener)
  },
  saveImageBuffer: (data, ext, name) => ipcRenderer.invoke('pulse:saveImageBuffer', { data, ext, name }),
  capturePreview: payload => ipcRenderer.invoke('pulse:capturePreview', payload),
  savePastedText: text => ipcRenderer.invoke('pulse:savePastedText', { text }),
  saveClipboardImage: () => ipcRenderer.invoke('pulse:saveClipboardImage'),
  getPathForFile: file => {
    try {
      return webUtils.getPathForFile(file) || ''
    } catch {
      return ''
    }
  },
  normalizePreviewTarget: (target, baseDir) => ipcRenderer.invoke('pulse:normalizePreviewTarget', target, baseDir),
  watchPreviewFile: url => ipcRenderer.invoke('pulse:watchPreviewFile', url),
  watchDirectory: dir => ipcRenderer.invoke('pulse:watchDirectory', dir),
  stopPreviewFileWatch: id => ipcRenderer.invoke('pulse:stopPreviewFileWatch', id),
  setActiveWork: payload => ipcRenderer.send('pulse:active-work', payload),
  setTitleBarTheme: payload => ipcRenderer.send('pulse:titlebar-theme', payload),
  setNativeTheme: mode => ipcRenderer.send('pulse:native-theme', mode),
  setTranslucency: payload => ipcRenderer.send('pulse:translucency', payload),
  setKeepAwake: on => ipcRenderer.send('pulse:keep-awake', on),
  minimizeToTray: {
    get: () => ipcRenderer.invoke('pulse:minimize-to-tray:get'),
    set: on => ipcRenderer.invoke('pulse:minimize-to-tray:set', on),
    onChanged: callback => {
      const listener = (_event, status) => callback(status)
      ipcRenderer.on('pulse:minimize-to-tray:changed', listener)

      return () => ipcRenderer.removeListener('pulse:minimize-to-tray:changed', listener)
    }
  },
  setDisableF12: blocked => ipcRenderer.send('pulse:devtools:disable-f12', blocked),
  setF12ShortcutActive: active => ipcRenderer.send('pulse:f12ShortcutActive', Boolean(active)),
  onF12Shortcut: callback => {
    const listener = (_event, input) => callback(input)
    ipcRenderer.on('pulse:f12-shortcut', listener)

    return () => ipcRenderer.removeListener('pulse:f12-shortcut', listener)
  },
  setPreviewShortcutActive: active => ipcRenderer.send('pulse:previewShortcutActive', Boolean(active)),
  openExternal: url => ipcRenderer.invoke('pulse:openExternal', url),
  mcpOauth: {
    // One-shot loopback listener for MCP OAuth against remote backends: bind
    // on this machine, hand redirectUri to mcp.servers.oauth.start, then wait
    // for the provider redirect and relay code/state via oauth.callback.
    listen: () => ipcRenderer.invoke('pulse:mcp-oauth:listen'),
    wait: (id, timeoutMs) => ipcRenderer.invoke('pulse:mcp-oauth:wait', id, timeoutMs),
    cancel: id => ipcRenderer.invoke('pulse:mcp-oauth:cancel', id)
  },
  openPreviewInBrowser: url => ipcRenderer.invoke('pulse:openPreviewInBrowser', url),
  reachPreviewUrl: url => ipcRenderer.invoke('pulse:preview:reach', url),
  setActiveConnectionRoute: route => ipcRenderer.send('pulse:connection:active-route', route),
  fetchLinkTitle: url => ipcRenderer.invoke('pulse:fetchLinkTitle', url),
  resolveFavicon: url => ipcRenderer.invoke('pulse:resolveFavicon', url),
  sanitizeWorkspaceCwd: cwd => ipcRenderer.invoke('pulse:workspace:sanitize', cwd),
  settings: {
    getDefaultProjectDir: () => ipcRenderer.invoke('pulse:setting:defaultProjectDir:get'),
    setDefaultProjectDir: dir => ipcRenderer.invoke('pulse:setting:defaultProjectDir:set', dir),
    pickDefaultProjectDir: () => ipcRenderer.invoke('pulse:setting:defaultProjectDir:pick')
  },
  zoom: {
    // Current zoom of this window, as { level, percent }.
    get: () => ipcRenderer.invoke('pulse:zoom:get'),
    // Synchronous zoom factor (1 = 100%). Coordinate math needs it in the
    // same tick as the event it converts, so no IPC round-trip here.
    factor: () => webFrame.getZoomFactor(),
    setPercent: percent => ipcRenderer.send('pulse:zoom:set-percent', percent),
    // Fires on every zoom change, including the Ctrl/Cmd +/-/0 shortcuts,
    // so the settings UI can stay in sync with the keyboard.
    onChanged: callback => {
      const listener = (_event, payload) => callback(payload)
      ipcRenderer.on('pulse:zoom:changed', listener)

      return () => ipcRenderer.removeListener('pulse:zoom:changed', listener)
    }
  },
  revealLogs: () => ipcRenderer.invoke('pulse:logs:reveal'),
  getRecentLogs: () => ipcRenderer.invoke('pulse:logs:recent'),
  // Fire-and-forget: persists a renderer error-boundary catch (with component
  // stack) to desktop.log so crashes survive the window (#79428).
  reportRendererError: report => ipcRenderer.send('pulse:logs:renderer-error', report),
  logLine: (line: string): void => ipcRenderer.send('pulse:logs:renderer-line', line),
  readDir: dirPath => ipcRenderer.invoke('pulse:fs:readDir', dirPath),
  gitRoot: startPath => ipcRenderer.invoke('pulse:fs:gitRoot', startPath),
  revealPath: targetPath => ipcRenderer.invoke('pulse:fs:reveal', targetPath),
  openDir: dirPath => ipcRenderer.invoke('pulse:fs:openDir', dirPath),
  desktopPluginsRoot: () => ipcRenderer.invoke('pulse:fs:desktopPluginsRoot'),
  reconcileDesktopPlugins: () => ipcRenderer.invoke('pulse:fs:reconcileDesktopPlugins'),
  logsRoot: (profile?: string) => ipcRenderer.invoke('pulse:fs:logsRoot', profile),
  renamePath: (targetPath, newName) => ipcRenderer.invoke('pulse:fs:rename', targetPath, newName),
  writeTextFile: (filePath, content) => ipcRenderer.invoke('pulse:fs:writeText', filePath, content),
  trashPath: targetPath => ipcRenderer.invoke('pulse:fs:trash', targetPath),
  git: {
    worktreeList: repoPath => ipcRenderer.invoke('pulse:git:worktreeList', repoPath),
    worktreeAdd: (repoPath, options) => ipcRenderer.invoke('pulse:git:worktreeAdd', repoPath, options),
    worktreeRemove: (repoPath, worktreePath, options) =>
      ipcRenderer.invoke('pulse:git:worktreeRemove', repoPath, worktreePath, options),
    branchSwitch: (repoPath, branch) => ipcRenderer.invoke('pulse:git:branchSwitch', repoPath, branch),
    branchList: repoPath => ipcRenderer.invoke('pulse:git:branchList', repoPath),
    baseBranchList: repoPath => ipcRenderer.invoke('pulse:git:baseBranchList', repoPath),
    repoStatus: repoPath => ipcRenderer.invoke('pulse:git:repoStatus', repoPath),
    fileDiff: (repoPath, filePath) => ipcRenderer.invoke('pulse:git:fileDiff', repoPath, filePath),
    scanRepos: (roots, options) => ipcRenderer.invoke('pulse:git:scanRepos', roots, options),
    review: {
      list: (repoPath, scope, baseRef) => ipcRenderer.invoke('pulse:git:review:list', repoPath, scope, baseRef),
      diff: (repoPath, filePath, scope, baseRef, staged) =>
        ipcRenderer.invoke('pulse:git:review:diff', repoPath, filePath, scope, baseRef, staged),
      stage: (repoPath, filePath) => ipcRenderer.invoke('pulse:git:review:stage', repoPath, filePath),
      unstage: (repoPath, filePath) => ipcRenderer.invoke('pulse:git:review:unstage', repoPath, filePath),
      revert: (repoPath, filePath) => ipcRenderer.invoke('pulse:git:review:revert', repoPath, filePath),
      revParse: (repoPath, ref) => ipcRenderer.invoke('pulse:git:review:revParse', repoPath, ref),
      commit: (repoPath, message, push) => ipcRenderer.invoke('pulse:git:review:commit', repoPath, message, push),
      commitContext: repoPath => ipcRenderer.invoke('pulse:git:review:commitContext', repoPath),
      push: repoPath => ipcRenderer.invoke('pulse:git:review:push', repoPath),
      shipInfo: repoPath => ipcRenderer.invoke('pulse:git:review:shipInfo', repoPath),
      prList: (repoPath, branches, numbers) =>
        ipcRenderer.invoke('pulse:git:review:prList', repoPath, branches, numbers),
      createPr: repoPath => ipcRenderer.invoke('pulse:git:review:createPr', repoPath)
    }
  },
  terminal: {
    attach: id => ipcRenderer.invoke('pulse:terminal:attach', id),
    cwd: id => ipcRenderer.invoke('pulse:terminal:cwd', id),
    dispose: id => ipcRenderer.invoke('pulse:terminal:dispose', id),
    resize: (id, size) => ipcRenderer.invoke('pulse:terminal:resize', id, size),
    start: options => ipcRenderer.invoke('pulse:terminal:start', options),
    write: (id, data) => ipcRenderer.invoke('pulse:terminal:write', id, data),
    onData: (id, callback) => {
      const channel = `pulse:terminal:${id}:data`
      const listener = (_event, payload) => callback(payload)
      ipcRenderer.on(channel, listener)

      return () => ipcRenderer.removeListener(channel, listener)
    },
    onExit: (id, callback) => {
      const channel = `pulse:terminal:${id}:exit`
      const listener = (_event, payload) => callback(payload)
      ipcRenderer.on(channel, listener)

      return () => ipcRenderer.removeListener(channel, listener)
    }
  },
  onClosePreviewRequested: callback => {
    const listener = () => callback()
    ipcRenderer.on('pulse:close-preview-requested', listener)

    return () => ipcRenderer.removeListener('pulse:close-preview-requested', listener)
  },
  onPreviewNav: callback => {
    const listener = (_event, command) => callback(command)
    ipcRenderer.on('pulse:preview-nav', listener)

    return () => ipcRenderer.removeListener('pulse:preview-nav', listener)
  },
  onOpenFolderRequested: callback => {
    const listener = () => callback()
    ipcRenderer.on('pulse:open-folder-requested', listener)

    return () => ipcRenderer.removeListener('pulse:open-folder-requested', listener)
  },
  onOpenUpdatesRequested: callback => {
    const listener = () => callback()
    ipcRenderer.on('pulse:open-updates', listener)

    return () => ipcRenderer.removeListener('pulse:open-updates', listener)
  },
  onDeepLink: callback => {
    const listener = (_event, payload) => callback(payload)
    ipcRenderer.on('pulse:deep-link', listener)

    return () => ipcRenderer.removeListener('pulse:deep-link', listener)
  },
  signalDeepLinkReady: () => ipcRenderer.invoke('pulse:deep-link-ready'),
  probePluginRepo: payload => ipcRenderer.invoke('pulse:plugin:probe', payload),
  installDesktopPlugin: payload => ipcRenderer.invoke('pulse:plugin:installDesktop', payload),
  removeDesktopPlugin: payload => ipcRenderer.invoke('pulse:plugin:removeDesktop', payload),
  onWindowStateChanged: callback => {
    const listener = (_event, payload) => callback(payload)
    ipcRenderer.on('pulse:window-state-changed', listener)

    return () => ipcRenderer.removeListener('pulse:window-state-changed', listener)
  },
  onFocusSession: callback => {
    const listener = (_event, sessionId) => callback(sessionId)
    ipcRenderer.on('pulse:focus-session', listener)

    return () => ipcRenderer.removeListener('pulse:focus-session', listener)
  },
  onNotificationAction: callback => {
    const listener = (_event, payload) => callback(payload)
    ipcRenderer.on('pulse:notification-action', listener)

    return () => ipcRenderer.removeListener('pulse:notification-action', listener)
  },
  onNotificationActivate: callback => {
    const listener = (_event, payload) => callback(payload)
    ipcRenderer.on('pulse:notification-activate', listener)

    return () => ipcRenderer.removeListener('pulse:notification-activate', listener)
  },
  onExternalOpenFailed: callback => {
    const listener = (_event, payload) => callback(payload)
    ipcRenderer.on('pulse:external-open-failed', listener)

    return () => ipcRenderer.removeListener('pulse:external-open-failed', listener)
  },
  onPreviewFileChanged: callback => {
    const listener = (_event, payload) => callback(payload)
    ipcRenderer.on('pulse:preview-file-changed', listener)

    return () => ipcRenderer.removeListener('pulse:preview-file-changed', listener)
  },
  onBackendExit: callback => {
    const listener = (_event, payload) => callback(payload)
    ipcRenderer.on('pulse:backend-exit', listener)

    return () => ipcRenderer.removeListener('pulse:backend-exit', listener)
  },
  // Cooperative pool retirement (main → renderer): the pooled backend under
  // `poolKey` is being stopped for a foreground open. Park that scope; do not
  // redial into the slot it vacated.
  onPoolBackendRetiring: callback => {
    const listener = (_event, payload) => callback(payload)
    ipcRenderer.on('pulse:pool:retiring', listener)

    return () => ipcRenderer.removeListener('pulse:pool:retiring', listener)
  },
  // Soft gateway-mode apply finished tearing down the primary backend. Renderer
  // should wipe session lists + re-dial without a window reload.
  onConnectionApplied: callback => {
    const listener = () => callback()
    ipcRenderer.on('pulse:connection:applied', listener)

    return () => ipcRenderer.removeListener('pulse:connection:applied', listener)
  },
  onPowerResume: callback => {
    const listener = () => callback()
    ipcRenderer.on('pulse:power-resume', listener)

    return () => ipcRenderer.removeListener('pulse:power-resume', listener)
  },
  // AC ↔ battery transitions; renderers slow their backstop polls on battery.
  getOnBattery: () => ipcRenderer.invoke('pulse:power-battery:get'),
  onBatteryChanged: callback => {
    const listener = (_event, onBattery) => callback(Boolean(onBattery))
    ipcRenderer.on('pulse:power-battery', listener)

    return () => ipcRenderer.removeListener('pulse:power-battery', listener)
  },
  onBootProgress: callback => {
    const listener = (_event, payload) => callback(payload)
    ipcRenderer.on('pulse:boot-progress', listener)

    return () => ipcRenderer.removeListener('pulse:boot-progress', listener)
  },
  // First-launch bootstrap progress -- emitted by the install.ps1 stage
  // runner in main.ts (apps/desktop/electron/bootstrap-runner.ts).
  // Renderer's install overlay subscribes to live events and queries the
  // current snapshot via getBootstrapState() to recover after a devtools
  // reload mid-bootstrap.
  getBootstrapState: () => ipcRenderer.invoke('pulse:bootstrap:get'),
  probeLocalBackend: () => ipcRenderer.invoke('pulse:local-backend:probe'),
  continueBootstrapLocal: () => ipcRenderer.invoke('pulse:bootstrap:continue-local'),
  recycleBackend: profile => ipcRenderer.invoke('pulse:backend:recycle', profile),
  resetBootstrap: () => ipcRenderer.invoke('pulse:bootstrap:reset'),
  repairBootstrap: () => ipcRenderer.invoke('pulse:bootstrap:repair'),
  cancelBootstrap: () => ipcRenderer.invoke('pulse:bootstrap:cancel'),
  onBootstrapEvent: callback => {
    const listener = (_event, payload) => callback(payload)
    ipcRenderer.on('pulse:bootstrap:event', listener)

    return () => ipcRenderer.removeListener('pulse:bootstrap:event', listener)
  },
  getVersion: () => ipcRenderer.invoke('pulse:version'),
  relaunchApp: () => ipcRenderer.invoke('pulse:app:relaunch'),
  getMachineProfile: () => ipcRenderer.invoke('pulse:machine:profile'),
  getRemoteDisplayReason: () => ipcRenderer.invoke('pulse:get-remote-display-reason'),
  uninstall: {
    summary: () => ipcRenderer.invoke('pulse:uninstall:summary'),
    run: mode => ipcRenderer.invoke('pulse:uninstall:run', { mode })
  },
  updates: {
    check: opts => ipcRenderer.invoke('pulse:updates:check', opts),
    apply: opts => ipcRenderer.invoke('pulse:updates:apply', opts),
    getBranch: () => ipcRenderer.invoke('pulse:updates:branch:get'),
    setBranch: name => ipcRenderer.invoke('pulse:updates:branch:set', name),
    onProgress: callback => {
      const listener = (_event, payload) => callback(payload)
      ipcRenderer.on('pulse:updates:progress', listener)

      return () => ipcRenderer.removeListener('pulse:updates:progress', listener)
    },
    takePendingRun: () => ipcRenderer.invoke('pulse:updates:metric:take'),
    ackPendingRun: sent => ipcRenderer.invoke('pulse:updates:metric:ack', sent),
    onPendingRun: callback => {
      const listener = () => callback()
      ipcRenderer.on('pulse:updates:metric:pending', listener)

      return () => ipcRenderer.removeListener('pulse:updates:metric:pending', listener)
    }
  },
  desktopMetrics: {
    setEnabled: (on, profile) => ipcRenderer.invoke('pulse:desktop-metrics:set-enabled', on, profile),
    takeRendererCrashes: () => ipcRenderer.invoke('pulse:desktop-metrics:crash:take'),
    ackRendererCrashes: sent => ipcRenderer.invoke('pulse:desktop-metrics:crash:ack', sent)
  },
  themes: {
    fetchMarketplace: id => ipcRenderer.invoke('pulse:vscode-theme:fetch', id),
    searchMarketplace: query => ipcRenderer.invoke('pulse:vscode-theme:search', query)
  },
  // Find-in-page (Ctrl/Cmd+F): delegates to Electron's
  // webContents.findInPage on the IPC sender's window so a Cmd+F pressed
  // in a secondary session window searches THAT window, not the primary.
  // `onFoundInPage` returns the unsubscribe fn; the renderer wires it via
  // `initFindInPageListener` in store/find-in-page.ts and tears it down
  // when the FindBar unmounts.
  findInPage: (query, options) => ipcRenderer.invoke('pulse:find-in-page', query, options),
  stopFindInPage: () => ipcRenderer.invoke('pulse:stop-find-in-page'),
  onFoundInPage: callback => {
    const listener = (_event, result) => callback(result)
    ipcRenderer.on('pulse:found-in-page', listener)

    return () => ipcRenderer.removeListener('pulse:found-in-page', listener)
  },
  // Main-process `before-input-event` forwards Ctrl/Cmd+F here so renderer
  // can open the FindBar even when the GTK compositor has already grabbed
  // the chord at the windowing layer (#81727).
  onOpenFindBarRequested: callback => {
    const listener = () => callback()
    ipcRenderer.on('pulse:open-find-bar', listener)

    return () => ipcRenderer.removeListener('pulse:open-find-bar', listener)
  }
})
