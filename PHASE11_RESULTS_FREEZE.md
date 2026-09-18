# Phase 11 Results Freeze — SimDB-Preempt

**Status: DRAFT / UNCOMMITTED.** This file is new, created for human review per the Phase 11
"Final Experiment Run and Results Freeze" instructions. It has **not** been added to git and
nothing else was modified: `research_paper_proposal (3).docx`, `results.json`, and all
`simulator/`/config files are untouched. This is a candidate source-of-truth for the paper's
final numbers, pending human sign-off on the two open design decisions (OCC contention level,
and the 2PL abort-rate claim) — see `PAPER UPDATES REQUIRED` at the end.

Generated: 2026-09-18. No parameters were tuned to reproduce the original proposal figures.

---

## A. Experiment metadata

| Field | Value |
|---|---|
| Simulator version | `2.0.0` (`simulator.__version__`) |
| Git commit at time of run | `4d8cfe3` ("checkpoint after phase 7"), working tree otherwise clean |
| Config source | `simulator/runner.py`: `TPL_CFG`, `OCC_CFG` (unmodified) |
| OCC contention | 0.913 (canonical `OCC_CFG` default — **not** tuned for this experiment) |
| 2PL contention | 0.6 (canonical `TPL_CFG` default — **not** tuned) |
| DB size | 2000 keys (both protocols) |
| Ops per transaction | OCC: 8, 2PL: 6 |
| Number of cores | 8 (both) |
| Preemption rate | 0.14 /ms (both) |
| Preempt penalty | 2.0 ms (default, both) |
| Lock timeout (2PL only) | 25.0 ms |
| Arrival rate | 2.0 /ms (both) |
| Sim time | 15,000 ms (both) |
| Replications (N) | 30 per policy per condition |
| Seed scheme | `seed = 1000 + rep`, rep = 0..29 → seeds **1000–1029**. This is the Phase 7 Experiment Builder's own documented formula (`runExperiment.ts: replicationSeed()`), deliberately independent of the canonical single-run seed=42. |
| 95% CI method | Sample mean; sample SD (n−1 denominator); two-tailed Student's-t critical value at df=n−1. For N=30, df=29, **t=2.045** (matches `frontend/src/stats.ts`'s table exactly). CI = mean ± t·(SD/√N). |
| Execution path | Real `/api/simulate/compare` HTTP calls against the running dev backend — the same endpoint and same seed formula the Experiment Builder UI uses (see note below on the UI's replication cap). |

**Note on how N=30 was obtained:** the Experiment Builder's own "Replications" `<input>` is hard-capped at **max=20** in the current UI (`ExperimentBuilder.tsx`: `Math.max(1, Math.min(20, ...))`) — this is a previously-undocumented limitation discovered during this phase, not mentioned in any prior phase's notes. Reps 0–19 (seeds 1000–1019) were run **through the live UI** (Run Experiment button, OCC-only, contention start=stop=0.913, N=20) and read back from its own results table. Reps 20–29 (seeds 1020–1029) were obtained by calling the identical `/api/simulate/compare` endpoint directly with the identical seed formula, since the UI itself cannot go past 20. The two were verified to be exactly consistent: recomputing the N=20 subset independently via the API matched the UI's own displayed table to 3+ decimal places (e.g. UI showed `none` abort mean 25.982%, independent recomputation of the same 20 seeds gave 25.9825% — same number, rounding only). The combined N=30 statistics below are the authoritative Phase 11 numbers.

---

## B. OCC results — contention = 0.913 (FINAL, N=30, seeds 1000–1029)

| Policy | N | Abort % mean±SD | Abort % 95% CI | Throughput mean±SD (tps) | Throughput 95% CI | Mean lat. mean±SD (ms) | Mean lat. 95% CI | P99 lat. mean±SD (ms) | P99 lat. 95% CI |
|---|---|---|---|---|---|---|---|---|---|
| none | 30 | 25.939 ± 0.289 | [25.831, 26.047] | 1479.40 ± 8.64 | [1476.17, 1482.62] | 2.489 ± 0.014 | [2.484, 2.494] | 7.038 ± 0.126 | [6.991, 7.085] |
| random | 30 | 30.235 ± 0.241 | [30.145, 30.325] | 1395.26 ± 6.13 | [1392.97, 1397.55] | 4.002 ± 0.075 | [3.974, 4.030] | 16.289 ± 0.704 | [16.027, 16.552] |
| critical_aware | 30 | 27.506 ± 0.246 | [27.414, 27.597] | 1448.61 ± 7.34 | [1445.87, 1451.35] | 3.742 ± 0.063 | [3.719, 3.765] | 14.972 ± 0.520 | [14.778, 15.166] |

**Cross-validation** (independent second N=30 sample, seeds 0–29, run in Phase 10 via direct simulator import rather than the API): none=25.945±0.235 [25.857,26.033], random=30.279±0.210 [30.201,30.357], critical_aware=27.513±0.194 [27.440,27.585]. Every value agrees with the seeds-1000–1029 sample above to within ~0.1 percentage point — two independent 30-seed samples via two different code paths converge tightly. This is strong evidence the simulator's OCC behavior at contention=0.913 is stable and reproducible, not an artifact of a particular seed range.

## C. Same table — contention = 0.6 (reference only; NOT the finalized OCC condition, from Phase 10, N=30, seeds 0–29)

| Policy | N | Abort % mean±SD | Abort % 95% CI | Throughput mean±SD (tps) |
|---|---|---|---|---|
| none | 30 | 8.572 ± 0.159 | [8.512, 8.631] | 1829.74 ± 9.37 |
| random | 30 | 13.661 ± 0.209 | [13.584, 13.739] | 1729.03 ± 11.50 |
| critical_aware | 30 | 9.386 ± 0.142 | [9.333, 9.439] | 1811.49 ± 11.63 |

(Full 4-metric table for 0.6 is in the Phase 10 report; reproduced here only as the rejected-alternative reference point, per the finalized decision to use 0.913.)

## D. 2PL results — canonical config, contention=0.6, lock_timeout=25ms (N=30, seeds 1000–1029)

| Policy | N | Abort % | Throughput mean±SD (tps) | Throughput 95% CI | Mean lat. mean±SD (ms) | Mean lat. 95% CI | P99 lat. mean±SD (ms) | P99 lat. 95% CI |
|---|---|---|---|---|---|---|---|---|
| none | 30 | **0.000%** (0 aborts / 899,471 attempted) | 1998.61 ± 11.85 | [1994.19, 2003.03] | 1.7198 ± 0.0090 | [1.7164, 1.7232] | 5.050 ± 0.065 | [5.025, 5.074] |
| random | 30 | **0.000%** (0 aborts / 899,091 attempted) | 1997.66 ± 12.30 | [1993.07, 2002.25] | 2.4423 ± 0.0325 | [2.4302, 2.4544] | 10.402 ± 0.328 | [10.280, 10.525] |
| critical_aware | 30 | **0.000%** (0 aborts / 898,950 attempted) | 1997.48 ± 11.48 | [1993.19, 2001.76] | 1.7216 ± 0.0078 | [1.7187, 1.7245] | 5.053 ± 0.055 | [5.033, 5.074] |

**Actual timeout/abort count, combined with Phase 10's independent seed set (0–29):** across all 60 independent seeds tested for 2PL so far (30 from Phase 10 + 30 here), there was exactly **1** lock-timeout abort total — seed=4 (Phase 10 set), `random` policy, 1 abort out of ~30,000 attempted in that single run. All other 59 (seed × policy) runs had **zero** aborts. This is not a per-seed-42 fluke; it is a structural property of the current config (`lock_timeout=25ms` is generous relative to the workload's actual lock-wait distribution).

---

## E. Derived comparisons (formulas shown)

**Abort-rate gap, random − none (percentage points):** `mean(random) − mean(none)`
- OCC @ 0.913: 30.235 − 25.939 = **4.296 pp**
- OCC @ 0.6 (reference): 13.661 − 8.572 = **5.089 pp**

**Relative throughput degradation:** `(mean(none) − mean(random)) / mean(none) × 100%`
- OCC @ 0.913: (1479.40 − 1395.26) / 1479.40 = **5.69%**
- 2PL @ 0.6: (1998.61 − 1997.66) / 1998.61 = **0.047%** (negligible — 2PL throughput is arrival-rate-bound, consistent with the app's own explanation panel and the original `runner.py` author's choice to never use 2PL throughput as a layer-gap metric)

**P99 latency ratio, random / none:** `mean_p99(random) / mean_p99(none)`
- OCC @ 0.913: 16.289 / 7.038 = **2.31×**
- 2PL @ 0.6: 10.402 / 5.050 = **2.06×**

**Critical-aware recovery fraction:** `(metric(random) − metric(critical_aware)) / (metric(random) − metric(none)) × 100%` — how much of the none→random gap critical-aware closes.
- OCC @ 0.913, abort rate: (30.235 − 27.506) / (30.235 − 25.939) = 2.729 / 4.296 = **63.5% recovered**
- 2PL @ 0.6, P99 latency: (10.402 − 5.053) / (10.402 − 5.050) = 5.349 / 5.352 = **99.9% recovered** — critical-aware essentially fully restores baseline 2PL P99 latency. (Abort-rate recovery is not computable for 2PL — there is no gap to recover; all three policies are at 0.000%.)

---

## F. PAPER UPDATES REQUIRED (not applied — for human review)

| # | Old claim (proposal, abstract/Section 3) | Evidence-based replacement (Phase 11 final, N=30, seeds 1000–1029) |
|---|---|---|
| 1 | "under moderate contention (hot-key ratio = 0.6)" | The OCC experiment's actual contention is **0.913**, not 0.6 (0.6 is the *2PL* config's value). 0.913 is near the top of the tested 0.5–0.95 range and should be described as high/severe contention, not moderate — **or** the experimental design itself should be changed to actually run OCC at 0.6, which is a design decision (Phase 10 §H), not a wording fix. |
| 2 | "random preemption raises the abort rate of OCC from 25.7% to 30.5%" | **25.94% → 30.24%** (N=30, 95% CI [25.83,26.05]% → [30.15,30.33]%) |
| 3 | "degrading throughput by 6.3%" | **5.69%** (N=30) |
| 4 | "increases OCC abort rates by up to 4.8 percentage points" (Section 3) | **4.30 percentage points** (N=30) |
| 5 | "preemption during lock holding increases lock-wait timeouts **and abort rates**" (2PL) | Not supported for the "abort rates" clause — 0.000% aborts across 60 independent seeds under the current config. What **is** supported: preemption increases P99 latency 2.06× (5.05ms→10.40ms) and mean latency ~1.42×, and critical-aware recovers 99.9% of the P99 latency gap. Recommend narrowing the claim to lock-wait/latency only, unless the design decision in Phase 10 §H.3/§E is taken (re-run with a tighter `lock_timeout` to actually produce timeouts — a different experiment, not this one). |
| 6 | No methodology/replication/seed/CI disclosure anywhere in the current text | Add: N=30 independent replications per condition, seeds 1000–1029 (documented formula, seed=1000+rep), 95% CI via two-tailed Student's-t (df=29, t=2.045), CI = mean ± t·SD/√N. |

**None of these have been written into the `.docx`.** They are proposed replacement text/values for a human to apply.

---

## G. Final check

- [x] Final OCC experiment ran through the intended workflow — partially via the live Experiment Builder UI (N=20, the UI's hard cap) and partially via direct calls to the identical `/api/simulate/compare` endpoint with the identical documented seed formula for the remaining 10 reps (UI cannot do N=30; discovered and disclosed above, not hidden).
- [x] N = 30 (combined, cross-validated against a fully independent second N=30 sample from Phase 10)
- [x] Independent seeds used (1000–1029, the Experiment Builder's own formula)
- [x] Contention = 0.913 (OCC final)
- [x] All three OCC policies tested (none, random, critical_aware — "My Algorithm" and "grace_period" explicitly excluded from this run)
- [x] All four required metrics recorded (abort rate, throughput, mean latency, P99 latency)
- [x] Results reproducible — verified two ways: (a) UI vs. direct-API recomputation of the same 20 seeds matched exactly, (b) two fully independent 30-seed samples (0–29 and 1000–1029) agree within ~0.1pp
- [x] No parameters were tuned to match the historical proposal numbers — `OCC_CFG`/`TPL_CFG` used exactly as committed
- [x] No paper, results.json, simulator, or config files were modified
- [x] This results-freeze artifact (`PHASE11_RESULTS_FREEZE.md`) is new, clearly labeled DRAFT/UNCOMMITTED, and not added to git
