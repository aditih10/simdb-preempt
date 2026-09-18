"""Regression tests: the package-refactored simulator must reproduce the
committed results.json exactly, given the same seeded config."""

import json
from pathlib import Path

from simulator import TPL_CFG, OCC_CFG, run_matrix

REPO_ROOT = Path(__file__).resolve().parent.parent


def _load_results():
    with open(REPO_ROOT / "results.json") as f:
        return json.load(f)


def test_tpl_matrix_matches_committed_results():
    expected = _load_results()["tpl"]["matrix"]
    actual = run_matrix("2pl", TPL_CFG)
    assert actual == expected


def test_occ_matrix_matches_committed_results():
    expected = _load_results()["occ"]["matrix"]
    actual = run_matrix("occ", OCC_CFG)
    assert actual == expected


def test_same_seed_is_deterministic():
    from simulator import Config, build_policy, run_simulation

    cfg = Config(seed=7, sim_time=2000)
    a = run_simulation("2pl", build_policy("critical_aware"), cfg)
    b = run_simulation("2pl", build_policy("critical_aware"), cfg)
    assert a == b


def test_different_seeds_are_independent_replications():
    from simulator import Config, build_policy, run_simulation

    r1 = run_simulation("occ", build_policy("random"), Config(seed=1, sim_time=3000))
    r2 = run_simulation("occ", build_policy("random"), Config(seed=2, sim_time=3000))
    # Not required to differ in every field, but a fresh seed over a
    # stochastic workload should not coincidentally reproduce the exact
    # same attempted/committed/aborted counts.
    assert (r1["attempted"], r1["committed"], r1["aborted"]) != (
        r2["attempted"], r2["committed"], r2["aborted"]
    )
