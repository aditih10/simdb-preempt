import { useEffect, useRef } from 'react'
import { Chart, type ChartConfiguration } from 'chart.js/auto'

/** Creates a Chart.js instance on a canvas and keeps it in sync with
 * `config` across re-renders, destroying it on unmount. Plain chart.js
 * (no React wrapper library) to keep the dependency footprint small. */
export function useChart(config: ChartConfiguration) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null)
  const chartRef = useRef<Chart | null>(null)

  useEffect(() => {
    if (!canvasRef.current) return
    chartRef.current = new Chart(canvasRef.current, config)
    return () => chartRef.current?.destroy()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [JSON.stringify(config.data), config.type])

  return canvasRef
}
