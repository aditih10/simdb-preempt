import type { CompareResponse, Metrics } from '../types'
import PolicyBarChart from './charts/PolicyBarChart'

const METRIC_CHARTS: { key: keyof Metrics; title: string; unit?: string }[] = [
  { key: 'abort_rate_pct', title: 'Abort Rate', unit: '%' },
  { key: 'throughput_tps', title: 'Throughput', unit: 'tps' },
  { key: 'mean_latency_ms', title: 'Mean Latency', unit: 'ms' },
  { key: 'p99_latency_ms', title: 'P99 Latency', unit: 'ms' },
  { key: 'cpu_ms_wasted_on_aborts', title: 'CPU Waste', unit: 'ms' },
  { key: 'preempt_events', title: 'Preemptions' },
  { key: 'deferred_events', title: 'Deferred Preemptions' },
]

export default function ComparisonCharts({ response }: { response: CompareResponse }) {
  const entries = Object.entries(response.matrix)
  return (
    <div className="chart-grid">
      {METRIC_CHARTS.map((m) => (
        <PolicyBarChart
          key={m.key}
          title={m.title}
          unit={m.unit}
          data={entries.map(([key, metrics]) => ({ policyKey: key, value: metrics[m.key] as number }))}
        />
      ))}
    </div>
  )
}
