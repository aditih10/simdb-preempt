import { useEffect, useRef, useState } from 'react'
import type { ApiError, Protocol, TraceResponse } from '../../types'
import type { ConfigOverrides } from '../ConfigPanel'
import { trace } from '../../api'
import { SimulateError } from '../../types'
import { POLICY_LABELS } from '../../palette'
import TimelineTrack from './TimelineTrack'
import EventTraceLog from './EventTraceLog'
import TransactionDetailPanel from './TransactionDetailPanel'

interface Props {
  protocol: Protocol
  policies: string[]
  algorithm?: string
  overrides: ConfigOverrides
}

const SPEEDS = [0.5, 1, 2, 4]

export default function TimelineSection({ protocol, policies, algorithm, overrides }: Props) {
  const [windowStart, setWindowStart] = useState(1000)
  const [windowSize, setWindowSize] = useState(150)
  const [running, setRunning] = useState(false)
  const [error, setError] = useState<ApiError | null>(null)
  const [result, setResult] = useState<TraceResponse | null>(null)
  const [focusedPolicy, setFocusedPolicy] = useState<string | null>(null)
  const [selectedTxn, setSelectedTxn] = useState<number | null>(null)

  const [playing, setPlaying] = useState(false)
  const [speed, setSpeed] = useState(1)
  const [cursorTime, setCursorTime] = useState<number | null>(null)
  const rafRef = useRef<number | null>(null)
  const lastFrameRef = useRef<number | null>(null)

  const nothingSelected = policies.length === 0 && !algorithm

  async function handleRun() {
    setRunning(true)
    setError(null)
    setPlaying(false)
    try {
      const res = await trace({
        protocol, policies, algorithm,
        config: overrides,
        window_start: windowStart,
        window_size: windowSize,
      })
      setResult(res)
      const keys = Object.keys(res.traces)
      setFocusedPolicy(keys[0] ?? null)
      setSelectedTxn(null)
      setCursorTime(res.window[0])
    } catch (e) {
      setError(e instanceof SimulateError ? e.detail : { type: 'bad_request', title: 'Request Failed', message: String(e) })
    } finally {
      setRunning(false)
    }
  }

  // Playback loop
  useEffect(() => {
    if (!playing || !result) return
    lastFrameRef.current = null
    const [start, end] = result.window
    function step(now: number) {
      if (lastFrameRef.current == null) lastFrameRef.current = now
      const dt = (now - lastFrameRef.current) / 1000
      lastFrameRef.current = now
      setCursorTime((prev) => {
        const next = (prev ?? start) + dt * speed * (end - start) / 5 // sweep window in ~5s at 1x
        if (next >= end) {
          setPlaying(false)
          return end
        }
        return next
      })
      rafRef.current = requestAnimationFrame(step)
    }
    rafRef.current = requestAnimationFrame(step)
    return () => { if (rafRef.current) cancelAnimationFrame(rafRef.current) }
  }, [playing, speed, result])

  function handleRestart() {
    setPlaying(false)
    setCursorTime(result?.window[0] ?? null)
  }

  const focusedTrace = result && focusedPolicy ? result.traces[focusedPolicy] : null
  const selectedTxnSummary = focusedTrace?.transactions.find((t) => t.txn === selectedTxn) ?? null

  return (
    <div className="timeline-section">
      <div className="timeline-intro">
        <h3 className="timeline-title">CPU Timeline &amp; Event Trace</h3>
        <p className="section-note">
          Runs each selected policy once and captures a windowed, per-core event trace — every
          block below is real simulated execution, not an illustration.
        </p>
      </div>
      <div className="sweep-controls">
        <label className="config-field">
          <span className="config-field-label">Window Start (ms)</span>
          <input className="mono config-field-input" type="number" min={0} step={50}
            value={windowStart} onChange={(e) => setWindowStart(Number(e.target.value))} />
        </label>
        <label className="config-field">
          <span className="config-field-label">Window Size (ms)</span>
          <input className="mono config-field-input" type="number" min={10} max={1000} step={10}
            value={windowSize} onChange={(e) => setWindowSize(Number(e.target.value))} />
        </label>
        <button className="btn btn-primary" onClick={handleRun} disabled={running || nothingSelected}>
          {running ? 'Running…' : 'Run Trace'}
        </button>
      </div>
      {nothingSelected && (
        <p className="results-placeholder">Select at least one baseline or "My Algorithm" above to trace.</p>
      )}
      {error && (
        <div className="result-error-card">
          <div className="result-error-title mono">{error.title}</div>
          <div className="result-error-message">{error.message}</div>
        </div>
      )}

      {result && (
        <>
          <div className="playback-bar">
            <button className="btn btn-ghost btn-small" onClick={() => setPlaying((p) => !p)}>
              {playing ? 'Pause' : 'Play'}
            </button>
            <button className="btn btn-ghost btn-small" onClick={handleRestart}>Restart</button>
            <label className="config-field-label mono">Speed</label>
            <select className="protocol-select speed-select" value={speed} onChange={(e) => setSpeed(Number(e.target.value))}>
              {SPEEDS.map((s) => <option key={s} value={s}>{s}x</option>)}
            </select>
            <input
              className="scrub-bar"
              type="range"
              min={result.window[0]}
              max={result.window[1]}
              step={(result.window[1] - result.window[0]) / 500}
              value={cursorTime ?? result.window[0]}
              onChange={(e) => { setPlaying(false); setCursorTime(Number(e.target.value)) }}
            />
            <span className="mono cursor-readout">{(cursorTime ?? result.window[0]).toFixed(1)}ms</span>
          </div>

          <div className="timeline-panel">
            {Object.entries(result.traces).map(([key, t]) => (
              <TimelineTrack
                key={key}
                policyKey={key}
                policyLabel={POLICY_LABELS[key] ?? key}
                segments={t.segments}
                markers={t.markers}
                window={result.window}
                cursorTime={cursorTime}
                selectedTxn={selectedTxn}
                onSelectTxn={(txn) => { setSelectedTxn(txn); setFocusedPolicy(key) }}
              />
            ))}
            <div className="legend">
              <div className="legend-item"><span className="legend-swatch" style={{ background: 'var(--cpu)' }} />executing</div>
              <div className="legend-item"><span className="legend-swatch" style={{ background: 'var(--wait)' }} />waiting on a lock</div>
              <div className="legend-item"><span className="legend-swatch" style={{ background: 'var(--penalty)' }} />context-switch penalty</div>
              <div className="legend-item"><span className="legend-swatch" style={{ background: 'var(--violet)' }} />OCC validation</div>
            </div>
          </div>

          <div className="trace-detail-grid">
            <div className="event-log-panel">
              <div className="config-panel-header">
                <h4 className="mono">Event Trace</h4>
                {Object.keys(result.traces).length > 1 && (
                  <select className="protocol-select policy-focus-select" value={focusedPolicy ?? ''} onChange={(e) => setFocusedPolicy(e.target.value)}>
                    {Object.keys(result.traces).map((k) => (
                      <option key={k} value={k}>{POLICY_LABELS[k] ?? k}</option>
                    ))}
                  </select>
                )}
              </div>
              {focusedTrace && (
                <EventTraceLog
                  segments={focusedTrace.segments}
                  markers={focusedTrace.markers}
                  cursorTime={cursorTime}
                  selectedTxn={selectedTxn}
                  onSelectTxn={setSelectedTxn}
                />
              )}
            </div>
            <TransactionDetailPanel protocol={protocol} txn={selectedTxnSummary} />
          </div>
        </>
      )}
    </div>
  )
}
