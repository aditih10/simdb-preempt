import type { Protocol, TransactionSummary } from '../../types'

interface Props {
  protocol: Protocol
  txn: TransactionSummary | null
}

const STATUS_LABEL: Record<string, string> = {
  commit: 'committed',
  abort: 'aborted',
  in_flight: 'still running at end of window',
}

export default function TransactionDetailPanel({ protocol, txn }: Props) {
  if (!txn) {
    return (
      <div className="txn-detail-panel">
        <h4 className="mono">Transaction detail</h4>
        <p className="results-placeholder">Click a segment or event line to inspect a transaction.</p>
      </div>
    )
  }
  return (
    <div className="txn-detail-panel">
      <h4 className="mono">Transaction TX{txn.txn}</h4>
      <table className="txn-detail-table">
        <tbody>
          <tr><td>Protocol</td><td className="mono">{protocol.toUpperCase()}</td></tr>
          <tr><td>Status</td><td className={`mono status-${txn.status}`}>{STATUS_LABEL[txn.status] ?? txn.status}</td></tr>
          <tr><td>Arrival</td><td className="mono">{txn.arrival != null ? `${txn.arrival.toFixed(3)}ms` : 'before window'}</td></tr>
          <tr><td>End time</td><td className="mono">{txn.end_time != null ? `${txn.end_time.toFixed(3)}ms` : '—'}</td></tr>
          <tr><td>CPU time</td><td className="mono">{txn.cpu_ms.toFixed(3)}ms</td></tr>
          <tr><td>Wait time</td><td className="mono">{txn.wait_ms.toFixed(3)}ms</td></tr>
          <tr><td>Preemptions</td><td className="mono">{txn.preempt_count}</td></tr>
          <tr><td>Critical-section time</td><td className="mono">{txn.critical_ms.toFixed(3)}ms</td></tr>
        </tbody>
      </table>
    </div>
  )
}
