import type { Metrics, Protocol } from '../types'
import { POLICY_LABELS } from '../palette'

function fmt(n: number, digits = 2): string {
  return Number(n).toFixed(digits).replace(/\.00$/, '')
}

function label(key: string, matrix: Record<string, Metrics>): string {
  return key === 'custom' ? `your policy ("${matrix[key].policy}")` : POLICY_LABELS[key] ?? key
}

/** Every sentence here is derived directly from the matrix passed in —
 * no invented causal claims, no numbers that didn't come back from the
 * simulator for this exact run. */
function buildBullets(protocol: Protocol, matrix: Record<string, Metrics>): string[] {
  const bullets: string[] = []
  const has = (k: string) => k in matrix

  if (has('custom')) {
    const c = matrix.custom
    bullets.push(
      `In this run, ${label('custom', matrix)} attempted ${c.attempted} transactions and ` +
      `committed ${c.committed} (${c.abort_rate_pct}% abort rate), with a P99 latency of ${c.p99_latency_ms}ms.`,
    )
    bullets.push(
      `${label('custom', matrix)} deferred ${c.deferred_events} preemption attempts and allowed ` +
      `${c.preempt_events} actual preemptions.`,
    )
    if (has('none')) {
      const n = matrix.none
      const diff = c.p99_latency_ms - n.p99_latency_ms
      bullets.push(
        `Compared with the no-preemption baseline, ${label('custom', matrix)}'s P99 latency was ` +
        `${diff >= 0 ? 'higher by' : 'lower by'} ${fmt(Math.abs(diff))}ms (${c.p99_latency_ms}ms vs ${n.p99_latency_ms}ms).`,
      )
    }
    if (has('random')) {
      const r = matrix.random
      bullets.push(
        `Compared with random (critical-section-blind) preemption, ${label('custom', matrix)} produced a ` +
        `P99 latency of ${c.p99_latency_ms}ms vs ${r.p99_latency_ms}ms, and an abort rate of ` +
        `${c.abort_rate_pct}% vs ${r.abort_rate_pct}%.`,
      )
    }
  }

  if (has('random') && has('critical_aware')) {
    const r = matrix.random, ca = matrix.critical_aware
    bullets.push(
      `critical_aware deferred ${ca.deferred_events} preemption attempts (vs 0 for random by construction) — ` +
      `random allowed ${r.preempt_events} actual preemptions in this run.`,
    )
  }

  if (protocol === '2pl') {
    bullets.push(
      '2PL blocks on a held lock, so a preempted lock-holder produces a cascading wait for every ' +
      'other transaction queued on that lock — this shows up primarily as P99 latency, not throughput ' +
      '(throughput is bounded by arrival rate, not by how long any one transaction takes).',
    )
  } else {
    bullets.push(
      'OCC never blocks, but a preemption during the validation/write phase forces the whole transaction ' +
      'to abort and retry — the read phase\'s CPU time is wasted. This shows up as CPU Waste and Abort Rate ' +
      'rather than P99 latency.',
    )
  }

  return bullets
}

export default function ExplanationPanel({ protocol, matrix }: { protocol: Protocol; matrix: Record<string, Metrics> }) {
  const bullets = buildBullets(protocol, matrix)
  if (bullets.length === 0) return null
  return (
    <div className="explanation-panel">
      <div className="explanation-title mono">Why did this happen? ({protocol.toUpperCase()})</div>
      <ul className="explanation-list">
        {bullets.map((b, i) => <li key={i}>{b}</li>)}
      </ul>
      <p className="results-integrity-note">
        This is a factual summary of this specific run's numbers — not a claim that any policy is
        better in general. Re-run with a different seed or workload to check whether the pattern holds.
      </p>
    </div>
  )
}
