# Session log — záznamy vývojových session

---

## Session 2026-03-21 (00:00–00:25 UTC)

### Co bylo hotovo v předchozích session (Fáze 1–4)
- Backend FastAPI (port 8012): Library, Benchmark, Runs routery
- Frontend React 18 + Vite + TypeScript + Tailwind: Knihovna, Benchmark, Výsledky stránky
- 6 YouTube videí s CZ titulky v knihovně (vše `subtitles_local: True`)
- 19 smoke testů (vše PASS)
- `npm run build` → `dist/` OK (606 KB bundle)
- První git commit: `00fe66b`

---

## Session 2026-03-21 (nová — dodělání projektu)

### Datum/čas: 2026-03-21, ~22:00–00:25 UTC

### Co bylo implementováno

#### Backend — nové endpointy a modely

| Soubor | Změna |
|--------|-------|
| `backend/app/config.py` | Přidány `MODEL_STORE_ROOT`, `MODELS_LOG_ROOT`; zajištěno vytvoření adresářů |
| `backend/app/models/benchmark.py` | Přidán model `LiveJobProgress` (percent, message, hw_series) |
| `backend/app/models/models.py` | Nový soubor: `ModelEvent`, `ModelLog`, `ModelStatus` (Pydantic v2) |
| `backend/app/services/benchmark_service.py` | `_job_hw_series` dict (posledních 120 HW vzorků per job); nová funkce `get_live_progress()`; `video_ids` v `BenchmarkJobStatus`; opraveny názvy parametrů pro volání `run_benchmark_matrix` |
| `backend/app/services/models_service.py` | Nový soubor: CRUD pro model logy, detekce nainstalovaných modelů přes `model_store_root` |
| `backend/app/routers/benchmark.py` | Přidán endpoint `GET /api/benchmark/jobs/{job_id}/live` |
| `backend/app/routers/models.py` | Nový soubor: `GET/POST/DELETE /api/models`, `/install`, `/note` |
| `backend/app/main.py` | Registrován `models.router` |

#### Frontend — nové komponenty a stránky

| Soubor | Popis |
|--------|-------|
| `frontend/src/components/LiveJobPanel.tsx` | Live panel pro běžící benchmark: progress bar (percent), CPU % graf, RAM MB graf (Recharts LineChart), YouTube iframe embed prvního videa. Polluje `/api/benchmark/jobs/{id}/live` každou 1s. |
| `frontend/src/components/WerDiff.tsx` | Vizualizace rozdílů přepis vs. reference: word-level Levenshtein diff, barevné kódování (zelená = správně, červená = substituce, šedá = vynecháno, oranžová = navíc). |
| `frontend/src/pages/ModelsPage.tsx` | Přehled modelů: installed/not installed, velikost MB, timeline install/uninstall/note eventů, inline přidání poznámky. |
| `frontend/src/pages/BenchmarkPage.tsx` | Doplněno: `LiveJobPanel` se zobrazí automaticky pro každý running/pending job. |
| `frontend/src/pages/ResultsPage.tsx` | Doplněno: tlačítko `▼ diff` v tabulce výsledků rozbalí `WerDiff` pro každý zdroj. |
| `frontend/src/App.tsx` | Přidána route `/models`. |
| `frontend/src/components/Layout.tsx` | Přidán nav link **Modely**. |
| `frontend/src/types/index.ts` | Přidány typy: `LiveJobProgress`, `HwSample`, `ModelEvent`, `ModelStatus`, `video_ids` v `BenchmarkJobStatus`. |
| `frontend/src/api/client.ts` | Přidány: `api.benchmark.getLive()`, `api.models.*` (list, get, recordInstall, recordUninstall, addNote). |

#### Scripty — opravy

| Soubor | Změna |
|--------|-------|
| `scripts/benchmark_worker.py` | Opraveny nesprávné názvy parametrů: `runs_root` → `run_root`, `clip_seed` → `clip_selection_seed`, `clip_strategy` → `clip_selection_strategy`. Přidána konverze URL strings → `SourceEntry` objekty via `parse_source_entries`. Přidán `model_store_root`. Default `evaluation_mode` změněn na `"synthetic"`. |

#### Model — whisper.cpp small

- **Způsob instalace**: Windows Junction (symlink bez kopírování dat) na model store starého projektu
  - `runtime/model_store/whisper_cpp_small` → `aSTT-comparison/.runtime/model_store/whisper_cpp_small` (465 MB, `ggml-small.bin`)
  - `runtime/model_store/whisper_cpp_runtime` → `aSTT-comparison/.runtime/model_store/whisper_cpp_runtime` (`whisper-cli.exe`)
- Adaptér `whisper_cpp_runner.py` detekuje binary a model automaticky
- Log instalace: zaznamenat přes `POST /api/models/whisper_cpp_small/install`

#### Git

- Commit `00fe66b`: "Fáze 1-4: kompletní základ aSTT-comp" — 77 souborů, 12 849 řádků

### Build výsledek
```
dist/index.html          0.39 kB
dist/assets/index.css   14.45 kB  (gzip: 3.33 kB)
dist/assets/index.js   606.42 kB  (gzip: 174 kB)
Varování: chunk > 500 kB (recharts) — nezávadné, lze řešit lazy importem
```

### Testy
- 19/19 smoke testů PASS (Python 3.13.2, .venv)

### Backend restart — problém a řešení
**Problém**: Stará instance uvicorn (z předchozí session) běžela s `--reload` jako 2 procesy (reloader + worker) na portu 8012. Opakované `Stop-Process` zabíjelo worker, ale reloader okamžitě spawnoval nový. Zdánlivě mrtvé procesy (125780, 144564) stále držely TCP LISTEN socket.

**Řešení**: `Get-Process python | Stop-Process -Force` — zabití VŠECH Python procesů najednou. Poté čistý start.

**Aktuální stav**: Backend běží (PID reloader 167340, worker 166136), 19 endpointů OK.

---

## Co zbývá (po session 2026-03-21 00:25)

| Priorita | Úkol |
|----------|------|
| Vysoká | Spustit první reálný benchmark (synthetic mode funguje, real mode potřebuje stažené audio) |
| Střední | Stáhnout audio z YouTube pro real mode (`yt-dlp`) |
| Nízká | Lazy import recharts (zmenšení bundle) |
| Nízká | Testy pro nové routery (models, live endpoint) |

---

## Session 2026-03-21 (~02:30–03:00 UTC)

### Co bylo implementováno

#### Modely — junctions ze starého projektu

Všechny modely propojeny jako Windows Junctions (žádné kopírování dat) z `aSTT-comparison/.runtime/model_store/`:

| Model | Velikost | Stav |
|-------|----------|------|
| `whisper_cpp_small` | 465 MB | ✅ (z minulé session) |
| `whisper_cpp_runtime` | ~5 MB | ✅ (z minulé session, whisper-cli.exe) |
| `whisper_cpp_base` | 141 MB | ✅ přidáno |
| `whisper_cpp_large_v3` | 2952 MB | ✅ přidáno — nejlepší pro češtinu |
| `vosk_small_cs_0_4` | 67 MB | ✅ přidáno — dedikovaný CZ model |
| `qwen3_asr_0_6b` | 1794 MB | ✅ přidáno — LLM-based ASR |
| `sherpa_onnx_small` | 252 MB | ✅ přidáno |

Příkaz pro junction: `cmd /c mklink /J <link> <target>`

#### První benchmark run — ověření pipeline

- **Run ID**: `run_20260321_025049`
- **Model**: whisper_cpp_small, setting: balanced
- **Video**: R3BsjbDtWrY (PlayStation VR2), 30s clip, seed=42
- **Evaluation mode**: synthetic

**Výsledky:**

| Metrika | Hodnota | Interpretace |
|---------|---------|--------------|
| WER | 15.5% | Přijatelné pro balanced |
| CER | 10.6% | Dobré |
| Latence | 1467ms | Vysoká — model přepisuje celý chunk najednou |
| **RTF** | **1.115** | ⚠️ > 1.0 — nestíhá real-time! |
| CPU | 39% | Střední zátěž |
| RAM | 2076 MB | 2 GB — normální pro whisper.cpp |

**Závěr**: whisper_cpp_small s balanced nastavením nestíhá real-time (RTF 1.115 > 1.0). Whisper.cpp base bude rychlejší, large-v3 pomalejší ale přesnější.

#### Oprava benchmark_worker.py

Opraveny názvy parametrů při volání `run_benchmark_matrix()` a přidána konverze URL → SourceEntry.

#### .gitignore — přidáno `.claude/`

Memory soubory Claude Code nepotřebují být v git historii.

### Testy
- 19/19 smoke testů PASS

### Co zbývá

| Priorita | Úkol |
|----------|------|
| Vysoká | Stáhnout audio z YouTube pro real mode (`yt-dlp`) a spustit reálný benchmark |
| Střední | Porovnat modely: whisper_cpp_base vs small vs large_v3 (RTF, WER) |
| Střední | Přidat qwen3_asr_1_7b junction (4.5 GB) |
| Nízká | Lazy import recharts |
| Nízká | Testy pro models router a live endpoint |

---

## Session 2026-03-27 (03:54–13:00 CET)

### Kontext
Pokračování tuningu whisper.cpp. Tuning job `tune_20260327_025443_6200bb` spuštěn v 03:54 CET, dokončen ~13:00 CET (9 h reálného času).

### Implementováno
- **WER soft metrika** — `_char_levenshtein` + `_is_soft_sub` (threshold 0.40) v `packages/benchmarks/metrics/text_metrics.py`; `word_error_rate_soft` v `tuning_worker.py`; sloupec v UI
- **clip_start_seconds** — manuální override startu klipu (vedle clip_seed) v UI i backendu
- **evaluation_mode** — heuristic / heuristic+llm stub (Ollama zatím ne)
- **Initial prompt UI oprava** — template picker místo červeného error bloku (Vite nebežel → stale bundle)
- **Filtry v TuningJobDetail** — threads, "pouze live mic", RAM limit (MODEL_RAM_MB lookup table)
- **Bug nalezen:** `elapsed_s` měří jen transkripci, ne model loading → odhad jobu 2× příliš krátký

### Analýzy provedené v session
- Detailní analýza tuning v2: `docs/tuning_analyza_2026-03-27.md`
- Kompletní data (CSV 160 triálů): `docs/tuning_v2_kompletni_data.md`
- Meta-kritika analýzy (v konverzaci + shrnutí v memory)

### Klíčové výsledky tuning v2 (tune_20260327_025443_6200bb)
- `large_v3_turbo` WER=0.1757 vs `small` WER=0.2758 — rozdíl signifikantní (z=4.70)
- beam=2 zdánlivě nejlepší, ale NENÍ statisticky signifikantní (z=0.07, n=763 slov)
- no_fallback + best_of: irelevantní parametry (nafouknuly prostor 4×)
- Všechny triály bez initial_prompt — výsledky nejsou produkční
- threads=2: large model nepoužitelný (RTF 1.52–2.0); threads≥6: OK (RTF 0.77–0.93)
- Pareto body: pouze threads=8 konfigurace
- Per-video WER rozdíl 45%: výsledky nejsou robustní pro beam ranking

### Plán tuning v3 (uložen v memory/tuning_v2_findings.md)
- Vynechat: no_fallback, best_of
- Přidat: initial_prompt jako parametr, chunk_seconds {15,30}, RAM měření (psutil)
- Opravit: model caching mezi triály (ušetří ~4.5 h z 9 h celkového času)
- Min. 4 videa různých žánrů
- Cíl: ~128 smysluplných triálů místo 160 redundantních

### Otevřené otázky / TODO
- [ ] perceived_delay_s — ověřit výpočet (pravděpodobně kumulativní, nenormalizované na délku audia)
- [ ] Model caching v tuning_worker.py (velký dopad na dobu jobu)
- [ ] RAM měření per trial
- [ ] Baseline trial (defaultní parametry) pro srovnání
- [ ] Tuning v3 spustit s promptem a min. 4 videi
