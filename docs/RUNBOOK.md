# Runbook (Operations)

Doc-Meta:
- owner: engineering
- status: active
- last_updated_utc: 2026-04-03T11:54:31Z
- review_due_utc: 2026-04-15T00:00:00Z

## 1. Stabilni start webu
Pouzivej root skripty:
- `web-up.cmd` - stabilni start v aktualnim okne
- `web-up-build.cmd` - stejny start + frontend build
- `web-up-bg.cmd` - start do noveho okna
- `web-status.cmd` - health + proces na portu `8012`
- `web-down.cmd` - stop procesu na portu `8012`
- `web-restart.cmd` - stop + start

URL:
- aplikace: `http://127.0.0.1:8012/benchmark`
- health: `http://127.0.0.1:8012/api/health`

## 2. Bezna diagnostika
1. Over health endpoint.
2. Over listener na portu 8012.
3. Zkontroluj posledni backend log.
4. Pri stale chybe udelej `web-restart.cmd`.

## 2.1 Dashboard monitoring (health/processes)
- Process monitoring endpointy:
  - `GET /api/health`
  - `GET /api/health/processes?mode=fast|slow|full`
  - `POST /api/health/processes/cleanup-stale-pids`
- Policy:
  - `GET /api/health/processes` bez `mode` pouziva default lane `fast`.
  - `slow/full` pouzivej jen cilene (deep scan / incident diagnostika).
- Dashboard ma Performance Monitor panel:
  - globalni vypinac `Turn monitoring OFF (all)` (zachova nastaveni v localStorage),
  - rychly preset `Graphs only, no logging`,
  - `view`: `full`, `graphs_only`, `numbers_only`,
  - RAM/CPU kanal samostatne: `show graph on/off`, `logging on/off`.
- Log scale:
  - `Scale: log/linear` je per graf,
  - `log coef` je globalni (`1x`, `2x`, `5x`, `10x`).
- Process panel:
  - `Full scan now` pro okamzity full scan processu,
  - `Clean stale PID files` odstrani neplatne PID soubory a ztiší stale warning.
- Refactor note:
  - Frontend process scan cadence je interně přesunut do samostatného hooku `frontend/src/components/dashboard/useProcessScanCadence.ts`.
  - Externí kontrakt zůstává stejný: stejné endpointy `GET /api/health/processes?mode=fast|slow|full` a stejné UI ovládání panelu.
  - Backend scan pouziva kratkou TTL cache cmdline/exe metadat per PID pro snizeni overheadu.

## 2.2 Validace monitoringu (2026-04-02)
- `web-status.cmd`: health `UP`, backend na `127.0.0.1:8012`.
- `GET /api/health`: `200`, payload obsahuje `cpu_percent`.
- `GET /api/health/processes?mode=fast`: `200`.
- `GET /api/health/processes?mode=slow`: `200`.
- `GET /api/health/processes?mode=full`: `200`.
- Frontend build: `npm --prefix frontend run build` OK.
- Backend sanity: `.venv\Scripts\python -m compileall backend/app/routers/health.py` OK.

## 3. Kde jsou logy a runtime data
- Mic session: `runtime/mic_sessions/`
- Mic event stream: `runtime/logs/mic_sequence_events.jsonl`
- Tuning jobs: `runtime/tuning/`
- Network audit: `runtime/network_access/network_access_log.jsonl`

## 4. Offline provoz
- Pro strict offline nastav `ASTT_STRICT_OFFLINE=1`.
- Pro produkcni beh drzet offline inventory aktualni:
  - `docs/offline_network_inventory.md`
  - `python scripts/network_audit_report.py`

## 5. Incident pravidlo
Pri oprave incidentu:
1. zapsat co se stalo a fix do `docs/session_log.md`,
2. pokud se meni start/provozni postup, aktualizovat tento `docs/RUNBOOK.md`.

## 6. Physical validation policy (obecne, cross-domain)
Tato politika plati obecne pro testovani kodu, security, performance i provozu.

### 6.1 Kriticky pohled na jednoduche pravidlo
Jednoduche pravidlo "vzdy udelej physical test pri runtime zmene" je uzitecne, ale ma slabiny:
- je prilis siroke a muze zbytecne zpomalit flow,
- nemodeluje riziko ani dopad (low-risk vs release-critical),
- snadno micha proof-of-fix a release-gate do jednoho kroku,
- bez jasneho test typu vede k falesnym zaverum.

Proto se pouziva rizikove-rizeny 3-loop model.

### 6.2 Doporuceny 3-loop model
- Loop A (Fast deterministic): unit/integration/static-security checks na kazdy commit/PR.
- Loop B (Physical fidelity): kratky fyzicky test na realne ceste, kdyz je runtime tvrzeni.
- Loop C (Soak/adversarial): long-run, recovery, abuse/chaos scenare pro release/high-risk zmeny.

Minimalni pravidlo:
- A je vzdy povinne.
- B je povinne, pokud je runtime tvrzeni a simulace ma fidelity gap.
- C je povinne pro release gate a high-risk zmeny.

### 6.3 Kdy je physical test MUST
- tvrzeni smeruje na realny provoz (`funguje`, `stabilni`, `rychle`, `bezpecne`),
- a soucasne existuje aspon jedno z:
  - real I/O/device/timing/race zavislost,
  - runtime adapter orchestrace nebo procesni behavior,
  - UX interakce ovlivnujici chovani (drag/drop/resize/player/focus/clipboard),
  - security enforcement, ktere simulace neoveri dostatecne verne.

### 6.4 Plusy fyzicke validace
- odhaluje chyby, ktere simulace maskuje (timing, race, device/runtime),
- potvrzuje skutecny end-to-end tok,
- snizuje riziko falesneho PASS pred releasem.

### 6.5 Minusy a slaba mista
- vyssi casova a operacni narocnost,
- horsi determinismus (flaky vlivy prostredi),
- riziko "single-run overconfidence",
- riziko "confirmation bias" u manualnich overeni,
- neexistuje pokryti vsech HW/OS kombinaci.

### 6.6 Mitigace slabych mist
- kombinovat physical test s reprodukovatelnym scriptovanym testem (A + B),
- u release-critical tvrzeni delat minimalne 2 behy (cold/warm nebo odlisny usek/seed),
- povinne logovat skutecne parametry + artefakty (`run_id`, report, timestamp, prostredi),
- explicitne oznacit test typ a hranice tvrzeni (`sanity` vs `baseline` vs `capability` vs `release-gate`),
- pro urgent hotfix lze docasne odlozit C-loop jen s explicitnim "deferred physical validation" zapisem a terminem.

## 7. Dokumentacni rutina po dokonceni zmeny
1. Pridat strucny zaznam do `docs/session_log.md` (idealne pres `scripts/add_session_log_entry.py`).
2. V zmenenych core dokumentech aktualizovat `Doc-Meta.last_updated_utc`.
3. Overit guard lokalne:
   - `python scripts/verify_docs_guard.py --base HEAD~1 --head HEAD`
