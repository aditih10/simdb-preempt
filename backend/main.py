"""
FastAPI backend for SimDB-Preempt — a thin API layer around the existing
simulator (see simulator/). No simulation logic lives here; this module
only validates requests, resolves Config, calls into the simulator, and
serializes the result.

Concurrency note: simulator.model.DB / run_operation / the OS-scheduler
process all draw from the *global* `random` module rather than a
per-simulation Random instance (see simulator/model.py, transactions.py).
That is fine for the original single-process CLI tool, but if two
requests ran run_simulation() concurrently on separate threads they could
interleave calls into the shared `random` state and silently corrupt each
other's "deterministic" seeded run. Rather than rewire the simulator's
randomness plumbing under a research-integrity-sensitive change, we
serialize all simulation execution behind SIM_LOCK — simulations are
sub-second to low-single-digit seconds, so this costs nothing meaningful
in a research-prototype API and keeps every run exactly as reproducible
as the CLI version.
"""

from __future__ import annotations
import json
import os
import subprocess
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from simulator import (
    __version__ as SIMULATOR_VERSION,
    BUILTIN_POLICIES,
    Config,
    Tracer,
    build_policy,
    layer_gap_score,
    run_matrix,
    run_simulation,
    TPL_CFG,
    OCC_CFG,
)
from backend.schemas import CompareRequest, ConfigOverrides, SimulateRequest, SweepRequest, TraceRequest
from backend.templates import TEMPLATES
from backend.trace_summary import summarize_transactions

REPO_ROOT = Path(__file__).resolve().parent.parent
SANDBOX_TIMEOUT_SECONDS = 10.0
MAX_SANDBOX_OUTPUT_BYTES = 2_000_000

app = FastAPI(title="SimDB-Preempt API", version=SIMULATOR_VERSION)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

SIM_LOCK = threading.Lock()

DEFAULT_CFG = {"2pl": TPL_CFG, "occ": OCC_CFG}

POLICY_DESCRIPTIONS = {
    "none": "Upper-bound baseline: the OS never interrupts a running transaction.",
    "random": "Critical-section-blind preemption — fires on schedule regardless "
              "of DBMS state. This is the 'layer gap' made concrete.",
    "critical_aware": "Defers preemption while a core is in a DBMS critical "
                       "section (holding a lock, or inside OCC validation).",
    "grace_period": "Critical-aware, but caps the deferral at max_grace_ms to "
                     "bound starvation risk.",
}


def _resolve_config(protocol: str, overrides: ConfigOverrides) -> Config:
    base = DEFAULT_CFG[protocol]
    merged = {**base.__dict__, **overrides.overrides()}
    return Config(**merged)


def _metadata(protocol: str, cfg: Config, policies: list[str]) -> dict:
    return {
        "simulator_version": SIMULATOR_VERSION,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "protocol": protocol,
        "policies": policies,
        "config": cfg.__dict__,
    }


@app.get("/api/health")
def health():
    return {"status": "ok", "simulator_version": SIMULATOR_VERSION}


@app.get("/api/policies")
def list_policies():
    return {
        "builtin": [
            {"key": key, "name": cls.name, "description": POLICY_DESCRIPTIONS.get(key, "")}
            for key, cls in BUILTIN_POLICIES.items()
        ]
    }


@app.get("/api/templates")
def list_templates():
    return {"templates": TEMPLATES}


@app.get("/api/config/defaults")
def config_defaults(protocol: str = "2pl"):
    if protocol not in DEFAULT_CFG:
        raise HTTPException(status_code=400, detail=f"protocol must be '2pl' or 'occ', got '{protocol}'")
    return DEFAULT_CFG[protocol].__dict__


def _sandbox_env() -> dict:
    """A minimal environment for the child process — just enough for
    Windows to launch a Python interpreter, nothing inherited from the
    parent process that the sandboxed code has no business seeing."""
    keep = ("PATH", "SystemRoot", "TEMP", "TMP", "PATHEXT")
    return {k: os.environ[k] for k in keep if k in os.environ}


def _invoke_sandbox(payload: dict) -> dict:
    """Runs user-submitted policy source in an isolated child process
    (python -m simulator.sandbox_runner) and returns its parsed JSON result
    (at least a "metrics" key; "segments"/"markers" too if "trace_window"
    was in the payload). Raises HTTPException with a structured,
    UI-friendly error body (never a raw traceback) on any failure — see
    simulator/sandbox.py and simulator/sandbox_runner.py for what's
    actually enforced."""
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "simulator.sandbox_runner"],
            input=json.dumps(payload),
            capture_output=True,
            text=True,
            timeout=SANDBOX_TIMEOUT_SECONDS,
            cwd=str(REPO_ROOT),
            env=_sandbox_env(),
        )
    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=408, detail={
            "type": "timeout", "title": "Simulation Timeout",
            "message": "Your policy exceeded the allowed simulation budget "
                       f"({SANDBOX_TIMEOUT_SECONDS:.0f}s).",
        })

    stdout = proc.stdout or ""
    if len(stdout.encode("utf-8", errors="ignore")) > MAX_SANDBOX_OUTPUT_BYTES:
        raise HTTPException(status_code=400, detail={
            "type": "output_too_large", "title": "Algorithm Error",
            "message": "Your policy produced more output than allowed.",
        })

    try:
        result = json.loads(stdout)
    except json.JSONDecodeError:
        # Genuinely unexpected (e.g. the interpreter itself crashed) —
        # log stderr for diagnosis but never hand the raw traceback back.
        print(f"[sandbox] non-JSON output, exit={proc.returncode}, stderr={proc.stderr[:2000]}",
              file=sys.stderr)
        raise HTTPException(status_code=400, detail={
            "type": "runtime_error", "title": "Algorithm Error",
            "message": "Your policy could not be executed.",
        })

    if "error" in result:
        err = result["error"]
        status = 408 if err.get("type") == "timeout" else 400
        raise HTTPException(status_code=status, detail=err)

    return result


def _run_custom_policy(protocol: str, source: str, cfg: Config) -> dict:
    result = _invoke_sandbox({"protocol": protocol, "source": source, "config": cfg.__dict__})
    return result["metrics"]


def _run_custom_trace(protocol: str, source: str, cfg: Config, window: tuple, core_id: int) -> dict:
    result = _invoke_sandbox({
        "protocol": protocol, "source": source, "config": cfg.__dict__,
        "trace_window": list(window), "core_id": core_id,
    })
    return result


@app.post("/api/simulate")
def simulate(req: SimulateRequest):
    cfg = _resolve_config(req.protocol, req.config)

    if req.algorithm is not None:
        metrics = _run_custom_policy(req.protocol, req.algorithm, cfg)
        return {"metrics": metrics, "metadata": _metadata(req.protocol, cfg, [metrics.get("policy", "custom")])}

    policy = build_policy(req.policy)
    with SIM_LOCK:
        metrics = run_simulation(req.protocol, policy, cfg)
    return {"metrics": metrics, "metadata": _metadata(req.protocol, cfg, [req.policy])}


@app.post("/api/simulate/compare")
def simulate_compare(req: CompareRequest):
    cfg = _resolve_config(req.protocol, req.config)

    matrix = {}
    if req.policies:
        with SIM_LOCK:
            matrix = run_matrix(req.protocol, cfg, policies=req.policies)

    if req.algorithm is not None:
        # Runs in its own subprocess (see _run_custom_policy) — no SIM_LOCK
        # needed, it never touches the parent process's global random state.
        matrix["custom"] = _run_custom_policy(req.protocol, req.algorithm, cfg)

    layer_gap = None
    if "none" in req.policies and "random" in req.policies:
        # Metric choice per protocol matches the original CLI script's
        # judgment call (simulator/runner.py main()): 2PL's throughput is
        # arrival-rate-bound and barely moves between policies, so its
        # layer-gap ratio is dominated by run-to-run noise (can even swing
        # negative from a near-zero denominator) — not a meaningful signal.
        # OCC's abort rate directly reflects validation-phase preemption,
        # so it stays meaningful there; throughput is meaningful for OCC
        # because retries actually change committed throughput.
        if req.protocol == "2pl":
            layer_gap = {
                "abort_rate_pct": layer_gap_score(matrix, "abort_rate_pct", lower_is_better=True),
                "p99_latency_ms": layer_gap_score(matrix, "p99_latency_ms", lower_is_better=True),
            }
        else:
            layer_gap = {
                "abort_rate_pct": layer_gap_score(matrix, "abort_rate_pct", lower_is_better=True),
                "throughput_tps": layer_gap_score(matrix, "throughput_tps", lower_is_better=False),
            }

    return {
        "matrix": matrix,
        "layer_gap": layer_gap,
        "metadata": _metadata(req.protocol, cfg, list(matrix.keys())),
    }


@app.post("/api/simulate/sweep")
def simulate_sweep(req: SweepRequest):
    """Runs each requested policy at every contention value in
    req.contentions, holding every other config field fixed — powers the
    Phase 5 "metric vs contention" charts. Contention is the only swept
    parameter for now; Phase 7 generalizes to arbitrary parameters plus
    replications with confidence intervals."""
    base_cfg = _resolve_config(req.protocol, req.config)
    series: dict[str, list[dict]] = {key: [] for key in req.policies}
    if req.algorithm is not None:
        series["custom"] = []

    for c in req.contentions:
        point_cfg = Config(**{**base_cfg.__dict__, "contention": c})
        if req.policies:
            with SIM_LOCK:
                row = run_matrix(req.protocol, point_cfg, policies=req.policies)
            for key in req.policies:
                series[key].append(row[key])
        if req.algorithm is not None:
            series["custom"].append(_run_custom_policy(req.protocol, req.algorithm, point_cfg))

    return {
        "contentions": req.contentions,
        "series": series,
        "metadata": _metadata(req.protocol, base_cfg, list(series.keys())),
    }


@app.post("/api/simulate/trace")
def simulate_trace(req: TraceRequest):
    """Runs each requested policy once and captures a windowed event trace
    on one core, for the Phase 6 timeline + event log + "click a
    transaction" detail panel. See simulator/model.py's Tracer(detailed=True)
    and backend/trace_summary.py."""
    cfg = _resolve_config(req.protocol, req.config)
    if req.core_id >= cfg.num_cores:
        raise HTTPException(status_code=400, detail=(
            f"core_id {req.core_id} is out of range for num_cores={cfg.num_cores}"
        ))
    window = (req.window_start, req.window_start + req.window_size)

    traces: dict[str, dict] = {}
    if req.policies:
        with SIM_LOCK:
            for key in req.policies:
                tracer = Tracer(window=window, core_id=req.core_id, detailed=True)
                metrics = run_simulation(req.protocol, build_policy(key), cfg, tracer)
                traces[key] = {
                    "segments": tracer.segments,
                    "markers": tracer.markers,
                    "transactions": summarize_transactions(tracer.segments, tracer.markers, req.protocol),
                    "metrics": metrics,
                }

    if req.algorithm is not None:
        result = _run_custom_trace(req.protocol, req.algorithm, cfg, window, req.core_id)
        traces["custom"] = {
            "segments": result.get("segments", []),
            "markers": result.get("markers", []),
            "transactions": summarize_transactions(
                result.get("segments", []), result.get("markers", []), req.protocol,
            ),
            "metrics": result["metrics"],
        }

    return {
        "window": list(window),
        "core_id": req.core_id,
        "traces": traces,
        "metadata": _metadata(req.protocol, cfg, list(traces.keys())),
    }
