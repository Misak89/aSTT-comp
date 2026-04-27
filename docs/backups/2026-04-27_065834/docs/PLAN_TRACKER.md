# Plan Tracker (Single Source Of Truth)

Doc-Meta:
- owner: engineering
- status: active
- last_updated_utc: 2026-04-27T02:19:31Z
- review_due_utc: 2026-04-15T00:00:00Z

Tento soubor je jedine misto, kde je videt aktualni plan a jeho stav.

## 1. Aktivni plan(y)

### A) Produktovy plan
- Plan ID: `real-mic-v4`
- Status: `in_progress`
- Scope:
  - real mic sekvence + fail-fast + metriky
  - rozhodovaci tuning v4

Navazne dokumenty:
- strategie: `docs/mic_sequence_vibe_coding_short_2026-03-30.md`
- kriticke varianty: `docs/mic_sequence_rychla_vs_orchestracni_prestavba_2026-03-30.md`
- roadmapa: `docs/tuning_v4_implementacni_plan.md`
- tasky + stav: `docs/tuning_v4_tasky.md`

### B) Dokumentacni governance plan
- Plan ID: `docs-governance-v1`
- Status: `in_progress`
- Scope:
  - jednotny vstup pro LLM (`AGENTS.md`)
  - stub-only `CLAUDE.md`
  - CI gate pro metadata + timestamp + mapovani docs update
  - kratky session update pri kazde kodove zmene
  - self-improving recurring-failure analyza ze `.specstory`
  - supply-chain security gate pro inspiraci/stahovani/build

### C) Post-audit stabilization plan
- Plan ID: `post-audit-v5`
- Status: `in_progress`
- Scope:
  - snizeni monitoring overhead bez ztraty diagnostiky
  - oddeleni strict live vs proxy latency lane
  - event-store orchestrace misto file polling
  - long-run/recovery/race validation

Navazne dokumenty:
- audit summary (MD): `docs/audit_conclusion_2026-04-02.md`
- audit summary (JSON): `docs/reports/audit_conclusion_2026-04-02.json`
- audit summary (JSONL): `docs/reports/audit_conclusion_2026-04-02.jsonl`
- roadmapa (MD): `docs/tuning_v5_implementacni_plan.md`
- roadmapa (JSON): `docs/tuning_v5_implementacni_plan.json`
- roadmapa (JSONL): `docs/tuning_v5_implementacni_plan.jsonl`
- closure report (MD/JSON/JSONL): `docs/reports/refactor_v5_closure_2026-04-03.*`

### D) Long transcript and segmentation plan
- Plan ID: `long-transcript-v6`
- Status: `in_progress`
- Scope:
  - dokonceni dlouhych prepisu pro `/transcript` (30-240 min)
  - segmentace importovanych nahravek v `/library` (preset + manual body)
  - pause-aware posun hranic segmentu
  - stabilni beh alespon 3 CZ modelu v long transcript rezimu

Navazne dokumenty:
- roadmapa (MD): `docs/tuning_v6_implementacni_plan.md`
- roadmapa (JSON): `docs/tuning_v6_implementacni_plan.json`
- roadmapa (JSONL): `docs/tuning_v6_implementacni_plan.jsonl`

### E) CS Online Mic Orchestrator plan
- Plan ID: `cs-online-mic-orchestrator-v7`
- Status: `in_progress`
- Scope:
  - online mic only (`cs` first)
  - sekvencni runner na jednom streamu bez resetu globalniho casu
  - presne event logy + dual output (human transcript + machine log)
  - KPI `kvalita x latence x HW` + jednotna porovnavaci tabulka

Navazne dokumenty:
- roadmapa (MD): `docs/tuning_v7_implementacni_plan.md`
- roadmapa (JSON): `docs/tuning_v7_implementacni_plan.json`
- roadmapa (JSONL): `docs/tuning_v7_implementacni_plan.jsonl`

## 2. Stav milniku

| Milnik | Stav | Zdroj |
|---|---|---|
| M0 feasibility | in_progress | `docs/tuning_v4_tasky.md` |
| M1 mic adapter + metriky | in_progress | `docs/tuning_v4_tasky.md` |
| M2 protokol + UI | planned | `docs/tuning_v4_tasky.md` |
| M3 smart search + gate | planned | `docs/tuning_v4_tasky.md` |
| D1 AGENTS authoritative + CLAUDE stub | done | `AGENTS.md`, `CLAUDE.md` |
| D2 docs guard metadata/timestamp enforcement | done | `scripts/verify_docs_guard.py` |
| D3 session update helper | done | `scripts/add_session_log_entry.py` |
| D5 specstory self-improving failure analytics | done | `scripts/specstory_failure_learning.py`, `docs/KNOWN_FAILURES.md` |
| D6 supply-chain security policy + guard | done | `docs/SECURITY_SUPPLY_CHAIN.md`, `scripts/supply_chain_guard.py` |
| D4 branch protection + required checks + CODEOWNERS | pending_repo_setting | `CONTRIBUTING.md`, `.github/CODEOWNERS` |
| P0 post-audit baseline + instrumentation lock | done | `docs/tuning_v5_implementacni_plan.md`, `docs/reports/monitoring_baseline_2026-04-03.md` |
| P1 monitoring budget + scan policy | done | `docs/tuning_v5_implementacni_plan.md` |
| P2 strict live vs proxy discipline | done | `docs/tuning_v5_implementacni_plan.md` |
| P3 event-store orchestration | done | `docs/tuning_v5_implementacni_plan.md` |
| P4 long-run validation gate | planned | `docs/tuning_v5_implementacni_plan.md` |
| V6-S0 intake + one-command non-physical gate | done | `docs/tuning_v6_implementacni_plan.md` |
| V6-S1 backend segment domain + pause-aware snapping | done | `docs/tuning_v6_implementacni_plan.md` |
| V6-S2 `/library` slicer UI (preset + max 21 bodu) | done | `docs/tuning_v6_implementacni_plan.md` |
| V6-S3 `/transcript` bundle orchestration (one logical row) | done | `docs/tuning_v6_implementacni_plan.md` |
| V6-S4 3x CZ model stability + long-run validation | planned | `docs/tuning_v6_implementacni_plan.md` |
| V7-S0 data contract + event schema (`run_id`, `sequence_id`, globalni cas) | in_progress (viz V7 matrix nize) | `docs/tuning_v7_implementacni_plan.md` |
| V7-S1 backend sekvencni orchestrator (single live stream) | in_progress (viz V7 matrix nize) | `docs/tuning_v7_implementacni_plan.md` |
| V7-S2 presne timestampovane event logy + transition reasons | in_progress (viz V7 matrix nize) | `docs/tuning_v7_implementacni_plan.md` |
| V7-S3 dual output + casova konzistence transcript/log | in_progress (viz V7 matrix nize) | `docs/tuning_v7_implementacni_plan.md` |
| V7-S4 KPI vypocet + jednotna porovnavaci tabulka | in_progress (viz V7 matrix nize) | `docs/tuning_v7_implementacni_plan.md` |
| V7-S5 validacni run 3-5 modelu + DoD kontrola | in_progress (viz V7 matrix nize) | `docs/tuning_v7_implementacni_plan.md` |

Operational note (2026-04-03): P4 (long-run/race/recovery validace dlouhych prepisu) je vedome odlozena do navazujici iterace `long-transcript-v6`.

## 2A. V7 Milestone Matrix (authoritative for V7)

Stavy V7 jsou zavazne vedeny dvojici `implementation` + `validation`.

| Milnik | implementation | validation | readiness | Evidence |
|---|---|---|---|---|
| V7-S0 | implemented | in_progress | amber | `backend/app/services/mic_v7_contract.py`, `tests/unit/test_mic_v7_contract.py` |
| V7-S1 | implemented | in_progress | amber | `backend/app/services/mic_service.py` (`create_session`, `start_recording`, `_update_v7_timing_on_start`), `tests/unit/test_mic_orchestrator_mode.py` |
| V7-S2 | implemented | in_progress | amber | `runtime/logs/mic_sequence_events.jsonl`, `apply_v7_event_contract`, `GET /api/mic/contract` |
| V7-S3 | implemented | in_progress | amber | `backend/app/routers/mic.py` (session payload + ws final envelope), `frontend/src/components/MicSession.tsx` |
| V7-S4 | implemented | in_progress | amber | `compute_kpi_summary`, `GET /api/mic/sequences/{token}/readiness`, `GET /api/health/mic-orchestrator-v7` |
| V7-S5 | in_progress | failed | red | `docs/reports/v7_readiness_checklist_2026-04-20.md`, `scripts/v7_readiness_checklist.py`, `tests/unit/test_v7_readiness_checklist.py` |

Aktualni stav V7-S5 (2026-04-20):
- Readiness checklist probe byl spusten nad `shared_seq` a skoncil `FAIL`.
- Hlavni blokery: model coverage `<3`, trialy nejsou `started/finalized`, chybi latency evidence, dashboard runtime mapping hlasi `warn`.

Aktualni UI/sequence stav (2026-04-25):
- MIC sekvence umi volitelne spolecne parametry i hromadne profily nad vybranymi modely; kazdy model pouzije pouze podporovane klice.
- Manualni MIC historie i `runtime/mic_sequences/<sequence_token>/report.json` ukladaji `model_params_used` a aktivni profil nastaveni pro audit porovnani.
- Nemeni to V7-S5 readiness: fyzicky validacni run 3-5 modelu stale chybi.

Aktualni UI/sequence stav (2026-04-26):
- MIC auto sekvence ma rozsireni `Automaticke ladeni parametru`: plan generuje sloty `model + varianta nastaveni + opakovani` kolem aktualniho baseline nastaveni.
- Sekvencni report a CSV export ukladaji `tuning_*` metadata, zmeny parametru proti baseline a souhrn kvality podle variant.
- Nemeni to V7-S5 readiness: fyzicky validacni run 3-5 modelu stale chybi a musi byt proveden na realnem mikrofon/audio loop vstupu.

Aktualni UI/sequence stav (2026-04-27):
- `Automaticke ladeni parametru` ma nastavitelnou `Velikost kroku` pro `Uzke doladeni` i `Sirsi overeni`; default zustava `1x`, rychle volby jsou `0.5x`, `1x`, `2x`.
- `tuning_step_size` se uklada do trial metadata, sequence planu, CSV exportu a historie, aby bylo zpetne videt, jak hrube se ladici parametry posouvaly.
- Popisky `Opakovani varianty` a `Max lag (s)` maji hover napovedu; `Max lag` zustava pouze hodnotici limit pro souhrn ladeni, ne stop podminka trialu.
- `Opakovani varianty` v automatickem ladeni je omezene na hodnoty `1x` az `10x` po jedne.
- Nemeni to V7-S5 readiness: fyzicky validacni run 3-5 modelu stale chybi a musi byt proveden na realnem mikrofon/audio loop vstupu.

Poznamka k readiness:
- `green`: implementation=`implemented` a validation=`passed`
- `amber`: implementation hotova, ale validace bezi/chybi
- `red`: validation=`failed` nebo implementation=`blocked/regressed`

## 3. Kam co zapisovat (zavazne)
1. Aktivni plan + stav: `docs/PLAN_TRACKER.md`
2. Dlouhodoba roadmapa: `docs/tuning_v4_implementacni_plan.md`, `docs/tuning_v5_implementacni_plan.md`, `docs/tuning_v6_implementacni_plan.md`, `docs/tuning_v7_implementacni_plan.md`
3. Operacni tasky: `docs/tuning_v4_tasky.md`
4. Co se realne provedlo: `docs/session_log.md`

## 4. Jak aktualizovat plan (povinna posloupnost)
1. Uprav roadmapu/tasky (pokud se meni scope).
2. Aktualizuj tento tracker:
   - `Doc-Meta.last_updated_utc`
   - statusy milniku
3. Zapis zmenu do `docs/session_log.md` (co/proc/dopad).
4. Pokud je dopad na architekturu/provoz, aktualizuj i `docs/ARCHITECTURE.md` nebo `docs/RUNBOOK.md`.

## 5. Stavovy slovnik
- `planned`
- `in_progress`
- `blocked`
- `done`
- `dropped`
- `pending_repo_setting`

## 6. Stavovy slovnik V7 matrix
- `implementation`: `planned`, `in_progress`, `implemented`, `blocked`, `regressed`
- `validation`: `not_started`, `in_progress`, `passed`, `failed`, `waived`
- `readiness`: `green`, `amber`, `red`
