export type BrainCategory = 'all' | 'self' | 'user' | 'concept' | 'belief' | 'project'

export interface BrainNodeSummary {
  id: string
  label: string
  category: string
  confidence?: number
  status?: string
  tags?: string[]
  timestamp?: null | number
  path?: string
  wikilinksCount?: number
  backlinksCount?: number
}

export interface BrainNodeDetail {
  id: string
  label: string
  category: string
  content: string
  confidence?: number
  status?: string
  tags?: string[]
  wikilinks?: string[]
  backlinks?: string[]
  aliases?: string[]
  supersedes?: string | null
  superseded_by?: string | null
}

export interface BrainBacklink {
  source_id: string
  source_title: string
  count: number
}

export interface BrainUnlinkedMention {
  source_id: string
  source_title: string
  matched_term: string
  snippet: string
  line_number: number
}
