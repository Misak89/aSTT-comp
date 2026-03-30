# Plan Tracker (Single Source of Truth)

Last updated: 2026-03-30
Owner: engineering

Tento soubor je jedine misto, kde je videt aktualni plan a jeho stav.

## 1. Aktivni plan
- Plan ID: `real-mic-v4`
- Status: `in_progress`
- Scope:
  - real mic sekvence + fail-fast + metriky
  - rozhodovaci tuning v4

Plan dokumenty:
- strategie: `docs/mic_sequence_vibe_coding_short_2026-03-30.md`
- kriticke varianty: `docs/mic_sequence_rychla_vs_orchestracni_prestavba_2026-03-30.md`
- roadmapa: `docs/tuning_v4_implementacni_plan.md`
- tasky + stav: `docs/tuning_v4_tasky.md`

## 2. Stav milniku
| Milnik | Stav | Zdroj |
|---|---|---|
| M0 feasibility | in_progress | `docs/tuning_v4_tasky.md` |
| M1 mic adapter + metriky | in_progress | `docs/tuning_v4_tasky.md` |
| M2 protokol + UI | planned | `docs/tuning_v4_tasky.md` |
| M3 smart search + gate | planned | `docs/tuning_v4_tasky.md` |

## 3. Kam co zapisovat (zavazne)
1. Aktivni plan + stav: `docs/PLAN_TRACKER.md`
2. Dlouhodoba roadmapa: `docs/tuning_v4_implementacni_plan.md`
3. Operacni tasky: `docs/tuning_v4_tasky.md`
4. Co se realne provedlo: `docs/session_log.md`

## 4. Jak aktualizovat plan (povinna posloupnost)
1. Uprav roadmapu/tasky (`docs/tuning_v4_implementacni_plan.md`, `docs/tuning_v4_tasky.md`).
2. Aktualizuj tento tracker:
  - `Last updated`
  - `Status`
  - `Stav milniku`
3. Zapis zmenu do `docs/session_log.md` (co/proc/dopad).
4. Pokud je dopad na architekturu/provoz, aktualizuj i `docs/ARCHITECTURE.md` nebo `docs/RUNBOOK.md`.

## 5. Minimalni format noveho plan dokumentu
- Cil
- Scope (in/out)
- Milniky nebo tasky s ID
- Definition of Done
- Rizika a blokery
- Rozhodovaci metriky
- Datum/verze

## 6. Stavovy slovnik
- `planned`
- `in_progress`
- `blocked`
- `done`
- `dropped`
