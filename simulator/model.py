"""
3. CONFIG, DB, CORE, STATS, TRACER
======================================================================
"""

from __future__ import annotations
import random
from dataclasses import dataclass, field

from .engine import Environment, Lock
from .policies import PreemptionPolicy


def percentile(values, p):
    """Linear-interpolation percentile, no numpy required."""
    if not values:
        return 0.0
    s = sorted(values)
    k = (len(s) - 1) * (p / 100.0)
    f, c = int(k), min(int(k) + 1, len(s) - 1)
    if f == c:
        return s[f]
    return s[f] + (s[c] - s[f]) * (k - f)


@dataclass
class Config:
    num_cores: int = 8
    db_size: int = 2000
    contention: float = 0.6
    ops_per_txn: int = 6
    mean_op_time: float = 0.4
    lock_timeout: float = 25.0
    preempt_rate: float = 0.14
    preempt_penalty: float = 2.0
    sim_time: float = 15_000.0
    arrival_rate: float = 2.0
    seed: int = 42

    # ---- paper-experiments flags: every one of these defaults to the
    # exact behavior that existed before this flag was added. See
    # RngPool / Core.enter_critical / transactions.py for what each mode
    # actually changes. ----
    rng_mode: str = "legacy"            # "legacy" | "split"
    double_preempt: str = "legacy"      # "legacy" | "ignore"
    grace_clock: str = "per_lock"       # "per_lock" | "per_section"
    occ_validation_preempt: str = "abort"  # "abort" | "delay"
    warmup_ms: float = 0.0


class RngPool:
    """Backs Config.rng_mode.

    "legacy" (default): every stream is the shared global `random` module,
    exactly as the original single-stream code used it — calling
    `pool.arrival.expovariate(...)` is calling `random.expovariate(...)`,
    same object, same global Mersenne Twister state, same call order as
    before this existed. This is what makes rng_mode="legacy" byte-identical
    to the pre-flag code: nothing about *which* draws happen or in what
    order changes, only the spelling of the call target.

    "split": four independent random.Random streams — arrival, keyset,
    op-time, and one per core for preemption timing (which also supplies
    the preemption-penalty draw, since that's a direct effect of a
    preemption firing, not of the workload) — each seeded deterministically
    from cfg.seed with a distinct offset. Workload draws (arrival/keyset/
    optime) never read from a preemption stream and vice versa, so how many
    preemption-timing draws a policy happens to make can never perturb the
    workload's own sample path.
    """

    _SALT = 7919  # arbitrary large prime, just for offset separation

    def __init__(self, cfg: Config):
        self.mode = cfg.rng_mode
        if self.mode == "split":
            base = cfg.seed
            self.arrival = random.Random(base * self._SALT + 1)
            self.keyset = random.Random(base * self._SALT + 2)
            self.optime = random.Random(base * self._SALT + 3)
            self._base = base
            self._preempt = {}
        else:
            self.arrival = random
            self.keyset = random
            self.optime = random
            self._preempt = None

    def preempt(self, core_id: int):
        if self.mode != "split":
            return random
        if core_id not in self._preempt:
            self._preempt[core_id] = random.Random(self._base * self._SALT + 1000 + core_id)
        return self._preempt[core_id]


class DB:
    def __init__(self, env: Environment, cfg: Config):
        self.env = env
        self.cfg = cfg
        self.pool_size = max(1, int(cfg.db_size * (1 - cfg.contention)))
        self.locks = [Lock(env) for _ in range(cfg.db_size)]
        self.versions = [0] * cfg.db_size

    def sample_keyset(self, k):
        pool = min(self.pool_size, self.cfg.db_size)
        keys = self.env.rng.keyset.sample(range(pool), min(k, pool))
        return sorted(keys)


class Core:
    def __init__(self, env, cfg: Config, policy: PreemptionPolicy, core_id: int, tracer=None):
        self.env = env
        self.cfg = cfg
        self.policy = policy
        self.id = core_id
        self.resource = Lock(env)
        self.current_proc = None
        self.current_txn_id = None
        self.critical = False
        self.critical_since = None
        self.preempt_count = 0
        self.deferred_count = 0
        # A3: steps remaining for whatever transaction is currently running
        # on this core (OCC: remaining reads + 1 for validation; 2PL:
        # remaining lock+operation steps). None when no transaction is
        # running. Set/decremented by transactions.py; read by
        # CommitProximityPolicy.
        self.ops_remaining = None
        # Only used for the Phase 6 event-trace timeline (tracer.detailed) —
        # see _os_scheduler below. None/non-detailed is the CLI's default
        # path and adds no new events, so results.json stays unaffected.
        self.tracer = tracer
        if policy.fires:
            env.process(self._os_scheduler())

    def _os_scheduler(self):
        while True:
            yield self.env.timeout(self.env.rng.preempt(self.id).expovariate(self.cfg.preempt_rate))
            if self.current_proc is None:
                continue
            # Capture which process/transaction this decision is ABOUT
            # before calling raise_interrupt(): that call can synchronously
            # drive the interrupted transaction all the way to completion
            # (e.g. a second preemption during OCC's own penalty-recovery
            # wait propagates Preempted out of run_operation uncaught,
            # aborting the transaction and clearing self.current_txn_id —
            # all inside this same call), which would otherwise leave the
            # marker below tagged with the *next* (or no) transaction.
            proc = self.current_proc
            txn_id = self.current_txn_id
            if self.policy.should_defer(self, self.env):
                self.deferred_count += 1
                if self.tracer and self.tracer.detailed:
                    self.tracer.mark(self.id, txn_id, "os_defer", self.env.now)
                continue
            if proc.raise_interrupt("os_preempt"):
                self.preempt_count += 1
                if self.tracer and self.tracer.detailed:
                    self.tracer.mark(self.id, txn_id, "os_preempt", self.env.now)

    def enter_critical(self):
        # grace_clock="per_lock" (default): always stamp — reproduces the
        # original unconditional-reset behavior exactly, since this branch
        # is taken regardless of self.critical's value.
        # grace_clock="per_section": a re-entrant call while already
        # critical (e.g. 2PL acquiring its 2nd..Nth lock) must not push the
        # clock forward, so GracePeriodPolicy's bound is measured from
        # first entry into the critical region, not the most recent lock.
        if not (self.cfg.grace_clock == "per_section" and self.critical):
            self.critical_since = self.env.now
        self.critical = True

    def exit_critical(self):
        self.critical = False
        self.critical_since = None


@dataclass
class Stats:
    committed_latencies: list = field(default_factory=list)
    # G: arrival-to-resolution time for every COMMITTED *and* ABORTED
    # transaction (committed_latencies above is committed-only and is left
    # untouched). A separate list, not a derived re-filter, because aborted
    # transactions never appear in committed_latencies at all.
    resolved_latencies: list = field(default_factory=list)
    aborted: int = 0
    committed: int = 0
    attempted: int = 0
    cpu_ms_wasted_on_aborts: float = 0.0
    preempt_events: int = 0
    deferred_events: int = 0
    # B: how many preemption attempts landed on a transaction already
    # waiting out a preempt_penalty (double_preempt="ignore" only — stays
    # 0 under the default "legacy" mode, since legacy lets those attempts
    # escape run_operation as Preempted instead of counting+discarding them).
    preempt_attempts_while_descheduled: int = 0
    # D: abort-cause breakdown. Always kept in exact sync with `aborted`
    # (see transactions.py) — aborts_validation_preempted + aborts_
    # version_conflict + aborts_lock_timeout must equal aborted in every run.
    aborts_validation_preempted: int = 0
    aborts_version_conflict: int = 0
    aborts_lock_timeout: int = 0
    # A1: preemptions by phase, incremented only when a preemption actually
    # lands (i.e. inside run_operation's own except-Preempted blocks, which
    # fire exactly once per successful Process.raise_interrupt — a deferred
    # attempt never reaches here at all). preempts_2pl_no_lock exists only
    # as a safety net: every 2PL run_operation call happens after that key's
    # lock is already appended to `held` (transactions.py), so this should
    # structurally always stay 0 — see Part A verification.
    preempts_occ_read: int = 0
    preempts_occ_validation: int = 0
    preempts_2pl_lock_held: int = 0
    preempts_2pl_no_lock: int = 0
    # A2: of the transactions preempted at least once during OCC validation
    # (was_preempted True, regardless of occ_validation_preempt mode — see
    # transactions.py), how many went on to commit vs. abort on a version
    # conflict. Tracked here, at the source, rather than reconstructed from
    # outside — see the Phase-2 report for why an external before/after
    # snapshot of committed/aborted is NOT a safe way to attribute outcomes
    # to a specific transaction in a concurrent run.
    txns_preempted_in_validation: int = 0
    txns_preempted_in_validation_committed: int = 0
    txns_preempted_in_validation_aborted: int = 0

    def abort_rate(self):
        return self.aborted / self.attempted if self.attempted else 0.0

    def abort_rate_resolved(self):
        """F: denominator is resolved transactions (committed+aborted) only,
        excluding whatever's still outstanding/in-flight — unlike
        abort_rate(), whose denominator (attempted) includes transactions
        truncated at the sim_time boundary that never resolved either way."""
        resolved = self.committed + self.aborted
        return self.aborted / resolved if resolved else 0.0

    def throughput(self, sim_time_ms):
        return self.committed / (sim_time_ms / 1000.0)

    def latency_stats(self):
        if not self.committed_latencies:
            return 0.0, 0.0
        return (sum(self.committed_latencies) / len(self.committed_latencies),
                percentile(self.committed_latencies, 99))

    def resolved_latency_stats(self):
        """G: mean/P99 over committed+aborted arrival-to-resolution time."""
        if not self.resolved_latencies:
            return 0.0, 0.0
        return (sum(self.resolved_latencies) / len(self.resolved_latencies),
                percentile(self.resolved_latencies, 99))


class Tracer:
    """Records execution segments for a bounded time window on one core, so
    the dashboard can render an honest Gantt timeline without the JSON
    blowing up. Segment kinds: cpu, lock_wait, preempt_penalty, and (OCC,
    detailed only) validation.

    `detailed=False` (the default, used by the CLI's results.json output)
    reproduces the original Phase-1 behavior exactly — no arrival markers,
    no os_defer/os_preempt markers, no distinct validation segment kind.
    `detailed=True` (used only by the Phase 6 API endpoint) adds all of the
    above for the interactive timeline/event-trace view. This flag exists
    specifically so the richer Phase 6 tracing can never change what the
    original CLI script writes to results.json."""

    def __init__(self, window=(0.0, 200.0), core_id=0, detailed=False):
        self.window = window
        self.core_id = core_id
        self.detailed = detailed
        self.segments = []
        self.markers = []  # commit/abort/(detailed: arrival/os_defer/os_preempt) events

    def active(self, core_id, t):
        return core_id == self.core_id and self.window[0] <= t <= self.window[1]

    def seg(self, core_id, txn_id, kind, start, end, **extra):
        if self.active(core_id, start):
            d = {"txn": txn_id, "kind": kind, "start": round(start, 3),
                 "end": round(min(end, self.window[1]), 3)}
            d.update(extra)
            self.segments.append(d)

    def mark(self, core_id, txn_id, event, t):
        if self.active(core_id, t):
            self.markers.append({"txn": txn_id, "event": event, "t": round(t, 3)})
