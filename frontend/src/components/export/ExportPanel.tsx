import { useEffect, useState } from 'react'
import type { Protocol } from '../../types'
import type { ConfigOverrides } from '../ConfigPanel'
import { buildRunRecord, comparisonToCSVRows, downloadCSV, downloadJSON, sha256Hex, type RunBatch } from '../../export'

interface Props {
  protocolChoice: Protocol | 'both'
  selectedBaselines: string[]
  includeMyAlgorithm: boolean
  source: string
  overrides: ConfigOverrides
  runs: RunBatch
}

export default function ExportPanel({ protocolChoice, selectedBaselines, includeMyAlgorithm, source, overrides, runs }: Props) {
  const [hash, setHash] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    if (includeMyAlgorithm && source) {
      sha256Hex(source).then((h) => { if (!cancelled) setHash(h) })
    } else {
      setHash(null)
    }
    return () => { cancelled = true }
  }, [includeMyAlgorithm, source])

  if (runs.length === 0) return null

  async function handleDownloadJSON() {
    const record = await buildRunRecord({ protocolChoice, selectedBaselines, includeMyAlgorithm, source, overrides, runs })
    downloadJSON(`simdb-preempt-run-${Date.now()}.json`, record)
  }

  function handleDownloadCSV() {
    downloadCSV(`simdb-preempt-run-${Date.now()}.csv`, comparisonToCSVRows(runs))
  }

  return (
    <div className="export-panel">
      <div className="config-panel-header">
        <h3 className="mono">Export</h3>
      </div>
      <p className="section-note">
        Download this run's metrics, resolved config, seed, and algorithm source. The JSON export
        is a full run record — reload it below under "Reproduce a Run" to confirm the simulator
        produces the exact same numbers from the same inputs.
      </p>
      <div className="run-again-bar">
        <button className="btn btn-ghost btn-small" onClick={handleDownloadJSON}>Download JSON</button>
        <button className="btn btn-ghost btn-small" onClick={handleDownloadCSV}>Download CSV</button>
        {hash && (
          <span className="mono export-hash" title={`Full SHA-256 of the submitted algorithm source: ${hash}`}>
            Algorithm SHA-256: {hash.slice(0, 12)}…
          </span>
        )}
      </div>
    </div>
  )
}
