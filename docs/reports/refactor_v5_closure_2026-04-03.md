# Refactor v5 Closure 2026-04-03

Doc-Meta:
- owner: engineering
- status: active
- doc_file: refactor_v5_closure_2026-04-03.md
- last_updated_utc: 2026-04-03T03:00:29Z
- review_due_utc: 2026-04-15T00:00:00Z

## Kratka obecna zprava
1. P0/P1: snizen monitoring overhead (central cadence + fast default process scan).
2. P2: oddelene latency lane (`strict_live`, `probe_online`, `batch_proxy`) a gating bez mixu live/proxy.
3. P3: event-store tok pro tuning (worker -> backend service/API -> frontend panel).
4. P3/P4: doplnena validace event sekvence + replay/validation CLI.
5. UTF-8 runtime control: web control prepnuto na UTF-8-safe cestu (`webctl.py`, `web.nu`, `web-*.cmd` wrappers).

## Podrobna technicka zprava
1. P3: SQLite WAL event-store modul v `packages/common/tuning_event_store.py`.
2. P3: worker emituje eventy `worker_started`, `job_loaded`, `status_changed`, `progress`, `trial_result`, `job_*` v `scripts/tuning_worker.py`.
3. P3: backend service podporuje inkrementalni event read v `backend/app/services/tuning_service.py`.
4. P3: API endpoint `GET /api/tuning/jobs/{job_id}/events` v `backend/app/routers/tuning.py`.
5. P3: frontend API client + typy pro event feed v `frontend/src/api/client.ts` a `frontend/src/types/index.ts`.
6. P3: Tuning UI panel pro live event stream v `frontend/src/pages/TuningPage.tsx`.
7. P3/P4: validacni sumarizace event sekvence (`ok/issues/terminal/post-terminal`) v service/API.
8. P4: replay CLI hardening pro CP1250/Unicode v `scripts/tuning_event_replay.py`.
9. P4: novy validacni CLI pro event integritu/race symptomy v `scripts/tuning_event_validate.py`.
10. P0/P1: baseline monitoring report generator + triplet vystupy v `scripts/monitoring_baseline_report.py`.
11. P4: unit testy pro event-store/service/validation:
   - `tests/unit/test_tuning_event_store.py`
   - `tests/unit/test_tuning_events_service.py`
   - `tests/unit/test_tuning_event_validation.py`
12. P1/P2 navic:
   - `backend/app/routers/health.py`: process scan policy fast-by-default.
   - `backend/app/services/tuning_decision.py`: lane-split decision gating.

## GitHub stav
- branch: `feature/tuning-v5`
- latest closure commit: `5f6040e`
- status: push to `origin/feature/tuning-v5` complete

## Nehotova vec (odlozeno)
- P4 long-run/race/recovery validace pro dlouhe prepisy (`/transcript`) je vedome odlozena na pristi iteraci podle pokynu ownera.
