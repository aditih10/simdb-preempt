import type { Marker, Segment } from '../../types'

export interface LogEvent {
  t: number
  txn: number | null
  text: string
}

/** Converts raw segments/markers into the "t=... TX2080 -> waiting for
 * lock 211" style event log the spec asks for. Every line is derived
 * directly from real segment/marker data — nothing invented. Plain "cpu"
 * segments are omitted (too frequent to be a useful log line); every
 * other segment kind and every marker becomes one or two lines. */
export function buildEventLog(segments: Segment[], markers: Marker[]): LogEvent[] {
  const events: LogEvent[] = []

  // Markers first: at an equal timestamp, the OS/policy decision is what
  // CAUSES the transaction-side effect (e.g. "OS -> preemption attempt" /
  // "Policy -> ALLOW" narratively precede "TX -> preempted, penalty
  // begins"), and "arrived" naturally precedes anything else at that
  // instant. The stable sort below preserves this as the tie-break order.
  for (const m of markers) {
    if (m.event === 'commit') events.push({ t: m.t, txn: m.txn, text: `TX${m.txn} → commit` })
    else if (m.event === 'abort') events.push({ t: m.t, txn: m.txn, text: `TX${m.txn} → abort` })
    else if (m.event === 'arrival') events.push({ t: m.t, txn: m.txn, text: `TX${m.txn} → arrived` })
    else if (m.event === 'os_defer') {
      events.push({ t: m.t, txn: m.txn, text: 'OS → preemption attempt' })
      events.push({ t: m.t, txn: m.txn, text: 'Policy → DEFER' })
    } else if (m.event === 'os_preempt') {
      events.push({ t: m.t, txn: m.txn, text: 'OS → preemption attempt' })
      events.push({ t: m.t, txn: m.txn, text: 'Policy → ALLOW (preempted)' })
    }
  }

  for (const s of segments) {
    if (s.kind === 'lock_wait') {
      events.push({ t: s.start, txn: s.txn, text: `TX${s.txn} → waiting for lock ${s.key}` })
      events.push({ t: s.end, txn: s.txn, text: `TX${s.txn} → lock acquired` })
    } else if (s.kind === 'preempt_penalty') {
      events.push({ t: s.start, txn: s.txn, text: `TX${s.txn} → preempted, context-switch penalty begins` })
      events.push({ t: s.end, txn: s.txn, text: `TX${s.txn} → resumed` })
    } else if (s.kind === 'validation') {
      events.push({ t: s.start, txn: s.txn, text: `TX${s.txn} → OCC validation begins` })
      events.push({ t: s.end, txn: s.txn, text: `TX${s.txn} → OCC validation complete` })
    }
  }

  return events
    .map((e, i) => ({ ...e, _i: i }))
    .sort((a, b) => (a.t - b.t) || (a._i - b._i))
    .map(({ _i, ...e }) => e)
}
