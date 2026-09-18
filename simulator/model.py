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


class DB:
    def __init__(self, env: Environment, cfg: Config):
        self.env = env
        self.cfg = cfg
        self.pool_size = max(1, int(cfg.db_size * (1 - cfg.contention)))
        self.locks = [Lock(env) for _ in range(cfg.db_size)]
        self.versions = [0] * cfg.db_size

    def sample_keyset(self, k):
        pool = min(self.pool_size, self.cfg.db_size)
        keys = random.sample(range(pool), min(k, pool))
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
        # Only used for the Phase 6 event-trace timeline (tracer.detailed) —
        # see _os_scheduler below. None/non-detailed is the CLI's default
        # path and adds no new events, so results.json stays unaffected.
        self.tracer = tracer
        if policy.fires:
            env.process(self._os_scheduler())

    def _os_scheduler(self):
        while True:
            yield self.env.timeout(random.expovariate(self.cfg.preempt_rate))
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
        self.critical = True
        self.critical_since = self.env.now

    def exit_critical(self):
        self.critical = False
        self.critical_since = None


@dataclass
class Stats:
    committed_latencies: list = field(default_factory=list)
    aborted: int = 0
    committed: int = 0
    attempted: int = 0
    cpu_ms_wasted_on_aborts: float = 0.0
    preempt_events: int = 0
    deferred_events: int = 0

    def abort_rate(self):
        return self.aborted / self.attempted if self.attempted else 0.0

    def throughput(self, sim_time_ms):
        return self.committed / (sim_time_ms / 1000.0)

    def latency_stats(self):
        if not self.committed_latencies:
            return 0.0, 0.0
        return (sum(self.committed_latencies) / len(self.committed_latencies),
                percentile(self.committed_latencies, 99))


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
