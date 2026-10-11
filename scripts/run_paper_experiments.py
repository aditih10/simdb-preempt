#!/usr/bin/env python3
"""
Phase 2 Part B experiment harness for SimDB-Preempt.

Runs the E0-E6 grid directly against the `simulator` package (no HTTP),
stdlib-only, parallelized with multiprocessing, resumable via raw.csv.

No changes to simulator/ are made or required by this script.

Usage:
    python scripts/run_paper_experiments.py --plan
    python scripts/run_paper_experiments.py --smoke
    python scripts/run_paper_experiments.py --run [--experiments E0,E1,...] [--workers N]
    python scripts/run_paper_experiments.py --summarize
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import multiprocessing as mp
import os
import platform
import random
import statistics
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from simulator.model import Config, DB, Core, RngPool, Stats, percentile  # noqa: E402
from simulator.engine import Environment, Lock  # noqa: E402
from simulator.policies import BUILTIN_POLICIES, GracePeriodPolicy  # noqa: E402
from simulator.transactions import arrival_process  # noqa: E402
from simulator.runner import TPL_CFG, OCC_CFG  # noqa: E402

RESULTS_DIR = REPO_ROOT / "results" / "paper"
RAW_CSV = RESULTS_DIR / "raw.csv"
SUMMARY_CSV = RESULTS_DIR / "summary.csv"
MANIFEST_JSON = RESULTS_DIR / "run_manifest.json"


# ===========================================================================
# Two-sided Student's t critical values at alpha=0.05 (the 97.5th
# percentile of the t distribution), df=1..30. Standard table values (e.g.
# NIST/SEMATECH e-Handbook of Statistical Methods, Table 1.3.6.7.2; also
# reproducible via R's qt(0.975, df) or Excel's T.INV.2T(0.05, df)).
# df=29 (N=30) = 2.045, matching the value already used in
# PHASE11_RESULTS_FREEZE.md for this same project's N=30 convention.
# ===========================================================================
T_CRIT_975 = {
    1: 12.706, 2: 4.303, 3: 3.182, 4: 2.776, 5: 2.571,
    6: 2.447, 7: 2.365, 8: 2.306, 9: 2.262, 10: 2.228,
    11: 2.201, 12: 2.179, 13: 2.160, 14: 2.145, 15: 2.131,
    16: 2.120, 17: 2.110, 18: 2.101, 19: 2.093, 20: 2.086,
    21: 2.080, 22: 2.074, 23: 2.069, 24: 2.064, 25: 2.060,
    26: 2.056, 27: 2.052, 28: 2.048, 29: 2.045, 30: 2.042,
}


def t_critical(df: int) -> float:
    """df <= 0: undefined (nan). df > 30: the t-table converges towards the
    normal z=1.960 as df grows; use df=30's value (2.042) as a slightly
    conservative (wider) stand-in rather than pulling in scipy for a more
    exact value — this project's grid never actually needs df > 29 (N=30)."""
    if df <= 0:
        return float("nan")
    if df in T_CRIT_975:
        return T_CRIT_975[df]
    return T_CRIT_975[30] if df > 30 else T_CRIT_975[1]


# ===========================================================================
# Grid definition
# ===========================================================================

# "Defaults for ALL experiments unless overridden" (Phase 2 Part B spec),
# plus "other fields from TPL_CFG" — these are the same numbers, so there's
# nothing to reconcile.
BASE_DEFAULTS = dict(
    rng_mode="split",
    double_preempt="ignore",
    grace_clock="per_section",
    occ_validation_preempt="delay",
    warmup_ms=1000.0,
    sim_time=15000.0,
    num_cores=8,
    db_size=2000,
    mean_op_time=0.4,
    preempt_rate=0.14,
    preempt_penalty=2.0,
    arrival_rate=2.0,
    lock_timeout=25.0,
)

SEEDS_N30 = list(range(1000, 1030))

BASE_POLICIES = ["none", "random", "critical_aware", "grace_period"]
EXTENDED_POLICIES = BASE_POLICIES + ["commit_proximity_k1", "commit_proximity_k2"]

# Adjustment (4): policy scope per experiment, to avoid redundant runs.
POLICY_SCOPE = {
    "E0": BASE_POLICIES,
    "E1": EXTENDED_POLICIES,
    "E2": EXTENDED_POLICIES,
    "E3": BASE_POLICIES,
    "E4": BASE_POLICIES,
    "E5": ["grace_period"],
    "E6": BASE_POLICIES,
}

E4_RATES = (1, 2, 3, 4, 5, 6)
E4_SATURATION_LATENCY_MS = 100.0  # "mean latency" = committed-only mean_latency_ms

# ---------------------------------------------------------------------------
# raw.csv column schema. IDENTITY_COLUMNS form the resumability dedup key
# (experiment, all config fields, policy, seed); METRIC_COLUMNS are every
# computed output.
# ---------------------------------------------------------------------------
IDENTITY_COLUMNS = [
    "experiment", "protocol", "policy", "seed",
    "num_cores", "db_size", "contention", "ops_per_txn", "mean_op_time",
    "lock_timeout", "preempt_rate", "preempt_penalty", "sim_time", "arrival_rate",
    "rng_mode", "double_preempt", "grace_clock", "occ_validation_preempt", "warmup_ms",
    "max_grace_ms",
]

METRIC_COLUMNS = [
    "attempted", "committed", "aborted",
    "abort_rate_pct", "abort_rate_resolved_pct",
    "aborts_validation_preempted", "aborts_version_conflict", "aborts_lock_timeout",
    "throughput_tps",
    "mean_latency_ms", "p50_latency_ms", "p95_latency_ms", "p99_latency_ms",
    "mean_latency_resolved_ms", "p99_latency_resolved_ms",
    "cpu_ms_wasted_on_aborts",
    "preempt_events", "deferred_events", "preempt_attempts_while_descheduled",
    "preempts_occ_read", "preempts_occ_validation",
    "preempts_2pl_lock_held", "preempts_2pl_no_lock",
    "txns_preempted_in_validation",
    "txns_preempted_in_validation_committed", "txns_preempted_in_validation_aborted",
    "mean_keyset_size_committed",
    "core_utilization_pct",
    "lock_wait_mean_ms", "lock_wait_p99_ms", "lock_wait_max_ms",
]

ALL_COLUMNS = IDENTITY_COLUMNS + METRIC_COLUMNS

_FLOAT_COLS = {
    "contention", "mean_op_time", "lock_timeout", "preempt_rate",
    "preempt_penalty", "sim_time", "arrival_rate", "warmup_ms", "max_grace_ms",
}
_INT_COLS = {"seed", "num_cores", "db_size", "ops_per_txn"}


def format_identity_value(col: str, v) -> str:
    """Canonical string form for an identity column — used both when
    writing a row and when computing a job's dedup key, so a freshly
    generated job and the same row re-read from raw.csv produce identical
    keys regardless of native-type vs. CSV-string origin."""
    if v is None or v == "":
        return ""
    if col in _FLOAT_COLS:
        return f"{float(v):.6f}"
    if col in _INT_COLS:
        return str(int(v))
    return str(v)


def make_job(experiment, protocol, policy, seed, contention, ops_per_txn,
             max_grace_ms=None, **overrides):
    job = dict(BASE_DEFAULTS)
    if max_grace_ms is None:
        # Every grace_period row carries its *actual* max_grace_ms (the
        # policy's own default, 1.0, unless swept in E5) rather than
        # leaving it blank — blank is reserved for policies where the
        # field genuinely doesn't apply.
        max_grace_ms = 1.0 if policy == "grace_period" else ""
    job.update(
        experiment=experiment, protocol=protocol, policy=policy, seed=seed,
        contention=contention, ops_per_txn=ops_per_txn, max_grace_ms=max_grace_ms,
    )
    job.update(overrides)
    return job


def job_key(job: dict) -> tuple:
    return tuple(format_identity_value(c, job.get(c, "")) for c in IDENTITY_COLUMNS)


def row_key(row: dict) -> tuple:
    return tuple(row.get(c, "") for c in IDENTITY_COLUMNS)


# ---------------------------------------------------------------------------
# Experiment generators. Every generator accepts seeds= so the smoke test
# can reuse the exact same job-construction logic with a smaller N.
# ---------------------------------------------------------------------------

def gen_E0(seeds=None):
    seeds = seeds or SEEDS_N30
    jobs = []
    arms = [
        ("E0_legacy", dict(rng_mode="legacy", double_preempt="legacy", grace_clock="per_lock",
                            occ_validation_preempt="abort", warmup_ms=0.0)),
        ("E0_corrected_abort", dict(rng_mode="split", double_preempt="ignore", grace_clock="per_section",
                                     occ_validation_preempt="abort", warmup_ms=1000.0)),
        ("E0_corrected", dict(rng_mode="split", double_preempt="ignore", grace_clock="per_section",
                               occ_validation_preempt="delay", warmup_ms=1000.0)),
    ]
    protocol_cfg = {
        "2pl": (TPL_CFG.contention, TPL_CFG.ops_per_txn),
        "occ": (OCC_CFG.contention, OCC_CFG.ops_per_txn),
    }
    for label, overrides in arms:
        for protocol, (contention, ops) in protocol_cfg.items():
            for policy in POLICY_SCOPE["E0"]:
                for seed in seeds:
                    jobs.append(make_job(label, protocol, policy, seed, contention, ops, **overrides))
    return jobs


def gen_E1(seeds=None):
    seeds = seeds or SEEDS_N30
    jobs = []
    workloads = [("W1", 0.6, 6), ("W2", 0.913, 8)]
    for wlabel, contention, ops in workloads:
        for policy in POLICY_SCOPE["E1"]:
            for seed in seeds:
                jobs.append(make_job(f"E1_{wlabel}", "2pl", policy, seed, contention, ops))
            for ovp in ("abort", "delay"):
                for seed in seeds:
                    jobs.append(make_job(f"E1_{wlabel}", "occ", policy, seed, contention, ops,
                                          occ_validation_preempt=ovp))
    return jobs


def gen_E2(seeds=None):
    seeds = seeds or SEEDS_N30
    jobs = []
    for contention in (0.5, 0.6, 0.7, 0.8, 0.9, 0.95):
        for protocol in ("2pl", "occ"):
            for policy in POLICY_SCOPE["E2"]:
                for seed in seeds:
                    jobs.append(make_job("E2", protocol, policy, seed, contention, 6))
    return jobs


def gen_E3(seeds=None):
    seeds = seeds or SEEDS_N30
    jobs = []
    for lock_timeout in (2, 4, 8, 16, 25):
        for contention in (0.6, 0.913):
            for policy in POLICY_SCOPE["E3"]:
                for seed in seeds:
                    jobs.append(make_job("E3", "2pl", policy, seed, contention, 6,
                                          lock_timeout=float(lock_timeout)))
    return jobs


def gen_E4_upper_bound(seeds=None):
    """Upper-bound job list for --plan only. The actual --run execution is
    adaptive (see run_E4_adaptive) — it checks mean committed latency after
    each arrival_rate point and skips higher rates once a (protocol,policy)
    saturates, so the true run count is <= this."""
    seeds = seeds or SEEDS_N30
    jobs = []
    for rate in E4_RATES:
        for protocol in ("2pl", "occ"):
            for policy in POLICY_SCOPE["E4"]:
                for seed in seeds:
                    jobs.append(make_job("E4", protocol, policy, seed, 0.6, 6,
                                          arrival_rate=float(rate)))
    return jobs


def gen_E5(seeds=None):
    seeds = seeds or SEEDS_N30
    jobs = []
    for grace in (0.25, 0.5, 1, 2, 4):
        for protocol in ("2pl", "occ"):
            for policy in POLICY_SCOPE["E5"]:
                for seed in seeds:
                    jobs.append(make_job("E5", protocol, policy, seed, 0.6, 6,
                                          max_grace_ms=float(grace)))
    return jobs


def gen_E6(seeds=None):
    seeds = seeds or SEEDS_N30
    jobs = []
    for penalty in (0.5, 1, 2, 4):
        for protocol in ("2pl", "occ"):
            for policy in POLICY_SCOPE["E6"]:
                for seed in seeds:
                    jobs.append(make_job("E6_penalty", protocol, policy, seed, 0.6, 6,
                                          preempt_penalty=float(penalty), preempt_rate=0.14))
    for rate in (0.05, 0.1, 0.14, 0.2, 0.3):
        for protocol in ("2pl", "occ"):
            for policy in POLICY_SCOPE["E6"]:
                for seed in seeds:
                    jobs.append(make_job("E6_rate", protocol, policy, seed, 0.6, 6,
                                          preempt_rate=float(rate), preempt_penalty=2.0))
    return jobs


ALL_GENERATORS = {
    "E0": gen_E0, "E1": gen_E1, "E2": gen_E2, "E3": gen_E3,
    "E4": gen_E4_upper_bound, "E5": gen_E5, "E6": gen_E6,
}


# ===========================================================================
# Simulation worker (top-level, picklable — required for multiprocessing
# spawn on Windows). No simulator changes: this only imports and calls
# existing public building blocks, plus its own scoped instrumentation for
# metrics run_simulation() doesn't expose (lock-wait distribution, core
# utilization, committed-transaction keyset size, p50/p95 latency).
# ===========================================================================

def build_config(job: dict) -> Config:
    return Config(
        num_cores=int(job["num_cores"]),
        db_size=int(job["db_size"]),
        contention=float(job["contention"]),
        ops_per_txn=int(job["ops_per_txn"]),
        mean_op_time=float(job["mean_op_time"]),
        lock_timeout=float(job["lock_timeout"]),
        preempt_rate=float(job["preempt_rate"]),
        preempt_penalty=float(job["preempt_penalty"]),
        sim_time=float(job["sim_time"]),
        arrival_rate=float(job["arrival_rate"]),
        seed=int(job["seed"]),
        rng_mode=job["rng_mode"],
        double_preempt=job["double_preempt"],
        grace_clock=job["grace_clock"],
        occ_validation_preempt=job["occ_validation_preempt"],
        warmup_ms=float(job["warmup_ms"]),
    )


def build_policy(job: dict):
    key = job["policy"]
    mg = job.get("max_grace_ms", "")
    if key == "grace_period" and mg not in (None, ""):
        return GracePeriodPolicy(max_grace_ms=float(mg))
    return BUILTIN_POLICIES[key]()


class _FullTracer:
    """Duck-typed stand-in for simulator.model.Tracer with no window/
    core_id restriction, so it captures every lock_wait segment and every
    commit/abort/arrival marker across all cores for the whole run.
    tracer.detailed=True only changes which *labels* get recorded
    (validation vs. cpu segment_kind, extra os_defer/os_preempt/arrival
    marks) — it never changes simulation timing, RNG consumption, or
    control flow (see transactions.py / model.py), so this is read-only
    instrumentation, not a behavior change."""

    def __init__(self):
        self.detailed = True
        self.lock_waits = []
        self.outcomes = {}
        self.arrivals = {}

    def seg(self, core_id, txn_id, kind, start, end, **extra):
        if kind == "lock_wait":
            self.lock_waits.append(end - start)

    def mark(self, core_id, txn_id, event, t):
        if event in ("commit", "abort"):
            self.outcomes[txn_id] = event
        elif event == "arrival":
            self.arrivals[txn_id] = t


def _simulate(protocol: str, policy, cfg: Config) -> dict:
    random.seed(cfg.seed)
    env = Environment()
    env.rng = RngPool(cfg)
    db = DB(env, cfg)
    stats = Stats()
    tracer = _FullTracer()
    cores = [Core(env, cfg, policy, i, tracer=tracer) for i in range(cfg.num_cores)]

    keyset_sizes_by_txn: dict[int, int] = {}
    _txn_counter = [0]
    orig_sample_keyset = DB.sample_keyset

    def patched_sample_keyset(self, k):
        keys = orig_sample_keyset(self, k)
        _txn_counter[0] += 1
        keyset_sizes_by_txn[_txn_counter[0]] = len(keys)
        return keys

    core_resource_ids = {id(c.resource): i for i, c in enumerate(cores)}
    busy = [0.0] * cfg.num_cores
    busy_start = [None] * cfg.num_cores
    orig_acquire = Lock._acquire
    orig_release = Lock.release

    def patched_acquire(self, proc, timeout):
        is_core = id(self) in core_resource_ids
        was_free = self.owner is None
        orig_acquire(self, proc, timeout)
        if is_core and was_free and self.owner is not None:
            busy_start[core_resource_ids[id(self)]] = self.env.now

    def patched_release(self):
        is_core = id(self) in core_resource_ids
        if is_core:
            i = core_resource_ids[id(self)]
            if busy_start[i] is not None:
                busy[i] += self.env.now - busy_start[i]
                busy_start[i] = None
        orig_release(self)
        if is_core:
            i = core_resource_ids[id(self)]
            if self.owner is not None:
                busy_start[i] = self.env.now

    DB.sample_keyset = patched_sample_keyset
    Lock._acquire = patched_acquire
    Lock.release = patched_release
    try:
        env.process(arrival_process(env, db, cores, cfg, stats, protocol, tracer))
        env.run(until=cfg.sim_time)
        for i in range(cfg.num_cores):
            if busy_start[i] is not None:
                busy[i] += cfg.sim_time - busy_start[i]
    finally:
        DB.sample_keyset = orig_sample_keyset
        Lock._acquire = orig_acquire
        Lock.release = orig_release

    stats.preempt_events = sum(c.preempt_count for c in cores)
    stats.deferred_events = sum(c.deferred_count for c in cores)

    mean_lat, p99_lat = stats.latency_stats()
    mean_res_lat, p99_res_lat = stats.resolved_latency_stats()
    p50_lat = percentile(stats.committed_latencies, 50)
    p95_lat = percentile(stats.committed_latencies, 95)

    committed_keyset_sizes = [
        keyset_sizes_by_txn[t] for t, outcome in tracer.outcomes.items()
        if outcome == "commit"
        and tracer.arrivals.get(t, 0.0) >= cfg.warmup_ms
        and t in keyset_sizes_by_txn
    ]
    mean_keyset_committed = (
        sum(committed_keyset_sizes) / len(committed_keyset_sizes)
        if committed_keyset_sizes else 0.0
    )

    core_utilization_pct = 100.0 * sum(busy) / (cfg.num_cores * cfg.sim_time)

    if protocol == "2pl":
        if tracer.lock_waits:
            lw_mean = sum(tracer.lock_waits) / len(tracer.lock_waits)
            lw_p99 = percentile(tracer.lock_waits, 99)
            lw_max = max(tracer.lock_waits)
        else:
            lw_mean = lw_p99 = lw_max = 0.0
    else:
        lw_mean = lw_p99 = lw_max = ""

    return {
        "attempted": stats.attempted,
        "committed": stats.committed,
        "aborted": stats.aborted,
        "abort_rate_pct": round(100 * stats.abort_rate(), 4),
        "abort_rate_resolved_pct": round(100 * stats.abort_rate_resolved(), 4),
        "aborts_validation_preempted": stats.aborts_validation_preempted,
        "aborts_version_conflict": stats.aborts_version_conflict,
        "aborts_lock_timeout": stats.aborts_lock_timeout,
        "throughput_tps": round(stats.throughput(cfg.sim_time), 4),
        "mean_latency_ms": round(mean_lat, 4),
        "p50_latency_ms": round(p50_lat, 4),
        "p95_latency_ms": round(p95_lat, 4),
        "p99_latency_ms": round(p99_lat, 4),
        "mean_latency_resolved_ms": round(mean_res_lat, 4),
        "p99_latency_resolved_ms": round(p99_res_lat, 4),
        "cpu_ms_wasted_on_aborts": round(stats.cpu_ms_wasted_on_aborts, 2),
        "preempt_events": stats.preempt_events,
        "deferred_events": stats.deferred_events,
        "preempt_attempts_while_descheduled": stats.preempt_attempts_while_descheduled,
        "preempts_occ_read": stats.preempts_occ_read,
        "preempts_occ_validation": stats.preempts_occ_validation,
        "preempts_2pl_lock_held": stats.preempts_2pl_lock_held,
        "preempts_2pl_no_lock": stats.preempts_2pl_no_lock,
        "txns_preempted_in_validation": stats.txns_preempted_in_validation,
        "txns_preempted_in_validation_committed": stats.txns_preempted_in_validation_committed,
        "txns_preempted_in_validation_aborted": stats.txns_preempted_in_validation_aborted,
        "mean_keyset_size_committed": round(mean_keyset_committed, 4),
        "core_utilization_pct": round(core_utilization_pct, 4),
        "lock_wait_mean_ms": round(lw_mean, 4) if lw_mean != "" else "",
        "lock_wait_p99_ms": round(lw_p99, 4) if lw_p99 != "" else "",
        "lock_wait_max_ms": round(lw_max, 4) if lw_max != "" else "",
    }


def run_job(job: dict) -> dict:
    """Top-level worker function — must stay importable at module scope for
    Windows' spawn start method to pickle it into child processes."""
    cfg = build_config(job)
    policy = build_policy(job)
    metrics = _simulate(job["protocol"], policy, cfg)
    row = {col: format_identity_value(col, job.get(col, "")) for col in IDENTITY_COLUMNS}
    row.update(metrics)
    return row


# ===========================================================================
# raw.csv I/O + resumability
# ===========================================================================

def load_done() -> dict:
    if not RAW_CSV.exists():
        return {}
    done = {}
    with open(RAW_CSV, newline="") as f:
        for row in csv.DictReader(f):
            done[row_key(row)] = row
    return done


def open_raw_writer():
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    write_header = (not RAW_CSV.exists()) or RAW_CSV.stat().st_size == 0
    f = open(RAW_CSV, "a", newline="")
    writer = csv.DictWriter(f, fieldnames=ALL_COLUMNS)
    if write_header:
        writer.writeheader()
        f.flush()
    return f, writer


# ===========================================================================
# E4's adaptive saturation logic (only experiment that can't be a flat,
# fully-precomputed job list, since later points depend on earlier results)
# ===========================================================================

def run_E4_adaptive(pool, done: dict, writer, f, seeds):
    for protocol in ("2pl", "occ"):
        for policy in POLICY_SCOPE["E4"]:
            saturated_at = None
            for rate in E4_RATES:
                if saturated_at is not None:
                    print(f"[E4] {protocol}/{policy}: skipping arrival_rate={rate} "
                          f"(saturated at arrival_rate={saturated_at})")
                    continue
                jobs = [make_job("E4", protocol, policy, s, 0.6, 6, arrival_rate=float(rate))
                        for s in seeds]
                point_rows = [done[job_key(j)] for j in jobs if job_key(j) in done]
                pending = [j for j in jobs if job_key(j) not in done]
                for row in pool.imap_unordered(run_job, pending, chunksize=4):
                    writer.writerow(row)
                    f.flush()
                    done[row_key(row)] = row
                    point_rows.append(row)
                mean_of_means = sum(float(r["mean_latency_ms"]) for r in point_rows) / len(point_rows)
                print(f"[E4] {protocol}/{policy} arrival_rate={rate}: "
                      f"mean_latency_ms (avg of {len(point_rows)} seeds) = {mean_of_means:.3f}")
                if mean_of_means > E4_SATURATION_LATENCY_MS:
                    saturated_at = rate
                    print(f"[E4] {protocol}/{policy}: SATURATED at arrival_rate={rate} "
                          f"(mean latency {mean_of_means:.1f}ms > {E4_SATURATION_LATENCY_MS}ms) "
                          f"— skipping higher rates")


# ===========================================================================
# --plan
# ===========================================================================

def print_plan():
    total = 0
    for name, fn in ALL_GENERATORS.items():
        n = len(fn())
        note = " (upper bound; actual depends on saturation skipping)" if name == "E4" else ""
        print(f"{name}: {n} runs{note}")
        total += n
    print(f"TOTAL: {total} runs (upper bound)")
    return total


# ===========================================================================
# --smoke : E1 with N=3, printed summary only, nothing written to raw.csv
# ===========================================================================

def run_smoke(workers: int):
    seeds = [1000, 1001, 1002]
    jobs = gen_E1(seeds=seeds)
    print(f"[smoke] E1, N={len(seeds)} (seeds={seeds}): {len(jobs)} runs, {workers} workers")

    t0 = time.time()
    rows = []
    with mp.Pool(processes=workers) as pool:
        for row in pool.imap_unordered(run_job, jobs, chunksize=1):
            rows.append(row)
    elapsed = time.time() - t0
    per_run = elapsed / len(rows)
    print(f"[smoke] completed {len(rows)} runs in {elapsed:.1f}s ({per_run:.3f}s/run average)")

    group_cols = [c for c in IDENTITY_COLUMNS if c != "seed"]
    groups: dict = {}
    for row in rows:
        groups.setdefault(tuple(row[c] for c in group_cols), []).append(row)

    report_fields = [
        "abort_rate_resolved_pct",
        "aborts_validation_preempted", "aborts_version_conflict", "aborts_lock_timeout",
        "p99_latency_ms", "p99_latency_resolved_ms",
        "preempts_occ_read", "preempts_occ_validation", "preempts_2pl_lock_held",
    ]
    for key in sorted(groups):
        label = dict(zip(group_cols, key))
        grows = groups[key]
        print(f"\n=== protocol={label['protocol']} policy={label['policy']} "
              f"contention={label['contention']} ops_per_txn={label['ops_per_txn']} "
              f"occ_validation_preempt={label['occ_validation_preempt']} (n={len(grows)}) ===")
        for field in report_fields:
            vals = [float(r[field]) for r in grows if r.get(field, "") != ""]
            if not vals:
                continue
            m = statistics.mean(vals)
            sd = statistics.stdev(vals) if len(vals) > 1 else 0.0
            print(f"  {field}: mean={m:.4f} sd={sd:.4f} values={[round(v, 4) for v in vals]}")

    n_upper_bound = sum(len(fn()) for fn in ALL_GENERATORS.values())
    est_seconds = per_run * n_upper_bound / workers
    print(f"\n[smoke] extrapolation: {per_run:.3f}s/run x {n_upper_bound} runs (upper bound) / "
          f"{workers} workers (ideal parallelism) ~= {est_seconds/60:.1f} min "
          f"({est_seconds/3600:.2f} hours)")
    print("[smoke] this is a rough estimate: E4's actual run count is <= its upper bound "
          "(saturation skipping), and per-run cost varies with arrival_rate/contention/protocol, "
          "so treat this as order-of-magnitude, not a precise ETA.")


# ===========================================================================
# --run : the full (or --experiments subset) grid, resumable
# ===========================================================================

def run_grid(workers: int, only_experiments=None, seeds=None):
    seeds = seeds or SEEDS_N30
    order = ["E0", "E1", "E2", "E3", "E4", "E5", "E6"]
    if only_experiments:
        order = [e for e in order if e in only_experiments]

    done = load_done()
    print(f"[run] {len(done)} runs already in {RAW_CSV} (will be skipped)")

    f, writer = open_raw_writer()
    start_ts = time.time()
    try:
        with mp.Pool(processes=workers) as pool:
            for name in order:
                if name == "E4":
                    print("[run] E4 (adaptive saturation sweep)...")
                    run_E4_adaptive(pool, done, writer, f, seeds)
                    continue
                jobs = ALL_GENERATORS[name](seeds=seeds)
                pending = [j for j in jobs if job_key(j) not in done]
                print(f"[run] {name}: {len(jobs)} total, {len(pending)} pending")
                n_done_this_exp = 0
                for row in pool.imap_unordered(run_job, pending, chunksize=4):
                    writer.writerow(row)
                    f.flush()
                    done[row_key(row)] = row
                    n_done_this_exp += 1
                    if n_done_this_exp % 100 == 0:
                        print(f"[run] {name}: {n_done_this_exp}/{len(pending)} done "
                              f"({time.time()-start_ts:.0f}s elapsed total)")
                print(f"[run] {name}: complete ({n_done_this_exp} run in this invocation)")
    finally:
        f.close()
    end_ts = time.time()

    write_manifest(start_ts, end_ts)
    print(f"[run] invocation finished in {end_ts-start_ts:.0f}s. "
          f"{sum(1 for _ in open(RAW_CSV))-1} total rows now in {RAW_CSV}")


def write_manifest(start_ts, end_ts):
    try:
        commit = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, text=True
        ).strip()
    except Exception:
        commit = "unknown"
    grid_counts = {name: len(fn()) for name, fn in ALL_GENERATORS.items()}
    n_rows_now = 0
    if RAW_CSV.exists():
        with open(RAW_CSV, newline="") as fh:
            n_rows_now = sum(1 for _ in csv.reader(fh)) - 1
    manifest = {
        "git_commit": commit,
        "python_version": platform.python_version(),
        "run_started_at": datetime.fromtimestamp(start_ts, tz=timezone.utc).isoformat(),
        "run_finished_at": datetime.fromtimestamp(end_ts, tz=timezone.utc).isoformat(),
        "wall_clock_seconds_this_invocation": round(end_ts - start_ts, 1),
        "seeds_n30": SEEDS_N30,
        "base_defaults": BASE_DEFAULTS,
        "policy_scope": POLICY_SCOPE,
        "grid_definition_upper_bound_counts": grid_counts,
        "total_runs_upper_bound": sum(grid_counts.values()),
        "total_rows_in_raw_csv_now": n_rows_now,
    }
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    with open(MANIFEST_JSON, "w") as fh:
        json.dump(manifest, fh, indent=2)
    print(f"[run] wrote {MANIFEST_JSON}")


# ===========================================================================
# --summarize : build summary.csv from raw.csv (mean, SD, 95% CI per metric,
# grouped by experiment + every config field except seed + policy)
# ===========================================================================

def summarize():
    if not RAW_CSV.exists():
        print(f"{RAW_CSV} not found; nothing to summarize.")
        return

    with open(RAW_CSV, newline="") as f:
        rows = list(csv.DictReader(f))
    if not rows:
        print(f"{RAW_CSV} is empty; nothing to summarize.")
        return

    group_cols = [c for c in IDENTITY_COLUMNS if c != "seed"]
    groups: dict = {}
    for row in rows:
        groups.setdefault(tuple(row[c] for c in group_cols), []).append(row)

    out_rows = []
    for gkey, grows in groups.items():
        base = dict(zip(group_cols, gkey))
        base["n"] = len(grows)
        for metric in METRIC_COLUMNS:
            vals = []
            for r in grows:
                v = r.get(metric, "")
                if v == "":
                    continue
                vals.append(float(v))
            if not vals:
                base[f"{metric}_mean"] = ""
                base[f"{metric}_sd"] = ""
                base[f"{metric}_ci95_lo"] = ""
                base[f"{metric}_ci95_hi"] = ""
                continue
            m = statistics.mean(vals)
            sd = statistics.stdev(vals) if len(vals) > 1 else 0.0
            df = len(vals) - 1
            if df > 0:
                tcrit = t_critical(df)
                half = tcrit * sd / math.sqrt(len(vals))
                lo, hi = m - half, m + half
            else:
                lo, hi = m, m
            base[f"{metric}_mean"] = round(m, 6)
            base[f"{metric}_sd"] = round(sd, 6)
            base[f"{metric}_ci95_lo"] = round(lo, 6)
            base[f"{metric}_ci95_hi"] = round(hi, 6)
        out_rows.append(base)

    fieldnames = group_cols + ["n"]
    for metric in METRIC_COLUMNS:
        fieldnames += [f"{metric}_mean", f"{metric}_sd", f"{metric}_ci95_lo", f"{metric}_ci95_hi"]

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    with open(SUMMARY_CSV, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for r in out_rows:
            w.writerow(r)
    print(f"[summarize] wrote {len(out_rows)} summary rows to {SUMMARY_CSV}")


# ===========================================================================
# CLI
# ===========================================================================

def main():
    ap = argparse.ArgumentParser(description="Phase 2 Part B experiment harness")
    ap.add_argument("--plan", action="store_true", help="print run counts per experiment and exit")
    ap.add_argument("--smoke", action="store_true", help="run E1 with N=3 and print a summary; writes nothing")
    ap.add_argument("--run", action="store_true", help="run the (resumable) grid, appending to raw.csv")
    ap.add_argument("--summarize", action="store_true", help="build summary.csv from raw.csv")
    ap.add_argument("--workers", type=int, default=os.cpu_count() or 4)
    ap.add_argument("--experiments", type=str, default="",
                     help="comma-separated subset for --run, e.g. E0,E1 (default: all)")
    args = ap.parse_args()

    if args.plan:
        print_plan()
    elif args.smoke:
        run_smoke(args.workers)
    elif args.summarize:
        summarize()
    elif args.run:
        only = set(e.strip() for e in args.experiments.split(",") if e.strip()) or None
        run_grid(args.workers, only_experiments=only)
    else:
        ap.print_help()


if __name__ == "__main__":
    try:
        mp.set_start_method("spawn")
    except RuntimeError:
        pass  # already set
    main()
