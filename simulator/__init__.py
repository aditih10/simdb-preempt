"""
SimDB-Preempt simulator package — a sandbox for testing OS scheduling
algorithms against the OS-DBMS "layer gap" WITHOUT touching a kernel.

Split out of the original single-file simdb_preempt_v2.py so the FastAPI
backend (see backend/) can import the simulator directly instead of
shelling out to a script. Behavior is unchanged from the original file;
`python simdb_preempt_v2.py` still runs the same matrix and writes the
same results.json.
"""

__version__ = "2.0.0"

from .engine import Environment, Process, Lock, Preempted
from .model import Config, DB, Core, Stats, Tracer, percentile
from .policies import (
    PreemptionPolicy,
    NoPreemptPolicy,
    RandomPolicy,
    CriticalAwarePolicy,
    GracePeriodPolicy,
    BUILTIN_POLICIES,
)
from .transactions import run_operation, transaction_2pl, transaction_occ, arrival_process
from .runner import (
    run_simulation,
    run_matrix,
    run_contention_sweep,
    layer_gap_score,
    build_policy,
    TPL_CFG,
    OCC_CFG,
    POLICIES_TO_TEST,
    main,
)

__all__ = [
    "__version__",
    "Environment", "Process", "Lock", "Preempted",
    "Config", "DB", "Core", "Stats", "Tracer", "percentile",
    "PreemptionPolicy", "NoPreemptPolicy", "RandomPolicy",
    "CriticalAwarePolicy", "GracePeriodPolicy", "BUILTIN_POLICIES",
    "run_operation", "transaction_2pl", "transaction_occ", "arrival_process",
    "run_simulation", "run_matrix", "run_contention_sweep", "layer_gap_score",
    "build_policy", "TPL_CFG", "OCC_CFG", "POLICIES_TO_TEST", "main",
]
