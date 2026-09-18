const FIELDS: { name: string; type: string; desc: string }[] = [
  { name: 'core.critical', type: 'bool', desc: 'True while the current transaction on this core is inside a DBMS critical section — holding a 2PL lock, or in the OCC validation/write phase.' },
  { name: 'core.critical_since', type: 'float | None', desc: 'Simulation time (ms) the current critical section began. None when core.critical is False.' },
  { name: 'core.preempt_count', type: 'int', desc: "Number of times the OS has actually preempted this core so far (i.e. should_defer returned False and a transaction was running)." },
  { name: 'core.deferred_count', type: 'int', desc: 'Number of preemption attempts this core has deferred (should_defer returned True) so far.' },
  { name: 'core.id', type: 'int', desc: 'The core index (0..num_cores-1).' },
  { name: 'env.now', type: 'float', desc: 'Current simulation time in milliseconds.' },
]

const EXAMPLE = `class MyPolicy(PreemptionPolicy):
    name = "my_policy"

    def should_defer(self, core, env):
        # Return True to SKIP this preemption attempt.
        # Return False to let the OS preempt now.
        if core.critical:
            return True
        return False`

export default function ApiReferencePanel() {
  return (
    <div className="reference-panel">
      <h3 className="mono">Algorithm API</h3>
      <p className="reference-note">
        Your policy is a subclass of <code className="mono">PreemptionPolicy</code> with
        one required method: <code className="mono">should_defer(self, core, env) -&gt; bool</code>.
        It's called every time the simulated OS scheduler is about to preempt a core that's
        currently running a transaction. Return <code className="mono">True</code> to defer
        (skip) that preemption attempt, or <code className="mono">False</code> to allow it.
      </p>

      <pre className="reference-example mono">{EXAMPLE}</pre>

      <h4 className="mono">Available on core / env</h4>
      <table className="reference-table">
        <tbody>
          {FIELDS.map((f) => (
            <tr key={f.name}>
              <td className="mono field-name">{f.name}</td>
              <td className="mono field-type">{f.type}</td>
              <td className="field-desc">{f.desc}</td>
            </tr>
          ))}
        </tbody>
      </table>

      <h4 className="mono">Execution model</h4>
      <ul className="reference-list">
        <li>This is a <strong>simulation</strong>, not real kernel/DBMS execution — no real process, thread, or database is touched.</li>
        <li>Your code runs in an isolated subprocess with a wall-clock timeout, a restricted set of builtins, and no filesystem or network access.</li>
        <li>Only the documented API above is available — imports, dunder attribute access (e.g. <code className="mono">__class__</code>), and calls to anything other than a short safe-builtin list are rejected before your code ever runs.</li>
        <li>Your <code className="mono">__init__</code>, if defined, must work with no arguments (all constructor parameters need defaults).</li>
      </ul>
    </div>
  )
}
