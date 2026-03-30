# Mic Sequence: Vibe Coding (Short Tech Note)

## Cíl
Rychlá smyčka `run -> analyze -> tweak -> rerun` pro live mic STT, bez ruční forenziky logů.

## Aktuální problém
- Data jsou rozpadlá ve 3 místech (`mic_sessions/*.json`, `history/*_manual.json`, `mic_sequence_events.jsonl`).
- Sekvence se umí rozpadnout na pomalém modelu.
- Chybí jednotné trial statusy a jeden sequence report na token.

## Nejrychlejší kvalitní postup (3 úrovně)

### L1: Quick Win (1 den)
- Přidej agregaci na `sequence_token`:
  - `runtime/mic_sequences/<token>/report.json`
  - `runtime/mic_sequences/<token>/report.csv`
- API:
  - `GET /api/mic/sequences/{token}`
  - `GET /api/mic/sequences/{token}/export.csv`
- Trial status: `ok | borderline | too_slow | fail`.

### L2: Stabilita (2–4 dny)
- Přesuň fail-fast do backendu:
  - `drop_rate` limit
  - `first_word_wall_ms` limit
  - `queue_depth_peak_s` limit
- Trial hard deadline v rámci slotu (`speech+pause`) musí být vynucen backendem.
- Pomalý model nesmí rozbít další slot.

### L3: Smart Loop (1–2 týdny)
- Backend sequence orchestrator (state machine).
- Adaptivní tuning:
  - smoke shortlist
  - narrowed grid
  - confirm `n>=3`
- Auto decision report po každém sequence tokenu.

## Tech anchor (kde to napojit)
- FE sequence flow: `frontend/src/components/MicSession.tsx`
- Backend timing/events: `backend/app/services/mic_service.py`
- WS/API: `backend/app/routers/mic.py`
- Runtime roots: `backend/app/config.py`

## Definition of Done (minimum)
1. Jeden endpoint vrátí kompletní sequence report.
2. Každý trial má jednotný status + důvod.
3. Sekvence pokračuje i po failu modelu.
4. Po doběhu existuje automatické shrnutí top kandidátů.

## Vibe Coding Prompt (short)
```text
Implement a fast mic-sequence iteration loop:
1) Aggregate per-sequence-token report (JSON+CSV),
2) Add /api/mic/sequences/{token} and /export.csv,
3) Normalize trial statuses (ok/borderline/too_slow/fail),
4) Move fail-fast thresholds to backend (drop_rate, first_word_wall_ms, queue_depth_peak_s),
5) Keep slot timing deterministic so one slow model cannot break next slots.
Offline-only, backward-compatible with current runtime/mic_sessions.
```

