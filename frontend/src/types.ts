export type Protocol = '2pl' | 'occ'

export interface PolicyInfo {
  key: string
  name: string
  description: string
}

export interface Template {
  key: string
  title: string
  description: string
  source: string
}

export interface Config {
  num_cores: number
  db_size: number
  contention: number
  ops_per_txn: number
  mean_op_time: number
  lock_timeout: number
  preempt_rate: number
  preempt_penalty: number
  sim_time: number
  arrival_rate: number
  seed: number
}

export interface Metrics {
  protocol: Protocol
  policy: string
  attempted: number
  committed: number
  aborted: number
  abort_rate_pct: number
  throughput_tps: number
  mean_latency_ms: number
  p99_latency_ms: number
  cpu_ms_wasted_on_aborts: number
  preempt_events: number
  deferred_events: number
}

export interface Metadata {
  simulator_version: string
  timestamp: string
  protocol: Protocol
  policies: string[]
  config: Config
}

export interface SimulateResponse {
  metrics: Metrics
  metadata: Metadata
}

export interface CompareResponse {
  matrix: Record<string, Metrics>
  layer_gap: Record<string, Record<string, number>> | null
  metadata: Metadata
}

export interface SweepResponse {
  contentions: number[]
  series: Record<string, Metrics[]>
  metadata: Metadata
}

export type SegmentKind = 'cpu' | 'lock_wait' | 'preempt_penalty' | 'validation'
export type MarkerEvent = 'commit' | 'abort' | 'arrival' | 'os_defer' | 'os_preempt'

export interface Segment {
  txn: number
  kind: SegmentKind
  start: number
  end: number
  key?: number
}

export interface Marker {
  txn: number
  event: MarkerEvent
  t: number
}

export interface TransactionSummary {
  txn: number
  arrival: number | null
  status: 'commit' | 'abort' | 'in_flight'
  end_time: number | null
  cpu_ms: number
  wait_ms: number
  preempt_count: number
  critical_ms: number
}

export interface PolicyTrace {
  segments: Segment[]
  markers: Marker[]
  transactions: TransactionSummary[]
  metrics: Metrics
}

export interface TraceResponse {
  window: [number, number]
  core_id: number
  traces: Record<string, PolicyTrace>
  metadata: Metadata
}

/** Matches the {type, title, message, line} shape emitted by
 * simulator/sandbox.py + simulator/sandbox_runner.py and surfaced as the
 * `detail` of a FastAPI HTTPException (see backend/main.py). */
export interface ApiError {
  type: 'validation_error' | 'runtime_error' | 'unsupported_api' | 'timeout'
    | 'output_too_large' | 'bad_request' | string
  title: string
  message: string
  line?: number | null
}

export class SimulateError extends Error {
  detail: ApiError
  constructor(detail: ApiError) {
    super(detail.message)
    this.detail = detail
  }
}
