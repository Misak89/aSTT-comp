# Contributing Rules (Project Contract)

Doc-Meta:
- owner: engineering
- status: active
- last_updated_utc: 2026-04-02T03:19:50Z
- review_due_utc: 2026-04-15T00:00:00Z

Tento soubor je zavazny pro vsechny, kdo meni kod nebo dokumentaci.

## 0. Source Of Truth
- `AGENTS.md` je jediny autoritativni instrukcni dokument pro AI agenty.
- `CLAUDE.md` musi zustat pouze stub odkazujici na `AGENTS.md`.

## 1. Povinny Start Pred Zmenou
1. Precist `AGENTS.md`.
2. Precist `docs/PLAN_TRACKER.md`.
3. Precist `docs/session_log.md` (alespon posledni relevantni session).
4. Pri dopadu na architekturu/provoz precist `docs/ARCHITECTURE.md` a `docs/RUNBOOK.md`.
5. Teprve potom menit kod.

## 2. Definition Of Done (Nejde Obejít)
- Kod je upraven a smysluplne otestovan.
- Pro kazdou kodovou zmenu je pridana nova strucna session polozka v `docs/session_log.md`.
- Je aktualizovana odpovidajici dokumentace dle mapovacich pravidel nize.
- Pri zmene app chovani je overeno, ze Dashboard stale zobrazuje aktualni stav (nebo je explicitne zdokumentovano proc ne).
- V kazdem zmenenem core dokumentu je aktualizovan `last_updated_utc`.
- PR prosel `docs-guard` CI checkem.

## 3. Povinna Metadata V Core Dokumentech
Core docs:
- `README.md`
- `AGENTS.md`
- `CLAUDE.md`
- `CONTRIBUTING.md`
- `docs/PLAN_TRACKER.md`
- `docs/session_log.md`
- `docs/ARCHITECTURE.md`
- `docs/RUNBOOK.md`

Kazdy core dokument musi obsahovat blok:
- `Doc-Meta:`
- `- owner: <value>`
- `- status: <active|stub|deprecated|archive>`
- `- last_updated_utc: YYYY-MM-DDTHH:MM:SSZ`
- `- review_due_utc: YYYY-MM-DDTHH:MM:SSZ`

## 4. Mapovaci Pravidla: Kdy Co Aktualizovat

### A) Jakakoli zmena kodu
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
- `web-*.cmd`, `web-*.sh`, `start_web_app*`
- `scripts/check_health.py`, `scripts/preflight.py`
- run/start/ops workflow

Povinne:
- `docs/session_log.md`
- `docs/RUNBOOK.md`

### D) Zmena planu/roadmapy
Priklady:
- `docs/tuning_*.md`
- `docs/mic_sequence_*.md`
- jiny `docs/*plan*.md` nebo `docs/*roadmap*.md`

Povinne:
- `docs/PLAN_TRACKER.md`
- `docs/session_log.md`

### E) Zmena app stavu/monitoringu (health, jobs, processy, long-running loops)
Priklady:
- backend endpointy pod `GET /api/health*`, `GET /api/benchmark/jobs*`, monitoring endpointy
- frontend dashboard (`frontend/src/pages/DashboardPage.tsx`) a souvisejici API klient
- background process/loop skripty ovlivnujici runtime stav

Povinne:
- overit Dashboard po zmene (manualne nebo testem) a uvést vysledek v PR "Validation"
- pri zmene monitoringu aktualizovat `docs/RUNBOOK.md` (jak cist stav a co je ocekavane)

## 5. Strucna Aktualizace 100+1% Pri Kazde Zmene
- Pro novou session polozku pouzij helper:
  - `python scripts/add_session_log_entry.py --title "<kratky_nazev>" --summary "<co>" --impact "<dopad>"`
- Povinne je max. vecne shrnuti, bez dlouhych odstavcu.
- Pro opakovane chyby z vibe coding session spust:
  - `python scripts/specstory_failure_learning.py`
- Tento krok aktualizuje:
  - `docs/KNOWN_FAILURES.md`
  - `docs/reports/specstory_failures.json`
  - `docs/reports/specstory_pattern_state.json`
- Spust bezpecnostni dependency gate:
  - `python scripts/supply_chain_guard.py`
- Pri zmene dependency souboru (`backend/requirements*`, `frontend/package*.json`) povinne aktualizuj:
  - `docs/reports/oss_intake_register.json`
  - dle potreby `docs/SECURITY_SUPPLY_CHAIN.md`
- Pro lokalni automaticke hlidani pred commitem pouzij:
  - `.pre-commit-config.yaml`
- Pro stabilni hook setup (hlavne Windows) nastav repo-managed hooks:
  - `python scripts/setup_git_hooks.py`
  - pote commit bez `--no-verify` spousti guard skripty pres lokalni `.git/hooks/pre-commit`.

## 6. Git A PR Pravidla
- Commity prubezne, male, auditovatelne.
- Pri velkem diffu sleduj upozorneni `verify_docs_guard.py` (many files/lines changed) a udelej checkpoint branch.
- Push pouze po explicitnim souhlasu maintainera.
- Zadny force push bez explicitniho souhlasu.
- PR musi projit checklistem v `.github/pull_request_template.md`.

## 7. CI A Branch Protection (Nastaveni Repozitare)
V GitHub nastaveni musi byt:
- branch protection na `main` (no direct push),
- required status check: `docs-guard`,
- vyzadovane review pro docs cesty pres `CODEOWNERS`.
