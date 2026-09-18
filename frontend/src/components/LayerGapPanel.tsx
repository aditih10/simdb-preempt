import { useState } from 'react'
import { POLICY_LABELS, colorFor } from '../palette'

interface Props {
  layerGap: Record<string, Record<string, number>>
}

const METRIC_TITLES: Record<string, string> = {
  abort_rate_pct: 'Abort Rate',
  p99_latency_ms: 'P99 Latency',
  throughput_tps: 'Throughput',
}

export default function LayerGapPanel({ layerGap }: Props) {
  const [open, setOpen] = useState(false)
  const metricKeys = Object.keys(layerGap)

  return (
    <div className="layer-gap-panel">
      <button className="layer-gap-toggle mono" onClick={() => setOpen((v) => !v)}>
        {open ? '▾' : '▸'} Optional: layer-gap closure score
      </button>
      {open && (
        <div className="layer-gap-body">
          <p className="section-note">
            A single derived number per metric — 100 means a policy fully closes the gap and matches
            the no-preemption ideal, 0 means it's no better than a critical-section-blind scheduler,
            below 0 means it's actively worse. This does <strong>not</strong> mean a policy with a
            higher score is "better" overall — check the raw metrics above for the full picture.
          </p>
          {metricKeys.map((metric) => (
            <div key={metric} className="gap-metric">
              <div className="gap-metric-title mono">{METRIC_TITLES[metric] ?? metric}</div>
              {Object.entries(layerGap[metric]).map(([policyKey, score]) => (
                <div key={policyKey} className="gap-row">
                  <span className="gap-row-label mono" style={{ color: colorFor(policyKey) }}>
                    {POLICY_LABELS[policyKey] ?? policyKey}
                  </span>
                  <div className="gap-bar-track">
                    <div
                      className="gap-bar-fill"
                      style={{
                        width: `${Math.max(0, Math.min(100, score))}%`,
                        background: colorFor(policyKey),
                      }}
                    />
                  </div>
                  <span className="gap-row-value mono">{score}</span>
                </div>
              ))}
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
