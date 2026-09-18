import { compare } from '../../api'
import type { Metrics, Protocol } from '../../types'
import type { ConfigOverrides } from '../ConfigPanel'

export type SweepParam = 'contention' | 'preempt_rate' | 'arrival_rate' | 'ops_per_txn'

export interface ExperimentConfig {
  param: SweepParam
  values: number[]
  protocols: Protocol[]
  replications: number
  policies: string[]
  algorithm?: string
  baseOverrides: ConfigOverrides
}

export interface ExperimentPoint {
  protocol: Protocol
  paramValue: number
  /** One entry per replication, per policy key (including "custom"). */
  byPolicy: Record<string, Metrics[]>
}

export interface ExperimentResult {
  config: ExperimentConfig
  points: ExperimentPoint[]
}

/** Distinct, reproducible seeds per replication — deliberately independent
 * of any user-set base seed, since the whole point of replicating is to
 * sample independent runs, not to repeat the same one. */
function replicationSeed(rep: number): number {
  return 1000 + rep
}

export function experimentTotalRuns(cfg: Pick<ExperimentConfig, 'protocols' | 'values' | 'replications'>): number {
  return cfg.protocols.length * cfg.values.length * cfg.replications
}

export async function runExperiment(
  cfg: ExperimentConfig,
  onProgress: (done: number, total: number) => void,
  shouldCancel: () => boolean,
): Promise<ExperimentResult> {
  const total = experimentTotalRuns(cfg)
  let done = 0
  const points: ExperimentPoint[] = []

  for (const protocol of cfg.protocols) {
    for (const value of cfg.values) {
      const byPolicy: Record<string, Metrics[]> = {}
      for (let rep = 0; rep < cfg.replications; rep++) {
        if (shouldCancel()) return { config: cfg, points }
        const res = await compare({
          protocol,
          policies: cfg.policies,
          algorithm: cfg.algorithm,
          config: { ...cfg.baseOverrides, [cfg.param]: value, seed: replicationSeed(rep) },
        })
        for (const [key, metrics] of Object.entries(res.matrix)) {
          if (!byPolicy[key]) byPolicy[key] = []
          byPolicy[key].push(metrics)
        }
        done++
        onProgress(done, total)
      }
      points.push({ protocol, paramValue: value, byPolicy })
    }
  }

  return { config: cfg, points }
}
