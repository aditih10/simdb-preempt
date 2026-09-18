// Small, self-contained statistics helpers for the Phase 7 experiment
// builder. No dependency pulled in for this — it's ~20 lines of real math.

// Two-tailed 95% Student's-t critical values by degrees of freedom (n-1).
// Standard table values; df > 30 falls back to the normal approximation.
const T_TABLE_95: Record<number, number> = {
  1: 12.706, 2: 4.303, 3: 3.182, 4: 2.776, 5: 2.571, 6: 2.447, 7: 2.365,
  8: 2.306, 9: 2.262, 10: 2.228, 11: 2.201, 12: 2.179, 13: 2.160, 14: 2.145,
  15: 2.131, 16: 2.120, 17: 2.110, 18: 2.101, 19: 2.093, 20: 2.086,
  21: 2.080, 22: 2.074, 23: 2.069, 24: 2.064, 25: 2.060, 26: 2.056,
  27: 2.052, 28: 2.048, 29: 2.045, 30: 2.042,
}

export function mean(values: number[]): number {
  return values.reduce((a, b) => a + b, 0) / values.length
}

/** Sample standard deviation (n-1 denominator). Returns 0 for n<2. */
export function stddev(values: number[]): number {
  if (values.length < 2) return 0
  const m = mean(values)
  const variance = values.reduce((sum, v) => sum + (v - m) ** 2, 0) / (values.length - 1)
  return Math.sqrt(variance)
}

export interface Summary {
  n: number
  mean: number
  stddev: number
  /** 95% CI half-width — only meaningful when sufficientForCI is true. */
  marginOfError: number
  ciLow: number
  ciHigh: number
  /** Per the spec: never present a CI (or imply significance) below n=3. */
  sufficientForCI: boolean
}

export function summarize(values: number[]): Summary {
  const n = values.length
  const m = mean(values)
  const sd = stddev(values)
  const sufficientForCI = n >= 3
  const tCrit = n - 1 <= 30 ? T_TABLE_95[n - 1] ?? 1.96 : 1.96
  const marginOfError = sufficientForCI ? tCrit * (sd / Math.sqrt(n)) : 0
  return {
    n, mean: m, stddev: sd, marginOfError,
    ciLow: m - marginOfError, ciHigh: m + marginOfError,
    sufficientForCI,
  }
}
