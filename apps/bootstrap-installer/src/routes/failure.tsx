import { useStore } from '@nanostores/react'
import { FileText, RefreshCw } from 'lucide-react'

import {
  $logPath,
  $mode,
  type BootstrapStateModel,
  openLogDir,
  startInstall,
  startUpdate
} from '../store'

interface FailureProps {
  bootstrap: BootstrapStateModel
}

/*
 * Failure screen — PULSE installer failure card.
 * rebranded to Pulse. Light card, red serif caps heading, gray reason,
 * solid blue Retry + quiet Open log folder, mono log path.
 */
export default function Failure({ bootstrap }: FailureProps) {
  const logPath = useStore($logPath)
  const mode = useStore($mode)
  const isUpdate = mode === 'update'

  return (
    <div className="pulse-fade-in flex h-full flex-col items-center justify-center gap-6 bg-white px-12 py-10 text-slate-900">
      <div className="w-full max-w-2xl min-w-0 text-center">
        <h1 className="m-0 font-serif text-5xl font-bold uppercase leading-tight tracking-wide text-red-700">
          {isUpdate ? 'Update didn\u2019t finish' : 'Install didn\u2019t finish'}
        </h1>

        <p className="m-0 mx-auto mt-3 max-w-xl text-center text-sm leading-normal text-slate-500">
          {bootstrap.error ??
            (isUpdate
              ? 'Something went wrong during the update.'
              : 'Something went wrong during installation.')}
        </p>
      </div>

      <div className="flex items-center gap-3">
        <button
          className="inline-flex cursor-pointer items-center gap-2 rounded-lg bg-blue-600 px-5 py-2.5 text-sm font-medium text-white transition-colors hover:bg-blue-700"
          onClick={() => void (isUpdate ? startUpdate() : startInstall())}
          type="button"
        >
          <RefreshCw size={16} />
          {isUpdate ? 'Retry update' : 'Retry install'}
        </button>
        <button
          className="inline-flex cursor-pointer items-center gap-2 rounded-lg border border-slate-200 bg-slate-50 px-5 py-2.5 text-sm font-medium text-slate-700 transition-colors hover:bg-slate-100"
          onClick={() => void openLogDir()}
          type="button"
        >
          <FileText size={16} />
          Open log folder
        </button>
      </div>

      {logPath && (
        <p className="max-w-lg text-center text-xs text-slate-400">
          Log: <code className="font-mono">{logPath}</code>
        </p>
      )}
    </div>
  )
}
