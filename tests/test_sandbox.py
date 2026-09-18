import pytest

from simulator.sandbox import PolicyValidationError, validate_policy_source
from simulator.sandbox_runner import run as sandbox_run
from backend.templates import TEMPLATES


@pytest.mark.parametrize("tmpl", TEMPLATES, ids=[t["key"] for t in TEMPLATES])
def test_all_shipped_templates_pass_validation(tmpl):
    class_name = validate_policy_source(tmpl["source"])
    assert class_name


def test_rejects_import():
    src = "import os\nclass P(PreemptionPolicy):\n    name='p'\n    def should_defer(self, core, env):\n        return False\n"
    with pytest.raises(PolicyValidationError, match="import"):
        validate_policy_source(src)


def test_rejects_dunder_attribute_escape():
    src = (
        "class P(PreemptionPolicy):\n"
        "    name = 'p'\n"
        "    def should_defer(self, core, env):\n"
        "        return ().__class__.__bases__\n"
    )
    with pytest.raises(PolicyValidationError, match="__"):
        validate_policy_source(src)


def test_rejects_dunder_name():
    src = (
        "class P(PreemptionPolicy):\n"
        "    name = 'p'\n"
        "    def should_defer(self, core, env):\n"
        "        return __builtins__\n"
    )
    with pytest.raises(PolicyValidationError):
        validate_policy_source(src)


def test_rejects_unsafe_bare_call():
    src = (
        "class P(PreemptionPolicy):\n"
        "    name = 'p'\n"
        "    def should_defer(self, core, env):\n"
        "        return eval('True')\n"
    )
    with pytest.raises(PolicyValidationError, match="eval"):
        validate_policy_source(src)


def test_rejects_no_policy_class():
    src = "x = 1\n"
    with pytest.raises(PolicyValidationError, match="PreemptionPolicy"):
        validate_policy_source(src)


def test_rejects_multiple_policy_classes():
    src = (
        "class A(PreemptionPolicy):\n"
        "    def should_defer(self, core, env):\n"
        "        return False\n"
        "class B(PreemptionPolicy):\n"
        "    def should_defer(self, core, env):\n"
        "        return True\n"
    )
    with pytest.raises(PolicyValidationError, match="2 classes"):
        validate_policy_source(src)


def test_rejects_missing_should_defer():
    src = "class P(PreemptionPolicy):\n    name = 'p'\n"
    with pytest.raises(PolicyValidationError, match="should_defer"):
        validate_policy_source(src)


def test_syntax_error_reports_line():
    src = "class P(PreemptionPolicy):\n    def should_defer(self, core, env)\n        return False\n"
    with pytest.raises(PolicyValidationError) as exc_info:
        validate_policy_source(src)
    assert exc_info.value.line == 2


def test_sandbox_runner_custom_policy_matches_builtin_critical_aware():
    """A custom policy with identical logic to the built-in critical_aware
    must produce byte-identical metrics — same DES core, same seed, so a
    fair, reproducible comparison holds regardless of where the policy
    code came from."""
    from simulator import Config, build_policy, run_simulation

    src = (
        "class MyCriticalAware(PreemptionPolicy):\n"
        "    name = 'my_critical_aware'\n"
        "    def should_defer(self, core, env):\n"
        "        if core.critical:\n"
        "            return True\n"
        "        return False\n"
    )
    cfg = Config(sim_time=5000, seed=42)
    result = sandbox_run({"protocol": "2pl", "source": src, "config": cfg.__dict__})
    assert "error" not in result

    baseline = run_simulation("2pl", build_policy("critical_aware"), cfg)
    custom = result["metrics"]
    for key in ("attempted", "committed", "aborted", "abort_rate_pct",
                "throughput_tps", "mean_latency_ms", "p99_latency_ms",
                "preempt_events", "deferred_events"):
        assert custom[key] == baseline[key], key


def test_sandbox_runner_reports_unsupported_api():
    src = (
        "class P(PreemptionPolicy):\n"
        "    name = 'p'\n"
        "    def should_defer(self, core, env):\n"
        "        return core.totally_undocumented_field\n"
    )
    result = sandbox_run({"protocol": "2pl", "source": src, "config": {"sim_time": 500}})
    assert result["error"]["type"] == "unsupported_api"


def test_sandbox_runner_reports_runtime_error():
    src = (
        "class P(PreemptionPolicy):\n"
        "    name = 'p'\n"
        "    def should_defer(self, core, env):\n"
        "        return 1 / 0\n"
    )
    result = sandbox_run({"protocol": "2pl", "source": src, "config": {"sim_time": 500}})
    assert result["error"]["type"] == "runtime_error"


def test_sandbox_runner_reports_validation_error_for_bad_source():
    result = sandbox_run({"protocol": "2pl", "source": "import os\n", "config": {}})
    assert result["error"]["type"] == "validation_error"
