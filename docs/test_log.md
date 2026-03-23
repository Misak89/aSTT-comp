# Test log

Automaticky aktualizováno cron monitorem každých 10 minut.

## Poslední úspěšný test run

- Datum: 2026-03-23 04:15 UTC
- Python: 3.11.9 (aSTT-comparison .venv)
- Testy: 19/19 PASSED
- Backend: UP (port 8012 odpovídá)

## Historie

| Datum | Výsledek | Poznámka |
| ----- | -------- | -------- |
| 2026-03-23 04:15 | 19/19 PASS | testy OK; backend UP; chunk_seconds obnoven (grupování segmentů replay), video autoplay+mute |
| 2026-03-23 02:34 | 19/19 PASS | testy OK; backend UP |
| 2026-03-23 03:15 | 19/19 PASS | testy OK; backend UP; přepis s časovými značkami [MM:SS], odstraněno pole Délka chunku |
| 2026-03-23 02:06 | 19/19 PASS | testy OK; backend UP |
| 2026-03-23 01:33 | 19/19 PASS | testy OK; backend UP; fix whisper hallucination: single-batch + timestamp replay (žádné chunky) |
| 2026-03-22 23:34 | 19/19 PASS | testy OK; backend UP; oprava scroll LiveJobPanel (scrollTop místo scrollIntoView) |
| 2026-03-22 23:01 | 19/19 PASS | testy OK; backend UP; chunk_seconds UI + milníky z audio pozice + frontend rebuild |
| 2026-03-22 22:31 | 19/19 PASS | testy OK; backend UP |
| 2026-03-22 22:01 | 19/19 PASS | testy OK; backend UP |
| 2026-03-22 21:31 | 19/19 PASS | testy OK; backend UP |
| 2026-03-22 21:01 | 19/19 PASS | testy OK; backend UP |
| 2026-03-22 20:31 | 19/19 PASS | testy OK; backend UP |
| 2026-03-22 20:01 | 19/19 PASS | testy OK; backend UP |
| 2026-03-22 19:31 | 19/19 PASS | testy OK; backend UP |
| 2026-03-22 19:01 | 19/19 PASS | testy OK; backend UP |
| 2026-03-22 18:31 | 19/19 PASS | testy OK; backend UP |
| 2026-03-22 18:01 | 19/19 PASS | testy OK; backend UP |
| 2026-03-22 17:31 | 19/19 PASS | testy OK; backend UP |
| 2026-03-22 17:01 | 19/19 PASS | testy OK; backend UP |
| 2026-03-22 16:31 | 19/19 PASS | testy OK; backend UP |
| 2026-03-22 16:01 | 19/19 PASS | testy OK; backend UP; fix 404 Výsledky: benchmark_matrix.json, chunk_metrics v ResultsPage, frontend rebuild |
| 2026-03-22 15:31 | 19/19 PASS | testy OK; backend UP; diskuse metrik: WER/CER, RTF, latence, HW, audio kvalita, chunk statistiky |
| 2026-03-22 15:01 | 19/19 PASS | testy OK; backend UP; izolované chunky [0→15s, 15→30s, ...] + měření zpoždění per milník |
| 2026-03-22 14:32 | 19/19 PASS | testy OK; backend UP; diskuse: RTF metodologie, whisper min. 30s delay, vosk/sherpa pro ≤10s |
| 2026-03-22 02:41 | 19/19 PASS | testy OK; backend UP; analýza old vs new projektu — whisper hallucination z 15s chunků |
| 2026-03-22 00:40 | 19/19 PASS | testy OK; backend UP; chunked whisper 15s, live transcript v progress.json, YouTube bez autoplay |
| 2026-03-22 02:00 | 19/19 PASS | testy OK; backend UP |
| 2026-03-22 01:50 | 19/19 PASS | testy OK; backend UP; přepis funkční: transcript len=1405, evaluation_mode=streaming ✓ |
| 2026-03-22 01:40 | 19/19 PASS | testy OK; backend DOWN; čeká na restart backendu pro aktivaci oprav přepisu |
| 2026-03-22 01:30 | 19/19 PASS | testy OK; backend DOWN; fix: frontend rebuild, retry přepisu 3×, tlačítko ↻ Načíst, výchozí mód streaming |
| 2026-03-22 01:20 | 19/19 PASS | testy OK; backend DOWN; fix: výchozí mód evaluace změněn na streaming |
| 2026-03-22 01:10 | 19/19 PASS | testy OK; backend DOWN; fix: evaluation_mode v BenchmarkJobStatus + správná zpráva pro synthetic mód |
| 2026-03-22 01:00 | 19/19 PASS | testy OK; backend DOWN |
| 2026-03-22 00:50 | 19/19 PASS | testy OK; backend DOWN |
| 2026-03-22 00:40 | 19/19 PASS | testy OK; backend DOWN |
| 2026-03-22 00:30 | 19/19 PASS | testy OK; backend DOWN |
| 2026-03-22 00:20 | 19/19 PASS | testy OK; backend DOWN |
| 2026-03-22 00:10 | 19/19 PASS | testy OK; backend DOWN |
| 2026-03-22 00:00 | 19/19 PASS | testy OK; backend DOWN |
| 2026-03-21 23:50 | 19/19 PASS | testy OK; backend DOWN |
| 2026-03-21 23:40 | 19/19 PASS | testy OK; backend DOWN |
| 2026-03-21 23:30 | 19/19 PASS | testy OK; backend DOWN |
| 2026-03-21 23:20 | 19/19 PASS | testy OK; backend DOWN |
| 2026-03-21 23:10 | 19/19 PASS | testy OK; backend DOWN |
| 2026-03-21 23:00 | 19/19 PASS | testy OK; backend DOWN |
| 2026-03-21 22:50 | 19/19 PASS | testy OK; backend DOWN |
| 2026-03-21 22:40 | 19/19 PASS | testy OK; backend DOWN |
| 2026-03-21 22:30 | 19/19 PASS | testy OK; backend DOWN |
| 2026-03-21 22:20 | 19/19 PASS | testy OK; backend DOWN |
| 2026-03-21 22:10 | 19/19 PASS | testy OK; backend DOWN |
| 2026-03-21 22:00 | 19/19 PASS | testy OK; backend DOWN |
| 2026-03-21 21:50 | 19/19 PASS | testy OK; backend DOWN |
| 2026-03-21 21:40 | 19/19 PASS | testy OK; backend DOWN |
| 2026-03-21 21:30 | 19/19 PASS | testy OK; backend DOWN; fix: transcript JSON path + toggle tlačítko |
| 2026-03-21 20:50 | 19/19 PASS | testy OK; backend DOWN |
| 2026-03-21 20:40 | 19/19 PASS | testy OK; backend DOWN |
| 2026-03-21 20:30 | 19/19 PASS | testy OK; backend DOWN |
| 2026-03-21 20:20 | 19/19 PASS | testy OK; backend DOWN |
| 2026-03-21 20:10 | 19/19 PASS | testy OK; backend DOWN |
| 2026-03-21 20:00 | 19/19 PASS | testy OK; backend DOWN |
| 2026-03-21 19:50 | 19/19 PASS | testy OK; backend DOWN |
| 2026-03-21 19:40 | 19/19 PASS | testy OK; backend DOWN; fix: CPU 0%, titulky, elapsed čas, HW hodnoty, filtr výsledků |
| 2026-03-21 19:00 | 19/19 PASS | testy OK; backend DOWN |
| 2026-03-21 18:50 | 19/19 PASS | testy OK; backend DOWN |
| 2026-03-21 18:40 | 19/19 PASS | testy OK; backend DOWN |
| 2026-03-21 18:30 | 19/19 PASS | testy OK; backend DOWN |
| 2026-03-21 18:20 | 19/19 PASS | testy OK; backend DOWN |
| 2026-03-21 18:10 | 19/19 PASS | testy OK; backend DOWN |
| 2026-03-21 18:00 | 19/19 PASS | testy OK; backend DOWN |
| 2026-03-21 17:50 | 19/19 PASS | testy OK; backend DOWN |
| 2026-03-21 17:40 | 19/19 PASS | testy OK; backend DOWN |
| 2026-03-21 17:30 | 19/19 PASS | testy OK; backend DOWN; commit: LiveJobPanel Req1–7, per-model params, duration display |
| 2026-03-21 16:20 | 19/19 PASS | testy OK; backend UP |
| 2026-03-21 16:10 | 19/19 PASS | testy OK; backend UP |
| 2026-03-21 16:00 | 19/19 PASS | testy OK; backend UP |
| 2026-03-21 15:50 | 19/19 PASS | testy OK; backend UP |
| 2026-03-21 15:40 | 19/19 PASS | testy OK; backend UP |
| 2026-03-21 15:30 | 19/19 PASS | testy OK; backend UP |
| 2026-03-21 15:20 | 19/19 PASS | testy OK; backend UP |
| 2026-03-21 15:10 | 19/19 PASS | testy OK; backend UP |
| 2026-03-21 15:01 | 19/19 PASS | testy OK; backend UP |
| 2026-03-21 14:30 | 19/19 PASS | testy OK; backend UP |
| 2026-03-21 14:20 | 19/19 PASS | testy OK; backend UP |
| 2026-03-21 14:19 | 19/19 PASS | testy OK; backend UP |
| 2026-03-21 14:00 | 19/19 PASS | testy OK; backend UP |
| 2026-03-21 13:54 | 19/19 PASS | testy OK; backend UP |
| 2026-03-21 13:50 | 19/19 PASS | streaming benchmark OK: RTF=0.472, CPU=32%, whisper_cpp_small, 30s clip R3BsjbDtWrY |
| 2026-03-21 13:15 | 19/19 PASS | testy OK; backend UP |
| 2026-03-21 13:03 | 19/19 PASS | testy OK; backend UP |
| 2026-03-21 11:41 | 19/19 PASS | testy OK; backend UP |
| 2026-03-21 11:40 | 19/19 PASS | testy OK; backend UP |
| 2026-03-21 02:55 | 19/19 PASS | testy OK; backend UP; první benchmark run: WER=15.5%, RTF=1.115 |
| 2026-03-21 00:25 | 19/19 PASS | testy OK; backend DOWN |
| 2026-03-21 00:00 | 19/19 PASS | testy OK; backend DOWN — stará instance před restartem |
| 2026-03-20 22:12 | 19/19 PASS | cron monitor run |
| 2026-03-20 22:10 | 19/19 PASS | manuální run: všechny fáze OK, backend OK, frontend dev OK |
| 2026-03-20 22:20 | build OK | Fáze 4: React frontend sestaven, dist/ OK |
| 2026-03-20 22:00 | 19/19 PASS | Fáze 3 hotova: subprocess, scénáře, streaming sim |
| 2026-03-20 21:45 | 11/11 PASS | cron monitor run |
| 2026-03-20 21:35 | 11/11 PASS | první run, nový .venv Python 3.13 |
