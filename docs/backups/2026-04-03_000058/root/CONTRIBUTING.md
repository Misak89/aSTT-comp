# Contributing Rules (Project Contract)

Doc-Meta:
- owner: engineering
- status: active
- doc_file: CONTRIBUTING.md
- last_updated_utc: 2026-04-02T20:07:29Z
- review_due_utc: 2026-04-15T00:00:00Z


Global Authority (Repository-wide): AGENTS.md is the only top-level Source of Truth for all project rules and interpretation. If any document conflicts with AGENTS.md, AGENTS.md always prevails.
Mandatory Start (in this repository): README.md -> AGENTS.md -> docs/PLAN_TRACKER.md.
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

## 3.1 Dokumentacni formaty
- Pro novy ne-core dokumentacni artefakt je povinny triplet `MD + JSON + JSONL`.
- `MD`: lidska cteci verze (narativ), ne canonical strojovy format.
- `JSON`: canonical strukturovany snapshot pro tooling a LLM.
- `JSONL`: append-only event timeline/log stream.
- `TXT`: mimo triplet, pokud nejde o docs artefakt pod `docs/` (napr. `scripts/log_cmd_help.txt`).

## 3.2 Naming dokumentace
- Pro nove snapshot/report dokumenty pouzivej suffix data na konci nazvu:
  - `<topic>_YYYY-MM-DD.md`
  - `<topic>_YYYY-MM-DD.json`
  - `<topic>_YYYY-MM-DD.jsonl`
- Stabilni "living docs" (napr. `PLAN_TRACKER.md`, `session_log.md`, `ARCHITECTURE.md`, `RUNBOOK.md`) jsou vyjimka.
- Pro nove ne-core docs s metadaty:
  - `MD` s `Doc-Meta` povinne `- doc_file: <filename>`
  - `JSON` s `doc_meta` povinne `doc_meta.doc_file: <filename>`
  - `JSONL` povinne validni JSONL a v prvnim zaznamu `doc_meta.doc_file: <filename>`
- Guard u noveho ne-core artefaktu kontroluje i pritomnost vsech 3 formatu (`.md`, `.json`, `.jsonl`).
- Logicke vyjasneni:
  - core/living governance docs zustavaji single-file kontrakty,
  - systemove state docs (`oss_intake_register`, `specstory_*`, `docs/models/*`, `docs/runs/*`) jsou riditelne specialni politikou,
  - triplet pravidlo je povinne pro nove ne-core lidske artefakty (audit/plan/analyza/report).

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
