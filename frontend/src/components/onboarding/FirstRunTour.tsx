const STEPS: string[] = [
  'Choose a protocol.',
  'Write your scheduling policy.',
  'Configure the workload.',
  'Run the simulation.',
  'Inspect the timeline.',
  'Compare metrics.',
  'Modify your algorithm.',
  'Repeat.',
]

interface Props {
  onTryExample: () => void
  onClose: () => void
}

export default function FirstRunTour({ onTryExample, onClose }: Props) {
  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal-panel tour-panel" onClick={(e) => e.stopPropagation()}>
        <div className="modal-header">
          <h2 className="mono">Welcome to the Simulation Lab</h2>
          <button className="modal-close" onClick={onClose} aria-label="Close">×</button>
        </div>
        <p className="tour-intro">
          This is a loop, not a linear tutorial — most sessions cycle through these eight steps
          several times as you refine an idea.
        </p>
        <ol className="tour-steps">
          {STEPS.map((step, i) => (
            <li key={i} className="tour-step">
              <span className="tour-step-number mono">{i + 1}</span>
              <span>{step}</span>
            </li>
          ))}
        </ol>
        <p className="tour-note">
          This is a simulation environment for early-stage evaluation, not a replacement for
          testing on a real kernel or database.
        </p>
        <div className="tour-actions">
          <button className="btn btn-primary" onClick={onTryExample}>
            Try the Critical-Aware Example
          </button>
          <button className="btn btn-ghost" onClick={onClose}>Skip for now</button>
        </div>
      </div>
    </div>
  )
}
