import { useEffect, useState } from 'react'
import { X } from 'lucide-react'

import { type BrainNodeDetail, type BrainNodeRef, getBrainNodeDetail } from '@/api/skills'
import { cn } from '@/lib/utils'

/** The memory evidence inspector (P4): when a graph node is selected, show its full provenance —
 *  content, type, source evidence, timestamps, confidence justification, revision/supersession
 *  history and relationships — read from the canonical backend model, nothing invented here.
 *
 *  `vaultId` is the brain node id (`category/slug`); a synthetic node (skill/ghost) has none, so
 *  the inspector only opens for real memory nodes and shows a light hint otherwise. */
export function BrainInspector({
  vaultId,
  label,
  onClose,
  onSelectNode
}: {
  vaultId: null | string
  label?: string
  onClose: () => void
  onSelectNode?: (vaultId: string) => void
}) {
  const [detail, setDetail] = useState<BrainNodeDetail | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<null | string>(null)

  useEffect(() => {
    if (!vaultId) {
      setDetail(null)
      setError(null)
      return
    }
    let cancelled = false
    setLoading(true)
    setError(null)
    getBrainNodeDetail(vaultId)
      .then(d => {
        if (cancelled) return
        if (!d.ok) {
          setError(d.message ?? 'not found')
          setDetail(null)
        } else {
          setDetail(d)
        }
      })
      .catch(e => !cancelled && setError(e instanceof Error ? e.message : String(e)))
      .finally(() => !cancelled && setLoading(false))
    return () => {
      cancelled = true
    }
  }, [vaultId])

  if (!vaultId) return null

  return (
    <div
      className="pointer-events-auto absolute top-3 right-3 bottom-3 z-30 flex w-80 flex-col overflow-hidden rounded-2xl border border-border bg-card/95 shadow-[0_16px_48px_rgba(0,0,0,0.4)] backdrop-blur-xl [-webkit-app-region:no-drag]"
      data-testid="brain-inspector"
    >
      <div className="flex items-start justify-between gap-2 border-b border-border px-4 py-3">
        <div className="min-w-0">
          <div className="truncate text-sm font-semibold text-foreground">{detail?.label ?? label ?? vaultId}</div>
          {detail?.kind ? (
            <KindBadge kind={detail.kind} status={detail.status} beliefStatus={detail.belief_status} procStatus={detail.procedure_status} />
          ) : null}
        </div>
        <button aria-label="Close inspector" className="rounded-full p-1 text-muted-foreground hover:bg-accent hover:text-foreground" onClick={onClose} type="button">
          <X className="h-4 w-4" />
        </button>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto px-4 py-3 text-xs text-foreground">
        {loading ? (
          <p className="text-muted-foreground">Loading evidence…</p>
        ) : error ? (
          <p className="text-destructive">{error}</p>
        ) : detail ? (
          <InspectorBody detail={detail} onSelectNode={onSelectNode} />
        ) : null}
      </div>
    </div>
  )
}

function KindBadge({ kind, status, beliefStatus, procStatus }: { kind: string; status?: string; beliefStatus?: string; procStatus?: string }) {
  const extra = kind === 'belief' ? beliefStatus : kind === 'procedure' ? procStatus : status
  return (
    <div className="mt-0.5 flex items-center gap-1.5">
      <span className="rounded-full bg-accent px-1.5 py-0.5 text-[0.6rem] font-medium uppercase tracking-wide text-accent-foreground">{kind}</span>
      {extra ? <span className="text-[0.62rem] text-muted-foreground">{extra}</span> : null}
    </div>
  )
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="mb-3">
      <div className="mb-1 text-[0.6rem] font-semibold uppercase tracking-wide text-muted-foreground">{title}</div>
      {children}
    </div>
  )
}

function RefRow({ r, onSelectNode }: { r: BrainNodeRef; onSelectNode?: (id: string) => void }) {
  const clickable = !r.missing && !!onSelectNode
  return (
    <button
      className={cn(
        'flex w-full flex-col items-start gap-0.5 rounded-md border border-border/60 px-2 py-1.5 text-left',
        clickable ? 'hover:border-border hover:bg-accent/40' : 'cursor-default opacity-70'
      )}
      disabled={!clickable}
      onClick={() => clickable && onSelectNode?.(r.id.split('#')[0])}
      type="button"
    >
      <span className="flex w-full items-center justify-between gap-2">
        <span className="truncate font-medium">{r.label}</span>
        {typeof r.confidence === 'number' ? (
          <span className="shrink-0 font-mono text-[0.6rem] text-muted-foreground">{r.confidence.toFixed(2)}</span>
        ) : null}
      </span>
      {r.snippet ? <span className="line-clamp-2 text-[0.68rem] text-muted-foreground">{r.snippet}</span> : null}
      {(r.kind || r.provenance) ? (
        <span className="text-[0.58rem] uppercase tracking-wide text-muted-foreground">
          {[r.kind, r.provenance].filter(Boolean).join(' · ')}
        </span>
      ) : null}
    </button>
  )
}

function fmtTime(ts?: null | number): string {
  if (!ts) return '—'
  try {
    return new Date(ts * 1000).toLocaleString()
  } catch {
    return String(ts)
  }
}

function InspectorBody({ detail, onSelectNode }: { detail: BrainNodeDetail; onSelectNode?: (id: string) => void }) {
  return (
    <>
      {detail.content ? (
        <Section title="Memory">
          <p className="whitespace-pre-wrap leading-relaxed">{detail.content}</p>
        </Section>
      ) : null}

      {detail.goal ? (
        <Section title="Goal">
          <p className="leading-relaxed">{detail.goal}</p>
        </Section>
      ) : null}

      {detail.confidence_justification ? (
        <Section title="Why PULSE holds this">
          <p className="leading-relaxed text-muted-foreground">{detail.confidence_justification}</p>
        </Section>
      ) : null}

      {detail.evidence && detail.evidence.length > 0 ? (
        <Section title={`Supporting evidence (${detail.evidence.length})`}>
          <div className="flex flex-col gap-1.5">
            {detail.evidence.map(r => <RefRow key={r.id} onSelectNode={onSelectNode} r={r} />)}
          </div>
        </Section>
      ) : null}

      {detail.counter_evidence && detail.counter_evidence.length > 0 ? (
        <Section title={`Counter-evidence (${detail.counter_evidence.length})`}>
          <div className="flex flex-col gap-1.5">
            {detail.counter_evidence.map(r => <RefRow key={r.id} onSelectNode={onSelectNode} r={r} />)}
          </div>
        </Section>
      ) : null}

      {detail.experience_refs && detail.experience_refs.length > 0 ? (
        <Section title={`Supporting experience (${detail.experience_refs.length})`}>
          <div className="flex flex-col gap-1.5">
            {detail.experience_refs.map(r => <RefRow key={r.id} onSelectNode={onSelectNode} r={r} />)}
          </div>
        </Section>
      ) : null}

      {detail.kind === 'procedure' && detail.counts ? (
        <Section title="Track record">
          <p className="text-muted-foreground">
            {detail.counts.success} success · {detail.counts.failure} failure · {detail.counts.partial} partial
            {typeof detail.success_rate === 'number' ? ` — success rate ${(detail.success_rate * 100).toFixed(0)}%` : ''}
          </p>
        </Section>
      ) : null}

      {detail.failure_modes && detail.failure_modes.length > 0 ? (
        <Section title="Known limitations">
          <ul className="list-disc space-y-0.5 pl-4 text-muted-foreground">
            {detail.failure_modes.map((m, i) => <li key={i}>{m}</li>)}
          </ul>
        </Section>
      ) : null}

      {detail.superseded_by ? (
        <Section title="Replaced by">
          <RefRow onSelectNode={onSelectNode} r={detail.superseded_by} />
        </Section>
      ) : null}

      {detail.supersedes && detail.supersedes.length > 0 ? (
        <Section title="Replaces">
          <div className="flex flex-col gap-1.5">
            {detail.supersedes.map(r => <RefRow key={r.id} onSelectNode={onSelectNode} r={r} />)}
          </div>
        </Section>
      ) : null}

      {detail.revisions && detail.revisions.length > 0 ? (
        <Section title="Revision history">
          <ul className="space-y-1">
            {detail.revisions.map((r, i) => (
              <li className="flex items-baseline justify-between gap-2 text-muted-foreground" key={i}>
                <span>{r.reason ?? 'change'}</span>
                <span className="shrink-0 font-mono text-[0.62rem]">
                  {typeof r.from === 'number' ? r.from.toFixed(2) : '—'} → {typeof r.to === 'number' ? r.to.toFixed(2) : '—'}
                </span>
              </li>
            ))}
          </ul>
        </Section>
      ) : null}

      {detail.corroborations && detail.corroborations.length > 0 ? (
        <Section title={`Restated ${detail.corroborations.length}×`}>
          <ul className="space-y-0.5 text-muted-foreground">
            {detail.corroborations.slice(0, 5).map((c, i) => <li className="truncate" key={i}>{c.text || fmtTime(c.at)}</li>)}
          </ul>
        </Section>
      ) : null}

      {detail.relationships && detail.relationships.length > 0 ? (
        <Section title={`Related (${detail.relationships.length})`}>
          <div className="flex flex-col gap-1.5">
            {detail.relationships.map(r => <RefRow key={r.id} onSelectNode={onSelectNode} r={r} />)}
          </div>
        </Section>
      ) : null}

      {detail.timestamps ? (
        <Section title="Timeline">
          <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-0.5 text-muted-foreground">
            <dt>Created</dt><dd className="text-right font-mono text-[0.62rem]">{fmtTime(detail.timestamps.created_at)}</dd>
            <dt>Updated</dt><dd className="text-right font-mono text-[0.62rem]">{fmtTime(detail.timestamps.updated_at)}</dd>
            <dt>Last recalled</dt><dd className="text-right font-mono text-[0.62rem]">{fmtTime(detail.timestamps.last_accessed)}</dd>
            <dt>Recall count</dt><dd className="text-right font-mono text-[0.62rem]">{detail.timestamps.access_count ?? 0}</dd>
          </dl>
        </Section>
      ) : null}
    </>
  )
}
