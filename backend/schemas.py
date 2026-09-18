"""Pydantic request/response models for the SimDB-Preempt API.

These mirror simulator.model.Config field-for-field (with validation
ranges added) rather than inventing a parallel shape, so the API layer
stays a thin wrapper around the existing simulator rather than a
reimplementation of it.
"""

from __future__ import annotations
from typing import Literal, Optional

from pydantic import BaseModel, Field, model_validator

from simulator import BUILTIN_POLICIES

Protocol = Literal["2pl", "occ"]


class ConfigOverrides(BaseModel):
    """All fields optional — unset fields fall back to the protocol's
    default Config (TPL_CFG for 2pl, OCC_CFG for occ)."""

    num_cores: Optional[int] = Field(default=None, ge=1, le=256)
    db_size: Optional[int] = Field(default=None, ge=10, le=1_000_000)
    contention: Optional[float] = Field(default=None, ge=0.0, lt=1.0)
    ops_per_txn: Optional[int] = Field(default=None, ge=1, le=1000)
    mean_op_time: Optional[float] = Field(default=None, gt=0.0)
    lock_timeout: Optional[float] = Field(default=None, gt=0.0)
    preempt_rate: Optional[float] = Field(default=None, ge=0.0)
    preempt_penalty: Optional[float] = Field(default=None, ge=0.0)
    sim_time: Optional[float] = Field(default=None, gt=0.0, le=600_000.0)
    arrival_rate: Optional[float] = Field(default=None, gt=0.0)
    seed: Optional[int] = Field(default=None)

    def overrides(self) -> dict:
        return {k: v for k, v in self.model_dump().items() if v is not None}


class SimulateRequest(BaseModel):
    protocol: Protocol = "2pl"
    policy: Optional[str] = "none"
    algorithm: Optional[str] = None  # custom PreemptionPolicy source, sandboxed
    config: ConfigOverrides = Field(default_factory=ConfigOverrides)

    @model_validator(mode="after")
    def _check_policy_or_algorithm(self):
        if self.algorithm is not None:
            if not self.algorithm.strip():
                raise ValueError("'algorithm' source must not be empty")
            # Real validation (AST whitelist, line numbers, friendly
            # messages) happens at execution time in the sandbox — see
            # simulator.sandbox.validate_policy_source — so the error
            # shape matches the "Algorithm Error" / "Unsupported API" /
            # "Simulation Timeout" taxonomy rather than a generic 422.
            return self
        if self.policy not in BUILTIN_POLICIES:
            raise ValueError(
                f"unknown policy '{self.policy}' — must be one of {sorted(BUILTIN_POLICIES)}"
            )
        return self


class CompareRequest(BaseModel):
    protocol: Protocol = "2pl"
    policies: list[str] = Field(default_factory=list)
    algorithm: Optional[str] = None  # optional custom policy, run alongside `policies`
    config: ConfigOverrides = Field(default_factory=ConfigOverrides)

    @model_validator(mode="after")
    def _check_policies(self):
        if not self.policies and self.algorithm is None:
            raise ValueError("select at least one baseline policy or provide 'algorithm'")
        unknown = [p for p in self.policies if p not in BUILTIN_POLICIES]
        if unknown:
            raise ValueError(
                f"unknown polic{'y' if len(unknown) == 1 else 'ies'} {unknown} — "
                f"must be one of {sorted(BUILTIN_POLICIES)}"
            )
        if self.algorithm is not None and not self.algorithm.strip():
            raise ValueError("'algorithm' source must not be empty")
        return self


class SweepRequest(BaseModel):
    """Sweeps contention only, for the Phase 5 'metric vs contention' charts.
    Phase 7 generalizes this to arbitrary parameters + replications; kept
    narrow here on purpose rather than building the general case early."""

    protocol: Protocol = "2pl"
    policies: list[str] = Field(default_factory=list)
    algorithm: Optional[str] = None
    contentions: list[float] = Field(min_length=2, max_length=10)
    config: ConfigOverrides = Field(default_factory=ConfigOverrides)

    @model_validator(mode="after")
    def _check(self):
        if not self.policies and self.algorithm is None:
            raise ValueError("select at least one baseline policy or provide 'algorithm'")
        unknown = [p for p in self.policies if p not in BUILTIN_POLICIES]
        if unknown:
            raise ValueError(
                f"unknown polic{'y' if len(unknown) == 1 else 'ies'} {unknown} — "
                f"must be one of {sorted(BUILTIN_POLICIES)}"
            )
        if self.algorithm is not None and not self.algorithm.strip():
            raise ValueError("'algorithm' source must not be empty")
        bad = [c for c in self.contentions if not (0.0 <= c < 1.0)]
        if bad:
            raise ValueError(f"contentions must each be in [0.0, 1.0) — got {bad}")
        return self


class TraceRequest(BaseModel):
    """Runs each requested policy once and captures a windowed, per-core
    event trace (segments + markers) for the Phase 6 timeline/event log,
    plus a derived per-transaction summary (backend/trace_summary.py)."""

    protocol: Protocol = "2pl"
    policies: list[str] = Field(default_factory=list)
    algorithm: Optional[str] = None
    config: ConfigOverrides = Field(default_factory=ConfigOverrides)
    window_start: float = Field(default=0.0, ge=0.0)
    window_size: float = Field(default=150.0, gt=0.0, le=1000.0)
    core_id: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def _check(self):
        if not self.policies and self.algorithm is None:
            raise ValueError("select at least one baseline policy or provide 'algorithm'")
        unknown = [p for p in self.policies if p not in BUILTIN_POLICIES]
        if unknown:
            raise ValueError(
                f"unknown polic{'y' if len(unknown) == 1 else 'ies'} {unknown} — "
                f"must be one of {sorted(BUILTIN_POLICIES)}"
            )
        if self.algorithm is not None and not self.algorithm.strip():
            raise ValueError("'algorithm' source must not be empty")
        return self
