import type { Marker, Segment } from '../../types'

interface Props {
  policyKey: string
  policyLabel: string
  segments: Segment[]
  markers: Marker[]
  window: [number, number]
  cursorTime: number | null
  selectedTxn: number | null
  onSelectTxn: (txn: number) => void
}

function pct(t: number, window: [number, number]): number {
  const [start, end] = window
  return Math.max(0, Math.min(100, ((t - start) / (end - start)) * 100))
}

export default function TimelineTrack({
  policyKey, policyLabel, segments, markers, window, cursorTime, selectedTxn, onSelectTxn,
}: Props) {
  return (
    <div className="timeline-row">
      <div className="timeline-label">
        <span className="name mono">{policyLabel}</span>
        <span className="stat mono">{segments.length} segments · {new Set(segments.map((s) => s.txn)).size} transactions</span>
      </div>
      <div className="timeline-track" data-policy={policyKey}>
        {segments.map((s, i) => (
          <div
            key={i}
            className={`seg seg-${s.kind}${selectedTxn === s.txn ? ' seg-selected' : ''}`}
            style={{ left: `${pct(s.start, window)}%`, width: `${Math.max(0.3, pct(s.end, window) - pct(s.start, window))}%` }}
            title={`TX${s.txn} · ${s.kind} · ${s.start.toFixed(2)}–${s.end.toFixed(2)}ms`}
            onClick={() => onSelectTxn(s.txn)}
          />
        ))}
        {markers.filter((m) => m.event === 'commit' || m.event === 'abort').map((m, i) => (
          <div
            key={`m${i}`}
            className={`marker marker-${m.event}`}
            style={{ left: `${pct(m.t, window)}%` }}
            title={`TX${m.txn} ${m.event} @ ${m.t.toFixed(2)}ms`}
          />
        ))}
        {cursorTime != null && (
          <div className="timeline-cursor" style={{ left: `${pct(cursorTime, window)}%` }} />
        )}
      </div>
    </div>
  )
}
