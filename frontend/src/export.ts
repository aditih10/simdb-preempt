/**
 * Export & reproducibility (Phase 8): downloading a run's metrics/config as
 * CSV/JSON, hashing the submitted algorithm source so an exported record is
 * self-describing about exactly which policy code produced it, and
 * re-running an exported record to verify the simulator reproduces it
 * exactly (same seed + config + algorithm -> same numbers, per the
 * determinism established since Phase 1/2).
 */

import type { CompareResponse, Metrics, Protocol, SweepResponse } from './types'
import type { ConfigOverrides } from './components/ConfigPanel'

export type RunBatch = { protocol: Protocol; response: CompareResponse }[]

export async function sha256Hex(text: string): Promise<string> {
  const bytes = new TextEncoder().encode(text)
  const digest = await crypto.subtle.digest('SHA-256', bytes)
  return Array.from(new Uint8Array(digest)).map((b) => b.toString(16).padStart(2, '0')).join('')
}

function downloadText(filename: string, mime: string, content: string) {
  const blob = new Blob([content], { type: mime })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  a.click()
  URL.revokeObjectURL(url)
}

export function downloadJSON(filename: string, data: unknown) {
  downloadText(filename, 'application/json', JSON.stringify(data, null, 2))
}

function csvCell(v: string | number): string {
  const s = String(v)
  return /[",\r\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s
}

export function toCSV(rows: (string | number)[][]): string {
  return rows.map((row) => row.map(csvCell).join(',')).join('\r\n')
}

export function downloadCSV(filename: string, rows: (string | number)[][]) {
  downloadText(filename, 'text/csv', toCSV(rows))
}

export const RUN_RECORD_VERSION = 1 as const

export interface RunRecordV1 {
  version: 1
  exported_at: string
  protocolChoice: Protocol | 'both'
  selectedBaselines: string[]
  includeMyAlgorithm: boolean
  algorithmSource: string | null
  algorithmHash: string | null
  overrides: ConfigOverrides
  runs: RunBatch
}

export async function buildRunRecord(args: {
  protocolChoice: Protocol | 'both'
  selectedBaselines: string[]
  includeMyAlgorithm: boolean
  source: string
  overrides: ConfigOverrides
  runs: RunBatch
}): Promise<RunRecordV1> {
  const algorithmSource = args.includeMyAlgorithm ? args.source : null
  const algorithmHash = algorithmSource != null ? await sha256Hex(algorithmSource) : null
  return {
    version: RUN_RECORD_VERSION,
    exported_at: new Date().toISOString(),
    protocolChoice: args.protocolChoice,
    selectedBaselines: args.selectedBaselines,
    includeMyAlgorithm: args.includeMyAlgorithm,
    algorithmSource,
    algorithmHash,
    overrides: args.overrides,
    runs: args.runs,
  }
}

export class RunRecordParseError extends Error {}

/** Structural validation only — trusts the file content as data, same as
 * any other user-supplied input, never executed as code (the algorithm
 * source still goes through the Phase 3 sandbox like any other run). */
export function parseRunRecord(raw: unknown): RunRecordV1 {
  if (typeof raw !== 'object' || raw === null) {
    throw new RunRecordParseError('Not a JSON object.')
  }
  const r = raw as Record<string, unknown>
  if (r.version !== 1) {
    throw new RunRecordParseError(`Unsupported run-record version: ${JSON.stringify(r.version)}`)
  }
  if (!Array.isArray(r.runs) || r.runs.length === 0) {
    throw new RunRecordParseError('Missing or empty "runs" array.')
  }
  if (!Array.isArray(r.selectedBaselines)) {
    throw new RunRecordParseError('Missing "selectedBaselines" array.')
  }
  if (typeof r.includeMyAlgorithm !== 'boolean') {
    throw new RunRecordParseError('Missing "includeMyAlgorithm" boolean.')
  }
  if (typeof r.overrides !== 'object' || r.overrides === null) {
    throw new RunRecordParseError('Missing "overrides" object.')
  }
  if (r.includeMyAlgorithm && typeof r.algorithmSource !== 'string') {
    throw new RunRecordParseError('"includeMyAlgorithm" is true but "algorithmSource" is missing.')
  }
  return r as unknown as RunRecordV1
}

const METRIC_FIELDS: (keyof Metrics)[] = [
  'attempted', 'committed', 'aborted', 'abort_rate_pct', 'throughput_tps',
  'mean_latency_ms', 'p99_latency_ms', 'cpu_ms_wasted_on_aborts',
  'preempt_events', 'deferred_events',
]

export function comparisonToCSVRows(runs: RunBatch): (string | number)[][] {
  const header = ['protocol', 'policy', ...METRIC_FIELDS]
  const rows: (string | number)[][] = [header]
  for (const { protocol, response } of runs) {
    for (const [key, m] of Object.entries(response.matrix)) {
      rows.push([protocol, key === 'custom' ? `${m.policy} (mine)` : m.policy, ...METRIC_FIELDS.map((f) => m[f])])
    }
  }
  return rows
}

export function sweepToCSVRows(sweep: SweepResponse): (string | number)[][] {
  const header = ['contention', 'protocol', 'policy', ...METRIC_FIELDS]
  const rows: (string | number)[][] = [header]
  for (const [key, points] of Object.entries(sweep.series)) {
    points.forEach((m, i) => {
      rows.push([sweep.contentions[i], m.protocol, key === 'custom' ? `${m.policy} (mine)` : m.policy, ...METRIC_FIELDS.map((f) => m[f])])
    })
  }
  return rows
}

export interface FieldDiff {
  field: keyof Metrics
  recorded: number
  reproduced: number
}

export interface PolicyDiff {
  protocol: Protocol
  policy: string
  match: boolean
  missing: boolean
  mismatches: FieldDiff[]
}

export interface ReproductionCheck {
  /** null when the record had no algorithm source to verify. */
  hashVerified: boolean | null
  allMatch: boolean
  policies: PolicyDiff[]
}

/** Re-checks a record's own stored hash against its own embedded source
 * (catches hand-edited export files) and diffs every metric field of the
 * freshly reproduced run against what was recorded, per policy per
 * protocol. Exact equality is the bar — these are deterministic seeded
 * simulations, not statistics, so "close" is not "reproduced". */
export async function checkReproduction(record: RunRecordV1, reproduced: RunBatch): Promise<ReproductionCheck> {
  let hashVerified: boolean | null = null
  if (record.algorithmSource != null && record.algorithmHash != null) {
    const recomputed = await sha256Hex(record.algorithmSource)
    hashVerified = recomputed === record.algorithmHash
  }

  const policies: PolicyDiff[] = []
  for (const { protocol, response: recordedResponse } of record.runs) {
    const reproEntry = reproduced.find((r) => r.protocol === protocol)
    for (const [key, recordedMetrics] of Object.entries(recordedResponse.matrix)) {
      const reproMetrics = reproEntry?.response.matrix[key]
      if (!reproMetrics) {
        policies.push({ protocol, policy: key, match: false, missing: true, mismatches: [] })
        continue
      }
      const mismatches: FieldDiff[] = []
      for (const field of METRIC_FIELDS) {
        if (recordedMetrics[field] !== reproMetrics[field]) {
          mismatches.push({ field, recorded: recordedMetrics[field] as number, reproduced: reproMetrics[field] as number })
        }
      }
      policies.push({ protocol, policy: key, match: mismatches.length === 0, missing: false, mismatches })
    }
  }

  return { hashVerified, allMatch: policies.every((p) => p.match), policies }
}
