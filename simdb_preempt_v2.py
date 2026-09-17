"""
SimDB-Preempt v2 — a sandbox for testing OS scheduling algorithms against
the OS-DBMS "layer gap" WITHOUT touching a kernel.

WHY THIS EXISTS
----------------
You cannot easily test "would a database-aware OS scheduler help?" against
a real kernel — that requires patching the scheduler, instrumenting a real
DBMS, and re-running production-scale workloads for every candidate idea.
This simulator gives you the same signal in milliseconds: it models what a
CPU core, a lock table, and a transaction protocol (2PL or OCC) actually
experience under a given preemption policy, so you can plug in a NEW
scheduling algorithm as a ~10-line Python class and immediately see its
effect on abort rate, throughput and P99 latency — no kernel required.

ZERO DEPENDENCIES. Pure standard library. `python3 simdb_preempt_v2.py`
just works — nothing to pip install, nothing to compile.

QUICK START FOR ALGORITHM DESIGNERS
------------------------------------
Subclass PreemptionPolicy and implement one method:

    class MyPolicy(PreemptionPolicy):
        name = "my_policy"
        def should_defer(self, core, env) -> bool:
            # Return True to SKIP this preemption attempt (i.e. your
            # algorithm decided now is a bad time to preempt this core).
            return core.critical and core.ms_in_critical(env) < 5.0

Then add it to POLICIES_TO_TEST at the bottom of this file and re-run.
The dashboard (dashboard.html) will show exactly how much of the layer
gap your policy closes, next to the built-in baselines.
"""

from __future__ import annotations
import heapq
import itertools
import json
import random
from collections import deque
from dataclasses import dataclass, field
from typing import Optional


# ======================================================================
# 1. MINIMAL DISCRETE-EVENT ENGINE  (replaces the simpy dependency)
# ======================================================================
# Three primitives only: Environment.timeout(), Lock (a FIFO mutex with
# optional acquire-timeout), and Process.raise_interrupt() (an OS-style
# preemption signal). This is intentionally small enough to read in five
# minutes — the entire "kernel model" you're testing against is right here.

class Preempted(Exception):
    """Raised inside a process's generator when the OS preempts it while
    it is mid-flight on a plain timeout (i.e. actually holding the CPU)."""
    def __init__(self, cause=None):
        super().__init__(cause)
        self.cause = cause


class Environment:
    def __init__(self):
        self.now = 0.0
        self._heap = []
        self._seq = itertools.count()
        self._immediate = deque()
        self.active_process = None

    def _schedule(self, delay, callback):
        timer = [self.now + delay, next(self._seq), callback, False]
        heapq.heappush(self._heap, timer)
        return timer

    def _cancel(self, timer):
        timer[3] = True

    def _schedule_now(self, callback):
        self._immediate.append(callback)

    def timeout(self, delay):
        return ("timeout", max(delay, 0.0))

    def process(self, gen):
        return Process(self, gen)

    def run(self, until):
        while True:
            while self._immediate:
                self._immediate.popleft()()
            if not self._heap or self._heap[0][0] > until:
                break
            timer = heapq.heappop(self._heap)
            if timer[3]:
                continue
            self.now = timer[0]
            timer[2]()
        self.now = until


class Process:
    """Drives a transaction/scheduler generator. Yield ('timeout', ms) to
    wait, or a Lock.request(...) instruction to acquire a mutex."""

    def __init__(self, env: Environment, gen):
        self.env = env
        self.gen = gen
        self.timer = None
        self.alive = True
        env._schedule_now(self._start)

    def _start(self):
        self._step(self.gen.send, None)

    def _step(self, driver, arg):
        prev = self.env.active_process
        self.env.active_process = self
        try:
            instr = driver(arg)
        except StopIteration:
            self.alive = False
            self.env.active_process = prev
            return
        finally:
            self.env.active_process = prev
        self._handle_instr(instr)

    def _handle_instr(self, instr):
        kind = instr[0]
        if kind == "timeout":
            self.timer = self.env._schedule(instr[1], self._on_timer)
        elif kind == "lock":
            _, lock, timeout = instr
            lock._acquire(self, timeout)
        else:
            raise RuntimeError(f"unknown yield instruction: {instr!r}")

    def _on_timer(self):
        self.timer = None
        self._step(self.gen.send, None)

    def resume_value(self, value):
        self._step(self.gen.send, value)

    def raise_interrupt(self, cause=None):
        """OS-style preemption. Only has an effect if the process is
        currently holding the CPU on a plain timeout (i.e. inside
        run_operation) — a process merely queued for a lock is not
        'running' and so cannot be preempted off a core it doesn't hold."""
        if self.timer is not None:
            self.env._cancel(self.timer)
            self.timer = None
            self._step(self.gen.throw, Preempted(cause))
            return True
        return False


class Lock:
    """A FIFO mutex used both for per-record locks (2PL) and for the core
    itself (only one transaction runs on a core at a time)."""

    def __init__(self, env: Environment):
        self.env = env
        self.owner = None
        self._waiters = []

    def request(self, timeout: Optional[float] = None):
        return ("lock", self, timeout)

    def _acquire(self, proc: Process, timeout):
        if self.owner is None:
            self.owner = proc
            self.env._schedule_now(lambda: proc.resume_value(True))
            return
        entry = {"proc": proc, "timer": None, "done": False}
        if timeout is not None:
            def on_timeout():
                if entry["done"]:
                    return
                entry["done"] = True
                if entry in self._waiters:
                    self._waiters.remove(entry)
                proc.resume_value(False)
            entry["timer"] = self.env._schedule(timeout, on_timeout)
        self._waiters.append(entry)

    def release(self):
        self.owner = None
        while self._waiters:
            entry = self._waiters.pop(0)
            if entry["done"]:
                continue
            entry["done"] = True
            if entry["timer"] is not None:
                self.env._cancel(entry["timer"])
            self.owner = entry["proc"]
            self.env._schedule_now(lambda p=entry["proc"]: p.resume_value(True))
            return


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


# ======================================================================
# 2. PLUGGABLE PREEMPTION POLICIES  <-- put your new algorithm here
# ======================================================================

class PreemptionPolicy:
    """Base class for an OS scheduling algorithm under test.

    `should_defer` is called every time the (simulated) OS scheduler is
    about to preempt a core, and only if that core is currently running
    something. Return True to skip the preemption. Everything you need to
    make that decision is on `core`: core.critical (bool: is the running
    transaction inside a lock hold / OCC validation right now), and
    core.critical_since (sim time the critical section started, or None).
    """
    name = "base"
    fires = True  # set False for a policy that never even attempts preemption

    def should_defer(self, core: "Core", env: Environment) -> bool:
        raise NotImplementedError


class NoPreemptPolicy(PreemptionPolicy):
    """Upper-bound baseline: the OS never interrupts a running transaction."""
    name = "none"
    fires = False

    def should_defer(self, core, env):
        return True


class RandomPolicy(PreemptionPolicy):
    """Models a critical-section-blind OS scheduler (default Linux EEVDF/
    CFS behaviour): preemption attempts fire on schedule regardless of
    what the DBMS is doing. This is the "layer gap" made concrete."""
    name = "random"

    def should_defer(self, core, env):
        return False


class CriticalAwarePolicy(PreemptionPolicy):
    """A scheduler that defers preemption while a core is in a DBMS
    critical section (holding a lock, or inside OCC validation)."""
    name = "critical_aware"

    def should_defer(self, core, env):
        return core.critical


class GracePeriodPolicy(PreemptionPolicy):
    """EXAMPLE custom algorithm, included to show the extension pattern.

    Idea: critical-aware scheduling is great but can starve the OS
    scheduler indefinitely if a transaction sits in a long critical
    section. This policy grants a lock-holder a short grace period
    (like critical-aware) but caps it — after `max_grace_ms` inside one
    critical section, the OS is allowed to preempt anyway, trading a
    bounded amount of cascading-wait risk for a fairness/starvation
    guarantee. Tune `max_grace_ms` and re-run to see the abort-rate /
    P99-latency trade-off curve this creates.
    """
    name = "grace_period"

    def __init__(self, max_grace_ms: float = 1.0):
        self.max_grace_ms = max_grace_ms

    def should_defer(self, core, env):
        if not core.critical:
            return False
        elapsed = env.now - (core.critical_since or env.now)
        return elapsed < self.max_grace_ms


BUILTIN_POLICIES = {
    "none": NoPreemptPolicy,
    "random": RandomPolicy,
    "critical_aware": CriticalAwarePolicy,
    "grace_period": GracePeriodPolicy,
}


# ======================================================================
# 3. CONFIG, DB, CORE, STATS, TRACER
# ======================================================================

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
    def __init__(self, env, cfg: Config, policy: PreemptionPolicy, core_id: int):
        self.env = env
        self.cfg = cfg
        self.policy = policy
        self.id = core_id
        self.resource = Lock(env)
        self.current_proc = None
        self.critical = False
        self.critical_since = None
        self.preempt_count = 0
        self.deferred_count = 0
        if policy.fires:
            env.process(self._os_scheduler())

    def _os_scheduler(self):
        while True:
            yield self.env.timeout(random.expovariate(self.cfg.preempt_rate))
            if self.current_proc is None:
                continue
            if self.policy.should_defer(self, self.env):
                self.deferred_count += 1
                continue
            if self.current_proc.raise_interrupt("os_preempt"):
                self.preempt_count += 1

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
    """Records execution segments for a bounded time window on core 0 only,
    so the dashboard can render an honest Gantt timeline without the JSON
    blowing up. Segment kinds: cpu, lock_wait, preempt_penalty."""

    def __init__(self, window=(0.0, 200.0), core_id=0):
        self.window = window
        self.core_id = core_id
        self.segments = []
        self.markers = []  # commit/abort events

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


# ======================================================================
# 4. TRANSACTIONS
# ======================================================================

def run_operation(env: Environment, core: Core, cfg: Config, txn_id, tracer=None):
    remaining = random.expovariate(1.0 / cfg.mean_op_time)
    extra_delay = 0.0
    preempted = False
    while remaining > 1e-9:
        start = env.now
        try:
            yield env.timeout(remaining)
            if tracer:
                tracer.seg(core.id, txn_id, "cpu", start, env.now)
            remaining = 0.0
        except Preempted:
            preempted = True
            elapsed = env.now - start
            if tracer:
                tracer.seg(core.id, txn_id, "cpu", start, env.now)
            remaining -= elapsed
            penalty = random.expovariate(1.0 / cfg.preempt_penalty)
            extra_delay += penalty
            pstart = env.now
            yield env.timeout(penalty)
            if tracer:
                tracer.seg(core.id, txn_id, "preempt_penalty", pstart, env.now)
    return extra_delay, preempted


def transaction_2pl(env, txn_id, db: DB, core: Core, cfg: Config, stats: Stats, tracer=None):
    stats.attempted += 1
    arrival = env.now
    keys = db.sample_keyset(random.randint(2, cfg.ops_per_txn))
    held = []
    cpu_time_spent = 0.0
    aborted = False

    yield core.resource.request()
    core.current_proc = env.active_process

    for key in keys:
        wstart = env.now
        acquired = yield db.locks[key].request(cfg.lock_timeout)
        if tracer and env.now > wstart:
            tracer.seg(core.id, txn_id, "lock_wait", wstart, env.now, key=key)
        if not acquired:
            aborted = True
            break
        held.append(key)
        core.enter_critical()
        try:
            extra_delay, _ = yield from run_operation(env, core, cfg, txn_id, tracer)
            cpu_time_spent += cfg.mean_op_time + extra_delay
        except Preempted:
            pass

    if not aborted:
        stats.committed += 1
        stats.committed_latencies.append(env.now - arrival)
        if tracer:
            tracer.mark(core.id, txn_id, "commit", env.now)
    else:
        stats.aborted += 1
        stats.cpu_ms_wasted_on_aborts += cpu_time_spent
        if tracer:
            tracer.mark(core.id, txn_id, "abort", env.now)

    for key in held:
        db.locks[key].release()
    core.exit_critical()
    core.current_proc = None
    core.resource.release()


def transaction_occ(env, txn_id, db: DB, core: Core, cfg: Config, stats: Stats, tracer=None):
    stats.attempted += 1
    arrival = env.now
    keys = db.sample_keyset(random.randint(2, cfg.ops_per_txn))
    read_versions = {}
    cpu_time_spent = 0.0
    aborted = False

    yield core.resource.request()
    core.current_proc = env.active_process
    core.exit_critical()  # read phase: safe to preempt

    for key in keys:
        read_versions[key] = db.versions[key]
        try:
            extra_delay, _ = yield from run_operation(env, core, cfg, txn_id, tracer)
            cpu_time_spent += cfg.mean_op_time + extra_delay
        except Preempted:
            pass

    core.enter_critical()  # validation + write phase: critical
    was_preempted = False
    try:
        extra_delay, was_preempted = yield from run_operation(env, core, cfg, txn_id, tracer)
        cpu_time_spent += cfg.mean_op_time + extra_delay
    except Preempted:
        was_preempted = True

    if was_preempted:
        aborted = True
    else:
        for key in keys:
            if db.versions[key] != read_versions[key]:
                aborted = True
                break

    if not aborted:
        for key in keys:
            db.versions[key] += 1
        stats.committed += 1
        stats.committed_latencies.append(env.now - arrival)
        if tracer:
            tracer.mark(core.id, txn_id, "commit", env.now)
    else:
        stats.aborted += 1
        stats.cpu_ms_wasted_on_aborts += cpu_time_spent
        if tracer:
            tracer.mark(core.id, txn_id, "abort", env.now)

    core.exit_critical()
    core.current_proc = None
    core.resource.release()


# ======================================================================
# 5. DRIVER
# ======================================================================

def arrival_process(env, db, core_pool, cfg, stats, protocol, tracer):
    txn_id = 0
    while True:
        yield env.timeout(random.expovariate(cfg.arrival_rate))
        txn_id += 1
        core = core_pool[txn_id % len(core_pool)]
        fn = transaction_2pl if protocol == "2pl" else transaction_occ
        env.process(fn(env, txn_id, db, core, cfg, stats, tracer))


def run_simulation(protocol: str, policy: PreemptionPolicy, cfg: Config, tracer=None):
    random.seed(cfg.seed)
    env = Environment()
    db = DB(env, cfg)
    stats = Stats()
    cores = [Core(env, cfg, policy, i) for i in range(cfg.num_cores)]
    env.process(arrival_process(env, db, cores, cfg, stats, protocol, tracer))
    env.run(until=cfg.sim_time)
    mean_lat, p99_lat = stats.latency_stats()
    stats.preempt_events = sum(c.preempt_count for c in cores)
    stats.deferred_events = sum(c.deferred_count for c in cores)
    return {
        "protocol": protocol,
        "policy": policy.name,
        "attempted": stats.attempted,
        "committed": stats.committed,
        "aborted": stats.aborted,
        "abort_rate_pct": round(100 * stats.abort_rate(), 2),
        "throughput_tps": round(stats.throughput(cfg.sim_time), 2),
        "mean_latency_ms": round(mean_lat, 3),
        "p99_latency_ms": round(p99_lat, 3),
        "cpu_ms_wasted_on_aborts": round(stats.cpu_ms_wasted_on_aborts, 1),
        "preempt_events": stats.preempt_events,
        "deferred_events": stats.deferred_events,
    }


def layer_gap_score(rows_by_policy, metric, lower_is_better=True, baseline="none", worst="random"):
    """0-100+ score: how much of the distance between the blind-OS
    baseline (`worst`, usually 'random') and the ideal baseline
    (`baseline`, usually 'none') a policy recovers.
    100 = fully closes the layer gap (matches the no-preemption ideal).
    0   = no better than a critical-section-blind OS scheduler.
    <0  = actively worse than blind random preemption.
    """
    b = rows_by_policy[baseline][metric]
    w = rows_by_policy[worst][metric]
    out = {}
    for name, row in rows_by_policy.items():
        p = row[metric]
        if w == b:
            out[name] = 100.0 if p == b else 0.0
            continue
        if lower_is_better:
            score = (w - p) / (w - b) * 100.0
        else:
            score = (p - w) / (b - w) * 100.0
        out[name] = round(score, 1)
    return out


# ======================================================================
# 6. EXPERIMENT MATRIX  (edit POLICIES_TO_TEST to add your own algorithm)
# ======================================================================

TPL_CFG = Config(sim_time=15_000, contention=0.60, db_size=2000,
                  ops_per_txn=6, preempt_rate=0.14, arrival_rate=2.0,
                  lock_timeout=25.0, seed=42)
OCC_CFG = Config(sim_time=15_000, contention=0.913, db_size=2000,
                  ops_per_txn=8, preempt_rate=0.14, arrival_rate=2.0, seed=42)

POLICIES_TO_TEST = ["none", "random", "critical_aware", "grace_period"]


def build_policy(key):
    return BUILTIN_POLICIES[key]()


def run_matrix(protocol, cfg, policies=POLICIES_TO_TEST, tracer_map=None):
    rows = {}
    for key in policies:
        tracer = tracer_map.get(key) if tracer_map else None
        rows[key] = run_simulation(protocol, build_policy(key), cfg, tracer)
    return rows


def run_contention_sweep(protocol, base_cfg, contentions, policies=("none", "random", "critical_aware", "grace_period")):
    sweep = {p: [] for p in policies}
    for c in contentions:
        cfg = Config(**{**base_cfg.__dict__, "contention": c})
        for p in policies:
            r = run_simulation(protocol, build_policy(p), cfg)
            sweep[p].append({"contention": c, "abort_rate_pct": r["abort_rate_pct"],
                              "throughput_tps": r["throughput_tps"]})
    return sweep


def main():
    print("Running 2PL matrix ...")
    tpl_tracer = {"random": Tracer(window=(1000, 1150)),
                  "critical_aware": Tracer(window=(1000, 1150))}
    tpl_rows = run_matrix("2pl", TPL_CFG, tracer_map=tpl_tracer)
    tpl_gap = {
        "p99_latency_ms": layer_gap_score(tpl_rows, "p99_latency_ms", lower_is_better=True),
        "abort_rate_pct": layer_gap_score(tpl_rows, "abort_rate_pct", lower_is_better=True),
    }

    print("Running OCC matrix ...")
    occ_rows = run_matrix("occ", OCC_CFG)
    occ_gap = {
        "abort_rate_pct": layer_gap_score(occ_rows, "abort_rate_pct", lower_is_better=True),
        "throughput_tps": layer_gap_score(occ_rows, "throughput_tps", lower_is_better=False),
    }

    print("Running OCC contention sweep ...")
    contentions = [round(x, 3) for x in [0.5, 0.6, 0.7, 0.8, 0.85, 0.9, 0.913, 0.95]]
    occ_sweep = run_contention_sweep("occ", OCC_CFG, contentions)

    header = ["protocol", "policy", "attempted", "committed", "aborted",
              "abort_rate_pct", "throughput_tps", "mean_latency_ms",
              "p99_latency_ms", "cpu_ms_wasted_on_aborts", "preempt_events", "deferred_events"]
    all_rows = list(tpl_rows.values()) + list(occ_rows.values())
    widths = {h: max(len(h), max(len(str(r[h])) for r in all_rows)) for h in header}
    print(" | ".join(h.ljust(widths[h]) for h in header))
    print("-+-".join("-" * widths[h] for h in header))
    for r in all_rows:
        print(" | ".join(str(r[h]).ljust(widths[h]) for h in header))

    print("\nLayer-Gap Closure Score (2PL, P99 latency):", tpl_gap["p99_latency_ms"])
    print("Layer-Gap Closure Score (OCC, abort rate):  ", occ_gap["abort_rate_pct"])

    results = {
        "meta": {"policies_tested": POLICIES_TO_TEST},
        "tpl": {"cfg": TPL_CFG.__dict__, "matrix": tpl_rows, "layer_gap": tpl_gap},
        "occ": {"cfg": OCC_CFG.__dict__, "matrix": occ_rows, "layer_gap": occ_gap,
                "sweep": occ_sweep},
        "traces": {
            "tpl_random": {"segments": tpl_tracer["random"].segments,
                            "markers": tpl_tracer["random"].markers,
                            "window": tpl_tracer["random"].window},
            "tpl_critical_aware": {"segments": tpl_tracer["critical_aware"].segments,
                                     "markers": tpl_tracer["critical_aware"].markers,
                                     "window": tpl_tracer["critical_aware"].window},
        },
    }
    with open("results.json", "w") as f:
        json.dump(results, f, indent=2)
    print("\nWrote results.json (feed this into dashboard.html)")


if __name__ == "__main__":
    main()
