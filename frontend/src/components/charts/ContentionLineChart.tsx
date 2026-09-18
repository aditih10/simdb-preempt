import type { ChartConfiguration } from 'chart.js/auto'
import { useChart } from './useChart'
import { colorFor, POLICY_LABELS, CHART_CHROME } from '../../palette'

interface Props {
  title: string
  unit?: string
  contentions: number[]
  series: { policyKey: string; values: number[] }[]
}

export default function ContentionLineChart({ title, unit, contentions, series }: Props) {
  const config: ChartConfiguration = {
    type: 'line',
    data: {
      labels: contentions.map((c) => c.toFixed(3)),
      datasets: series.map((s) => ({
        label: POLICY_LABELS[s.policyKey] ?? s.policyKey,
        data: s.values,
        borderColor: colorFor(s.policyKey),
        backgroundColor: colorFor(s.policyKey),
        borderWidth: 2,
        pointRadius: 4,
        pointHoverRadius: 6,
        tension: 0.15,
      })),
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      interaction: { mode: 'index', intersect: false },
      plugins: {
        legend: {
          display: series.length > 1,
          position: 'bottom',
          labels: { color: CHART_CHROME.tickColor, boxWidth: 10, boxHeight: 10, font: { family: 'IBM Plex Mono', size: 11 } },
        },
        tooltip: {
          callbacks: {
            label: (ctx) => `${ctx.dataset.label}: ${ctx.parsed.y}${unit ?? ''}`,
          },
        },
      },
      scales: {
        x: {
          title: { display: true, text: 'contention', color: CHART_CHROME.tickColor },
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
