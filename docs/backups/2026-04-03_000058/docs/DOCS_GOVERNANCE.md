# Docs Governance (Enforcement Contract)

Doc-Meta:
- owner: engineering
- status: active
- doc_file: DOCS_GOVERNANCE.md
- last_updated_utc: 2026-04-02T20:07:29Z
- review_due_utc: 2026-04-15T00:00:00Z


Global Authority (Repository-wide): AGENTS.md is the only top-level Source of Truth for all project rules and interpretation. If any document conflicts with AGENTS.md, AGENTS.md always prevails.
Mandatory Start (in this repository): README.md -> AGENTS.md -> docs/PLAN_TRACKER.md.
## 1. Cile
- Zabranit rozpadu dokumentace pri beznem vyvoji.
- Vynutit kratkou aktualizaci dokumentace pri kazde kodove zmene.
- Drzet konzistentni metadata vcetne UTC timestampu.

## 2. Povinne dokumenty (core)
- `README.md`
- `AGENTS.md`
- `CLAUDE.md` (stub-only)
- `CONTRIBUTING.md`
- `docs/PLAN_TRACKER.md`
- `docs/session_log.md`
- `docs/ARCHITECTURE.md`
- `docs/RUNBOOK.md`

## 3. Neobchazitelna struktura
- Kazdy core dokument musi mit `Doc-Meta` blok.
- `last_updated_utc` musi byt ve formatu `YYYY-MM-DDTHH:MM:SSZ`.
- Pokud je core dokument menen, musi byt v patchi zmenena i hodnota `last_updated_utc`.
- `CLAUDE.md` musi zustat stub odkazujici na `AGENTS.md`.

## 3.1 Formaty dokumentace (MD / JSON / JSONL)
- Pro novy ne-core dokumentacni artefakt je povinny triplet `MD + JSON + JSONL` (101% pravidlo).
- `MD`:
  - je pouze lidska cteci vrstva (narativ, vysvetleni, rozhodnuti).
  - neni canonical strojovy format.
  - pokud ma novy ne-core soubor `Doc-Meta`, guard vyzaduje `- doc_file: <filename>`.
- `JSON`:
  - canonical strukturovany snapshot pro tooling/LLM parsovani.
  - pokud novy ne-core soubor obsahuje `doc_meta`, guard vyzaduje `doc_meta.doc_file`.
- `JSONL`:
  - append-only event stream (timeline provenance).
  - u noveho JSONL artefaktu guard vyzaduje validni JSONL + `doc_meta.doc_file` v prvnim zaznamu.
- `TXT`:
  - neni triplet format.
  - je povoleny pro operacni/help soubory mimo `docs/` (napr. `scripts/log_cmd_help.txt`).

## 3.2 Naming pravidla dokumentace
- Pro nove "snapshot/report" dokumenty pouzivat suffix data na konci nazvu:
  - `<topic>_YYYY-MM-DD.md`
  - `<topic>_YYYY-MM-DD.json`
  - `<topic>_YYYY-MM-DD.jsonl`
- "Living docs" se stabilnim nazvem (napr. `PLAN_TRACKER.md`, `session_log.md`, `ARCHITECTURE.md`, `RUNBOOK.md`) jsou z tohoto pravidla vyjimka.
- Pokud je dokument se suffix datem, datum v nazvu je datum vzniku dokumentu/snapshotu.
- U non-core living artefaktu bez suffixu data stale plati triplet pravidlo.

## 3.3 Logicke vyjasneni (anti-contradiction)
- Absolutni vyklad "uplne kazdy docs soubor musi mit 3 formaty" by byl v rozporu s core governance dokumenty a systemovymi state soubory.
- Proto plati:
  - core/living governance dokumenty zustavaji single-file kontrakty,
  - systemove state artefakty (`oss_intake_register`, `specstory_*`, `docs/models/*`, `docs/runs/*`) jsou formatove rizeny specializovane,
  - triplet `MD+JSON+JSONL` je povinny pro nove ne-core lidske dokumentacni artefakty (audit, plan, analyza, roadmapa, report).

## 4. Automaticke vynuceni
- Gate script: `scripts/verify_docs_guard.py`
- CI workflow: `.github/workflows/docs-guard.yml`
- PR checklist: `.github/pull_request_template.md`
- Odpovednost revieweru: `.github/CODEOWNERS`
- Guard navic pro nove docs soubory mimo explicitni vyjimky kontroluje:
  - u snapshot/report dokumentu suffix data v nazvu (`_YYYY-MM-DD`),
  - u novych ne-core `MD`/`JSON`/`JSONL` s metadaty konzistenci `doc_file` s nazvem souboru,
  - pro novy ne-core artefakt pritomnost vsech tri formatu (`.md`, `.json`, `.jsonl`) v ramci stejneho tematickeho stemu.
- Pri zmene `scripts/specstory_failure_learning.py` guard vyzaduje i update:
  - `docs/KNOWN_FAILURES.md`
  - `docs/reports/specstory_failures.json`
  - `docs/reports/specstory_pattern_state.json`
- Pri zmene dependency souboru guard vyzaduje:
  - `docs/reports/oss_intake_register.json`
- CI navic spousti:
  - `python scripts/supply_chain_guard.py`
- Lokalne je pripraveno:
  - `.pre-commit-config.yaml` (supply-chain + specstory loop)

## 5. Operacni rutina po kazde zmene
1. Dokoncit kod + testy.
2. Zkontrolovat, ze Dashboard odpovida aktualnimu stavu app (health/jobs/processy); pri rozdilu doplnit opravu nebo jasne zdokumentovat omezeni.
3. Pridat strucny zapis:
   - `python scripts/add_session_log_entry.py --title "<kratky_nazev>" --summary "<co>" --impact "<dopad>"`
4. Aktualizovat recurring failure analyzu:
   - `python scripts/specstory_failure_learning.py`
   - vystupy: `docs/KNOWN_FAILURES.md`, `docs/reports/specstory_failures.json`, `docs/reports/specstory_pattern_state.json`
5. Aktualizovat OSS intake/security stav:
   - `python scripts/supply_chain_guard.py`
   - vystup/registr: `docs/reports/oss_intake_register.json`
6. Aktualizovat prislusne core docs a jejich `last_updated_utc`.
7. Projit docs guard.

## 6. Backup dokumentace
- Referencni backup pred governance zmenami:
  - `docs/backups/2026-04-01_154911`
