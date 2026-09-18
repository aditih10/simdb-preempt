import type { ApiError, CompareResponse, Metrics, Protocol } from '../types'
import ComparisonCharts from './ComparisonCharts'
import ExplanationPanel from './ExplanationPanel'
import LayerGapPanel from './LayerGapPanel'

const ERROR_LABELS: Record<string, string> = {
  validation_error: 'Algorithm Error',
  runtime_error: 'Algorithm Error',
  unsupported_api: 'Unsupported API',
  timeout: 'Simulation Timeout',
  output_too_large: 'Algorithm Error',
  bad_request: 'Request Error',
}

function ErrorCard({ error }: { error: ApiError }) {
  const title = error.title || ERROR_LABELS[error.type] || 'Error'
  return (
    <div className="result-error-card">
      <div className="result-error-title mono">{title}</div>
      <div className="result-error-message">
        {error.line != null ? `Line ${error.line}: ` : ''}
        {error.message}
      </div>
    </div>
  )
}

const METRIC_COLUMNS: { key: keyof Metrics; label: string; suffix?: string }[] = [
  { key: 'abort_rate_pct', label: 'Abort %' },
  { key: 'throughput_tps', label: 'TPS' },
  { key: 'mean_latency_ms', label: 'Mean Lat.', suffix: 'ms' },
  { key: 'p99_latency_ms', label: 'P99 Lat.', suffix: 'ms' },
  { key: 'cpu_ms_wasted_on_aborts', label: 'CPU Waste', suffix: 'ms' },
  { key: 'preempt_events', label: 'Preempts' },
  { key: 'deferred_events', label: 'Deferred' },
]

function ComparisonTable({ protocol, response }: { protocol: Protocol; response: CompareResponse }) {
  const rows = Object.entries(response.matrix)
  return (
    <div className="comparison-table-wrap">
      <div className="metrics-table-header mono">{protocol.toUpperCase()}</div>
      <table className="comparison-table">
        <thead>
          <tr>
            <th>Policy</th>
            {METRIC_COLUMNS.map((c) => <th key={c.key} className="mono">{c.label}</th>)}
          </tr>
        </thead>
        <tbody>
          {rows.map(([key, m]) => (
            <tr key={key} className={key === 'custom' ? 'row-my-algorithm' : ''}>
              <td className="mono policy-name">{key === 'custom' ? `${m.policy} (mine)` : m.policy}</td>
              {METRIC_COLUMNS.map((c) => (
                <td key={c.key} className="mono metric-value">
                  {m[c.key]}{c.suffix ?? ''}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
      <p className="results-integrity-note">
        Raw simulation output under this run's configuration — this does not label any policy
        "better," only what each one produced here.
      </p>
      <ComparisonCharts response={response} />
      <ExplanationPanel protocol={protocol} matrix={response.matrix} />
      {response.layer_gap && <LayerGapPanel layerGap={response.layer_gap} />}
    </div>
  )
}

interface Props {
  running: boolean
  error: ApiError | null
  runs: { protocol: Protocol; response: CompareResponse }[]
}

export default function ResultsPanel({ running, error, runs }: Props) {
  return (
    <div className="results-panel">
      <h3 className="mono">Results</h3>
      {running && <div className="results-placeholder">Running simulation…</div>}
      {!running && error && <ErrorCard error={error} />}
      {!running && !error && runs.length > 0 && (
        <div className="comparison-runs">
          {runs.map((r) => <ComparisonTable key={r.protocol} protocol={r.protocol} response={r.response} />)}
        </div>
      )}
      {!running && !error && runs.length === 0 && (
        <div className="results-placeholder">Select at least one policy and run a simulation to see metrics here.</div>
      )}
    </div>
  )
}
