# aSTT-comp
`web-up.cmd`
Open: `http://127.0.0.1:8012/benchmark`
`web-status.cmd` (v druhem okne)
`web-down.cmd`

## Povinne dokumenty (cti v tomto poradi)
1. [CONTRIBUTING.md](./CONTRIBUTING.md) - zavazna pravidla prace v repu
2. [docs/ARCHITECTURE.md](./docs/ARCHITECTURE.md) - architektura a logika
3. [docs/RUNBOOK.md](./docs/RUNBOOK.md) - provozni navody a incidenty
4. [docs/PLAN_TRACKER.md](./docs/PLAN_TRACKER.md) - kam se zapisuje aktivni plan a jeho stav
5. [docs/session_log.md](./docs/session_log.md) - historie implementacnich session

## Aktivni pokracovani (roadmapa)
- [docs/mic_sequence_vibe_coding_short_2026-03-30.md](./docs/mic_sequence_vibe_coding_short_2026-03-30.md)
- [docs/mic_sequence_rychla_vs_orchestracni_prestavba_2026-03-30.md](./docs/mic_sequence_rychla_vs_orchestracni_prestavba_2026-03-30.md)
- [docs/tuning_v4_implementacni_plan.md](./docs/tuning_v4_implementacni_plan.md)
- [docs/tuning_v4_tasky.md](./docs/tuning_v4_tasky.md)

## Zavazna pravidla dokumentace
- Kazda zmena kodu musi mit zapis v `docs/session_log.md`.
- Zmena architektury (`backend/app/services`, `backend/app/routers`, `packages/*`) musi mit update `docs/ARCHITECTURE.md`.
- Zmena provozu/startu (`web-*.cmd`, `start_web_app*.cmd`, health/preflight skripty) musi mit update `docs/RUNBOOK.md`.
- Plan/roadmapa se aktualizuje pres `docs/PLAN_TRACKER.md` (single source of truth).
- Push se dela pouze po explicitnim souhlasu maintainera.

Kontrola je v CI (`.github/workflows/docs-guard.yml`) a v PR checklistu (`.github/pull_request_template.md`).

## Stabilni spousteni webu
- `web-up.cmd`: doporuceny stabilni start v aktualnim okne (backend bezi, okno nezavirat).
- `web-up-build.cmd`: stejny stabilni start, ale predem vynuti frontend build.
- `web-up-bg.cmd`: volitelny start do noveho okna `aSTT-web`.
- `web-status.cmd`: zobrazi health a proces na portu 8012.
- `web-down.cmd`: ukonci proces, ktery posloucha na portu 8012.
- `web-restart.cmd`: stop + start v jednom kroku.

## Logika
- Hlavni produkcni vstup jsou root `web-*.cmd` soubory.
- Backend bezi na `127.0.0.1:8012`, frontend se servira z backendu.
- Pokud je potreba plny rebuild frontendu, pouzij `web-up-build.cmd`.
