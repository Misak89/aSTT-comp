# Tuning v4 - implementacni tasky

Navazuje na:
- `docs/tuning_v4_inteligentni_plan.md`
- `docs/tuning_v4_implementacni_plan.md`

## Pravidla realizace
- Kazdy task ma jasny output + DoD.
- Bez splneni `v4.0` se nepokracuje do plne implementace.
- Zadny "pseudo-online" fallback nesmi byt reportovan jako real mic.

## Milnik M0 - Feasibility gate

### V4-001: Whisper mic spike API
- Typ: backend
- Soubory:
  - `backend/app/services/mic_service.py`
  - `packages/adapters/whisper_cpp_runner.py`
- Cíl:
  - Minimalni E2E mic path pro `whisper_cpp_small`.
- DoD:
  - Session start/chunk/final bez crash.
  - Log `first_token_ms`.

### V4-002: Turbo mic spike API
- Typ: backend
- Soubory:
  - `backend/app/services/mic_service.py`
  - `packages/adapters/whisper_cpp_runner.py`
- Cíl:
  - Overit stejnou cestu pro `whisper_cpp_large_v3_turbo`.
- DoD:
  - 10 minut beh bez locku.
  - Final text + latence metriky.

### V4-003: Feasibility report
- Typ: docs
- Soubory:
  - `docs/tuning_v4_feasibility_report.md` (novy)
- Cíl:
  - Rozhodnout GO/NO-GO pro plnou implementaci.
- DoD:
  - Popsane limitery, rizika, doporuceni.

## Milnik M1 - Real mic adapter + metriky

### V4-010: Capability flags v registry
- Typ: backend
- Soubory:
  - `packages/adapters/_registry.py`
- Cíl:
  - Spravne expose `supports_microphone` pro whisper modely po overeni.
- DoD:
  - API `/api/models` vraci konzistentni mic capability.

### V4-011: Odstraneni hard blokace whisper v mic_service
- Typ: backend
- Soubory:
  - `backend/app/services/mic_service.py`
- Cíl:
  - Nahradit hard reject capability checkem + reason kody.
- DoD:
  - Pri unsupported mode vraci jasny kod chyby.
  - Pri supported mode spusti session.

### V4-012: Event schema sjednoceni
- Typ: backend
- Soubory:
  - `backend/app/services/mic_service.py`
  - `backend/app/models/tuning.py`
- Cíl:
  - Jednotny format eventu `partial|final|stats`.
- DoD:
  - Vsechny adaptery vraci stejnou strukturu.

### V4-013: Real mic latence metriky
- Typ: backend
- Soubory:
  - `backend/app/services/mic_service.py`
  - `scripts/tuning_worker.py`
- Cíl:
  - Zapis `first_token_ms_p50/p95`, `segment_finalize_ms_p50/p95`.
- DoD:
  - Metriky vyplnene v `status.json` u mic trialu.

### V4-014: Stabilita a drop metriky
- Typ: backend
- Soubory:
  - `backend/app/services/mic_service.py`
  - `scripts/tuning_worker.py`
- Cíl:
  - Zapis `drop_rate`, `session_resets`, reason codes.
- DoD:
  - Pri chybe trialu je jednoznacny duvod.

### V4-015: RAM/CPU telemetrie
- Typ: backend
- Soubory:
  - `packages/benchmarks/runners/host_telemetry.py`
  - `scripts/tuning_worker.py`
- Cíl:
  - Povinne `worker_rss_peak_mb`, `cpu_p95` (+ `temp_p95` kde dostupne).
- DoD:
  - V reportu zadne prazdne RAM/CPU sloupce.

## Milnik M2 - Protokol + kalibrace + UI

### V4-020: Mic protokol model
- Typ: backend
- Soubory:
  - `backend/app/models/tuning.py`
- Cíl:
  - Datovy model pro povinna metadata testu.
- DoD:
  - Validace failne pri chybejicich polich v `real_mic_mode`.

### V4-021: Kalibracni skript
- Typ: scripts
- Soubory:
  - `scripts/mic_calibration_check.py` (novy)
- Cíl:
  - Overit RMS, clipping, noise floor pred startem jobu.
- DoD:
  - Skript vraci `pass/fail` + duvod + namerene hodnoty.

### V4-022: Gate kalibrace v tuning_service
- Typ: backend
- Soubory:
  - `backend/app/services/tuning_service.py`
- Cíl:
  - Nedovolit start real mic jobu bez uspesne kalibrace.
- DoD:
  - API vraci srozumitelne validacni chyby.

### V4-023: UI checklist pro real mic
- Typ: frontend
- Soubory:
  - `frontend/src/pages/TuningPage.tsx`
  - `frontend/src/types/index.ts`
  - `frontend/src/api/client.ts`
- Cíl:
  - Pred startem zobrazit protokol + stav kalibrace.
- DoD:
  - Tlacitko start je disabled bez vyplneneho checklistu.

### V4-024: UI pro ETA confidence
- Typ: frontend
- Soubory:
  - `frontend/src/pages/TuningPage.tsx`
- Cíl:
  - Zobrazit `ETA confidence: low|medium|high`.
- DoD:
  - Pri adaptivnim search je nejistota viditelna.

## Milnik M3 - Smart search + release gate

### V4-030: Planner skeleton
- Typ: scripts
- Soubory:
  - `scripts/tuning_smart_planner.py` (novy)
  - `scripts/tuning_worker.py`
- Cíl:
  - Zavest `search_mode=smart` s fazemi gate/search/confirm.
- DoD:
  - Job bezi v smart rezimu end-to-end.

### V4-031: Successive Halving implementace
- Typ: scripts
- Soubory:
  - `scripts/tuning_smart_planner.py`
- Cíl:
  - Prvni verze adaptivniho vyberu trialu.
- DoD:
  - Mene trialu nez full grid pri podobne kvalite top kandidatu.

### V4-032: Normalizovane skore + audit
- Typ: scripts/backend
- Soubory:
  - `scripts/tuning_worker.py`
  - `backend/app/services/tuning_decision.py`
- Cíl:
  - Ukladat `selection_reason`, `pruned_reason`, `score_components`.
- DoD:
  - Kazdy vyradeny/vybrany trial ma vysvetleni.

### V4-033: Reproducibility policy
- Typ: scripts
- Soubory:
  - `scripts/tuning_worker.py`
- Cíl:
  - Finalisti `n>=3`, top2 `n>=5`.
- DoD:
  - Report obsahuje CI + variance.

### V4-034: Randomizace + warmup
- Typ: scripts
- Soubory:
  - `scripts/tuning_worker.py`
- Cíl:
  - Omezit bias poradi trialu (thermal/warmup efekt).
- DoD:
  - Ulozen random seed + warmup trial mimo scoring.

### V4-035: Verifikacni report template
- Typ: docs
- Soubory:
  - `docs/tuning_v4_validation_report_template.md` (novy)
- Cíl:
  - Standardni forma finalniho rozhodnuti.
- DoD:
  - Obsahuje: metriky, limity, confidence, rizika.

### V4-036: Release gate skripty
- Typ: scripts
- Soubory:
  - `scripts/tuning_smoke_validate.py`
  - `scripts/tuning_soak_validate.py`
  - `scripts/tuning_hw_matrix_report.py`
  - `scripts/tuning_decision_report.py`
- Cíl:
  - Formalni kontrola smoke -> decision -> soak -> hw matrix.
- DoD:
  - Jednim prikazem jde overit, zda build splnuje v4 gate.

## Doporucene poradi realizace (kratke sprinty)
1. Sprint A: `V4-001..V4-003` (GO/NO-GO)
2. Sprint B: `V4-010..V4-015` (mic adapter + metriky)
3. Sprint C: `V4-020..V4-024` (protokol + kalibrace + UI)
4. Sprint D: `V4-030..V4-036` (smart search + verifikace)

## Blokacni podminky
- Bez `V4-003` (GO) se nezacina Sprint B.
- Bez `V4-021` + `V4-022` nema real mic tuning rozhodovaci hodnotu.
- Bez `V4-033` + `V4-036` nema byt vydano finalni doporuceni modelu.
