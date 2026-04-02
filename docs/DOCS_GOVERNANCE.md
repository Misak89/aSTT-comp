# Docs Governance (Enforcement Contract)

Doc-Meta:
- owner: engineering
- status: active
- last_updated_utc: 2026-04-01T18:51:55Z
- review_due_utc: 2026-04-15T00:00:00Z

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

## 4. Automaticke vynuceni
- Gate script: `scripts/verify_docs_guard.py`
- CI workflow: `.github/workflows/docs-guard.yml`
- PR checklist: `.github/pull_request_template.md`
- Odpovednost revieweru: `.github/CODEOWNERS`
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
