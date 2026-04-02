# Mic Sekvence: Rychlá Iterace vs Velká Přestavba (2026-03-30)

## Cíl
Mít rychlou smyčku `spustit -> analyzovat -> upravit -> znovu spustit` pro live mic STT, aby šlo najít:
- použitelné modely,
- jejich nejnižší stabilní latenci,
- parametry, které drží kvalitu bez kolapsu (drop/backpressure).

## Kde je klíčová logika dnes (konkrétně)

### Frontend orchestrace sekvence
- Plánování slotů a přepínání modelů: `frontend/src/components/MicSession.tsx:1683` (advance), `:1700` (target start).
- Stop pravidla (slot/hard cap/silence): `frontend/src/components/MicSession.tsx:1586`, `:1613`, `:1628`.
- Start trialu + sequence meta v params: `frontend/src/components/MicSession.tsx:1070`, `:1105`.
- Uložení výsledků a metrik: `frontend/src/components/MicSession.tsx:957`, `:1008`.
- WS close handling: `frontend/src/components/MicSession.tsx:1317`.

### Backend mic pipeline
- Session storage roots: `backend/app/config.py:10`, `:20`.
- Event log soubor: `backend/app/services/mic_service.py:43`.
- Start/stop timing sekvence (`sequence_timing`): `backend/app/services/mic_service.py:224`, `:304`.
- Zpracování chunku + backpressure drop: `backend/app/services/mic_service.py:1292`, `:1354`.
- Finalizace a závěrečné metriky: `backend/app/services/mic_service.py:1461`.
- Transport event logging (ws_*): `backend/app/services/mic_service.py:495`.

### WS router a API
- WS endpoint: `backend/app/routers/mic.py:333`.
- Transport event body (`ws_accept`, `ws_disconnect`, `ws_final_sent`): `backend/app/routers/mic.py:346`, `:423`, `:440`.
- Session state endpoint: `backend/app/routers/mic.py:273`.
- Manual records API: `backend/app/routers/mic.py:165`, `:178`, `:196`.

### Data struktura (runtime)
- Session snapshots: `runtime/mic_sessions/mic_<id>.json`
- Manual records history: `runtime/mic_sessions/history/*_manual.json`
- Session event stream: `runtime/logs/mic_sequence_events.jsonl`

## Proč dnes není iterace dost rychlá
- Analýza je distribuovaná do 3 zdrojů (snapshot/manual/events), chybí jeden agregovaný sequence report.
- Část degradovaných běhů jde přes `web_mic_auto_error`, část přes čistý `final`; vyhodnocení není jednotné.
- Není centrální sequence stav (`completed/aborted` + důvod) a průběžné KPI v jedné tabulce.
- Fail-fast existuje částečně v UI, ale ne jako konzistentní backend rozhodovací vrstva pro všechny stavy.

## Varianta A: Rychlá kvalitní úprava (doporučeno)

### Rozsah
1. Agregovaný sequence report na token:
   - `runtime/mic_sequences/<token>/report.json` + `report.csv`
2. API:
   - `GET /api/mic/sequences/{token}`
   - `GET /api/mic/sequences/{token}/export.csv`
3. Jednotná klasifikace trialu:
   - `ok | borderline | fail | too_slow_for_slot`
4. Fail-fast pravidla:
   - např. `drop_rate > 0.35`, `first_word_wall_ms > 10s`, `queue_depth_peak_s > 3`
5. UI panel „Sekvenční report“ (bez velkého redesignu).

### Náročnost
- Implementace: ~8-14 hodin čistého času.
- Riziko regresí: nízké až střední (lokální změny, žádný velký zásah do architektury).
- Přínos: okamžitě kratší cyklus ladění a rychlé rozhodování.

## Varianta B: Velká přestavba orchestrace (kvalitnější, pomalejší)

### Co to znamená konkrétně
1. Přesun řízení sekvence z frontendu do backendu (server-side scheduler).
2. Stavový automat sequence jobu (FSM):
   - `pending -> running -> waiting_slot -> stopping_trial -> completed/aborted`
3. Per-token command/event log jako jediný source of truth.
4. WS pouze pro stream audia/textu; orchestrace oddělená od transportu.
5. Resume/recovery po pádu browseru nebo WS.
6. Samostatný sequence evaluator (ranker) s doporučením parametrů.

### Náročnost
- Implementace: ~60-100 hodin (cca 2-4 týdny podle paralelizace).
- Riziko regresí: střední až vysoké (dotkne se FE, routeru, lifecycle, persistence, testů).
- Přínos: robustnější dlouhodobě, ale pomalejší dodání hodnoty teď.

## Kritické srovnání (proč teď A, ne B)
- A je nejrychlejší cesta k cíli „mít data a rychle iterovat“.
- B je architektonicky lepší dlouhodobě, ale zpozdí sběr rozhodovacích dat.
- V aktuální fázi (hledání použitelnosti modelů a parametrů) je čas na data důležitější než plná orchestrátorová čistota.

## Stručný prompt pro další iteraci
```
Implementuj rapid mic-sequence loop:
1) agregovaný report per sequence token (JSON+CSV),
2) API endpointy /api/mic/sequences/{token} a /export.csv,
3) jednotnou klasifikaci trialu (ok/borderline/fail/too_slow_for_slot),
4) fail-fast pravidla (drop_rate, first_word_wall_ms, queue_depth_peak_s),
5) UI sekční tabulku seřazenou podle seq index.
Zachovej offline režim a kompatibilitu s current runtime/mic_sessions.
```

