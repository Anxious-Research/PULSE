import { useMemo, useState } from 'react'

import { Button } from '@/components/ui/button'
import { SearchField } from '@/components/ui/search-field'
import { cn } from '@/lib/utils'

import { BrainGraphView } from './brain-graph-view'
import type { BrainCategory, BrainNodeDetail, BrainNodeSummary } from './types'

interface BrainExplorerProps {
  nodes: BrainNodeSummary[]
  edges: Array<[string, string]>
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
  { id: 'project', label: 'Projects', icon: '📁' },
]

export function BrainExplorer({
  nodes,
  edges,
  activeNode,
  loading,
  onSelectNode,
  onOpenStarmap,
}: BrainExplorerProps) {
  const [selectedCategory, setSelectedCategory] = useState<BrainCategory>('all')
  const [searchQuery, setSearchQuery] = useState('')
  const [viewMode, setViewMode] = useState<'split' | 'graph' | 'notes'>('split')

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
    <div className="flex flex-col h-full w-full overflow-hidden bg-background text-foreground">
      {/* ── Top Bar: Title, Search, Category Pills & View Switcher ─────────── */}
      <div className="flex items-center justify-between px-4 py-2.5 border-b border-border bg-card/60 shrink-0 gap-3">
        <div className="flex items-center gap-2 min-w-0">
          <span className="font-semibold text-sm flex items-center gap-1.5 whitespace-nowrap">
            <span>🧠</span> PULSE Brain Vault
          </span>
          <span className="text-xs text-muted-foreground hidden sm:inline">
            ({nodes.length} nodes, {edges.length} connections)
          </span>
        </div>

        {/* Search Field */}
        <div className="max-w-xs flex-1">
          <SearchField
            placeholder="Search notes, wikilinks, tags..."
            value={searchQuery}
            onChange={e => setSearchQuery(e.target.value)}
          />
        </div>

        {/* View Mode Controls */}
        <div className="flex items-center gap-1 bg-muted/60 p-0.5 rounded-lg border border-border/50">
          <Button
            size="sm"
            variant={viewMode === 'split' ? 'default' : 'ghost'}
            className="text-xs h-7 px-2.5"
            onClick={() => setViewMode('split')}
          >
            🕸️ Split Graph
          </Button>
          <Button
            size="sm"
            variant={viewMode === 'graph' ? 'default' : 'ghost'}
            className="text-xs h-7 px-2.5"
            onClick={() => setViewMode('graph')}
          >
            🌐 Graph Only
          </Button>
          <Button
            size="sm"
            variant={viewMode === 'notes' ? 'default' : 'ghost'}
            className="text-xs h-7 px-2.5"
            onClick={() => setViewMode('notes')}
          >
            📝 Notes List
          </Button>
          {onOpenStarmap && (
            <Button size="sm" variant="outline" className="text-xs h-7 px-2" onClick={onOpenStarmap}>
              StarMap
            </Button>
          )}
        </div>
      </div>

      {/* Category Filter Bar */}
      <div className="flex items-center gap-1.5 px-4 py-1.5 border-b border-border bg-muted/20 overflow-x-auto shrink-0">
        {CATEGORIES.map(cat => (
          <button
            key={cat.id}
            className={cn(
              'text-xs px-2.5 py-1 rounded-md transition-colors flex items-center gap-1.5 whitespace-nowrap',
              selectedCategory === cat.id
                ? 'bg-primary text-primary-foreground font-medium shadow-sm'
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

      {/* ── Main Work Area ───────────────────────────────────────────────── */}
      <div className="flex flex-1 min-h-0 overflow-hidden">
        {/* Left Column: Notes List (Visible in 'split' or 'notes' view) */}
        {viewMode !== 'graph' && (
          <div className="flex flex-col w-72 border-r border-border bg-card/30 shrink-0 overflow-y-auto divide-y divide-border/50">
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
        )}

        {/* Center Canvas: Interactive Obsidian-Style Knowledge Graph (Visible in 'split' or 'graph' view) */}
        {viewMode !== 'notes' && (
          <div className="flex-1 relative min-w-0 h-full border-r border-border">
            <BrainGraphView
              activeNodeId={activeNode?.id || null}
              edges={edges}
              nodes={nodes}
              searchQuery={searchQuery}
              selectedCategory={selectedCategory}
              onSelectNode={onSelectNode}
            />
          </div>
        )}

        {/* Right Column: Note Inspector & Backlinks Pane (Visible in 'split' or 'notes' view) */}
        {viewMode !== 'graph' && (
          <div className="w-96 flex flex-col shrink-0 overflow-y-auto bg-background p-5 space-y-4">
            {loading ? (
              <div className="flex-1 flex items-center justify-center text-sm text-muted-foreground">
                Loading note...
              </div>
            ) : !activeNode ? (
              <div className="flex-1 flex flex-col items-center justify-center text-muted-foreground p-6 text-center">
                <span className="text-3xl mb-2">🧠</span>
                <span className="font-medium text-sm">Select a node to inspect</span>
                <span className="text-xs text-muted-foreground/80 max-w-xs mt-1">
                  Click any star in the interactive graph or choose a note from the left list.
                </span>
              </div>
            ) : (
              <div className="space-y-4">
                {/* Note Header */}
                <div className="border-b border-border pb-3 space-y-2">
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

                  <h2 className="text-lg font-bold tracking-tight">{activeNode.label}</h2>
                  <div className="text-xs text-muted-foreground font-mono">{activeNode.id}</div>

                  {/* Tags */}
                  {activeNode.tags && activeNode.tags.length > 0 && (
                    <div className="flex flex-wrap gap-1 pt-1">
                      {activeNode.tags.map(t => (
                        <span key={t} className="text-xs px-1.5 py-0.5 rounded bg-muted/80 text-muted-foreground">
                          #{t}
                        </span>
                      ))}
                    </div>
                  )}
                </div>

                {/* Markdown Content */}
                <div className="prose prose-sm dark:prose-invert max-w-none text-xs leading-relaxed whitespace-pre-wrap font-sans">
                  {renderedContent}
                </div>

                {/* Backlinks Pane */}
                {activeNode.backlinks && activeNode.backlinks.length > 0 && (
                  <div className="border-t border-border pt-3 space-y-2">
                    <span className="text-xs font-semibold uppercase tracking-wider text-muted-foreground flex items-center gap-1">
                      <span>🔗</span> Incoming Backlinks ({activeNode.backlinks.length})
                    </span>
                    <div className="flex flex-wrap gap-1.5">
                      {activeNode.backlinks.map(b => (
                        <button
                          key={b}
                          className="text-xs px-2 py-1 rounded bg-muted/60 hover:bg-primary/20 hover:text-primary transition-colors border border-border/60"
                          type="button"
                          onClick={() => onSelectNode(b)}
                        >
                          [[{b}]]
                        </button>
                      ))}
                    </div>
                  </div>
                )}

                {/* Outgoing Wikilinks Pane */}
                {activeNode.wikilinks && activeNode.wikilinks.length > 0 && (
                  <div className="border-t border-border pt-3 space-y-2">
                    <span className="text-xs font-semibold uppercase tracking-wider text-muted-foreground flex items-center gap-1">
                      <span>↗️</span> Outgoing Wikilinks ({activeNode.wikilinks.length})
                    </span>
                    <div className="flex flex-wrap gap-1.5">
                      {activeNode.wikilinks.map(w => (
                        <button
                          key={w}
                          className="text-xs px-2 py-1 rounded bg-muted/60 hover:bg-primary/20 hover:text-primary transition-colors border border-border/60"
                          type="button"
                          onClick={() => onSelectNode(w)}
                        >
                          [[{w}]]
                        </button>
                      ))}
                    </div>
                  </div>
                )}
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  )
}
