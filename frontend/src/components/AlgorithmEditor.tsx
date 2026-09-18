import CodeMirror from '@uiw/react-codemirror'
import { python } from '@codemirror/lang-python'
import type { Template } from '../types'

interface Props {
  source: string
  onChange: (source: string) => void
  onRun: () => void
  onReset: () => void
  running: boolean
  templates: Template[]
  selectedTemplateKey: string
  onSelectTemplate: (key: string) => void
  errorLine?: number | null
}

export default function AlgorithmEditor({
  source, onChange, onRun, onReset, running,
  templates, selectedTemplateKey, onSelectTemplate, errorLine,
}: Props) {
  return (
    <div className="editor-panel">
      <div className="editor-toolbar">
        <span className="editor-filename mono">policy.py</span>
        <select
          className="template-select"
          value={selectedTemplateKey}
          onChange={(e) => onSelectTemplate(e.target.value)}
          title="Load a starter template"
        >
          {templates.map((t) => (
            <option key={t.key} value={t.key}>{t.title}</option>
          ))}
        </select>
        <div className="editor-toolbar-spacer" />
        <button className="btn btn-ghost" onClick={onReset} disabled={running}>
          Reset to Template
        </button>
        <button className="btn btn-primary" onClick={onRun} disabled={running}>
          {running ? 'Running…' : 'Run Simulation'}
        </button>
      </div>
      <CodeMirror
        value={source}
        height="440px"
        theme="dark"
        extensions={[python()]}
        onChange={onChange}
        basicSetup={{ lineNumbers: true, foldGutter: true, autocompletion: true }}
      />
      {errorLine != null && (
        <div className="editor-error-hint mono">
          Error reported at line {errorLine} — see the panel below.
        </div>
      )}
    </div>
  )
}
