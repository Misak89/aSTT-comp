# aSTT-comp — projekt kontext pro Codex

## Proč tento projekt existuje
Čistý přepis `aSTT-comparison` (organicky narostlý, monolitický) do nové struktury.
Původní: main.py 3224 řádků, index.html 7178 řádků — nešlo udržovat.

## Cíl aplikace
Kriticky změřit a ohodnotit různé STT modely pro **online přepis češtiny** (a později dalších jazyků).

**Klíčový požadavek: měření simuluje živý rozhovor, ne dávkové zpracování záznamu.**
- Audio je posíláno do STT modelu v real-time chuncích (100–200ms) jako z mikrofonu
- Model musí stíhat přepisovat jak audio přichází (RTF < 1.0 = stíhá, RTF > 1.0 = nestíhá)
- Tím testujeme skutečnou použitelnost pro live dialog, ne jen offline přesnost

Vstup: YouTube videa s `cs` titulky (ground truth)
Výstup: WER/CER, latence, RTF, HW nároky pro každý model × nastavení × video

## Tech stack
- **Backend:** FastAPI + uvicorn na `127.0.0.1:8012`, Pydantic v2
- **Frontend:** React 18 + Vite + TypeScript + TailwindCSS + TanStack Table + Recharts
- **Packages:** zkopírovány z aSTT-comparison (benchmarks, adapters, ingest)
- **Soubory:** bez přísného limitu řádků — každý soubor = jedna zodpovědnost, přehledný na první přečtení

## Jazykové Kódy (Důležité)
- Čeština se v projektu značí výhradně `cs` (ISO 639-1).
- Regionální zápis může být `cs-CZ` (jazyk + země).

## Kategorie STT Modelů
- `whisper_cpp`: whisper.cpp modely (offline, mic režim dostupný dle konkrétního modelu).
- `faster_whisper`: CTranslate2 streaming modely (streaming + mic).
- `vosk`: lehké streaming modely (streaming + mic, nízké HW nároky).
- `sherpa_onnx`: ONNX streaming modely (streaming + mic, jazyk dle bundle).
- `qwen_asr`: LLM ASR modely (offline/dávkové, bez live mic režimu).
- `moonshine`: moderní streaming model (aktuálně EN).

## Adresářová struktura
```
aSTT-comp/
├── backend/
│   ├── app/
│   │   ├── config.py
│   │   ├── main.py
│   │   ├── models/            # library.py, benchmark.py, runs.py
│   │   ├── routers/           # health, library, benchmark, runs
│   │   └── services/          # library_service, benchmark_service, results_service
│   └── requirements.txt
├── packages/
│   ├── benchmarks/
│   │   ├── runners/           # matrix_benchmark_runner.py, host_telemetry.py
│   │   ├── metrics/           # text_metrics.py (WER/CER + diff)
│   │   └── ground_truth/
│   │       ├── reference_manifest.py
│   │       └── vtt_reference.py
│   ├── adapters/              # whisper_cpp, sherpa_onnx, vosk, qwen_asr
│   └── ingest/                # source_resolver, yt_dlp_fetcher
├── scripts/                   # volatelné skripty pro testy a utility
│   ├── check_health.py        # ověří backend
│   ├── check_model.py         # test připravenosti konkrétního modelu
│   ├── preflight.py           # HW podmínky před benchmarkem
│   ├── copy_subtitles.py      # zkopíruje VTT ze starého projektu
│   └── run_benchmark.py       # CLI spuštění benchmarku
├── docs/
│   ├── plan.md                # kompletní specifikace (tento soubor)
│   ├── models/                # log instalací/odinstalací modelů
│   └── runs/                  # auto-generovaná dokumentace každého runu
└── .runtime/
    ├── library/items.json
    ├── library/subtitles/{video_id}/*.vtt
    ├── library/results/{video_id}/{model}__{setting}/
    ├── runs/{run_id}/
    └── jobs/{job_id}/
```

## API endpointy
- `GET /api/health`
- `GET/POST /api/library/items`
- `POST /api/library/download-subtitles`
- `GET /api/library/subtitle/{video_id}/{filename}`
- `GET /api/library/latest-results/{video_id}`
- `POST /api/benchmark/jobs` → 202 async
- `GET /api/benchmark/jobs`, `GET /api/benchmark/jobs/{job_id}`
- `POST /api/benchmark/jobs/{job_id}/cancel`
- `GET /api/benchmark/options`
- `GET /api/runs`, `GET /api/runs/{run_id}`
- `GET /api/models` — přehled instalovaných modelů + log
- `POST /api/models/{model_id}/install`, `DELETE /api/models/{model_id}`

## Fáze implementace

### Fáze 1 ✅ Scaffold
Skeleton souborů, config, main, modely, routery, services kostra.

### Fáze 2 🔄 Library + VTT
- library_service.py (CRUD, download titulků) — EXISTS, verify
- vtt_reference.py (85 řádků) — EXISTS
- results_service.py — EXISTS, verify
- Spustit backend, otestovat endpointy

### Fáze 3 ⏳ Benchmark pipeline (subprocess izolace)
- Každý STT run běží v **izolovaném podprocesu** (ne ve vlákně backendu)
  - Proč: crash modelu (qwen bfloat16) nezabije backend
  - Proč: přesné CPU/RAM měření jen pro STT proces (ne celý backend)
  - Proč: možnost kill/timeout bez vedlejších efektů
- Audio se posílá v real-time chuncích (streaming simulace živého rozhovoru)
- benchmark_service.py → spustí worker subprocess → monitoruje psutil
- progress_callback přes soubor nebo pipe (ne threading)
- HW telemetrie: 2× pre-sample + 2× post-sample s 2s intervaly

### Fáze 4 ⏳ Frontend React
Tři hlavní obrazovky:

**1. Knihovna (`/library`)**
- Tabulka videí, inline benchmark výsledky (WER/CER per model)
- Akce: přidat video, stáhnout titulky, spustit benchmark pro video

**2. Benchmark (`/benchmark`)**
- Výběr videí + modelů + nastavení + scénáře
- Spuštění → progress bar s live stavem
- Během runu: video přehrávač + live přepis + live metriky + HW grafy
- Po dokončení: WER diff (kde model chyboval), shrnutí

**3. Výsledky (`/results`)**
- Tabulka: videa × modely, hodnoty = WER/CER
- Scatter grafy: kvalita vs latence, RAM vs WER
- Doporučení Top 3 s odůvodněním

## Klíčová funkce: VTT → automatický ground truth
`packages/benchmarks/ground_truth/vtt_reference.py`:
- `extract_vtt_clip_text(video_id, clip_start_s, clip_end_s, subtitles_root)` → str|None

## Scénáře (reprodukovatelnost)
Uložené JSON konfigurace benchmarku. Stejný scénář = stejné podmínky = srovnatelné výsledky.
```json
{
  "scenario_id": "cs_dialog_120s_seed42",
  "clip_seconds": 120,
  "clip_seed": 42,
  "models": ["whisper_cpp_small"],
  "settings": ["balanced"],
  "videos": ["R3BsjbDtWrY", "SQRKerJ22zw"]
}
```

## HW měření a podmínky
- 2× pre-sample (před startem), 2× post-sample (po dokončení), 2s interval
- `conditions_clean: true/false` — detekce zda jiné procesy zatěžovaly CPU > 20%
- CPU teplota (throttling detection)
- Clock audit: drift wall vs monotonic > 2s = varování
- Upozornění pokud systém není "čistý" před spuštěním

## Modely — log instalací
`docs/models/{model_id}.json`:
```json
{
  "model_id": "whisper_cpp_small",
  "events": [
    {"type": "install", "date": "2026-03-15T14:32:00Z", "version": "v1.5.4", "size_mb": 461},
    {"type": "uninstall", "date": "...", "reason": "..."}
  ]
}
```

## Scripts — volatelné testy
```bash
python scripts/check_health.py              # backend alive?
python scripts/check_model.py whisper_cpp_small   # model ready?
python scripts/preflight.py                # HW podmínky OK?
python scripts/copy_subtitles.py           # kopíruj VTT ze starého projektu
python scripts/run_benchmark.py --scenario cs_dialog_120s_seed42
```

## Automatická dokumentace runů
Po každém benchmarku se auto-generuje `docs/runs/{run_id}/README.md`:
- parametry runu, scénář, podmínky HW, výsledky per model, WER diff, datum

## Testování
```bash
# unit testy
.venv\Scripts\python -m pytest tests/ -v

# smoke testy (bez modelů)
python scripts/check_health.py
python scripts/preflight.py

# integration test konkrétního modelu
python scripts/check_model.py whisper_cpp_small
```

## Spuštění backendu
Projekt má vlastní .venv (Python 3.13):
```bash
cd C:\Users\adamf\OneDrive\Dokumenty\aSTT-comp
.venv\Scripts\python -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8012 --reload
```

## Spuštění testů
```bash
cd C:\Users\adamf\OneDrive\Dokumenty\aSTT-comp
.venv\Scripts\python -m pytest tests/ -v
```

## Testovací videa (6 videí s `cs` titulky)
| video_id | Název | Délka |
|---|---|---|
| R3BsjbDtWrY | PlayStation VR2 Tech rozhovor | 143s |
| SQRKerJ22zw | Rozhovor s Capsem z G2 Esports | 286s |
| QyKlQLkaH0s | Rozhovor s Kaiserem z MAD Lions | 305s |
| s9F5qXVK4uU | Mezinárodní den autismu – rozhovor | 548s |
| 3_sujNKFpPU | Cukrfree Podcast #24 (nejdelší!) | 2956s |
| t9j-7JxuZVU | Rozhovor s vývojářem Cyberpunk 2077 | 559s |

Titulky jsou staženy ve starém projektu:
`C:\Users\adamf\OneDrive\Dokumenty\aSTT-comparison\.runtime\source_library\subtitles\{video_id}\`

## Poučení z aSTT-comparison
- Soubory: jedna zodpovědnost, přehledné — ne monolitické
- Frontend: React komponenty, ne jeden HTML soubor
- Qwen na Windows: vždy float32, nikdy bfloat16 (nativní crash)
- sherpa_onnx: optional, ne blokující dependency
- Git: commity průběžně, push nikdy bez souhlasu
- AGENTS.md + memory: aktualizovat na konci každé session
