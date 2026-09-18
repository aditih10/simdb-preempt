"""Unit tests for the Phase 6 Tracer(detailed=True) instrumentation added
to Core/transactions.py — verifies the new event types appear ONLY when
detailed=True, which is what keeps the CLI's results.json byte-identical
(see tests/test_simulator.py's regression tests for that guarantee)."""

from simulator import Config, build_policy, run_simulation, Tracer


def _event_types(markers):
    return {m["event"] for m in markers}


def _segment_kinds(segments):
    return {s["kind"] for s in segments}


def test_non_detailed_tracer_has_no_new_event_types():
    cfg = Config(sim_time=3000, seed=7)
    tracer = Tracer(window=(0, 3000), core_id=0, detailed=False)
    run_simulation("2pl", build_policy("random"), cfg, tracer)
    events = _event_types(tracer.markers)
    assert "arrival" not in events
    assert "os_defer" not in events
    assert "os_preempt" not in events
    assert events <= {"commit", "abort"}


def test_detailed_tracer_2pl_records_arrivals_and_os_decisions():
    cfg = Config(sim_time=3000, seed=7)
    tracer = Tracer(window=(0, 3000), core_id=0, detailed=True)
    run_simulation("2pl", build_policy("random"), cfg, tracer)
    events = _event_types(tracer.markers)
    assert "arrival" in events
    # "random" never defers, so every attempt on a busy core preempts.
    assert "os_preempt" in events


def test_detailed_tracer_critical_aware_records_defers():
    cfg = Config(sim_time=3000, seed=7)
    tracer = Tracer(window=(0, 3000), core_id=0, detailed=True)
    run_simulation("2pl", build_policy("critical_aware"), cfg, tracer)
    assert "os_defer" in _event_types(tracer.markers)


def test_detailed_tracer_occ_has_distinct_validation_segments():
    cfg = Config(sim_time=3000, seed=7, contention=0.9)
    tracer = Tracer(window=(0, 3000), core_id=0, detailed=True)
    run_simulation("occ", build_policy("none"), cfg, tracer)
    assert "validation" in _segment_kinds(tracer.segments)


def test_non_detailed_tracer_occ_has_no_validation_segments():
    """Matches the original (pre-Phase-6) behavior exactly — OCC's
    validation phase was indistinguishable from a plain "cpu" segment."""
    cfg = Config(sim_time=3000, seed=7, contention=0.9)
    tracer = Tracer(window=(0, 3000), core_id=0, detailed=False)
    run_simulation("occ", build_policy("none"), cfg, tracer)
    assert "validation" not in _segment_kinds(tracer.segments)


def test_detailed_tracer_never_records_a_none_txn_marker():
    """Regression: a second preemption during OCC's own penalty-recovery
    wait can synchronously abort the transaction (Preempted propagates
    uncaught out of run_operation) and clear core.current_txn_id *before*
    Core._os_scheduler's raise_interrupt() call returns — so the
    subsequent marker() call must use the txn id captured before
    raise_interrupt(), not whatever current_txn_id is afterward. With
    OCC_CFG's real parameters this reproduces reliably at seed=42."""
    from simulator import OCC_CFG

    for policy_key in ("random", "critical_aware"):
        tracer = Tracer(window=(0, OCC_CFG.sim_time), core_id=0, detailed=True)
        run_simulation("occ", build_policy(policy_key), OCC_CFG, tracer)
        none_txn_markers = [m for m in tracer.markers if m["txn"] is None]
        assert none_txn_markers == [], (policy_key, none_txn_markers)


def test_trace_summary_matches_builtin_output_shape():
    from backend.trace_summary import summarize_transactions

    cfg = Config(sim_time=2000, seed=3)
    tracer = Tracer(window=(0, 2000), core_id=0, detailed=True)
    run_simulation("2pl", build_policy("critical_aware"), cfg, tracer)
    summary = summarize_transactions(tracer.segments, tracer.markers, "2pl")
    assert len(summary) > 0
    row = summary[0]
    assert set(row.keys()) == {
        "txn", "arrival", "status", "end_time", "cpu_ms", "wait_ms",
        "preempt_count", "critical_ms",
    }
    assert row["status"] in ("commit", "abort", "in_flight")
