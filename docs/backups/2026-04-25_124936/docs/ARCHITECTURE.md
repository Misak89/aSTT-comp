# Architecture (Single Source)

Doc-Meta:
- owner: engineering
- status: active
- last_updated_utc: 2026-04-25T10:19:54Z
- review_due_utc: 2026-04-15T00:00:00Z

Tento dokument je centralni popis toho, jak projekt funguje.
Detailni specializovane analyzy zustavaji v `docs/tuning_*.md`.

## 1. Cile systemu
- Merit realnou pouzitelnost STT modelu pro online prepis.
- Hlavni metriky: WER/CER, RTF, latence, stabilita, HW naroky.
- Primarni deployment: offline-first.

## 2. Hlavni komponenty
- Backend: FastAPI (`backend/app`)
- Frontend: React + Vite (`frontend/src`)
- STT/adapters: `packages/adapters`
- Benchmark/tuning pipeline: `packages/benchmarks`, `scripts/tuning_worker.py`
- Runtime data: `runtime/` (single active runtime root)
- Runtime path resolver: `packages/common/runtime_paths.py` (canonical `runtime/`, optional legacy read fallback for `.runtime` in utility flows)
- Health scan policy (`backend/app/routers/health.py`): default process scan lane is `fast`; `slow/full` are explicit lanes, and per-PID cmdline/exe metadata uses short TTL cache for lower monitoring overhead.
- V7 runtime mapping health: `GET /api/health/mic-orchestrator-v7` (contract validity + timeline/readiness aggregation from runtime artefacts).

## 3. Datove toky (zjednodusene)
1. Frontend vola API (`/api/...`).
2. Backend service pripravi job/session.
3. Worker/adapters provedou STT (batch nebo mic).
4. Vysledky a telemetrie se ukladaji do runtime souboru.
5. Frontend polluje nebo odebira WS udalosti.

## 4. Mic pipeline
- API/WS: `backend/app/routers/mic.py`
- Orchestrace: `backend/app/services/mic_service.py`
- V7 contract module: `backend/app/services/mic_v7_contract.py`
- Frontend orchestrace sekvence: `frontend/src/components/MicSession.tsx`
- Session artefakty: `runtime/mic_sessions/*`
- Event stream: `runtime/logs/mic_sequence_events.jsonl`
- Sequence report artefakty: `runtime/mic_sequences/<sequence_token>/report.json` + `report.csv`
- MIC UI muze poslat spolecne parametry sekvence; backend i frontend ukladaji auditni snapshot `model_params_used` s hodnotami skutecne pouzitymi pro konkretni model.
- V7 operational endpoints:
  - `GET /api/mic/contract` (schema/version, event names, reason-code vocabulary)
  - `GET /api/mic/sequences/{token}/readiness` (PASS/FAIL checker nad timeline + KPI + kontrakt)
- Preflight gate:
  - session create naplní `preflight_ok/errors/warnings`,
  - `start_recording` blokuje start pri `preflight_ok=false` (reason `preflight_failed`),
  - frontend zobrazi blokaci pred otevrenim WS.

## 5. Tuning pipeline
- API/service: `backend/app/routers/tuning.py`, `backend/app/services/tuning_service.py`
- Worker: `scripts/tuning_worker.py`
- Reporty/validace: `scripts/tuning_*`, `backend/app/services/tuning_decision.py`
- Decision lane discipline:
  - worker zapisuje `latency_quality` + `latency_lane` do `status.json`,
  - decision report pouziva pure-lane pool (`strict_live` -> `probe_online` -> `batch_proxy` -> `mixed` -> `unknown`),
  - ranking nikdy nemicha `strict_live` kandidaty s proxy lane.
- Stav jobu: `runtime/tuning/<job_id>/status.json`

## 6. Dokumentacni navaznosti
- Provozni navody: `docs/RUNBOOK.md`
- Pravidla aktualizace dokumentace: `CONTRIBUTING.md`
- Session historie implementace: `docs/session_log.md`

## 7. Dokumentacni gate architektura
- Vynuceni bezi pres `scripts/verify_docs_guard.py`.
- Guard kontroluje mapovani kod->docs i povinne `Doc-Meta` bloky.
- CI workflow `.github/workflows/docs-guard.yml` je merge gate.
