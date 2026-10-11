"""
5. DRIVER  /  6. EXPERIMENT MATRIX
======================================================================
"""

from __future__ import annotations
import json
import random

from .engine import Environment
from .model import Config, Core, DB, RngPool, Stats, Tracer
from .policies import PreemptionPolicy, BUILTIN_POLICIES
from .transactions import arrival_process


def run_simulation(protocol: str, policy: PreemptionPolicy, cfg: Config, tracer=None):
    random.seed(cfg.seed)
    env = Environment()
    env.rng = RngPool(cfg)
    db = DB(env, cfg)
    stats = Stats()
    cores = [Core(env, cfg, policy, i, tracer=tracer) for i in range(cfg.num_cores)]
    env.process(arrival_process(env, db, cores, cfg, stats, protocol, tracer))
    env.run(until=cfg.sim_time)
    mean_lat, p99_lat = stats.latency_stats()
    mean_res_lat, p99_res_lat = stats.resolved_latency_stats()
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
        # ---- new, additive fields (paper-experiments) ----
        "abort_rate_resolved_pct": round(100 * stats.abort_rate_resolved(), 2),
        "mean_latency_resolved_ms": round(mean_res_lat, 3),
        "p99_latency_resolved_ms": round(p99_res_lat, 3),
        "preempt_attempts_while_descheduled": stats.preempt_attempts_while_descheduled,
        "aborts_validation_preempted": stats.aborts_validation_preempted,
        "aborts_version_conflict": stats.aborts_version_conflict,
        "aborts_lock_timeout": stats.aborts_lock_timeout,
        "preempts_occ_read": stats.preempts_occ_read,
        "preempts_occ_validation": stats.preempts_occ_validation,
        "preempts_2pl_lock_held": stats.preempts_2pl_lock_held,
        "preempts_2pl_no_lock": stats.preempts_2pl_no_lock,
        "txns_preempted_in_validation": stats.txns_preempted_in_validation,
        "txns_preempted_in_validation_committed": stats.txns_preempted_in_validation_committed,
        "txns_preempted_in_validation_aborted": stats.txns_preempted_in_validation_aborted,
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
