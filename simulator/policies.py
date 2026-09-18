"""
2. PLUGGABLE PREEMPTION POLICIES  <-- put your new algorithm here
======================================================================
"""

from __future__ import annotations
from .engine import Environment


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
