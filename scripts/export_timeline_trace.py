#!/usr/bin/env python3
"""
Export a single-run execution trace for the paper's timeline figure.

Runs one OCC simulation (seed 1000, policy "random", corrected settings)
through the unmodified simulator.runner.run_simulation, with read-only
runtime instrumentation added here only:

  * a whole-run tracer (detailed=True) for every segment on every core,
    plus commit/abort/arrival/os_preempt markers;
  * a wrapper around transactions.transaction_occ that maps the running
    Process to its txn_id and core;
  * a DB subclass whose `versions` list logs every read (version snapshot),
    validation check and version bump, attributed to the active txn.

None of this changes simulation timing, RNG draws or control flow, so the
run's metrics match an uninstrumented run of the same config (the script
checks this when run with --verify).

It then searches for "witness" transactions T (arrived after warm-up) that
(a) were preempted during their OCC validation operation, (b) during that
preemption's penalty another transaction U committed and bumped a key in
T's read set, and (c) then aborted with abort_cause="version_conflict".
The first such T in arrival (txn_id) order whose U ran on a different core
is exported.

Usage:
    python scripts/export_timeline_trace.py [--verify]

Outputs (figures/timeline/):
    segments.csv  core_id, txn_id, kind (read/validation/penalty/idle),
                  start_ms, end_ms, clipped
    outcomes.csv  core_id, txn_id, time_ms, outcome, abort_cause
    witness.json  the chosen witness plus run-wide witness counts
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import defaultdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import simulator.runner as runner_mod  # noqa: E402
import simulator.transactions as txn_mod  # noqa: E402
from simulator.model import Config, DB  # noqa: E402
from simulator.runner import build_policy, run_simulation  # noqa: E402

OUT_DIR = REPO_ROOT / "figures" / "timeline"

CFG = Config(
    num_cores=8, db_size=2000, contention=0.913, ops_per_txn=8,
    mean_op_time=0.4, preempt_rate=0.14, preempt_penalty=2.0,
    arrival_rate=2.0, sim_time=15000.0, seed=1000,
    rng_mode="split", double_preempt="ignore", grace_clock="per_section",
    occ_validation_preempt="delay", warmup_ms=1000.0,
)
POLICY = "random"
PROTOCOL = "occ"
PRE_MS = 6.0    # window starts this long before T's first read
POST_MS = 3.0   # ... and ends this long after T's abort
N_OTHER_CORES = 2

KIND_MAP = {"cpu": "read", "validation": "validation", "preempt_penalty": "penalty"}


# ---------------------------------------------------------------------------
# Instrumentation
# ---------------------------------------------------------------------------

class Recorder:
    """Collects everything the figure needs. All times are raw sim-ms."""

    detailed = True  # tracer protocol: distinct validation kind + markers

    def __init__(self):
        self.env = None
        self.segments = []          # (core, txn, kind, start, end)
        self.markers = []           # (core, txn, event, t)
        self.proc_txn = {}          # Process -> txn_id
        self.txn = {}               # txn_id -> per-transaction record

    # ---- tracer protocol (called by simulator code) ----
    def seg(self, core_id, txn_id, kind, start, end, **extra):
        self.segments.append((core_id, txn_id, kind, start, end))

    def mark(self, core_id, txn_id, event, t):
        self.markers.append((core_id, txn_id, event, t))
        rec = self.txn.get(txn_id)
        if rec is None:
            return
        if event == "arrival":
            rec["arrival"] = t
        elif event in ("commit", "abort"):
            rec["outcome"] = event
            rec["outcome_time"] = t

    # ---- helpers for the versions list / keyset hooks ----
    def active_txn(self):
        proc = self.env.active_process if self.env is not None else None
        return self.proc_txn.get(proc)


REC = Recorder()


class LoggedVersions(list):
    """db.versions replacement. A transaction's first len(keyset) reads are
    its read-phase snapshots (one per key, in keyset order); later reads
    are validation checks (or the read half of `versions[key] += 1`);
    every write is a commit-time version bump."""

    def __getitem__(self, key):
        value = list.__getitem__(self, key)
        txn_id = REC.active_txn()
        if txn_id is not None:
            rec = REC.txn[txn_id]
            if len(rec["reads"]) < len(rec["keys"]):
                expected = rec["keys"][len(rec["reads"])]
                assert key == expected, (txn_id, key, expected)
                rec["reads"].append((key, REC.env.now, value))
            elif rec["outcome"] is None and not rec["bumps"]:
                rec["checks"].append((key, REC.env.now, value))
        return value

    def __setitem__(self, key, value):
        list.__setitem__(self, key, value)
        txn_id = REC.active_txn()
        if txn_id is not None:
            REC.txn[txn_id]["bumps"].append((key, REC.env.now, value))


class LoggedDB(DB):
    def __init__(self, env, cfg):
        super().__init__(env, cfg)
        self.versions = LoggedVersions(self.versions)
        REC.env = env

    def sample_keyset(self, k):
        keys = super().sample_keyset(k)
        txn_id = REC.active_txn()
        if txn_id is not None:
            REC.txn[txn_id]["keys"] = list(keys)
        return keys


_orig_transaction_occ = txn_mod.transaction_occ


def logged_transaction_occ(env, txn_id, db, core, cfg, stats, tracer=None):
    REC.proc_txn[env.active_process] = txn_id
    REC.txn[txn_id] = {
        "txn": txn_id, "core": core.id, "keys": [], "reads": [], "checks": [],
        "bumps": [], "arrival": None, "outcome": None, "outcome_time": None,
    }
    return (yield from _orig_transaction_occ(env, txn_id, db, core, cfg, stats, tracer))


def run_instrumented():
    orig_db = runner_mod.DB
    runner_mod.DB = LoggedDB
    txn_mod.transaction_occ = logged_transaction_occ
    try:
        metrics = run_simulation(PROTOCOL, build_policy(POLICY), CFG, tracer=REC)
    finally:
        runner_mod.DB = orig_db
        txn_mod.transaction_occ = _orig_transaction_occ
    return metrics


# ---------------------------------------------------------------------------
# Post-processing
# ---------------------------------------------------------------------------

def abort_cause(rec):
    """Recomputes the simulator's own abort decision from the logged reads
    and checks: version_conflict iff some checked key's version differs
    from the read-phase snapshot (the same loop transactions.py runs)."""
    if rec["outcome"] != "abort":
        return ""
    snap = {k: v for k, _, v in rec["reads"]}
    for k, _, v in rec["checks"]:
        if v != snap[k]:
            return "version_conflict"
    return "validation_preempted"


def conflict_key(rec):
    snap = {k: v for k, _, v in rec["reads"]}
    for k, _, v in rec["checks"]:
        if v != snap[k]:
            return k
    return None


def build_idle(segments_by_core, sim_time):
    idle = []
    for core, segs in segments_by_core.items():
        t = 0.0
        for _, _, _, s, e in sorted(segs, key=lambda x: (x[3], x[4])):
            if s > t + 1e-9:
                idle.append((core, "", "idle", t, s))
            t = max(t, e)
        if sim_time > t + 1e-9:
            idle.append((core, "", "idle", t, sim_time))
    return idle


def find_witnesses(recs, segs_by_txn, commits):
    """Returns every T satisfying (a)-(c), each with its qualifying
    (penalty, U, shared key) triples. `commits` is a time-sorted list of
    (t, txn_id, core) for committed transactions."""
    out = []
    for txn_id in sorted(recs):
        rec = recs[txn_id]
        if abort_cause(rec) != "version_conflict":
            continue                                           # (c)
        segs = sorted(segs_by_txn.get(txn_id, []), key=lambda x: x[3])
        val = [x for x in segs if x[2] == "validation"]
        if not val:
            continue
        val_start = val[0][3]
        penalties = [x for x in segs if x[2] == "preempt_penalty" and x[3] >= val_start - 1e-12]
        if not penalties:
            continue                                           # (a)
        read_time = {k: t for k, t, _ in rec["reads"]}
        hits = []
        for p in penalties:
            ps, pe = p[3], p[4]
            for t, u_id, u_core in commits:
                if t <= ps:
                    continue
                if t >= pe:
                    break
                if u_id == txn_id:
                    continue
                bumped = [k for k, _, _ in recs[u_id]["bumps"]]
                shared = [k for k in rec["keys"] if k in bumped and read_time[k] < t]
                if shared:                                     # (b)
                    hits.append({"penalty": p, "u": u_id, "u_core": u_core,
                                 "u_commit": t, "shared": shared})
        if hits:
            out.append({"t": txn_id, "val_start": val_start, "hits": hits})
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--verify", action="store_true",
                    help="also run the same config uninstrumented and check the metrics match")
    args = ap.parse_args()

    metrics = run_instrumented()
    recs = {k: r for k, r in REC.txn.items()}

    # Sanity: recomputed abort causes must match the simulator's counters
    # for the transactions it counted (arrival >= warmup).
    counted = [r for r in recs.values() if r["arrival"] is not None and r["arrival"] >= CFG.warmup_ms]
    vc = sum(1 for r in counted if abort_cause(r) == "version_conflict")
    vp = sum(1 for r in counted if abort_cause(r) == "validation_preempted")
    assert vc == metrics["aborts_version_conflict"], (vc, metrics["aborts_version_conflict"])
    assert vp == metrics["aborts_validation_preempted"], (vp, metrics["aborts_validation_preempted"])
    commits_counted = sum(1 for r in counted if r["outcome"] == "commit")
    assert commits_counted == metrics["committed"], (commits_counted, metrics["committed"])

    if args.verify:
        plain = run_simulation(PROTOCOL, build_policy(POLICY), CFG)
        diff = {k: (plain[k], metrics[k]) for k in plain if plain[k] != metrics[k]}
        print("verify: instrumented metrics == uninstrumented metrics:", not diff, diff or "")

    segs_by_txn = defaultdict(list)
    segs_by_core = defaultdict(list)
    for s in REC.segments:
        segs_by_txn[s[1]].append(s)
        segs_by_core[s[0]].append(s)
    commits = sorted((r["outcome_time"], r["txn"], r["core"]) for r in recs.values() if r["outcome"] == "commit")

    witnesses = find_witnesses(recs, segs_by_txn, commits)
    post_warmup = [w for w in witnesses if recs[w["t"]]["arrival"] >= CFG.warmup_ms]
    diff_core = [w for w in post_warmup if any(h["u_core"] != recs[w["t"]]["core"] for h in w["hits"])]
    if not diff_core:
        print("No witness transaction found.")
        return 1

    w = diff_core[0]
    T = recs[w["t"]]
    hit = next(h for h in w["hits"] if h["u_core"] != T["core"])
    U = recs[hit["u"]]
    shared_key = hit["shared"][0]
    t_read_shared = next(t for k, t, _ in T["reads"] if k == shared_key)
    pen = hit["penalty"]
    t_first_read = T["reads"][0][1]
    win = (t_first_read - PRE_MS, T["outcome_time"] + POST_MS)

    # Every other transaction that committed a bump to one of T's keys
    # between T's read of that key and T's validation check — i.e. every
    # writer that would have tripped T's version check.
    t_check = T["checks"][0][1] if T["checks"] else T["outcome_time"]
    read_time = {k: t for k, t, _ in T["reads"]}
    conflicting_writers = sorted({
        (r["outcome_time"], r["txn"], r["core"], k)
        for r in recs.values() if r["outcome"] == "commit" and r["txn"] != T["txn"]
        for k, bt, _ in r["bumps"] if k in read_time and read_time[k] < bt <= t_check
    })

    # Cores to export: T's, U's, and the N busiest others in the window.
    idle = build_idle(segs_by_core, CFG.sim_time)
    all_segs = REC.segments + idle

    def busy_in_window(core):
        tot = 0.0
        for c, _, kind, s, e in REC.segments:
            if c == core:
                tot += max(0.0, min(e, win[1]) - max(s, win[0]))
        return tot

    others = sorted((c for c in range(CFG.num_cores) if c not in (T["core"], U["core"])),
                    key=lambda c: (-busy_in_window(c), c))[:N_OTHER_CORES]
    cores = [T["core"], U["core"]] + others

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    seg_rows = []
    for c, txn, kind, s, e in all_segs:
        if c not in cores or e <= win[0] or s >= win[1]:
            continue
        cs, ce = max(s, win[0]), min(e, win[1])
        seg_rows.append({
            "core_id": c, "txn_id": txn, "kind": KIND_MAP.get(kind, kind),
            "start_ms": round(cs, 6), "end_ms": round(ce, 6),
            "clipped": int(cs != s or ce != e),
        })
    seg_rows.sort(key=lambda r: (cores.index(r["core_id"]), r["start_ms"], r["end_ms"]))
    with open(OUT_DIR / "segments.csv", "w", newline="") as f:
        wr = csv.DictWriter(f, fieldnames=["core_id", "txn_id", "kind", "start_ms", "end_ms", "clipped"])
        wr.writeheader()
        wr.writerows(seg_rows)

    out_rows = []
    for r in recs.values():
        if r["outcome"] and r["core"] in cores and win[0] <= r["outcome_time"] <= win[1]:
            out_rows.append({"core_id": r["core"], "txn_id": r["txn"],
                             "time_ms": round(r["outcome_time"], 6), "outcome": r["outcome"],
                             "abort_cause": abort_cause(r)})
    out_rows.sort(key=lambda r: (r["time_ms"], r["txn_id"]))
    with open(OUT_DIR / "outcomes.csv", "w", newline="") as f:
        wr = csv.DictWriter(f, fieldnames=["core_id", "txn_id", "time_ms", "outcome", "abort_cause"])
        wr.writeheader()
        wr.writerows(out_rows)

    preempts_T = sorted(t for c, txn, ev, t in REC.markers if txn == T["txn"] and ev == "os_preempt")
    witness = {
        "config": {**CFG.__dict__, "protocol": PROTOCOL, "policy": POLICY},
        "T": {
            "txn_id": T["txn"], "core_id": T["core"], "arrival_ms": T["arrival"],
            "keyset": T["keys"],
            "reads": [{"key": k, "time_ms": t, "version": v} for k, t, v in T["reads"]],
            "first_read_ms": t_first_read,
            "validation_start_ms": w["val_start"],
            "preemption_time_ms": pen[3],
            "penalty_end_ms": pen[4],
            "penalty_duration_ms": pen[4] - pen[3],
            "os_preempt_markers_ms": preempts_T,
            "version_check_ms": t_check,
            "abort_time_ms": T["outcome_time"],
            "abort_cause": abort_cause(T),
            "conflict_key_detected_by_check": conflict_key(T),
        },
        "U": {
            "txn_id": U["txn"], "core_id": U["core"], "arrival_ms": U["arrival"],
            "commit_time_ms": hit["u_commit"],
            "keyset": U["keys"],
        },
        "shared_key": shared_key,
        "shared_keys_all": hit["shared"],
        "T_read_time_of_shared_key_ms": t_read_shared,
        "U_commit_time_ms": hit["u_commit"],
        "all_conflicting_writers": [
            {"commit_time_ms": t, "txn_id": x, "core_id": c, "key": k}
            for t, x, c, k in conflicting_writers
        ],
        "window_ms": list(win),
        "exported_cores": cores,
        "witness_counts": {
            "whole_run": len(witnesses),
            "after_warmup": len(post_warmup),
            "after_warmup_with_U_on_other_core": len(diff_core),
        },
        "run_metrics": metrics,
    }
    with open(OUT_DIR / "witness.json", "w") as f:
        json.dump(witness, f, indent=2)

    print(json.dumps({k: witness[k] for k in (
        "shared_key", "shared_keys_all", "T_read_time_of_shared_key_ms", "U_commit_time_ms",
        "window_ms", "exported_cores", "witness_counts")}, indent=2))
    print(json.dumps({"T": {k: v for k, v in witness["T"].items() if k not in ("reads",)},
                      "U": witness["U"], "all_conflicting_writers": witness["all_conflicting_writers"]},
                     indent=2))
    print(f"segments.csv rows: {len(seg_rows)}  outcomes.csv rows: {len(out_rows)}")
    print(f"wrote {OUT_DIR}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
