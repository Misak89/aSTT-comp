# Architecture (Single Source)

Doc-Meta:
- owner: engineering
- status: active
- last_updated_utc: 2026-04-27T05:24:01Z
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

## 3A. Library audio cache
- Plna audio cache knihovny je v `runtime/audio_cache/`.
- Preferovany nazev souboru je `{prefix8}_{video_id}.wav`, kde `prefix8` je osm znaku odvozenych z pole `title` v knihovne a `video_id` zustava stabilni identifikator.
- Backend resolver v `backend/app/services/library_service.py` zachovava kompatibilitu s legacy tvarem `{video_id}.wav` a umi dohledat i stary prefixed soubor po zmene nazvu v knihovne.
- Benchmark, MIC loop, transcript serving a tuning workery maji pouzivat resolver cache, ne skladat cestu k WAV souboru rucne.

## 4. Mic pipeline
- API/WS: `backend/app/routers/mic.py`
- Orchestrace: `backend/app/services/mic_service.py`
- V7 contract module: `backend/app/services/mic_v7_contract.py`
- Frontend orchestrace sekvence: `frontend/src/components/MicSession.tsx`
- Session artefakty: `runtime/mic_sessions/*`
- Event stream: `runtime/logs/mic_sequence_events.jsonl`
- Sequence report artefakty: `runtime/mic_sequences/<sequence_token>/report.json` + `report.csv`
- MIC UI muze vyplnit spolecne parametry nebo hromadne profily sekvence; backend i frontend ukladaji auditni snapshot `model_params_used` a aktivni profil s hodnotami skutecne pouzitymi pro konkretni model.
- Frontend MIC pipeline pocita vstupni dukazni metriky primo z PCM chunku pred odeslanim na WebSocket: RMS/peak dBFS, VAD/silence, clipping, chunk count a odeslane audio/WS bytes.
- Automaticky MIC transcript v historii je autoritativni jen z WebSocket session `final.text` (`transcript_source=mic_ws_final`); top-level transcript, history snapshot, reference text ani simulace se do MIC radku nedoplnuji.
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
