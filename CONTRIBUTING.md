# Contributing Rules (Project Contract)

Tento soubor je zavazny pro vsechny, kdo meni kod v repozitari.

## 1. Co je povinne precist pred zmenou
1. `README.md`
2. `docs/ARCHITECTURE.md`
3. `docs/RUNBOOK.md`
4. `docs/PLAN_TRACKER.md`
5. `AGENTS.md` (pokud pracujes s AI coding asistentem)

## 2. Definition of Done pro kazdou zmenu
- Kod je upraven a smysluplne otestovan.
- Je zaznamenano **co se zmenilo a proc** v `docs/session_log.md`.
- Je aktualizovana odpovidajici dokumentace podle typu zmeny:
  - Architektura/logika: `docs/ARCHITECTURE.md`
  - Provoz/spousteni/incidenty: `docs/RUNBOOK.md`
  - Model install/uninstall audit: `docs/models/{model_id}.json` (kde relevantni)
- Pokud zmena ovlivnuje uzivatele, je update i v `README.md` (minimalne odkaz/kratky navod).

## 2.1 Kvalita dokumentace (povinne)
Kazdy zapis musi byt:
- Strucny: jen rozhodovaci informace, bez vaty.
- Logicky: stejna struktura, stejne nazvoslovi.
- Jednoznacny: bez nejasnych formulaci a bez konfliktu mezi dokumenty.
- Uzavreny kruh: README -> CONTRIBUTING -> PLAN_TRACKER -> ARCHITECTURE/RUNBOOK -> session_log -> PR checklist -> CI guard.

## 3. Povinna mapovaci pravidla: kdy co aktualizovat

### A) Zmena kodu (backend/frontend/packages/scripts)
- Povinne: `docs/session_log.md`

### B) Zmena architektury nebo hlavni logiky pipeline
Priklady:
- `backend/app/services/*`
- `backend/app/routers/*`
- `packages/*`

Povinne:
- `docs/session_log.md`
- `docs/ARCHITECTURE.md`

### C) Zmena provozu/spousteni/diagnostiky
Priklady:
- `web-*.cmd`, `start_web_app*.cmd`
- `scripts/check_health.py`, `scripts/preflight.py`
- behavior endpointu pro provozni workflow

Povinne:
- `docs/session_log.md`
- `docs/RUNBOOK.md`

### D) Benchmark/tuning rozhodovaci logika
Povinne:
- `docs/session_log.md`
- odpovidajici dokument ve `docs/tuning_*.md` (pokud se meni metodika/pravidla)

### E) Mic sequence roadmap / pokracovani
Pokud se meni mic sekvence, fail-fast, statusy trialu, nebo evaluace sekvence, povinne aktualizuj:
- `docs/mic_sequence_vibe_coding_short_2026-03-30.md`
- `docs/mic_sequence_rychla_vs_orchestracni_prestavba_2026-03-30.md`
- navazne `docs/tuning_v4_implementacni_plan.md` a `docs/tuning_v4_tasky.md` (pokud se meni scope/tasky)
- a vzdy aktualizuj pointer/stav v `docs/PLAN_TRACKER.md`

### F) Zavazne pravidlo "kam se zapisuje plan"
- Aktivni plan a jeho stav je vzdy v `docs/PLAN_TRACKER.md`.
- Detailni roadmapa je v `docs/tuning_v4_implementacni_plan.md`.
- Rozpad na konkretni ukoly a stav je v `docs/tuning_v4_tasky.md`.
- Co se realne provedlo patri do `docs/session_log.md`.
- Bez teto ctverice neni plan povazovan za aktualni.

## 4. Git pravidla
- Commity prubezne, male a auditovatelne.
- Push pouze po explicitnim souhlasu maintainera.
- Zadny force push bez explicitniho souhlasu.

## 5. PR pravidla
- Kazdy PR musi projit checklistem (`.github/pull_request_template.md`).
- PR musi projit CI docs guard (`.github/workflows/docs-guard.yml`).
- V GitHub nastaveni repa musi byt `docs-guard` nastaven jako required status check (branch protection).

## 6. Offline-first pravidlo
- Projekt smeruje na plne offline provoz.
- Pri zmenech se nesmi nepozorovane pridat nova sitova zavislost bez:
  - zaznamu v `docs/offline_network_inventory.md`
  - aktualizace odpovidajiciho kodu/guardu.
