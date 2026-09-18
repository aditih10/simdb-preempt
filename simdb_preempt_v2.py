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

NOTE: the simulator implementation now lives in the simulator/ package
(engine, policies, model, transactions, runner) so the FastAPI backend
(backend/) can import it directly. This file is a thin CLI entry point
kept for backwards compatibility — `python simdb_preempt_v2.py` still
runs the exact same experiment matrix and writes the same results.json.
"""

from simulator import main

if __name__ == "__main__":
    main()
