import { useEffect, useMemo, useState } from 'react'
import AlgorithmEditor from './components/AlgorithmEditor'
import ApiReferencePanel from './components/ApiReferencePanel'
import ResultsPanel from './components/ResultsPanel'
import ConfigPanel, { type ConfigOverrides } from './components/ConfigPanel'
import BaselineSelector from './components/BaselineSelector'
import SweepPanel from './components/SweepPanel'
import TimelineSection from './components/timeline/TimelineSection'
import ExperimentBuilder from './components/experiment/ExperimentBuilder'
import ExportPanel from './components/export/ExportPanel'
import ReproducePanel from './components/export/ReproducePanel'
import FirstRunTour from './components/onboarding/FirstRunTour'
import Documentation from './components/docs/Documentation'
import { compare, fetchConfigDefaults, fetchPolicies, fetchTemplates } from './api'
import { SimulateError, type ApiError, type CompareResponse, type Config, type PolicyInfo, type Protocol, type Template } from './types'
import { checkReproduction, type ReproductionCheck, type RunRecordV1 } from './export'
import './App.css'

const STORAGE_KEY = 'simdb-preempt:editor-source'
const STORAGE_TEMPLATE_KEY = 'simdb-preempt:editor-template'
const STORAGE_TOUR_SEEN_KEY = 'simdb-preempt:tour-seen'
const MAX_HISTORY = 10

type ProtocolChoice = Protocol | 'both'
type RunBatch = { protocol: Protocol; response: CompareResponse }[]
interface HistoryEntry {
  id: string
  timestamp: string
  seed: number
  protocolChoice: ProtocolChoice
  runs: RunBatch
}

export default function App() {
  const [templates, setTemplates] = useState<Template[]>([])
  const [templateKey, setTemplateKey] = useState<string>(
    () => localStorage.getItem(STORAGE_TEMPLATE_KEY) || 'critical_aware',
  )
  const [source, setSource] = useState<string>(() => localStorage.getItem(STORAGE_KEY) || '')

  const [protocolChoice, setProtocolChoice] = useState<ProtocolChoice>('2pl')
  const [defaults, setDefaults] = useState<Record<Protocol, Config | null>>({ '2pl': null, occ: null })
  const [overrides, setOverrides] = useState<ConfigOverrides>({})

  const [policies, setPolicies] = useState<PolicyInfo[]>([])
  const [selectedBaselines, setSelectedBaselines] = useState<Set<string>>(new Set(['none', 'random', 'critical_aware']))
  const [includeMyAlgorithm, setIncludeMyAlgorithm] = useState(true)

  const [running, setRunning] = useState(false)
  const [error, setError] = useState<ApiError | null>(null)
  const [runs, setRuns] = useState<RunBatch>([])
  const [history, setHistory] = useState<HistoryEntry[]>([])
  const [viewingHistoryId, setViewingHistoryId] = useState<string | null>(null)

  const [tourOpen, setTourOpen] = useState<boolean>(() => !localStorage.getItem(STORAGE_TOUR_SEEN_KEY))
  const [docsOpen, setDocsOpen] = useState(false)

  useEffect(() => {
    fetchTemplates().then((ts) => {
      setTemplates(ts)
      if (!source) {
        const initial = ts.find((t) => t.key === templateKey) ?? ts[0]
        if (initial) setSource(initial.source)
      }
    })
    fetchPolicies().then(setPolicies)
    fetchConfigDefaults('2pl').then((cfg) => setDefaults((d) => ({ ...d, '2pl': cfg })))
    fetchConfigDefaults('occ').then((cfg) => setDefaults((d) => ({ ...d, occ: cfg })))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  useEffect(() => { localStorage.setItem(STORAGE_KEY, source) }, [source])
  useEffect(() => { localStorage.setItem(STORAGE_TEMPLATE_KEY, templateKey) }, [templateKey])

  const activeProtocols: Protocol[] = useMemo(
    () => (protocolChoice === 'both' ? ['2pl', 'occ'] : [protocolChoice]),
    [protocolChoice],
  )
  const primaryDefaults = defaults[activeProtocols[0]]

  function handleSelectTemplate(key: string) {
    setTemplateKey(key)
    const t = templates.find((t) => t.key === key)
    if (t) setSource(t.source)
  }

  function handleReset() {
    const t = templates.find((t) => t.key === templateKey)
    if (t) setSource(t.source)
  }

  function toggleBaseline(key: string) {
    setSelectedBaselines((prev) => {
      const next = new Set(prev)
      if (next.has(key)) next.delete(key)
      else next.add(key)
      return next
    })
  }

  async function executeRun(
    protocols: Protocol[],
    baselines: string[],
    algorithm: string | undefined,
    runOverrides: ConfigOverrides,
    forHistoryProtocolChoice: ProtocolChoice,
  ): Promise<RunBatch | null> {
    setRunning(true)
    setError(null)
    setViewingHistoryId(null)
    try {
      const results: RunBatch = []
      for (const protocol of protocols) {
        const response = await compare({
          protocol,
          policies: baselines,
          algorithm,
          config: runOverrides,
        })
        results.push({ protocol, response })
      }
      setRuns(results)
      const usedSeed = results[0]?.response.metadata.config.seed ?? 0
      setHistory((prev) => [
        {
          id: `${Date.now()}`,
          timestamp: new Date().toLocaleTimeString(),
          seed: usedSeed,
          protocolChoice: forHistoryProtocolChoice,
          runs: results,
        },
        ...prev,
      ].slice(0, MAX_HISTORY))
      return results
    } catch (e) {
      if (e instanceof SimulateError) {
        setError(e.detail)
      } else {
        setError({ type: 'bad_request', title: 'Request Failed', message: String(e) })
      }
      return null
    } finally {
      setRunning(false)
    }
  }

  function runWithOverrides(runOverrides: ConfigOverrides) {
    return executeRun(
      activeProtocols,
      Array.from(selectedBaselines),
      includeMyAlgorithm ? source : undefined,
      runOverrides,
      protocolChoice,
    )
  }

  function handleRun() {
    return runWithOverrides(overrides)
  }

  function handleRunAgainNewSeed() {
    const newSeed = Math.floor(Math.random() * 1_000_000)
    const next = { ...overrides, seed: newSeed }
    setOverrides(next)
    return runWithOverrides(next)
  }

  async function handleLoadRunRecord(record: RunRecordV1): Promise<ReproductionCheck> {
    setProtocolChoice(record.protocolChoice)
    setSelectedBaselines(new Set(record.selectedBaselines))
    setIncludeMyAlgorithm(record.includeMyAlgorithm)
    if (record.algorithmSource != null) setSource(record.algorithmSource)
    setOverrides(record.overrides)

    const protocols: Protocol[] = record.protocolChoice === 'both' ? ['2pl', 'occ'] : [record.protocolChoice]
    const algorithm = record.includeMyAlgorithm ? record.algorithmSource ?? undefined : undefined
    const reproduced = await executeRun(protocols, record.selectedBaselines, algorithm, record.overrides, record.protocolChoice)
    if (reproduced === null) {
      throw new Error('Reproduction run failed — see the error above for details.')
    }
    return checkReproduction(record, reproduced)
  }

  function closeTour() {
    localStorage.setItem(STORAGE_TOUR_SEEN_KEY, '1')
    setTourOpen(false)
  }

  function handleTryCriticalAwareExample() {
    const template = templates.find((t) => t.key === 'critical_aware')
    setTemplateKey('critical_aware')
    setProtocolChoice('2pl')
    setSelectedBaselines(new Set(['none', 'random', 'critical_aware']))
    setIncludeMyAlgorithm(true)
    setOverrides({})
    closeTour()
    if (template) {
      setSource(template.source)
      executeRun(['2pl'], ['none', 'random', 'critical_aware'], template.source, {}, '2pl')
    }
  }

  function viewHistoryEntry(entry: HistoryEntry) {
    setRuns(entry.runs)
    setViewingHistoryId(entry.id)
    setError(null)
  }

  const nothingSelected = selectedBaselines.size === 0 && !includeMyAlgorithm

  return (
    <div className="app-shell">
      <header className="app-header">
        <div className="kicker mono">
          simdb-preempt <span>/</span> simulation lab
        </div>
        <div className="app-header-row">
          <h1>Build your scheduler</h1>
          <div className="app-header-actions">
            <button className="btn btn-ghost btn-small" onClick={() => setTourOpen(true)}>Tour</button>
            <button className="btn btn-ghost btn-small" onClick={() => setDocsOpen(true)}>Docs</button>
          </div>
        </div>
        <p className="lede">
          Write a preemption policy, configure the workload, and run it against a simulated
          2PL or OCC transaction stream — no kernel patch, no real DBMS required. This is a
          simulation environment for early-stage evaluation, not a replacement for testing on
          a real kernel or database.
        </p>
      </header>

      {tourOpen && <FirstRunTour onTryExample={handleTryCriticalAwareExample} onClose={closeTour} />}
      {docsOpen && <Documentation onClose={() => setDocsOpen(false)} />}

      <div className="protocol-bar">
        <label className="mono protocol-label">Protocol</label>
        <select
          className="protocol-select"
          value={protocolChoice}
          onChange={(e) => setProtocolChoice(e.target.value as ProtocolChoice)}
        >
          <option value="2pl">2PL — Two-Phase Locking</option>
          <option value="occ">OCC — Optimistic Concurrency Control</option>
          <option value="both">Both — run 2PL and OCC</option>
        </select>
        <BaselineSelector
          policies={policies}
          selected={selectedBaselines}
          onToggle={toggleBaseline}
          includeMyAlgorithm={includeMyAlgorithm}
          onToggleMyAlgorithm={() => setIncludeMyAlgorithm((v) => !v)}
        />
      </div>

      <ConfigPanel
        values={primaryDefaults}
        overrides={overrides}
        onChange={setOverrides}
        onResetToDefaults={() => setOverrides({})}
      />
      {protocolChoice === 'both' && (
        <p className="protocol-note both-note">
          "Both" applies the same overrides on top of each protocol's own defaults (2PL and OCC
          have different default contention/ops-per-txn) and runs two separate simulations.
        </p>
      )}

      <main className="workspace">
        <section className="workspace-main">
          <AlgorithmEditor
            source={source}
            onChange={setSource}
            onRun={handleRun}
            onReset={handleReset}
            running={running || nothingSelected}
            templates={templates}
            selectedTemplateKey={templateKey}
            onSelectTemplate={handleSelectTemplate}
            errorLine={error?.line}
          />
          {nothingSelected && (
            <p className="editor-error-hint mono" style={{ border: 'none', background: 'transparent' }}>
              Select at least one baseline or enable "My Algorithm" to run.
            </p>
          )}

          <div className="run-again-bar">
            <button className="btn btn-ghost btn-small" onClick={handleRunAgainNewSeed} disabled={running || nothingSelected}>
              Run Again (new seed)
            </button>
            {history.length > 0 && (
              <div className="run-history">
                <span className="config-field-label mono">History</span>
                {history.map((h) => (
                  <button
                    key={h.id}
                    className={`history-chip mono${viewingHistoryId === h.id ? ' history-chip-active' : ''}`}
                    onClick={() => viewHistoryEntry(h)}
                    title={`${h.protocolChoice} · seed ${h.seed}`}
                  >
                    {h.timestamp} · seed {h.seed}
                  </button>
                ))}
              </div>
            )}
          </div>

          <ResultsPanel running={running} error={error} runs={runs} />

          <ExportPanel
            protocolChoice={protocolChoice}
            selectedBaselines={Array.from(selectedBaselines)}
            includeMyAlgorithm={includeMyAlgorithm}
            source={source}
            overrides={overrides}
            runs={runs}
          />

          <ReproducePanel onLoad={handleLoadRunRecord} running={running} />

          <SweepPanel
            protocol={activeProtocols[0]}
            policies={Array.from(selectedBaselines)}
            algorithm={includeMyAlgorithm ? source : undefined}
            overrides={overrides}
          />

          <TimelineSection
            protocol={activeProtocols[0]}
            policies={Array.from(selectedBaselines)}
            algorithm={includeMyAlgorithm ? source : undefined}
            overrides={overrides}
          />

          <ExperimentBuilder
            policies={Array.from(selectedBaselines)}
            algorithm={includeMyAlgorithm ? source : undefined}
            overrides={overrides}
          />
        </section>
        <aside className="workspace-aside">
          <ApiReferencePanel />
        </aside>
      </main>
    </div>
  )
}
