import { useMemo, useState } from 'react'

import { Button } from '@/components/ui/button'
import { SearchField } from '@/components/ui/search-field'
import { cn } from '@/lib/utils'

import type { BrainCategory, BrainNodeDetail, BrainNodeSummary } from './types'

interface BrainExplorerProps {
  nodes: BrainNodeSummary[]
  activeNode: BrainNodeDetail | null
  loading: boolean
  onSelectNode: (nodeId: string) => void
  onOpenStarmap?: () => void
}

const CATEGORIES: Array<{ id: BrainCategory; label: string; icon: string }> = [
  { id: 'all', label: 'All Knowledge', icon: '🧠' },
  { id: 'self', label: 'Self & Anatomy', icon: '🤖' },
  { id: 'user', label: 'User & Life', icon: '👤' },
  { id: 'concept', label: 'Concepts', icon: '💡' },
  { id: 'belief', label: 'Beliefs & Hypotheses', icon: '⚖️' },
  { id: 'project', label: 'Projects', icon: '📁' }
]

export function BrainExplorer({
  nodes,
  activeNode,
  loading,
  onSelectNode,
  onOpenStarmap
}: BrainExplorerProps) {
  const [selectedCategory, setSelectedCategory] = useState<BrainCategory>('all')
  const [searchQuery, setSearchQuery] = useState('')

  const filteredNodes = useMemo(() => {
    return nodes.filter(node => {
      const matchesCategory = selectedCategory === 'all' || node.category === selectedCategory
      const q = searchQuery.toLowerCase().trim()
      const matchesQuery =
        !q ||
        node.label.toLowerCase().includes(q) ||
        node.id.toLowerCase().includes(q) ||
        (node.tags && node.tags.some(t => t.toLowerCase().includes(q)))

      return matchesCategory && matchesQuery
    })
  }, [nodes, selectedCategory, searchQuery])

  // Parse markdown body and convert [[links]] to clickable spans
  const renderedContent = useMemo(() => {
    if (!activeNode?.content) return null

    const text = activeNode.content
    // Split by [[wikilink]]
    const parts = text.split(/(\[\[[^\]\n]+\]\])/g)

    return parts.map((part, idx) => {
      const match = part.match(/^\[\[(?<target>[^\]\|#\n]+)(?:#(?<heading>[^\]\|\n]+))?(?:\|(?<alias>[^\]\n]+))?\]\]$/)
      if (match && match.groups) {
        const target = match.groups.target.trim()
        const alias = match.groups.alias ? match.groups.alias.trim() : target
        return (
          <button
            key={idx}
            className="inline-flex items-center text-primary underline underline-offset-2 hover:opacity-80 font-medium px-1 py-0.5 rounded bg-primary/10 transition-colors"
            type="button"
            onClick={() => onSelectNode(target)}
          >
            {alias}
          </button>
        )
      }
      return <span key={idx}>{part}</span>
    })
  }, [activeNode, onSelectNode])

  return (
    <div className="flex h-full w-full overflow-hidden bg-background text-foreground">
      {/* ── Left Column: Categories & Note Explorer ──────────────────────── */}
      <div className="flex flex-col w-80 border-r border-border bg-card/40 shrink-0">
        <div className="p-3 border-b border-border space-y-2">
          <div className="flex items-center justify-between">
            <span className="font-semibold text-sm flex items-center gap-1.5">
              <span>🧠</span> PULSE Brain Vault
            </span>
            {onOpenStarmap && (
              <Button size="sm" variant="outline" onClick={onOpenStarmap}>
                StarMap
              </Button>
            )}
          </div>
          <SearchField
            placeholder="Search notes, concepts, tags..."
            value={searchQuery}
            onChange={e => setSearchQuery(e.target.value)}
          />
        </div>

        {/* Category Filter Pills */}
        <div className="flex flex-wrap gap-1 p-2 border-b border-border bg-muted/20">
          {CATEGORIES.map(cat => (
            <button
              key={cat.id}
              className={cn(
                'text-xs px-2 py-1 rounded-md transition-colors flex items-center gap-1',
                selectedCategory === cat.id
                  ? 'bg-primary text-primary-foreground font-medium'
                  : 'hover:bg-muted text-muted-foreground'
              )}
              type="button"
              onClick={() => setSelectedCategory(cat.id)}
            >
              <span>{cat.icon}</span>
              <span>{cat.label}</span>
            </button>
          ))}
        </div>

        {/* Notes List */}
        <div className="flex-1 overflow-y-auto divide-y divide-border/50">
          {filteredNodes.length === 0 ? (
            <div className="p-4 text-center text-xs text-muted-foreground">
              No notes found matching your filter.
            </div>
          ) : (
            filteredNodes.map(node => {
              const isSelected = activeNode?.id === node.id
              return (
                <button
                  key={node.id}
                  className={cn(
                    'w-full text-left p-2.5 transition-colors flex flex-col gap-1',
                    isSelected ? 'bg-primary/15 border-l-2 border-primary' : 'hover:bg-muted/40'
                  )}
                  type="button"
                  onClick={() => onSelectNode(node.id)}
                >
                  <div className="flex items-center justify-between">
                    <span className="text-sm font-medium truncate">{node.label}</span>
                    {node.status && (
                      <span
                        className={cn(
                          'text-[10px] px-1.5 py-0.2 rounded-full uppercase tracking-wider',
                          node.status === 'active'
                            ? 'bg-emerald-500/15 text-emerald-600 dark:text-emerald-400'
                            : node.status === 'superseded'
                              ? 'bg-amber-500/15 text-amber-600 dark:text-amber-400'
                              : 'bg-rose-500/15 text-rose-600 dark:text-rose-400'
                        )}
                      >
                        {node.status}
                      </span>
                    )}
                  </div>
                  <div className="flex items-center justify-between text-xs text-muted-foreground">
                    <span className="capitalize">{node.category}</span>
                    {node.backlinksCount !== undefined && node.backlinksCount > 0 && (
                      <span className="text-[11px] opacity-80">
                        {node.backlinksCount} {node.backlinksCount === 1 ? 'backlink' : 'backlinks'}
                      </span>
                    )}
                  </div>
                  {node.tags && node.tags.length > 0 && (
                    <div className="flex flex-wrap gap-1 mt-0.5">
                      {node.tags.slice(0, 3).map(tag => (
                        <span key={tag} className="text-[10px] text-muted-foreground bg-muted/60 px-1 rounded">
                          #{tag}
                        </span>
                      ))}
                    </div>
                  )}
                </button>
              )
            })
          )}
        </div>
      </div>

      {/* ── Center Column: Active Note Inspector ─────────────────────────── */}
      <div className="flex-1 flex flex-col min-w-0 overflow-y-auto">
        {loading ? (
          <div className="flex-1 flex items-center justify-center text-sm text-muted-foreground">
            Loading note...
          </div>
        ) : !activeNode ? (
          <div className="flex-1 flex flex-col items-center justify-center text-muted-foreground p-8 text-center">
            <span className="text-3xl mb-2">🧠</span>
            <span className="font-medium text-base">Select a note to inspect</span>
            <span className="text-xs text-muted-foreground/80 max-w-sm mt-1">
              Explore PULSE&apos;s self-awareness, evolving beliefs, user knowledge, and bi-directional link network.
            </span>
          </div>
        ) : (
          <div className="p-6 max-w-3xl space-y-4">
            {/* Note Header */}
            <div className="border-b border-border pb-4 space-y-2">
              <div className="flex items-center gap-2">
                <span className="text-xs font-semibold px-2 py-0.5 rounded bg-muted uppercase tracking-wider">
                  {activeNode.category}
                </span>
                {activeNode.status && (
                  <span
                    className={cn(
                      'text-xs font-semibold px-2 py-0.5 rounded uppercase tracking-wider',
                      activeNode.status === 'active'
                        ? 'bg-emerald-500/15 text-emerald-600 dark:text-emerald-400'
                        : activeNode.status === 'superseded'
                          ? 'bg-amber-500/15 text-amber-600 dark:text-amber-400'
                          : 'bg-rose-500/15 text-rose-600 dark:text-rose-400'
                    )}
                  >
                    {activeNode.status}
                  </span>
                )}
                {activeNode.confidence !== undefined && (
                  <span className="text-xs text-muted-foreground">
                    {Math.round(activeNode.confidence * 100)}% Confidence
                  </span>
                )}
              </div>
              <h1 className="text-2xl font-bold tracking-tight">{activeNode.label}</h1>
              {activeNode.tags && activeNode.tags.length > 0 && (
                <div className="flex flex-wrap gap-1.5 pt-1">
                  {activeNode.tags.map(tag => (
                    <span key={tag} className="text-xs bg-muted px-1.5 py-0.5 rounded font-mono text-muted-foreground">
                      #{tag}
                    </span>
                  ))}
                </div>
              )}
            </div>

            {/* Note Body */}
            <div className="prose prose-sm dark:prose-invert max-w-none leading-relaxed whitespace-pre-wrap font-sans text-sm">
              {renderedContent}
            </div>
          </div>
        )}
      </div>

      {/* ── Right Column: Backlinks & Forward Links Panel ────────────────── */}
      {activeNode && (
        <div className="w-72 border-l border-border bg-card/30 flex flex-col shrink-0 overflow-y-auto p-4 space-y-6">
          {/* Backlinks */}
          <div>
            <h3 className="text-xs font-semibold text-muted-foreground uppercase tracking-wider mb-2 flex items-center justify-between">
              <span>Incoming Backlinks</span>
              <span>{activeNode.backlinks?.length ?? 0}</span>
            </h3>
            {(!activeNode.backlinks || activeNode.backlinks.length === 0) ? (
              <span className="text-xs text-muted-foreground/70">No incoming links to this note.</span>
            ) : (
              <div className="space-y-1">
                {activeNode.backlinks.map(bId => (
                  <button
                    key={bId}
                    className="w-full text-left text-xs p-1.5 rounded hover:bg-muted font-medium text-primary block truncate transition-colors"
                    type="button"
                    onClick={() => onSelectNode(bId)}
                  >
                    ← [[{bId}]]
                  </button>
                ))}
              </div>
            )}
          </div>

          {/* Outgoing Wikilinks */}
          <div>
            <h3 className="text-xs font-semibold text-muted-foreground uppercase tracking-wider mb-2 flex items-center justify-between">
              <span>Outgoing Links</span>
              <span>{activeNode.wikilinks?.length ?? 0}</span>
            </h3>
            {(!activeNode.wikilinks || activeNode.wikilinks.length === 0) ? (
              <span className="text-xs text-muted-foreground/70">No outgoing wikilinks.</span>
            ) : (
              <div className="space-y-1">
                {activeNode.wikilinks.map(wId => (
                  <button
                    key={wId}
                    className="w-full text-left text-xs p-1.5 rounded hover:bg-muted font-medium text-foreground/90 block truncate transition-colors"
                    type="button"
                    onClick={() => onSelectNode(wId)}
                  >
                    → [[{wId}]]
                  </button>
                ))}
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  )
}
