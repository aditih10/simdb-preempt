"""
4. TRANSACTIONS
======================================================================
"""

from __future__ import annotations
import random

from .engine import Environment, Preempted
from .model import Config, Core, DB, Stats


def run_operation(env: Environment, core: Core, cfg: Config, txn_id, tracer=None, segment_kind="cpu"):
    remaining = random.expovariate(1.0 / cfg.mean_op_time)
    extra_delay = 0.0
    preempted = False
    while remaining > 1e-9:
        start = env.now
        try:
            yield env.timeout(remaining)
            if tracer:
                tracer.seg(core.id, txn_id, segment_kind, start, env.now)
            remaining = 0.0
        except Preempted:
            preempted = True
            elapsed = env.now - start
            if tracer:
                tracer.seg(core.id, txn_id, segment_kind, start, env.now)
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
    core.current_txn_id = txn_id
    if tracer and tracer.detailed:
        tracer.mark(core.id, txn_id, "arrival", arrival)

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
    core.current_txn_id = None
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
    core.current_txn_id = txn_id
    if tracer and tracer.detailed:
        tracer.mark(core.id, txn_id, "arrival", arrival)
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
    validation_kind = "validation" if (tracer and tracer.detailed) else "cpu"
    try:
        extra_delay, was_preempted = yield from run_operation(
            env, core, cfg, txn_id, tracer, segment_kind=validation_kind)
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
    core.current_txn_id = None
    core.resource.release()


def arrival_process(env, db, core_pool, cfg, stats, protocol, tracer):
    txn_id = 0
    while True:
        yield env.timeout(random.expovariate(cfg.arrival_rate))
        txn_id += 1
        core = core_pool[txn_id % len(core_pool)]
        fn = transaction_2pl if protocol == "2pl" else transaction_occ
        env.process(fn(env, txn_id, db, core, cfg, stats, tracer))
