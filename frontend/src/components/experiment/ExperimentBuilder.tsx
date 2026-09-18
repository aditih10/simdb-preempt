import { useRef, useState } from 'react'
import type { ApiError, Metrics, Protocol } from '../../types'
import type { ConfigOverrides } from '../ConfigPanel'
import { runExperiment, experimentTotalRuns, type ExperimentConfig, type ExperimentResult, type SweepParam } from './runExperiment'
import ParamLineChart from './ParamLineChart'
import ExperimentResultsTable from './ExperimentResultsTable'
import { SimulateError } from '../../types'

interface Props {
  policies: string[]
  algorithm?: string
  overrides: ConfigOverrides
}

const MAX_RUNS = 300

const PARAM_META: Record<SweepParam, { label: string; unit?: string; defaults: { start: number; stop: number; step: number } }> = {
  contention: { label: 'Contention', defaults: { start: 0.1, stop: 0.9, step: 0.1 } },
  preempt_rate: { label: 'Preemption Rate', unit: '/ms', defaults: { start: 0.05, stop: 0.20, step: 0.05 } },
  arrival_rate: { label: 'Arrival Rate', unit: '/ms', defaults: { start: 1, stop: 5, step: 1 } },
  ops_per_txn: { label: 'Ops / Txn', defaults: { start: 2, stop: 10, step: 2 } },
}

const METRIC_CHARTS: { key: keyof Metrics; title: string; unit?: string }[] = [
  { key: 'abort_rate_pct', title: 'Abort Rate', unit: '%' },
  { key: 'throughput_tps', title: 'Throughput', unit: 'tps' },
  { key: 'mean_latency_ms', title: 'Mean Latency', unit: 'ms' },
  { key: 'p99_latency_ms', title: 'P99 Latency', unit: 'ms' },
  { key: 'cpu_ms_wasted_on_aborts', title: 'CPU Waste', unit: 'ms' },
]

function range(start: number, stop: number, step: number): number[] {
  const out: number[] = []
  for (let v = start; v <= stop + 1e-9 && out.length < 10; v += step) {
    out.push(Math.round(v * 1000) / 1000)
  }
  return out
}

export default function ExperimentBuilder({ policies, algorithm, overrides }: Props) {
  const [param, setParam] = useState<SweepParam>('contention')
  const [start, setStart] = useState(PARAM_META.contention.defaults.start)
  const [stop, setStop] = useState(PARAM_META.contention.defaults.stop)
  const [step, setStep] = useState(PARAM_META.contention.defaults.step)
  const [protocols, setProtocols] = useState<Set<Protocol>>(new Set(['2pl']))
  const [replications, setReplications] = useState(5)

  const [running, setRunning] = useState(false)
  const [progress, setProgress] = useState({ done: 0, total: 0 })
  const [error, setError] = useState<ApiError | null>(null)
  const [result, setResult] = useState<ExperimentResult | null>(null)
  const [metricKey, setMetricKey] = useState<keyof Metrics>('abort_rate_pct')
  const cancelRef = useRef(false)

  function handleParamChange(p: SweepParam) {
    setParam(p)
    setStart(PARAM_META[p].defaults.start)
    setStop(PARAM_META[p].defaults.stop)
    setStep(PARAM_META[p].defaults.step)
  }

  function toggleProtocol(p: Protocol) {
    setProtocols((prev) => {
      const next = new Set(prev)
      if (next.has(p)) next.delete(p)
      else next.add(p)
      return next
    })
  }

  const values = range(start, stop, step)
  const cfgPreview: ExperimentConfig = {
    param, values, protocols: Array.from(protocols), replications,
    policies, algorithm, baseOverrides: overrides,
  }
  const totalRuns = experimentTotalRuns(cfgPreview)
  const nothingSelected = policies.length === 0 && !algorithm
  const tooManyRuns = totalRuns > MAX_RUNS
  const noProtocols = protocols.size === 0

  async function handleRun() {
    setRunning(true)
    setError(null)
    setResult(null)
    setProgress({ done: 0, total: totalRuns })
    cancelRef.current = false
    try {
      const res = await runExperiment(
        cfgPreview,
        (done, total) => setProgress({ done, total }),
        () => cancelRef.current,
      )
      setResult(res)
    } catch (e) {
      setError(e instanceof SimulateError ? e.detail : { type: 'bad_request', title: 'Request Failed', message: String(e) })
    } finally {
      setRunning(false)
    }
  }

  function handleCancel() {
    cancelRef.current = true
  }

  return (
    <div className="experiment-builder">
      <div className="config-panel-header">
        <h3 className="mono">Experiment Builder</h3>
      </div>
      <p className="section-note">
        Sweeps one parameter across a range, replicated with independent seeds, and aggregates
        mean/standard-deviation/confidence-interval statistics per policy — not just a single run.
      </p>

      <div className="sweep-controls">
        <label className="config-field">
          <span className="config-field-label">Parameter</span>
          <select className="protocol-select" value={param} onChange={(e) => handleParamChange(e.target.value as SweepParam)}>
            {Object.entries(PARAM_META).map(([key, meta]) => (
              <option key={key} value={key}>{meta.label}</option>
            ))}
          </select>
        </label>
        <label className="config-field">
          <span className="config-field-label">Start</span>
          <input className="mono config-field-input" type="number" step="any" value={start} onChange={(e) => setStart(Number(e.target.value))} />
        </label>
        <label className="config-field">
          <span className="config-field-label">Stop</span>
          <input className="mono config-field-input" type="number" step="any" value={stop} onChange={(e) => setStop(Number(e.target.value))} />
        </label>
        <label className="config-field">
          <span className="config-field-label">Step</span>
          <input className="mono config-field-input" type="number" step="any" value={step} onChange={(e) => setStep(Number(e.target.value))} />
        </label>
        <label className="config-field">
          <span className="config-field-label">Replications</span>
          <input className="mono config-field-input" type="number" min={1} max={20} value={replications}
            onChange={(e) => setReplications(Math.max(1, Math.min(20, Number(e.target.value))))} />
        </label>
      </div>

      <div className="baseline-selector experiment-protocols">
        <span className="config-field-label mono">Protocols</span>
        <label className="baseline-checkbox">
          <input type="checkbox" checked={protocols.has('2pl')} onChange={() => toggleProtocol('2pl')} /> 2PL
        </label>
        <label className="baseline-checkbox">
          <input type="checkbox" checked={protocols.has('occ')} onChange={() => toggleProtocol('occ')} /> OCC
        </label>
      </div>

      <p className="protocol-note">
        {values.length} value{values.length === 1 ? '' : 's'} × {protocols.size} protocol{protocols.size === 1 ? '' : 's'} × {replications} replication{replications === 1 ? '' : 's'} = <strong>{totalRuns} runs</strong>
        {tooManyRuns && <span className="warning-text"> — reduce this below {MAX_RUNS} to run.</span>}
      </p>

      <div className="run-again-bar">
        <button className="btn btn-primary" onClick={handleRun} disabled={running || nothingSelected || tooManyRuns || noProtocols}>
          {running ? 'Running…' : 'Run Experiment'}
        </button>
        {running && <button className="btn btn-ghost btn-small" onClick={handleCancel}>Cancel</button>}
        {progress.total > 0 && (
          <span className="mono progress-readout">
            Run {progress.done} / {progress.total}
          </span>
        )}
      </div>
      {progress.total > 0 && (running || progress.done > 0) && (
        <div className="progress-bar-track">
          <div className="progress-bar-fill" style={{ width: `${(progress.done / progress.total) * 100}%` }} />
        </div>
      )}

      {nothingSelected && <p className="results-placeholder">Select at least one baseline or "My Algorithm" above to run an experiment.</p>}
      {noProtocols && <p className="results-placeholder">Select at least one protocol.</p>}
      {error && (
        <div className="result-error-card">
          <div className="result-error-title mono">{error.title}</div>
          <div className="result-error-message">{error.message}</div>
        </div>
      )}

      {result && (
        <div className="experiment-results">
          {result.config.protocols.map((protocol) => {
            const protoPoints = result.points.filter((p) => p.protocol === protocol)
            if (protoPoints.length === 0) return null
            const seriesKeys = Array.from(new Set(protoPoints.flatMap((p) => Object.keys(p.byPolicy))))
            return (
              <div key={protocol} className="experiment-protocol-block">
                <div className="metrics-table-header mono">{protocol.toUpperCase()}</div>
                <div className="chart-grid">
                  {METRIC_CHARTS.map((m) => (
                    <ParamLineChart
                      key={m.key}
                      title={m.title}
                      unit={m.unit}
                      xLabel={PARAM_META[result.config.param].label}
                      xValues={protoPoints.map((p) => p.paramValue)}
                      series={seriesKeys.map((key) => ({
                        policyKey: key,
                        values: protoPoints.map((p) => {
                          const reps = p.byPolicy[key] ?? []
                          return reps.length ? reps.reduce((s, r) => s + (r[m.key] as number), 0) / reps.length : 0
                        }),
                      }))}
                    />
                  ))}
                </div>

                <div className="config-panel-header">
                  <h4 className="mono">Statistics</h4>
                  <select className="protocol-select" value={metricKey} onChange={(e) => setMetricKey(e.target.value as keyof Metrics)}>
                    {METRIC_CHARTS.map((m) => <option key={m.key} value={m.key}>{m.title}</option>)}
                  </select>
                </div>
                <ExperimentResultsTable
                  points={protoPoints}
                  metricKey={metricKey}
                  unit={METRIC_CHARTS.find((m) => m.key === metricKey)?.unit}
                />
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}
