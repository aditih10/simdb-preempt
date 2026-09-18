import type { Config } from '../types'

export type ConfigOverrides = Partial<Config>

interface FieldSpec {
  key: keyof Config
  label: string
  unit?: string
  min: number
  max: number
  step: number
  hint: string
}

// Bounds mirror backend/schemas.py's ConfigOverrides field-for-field so the
// UI can't submit something the API will reject anyway.
const FIELDS: FieldSpec[] = [
  { key: 'num_cores', label: 'CPU Cores', min: 1, max: 256, step: 1, hint: 'Simulated CPU cores' },
  { key: 'db_size', label: 'DB Size', unit: 'keys', min: 10, max: 1_000_000, step: 10, hint: 'Number of distinct lockable/versioned records' },
  { key: 'contention', label: 'Contention', min: 0, max: 0.999, step: 0.001, hint: 'Fraction of DB size that is the "hot" key pool (higher = more contention)' },
  { key: 'ops_per_txn', label: 'Ops / Txn', min: 1, max: 1000, step: 1, hint: 'Max operations sampled per transaction' },
  { key: 'mean_op_time', label: 'Mean Op Time', unit: 'ms', min: 0.01, max: 100, step: 0.05, hint: 'Mean simulated CPU time per operation' },
  { key: 'lock_timeout', label: 'Lock Timeout', unit: 'ms', min: 0.1, max: 1000, step: 0.5, hint: '2PL lock-acquire timeout before abort' },
  { key: 'preempt_rate', label: 'Preempt Rate', unit: '/ms', min: 0, max: 5, step: 0.01, hint: 'Mean rate of OS preemption attempts per core' },
  { key: 'preempt_penalty', label: 'Preempt Penalty', unit: 'ms', min: 0, max: 100, step: 0.1, hint: 'Mean context-switch cost paid after a preemption' },
  { key: 'arrival_rate', label: 'Arrival Rate', unit: '/ms', min: 0.01, max: 100, step: 0.1, hint: 'Mean rate of new transactions arriving' },
  { key: 'sim_time', label: 'Sim Time', unit: 'ms', min: 1, max: 600_000, step: 500, hint: 'Total simulated duration' },
  { key: 'seed', label: 'Seed', min: -2_147_483_648, max: 2_147_483_647, step: 1, hint: 'Random seed — same seed + config + algorithm reproduces the same run' },
]

interface Props {
  values: Config | null
  overrides: ConfigOverrides
  onChange: (overrides: ConfigOverrides) => void
  onResetToDefaults: () => void
}

export default function ConfigPanel({ values, overrides, onChange, onResetToDefaults }: Props) {
  function setField(key: keyof Config, raw: string) {
    if (raw === '') {
      const next = { ...overrides }
      delete next[key]
      onChange(next)
      return
    }
    const num = Number(raw)
    if (Number.isNaN(num)) return
    onChange({ ...overrides, [key]: num })
  }

  const changedCount = Object.keys(overrides).length

  return (
    <div className="config-panel">
      <div className="config-panel-header">
        <h3 className="mono">Workload Configuration</h3>
        <button className="btn btn-ghost btn-small" onClick={onResetToDefaults} disabled={changedCount === 0}>
          Reset to Defaults{changedCount > 0 ? ` (${changedCount} changed)` : ''}
        </button>
      </div>
      <div className="config-grid">
        {FIELDS.map((f) => {
          const current = overrides[f.key] ?? values?.[f.key] ?? ''
          const isOverridden = overrides[f.key] !== undefined
          return (
            <label key={f.key} className={`config-field${isOverridden ? ' config-field-changed' : ''}`} title={f.hint}>
              <span className="config-field-label">
                {f.label}{f.unit ? ` (${f.unit})` : ''}
              </span>
              <input
                type="number"
                className="mono config-field-input"
                value={current}
                min={f.min}
                max={f.max}
                step={f.step}
                onChange={(e) => setField(f.key, e.target.value)}
              />
            </label>
          )
        })}
      </div>
    </div>
  )
}
