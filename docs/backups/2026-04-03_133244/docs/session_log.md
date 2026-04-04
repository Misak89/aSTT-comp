# Session log — záznamy vývojových session

Doc-Meta:
- owner: engineering
- status: active
- last_updated_utc: 2026-04-03T03:53:52Z
- review_due_utc: 2026-04-15T00:00:00Z

---

## Session 2026-04-03T00:14:00Z - v5 monitoring quick win: default fast scan + PID metadata TTL cache

### Co bylo provedeno
- Pred docs editaci byl vytvoren centralni backup do `docs/backups/2026-04-03_021254/`.
- Refactor monitoringu v `backend/app/routers/health.py` bez zmeny endpoint kontraktu:
  - `GET /api/health/processes` ma novy default lane `fast` (misto `full`),
  - invalidni `mode` fallback je `fast`,
  - pridana kratka TTL cache cmdline/exe metadat per PID (`_PROC_META_CACHE`) pro snizeni scan overheadu.
- Doplneny unit contract testy: `tests/unit/test_health_scan_policy_contract.py`.

### Validace
- `pytest tests/unit/test_health_scan_policy_contract.py tests/unit/test_dashboard_monitor_contract.py tests/unit/test_runtime_paths.py` -> PASS.
- `.venv\Scripts\python -m compileall backend/app/routers/health.py` -> PASS.
- Fyzicky smoke po plnem restartu (`web-down.cmd` -> `web-up-bg.cmd`):
  - `GET /api/health` -> 200,
  - `GET /api/health/processes` -> 200 + `scan_phase=fast`,
  - `GET /api/health/processes?mode=full` -> 200.

### Dopad
- Monitoring default lane je levnejsi a vic odpovida audit doporuceni (fast-by-default).
- Diagnosticke deep scan cesty (`slow/full`) zustavaji dostupne explicitne.

---

## Session 2026-04-02T23:18:00Z - v5 refactor start: path portability + dashboard scan cadence extraction

### Co bylo provedeno
- Pred dokumentacnimi upravami byl vytvoren centralni backup do `docs/backups/2026-04-03_011754/`.
- Zavedena centralni path utilita `packages/common/runtime_paths.py`:
  - canonical runtime root `runtime/`,
  - env override `ASTT_RUNTIME_ROOT`,
  - kandidati `runtime` + legacy `.runtime` pro kompatibilni cteni.
- Refactor runtime cest bez zmeny externi funkcnosti:
  - `packages/adapters/model_readiness.py`,
  - `packages/adapters/whisper_cpp_runner.py`,
  - `packages/benchmarks/runners/matrix_benchmark_runner.py`,
  - `packages/benchmarks/runners/online_testset_runner.py`,
  - `scripts/check_model.py`,
  - `scripts/copy_subtitles.py` (odstranen hardcoded absolutni path, pridany CLI parametry).
- Frontend monitoring refactor bez zmeny kontraktu:
  - process scan cadence vytazena z `DashboardPage.tsx` do `frontend/src/components/dashboard/useProcessScanCadence.ts`.
- Pridany testy: `tests/unit/test_runtime_paths.py`.

### Validace
- `pytest tests/unit/test_runtime_paths.py tests/unit/test_dashboard_monitor_contract.py` -> PASS.
- `python -m compileall ...` pro zmenene Python moduly -> PASS.
- `npm --prefix frontend run build` -> PASS.

### Dopad
- Runtime path handling je prenositelnejsi mezi stroji a konzistentni s `runtime/`.
- Dashboard monitoring orchestrace je modularnejsi pri stejnem API/UI chovani.

---

## Session 2026-04-02T22:05:22Z - AGENTS compact rewrite finished (one-page contract)

### Co bylo provedeno
- Pred editaci byl vytvoren centralni backup do `docs/backups/2026-04-03_000435/`.
- `AGENTS.md` byl zkracen a prepsan do kratke kontraktni verze:
  - proc projekt existuje,
  - aktualni cil v 5 bodech,
  - priority,
  - hard rules,
  - definition of done,
  - odkazy na detailni dokumenty.
- Dlouhe sekce (obsahle seznamy endpointu, velke stromy a procedury) byly odstraneny z AGENTS a delegovany na specializovane docs.

### Vystupy
- `AGENTS.md`
- `docs/backups/2026-04-03_000435/*`

### Dopad
- Source-of-truth je ted mnohem prehlednejsi, kratsi a prakticky pouzitelny pro cizi LLM i lidskeho maintainera.

---

## Session 2026-04-02T22:02:50Z - note added: documentation history is in timestamped backups

### Co bylo provedeno
- Pred editaci byl vytvoren centralni backup do `docs/backups/2026-04-03_000240/`.
- Do `docs/DOCS_GOVERNANCE.md` a `CONTRIBUTING.md` byla doplnena explicitni poznamka:
  - historii dokumentace je treba hledat v `docs/backups/` podle timestampu v nazvu adresare.

### Vystupy
- `CONTRIBUTING.md`
- `docs/DOCS_GOVERNANCE.md`
- `docs/backups/2026-04-03_000240/*`

### Dopad
- Je jednoznacne popsano, kde hledat historicke verze dokumentace a jak je cist podle data.

---

## Session 2026-04-02T22:01:03Z - backup rule synchronized to CONTRIBUTING and DOCS_GOVERNANCE

### Co bylo provedeno
- Pred upravou dokumentace byl vytvoren centralni backup do `docs/backups/2026-04-03_000058/`.
- Do `CONTRIBUTING.md` byla doplnena povinnost vytvaret backup pred docs editaci.
- Do `docs/DOCS_GOVERNANCE.md` byla doplnena stejna povinnost:
  - v operacni rutine jako prvni krok,
  - v sekci backup politiky vcetne struktury `root/...` a `docs/...`.

### Vystupy
- `CONTRIBUTING.md`
- `docs/DOCS_GOVERNANCE.md`
- `docs/backups/2026-04-03_000058/*`

### Dopad
- Pravidlo "nejdriv backup, pak docs editace" je ted konzistentne popsane v AGENTS, CONTRIBUTING i DOCS_GOVERNANCE.

---

## Session 2026-04-02T21:59:40Z - AGENTS backup rule for documentation edits

### Co bylo provedeno
- Do `AGENTS.md` byl doplnen povinny krok: pred editaci dokumentace nejdriv vytvorit centralni timestamp backup v `docs/backups/<YYYY-MM-DD_HHMMSS>/`.
- Doplneno, ze backup musi zachovat relativni strukturu souboru (`root/...`, `docs/...`).

### Vystupy
- `AGENTS.md`

### Dopad
- Proces docs zmen je bezpecnejsi a konzistentni: nejdriv backup, az potom editace.

---

## Session 2026-04-02T20:32:30Z - AGENTS goal list corrected to 5 explicit points

### Co bylo provedeno
- Sekce "Cil aplikace" v `AGENTS.md` byla upravena na explicitnich 5 bodu.
- Bod `/transcript` a bod o akceptovanem zpozdeni byly oddeleny (3 a 4), Dashboard zustal bod 5.

### Vystupy
- `AGENTS.md`

### Dopad
- Cile odpovidaji presne zadani 1-5 bez slouceni bodu.

---

## Session 2026-04-02T20:29:24Z - AGENTS goal alignment (online mic, simulation labeling, transcript, dashboard)

### Co bylo provedeno
- V `AGENTS.md` byla prepsana sekce "Cil aplikace" podle aktualniho zadani:
  - primarni fokus na online mikrofonni STT mereni a tuning,
  - explicitni odliseni simulace od online streamu,
  - cil pro `/transcript` (30-240 min, preferovane zpozdeni cca 30-60 s),
  - role Dashboardu jako diagnostiky HW naroku.
- Byly sjednoceny formulace vstupu/vystupu, aby odpovidaly temto ctyrem pilirum.

### Vystupy
- `AGENTS.md`

### Dopad
- Globalni source-of-truth dokument ted presne odrazi realny produktovy cil a provozni ocekavani.

---

## Session 2026-04-02T20:07:29Z - docs contract clarification (MD/JSON/JSONL vs TXT) + AGENTS drift fix

### Co bylo provedeno
- Ujasnen dokumentacni kontrakt: triplet je striktne `MD + JSON + JSONL`; `TXT` soubory mimo `docs/` nejsou triplet artefakt.
- Sjednocena pravidla v `AGENTS.md`, `CONTRIBUTING.md`, `docs/DOCS_GOVERNANCE.md`.
- Opraven fakticky drift v `AGENTS.md`:
  - odstranena neaktualni reference na `scripts/run_benchmark.py`,
  - runtime root sjednocen na `runtime/`,
  - doplnena viditelna reference na `scripts/log_cmd.py`, `scripts/log_cmd_unix.py`, `scripts/log_cmd_help.txt`.
- Opraven drift v `docs/ARCHITECTURE.md` (`runtime/` jako jediny aktivni runtime root).
- Guard testy doplneny o kontrolu, ze `.txt` soubor neni povazovan za guarded triplet format.

### Vystupy
- `AGENTS.md`
- `CONTRIBUTING.md`
- `docs/DOCS_GOVERNANCE.md`
- `docs/ARCHITECTURE.md`
- `scripts/verify_docs_guard.py`
- `tests/unit/test_verify_docs_guard.py`

### Dopad
- Pravidla jsou jednoznacna: triplet se tyka docs artefaktu (`MD/JSON/JSONL`), ne operacnich `.txt` helper souboru.
- `log_cmd` napoveda zustava snadno dohledatelna bez presunu v tomto kroku.

---

## Session 2026-04-02T06:19:04Z - docs triplet enforcement + contradiction fix

### Co bylo provedeno
- Byl proveden audit dokumentacnich pravidel po audit session a nalezen logicky rozpor: formaty byly popsane, ale ne jako striktne povinny triplet.
- Kontrakt byl sjednocen v `AGENTS.md`, `CONTRIBUTING.md`, `docs/DOCS_GOVERNANCE.md`:
  - novy ne-core lidsky docs artefakt ma povinny triplet `MD + JSON + JSONL`,
  - `MD` je explicitne human-read vrstva, `JSON` canonical snapshot, `JSONL` append-only timeline.
- `scripts/verify_docs_guard.py` byl rozsireny:
  - validace `doc_file` i pro `JSONL` (prvni zaznam `doc_meta.doc_file`),
  - kontrola doc triplet companion souboru pro nove ne-core artefakty,
  - explicitni vyjimky pro core/living/system-state docs.
- Doplneny chybejici soubory:
  - `docs/reports/audit_conclusion_2026-04-02.jsonl`,
  - `docs/tuning_v5_implementacni_plan.json`,
  - `docs/tuning_v5_implementacni_plan.jsonl`.
- Doplneny unit testy guardu pro JSONL contract a triplet exemptions.

### Vystupy
- `AGENTS.md`
- `CONTRIBUTING.md`
- `docs/DOCS_GOVERNANCE.md`
- `docs/PLAN_TRACKER.md`
- `scripts/verify_docs_guard.py`
- `tests/unit/test_verify_docs_guard.py`
- `docs/reports/audit_conclusion_2026-04-02.jsonl`
- `docs/tuning_v5_implementacni_plan.json`
- `docs/tuning_v5_implementacni_plan.jsonl`

### Dopad
- Dokumentacni kontrakt je jasny, jednotny a strojove vymahatelny.
- Riziko dalsiho rozporu mezi promptem a realnym docs guard chovanim je snizeno.

---

## Session 2026-04-02T05:46:26Z - docs format contract (MD/JSON/JSONL) + guard update

### Co bylo provedeno
- Doplnena pravidla dokumentacnich formatu (`MD`, `JSON`, `JSONL`) do governance dokumentace.
- Doplnena naming pravidla pro nove snapshot/report docs (`_YYYY-MM-DD`) s vyjimkou pro living docs.
- Doplneno pravidlo `doc_file` pro nove docs s metadaty.
- Aktualizovan docs guard (`scripts/verify_docs_guard.py`) pro kontrolu:
  - date suffixu u novych docs snapshot/report souboru (mimo vyjimky),
  - konzistence `doc_file` u novych `MD`/`JSON` dokumentu s metadaty.
- Plan v5 doplnen o dokumentacni formaty.

### Vystupy
- `docs/DOCS_GOVERNANCE.md`
- `CONTRIBUTING.md`
- `scripts/verify_docs_guard.py`
- `docs/tuning_v5_implementacni_plan.md`

### Dopad
- Jednotna pravidla pro dokumentacni artefakty v lidskem i strojovem formatu.
- Guard umi zachytit nekonzistentni naming/metadata u nove vzniklych docs souboru.

---

## Session 2026-04-02T05:31:57Z - audit conclusion persisted + v5 staged plan

### Co bylo provedeno
- Auditni zaver byl ulozen jako textovy report (MD) i strukturovany report (JSON).
- Byl vytvoren navazny etapovy implementacni plan v5 (post-audit stabilization).
- `PLAN_TRACKER` byl aktualizovan o novy plan `post-audit-v5` a milniky P0-P4.

### Vystupy
- `docs/audit_conclusion_2026-04-02.md`
- `docs/reports/audit_conclusion_2026-04-02.json`
- `docs/tuning_v5_implementacni_plan.md`
- `docs/PLAN_TRACKER.md` (aktualizovane)

### Dopad
- Auditni zaver je archivovan ve dvou formatech pro lidi i tooling.
- Existuje jasna phased roadmapa navazujici na audit.
- Dokumentacni navaznost je uzavrena podle governance pravidel.

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

---

## Session 2026-03-29 až 2026-03-30 (feature/tuning-v4)

### Kontext
- Integrace změn pro v4 větev: real mic workflow, sekvenční testování, offline provoz, provozní start webu.
- Referenční commit: `afda0e0` (`feat(v4): improve mic sequence workflow, telemetry, and offline tooling`).

### Co bylo implementováno

#### 1) Mic pipeline a sekvenční režim
- Výrazně rozšířen `backend/app/services/mic_service.py`:
  - detailnější telemetrie a event logování,
  - reason kódy pro nestabilní běhy (např. backpressure/drop),
  - rozšířený timing sekvence.
- Rozšířeny routery/API pro mic (`backend/app/routers/mic.py`) včetně perzistence manuálních záznamů.
- Frontend `MicSession` (`frontend/src/components/MicSession.tsx`) doplněn o:
  - sekvenční orchestrace modelů,
  - ukládání výsledků,
  - robustnější stavové hlášky při WS/pipeline chybách.

#### 2) Tuning v4 a rozhodovací/validační vrstva
- Aktualizace `scripts/tuning_worker.py` pro v4 workflow.
- Doplňky validačních/report skriptů:
  - `scripts/tuning_decision_report.py`
  - `scripts/tuning_hw_matrix_report.py`
  - `scripts/tuning_soak_validate.py`
- Přidán smoke skript pro CZ streaming modely:
  - `scripts/smoke_cz_streaming_models.py`.

#### 3) Modely a readiness
- Registry modelů rozšířena (`packages/adapters/_registry.py`), doplněna podpora/napojení pro další streaming cesty.
- Přidán adapter `packages/adapters/faster_whisper_runner.py`.
- Aktualizována readiness logika (`packages/adapters/model_readiness.py`).

#### 4) UI a provozní ergonomie
- Nová stránka `/hwflow`:
  - `frontend/src/pages/HWFlowPage.tsx`
  - napojení v `App.tsx` a navigaci.
- Rozšíření UI/API typů pro mic a benchmark:
  - `frontend/src/api/client.ts`
  - `frontend/src/types/index.ts`
  - úpravy `BenchmarkPage`, `LibraryPage`, `TuningPage`.

#### 5) Offline a start webu
- Přidány/aktualizovány utility pro stabilní start/stop:
  - `web-up.cmd`, `web-up-build.cmd`, `web-up-bg.cmd`
  - `web-status.cmd`, `web-down.cmd`, `web-restart.cmd`
  - `start_web_app_detached.cmd`
- Aktualizován `README.md` s krátkým návodem stabilního spuštění.
- Doplněny/aktualizovány skripty pro audit a provoz (`network_audit_report.py`, `check_health.py`, `preflight.py`, ...).

### Dokumentace vytvořená v této session
- `docs/mic_sequence_rychla_vs_orchestracni_prestavba_2026-03-30.md`
- `docs/mic_sequence_vibe_coding_short_2026-03-30.md`
- aktualizace:
  - `docs/tuning_v4_implementacni_plan.md`
  - `docs/tuning_v4_tasky.md`

---

## Session 2026-03-30 (Documentation Contract Hardening)

### Kontext
- Cíl: zavést jednotná, snadno dohledatelná a vynutitelná pravidla dokumentace pro všechny vývojáře.

### Implementováno
- Přidán závazný kontrakt:
  - `CONTRIBUTING.md`
- Přidány centralizované dokumenty:
  - `docs/ARCHITECTURE.md`
  - `docs/RUNBOOK.md`
- Upraven root vstup (`README.md`) tak, aby byly pravidla vidět hned po otevření repa.
- Přidán PR checklist:
  - `.github/pull_request_template.md`
- Přidán CI guard:
  - `.github/workflows/docs-guard.yml`
  - `scripts/verify_docs_guard.py`

### Vynucená pravidla (nově)
- Každá změna kódu vyžaduje update `docs/session_log.md`.
- Změna architektury/logiky vyžaduje update `docs/ARCHITECTURE.md`.
- Změna provozu/spouštění vyžaduje update `docs/RUNBOOK.md`.
- Kontrola běží automaticky v CI přes docs guard workflow.
- Aktivní mic pokračování je explicitně navázáno v `README.md` + `CONTRIBUTING.md` na:
  - `docs/mic_sequence_vibe_coding_short_2026-03-30.md`
  - `docs/mic_sequence_rychla_vs_orchestracni_prestavba_2026-03-30.md`
  - `docs/tuning_v4_implementacni_plan.md`
  - `docs/tuning_v4_tasky.md`

### Upresneni "kam se zapisuje plan" (doplneno)
- Zavedeno centralni misto:
  - `docs/PLAN_TRACKER.md` (single source of truth pro aktivni plan a stav).
- Pravidla aktualizace planu jsou explicitne v:
  - `CONTRIBUTING.md` (sekce F),
  - `README.md` (Povinne dokumenty + aktivni roadmapa).
- CI guard (`scripts/verify_docs_guard.py`) nově vyžaduje update `docs/PLAN_TRACKER.md`,
  pokud se mění plan dokumenty `docs/tuning_*` nebo `docs/mic_sequence_*`.

### Kriticky audit uzavreni dokumentacni smycky (doplneno)
- Nalezena a opravena mezera v docs guardu:
  - root `web-*.cmd` / `start_web_app*.cmd` drive nespadaly do `code_changes`, tedy nevyzadovaly `session_log`/`RUNBOOK`.
  - guard upraven: root cmd/ps1 a dalsi root code/config soubory jsou zahrnuty.
- Guard rozsiren:
  - plan tracker update se vyzaduje i pro obecne docs plan/roadmap markdowny (`*plan*.md`, `*roadmap*.md`).
- Sjednoceny kontrakt:
  - `AGENTS.md` a `CLAUDE.md` doplneny o povinnost `docs/PLAN_TRACKER.md`.
- Doplneno pravidlo procesu:
  - v `CONTRIBUTING.md` je explicitne uvedeno nastavit `docs-guard` jako required check v branch protection.

### Finalizace kvality dokumentacniho kruhu (dodelano)
- `CONTRIBUTING.md` doplnen o povinnou kvalitu zapisu:
  - strucna / logicka / jednoznacna / uzavreny kruh.
- `docs/PLAN_TRACKER.md` preveden na operacni format:
  - `Last updated`, `Status`, tabulka milniku, jasna aktualizacni posloupnost.
- `scripts/verify_docs_guard.py` zpresnen:
  - nehlida jen pritomnost souboru, ale i **substantive added content** v povinnych docs.
- `README.md` doplnen o explicitni pravidlo, ze plan se aktualizuje pres `docs/PLAN_TRACKER.md`.

---

## Session 2026-04-01T13:55:00Z (docs-governance-hardening)

### Summary
- Vytvoren backup dokumentace do `docs/backups/2026-04-01_154911`.
- `CLAUDE.md` preveden na stub a potvrzeno, ze `AGENTS.md` je jediny source of truth.
- Zavedena jednotna `Doc-Meta` struktura (owner/status/last_updated_utc/review_due_utc) v core docs.
- Posilen CI guard (`scripts/verify_docs_guard.py`) o validaci metadata, timestampu a session entry struktury.
- Pridan helper `scripts/add_session_log_entry.py` pro kratke, konzistentni session zapisy s UTC timestampem.

### Why
- Predchozi stav mel riziko zastaravani, duplicity instrukci a slabou vymahatelnost.

### Impact
- Dokumentace ma vynutitelny minimalni standard.
- PR bez konzistentni dokumentacni aktualizace neprojde CI guardem.

---

## Session 2026-04-01T15:42:10Z (specstory-selfimproving)

### Summary
- Pridan analyzator recurring failures ze .specstory s auto-prioritizaci a generovanim KNOWN_FAILURES/reportu/state.

### Impact
- Projekt ma opakovatelny, datovy feedback loop pro vibe coding chyby a pripravenou self-improving dokumentacni smycku.

---

## Session 2026-04-01T15:43:01Z (specstory-guard-link)

### Summary
- Docs guard doplnen o vazbu: pri zmene specstory analyzatoru musi byt zmenen i KNOWN_FAILURES + report + state artefakty.

### Impact
- Smycka je vic neobchazitelna: logika analyzy a jeji dokumentacni/statisticke vystupy se nemohou rozjet.

---

## Session 2026-04-01T16:11:04Z (supply-chain-security-gate)

### Summary
- Pridana security policy a guard proti rizikovym OSS zdrojum/licencim/dependency praktikam vcetne intake registru.

### Impact
- Inspirace, stahovani i build maji novy strojovy security gate a CI check, ktery snizuje riziko infikovaneho nebo nekompatibilne licencovaneho kodu.

---

## Session 2026-04-01T16:12:13Z (supply-chain-local-hooks)

### Summary
- Pridany lokalni pre-commit hooky pro supply-chain guard a specstory learning loop.

### Impact
- Bezpecnostni a quality gate se daji spoustet automaticky i pred push, ne pouze v CI.

---

## Session 2026-04-02T03:17:36Z (dashboard-monitoring-validation)

### Summary
- Validated dashboard/health/monitoring changes: /api/health and /api/health/processes (fast/slow/full) returned 200, backend was UP, frontend build succeeded, and health router compiled.

### Impact
- Monitoring behavior is now verified and documented before docs-governance guard execution.

---

## Session 2026-04-02T03:19:38Z (docs-guard-utf8-fix)

### Summary
- Fixed verify_docs_guard subprocess decoding on Windows by forcing UTF-8 with replacement fallback for git outputs.

### Impact
- Docs guard now reliably validates session_log and core docs in Czech/UTF-8 content on this environment.

---

## Session 2026-04-02T03:35:50Z (hooks-and-guard-hardening)

### Summary
- Added UTF-8-safe docs-guard tests, dashboard monitoring contract tests, large-change reminder in verify_docs_guard, and Windows-safe local hook bootstrap via scripts/setup_git_hooks.py.

### Impact
- Commits can run local guards without --no-verify in this environment, and monitoring/docs-guard regressions are now covered by tests and proactive large-diff warning.

---

## Session 2026-04-02T03:38:14Z (windows-hook-stability-tuning)

### Summary
- Adjusted setup_git_hooks to install local .git/hooks/pre-commit with absolute .venv Python shebang and non-mutating guard step to improve commit reliability on Windows.

### Impact
- Local commits can run guard checks without shell signal-pipe failures or forced --no-verify workflow.

---

## Session 2026-04-03T01:14:18Z (v5-latency-lane-split)

### Summary
- Implemented strict_live/probe_online/batch_proxy lane split in tuning worker+decision service, updated Tuning UI/report fields, and added unit tests for pure-lane decision gating.

### Impact
- Live recommendation now stays in a dedicated lane without proxy contamination while preserving backward compatibility for legacy latency_quality data.

---

## Session 2026-04-03T01:49:10Z (post-audit-v5-event-store)

### Summary
- Added SQLite WAL tuning event-store flow (worker emission, API/service access, UI event panel) with baseline monitoring report and event validation tooling.

### Impact
- Moves v5 from planned to in-progress with auditable event timeline, lower-risk diagnostics, and reproducible checks for event integrity and baseline process-scan performance.

---

## Session 2026-04-03T02:10:01Z (utf8-native-webctl)

### Summary
- Added UTF-8-native web control via scripts/webctl.py and rewired Windows web wrappers away from embedded PowerShell logic.

### Impact
- Windows start/stop/status/restart can now run through Python UTF-8 execution path (suitable for Nushell/Windows Terminal/WezTerm) with lower CP1250 risk.

---

## Session 2026-04-03T02:24:36Z (utf8-webctl-hardening)

### Summary
- Hardened scripts/webctl.py to stop/start the full uvicorn process tree deterministically and added Nushell wrapper web.nu for UTF-8-native control.

### Impact
- Windows restart workflow is now stable (down->up-bg/status) and users can run UTF-8-native commands via Python or Nushell without relying on CP1250 shell behavior.

---

## Session 2026-04-03T03:01:53Z (v5-refactor-closure-docs)

### Summary
- Updated PLAN_TRACKER milestone statuses (P1/P2/P3 done), published refactor closure triplet report in docs/reports, and recorded deferred P4 long-run transcript validation for next iteration.

### Impact
- Repository now has both executive and detailed closure documentation synced to tracker state, with explicit deferred-note traceability for long transcript validation.

---

## Session 2026-04-03T03:41:45Z (v6-plan-and-implementation-docs)

### Summary
- Added V6 long-transcript segmentation plan triplet with one-command non-physical gate and explicit testing checkpoints by location/time.

### Impact
- Plan tracker now includes long-transcript-v6 milestones and implementation can proceed without duplicating completed V5 stabilization work.

---

## Session 2026-04-03T03:50:54Z (v6-s0-intake-and-utf8-contract)

### Summary
- Started long-transcript-v6 on feature/tuning-v6 by completing S0: OSS intake update plus verified one-command non-physical gate.

### Impact
- V6 tracker is now in_progress with S0 done and V6 docs now explicitly enforce UTF-8-only workflow without CP1250/windows-1250.

---

## Session 2026-04-03T03:53:52Z (v6-s1-segment-bundle-backend)

### Summary
- Implemented V6 S1 backend segment-bundle domain (models, library API/service preview+upsert+get) and added unit tests for preset/manual validation and persistence roundtrip.

### Impact
- V6 now has a canonical persisted segment bundle foundation for library/transcript integration, with CP-safe UTF-8 docs and passing unit regression subset.

---

## Session 2026-04-03T04:21:00Z (v6-s2-s3-slicer-and-transcript-orchestration)

### Summary
- Completed V6 S2/S3 implementation: compact `/library` slicer UI (preset + manual max 21 + pause-aware preview/save), transcript segment-bundle orchestration as one logical output flow, and streaming segment offset handling in benchmark worker for local/online sources.

### Impact
- Long transcript workflow now supports persisted segment bundles end-to-end (`library -> transcript`) with non-physical gate updated to include frontend build and new V6 unit tests; physical 3-model long-run validation remains deferred to owner execution (V6-S4).

---

## Session 2026-04-03T11:05:02Z (v6-physical-smoke-transcribe-13s)

### Summary
- Added user-facing one-command physical smoke runner `scripts/smoke_transcribe_13s.py` plus Windows wrapper `smoke-transcribe-13s.cmd` for 13s transcript checks via backend API.
- Fixed benchmark local-source normalization from `file:///...` to valid local paths in backend source resolving.
- Fixed model parameter precedence so explicit per-model params (for example `threads`, `beam_size`) are not overwritten by preset setting defaults.
- Fixed sticky text selection lock in Transcribe layout resize and enforced copyable section headers for `Zdroj audia` and `Model STT`.
- Executed physical smoke runs: turbo PASS with offset-difference check, plus all-installed run where whisper family passed and non-whisper adapters returned empty transcript in current streaming path.

### Impact
- Project now has a reproducible physical smoke workflow for user-side STT validation in transcript mode with persistent reports under `runtime/logs`.
