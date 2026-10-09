import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import backend.main as backend_main
from backend.main import app

REPO_ROOT = Path(__file__).resolve().parent.parent

client = TestClient(app)


def _restrict_to_expected_keys(actual, expected):
    """paper-experiments added new, additive metrics fields — compare only
    the keys results.json (frozen before those existed) actually has."""
    return {k: actual[k] for k in expected}


def test_health():
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_list_policies_includes_all_builtins():
    r = client.get("/api/policies")
    assert r.status_code == 200
    keys = {p["key"] for p in r.json()["builtin"]}
    assert keys == {"none", "random", "critical_aware", "grace_period",
                     "commit_proximity_k1", "commit_proximity_k2"}


def test_config_defaults_2pl_and_occ_differ():
    tpl = client.get("/api/config/defaults", params={"protocol": "2pl"}).json()
    occ = client.get("/api/config/defaults", params={"protocol": "occ"}).json()
    assert tpl["contention"] == 0.6
    assert occ["contention"] == 0.913


def test_config_defaults_invalid_protocol():
    r = client.get("/api/config/defaults", params={"protocol": "bogus"})
    assert r.status_code == 400


def test_simulate_valid_policy_returns_expected_metric_keys():
    r = client.post("/api/simulate", json={"protocol": "2pl", "policy": "critical_aware"})
    assert r.status_code == 200
    body = r.json()
    expected_keys = {
        "protocol", "policy", "attempted", "committed", "aborted",
        "abort_rate_pct", "throughput_tps", "mean_latency_ms",
        "p99_latency_ms", "cpu_ms_wasted_on_aborts", "preempt_events",
        "deferred_events",
    }
    assert expected_keys.issubset(body["metrics"].keys())
    assert body["metadata"]["simulator_version"]
    assert body["metadata"]["protocol"] == "2pl"


def test_simulate_matches_committed_results_for_default_config():
    with open(REPO_ROOT / "results.json") as f:
        expected = json.load(f)["tpl"]["matrix"]["none"]
    r = client.post("/api/simulate", json={"protocol": "2pl", "policy": "none"})
    assert r.status_code == 200
    assert _restrict_to_expected_keys(r.json()["metrics"], expected) == expected


def test_simulate_invalid_policy_name_rejected():
    r = client.post("/api/simulate", json={"protocol": "2pl", "policy": "does_not_exist"})
    assert r.status_code == 422


def test_simulate_custom_algorithm_not_subclassing_policy_rejected():
    r = client.post("/api/simulate", json={"protocol": "2pl", "policy": "none",
                                            "algorithm": "class X: pass"})
    assert r.status_code == 400
    assert r.json()["detail"]["type"] == "validation_error"


@pytest.mark.parametrize("field,value", [
    ("contention", 1.5),      # must be < 1.0
    ("contention", -0.1),     # must be >= 0.0
    ("num_cores", 0),         # must be >= 1
    ("sim_time", -100),       # must be > 0
    ("sim_time", "not-a-number"),
])
def test_simulate_malformed_config_rejected(field, value):
    r = client.post("/api/simulate", json={
        "protocol": "2pl", "policy": "none", "config": {field: value},
    })
    assert r.status_code == 422


def test_simulate_reproducible_with_same_seed():
    payload = {"protocol": "occ", "policy": "grace_period", "config": {"seed": 99, "sim_time": 3000}}
    r1 = client.post("/api/simulate", json=payload)
    r2 = client.post("/api/simulate", json=payload)
    assert r1.json()["metrics"] == r2.json()["metrics"]


def test_simulate_different_seeds_are_independent():
    base = {"protocol": "occ", "policy": "random", "config": {"sim_time": 3000}}
    r1 = client.post("/api/simulate", json={**base, "config": {**base["config"], "seed": 1}})
    r2 = client.post("/api/simulate", json={**base, "config": {**base["config"], "seed": 2}})
    m1, m2 = r1.json()["metrics"], r2.json()["metrics"]
    assert (m1["attempted"], m1["aborted"]) != (m2["attempted"], m2["aborted"])


def test_compare_matrix_matches_committed_results():
    with open(REPO_ROOT / "results.json") as f:
        expected = json.load(f)["occ"]["matrix"]
    r = client.post("/api/simulate/compare", json={
        "protocol": "occ",
        "policies": ["none", "random", "critical_aware", "grace_period"],
    })
    assert r.status_code == 200
    actual_matrix = r.json()["matrix"]
    restricted = {p: _restrict_to_expected_keys(row, expected[p]) for p, row in actual_matrix.items()}
    assert restricted == expected


def test_compare_with_custom_algorithm_alongside_baselines():
    src = (
        "class MyCriticalAware(PreemptionPolicy):\n"
        "    name = 'my_critical_aware'\n"
        "    def should_defer(self, core, env):\n"
        "        if core.critical:\n"
        "            return True\n"
        "        return False\n"
    )
    r = client.post("/api/simulate/compare", json={
        "protocol": "2pl", "policies": ["none", "random"], "algorithm": src,
    })
    assert r.status_code == 200, r.text
    matrix = r.json()["matrix"]
    assert set(matrix.keys()) == {"none", "random", "custom"}

    with open(REPO_ROOT / "results.json") as f:
        expected_critical_aware = json.load(f)["tpl"]["matrix"]["critical_aware"]
    for key in ("attempted", "committed", "aborted", "abort_rate_pct",
                "throughput_tps", "mean_latency_ms", "p99_latency_ms",
                "preempt_events", "deferred_events"):
        assert matrix["custom"][key] == expected_critical_aware[key], key


def test_compare_with_only_custom_algorithm_no_baselines():
    src = "class P(PreemptionPolicy):\n    def should_defer(self, core, env):\n        return False\n"
    r = client.post("/api/simulate/compare", json={"protocol": "2pl", "algorithm": src})
    assert r.status_code == 200, r.text
    assert list(r.json()["matrix"].keys()) == ["custom"]
    assert r.json()["layer_gap"] is None


def test_compare_no_policies_and_no_algorithm_rejected():
    r = client.post("/api/simulate/compare", json={"protocol": "2pl"})
    assert r.status_code == 422


def test_compare_layer_gap_present_when_baselines_included():
    r = client.post("/api/simulate/compare", json={
        "protocol": "2pl", "policies": ["none", "random", "critical_aware"],
    })
    assert r.status_code == 200
    assert r.json()["layer_gap"] is not None


def test_compare_layer_gap_metric_choice_is_protocol_specific():
    # 2PL: abort_rate_pct + p99_latency_ms, NOT throughput_tps (arrival-rate
    # bound, near-zero denominator makes the ratio noise-dominated — see
    # backend/main.py's comment on this).
    r_2pl = client.post("/api/simulate/compare", json={
        "protocol": "2pl", "policies": ["none", "random", "critical_aware"],
    })
    assert set(r_2pl.json()["layer_gap"].keys()) == {"abort_rate_pct", "p99_latency_ms"}

    # OCC: abort_rate_pct + throughput_tps, matching the original CLI script.
    r_occ = client.post("/api/simulate/compare", json={
        "protocol": "occ", "policies": ["none", "random", "critical_aware"],
    })
    assert set(r_occ.json()["layer_gap"].keys()) == {"abort_rate_pct", "throughput_tps"}


def test_compare_layer_gap_absent_without_baselines():
    r = client.post("/api/simulate/compare", json={
        "protocol": "2pl", "policies": ["critical_aware", "grace_period"],
    })
    assert r.status_code == 200
    assert r.json()["layer_gap"] is None


def test_compare_unknown_policy_rejected():
    r = client.post("/api/simulate/compare", json={
        "protocol": "2pl", "policies": ["none", "totally_made_up"],
    })
    assert r.status_code == 422


def test_compare_empty_policy_list_rejected():
    r = client.post("/api/simulate/compare", json={"protocol": "2pl", "policies": []})
    assert r.status_code == 422


# ---- Phase 3: sandboxed custom-algorithm execution via /api/simulate ----

def test_list_templates():
    r = client.get("/api/templates")
    assert r.status_code == 200
    keys = {t["key"] for t in r.json()["templates"]}
    assert keys == {"always_preempt", "never_preempt", "critical_aware", "grace_period", "blank"}


def test_simulate_custom_algorithm_valid_matches_builtin():
    src = (
        "class MyCriticalAware(PreemptionPolicy):\n"
        "    name = 'my_critical_aware'\n"
        "    def should_defer(self, core, env):\n"
        "        if core.critical:\n"
        "            return True\n"
        "        return False\n"
    )
    r = client.post("/api/simulate", json={
        "protocol": "2pl", "algorithm": src, "config": {"sim_time": 5000, "seed": 42},
    })
    assert r.status_code == 200, r.text
    custom = r.json()["metrics"]

    baseline = client.post("/api/simulate", json={
        "protocol": "2pl", "policy": "critical_aware", "config": {"sim_time": 5000, "seed": 42},
    }).json()["metrics"]

    for key in ("attempted", "committed", "aborted", "abort_rate_pct", "p99_latency_ms"):
        assert custom[key] == baseline[key], key


def test_simulate_custom_algorithm_syntax_error():
    r = client.post("/api/simulate", json={
        "protocol": "2pl", "algorithm": "class P(PreemptionPolicy)\n    pass\n",
    })
    assert r.status_code == 400
    assert r.json()["detail"]["type"] == "validation_error"


def test_simulate_custom_algorithm_disallowed_import():
    r = client.post("/api/simulate", json={
        "protocol": "2pl",
        "algorithm": "import os\nclass P(PreemptionPolicy):\n    def should_defer(self, core, env):\n        return False\n",
    })
    assert r.status_code == 400
    assert r.json()["detail"]["type"] == "validation_error"
    assert "import" in r.json()["detail"]["message"]


def test_simulate_custom_algorithm_sandbox_escape_rejected():
    r = client.post("/api/simulate", json={
        "protocol": "2pl",
        "algorithm": (
            "class P(PreemptionPolicy):\n"
            "    def should_defer(self, core, env):\n"
            "        return ().__class__.__bases__[0].__subclasses__()\n"
        ),
    })
    assert r.status_code == 400
    assert r.json()["detail"]["type"] == "validation_error"


def test_simulate_custom_algorithm_empty_source_rejected():
    r = client.post("/api/simulate", json={"protocol": "2pl", "algorithm": "   "})
    assert r.status_code == 422


def test_simulate_custom_algorithm_timeout(monkeypatch):
    monkeypatch.setattr(backend_main, "SANDBOX_TIMEOUT_SECONDS", 1.0)
    src = (
        "class Loop(PreemptionPolicy):\n"
        "    name = 'loop'\n"
        "    def should_defer(self, core, env):\n"
        "        x = 0\n"
        "        while True:\n"
        "            x += 1\n"
        "        return True\n"
    )
    r = client.post("/api/simulate", json={"protocol": "2pl", "algorithm": src})
    assert r.status_code == 408
    assert r.json()["detail"]["type"] == "timeout"


# ---- Phase 5: contention sweep for "metric vs contention" charts ----

def test_sweep_basic_shape_and_reproducibility():
    payload = {
        "protocol": "2pl",
        "policies": ["none", "critical_aware"],
        "contentions": [0.3, 0.6],
        "config": {"sim_time": 3000},
    }
    r1 = client.post("/api/simulate/sweep", json=payload)
    assert r1.status_code == 200, r1.text
    body = r1.json()
    assert body["contentions"] == [0.3, 0.6]
    assert set(body["series"].keys()) == {"none", "critical_aware"}
    assert len(body["series"]["none"]) == 2
    assert len(body["series"]["critical_aware"]) == 2

    r2 = client.post("/api/simulate/sweep", json=payload)
    assert r2.json()["series"] == body["series"]


def test_sweep_point_matches_direct_simulate_at_same_contention():
    r = client.post("/api/simulate/sweep", json={
        "protocol": "2pl", "policies": ["none"], "contentions": [0.4, 0.7],
        "config": {"sim_time": 2000},
    })
    assert r.status_code == 200
    point = r.json()["series"]["none"][0]  # contention=0.4

    direct = client.post("/api/simulate", json={
        "protocol": "2pl", "policy": "none", "config": {"sim_time": 2000, "contention": 0.4},
    }).json()["metrics"]
    assert point == direct


def test_sweep_with_custom_algorithm():
    src = "class P(PreemptionPolicy):\n    def should_defer(self, core, env):\n        return core.critical\n"
    r = client.post("/api/simulate/sweep", json={
        "protocol": "2pl", "policies": ["none"], "algorithm": src,
        "contentions": [0.3, 0.5], "config": {"sim_time": 1500},
    })
    assert r.status_code == 200, r.text
    assert len(r.json()["series"]["custom"]) == 2


def test_sweep_rejects_too_few_or_too_many_points():
    base = {"protocol": "2pl", "policies": ["none"], "config": {"sim_time": 500}}
    assert client.post("/api/simulate/sweep", json={**base, "contentions": [0.5]}).status_code == 422
    assert client.post("/api/simulate/sweep", json={**base, "contentions": [round(x * 0.05, 2) for x in range(11)]}).status_code == 422


def test_sweep_rejects_out_of_range_contention():
    r = client.post("/api/simulate/sweep", json={
        "protocol": "2pl", "policies": ["none"], "contentions": [0.5, 1.2], "config": {"sim_time": 500},
    })
    assert r.status_code == 422


# ---- Phase 6: event trace / timeline ----

def test_trace_basic_shape():
    r = client.post("/api/simulate/trace", json={
        "protocol": "2pl", "policies": ["critical_aware"],
        "window_start": 1000, "window_size": 150,
    })
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["window"] == [1000, 1150]
    assert body["core_id"] == 0
    trace = body["traces"]["critical_aware"]
    assert "segments" in trace and "markers" in trace and "transactions" in trace and "metrics" in trace
    assert len(trace["segments"]) > 0


def test_trace_matches_committed_2pl_traces_exactly():
    """The default window/policies here reproduce exactly what
    simulator/runner.py main() traces into results.json for 2PL — since
    detailed=True only ADDS new marker/segment kinds, the pre-existing
    cpu/lock_wait/preempt_penalty segments and commit/abort markers must
    still match byte-for-byte."""
    import json as _json
    from pathlib import Path
    with open(Path(__file__).resolve().parent.parent / "results.json") as f:
        expected = _json.load(f)["traces"]

    r = client.post("/api/simulate/trace", json={
        "protocol": "2pl", "policies": ["random", "critical_aware"],
        "window_start": 1000, "window_size": 150,
    })
    assert r.status_code == 200, r.text
    traces = r.json()["traces"]

    for key, expected_key in [("random", "tpl_random"), ("critical_aware", "tpl_critical_aware")]:
        # detailed=True only ADDS new marker types (arrival/os_defer/
        # os_preempt) for 2PL — segments and the commit/abort markers must
        # be byte-for-byte identical to what the CLI wrote to results.json.
        assert traces[key]["segments"] == expected[expected_key]["segments"]
        commit_abort_markers = [m for m in traces[key]["markers"] if m["event"] in ("commit", "abort")]
        assert commit_abort_markers == expected[expected_key]["markers"]


def test_trace_occ_has_validation_segment_kind():
    r = client.post("/api/simulate/trace", json={
        "protocol": "occ", "policies": ["none"],
        "window_start": 0, "window_size": 200,
        "config": {"contention": 0.9},
    })
    assert r.status_code == 200, r.text
    kinds = {s["kind"] for s in r.json()["traces"]["none"]["segments"]}
    assert "validation" in kinds


def test_trace_with_custom_algorithm():
    src = (
        "class P(PreemptionPolicy):\n"
        "    name = 'p'\n"
        "    def should_defer(self, core, env):\n"
        "        return core.critical\n"
    )
    r = client.post("/api/simulate/trace", json={
        "protocol": "2pl", "policies": ["none"], "algorithm": src,
        "window_start": 0, "window_size": 150,
    })
    assert r.status_code == 200, r.text
    assert "custom" in r.json()["traces"]
    assert len(r.json()["traces"]["custom"]["transactions"]) >= 0


def test_trace_core_id_out_of_range_rejected():
    r = client.post("/api/simulate/trace", json={
        "protocol": "2pl", "policies": ["none"], "core_id": 999,
    })
    assert r.status_code == 400


def test_trace_window_size_too_large_rejected():
    r = client.post("/api/simulate/trace", json={
        "protocol": "2pl", "policies": ["none"], "window_size": 5000,
    })
    assert r.status_code == 422


def test_trace_no_policies_and_no_algorithm_rejected():
    r = client.post("/api/simulate/trace", json={"protocol": "2pl"})
    assert r.status_code == 422
