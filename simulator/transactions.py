"""
4. TRANSACTIONS
======================================================================
"""

from __future__ import annotations

from .engine import Environment, Preempted
from .model import Config, Core, DB, Stats


def run_operation(env: Environment, core: Core, cfg: Config, txn_id, tracer=None, segment_kind="cpu", stats=None):
    remaining = env.rng.optime.expovariate(1.0 / cfg.mean_op_time)
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
            penalty = env.rng.preempt(core.id).expovariate(1.0 / cfg.preempt_penalty)
            extra_delay += penalty
            pstart = env.now
            # double_preempt="legacy" (default): the loop below runs exactly
            # once — `yield env.timeout(penalty_left)` with penalty_left ==
            # penalty, identical to the original unwrapped yield. On a clean
            # finish it `break`s immediately (same as falling through to the
            # tracer.seg call below); on a second Preempted it re-raises
            # immediately (mode != "ignore"), propagating out of
            # run_operation exactly as the original code did. Byte-identical.
            #
            # double_preempt="ignore": a descheduled thread (one already
            # waiting out its penalty) cannot be preempted again — a second
            # interrupt during this wait is discarded and counted, and the
            # penalty clock resumes for whatever time is left, looping for
            # as many further interrupts as land on it. Preempted therefore
            # never escapes run_operation in this mode.
            penalty_left = penalty
            while True:
                p_start = env.now
                try:
                    yield env.timeout(penalty_left)
                    break
                except Preempted:
                    if cfg.double_preempt != "ignore":
                        raise
                    if stats is not None:
                        stats.preempt_attempts_while_descheduled += 1
                    penalty_left -= (env.now - p_start)
                    if penalty_left <= 1e-9:
                        break
            if tracer:
                tracer.seg(core.id, txn_id, "preempt_penalty", pstart, env.now)
    return extra_delay, preempted


def transaction_2pl(env, txn_id, db: DB, core: Core, cfg: Config, stats: Stats, tracer=None):
    arrival = env.now
    # E: warmup_ms=0.0 (default) makes count_stats True for every arrival
    # (env.now is never negative), so this is a no-op at the default.
    count_stats = arrival >= cfg.warmup_ms
    if count_stats:
        stats.attempted += 1
    keys = db.sample_keyset(env.rng.keyset.randint(2, cfg.ops_per_txn))
    held = []
    cpu_time_spent = 0.0
    aborted = False
    abort_cause = None

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
            abort_cause = "lock_timeout"
            break
        held.append(key)
        core.enter_critical()
        try:
            extra_delay, _ = yield from run_operation(
                env, core, cfg, txn_id, tracer, stats=(stats if count_stats else None))
            cpu_time_spent += cfg.mean_op_time + extra_delay
        except Preempted:
            pass

    if not aborted:
        if count_stats:
            stats.committed += 1
            stats.committed_latencies.append(env.now - arrival)
            stats.resolved_latencies.append(env.now - arrival)
        if tracer:
            tracer.mark(core.id, txn_id, "commit", env.now)
    else:
        if count_stats:
            stats.aborted += 1
            stats.cpu_ms_wasted_on_aborts += cpu_time_spent
            stats.resolved_latencies.append(env.now - arrival)
            if abort_cause == "lock_timeout":
                stats.aborts_lock_timeout += 1
        if tracer:
            tracer.mark(core.id, txn_id, "abort", env.now)

    for key in held:
        db.locks[key].release()
    core.exit_critical()
    core.current_proc = None
    core.current_txn_id = None
    core.resource.release()


def transaction_occ(env, txn_id, db: DB, core: Core, cfg: Config, stats: Stats, tracer=None):
    arrival = env.now
    count_stats = arrival >= cfg.warmup_ms
    if count_stats:
        stats.attempted += 1
    keys = db.sample_keyset(env.rng.keyset.randint(2, cfg.ops_per_txn))
    read_versions = {}
    cpu_time_spent = 0.0
    aborted = False
    abort_cause = None

    yield core.resource.request()
    core.current_proc = env.active_process
    core.current_txn_id = txn_id
    if tracer and tracer.detailed:
        tracer.mark(core.id, txn_id, "arrival", arrival)
    core.exit_critical()  # read phase: safe to preempt

    for key in keys:
        read_versions[key] = db.versions[key]
        try:
            extra_delay, _ = yield from run_operation(
                env, core, cfg, txn_id, tracer, stats=(stats if count_stats else None))
            cpu_time_spent += cfg.mean_op_time + extra_delay
        except Preempted:
            pass

    core.enter_critical()  # validation + write phase: critical
    was_preempted = False
    validation_kind = "validation" if (tracer and tracer.detailed) else "cpu"
    try:
        extra_delay, was_preempted = yield from run_operation(
            env, core, cfg, txn_id, tracer, segment_kind=validation_kind,
            stats=(stats if count_stats else None))
        cpu_time_spent += cfg.mean_op_time + extra_delay
    except Preempted:
        was_preempted = True

    if was_preempted:
        aborted = True
        abort_cause = "validation_preempted"
    else:
        for key in keys:
            if db.versions[key] != read_versions[key]:
                aborted = True
                abort_cause = "version_conflict"
                break

    if not aborted:
        for key in keys:
            db.versions[key] += 1
        if count_stats:
            stats.committed += 1
            stats.committed_latencies.append(env.now - arrival)
            stats.resolved_latencies.append(env.now - arrival)
        if tracer:
            tracer.mark(core.id, txn_id, "commit", env.now)
    else:
        if count_stats:
            stats.aborted += 1
            stats.cpu_ms_wasted_on_aborts += cpu_time_spent
            stats.resolved_latencies.append(env.now - arrival)
            if abort_cause == "validation_preempted":
                stats.aborts_validation_preempted += 1
            elif abort_cause == "version_conflict":
                stats.aborts_version_conflict += 1
        if tracer:
            tracer.mark(core.id, txn_id, "abort", env.now)

    core.exit_critical()
    core.current_proc = None
    core.current_txn_id = None
    core.resource.release()


def arrival_process(env, db, core_pool, cfg, stats, protocol, tracer):
    txn_id = 0
    while True:
        yield env.timeout(env.rng.arrival.expovariate(cfg.arrival_rate))
        txn_id += 1
        core = core_pool[txn_id % len(core_pool)]
        fn = transaction_2pl if protocol == "2pl" else transaction_occ
        env.process(fn(env, txn_id, db, core, cfg, stats, tracer))
