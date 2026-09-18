interface Section {
  id: string
  title: string
  body: React.ReactNode
}

const CONFIG_FIELDS: { name: string; desc: string }[] = [
  { name: 'num_cores', desc: 'Number of simulated CPU cores transactions run on.' },
  { name: 'db_size', desc: 'Total number of distinct lockable/versioned keys in the simulated database.' },
  { name: 'contention', desc: "Shrinks the pool of keys transactions actually draw from: pool size = db_size × (1 − contention). Higher contention means a smaller hot-key pool and more collisions between transactions." },
  { name: 'ops_per_txn', desc: 'Upper bound on how many keys a single transaction touches (each transaction samples a random count between 2 and this value).' },
  { name: 'mean_op_time', desc: 'Mean simulated CPU time (ms) per operation.' },
  { name: 'lock_timeout', desc: '2PL only — how long (ms) a transaction waits for a lock before aborting.' },
  { name: 'preempt_rate', desc: 'Mean rate (per ms) at which the simulated OS scheduler attempts a preemption on each core.' },
  { name: 'preempt_penalty', desc: 'Mean extra CPU time (ms) a preempted operation pays before it can resume, modeling context-switch cost.' },
  { name: 'sim_time', desc: 'Total simulated duration (ms) of one run.' },
  { name: 'arrival_rate', desc: 'Mean rate (per ms) at which new transactions arrive.' },
  { name: 'seed', desc: "Random seed. Same seed + config + algorithm always reproduces the same run, down to the individual event." },
]

const METRICS: { name: string; desc: string }[] = [
  { name: 'abort_rate_pct', desc: 'Percentage of attempted transactions that aborted (lock timeout for 2PL; version conflict or preemption-during-validation for OCC).' },
  { name: 'throughput_tps', desc: 'Committed transactions per simulated second.' },
  { name: 'mean_latency_ms', desc: 'Mean end-to-end latency of committed transactions.' },
  { name: 'p99_latency_ms', desc: '99th-percentile end-to-end latency of committed transactions — the metric most sensitive to preemption-induced tail effects.' },
  { name: 'cpu_ms_wasted_on_aborts', desc: 'Total simulated CPU time spent on transactions that ultimately aborted — the direct cost of the OS/DBMS layer gap.' },
  { name: 'preempt_events', desc: 'Number of times the scheduler actually preempted a running transaction (should_defer returned False while something was running).' },
  { name: 'deferred_events', desc: 'Number of preemption attempts the policy chose to skip (should_defer returned True).' },
]

const SECTIONS: Section[] = [
  {
    id: 'what-is-it',
    title: 'What SimDB-Preempt is',
    body: (
      <p>
        SimDB-Preempt is a lightweight, in-browser discrete-event simulator and scheduler
        playground for studying how general-purpose OS preemption interacts with database
        transaction concurrency control. You write a preemption policy in Python, run it against
        a simulated 2PL or OCC transaction workload, and see exactly how abort rate, throughput,
        and tail latency respond — no kernel patch, no real database, no cluster to provision.
      </p>
    ),
  },
  {
    id: 'problem',
    title: 'What problem it addresses',
    body: (
      <p>
        General-purpose OS schedulers (e.g. Linux's EEVDF/CFS) decide when to preempt a running
        thread using signals like time slice and priority — they have no idea whether that thread
        currently holds a database lock or is mid-validation on a transaction. SimDB-Preempt
        isolates and measures exactly how much that blindness costs: how many extra aborts, how
        much wasted CPU, and how much added tail latency a critical-section-blind scheduler
        introduces compared to one that knows when <em>not</em> to preempt.
      </p>
    ),
  },
  {
    id: 'layer-gap',
    title: 'What the OS–DBMS layer gap means',
    body: (
      <p>
        The "layer gap" is the missing communication channel between the OS scheduler and the
        DBMS's internal concurrency-control state. The OS operates purely on thread-level signals;
        the DBMS's notion of "this thread is holding lock #482" or "this thread is mid-validation"
        never crosses that boundary in a real system. SimDB-Preempt models both layers explicitly —
        a simulated core that tracks whether it's inside a critical section, and a pluggable
        OS-scheduler policy that decides whether to respect that — so the cost of the gap becomes a
        directly measurable, comparable number instead of an assumption.
      </p>
    ),
  },
  {
    id: '2pl',
    title: 'Two-Phase Locking (2PL)',
    body: (
      <p>
        Each transaction samples a handful of keys and acquires a lock on each in turn, with a
        timeout (<code className="mono">lock_timeout</code>) — if a lock can't be acquired before
        timing out, the transaction aborts and releases everything it already held. While a
        transaction holds any lock, its core is marked as being in a critical section — this is the
        window a critical-aware OS scheduler avoids preempting through, since preempting here
        extends every other transaction's wait for that lock.
      </p>
    ),
  },
  {
    id: 'occ',
    title: 'Optimistic Concurrency Control (OCC)',
    body: (
      <p>
        Each transaction reads a snapshot of its keys' versions — this read phase is <em>not</em> a
        critical section, so it's safe to preempt — then enters a validation-and-write phase that
        re-checks whether any key's version changed since it was read, and commits new versions if
        not. Being preempted <em>during validation</em> is its own abort cause in this simulator, in
        addition to the classic "someone else committed first" version-mismatch abort — this models
        exactly the mechanism by which OS preemption inflates OCC's abort rate.
      </p>
    ),
  },
  {
    id: 'preemption',
    title: 'Preemption policies',
    body: (
      <ul className="docs-list">
        <li><strong>none</strong> — never preempts; the idealized upper-bound baseline.</li>
        <li><strong>random</strong> — preempts on a fixed schedule regardless of DBMS state; models a default, critical-section-blind scheduler.</li>
        <li><strong>critical_aware</strong> — defers preemption for as long as the core is in a critical section.</li>
        <li><strong>grace_period</strong> — like critical_aware, but caps the deferral at <code className="mono">max_grace_ms</code> so one long critical section can't starve the scheduler indefinitely.</li>
        <li>You can also write your own — see Policy API below.</li>
      </ul>
    ),
  },
  {
    id: 'des',
    title: 'Discrete-event simulation',
    body: (
      <p>
        Rather than running real threads against a wall clock, SimDB-Preempt advances a virtual
        clock from event to event — a lock becoming free, an operation finishing, a transaction
        arriving, a preemption attempt firing. Nothing happens "in between" events, so a
        15-second simulated workload computes in a fraction of a second of real time, and — because
        the only randomness comes from a seeded generator — the exact same sequence of events
        happens every time you use the same seed (see Reproducibility below).
      </p>
    ),
  },
  {
    id: 'policy-api',
    title: 'Policy API',
    body: (
      <p>
        Your policy is a Python class extending <code className="mono">PreemptionPolicy</code> with
        one required method: <code className="mono">should_defer(self, core, env) -&gt; bool</code>,
        called every time the simulated OS scheduler is about to preempt a core that's currently
        running a transaction. Return <code className="mono">True</code> to defer (skip) that
        preemption attempt, or <code className="mono">False</code> to allow it. This section is a
        conceptual overview — the "Algorithm API" panel next to the code editor is the exact,
        authoritative field reference (<code className="mono">core.critical</code>,{' '}
        <code className="mono">core.critical_since</code>, <code className="mono">env.now</code>,
        and the execution model your code runs under).
      </p>
    ),
  },
  {
    id: 'variables',
    title: 'Available variables',
    body: (
      <>
        <p>The workload/config fields exposed in the Config panel, all bounded to safe ranges:</p>
        <table className="reference-table">
          <tbody>
            {CONFIG_FIELDS.map((f) => (
              <tr key={f.name}>
                <td className="mono field-name">{f.name}</td>
                <td className="field-desc">{f.desc}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </>
    ),
  },
  {
    id: 'metrics',
    title: 'Metrics',
    body: (
      <table className="reference-table">
        <tbody>
          {METRICS.map((m) => (
            <tr key={m.name}>
              <td className="mono field-name">{m.name}</td>
              <td className="field-desc">{m.desc}</td>
            </tr>
          ))}
        </tbody>
      </table>
    ),
  },
  {
    id: 'methodology',
    title: 'Experiment methodology',
    body: (
      <p>
        The Experiment Builder sweeps one workload parameter (contention, preemption rate, arrival
        rate, or ops/txn) across a range of values and repeats each point across multiple
        independent replications, each with its own seed, then reports the mean, sample standard
        deviation, and a 95% confidence interval (two-tailed Student's-t, computed from the actual
        replication count) per policy per point. A single run is never treated as statistically
        significant — the results table explicitly shows "n/a (N&lt;3)" instead of a confidence
        interval below 3 replications.
      </p>
    ),
  },
  {
    id: 'reproducibility',
    title: 'Reproducibility',
    body: (
      <p>
        Every simulation run is fully determined by its protocol, policy (or algorithm source),
        config, and seed — the simulator reseeds Python's random module at the start of every run,
        so the same inputs always produce the exact same output, down to the individual event.
        Independent replications use a documented formula (<code className="mono">seed = 1000 +
        replication index</code>), not the run's own seed, since the point of replicating is to
        sample independent outcomes rather than repeat one. You can download a full JSON record of
        any comparison run — config, seed, algorithm source and its SHA-256 hash, and results — and
        reload it later under "Reproduce a Run" to verify the simulator reproduces it exactly.
      </p>
    ),
  },
  {
    id: 'limitations',
    title: 'Limitations',
    body: (
      <>
        <p className="docs-disclaimer">
          This is a simulation environment for early-stage evaluation and algorithm exploration —
          it is <strong>not</strong> a replacement for validating a scheduling policy on a real OS
          kernel or a real DBMS under real workloads.
        </p>
        <ul className="docs-list">
          <li>
            <strong>Sandboxed execution has no memory limit.</strong> Custom algorithm code runs in
            an isolated subprocess with an AST whitelist (no imports, no dunder attribute access, no
            calls outside a short safe-builtin list) and a 10-second wall-clock timeout — but nothing
            currently bounds how much memory a submitted policy can allocate.
          </li>
          <li>
            <strong>The Experiment Builder's replication count is capped at 20</strong> in the UI,
            even though the underlying statistics and seed formula support any N. Running more than
            20 replications today requires calling the same <code className="mono">/api/simulate/compare</code> endpoint
            directly with additional <code className="mono">seed = 1000 + rep</code> values past 20.
          </li>
          <li>
            The workload model draws keys uniformly from a contention-scaled "hot" subset of the key
            space — it does not model a realistic skewed (e.g. Zipfian) access distribution.
          </li>
          <li>
            2PL transaction aborts (lock-timeout aborts) essentially never occur under the app's
            default configuration — <code className="mono">lock_timeout</code> is generous relative
            to the modeled lock-wait distribution, so preemption's effect on 2PL shows up almost
            entirely as latency degradation, not abort rate.
          </li>
          <li>
            All simulation randomness comes from Python's global <code className="mono">random</code> module,
            reseeded per run — safe for the sequential runs this UI makes, but the reason the backend
            serializes every simulation behind a single lock rather than running them in parallel.
          </li>
        </ul>
      </>
    ),
  },
]

interface Props {
  onClose: () => void
}

export default function Documentation({ onClose }: Props) {
  function scrollTo(id: string) {
    document.getElementById(id)?.scrollIntoView({ behavior: 'auto', block: 'start' })
  }

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal-panel docs-panel" onClick={(e) => e.stopPropagation()}>
        <div className="modal-header">
          <h2 className="mono">Documentation</h2>
          <button className="modal-close" onClick={onClose} aria-label="Close">×</button>
        </div>
        <div className="docs-body">
          <nav className="docs-nav mono">
            {SECTIONS.map((s, i) => (
              <button key={s.id} className="docs-nav-item" onClick={() => scrollTo(s.id)}>
                {i + 1}. {s.title}
              </button>
            ))}
          </nav>
          <div className="docs-content">
            {SECTIONS.map((s, i) => (
              <section key={s.id} id={s.id} className="docs-section">
                <h3 className="mono">{i + 1}. {s.title}</h3>
                {s.body}
              </section>
            ))}
          </div>
        </div>
      </div>
    </div>
  )
}
