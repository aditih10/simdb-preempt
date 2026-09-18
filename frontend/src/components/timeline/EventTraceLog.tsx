import { useMemo, useRef, useEffect } from 'react'
import type { Marker, Segment } from '../../types'
import { buildEventLog } from './eventLog'

interface Props {
  segments: Segment[]
  markers: Marker[]
  cursorTime: number | null
  selectedTxn: number | null
  onSelectTxn: (txn: number) => void
}

export default function EventTraceLog({ segments, markers, cursorTime, selectedTxn, onSelectTxn }: Props) {
  const events = useMemo(() => buildEventLog(segments, markers), [segments, markers])
  const activeRef = useRef<HTMLDivElement | null>(null)

  const activeIndex = useMemo(() => {
    if (cursorTime == null || events.length === 0) return -1
    let idx = -1
    for (let i = 0; i < events.length; i++) {
      if (events[i].t <= cursorTime) idx = i
      else break
    }
    return idx
  }, [cursorTime, events])

  useEffect(() => {
    activeRef.current?.scrollIntoView({ block: 'nearest' })
  }, [activeIndex])

  return (
    <div className="event-log">
      {events.length === 0 && <div className="results-placeholder">No events in this window.</div>}
      {events.map((e, i) => (
        <div
          key={i}
          ref={i === activeIndex ? activeRef : undefined}
          className={
            'event-log-row mono' +
            (i === activeIndex ? ' event-log-row-active' : '') +
            (e.txn != null && e.txn === selectedTxn ? ' event-log-row-selected' : '')
          }
          onClick={() => e.txn != null && onSelectTxn(e.txn)}
        >
          <span className="event-log-t">t={e.t.toFixed(3)}</span>
          <span className="event-log-text">{e.text}</span>
        </div>
      ))}
    </div>
  )
}
