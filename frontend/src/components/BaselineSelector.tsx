import type { PolicyInfo } from '../types'
import { colorFor } from '../palette'

interface Props {
  policies: PolicyInfo[]
  selected: Set<string>
  onToggle: (key: string) => void
  includeMyAlgorithm: boolean
  onToggleMyAlgorithm: () => void
}

export default function BaselineSelector({
  policies, selected, onToggle, includeMyAlgorithm, onToggleMyAlgorithm,
}: Props) {
  return (
    <div className="baseline-selector">
      <span className="config-field-label mono">Run</span>
      {policies.map((p) => (
        <label key={p.key} className="baseline-checkbox" title={p.description}>
          <input
            type="checkbox"
            checked={selected.has(p.key)}
            onChange={() => onToggle(p.key)}
          />
          <span className="baseline-dot" style={{ background: colorFor(p.key) }} />
          {p.name}
        </label>
      ))}
      <label className="baseline-checkbox baseline-checkbox-mine" title="Run the code currently in the editor">
        <input type="checkbox" checked={includeMyAlgorithm} onChange={onToggleMyAlgorithm} />
        <span className="baseline-dot" style={{ background: colorFor('custom') }} />
        My Algorithm
      </label>
    </div>
  )
}
