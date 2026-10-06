import { useMemo, useState } from 'react'

import { Button } from '@/components/ui/button'
import { SearchField } from '@/components/ui/search-field'
import { cn } from '@/lib/utils'

import { BrainGraphView } from './brain-graph-view'
import type { BrainCategory, BrainNodeDetail, BrainNodeSummary, BrainUnlinkedMention } from './types'

interface BrainExplorerProps {
  nodes: BrainNodeSummary[]
  edges: Array<[string, string]>
  activeNode: BrainNodeDetail | null
  unlinkedMentions?: BrainUnlinkedMention[]
  loading: boolean
  onSelectNode: (nodeId: string) => void
  onCreateNode?: (id: string, title: string, category: string, content: string) => Promise<boolean>
  onEditNode?: (nodeId: string, newContent: string) => Promise<boolean>
  onRenameNode?: (oldId: string, newId: string) => Promise<boolean>
  onDeleteNode?: (nodeId: string) => Promise<boolean>
  onLinkifyMention?: (sourceId: string, targetId: string, term: string) => Promise<boolean>
  onOpenDailyNote?: () => void
  onOpenStarmap?: () => void
}

const CATEGORIES: Array<{ id: BrainCategory; label: string; icon: string }> = [
  { id: 'all', label: 'All Knowledge', icon: '🧠' },
  { id: 'self', label: 'Self & Anatomy', icon: '🤖' },
  { id: 'user', label: 'User & Life', icon: '👤' },
  { id: 'concept', label: 'Concepts', icon: '💡' },
  { id: 'belief', label: 'Beliefs & Hypotheses', icon: '⚖️' },
  { id: 'project', label: 'Projects', icon: '📁' },
  { id: 'daily', label: 'Daily Notes', icon: '📅' },
]

export function BrainExplorer({
  nodes,
  edges,
  activeNode,
  unlinkedMentions = [],
  loading,
  onSelectNode,
  onCreateNode,
  onEditNode,
  onRenameNode,
  onDeleteNode,
  onLinkifyMention,
  onOpenDailyNote,
  onOpenStarmap,
}: BrainExplorerProps) {
  const [selectedCategory, setSelectedCategory] = useState<BrainCategory>('all')
  const [searchQuery, setSearchQuery] = useState('')
  const [viewMode, setViewMode] = useState<'split' | 'graph' | 'notes'>('split')

  // Edit State
  const [isEditing, setIsEditing] = useState(false)
  const [editContent, setEditContent] = useState('')
  const [isSaving, setIsSaving] = useState(false)
  const [isDeleting, setIsDeleting] = useState(false)

  // New Note Modal / Input State
  const [isCreating, setIsCreating] = useState(false)
  const [newTitle, setNewTitle] = useState('')
  const [newCategory, setNewCategory] = useState<BrainCategory>('concept')

  // Rename State
  const [isRenaming, setIsRenaming] = useState(false)
  const [renameSlug, setRenameSlug] = useState('')

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

  const handleStartEdit = () => {
    if (activeNode) {
      setEditContent(activeNode.content)
      setIsEditing(true)
    }
  }

  const handleCancelEdit = () => {
    setIsEditing(false)
    setEditContent('')
  }

  const handleSaveEdit = async () => {
    if (!activeNode || !onEditNode) return
    setIsSaving(true)
    try {
      const ok = await onEditNode(activeNode.id, editContent)
      if (ok) {
        setIsEditing(false)
      }
    } finally {
      setIsSaving(false)
    }
  }

  const handleCreateSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!newTitle.trim() || !onCreateNode) return
    const cat = newCategory === 'all' ? 'concept' : newCategory
    const slug = `${cat}/${newTitle.trim().toLowerCase().replace(/[^a-z0-9]+/g, '-')}`
    const initialContent = `# ${newTitle.trim()}\n\nWrite note content with [[wikilinks]]...\n\n*Related*: [[self/identity]]`
    const ok = await onCreateNode(slug, newTitle.trim(), cat, initialContent)
    if (ok) {
      setIsCreating(false)
      setNewTitle('')
      onSelectNode(slug)
    }
  }

  const handleStartRename = () => {
    if (activeNode) {
      setRenameSlug(activeNode.id)
      setIsRenaming(true)
    }
  }

  const handleSaveRename = async () => {
    if (!activeNode || !onRenameNode || !renameSlug.trim()) return
    const ok = await onRenameNode(activeNode.id, renameSlug.trim())
    if (ok) {
      setIsRenaming(false)
      onSelectNode(renameSlug.trim())
    }
  }

  const handleDelete = async () => {
    if (!activeNode || !onDeleteNode) return
    if (!window.confirm(`Are you sure you want to delete note "${activeNode.label}" (${activeNode.id})?`)) {
      return
    }
    setIsDeleting(true)
    try {
      await onDeleteNode(activeNode.id)
      setIsEditing(false)
    } finally {
      setIsDeleting(false)
    }
  }

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
      {/* ── Top Bar: Title, Actions, Search & View Switcher ─────────── */}
      <div className="flex items-center justify-between px-4 py-2.5 border-b border-border bg-card/60 shrink-0 gap-3">
        <div className="flex items-center gap-2 min-w-0">
          <span className="font-semibold text-sm flex items-center gap-1.5 whitespace-nowrap">
            <span>🧠</span> PULSE Knowledge Graph
          </span>
          <span className="text-xs text-muted-foreground hidden sm:inline">
            ({nodes.length} notes, {edges.length} links)
          </span>

          {/* New Note & Daily Note Quick Actions */}
          <Button
            size="sm"
            variant="outline"
            className="text-xs h-7 px-2 ml-2"
            onClick={() => setIsCreating(true)}
          >
            ➕ New Note
          </Button>
          {onOpenDailyNote && (
            <Button
              size="sm"
              variant="outline"
              className="text-xs h-7 px-2"
              onClick={onOpenDailyNote}
            >
              📅 Today
            </Button>
          )}
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

      {/* ── New Note Creator Modal ────────────────────────────────────────── */}
      {isCreating && (
        <div className="fixed inset-0 bg-background/80 backdrop-blur-sm z-50 flex items-center justify-center p-4">
          <form
            onSubmit={handleCreateSubmit}
            className="bg-card border border-border rounded-xl shadow-2xl p-5 w-full max-w-md space-y-4"
          >
            <div className="flex items-center justify-between border-b border-border pb-2">
              <h3 className="font-semibold text-sm flex items-center gap-1.5">
                <span>➕</span> Create New Knowledge Note
              </h3>
              <button
                type="button"
                className="text-muted-foreground hover:text-foreground text-sm font-bold"
                onClick={() => setIsCreating(false)}
              >
                ✕
              </button>
            </div>

            <div className="space-y-3 text-xs">
              <div>
                <label className="block text-muted-foreground mb-1">Note Title</label>
                <input
                  type="text"
                  required
                  placeholder="e.g. Distributed Consensus Algorithms"
                  value={newTitle}
                  onChange={e => setNewTitle(e.target.value)}
                  className="w-full p-2 bg-muted/50 border border-border rounded-md focus:outline-none focus:ring-1 focus:ring-primary text-xs"
                  autoFocus
                />
              </div>

              <div>
                <label className="block text-muted-foreground mb-1">Category / Vault Subfolder</label>
                <select
                  value={newCategory}
                  onChange={e => setNewCategory(e.target.value as BrainCategory)}
                  className="w-full p-2 bg-muted/50 border border-border rounded-md focus:outline-none focus:ring-1 focus:ring-primary text-xs"
                >
                  <option value="concept">💡 Concept (Domain knowledge)</option>
                  <option value="project">📁 Project (Workflows & codebases)</option>
                  <option value="user">👤 User (Preferences & profiles)</option>
                  <option value="belief">⚖️ Belief (Hypotheses)</option>
                  <option value="daily">📅 Daily (Journal & logs)</option>
                </select>
              </div>
            </div>

            <div className="flex justify-end gap-2 pt-2 border-t border-border">
              <Button size="sm" variant="ghost" type="button" onClick={() => setIsCreating(false)}>
                Cancel
              </Button>
              <Button size="sm" variant="default" type="submit">
                Create Note
              </Button>
            </div>
          </form>
        </div>
      )}

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
                    onClick={() => {
                      setIsEditing(false)
                      setIsRenaming(false)
                      onSelectNode(node.id)
                    }}
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
              onSelectNode={nodeId => {
                setIsEditing(false)
                setIsRenaming(false)
                onSelectNode(nodeId)
              }}
            />
          </div>
        )}

        {/* Right Column: Note Inspector & Markdown Editor */}
        {viewMode !== 'graph' && (
          <div className="w-96 flex flex-col shrink-0 overflow-y-auto bg-background p-5 space-y-4">
            {loading ? (
              <div className="flex-1 flex items-center justify-center text-sm text-muted-foreground">
                Loading note...
              </div>
            ) : !activeNode ? (
              <div className="flex-1 flex flex-col items-center justify-center text-muted-foreground p-6 text-center">
                <span className="text-3xl mb-2">🧠</span>
                <span className="font-medium text-sm">Select a note to inspect</span>
                <span className="text-xs text-muted-foreground/80 max-w-xs mt-1">
                  Click any node in the interactive graph or select a note from the left list.
                </span>
              </div>
            ) : (
              <div className="space-y-4">
                {/* Note Header & Action Buttons */}
                <div className="border-b border-border pb-3 space-y-2">
                  <div className="flex items-center justify-between">
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
                    </div>

                    {/* Edit, Rename and Delete Actions */}
                    <div className="flex items-center gap-1">
                      {!isEditing && !isRenaming ? (
                        <>
                          {onEditNode && (
                            <Button size="sm" variant="outline" className="h-6 text-xs px-2" onClick={handleStartEdit}>
                              ✏️ Edit
                            </Button>
                          )}
                          {onRenameNode && activeNode.category !== 'self' && (
                            <Button size="sm" variant="ghost" className="h-6 text-xs px-2" onClick={handleStartRename}>
                              🔄 Rename
                            </Button>
                          )}
                          {onDeleteNode && activeNode.category !== 'self' && (
                            <Button
                              size="sm"
                              variant="ghost"
                              className="h-6 text-xs px-2 text-destructive hover:text-destructive"
                              disabled={isDeleting}
                              onClick={handleDelete}
                            >
                              🗑️ Delete
                            </Button>
                          )}
                        </>
                      ) : isEditing ? (
                        <>
                          <Button
                            size="sm"
                            variant="default"
                            className="h-6 text-xs px-2.5"
                            disabled={isSaving}
                            onClick={handleSaveEdit}
                          >
                            {isSaving ? 'Saving...' : '💾 Save'}
                          </Button>
                          <Button
                            size="sm"
                            variant="ghost"
                            className="h-6 text-xs px-2"
                            disabled={isSaving}
                            onClick={handleCancelEdit}
                          >
                            Cancel
                          </Button>
                        </>
                      ) : (
                        <>
                          <Button
                            size="sm"
                            variant="default"
                            className="h-6 text-xs px-2.5"
                            onClick={handleSaveRename}
                          >
                            Confirm Rename
                          </Button>
                          <Button
                            size="sm"
                            variant="ghost"
                            className="h-6 text-xs px-2"
                            onClick={() => setIsRenaming(false)}
                          >
                            Cancel
                          </Button>
                        </>
                      )}
                    </div>
                  </div>

                  {isRenaming ? (
                    <div className="space-y-1.5 pt-1">
                      <label className="text-[11px] text-muted-foreground">New Canonical Node Path:</label>
                      <input
                        type="text"
                        value={renameSlug}
                        onChange={e => setRenameSlug(e.target.value)}
                        className="w-full p-1.5 text-xs font-mono bg-card border border-border rounded focus:outline-none focus:ring-1 focus:ring-primary"
                      />
                      <span className="text-[10px] text-muted-foreground">
                        Note: Renaming will automatically update all `[[links]]` pointing to this note across the entire vault.
                      </span>
                    </div>
                  ) : (
                    <>
                      <h2 className="text-lg font-bold tracking-tight">{activeNode.label}</h2>
                      <div className="text-xs text-muted-foreground font-mono">{activeNode.id}</div>
                    </>
                  )}

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

                {/* Markdown View vs Editor */}
                {isEditing ? (
                  <div className="space-y-2">
                    <textarea
                      value={editContent}
                      onChange={e => setEditContent(e.target.value)}
                      rows={14}
                      className="w-full p-2.5 text-xs font-mono bg-card border border-border rounded-md focus:outline-none focus:ring-1 focus:ring-primary leading-relaxed resize-y"
                      placeholder="Write markdown with [[wikilinks]]..."
                    />
                    <div className="text-[11px] text-muted-foreground">
                      Tip: Use <code>[[category/note-name]]</code> to link to other knowledge nodes.
                    </div>
                  </div>
                ) : (
                  <div className="prose prose-sm dark:prose-invert max-w-none text-xs leading-relaxed whitespace-pre-wrap font-sans">
                    {renderedContent}
                  </div>
                )}

                {/* Backlinks Pane */}
                {!isEditing && activeNode.backlinks && activeNode.backlinks.length > 0 && (
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
                          onClick={() => {
                            setIsEditing(false)
                            onSelectNode(b)
                          }}
                        >
                          [[{b}]]
                        </button>
                      ))}
                    </div>
                  </div>
                )}

                {/* Outgoing Wikilinks Pane */}
                {!isEditing && activeNode.wikilinks && activeNode.wikilinks.length > 0 && (
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
                          onClick={() => {
                            setIsEditing(false)
                            onSelectNode(w)
                          }}
                        >
                          [[{w}]]
                        </button>
                      ))}
                    </div>
                  </div>
                )}

                {/* Unlinked Mentions 1-Click Linkifier (Obsidian Parity) */}
                {!isEditing && unlinkedMentions && unlinkedMentions.length > 0 && (
                  <div className="border-t border-border pt-3 space-y-2">
                    <span className="text-xs font-semibold uppercase tracking-wider text-muted-foreground flex items-center gap-1">
                      <span>⚡</span> Unlinked Mentions ({unlinkedMentions.length})
                    </span>
                    <div className="space-y-1.5">
                      {unlinkedMentions.map((m, idx) => (
                        <div
                          key={idx}
                          className="p-2 rounded bg-muted/40 border border-border/60 text-xs space-y-1"
                        >
                          <div className="flex items-center justify-between font-medium">
                            <span>In [[{m.source_id}]]</span>
                            {onLinkifyMention && (
                              <Button
                                size="sm"
                                variant="outline"
                                className="h-5 text-[10px] px-1.5"
                                onClick={() => onLinkifyMention(m.source_id, activeNode.id, m.matched_term)}
                              >
                                🔗 Link
                              </Button>
                            )}
                          </div>
                          <div className="text-muted-foreground text-[11px] italic">
                            &ldquo;...{m.snippet}...&rdquo;
                          </div>
                        </div>
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
