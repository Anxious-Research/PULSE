import { CircleAlert, Rocket } from 'lucide-react'
import { useState } from 'react'

import { launchPULSEDesktop } from '../store'

/*
 * Success screen — exact replica of the Hermes installer success card,
 * rebranded to Pulse. Light card, blue serif caps heading, gray subline
 * with a pill for `pulse desktop`, solid blue Launch button, pink inline
 * error box when the desktop app can't be auto-launched.
 */
export default function Success() {
  const [error, setError] = useState<string | null>(null)
  const [launching, setLaunching] = useState(false)

  async function handleLaunch() {
    setError(null)
    setLaunching(true)

    try {
      await launchPULSEDesktop()
      // On success the installer exits — control never returns here.
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e)
      setError(msg)
      setLaunching(false)
    }
  }

  return (
    <div className="pulse-fade-in flex h-full flex-col items-center justify-center gap-6 bg-white px-12 py-10 text-slate-900">
      <div className="w-full max-w-2xl min-w-0 text-center">
        <h1 className="m-0 font-serif text-6xl font-bold uppercase leading-tight tracking-wide text-blue-700">
          Pulse is ready
        </h1>

        <p className="m-0 mx-auto mt-3 max-w-xl text-center text-sm leading-normal text-slate-500">
          You can launch from here, or any time from your terminal with{' '}
          <code className="rounded bg-slate-100 px-1.5 py-0.5 font-mono text-[13px] text-slate-600">
            pulse desktop
          </code>
          .
        </p>
      </div>

      <button
        className="inline-flex cursor-pointer items-center gap-2 rounded-lg bg-blue-600 px-5 py-2.5 text-sm font-medium text-white transition-colors hover:bg-blue-700 disabled:pointer-events-none disabled:opacity-50"
        disabled={launching}
        onClick={() => void handleLaunch()}
        type="button"
      >
        <Rocket size={16} />
        {launching ? 'Launching' : 'Launch Pulse'}
      </button>

      {error && (
        <div
          className="w-full max-w-2xl rounded-lg border border-red-200 bg-red-50 p-4 text-left"
          role="alert"
        >
          <div className="flex items-center gap-2 text-sm font-semibold text-red-700">
            <CircleAlert size={16} />
            Couldn&rsquo;t launch the desktop app
          </div>
          <div className="mt-1 break-words text-sm leading-relaxed text-red-900/80">{error}</div>
        </div>
      )}
    </div>
  )
}
