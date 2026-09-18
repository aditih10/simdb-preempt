"""Derives a per-transaction summary table from a Tracer's raw segments +
markers, for the Phase 6 timeline's "click a transaction" detail panel.
Pure presentation-layer aggregation over already-serialized trace data —
touches nothing in simulator/, so it can't affect simulation behavior."""

from __future__ import annotations
from collections import defaultdict


def summarize_transactions(segments: list[dict], markers: list[dict], protocol: str) -> list[dict]:
    by_txn: dict[int, dict] = defaultdict(lambda: {"segments": [], "markers": []})
    for s in segments:
        by_txn[s["txn"]]["segments"].append(s)
    for m in markers:
        by_txn[m["txn"]]["markers"].append(m)

    out = []
    for txn_id, data in by_txn.items():
        segs = sorted(data["segments"], key=lambda s: s["start"])
        marks = sorted(data["markers"], key=lambda m: m["t"])

        arrival = next((m["t"] for m in marks if m["event"] == "arrival"), None)
        terminal = next((m for m in marks if m["event"] in ("commit", "abort")), None)

        cpu_ms = sum(s["end"] - s["start"] for s in segs if s["kind"] in ("cpu", "validation"))
        wait_ms = sum(s["end"] - s["start"] for s in segs if s["kind"] == "lock_wait")
        preempt_count = sum(1 for s in segs if s["kind"] == "preempt_penalty")

        if protocol == "occ":
            critical_ms = sum(s["end"] - s["start"] for s in segs if s["kind"] == "validation")
        else:
            lock_waits = [s for s in segs if s["kind"] == "lock_wait"]
            if lock_waits:
                critical_start = lock_waits[0]["end"]
            elif segs:
                critical_start = segs[0]["start"]
            else:
                critical_start = None
            critical_ms = (
                max(0.0, terminal["t"] - critical_start)
                if (critical_start is not None and terminal is not None)
                else 0.0
            )

        out.append({
            "txn": txn_id,
            "arrival": arrival,
            "status": terminal["event"] if terminal else "in_flight",
            "end_time": terminal["t"] if terminal else None,
            "cpu_ms": round(cpu_ms, 3),
            "wait_ms": round(wait_ms, 3),
            "preempt_count": preempt_count,
            "critical_ms": round(critical_ms, 3),
        })

    # txn=None shouldn't occur (see simulator/model.py Core._os_scheduler's
    # comment on why it briefly could before that fix) — sort defensively
    # rather than let a future edge case 500 the endpoint.
    return sorted(out, key=lambda t: (t["txn"] is None, t["txn"]))
