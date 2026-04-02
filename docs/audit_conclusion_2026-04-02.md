# Audit Conclusion 2026-04-02 - Runtime, Measurement, Governance

Doc-Meta:
- owner: engineering
- status: active
- doc_file: audit_conclusion_2026-04-02.md
- last_updated_utc: 2026-04-02T07:11:11Z
- review_due_utc: 2026-04-15T00:00:00Z

Scope:
- Project root analyzed: `C:\Users\adamf\OneDrive\Dokumenty\aSTT-comp`
- Method: docs-first review, then code verification
- Code changes during audit: none

## 1. Executive Summary
1. Biggest HW-risk is monitoring overhead: frontend runs multiple periodic timers while backend performs process scans including GPU queries; this combination unnecessarily wakes CPU.
Evidence: `frontend/src/pages/DashboardPage.tsx:43`, `frontend/src/pages/DashboardPage.tsx:576`, `frontend/src/pages/DashboardPage.tsx:742`, `backend/app/routers/health.py:418`, `backend/app/routers/health.py:445`, `backend/app/routers/health.py:168`.
2. Latency measurement is methodologically mixed (live/probe/proxy), so ranking for live-dialog suitability can be impure.
Evidence: `packages/benchmarks/runners/streaming_runner.py:173`, `packages/benchmarks/runners/streaming_runner.py:338`, `scripts/tuning_worker.py:924`.
3. Benchmark orchestration is robust (subprocess isolation), but file I/O polling still increases overhead and state complexity.
Evidence: `backend/app/services/benchmark_service.py:223`, `backend/app/services/benchmark_service.py:268`, `scripts/benchmark_worker.py:33`.
4. Reproducibility is a strong side: seed, source manifest, timeline events, immutable fingerprint, host telemetry.
Evidence: `packages/benchmarks/runners/matrix_benchmark_runner.py:263`, `packages/benchmarks/runners/matrix_benchmark_runner.py:451`, `packages/benchmarks/runners/matrix_benchmark_runner.py:855`, `packages/benchmarks/runners/host_telemetry.py:586`, `packages/benchmarks/runners/host_telemetry.py:608`.
5. UTF-8/Unicode is mostly handled correctly in core paths; primary problems are architecture and measurement discipline, not CP1250 in the main pipeline.
Evidence: `packages/benchmarks/metrics/text_metrics.py:33`, `packages/common/console_io.py:8`.
6. Documentation has drift vs reality (e.g., `run_benchmark.py` reference, `.runtime` vs `runtime` mismatch).
Evidence: `AGENTS.md:68`, `AGENTS.md:73`, `backend/app/config.py:8`.
7. Basic unit validation is present, but critical unit suites in this environment are partially blocked by filesystem `tempfile` permission errors, reducing signal quality.
Evidence: `tests/unit/test_vtt_reference.py:57`, `tests/unit/test_benchmark_matrix.py:122`, `tests/integration/test_api.py:58`.

## 2. Biggest Weaknesses by Priority
1. P1: Monitoring consumes more HW than needed.
Problem: parallel timers + periodic full/slow process scan + GPU query.
Root cause: monitoring split across frontend scheduling and backend heavy scan without central budget.
Impact: higher CPU + jitter during STT measurement itself.
Severity: critical. Confidence: high.
2. P1: Latency metrics are not methodologically homogeneous.
Problem: one KPI lane mixes live/probe/proxy latency.
Root cause: single pipeline serves both online and batch-replay modes with fallbacks.
Impact: distorted decision on "best live Czech model."
Severity: critical. Confidence: high.
3. P1: File-based polling orchestration.
Problem: frequent read/write of `progress/status` plus polling loops.
Root cause: file IPC instead of event stream.
Impact: unnecessary CPU wake-ups, harder race/debug.
Severity: high. Confidence: high.
4. P2: High coupling in runtime layers.
Problem: large service/page modules (mic/tuning/dashboard) carry too many responsibilities.
Root cause: organic growth without hard modular boundaries for orchestration/telemetry/UI.
Impact: slower refactor, higher regression risk.
Severity: high. Confidence: medium-high.
5. P2: Test strategy is more schema/smoke than runtime stress behavior.
Problem: weaker coverage for race/recovery/long-run stability.
Root cause: emphasis on quick contract tests over long integration scenarios.
Impact: late detection of production-like failures.
Severity: high. Confidence: high.
6. P3: Documentation drift.
Problem: part of docs do not reflect current implementation.
Root cause: rapid evolution + multiple documentation sources.
Impact: onboarding/agent errors and wasted cycles.
Severity: medium. Confidence: high.

## 3. What to Fix First
1. Introduce "single monitoring budget" (backend collector + frontend render only).
Concrete change: one backend collector at 1Hz; frontend must not multi-poll same signals.
Benefit: immediate CPU reduction + more stable benchmark signal.
Effort: medium. Risk: low-medium. Type: architecture/runtime.
2. Split benchmark modes in data model: `strict_live` vs `batch_proxy`.
Concrete change: ranking/score never mixes modes; proxy results are explicitly separated.
Benefit: methodologically clean comparison for target scenarios.
Effort: medium. Risk: medium. Type: measurement discipline.
3. Replace file polling by event log (SQLite WAL or lightweight bus).
Concrete change: worker appends events; API serves incremental state.
Benefit: lower I/O overhead, better traceability, fewer race conditions.
Effort: medium-high. Risk: medium. Type: orchestration.

## 4. Quick Wins
1. Change default `/api/health/processes` mode from `full` to `fast`; use `full` only on explicit action.
2. Add TTL cache for `cmdline/exe` per PID in health scan.
3. Reduce progress write frequency to state changes or coarse buckets (e.g., >=500ms).
4. In dashboard, unify RAM/CPU sampling timer from one batch instead of parallel ticks.
5. Align docs on `runtime` vs `.runtime` and remove invalid `run_benchmark.py` references.

## 5. What Not to Solve Now
1. More model/adaptor expansion before strict live-vs-proxy methodology is clean.
2. UI cosmetics before metric correctness and monitoring overhead are fixed.
3. Android/iOS work before a stable benchmark harness for scenarios 1-4.

## 6. Target Module/Data-Flow Structure
1. `orchestrator`: job lifecycle, retry, cancel, state machine.
2. `measurement_core`: latency/RTF/WER discipline, strict schema.
3. `runtime_collector`: CPU/RAM/GPU sampling, single source of truth.
4. `event_store`: append-only events + snapshots.
5. `ui_adapter`: dashboard/tuning read aggregates only.
6. Flow: Worker events -> Event store -> Aggregator -> API/WebSocket -> Frontend render.

## 7. Missing Metrics and Benchmark System for Scenarios 1-4
1. Scenario 1 (2-8s): first-token p50/p95/p99, partial update cadence, dropout rate.
2. Scenario 2 (12-25s): quality-latency frontier with confidence intervals from repeats.
3. Scenario 3 (1-3 min): throughput stability, backlog growth, catch-up behavior.
4. Scenario 4 (2-4 h): memory leak slope, crash-free duration, recovery time, thermal throttling impact.
5. Across all scenarios: separate `cold-start` vs `warm-start`, `clean-host` vs `loaded-host`, fixed seed+manifest+artifact fingerprint.

## 8. Phased Action Plan
1. Phase A (1-2 days): monitoring budget + default fast scan + docs drift fixes.
2. Phase B (2-4 days): strict latency mode split + score gate by quality class.
3. Phase C (4-7 days): event-store IPC instead of file polling.
4. Phase D (3-5 days): integration tests for long-run/recovery/race.
5. Phase E: only then model-matrix/platform expansion.

## 9. Uncertainties (Last)
1. Some unit tests in this environment fail on `PermissionError` in `tempfile`, limiting full behavior validation.
2. Full on-target HW runtime profiling was not executed in this step; audit is static + targeted validation.
3. GPU metric fidelity depends on `nvidia-smi` availability and host GPU stack.

## Top 3 Root Causes
1. No unified monitoring/measurement architecture (multiple independent loops).
2. Mixed measurement modes in one decision lane.
3. File-polling orchestration instead of event-native runtime.

## Top 3 Highest-ROI Interventions
1. Centralized collector + one-timer frontend render.
2. Hard split of `strict_live` and `batch_proxy` in data + score.
3. Move to append-only event store (SQLite WAL) for job/tuning telemetry.

## Top 3 Risks if Nothing Changes
1. Wrong "best model" selection for live Czech due to methodological distortion.
2. Monitoring HW overhead contaminates benchmark results.
3. Coupling growth slows delivery and increases regression rate.
