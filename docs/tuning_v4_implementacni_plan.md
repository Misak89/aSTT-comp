# Tuning v4 - implementacni plan (real mic: mobil -> mikrofon)

## Kontext
- V3 umi replay tuning, ale finalni cil je realny online mic provoz.
- V codebase existuje mic pipeline (`backend/app/services/mic_service.py`, `backend/app/routers/mic.py`), ale whisper je zatim blokovan.
- V4 dodela realny mic tuning + inteligentni search + rozhodovaci report.

Task breakdown: `docs/tuning_v4_tasky.md`.

## Kriticke blokery (stav 2026-03-29)
Puvodni blokery pro whisper mic path byly z velke casti odstraneny:
1. `whisper_cpp_*` uz neni v mic service hard blokovany.
2. Registry expose `supports_microphone=True` pro `whisper_cpp_small` a `whisper_cpp_large_v3_turbo`.
3. Real mic job umi end-to-end path pres mic session + tuning worker.

Zbyvajici kriticka prace:
1. Dotahnout plne sjednoceny event model vc. dedikovaneho `stats` eventu.
2. Dodelat CPU/temp p95 telemetrii pro release gate.
3. Dokoncit smart search planner + ETA confidence.
4. Potvrdit finalni doporuceni na vice realnych HW profilech.

## Scope v4
- Real mic benchmark/tuning: audio fyzicky z mobilu do mikrofonu PC.
- Transparentni metriky: first token, finalize delay, dropy, RTF, WER/soft-WER, RAM/CPU, stabilita.
- Inteligentni vyber kombinaci (ne cisty brute-force grid).

## V4.0 - Feasibility spike (must-have pred hlavni implementaci)
**Cil**
- Do 1-2 dnu overit, ze whisper mic path je realne provozovatelna a stabilni.

**Kod / soubory**
- `backend/app/services/mic_service.py`
- `packages/adapters/whisper_cpp_runner.py`
- `packages/adapters/_registry.py`

**Implementace**
- Minimalni end-to-end prototype pro `whisper_cpp_small` a `whisper_cpp_large_v3_turbo`.
- Vystup eventu `partial/final/stats` bez UI integrace.

**Definition of done**
- 10min real mic beh bez crash/lock.
- Prokazatelny first-token a final text u obou modelu.
- Pokud fail: stop v4, sepsat limiter a fallback plan.

## V4.1 - Mic adapter layer pro whisper.cpp
**Cil**
- Povolit `whisper_cpp_*` v mic session bez obchazeni pres offline batch flow.

**Kod / soubory**
- `backend/app/services/mic_service.py`
- `packages/benchmarks/runners/streaming_runner.py`
- `packages/adapters/whisper_cpp_runner.py`
- `packages/adapters/_registry.py`

**Implementace**
- Mic-compatible ingest vrstva pro whisper (chunk buffer + rolling finalize).
- Nahradit hard blokaci whisper adapteru capability checkem.
- Sjednotit event model (`partial`, `final`, `stats`) mezi whisper/vosk/sherpa.

**Definition of done**
- Mic session lze zalozit a dokoncit pro `whisper_cpp_small` a `whisper_cpp_large_v3_turbo`.
- Backend vraci prubezne partial texty a final text bez padu session.

## V4.2 - Real mic metriky a telemetrie
**Cil**
- Merit to, co replay neumi: realnou odezvu na mikrofonu.

**Kod / soubory**
- `backend/app/services/mic_service.py`
- `backend/app/models/tuning.py`
- `scripts/tuning_worker.py`
- `packages/benchmarks/runners/host_telemetry.py`

**Implementace**
- Pridat metriky:
  - `first_token_ms_p50/p95`
  - `segment_finalize_ms_p50/p95`
  - `drop_rate`
  - `session_resets`
  - `worker_rss_peak_mb`
  - `cpu_p95`, `temp_p95` (kde je dostupne)
- Ukladat metriky per trial i per video do `status.json` + final reportu.

**Definition of done**
- Kazdy mic trial ma latence + stabilita + RAM/CPU metriky.
- Pri chybe je explicitni reason code (`timeout`, `buffer_overrun`, `no_tokens`, ...).

## V4.3 - Mic protokol + kalibrace (reprodukovatelnost)
**Cil**
- Zabranit falesnemu srovnani kvuli rozdilne hlasitosti/hluku.

**Kod / soubory**
- `docs/tuning_v4_mic_protocol.md` (novy)
- `scripts/mic_calibration_check.py` (novy)
- `frontend/src/pages/TuningPage.tsx`
- `backend/app/models/tuning.py`

**Implementace**
- Povinna metadata trialu:
  - vzdalenost mobil-mic (cm)
  - hlasitost mobilu (%)
  - hlasitost input device (%)
  - prostredi (`quiet`, `office_noise`)
  - device note (mobil/mikrofon)
- Povinna pre-run kalibrace:
  - RMS rozsah (napr. -24 az -12 dBFS),
  - clipping rate (napr. <0.1%),
  - noise floor check.
- Job bez kalibrace/protokolu nelze spustit v `real_mic_mode`.

**Definition of done**
- Kazdy run ma ulozeny kalibracni vysledek + protokol.
- Pri nevalidni kalibraci run fail-fast pred trialy.

## V4.4 - Inteligentni planner (smart search)
**Cil**
- Zkratit dobu hledani a nepalit cas na evidentne slabe kombinace.

**Kod / soubory**
- `scripts/tuning_worker.py`
- `scripts/tuning_smart_planner.py` (novy)
- `backend/app/services/tuning_service.py`

**Implementace**
- Pridat `search_mode: grid|smart`.
- Faze:
  - gate: kratky smoke + hard prune,
  - search: Successive Halving (v1),
  - confirm: `n>=3`, top2 `n>=5`.
- Score (normalizovane):
  - `WER_soft`, `RTF`, `first_token_ms`, `segment_finalize_ms_p95`, `perceived_delay_s`, `RAM_peak`, `stability_penalty`.
- Logovat `selection_reason` a `pruned_reason`.

**Definition of done**
- Smart mode ma mene trialu nez full grid a drzi kvalitu top kandidatu.
- Rozhodnuti je auditovatelne (duvod vyberu/vyrazeni).

## V4.5 - UI pro real mic tuning + ETA + nejistota ETA
**Cil**
- Udelat beh transparentni a citelny i u dlouhych jobu.

**Kod / soubory**
- `frontend/src/pages/TuningPage.tsx`
- `frontend/src/types/index.ts`
- `frontend/src/api/client.ts`

**Implementace**
- Volba rezimu: `Replay` vs `Real mic`.
- Zobrazit:
  - elapsed + ETA,
  - ETA confidence (`low/medium/high`) v prvnich fazich,
  - aktualni fazi (`gate/search/confirm`),
  - prune count,
  - trial end time (`HH:MM:SS`),
  - top kandidaty live.
- Zachovat kompaktnost tabulky (zadne nekonecne roztazeni doprava).

**Definition of done**
- Uzivatel vidi prubeh, ETA i nejistotu ETA a duvod rozhodnuti.
- UI zustava citelne i pri dlouhem jobu.

## V4.6 - Verifikace, metodika, release gate
**Cil**
- Potvrdit, ze v4 dava rozhodovaci data pro realny online mic provoz.

**Kod / soubory**
- `scripts/tuning_smoke_validate.py`
- `scripts/tuning_soak_validate.py`
- `scripts/tuning_hw_matrix_report.py`
- `scripts/tuning_decision_report.py`
- `docs/tuning_v4_validation_report_template.md` (novy)

**Implementace**
- Validace:
  - smoke (2 CZ videa),
  - decision run (smart search),
  - soak 30-60 min.
- HW matice: `weak_office`, `mid_office`, `strong_office` na realnych strojich.
- Reproducibility:
  - finalisti `n>=3`,
  - top2 `n>=5`,
  - CI + variance.
- Randomizace poradi trialu + warmup trial mimo scoring.

**Definition of done**
- Finalni doporuceni model+parametry pro kazdy HW profil.
- Report obsahuje rizika, limity, confidence a randomization seed.

## Tvrde rozhodovaci prahy per HW profil

`weak_office`
- `RTF <= 1.15`
- `first_token_ms_p50 <= 2200`
- `segment_finalize_ms_p95 <= 4500`
- `drop_rate <= 1.5%`
- `ram_peak_mb <= 2200`

`mid_office`
- `RTF <= 1.00`
- `first_token_ms_p50 <= 1500`
- `segment_finalize_ms_p95 <= 3200`
- `drop_rate <= 1.0%`
- `ram_peak_mb <= 3000`

`strong_office`
- `RTF <= 0.90`
- `first_token_ms_p50 <= 1000`
- `segment_finalize_ms_p95 <= 2500`
- `drop_rate <= 0.5%`
- `ram_peak_mb <= 4200`

Pozn.: Pokud kandidat nesplni hard prahy, nema jit do finalniho doporuceni bez vyjimky s jasnym duvodem.

## Priorita a poradi realizace
1. `v4.0` (feasibility gate)
2. `v4.1` (whisper mic adapter)
3. `v4.2` (real mic metriky)
4. `v4.3` (protokol + kalibrace)
5. `v4.4` (smart search)
6. `v4.5` (UI)
7. `v4.6` (release gate)

## Milniky
- M0: Feasibility potvrzena (`v4.0`)
- M1: Whisper mic session end-to-end (`v4.1 + v4.2`)
- M2: Real mic job spustitelny z UI s protokolem (`v4.3 + v4.5`)
- M3: Smart search + finalni validace (`v4.4 + v4.6`)

## Hlavni rizika
1. Whisper mic latence muze byt nestabilni pri malych chuncich.
2. Pseudo-online fallback nesmi byt oznacen jako real mic.
3. Bez kalibrace zvuku budou data neporovnatelna.
4. Bez randomizace poradi hrozi bias kvuli warmup/thermal efektu.

## Globalni akceptacni kriterium v4
- Z UI jde spustit `real_mic_mode` pro `whisper_cpp_small` a `whisper_cpp_large_v3_turbo`.
- Finalni report da jasne doporuceni pro live mic na starsim office HW.
- Data jsou reprodukovatelna, auditovatelna a rozhodovaci.
