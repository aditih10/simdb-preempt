"""
Entry point for the isolated child process that actually runs a
user-submitted policy. Invoked as:

    python -m simulator.sandbox_runner

It reads one JSON object from stdin: {"source": str, "protocol": "2pl"|"occ",
"config": {...Config fields...}, "trace_window": [start, stop]?, "core_id": int?},
and writes exactly one JSON object to stdout: either
{"metrics": {...}, "segments"?: [...], "markers"?: [...]} on success (the
trace fields are only present when "trace_window" was requested) or
{"error": {...}} on failure. The parent process (backend/main.py) enforces
the wall-clock timeout via subprocess.run(timeout=...) and never inspects
or trusts anything this process does beyond that one JSON line — this
process gets no arguments beyond the JSON on stdin and is expected to open
no files, sockets, or subprocesses of its own.
"""

from __future__ import annotations
import json
import sys
import traceback

from .model import Config, Tracer
from .policies import PreemptionPolicy
from .runner import run_simulation
from .sandbox import PolicyValidationError, SAFE_BUILTINS, validate_policy_source


def _error(error_type: str, title: str, message: str, line=None) -> dict:
    return {"error": {"type": error_type, "title": title, "message": message, "line": line}}


def run(payload: dict) -> dict:
    source = payload.get("source", "")
    protocol = payload.get("protocol", "2pl")
    config_fields = payload.get("config", {})

    try:
        class_name = validate_policy_source(source)
    except PolicyValidationError as e:
        return _error("validation_error", "Algorithm Error", e.message, e.line)

    namespace = {"__builtins__": SAFE_BUILTINS, "PreemptionPolicy": PreemptionPolicy}
    try:
        exec(compile(source, "<policy>", "exec"), namespace)
    except Exception as e:
        return _error("runtime_error", "Algorithm Error",
                       f"error defining policy: {e}", getattr(e, "lineno", None))

    policy_cls = namespace[class_name]
    try:
        policy = policy_cls()
    except Exception as e:
        return _error("runtime_error", "Algorithm Error",
                       f"could not construct {class_name}(): {e}")

    if not hasattr(policy, "name") or not isinstance(policy.name, str) or not policy.name:
        policy.name = class_name  # tolerate a policy that forgot to set `name`
    if not hasattr(policy, "fires"):
        policy.fires = True

    try:
        cfg = Config(**config_fields)
    except TypeError as e:
        return _error("validation_error", "Invalid Configuration", str(e))

    trace_window = payload.get("trace_window")
    tracer = Tracer(window=tuple(trace_window), core_id=payload.get("core_id", 0), detailed=True) \
        if trace_window is not None else None

    try:
        metrics = run_simulation(protocol, policy, cfg, tracer)
    except AttributeError as e:
        return _error(
            "unsupported_api", "Unsupported API",
            f"your policy accessed something not on the documented simulation "
            f"state: {e}",
        )
    except RecursionError:
        return _error(
            "runtime_error", "Algorithm Error",
            "your policy recursed too deeply (infinite recursion?)",
        )
    except Exception as e:
        return _error("runtime_error", "Algorithm Error", f"{type(e).__name__}: {e}")

    result = {"metrics": metrics}
    if tracer is not None:
        result["segments"] = tracer.segments
        result["markers"] = tracer.markers
    return result


def main():
    try:
        payload = json.loads(sys.stdin.read())
    except json.JSONDecodeError as e:
        print(json.dumps(_error("bad_request", "Bad Request", f"invalid JSON payload: {e}")))
        return

    try:
        result = run(payload)
    except Exception:
        # Absolute last resort — never let this process crash without
        # emitting a JSON line, or the parent's subprocess.run() call
        # would see a non-JSON stdout and have to guess what happened.
        result = _error("runtime_error", "Algorithm Error",
                         f"unexpected sandbox failure: {traceback.format_exc(limit=3)}")

    print(json.dumps(result))


if __name__ == "__main__":
    main()
