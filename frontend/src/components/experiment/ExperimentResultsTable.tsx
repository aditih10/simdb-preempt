import type { Metrics } from '../../types'
import { POLICY_LABELS } from '../../palette'
import { summarize } from '../../stats'
import { downloadCSV } from '../../export'
import type { ExperimentPoint } from './runExperiment'

interface Props {
  points: ExperimentPoint[]
  metricKey: keyof Metrics
  unit?: string
}

export default function ExperimentResultsTable({ points, metricKey, unit }: Props) {
  function handleDownloadCSV() {
    const header = ['policy', 'param_value', 'n', 'mean', 'stddev', 'ci_low', 'ci_high']
    const rows: (string | number)[][] = [header]
    for (const point of points) {
      for (const [policyKey, reps] of Object.entries(point.byPolicy)) {
        const values = reps.map((m) => m[metricKey] as number)
        const s = summarize(values)
        rows.push([
          POLICY_LABELS[policyKey] ?? policyKey,
          point.paramValue,
          s.n,
          s.mean,
          s.n >= 2 ? s.stddev : '',
          s.sufficientForCI ? s.ciLow : '',
          s.sufficientForCI ? s.ciHigh : '',
        ])
      }
    }
    downloadCSV(`simdb-preempt-experiment-${metricKey}-${Date.now()}.csv`, rows)
  }

  return (
    <div className="comparison-table-wrap">
      <div className="run-again-bar">
        <button className="btn btn-ghost btn-small" onClick={handleDownloadCSV}>Download CSV</button>
      </div>
      <table className="comparison-table experiment-table">
        <thead>
          <tr>
            <th>Policy</th>
            <th className="mono">Param Value</th>
            <th className="mono">N</th>
            <th className="mono">Mean</th>
            <th className="mono">Std Dev</th>
            <th className="mono">95% CI</th>
          </tr>
        </thead>
        <tbody>
          {points.flatMap((point) =>
            Object.entries(point.byPolicy).map(([policyKey, reps]) => {
              const values = reps.map((m) => m[metricKey] as number)
              const s = summarize(values)
              return (
                <tr key={`${point.paramValue}-${policyKey}`}>
                  <td className="mono policy-name">{POLICY_LABELS[policyKey] ?? policyKey}</td>
                  <td className="mono metric-value">{point.paramValue.toFixed(3)}</td>
                  <td className="mono metric-value">{s.n}</td>
                  <td className="mono metric-value">{s.mean.toFixed(3)}{unit ?? ''}</td>
                  <td className="mono metric-value">{s.n >= 2 ? `${s.stddev.toFixed(3)}${unit ?? ''}` : '—'}</td>
                  <td className="mono metric-value">
                    {s.sufficientForCI
                      ? `${s.ciLow.toFixed(3)} – ${s.ciHigh.toFixed(3)}${unit ?? ''}`
                      : `n/a (N<3)`}
                  </td>
                </tr>
              )
            }),
          )}
        </tbody>
      </table>
      <p className="results-integrity-note">
        A single run (N=1) is never statistically significant — no standard deviation or
        confidence interval is computed for it. Confidence intervals only appear at N≥3 and use a
        Student's-t critical value for the given sample size, so they widen appropriately for
        small N rather than overclaiming precision.
      </p>
    </div>
  )
}
