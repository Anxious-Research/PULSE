import type {
  OfficialSkillInfo,
  SkillHubPreview,
  SkillHubScanResult,
  SkillHubSearchResponse,
  SkillHubSourcesResponse,
  SkillInfo,
  StarmapGraph
} from '@/types/pulse'
import type { ActionResponse } from '@/types/pulse'

import { capabilityScoped, pulseApi, type ProfileScope, profileScoped } from './client'

export function getSkills(profile?: ProfileScope): Promise<SkillInfo[]> {
  return window.pulseDesktop.api<SkillInfo[]>({
    ...capabilityScoped(profile),
    path: '/api/skills'
  })
}

/** Raw SKILL.md text (frontmatter included) for ANY skill — bundled, hub, or
 *  learned — backing the Capabilities detail pane's full-skill view. */
export function getSkillContent(
  name: string,
  profile?: ProfileScope
): Promise<{ content: string; name: string; path: string }> {
  return window.pulseDesktop.api<{ content: string; name: string; path: string }>({
    ...capabilityScoped(profile),
    path: `/api/skills/content?name=${encodeURIComponent(name)}`
  })
}

export function setSkillEnabled(
  name: string,
  enabled: boolean,
  profile?: ProfileScope
): Promise<{ ok: boolean; name: string; enabled: boolean }> {
  return window.pulseDesktop.api<{ ok: boolean; name: string; enabled: boolean }>({
    ...capabilityScoped(profile),
    path: '/api/skills/toggle',
    method: 'PUT',
    body: { name, enabled }
  })
}

export function getStarmapGraph(): Promise<StarmapGraph> {
  return pulseApi<StarmapGraph>({
    ...profileScoped(),
    // Backend REST contract — stays /api/learning even though the UI feature is
    // now "star map". Renaming this would break against an un-upgraded backend.
    path: '/api/learning/graph'
  })
}

export interface LearningNodeDetail {
  content: string
  kind: 'memory' | 'skill'
  label: string
  ok: boolean
}

export function getLearningNode(id: string, profile?: ProfileScope): Promise<LearningNodeDetail> {
  return window.pulseDesktop.api<LearningNodeDetail>({
    ...capabilityScoped(profile),
    path: `/api/learning/node?id=${encodeURIComponent(id)}`
  })
}

/** A resolved reference to another node (evidence, counter-evidence, relationship), with a
 *  human label so the inspector can render it without a second round-trip. */
export interface BrainNodeRef {
  id: string
  label: string
  missing: boolean
  category?: string
  status?: string
  snippet?: string
  kind?: string
  provenance?: string
  confidence?: number
}

/** Full evidence provenance for one brain vault node, from GET /api/brain/node. Shape mirrors
 *  agent.brain.inspect.inspect_node — fields present depend on `kind` (belief/procedure/memory). */
export interface BrainNodeDetail {
  ok: boolean
  message?: string
  id?: string
  label?: string
  kind?: 'belief' | 'memory' | 'procedure'
  category?: string
  content?: string
  status?: string
  confidence?: number
  salience?: number
  stability?: number
  timestamps?: { created_at?: null | number; updated_at?: null | number; last_accessed?: null | number; access_count?: number }
  source_turn?: null | string
  superseded_by?: BrainNodeRef
  supersedes?: BrainNodeRef[]
  corroborations?: { at?: number; text?: string; turn?: string }[]
  corrections?: Record<string, unknown>[]
  relationships?: BrainNodeRef[]
  // belief
  belief_status?: string
  confidence_justification?: string
  evidence?: BrainNodeRef[]
  counter_evidence?: BrainNodeRef[]
  revisions?: { at?: number; reason?: string; from?: number; to?: number }[]
  asserted?: boolean
  // procedure
  goal?: string
  procedure_status?: string
  success_rate?: number
  counts?: { success: number; failure: number; partial: number }
  steps?: string[]
  preconditions?: string[]
  failure_modes?: string[]
  discouraged?: string[]
  reconsider_when?: string[]
  outcomes?: Record<string, unknown>[]
  experience_refs?: BrainNodeRef[]
}

/** Inspector data for a selected brain node — content, evidence, provenance, history. `id` is the
 *  vault node id (the graph node's `vaultId`). */
export function getBrainNodeDetail(id: string, profile?: ProfileScope): Promise<BrainNodeDetail> {
  return window.pulseDesktop.api<BrainNodeDetail>({
    ...capabilityScoped(profile),
    path: `/api/brain/node?id=${encodeURIComponent(id)}`
  })
}

export function deleteLearningNode(id: string, profile?: ProfileScope): Promise<{ message: string; ok: boolean }> {
  return window.pulseDesktop.api<{ message: string; ok: boolean }>({
    ...capabilityScoped(profile),
    path: '/api/learning/node',
    method: 'DELETE',
    body: { id }
  })
}

export function editLearningNode(
  id: string,
  content: string,
  profile?: ProfileScope
): Promise<{ message: string; ok: boolean }> {
  return window.pulseDesktop.api<{ message: string; ok: boolean }>({
    ...capabilityScoped(profile),
    path: '/api/learning/node',
    method: 'PUT',
    body: { content, id }
  })
}

/** §7: create the note a ghost node stands for — an unresolved [[wikilink]]
 *  becomes a real note under the linked title, so the links resolve. */
export function resolveGhostNode(
  id: string,
  content: string,
  profile?: ProfileScope,
  category?: string
): Promise<{ category: string; id: string; message: string; ok: boolean; title: string }> {
  return window.pulseDesktop.api<{ category: string; id: string; message: string; ok: boolean; title: string }>({
    ...capabilityScoped(profile),
    path: '/api/learning/ghost',
    method: 'POST',
    body: { category, content, id }
  })
}

// ---------------------------------------------------------------------------
// Skills hub — search / preview / scan / install (parity with `pulse skills`
// and the dashboard's Browse-hub tab). Installs spawn background actions whose
// logs are tailed via getActionStatus().
// ---------------------------------------------------------------------------

const HUB_REQUEST_TIMEOUT_MS = 45_000

/** The full built-in optional-skills catalog (local checkout scan — fast),
 *  with per-profile installed flags. Feeds the Capabilities Skills list's
 *  "available to install" rows. */
export function getOfficialSkills(profile?: ProfileScope): Promise<{ skills: OfficialSkillInfo[] }> {
  return window.pulseDesktop.api<{ skills: OfficialSkillInfo[] }>({
    ...capabilityScoped(profile),
    path: '/api/skills/hub/official'
  })
}

export function getSkillHubSources(profile?: null | string): Promise<SkillHubSourcesResponse> {
  return pulseApi<SkillHubSourcesResponse>({
    ...profileScoped(profile),
    path: '/api/skills/hub/sources',
    timeoutMs: HUB_REQUEST_TIMEOUT_MS
  })
}

export function searchSkillsHub(
  query: string,
  source = 'all',
  limit = 20,
  profile?: null | string
): Promise<SkillHubSearchResponse> {
  const params = new URLSearchParams({ q: query, source, limit: String(limit) })

  return pulseApi<SkillHubSearchResponse>({
    ...profileScoped(profile),
    path: `/api/skills/hub/search?${params.toString()}`,
    timeoutMs: HUB_REQUEST_TIMEOUT_MS
  })
}

export function previewSkillHub(identifier: string, profile?: ProfileScope): Promise<SkillHubPreview> {
  return window.pulseDesktop.api<SkillHubPreview>({
    ...capabilityScoped(profile),
    path: `/api/skills/hub/preview?identifier=${encodeURIComponent(identifier)}`,
    timeoutMs: HUB_REQUEST_TIMEOUT_MS
  })
}

export function scanSkillHub(identifier: string, profile?: null | string): Promise<SkillHubScanResult> {
  return pulseApi<SkillHubScanResult>({
    ...profileScoped(profile),
    path: `/api/skills/hub/scan?identifier=${encodeURIComponent(identifier)}`,
    timeoutMs: HUB_REQUEST_TIMEOUT_MS
  })
}

export function installSkillFromHub(identifier: string, profile?: ProfileScope): Promise<ActionResponse> {
  return window.pulseDesktop.api<ActionResponse>({
    ...capabilityScoped(profile),
    path: '/api/skills/hub/install',
    method: 'POST',
    body: { identifier }
  })
}

export function uninstallSkillFromHub(name: string, profile?: ProfileScope): Promise<ActionResponse> {
  return window.pulseDesktop.api<ActionResponse>({
    ...capabilityScoped(profile),
    path: '/api/skills/hub/uninstall',
    method: 'POST',
    body: { name }
  })
}

export function updateSkillsFromHub(profile?: ProfileScope): Promise<ActionResponse> {
  return window.pulseDesktop.api<ActionResponse>({
    ...capabilityScoped(profile),
    path: '/api/skills/hub/update',
    method: 'POST',
    body: {}
  })
}
