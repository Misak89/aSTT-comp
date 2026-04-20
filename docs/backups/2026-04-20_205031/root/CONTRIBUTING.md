# Contributing Rules (Project Contract)

Doc-Meta:
- owner: engineering
- status: active
- doc_file: CONTRIBUTING.md
- last_updated_utc: 2026-04-04T12:40:06Z
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

## 1.1 Backup dokumentace (MUST)
- Pred jakoukoli editaci dokumentace vytvor centralni timestamp backup do `docs/backups/<YYYY-MM-DD_HHMMSS>/`.
- V backupu zachovej relativni strukturu puvodnich souboru (napr. `root/CONTRIBUTING.md`, `docs/DOCS_GOVERNANCE.md`, `docs/session_log.md`).
- Zalohuj minimalne vsechny soubory, ktere budes v dane session upravovat.
- Historii dokumentace dohledavej podle data/casu v `docs/backups/` (timestamp ve jmenu adresare).

## 2. Definition Of Done (Nejde Obejít)
- Kod je upraven a smysluplne otestovan.
- Pro kazdou kodovou zmenu je pridana nova strucna session polozka v `docs/session_log.md`.
- Je aktualizovana odpovidajici dokumentace dle mapovacich pravidel nize.
- Pri zmene app chovani je overeno, ze Dashboard stale zobrazuje aktualni stav (nebo je explicitne zdokumentovano proc ne).
- V kazdem zmenenem core dokumentu je aktualizovan `last_updated_utc`.
- PR prosel `docs-guard` CI checkem.

## 2.1 Validita testu (MUST, plati globalne)
Pravidlo plati pro vsechny testy: unit, integration, e2e, smoke, performance, security, reliability, manual verification.

Pred testem musi byt explicitne urceno:
- typ testu (`sanity`, `baseline`, `capability`, `regression`, `security-gate`, `release-gate`),
- cil testu (co presne overuje),
- co z vysledku lze a nelze tvrdit.

Povinny obsah kazdeho test reportu:
- vstupy a rozsah (`source`, casovy usek, dataset, prostredi),
- skutecne pouzite parametry (ne jen zamyslene),
- verze/test harness (skript, endpoint, commit/ref),
- artefakty pro audit (`run_id`, cesta na report/log, cas).

Test je neplatny a nesmi byt pouzit pro produktove rozhodnuti, pokud:
- konfigurace neodpovida cili testu,
- chyba harnessu nebo infrastruktury je zamenena za chybu testovane komponenty,
- chybi dukaz o realne pouzitych parametrech,
- je vyvozovan sirsi zaver, nez dovoluje typ testu.

Interpretace vysledku:
- `sanity`: potvrzuje pouze, ze tok bezi; nerika nic o kvalite.
- `baseline`: porovnava modely/nastroje ve stejne konfiguraci; nerika nic o optimalnim per-model nastaveni.
- `capability` a `release-gate`: vyzaduji odpovidajici per-system/per-model nastaveni a reprodukovatelne artefakty.

## 2.2 Physical validation gate (MUST when applicable)
- Pouzivej 3-loop test model:
  - Loop A: fast deterministic checks (unit/integration/security static checks) na kazdy commit/PR.
  - Loop B: short physical fidelity checks pro runtime tvrzeni.
  - Loop C: soak/adversarial checks pro release/high-risk zmeny.
- Physical gate je `MUST`, pokud je tvrzeni runtime kriticke (`funguje/stabilni/rychle/bezpecne`) a zaroven existuje fidelity gap mezi simulaci a realitou.
- Simulace/synteticke testy jsou nutne, ale samy o sobe nestaci pro release-level tvrzeni.
- Detailni rozhodovaci pravidla, plusy/minusy, slaba mista a mitigace jsou v `docs/RUNBOOK.md` (sekce physical validation policy).

## 2.3 Stuck-loop handling (MUST)
- Na stejnou hypotezu max 3 pokusy (vyjimecne 4 jen s novym dukazem).
- Po 3-4 neuspesnych pokusech zastav retry stejne cesty (`no-loop rule`).
- Povinne zapsat: co bylo zkouseno, co selhalo, jaky je blocker.
- Dalsi krok: navrhnout max 2 bezpecne varianty nebo oznacit stav `blocked` a cekat na rozhodnuti ownera.
- Neni dovoleno "tocit dalsi pokusy" bez nove evidence nebo noveho vstupu.

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
