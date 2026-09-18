import type { ChartConfiguration } from 'chart.js/auto'
import { useChart } from './useChart'
import { colorFor, POLICY_LABELS, CHART_CHROME } from '../../palette'

interface Props {
  title: string
  unit?: string
  data: { policyKey: string; value: number }[]
  formatValue?: (v: number) => string
}

export default function PolicyBarChart({ title, unit, data, formatValue }: Props) {
  const config: ChartConfiguration = {
    type: 'bar',
    data: {
      labels: data.map((d) => POLICY_LABELS[d.policyKey] ?? d.policyKey),
      datasets: [{
        data: data.map((d) => d.value),
        backgroundColor: data.map((d) => colorFor(d.policyKey)),
        borderRadius: 4,
        borderSkipped: false,
        maxBarThickness: 48,
      }],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: { display: false },
        tooltip: {
          callbacks: {
            label: (ctx) => {
              const v = ctx.parsed.y ?? 0
              return formatValue ? formatValue(v) : `${v}${unit ?? ''}`
            },
          },
        },
      },
      scales: {
        x: {
          grid: { display: false },
          ticks: { color: CHART_CHROME.tickColor, font: { family: 'IBM Plex Mono', size: 11 } },
        },
        y: {
          beginAtZero: true,
          grid: { color: CHART_CHROME.gridColor },
          ticks: { color: CHART_CHROME.tickColor, font: { family: 'IBM Plex Mono', size: 11 } },
        },
      },
    },
  }
  const canvasRef = useChart(config)

  return (
    <div className="chart-panel">
      <div className="chart-title">{title}{unit ? ` (${unit})` : ''}</div>
      <div className="chart-canvas-wrap">
        <canvas ref={canvasRef} />
      </div>
    </div>
  )
}
