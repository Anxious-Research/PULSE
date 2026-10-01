import { startInstall } from '../store'

/*
 * Welcome screen — same Hermes-card family as success/failure (light card,
 * blue serif caps heading, gray subline, solid blue Install button),
 * rebranded to Pulse.
 */
export default function Welcome() {
  return (
    <div className="pulse-fade-in flex h-full flex-col items-center justify-center gap-6 bg-white px-12 py-10 text-slate-900">
      <div className="w-full max-w-2xl min-w-0 text-center">
        <h1 className="m-0 font-serif text-6xl font-bold uppercase leading-tight tracking-wide text-blue-700">
          Pulse Agent
        </h1>

        <p className="m-0 mx-auto mt-3 max-w-xl text-center text-sm leading-normal text-slate-500">
          The agent that grows with you. We&rsquo;ll set things up in the background &mdash; takes a
          few minutes.
        </p>
      </div>

      <button
        className="inline-flex cursor-pointer items-center gap-2 rounded-lg bg-blue-600 px-5 py-2.5 text-sm font-medium text-white transition-colors hover:bg-blue-700"
        onClick={() => void startInstall()}
        type="button"
      >
        Install
      </button>
    </div>
  )
}
