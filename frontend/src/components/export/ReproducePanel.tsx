import { useRef, useState } from 'react'
import { parseRunRecord, RunRecordParseError, type ReproductionCheck, type RunRecordV1 } from '../../export'

interface Props {
  onLoad: (record: RunRecordV1) => Promise<ReproductionCheck>
  running: boolean
}

export default function ReproducePanel({ onLoad, running }: Props) {
  const [error, setError] = useState<string | null>(null)
  const [check, setCheck] = useState<ReproductionCheck | null>(null)
  const [loadedFrom, setLoadedFrom] = useState<string | null>(null)
  const fileInputRef = useRef<HTMLInputElement>(null)

  async function handleFile(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0]
    if (!file) return
    setError(null)
    setCheck(null)
    try {
      const text = await file.text()
      const record = parseRunRecord(JSON.parse(text))
      setLoadedFrom(file.name)
      const result = await onLoad(record)
      setCheck(result)
    } catch (err) {
      setError(err instanceof RunRecordParseError ? err.message : `Could not load run record: ${String(err)}`)
    } finally {
      if (fileInputRef.current) fileInputRef.current.value = ''
    }
  }

  return (
    <div className="reproduce-panel">
      <div className="config-panel-header">
        <h3 className="mono">Reproduce a Run</h3>
      </div>
      <p className="section-note">
        Load a JSON run record exported above. It restores the protocol, config overrides, seed,
        selected baselines, and algorithm source exactly as they were, re-runs the simulation,
        and checks whether every returned metric matches the recorded output exactly — these are
        deterministic seeded simulations, so "reproduced" means an exact match, not a close one.
      </p>
      <input ref={fileInputRef} type="file" accept="application/json" onChange={handleFile} disabled={running} />
      {loadedFrom && <p className="section-note mono">Loaded: {loadedFrom}</p>}
      {error && (
        <div className="result-error-card">
          <div className="result-error-title mono">Could Not Load Run Record</div>
          <div className="result-error-message">{error}</div>
        </div>
      )}
      {check && (
        <div className={`reproduction-check ${check.allMatch ? 'reproduction-pass' : 'reproduction-fail'}`}>
          <div className="mono reproduction-check-title">
            {check.allMatch
              ? 'Reproduction PASSED — every metric matches the recorded run exactly.'
              : 'Reproduction MISMATCH — see the differing fields below.'}
          </div>
          {check.hashVerified === false && (
            <div className="mono reproduction-check-title reproduction-fail-text">
              Warning: the record's stored algorithm hash does not match its own embedded source —
              this export file may have been edited by hand after it was downloaded.
            </div>
          )}
          <div className="comparison-table-wrap">
            <table className="comparison-table">
              <thead>
                <tr><th>Protocol</th><th>Policy</th><th>Result</th><th>Mismatched Fields</th></tr>
              </thead>
              <tbody>
                {check.policies.map((p) => (
                  <tr key={`${p.protocol}-${p.policy}`}>
                    <td className="mono">{p.protocol.toUpperCase()}</td>
                    <td className="mono policy-name">{p.policy}</td>
                    <td className={`mono ${p.match ? '' : 'reproduction-fail-text'}`}>
                      {p.missing ? 'no data in reproduced run' : p.match ? 'match' : 'MISMATCH'}
                    </td>
                    <td className="mono">
                      {p.mismatches.map((m) => `${m.field}: ${m.recorded} → ${m.reproduced}`).join('; ') || '—'}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  )
}
