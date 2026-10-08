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

/** A single pulled-out "headline" delta for this run, purely for visual
 * emphasis — derived from numbers already in the matrix, the same way
 * ExplanationPanel derives its narrative bullets. Picks the protocol's
 * own most-affected metric (P99 latency for 2PL, abort rate for OCC),
 * matching the judgment call already encoded in backend/main.py's
 * layer-gap metric selection. Returns null rather than guess when
 * either baseline is missing from this run's selection. */
function headlineStat(protocol: Protocol, matrix: Record<string, Metrics>): { value: string; label: string } | null {
  if (!('none' in matrix) || !('random' in matrix)) return null
  const n = matrix.none, r = matrix.random
  if (protocol === '2pl') {
    const delta = r.p99_latency_ms - n.p99_latency_ms
    return { value: `${delta >= 0 ? '+' : ''}${delta.toFixed(2)}ms`, label: 'P99 latency, random vs. none' }
  }
  const delta = r.abort_rate_pct - n.abort_rate_pct
  return { value: `${delta >= 0 ? '+' : ''}${delta.toFixed(2)}pp`, label: 'Abort rate, random vs. none' }
}

function ComparisonTable({ protocol, response }: { protocol: Protocol; response: CompareResponse }) {
  const rows = Object.entries(response.matrix)
  const headline = headlineStat(protocol, response.matrix)
  return (
    <div className="comparison-block">
      <div className="comparison-head">
        <span className="comparison-protocol mono">{protocol.toUpperCase()}</span>
        {headline && (
          <div className="comparison-headline">
            <span className="comparison-headline-value">{headline.value}</span>
            <span className="comparison-headline-label mono">{headline.label}</span>
          </div>
        )}
      </div>
      <div className="comparison-table-wrap">
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
      </div>
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
