import { useState } from 'react'
import type { ApiError, Metrics, Protocol, SweepResponse } from '../types'
import type { ConfigOverrides } from './ConfigPanel'
import { sweep } from '../api'
import { SimulateError } from '../types'
import ContentionLineChart from './charts/ContentionLineChart'
import { downloadCSV, sweepToCSVRows } from '../export'

interface Props {
  protocol: Protocol
  policies: string[]
  algorithm?: string
  overrides: ConfigOverrides
}

const METRIC_CHARTS: { key: keyof Metrics; title: string; unit?: string }[] = [
  { key: 'abort_rate_pct', title: 'Abort Rate vs Contention', unit: '%' },
  { key: 'throughput_tps', title: 'Throughput vs Contention', unit: 'tps' },
  { key: 'mean_latency_ms', title: 'Mean Latency vs Contention', unit: 'ms' },
  { key: 'p99_latency_ms', title: 'P99 Latency vs Contention', unit: 'ms' },
  { key: 'cpu_ms_wasted_on_aborts', title: 'CPU Waste vs Contention', unit: 'ms' },
]

function range(start: number, stop: number, step: number): number[] {
  const out: number[] = []
  for (let v = start; v <= stop + 1e-9 && out.length < 10; v += step) {
    out.push(Math.round(v * 1000) / 1000)
  }
  return out
}

export default function SweepPanel({ protocol, policies, algorithm, overrides }: Props) {
  const [start, setStart] = useState(0.1)
  const [stop, setStop] = useState(0.9)
  const [step, setStep] = useState(0.2)
  const [running, setRunning] = useState(false)
  const [error, setError] = useState<ApiError | null>(null)
  const [result, setResult] = useState<SweepResponse | null>(null)

  const nothingSelected = policies.length === 0 && !algorithm

  async function handleRun() {
    setRunning(true)
    setError(null)
    try {
      const contentions = range(start, stop, step)
      const res = await sweep({
        protocol,
        policies,
        algorithm,
        contentions,
        config: overrides,
      })
      setResult(res)
    } catch (e) {
      setError(e instanceof SimulateError ? e.detail : { type: 'bad_request', title: 'Request Failed', message: String(e) })
    } finally {
      setRunning(false)
    }
  }

  return (
    <div className="sweep-panel">
      <div className="config-panel-header">
        <h3 className="mono">Sensitivity: Metric vs Contention</h3>
      </div>
      <p className="section-note">
        Sweeps the contention knob across the range below, holding every other workload
        parameter at its current value, and re-runs each selected policy at every point.
      </p>
      <div className="sweep-controls">
        <label className="config-field">
          <span className="config-field-label">Start</span>
          <input className="mono config-field-input" type="number" min={0} max={0.99} step={0.05}
            value={start} onChange={(e) => setStart(Number(e.target.value))} />
        </label>
        <label className="config-field">
          <span className="config-field-label">Stop</span>
          <input className="mono config-field-input" type="number" min={0} max={0.999} step={0.05}
            value={stop} onChange={(e) => setStop(Number(e.target.value))} />
        </label>
        <label className="config-field">
          <span className="config-field-label">Step</span>
          <input className="mono config-field-input" type="number" min={0.01} max={0.5} step={0.01}
            value={step} onChange={(e) => setStep(Number(e.target.value))} />
        </label>
        <button className="btn btn-primary" onClick={handleRun} disabled={running || nothingSelected}>
          {running ? 'Running…' : 'Run Contention Sweep'}
        </button>
      </div>
      {nothingSelected && (
        <p className="results-placeholder">Select at least one baseline or "My Algorithm" above to sweep.</p>
      )}
      {error && (
        <div className="result-error-card">
          <div className="result-error-title mono">{error.title}</div>
          <div className="result-error-message">{error.message}</div>
        </div>
      )}
      {result && (
        <>
          <div className="run-again-bar">
            <button
              className="btn btn-ghost btn-small"
              onClick={() => downloadCSV(`simdb-preempt-sweep-${Date.now()}.csv`, sweepToCSVRows(result))}
            >
              Download CSV
            </button>
          </div>
          <div className="chart-grid">
          {METRIC_CHARTS.map((m) => (
            <ContentionLineChart
              key={m.key}
              title={m.title}
              unit={m.unit}
              contentions={result.contentions}
              series={Object.entries(result.series).map(([policyKey, points]) => ({
                policyKey,
                values: points.map((p) => p[m.key] as number),
              }))}
            />
          ))}
          </div>
        </>
      )}
    </div>
  )
}
