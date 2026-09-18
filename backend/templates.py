"""Starter templates for the in-browser algorithm editor (Phase 3/4 spec).
Each one is valid, sandbox-passing PreemptionPolicy source — kept here
rather than inline in a route so they're easy to review/extend."""

TEMPLATES = [
    {
        "key": "always_preempt",
        "title": "Always Preempt",
        "description": "Never defers — the OS preempts on every attempt, "
                        "regardless of what the transaction is doing.",
        "source": '''class AlwaysPreemptPolicy(PreemptionPolicy):
    name = "always_preempt"

    def should_defer(self, core, env):
        return False
''',
    },
    {
        "key": "never_preempt",
        "title": "Never Preempt",
        "description": "Defers every attempt — equivalent to the built-in "
                        "'none' baseline, written out explicitly.",
        "source": '''class NeverPreemptPolicy(PreemptionPolicy):
    name = "never_preempt"

    def should_defer(self, core, env):
        return True
''',
    },
    {
        "key": "critical_aware",
        "title": "Critical-Aware",
        "description": "Defers only while the core is in a DBMS critical "
                        "section (holding a lock, or inside OCC validation).",
        "source": '''class MyCriticalAwarePolicy(PreemptionPolicy):
    name = "my_critical_aware"

    def should_defer(self, core, env):
        if core.critical:
            return True
        return False
''',
    },
    {
        "key": "grace_period",
        "title": "Grace Period",
        "description": "Like critical-aware, but only defers for up to "
                        "max_grace_ms inside one critical section, then lets "
                        "the OS preempt anyway.",
        "source": '''class MyGracePeriodPolicy(PreemptionPolicy):
    name = "my_grace_period"

    def __init__(self, max_grace_ms=1.0):
        self.max_grace_ms = max_grace_ms

    def should_defer(self, core, env):
        if not core.critical:
            return False
        elapsed = env.now - core.critical_since
        return elapsed < self.max_grace_ms
''',
    },
    {
        "key": "blank",
        "title": "Blank Policy",
        "description": "Minimal skeleton to start from scratch.",
        "source": '''class MyPolicy(PreemptionPolicy):
    name = "my_policy"

    def should_defer(self, core, env):
        # Return True to DEFER (skip) this preemption attempt.
        # Return False to let the OS preempt now.
        return False
''',
    },
]
