import { BrainView } from '../brain'

/**
 * Unified PULSE Knowledge Graph & StarMap Overlay.
 * Integrates the Obsidian force-directed neural graph and markdown vault explorer.
 */
export function StarmapView({ onClose }: { onClose: () => void }) {
  return <BrainView onClose={onClose} />
}
