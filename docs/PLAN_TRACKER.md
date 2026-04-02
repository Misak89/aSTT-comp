# Plan Tracker (Single Source Of Truth)

Doc-Meta:
- owner: engineering
- status: active
- last_updated_utc: 2026-04-01T16:07:44Z
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

## 3. Kam co zapisovat (zavazne)
1. Aktivni plan + stav: `docs/PLAN_TRACKER.md`
2. Dlouhodoba roadmapa: `docs/tuning_v4_implementacni_plan.md`
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
