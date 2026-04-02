# aSTT-comp — Specifikace produktu

- vytvořeno: 2026-03-20
- status: v1 — schváleno

---

## Základní princip

Měření simuluje **živý rozhovor**, ne dávkové zpracování záznamu.

Audio je posíláno STT modelu v real-time chuncích (100–200 ms), jako by přicházelo
z mikrofonu. Model musí stíhat přepisovat jak audio přichází.

- RTF < 1.0 → model stíhá → použitelný pro real-time
- RTF > 1.0 → model nestíhá → nevhodný pro live přepis

Tím testujeme skutečnou použitelnost pro živý dialog — ne jen offline přesnost na celém souboru.

---

## Obrazovky aplikace

### 1. Knihovna (`/library`)

Tabulka videí. U každého videa vidíš inline výsledky posledního benchmarku.

**Co vidíš pro každé video:**

- název, délka, žánr
- stav titulků (staženy / chybí)
- poslední WER/CER per model (barevně — zelená/žlutá/červená)
- tlačítko "Spustit benchmark"

**Akce:**

- Přidat video (URL → extrahuje video_id, uloží)
- Stáhnout titulky (yt-dlp → VTT)
- Rozkliknout video → detail s přepisy a historií runů

---

### 2. Benchmark (`/benchmark`)

Konfigurace a spuštění měření.

**Nastavení:**

- výběr videí z knihovny (checkboxy)
- výběr modelů + nastavení (checkboxy)
- délka klipu (default 120s), seed pro determinismus
- výběr nebo uložení scénáře

**Během runu — co vidíš:**

```
┌─────────────────────────┬───────────────────────────────┐
│  VIDEO PŘEHRÁVAČ        │  LIVE PŘEPIS                  │
│  (obraz + zvuk)         │  whisper_small / balanced     │
│  ████████░░░░ 0:43/2:00 │  "...a to je důvod proč      │
│                         │   jsme se rozhodli pro..."    │
├─────────────────────────┴───────────────────────────────┤
│  METRIKY V ČASE                                         │
│  WER: 4.2%  CER: 2.1%  RTF: 0.18  Latence: 340ms      │
├─────────────────────────────────────────────────────────┤
│  HW ZATÍŽENÍ (live graf posledních 30s)                 │
│  CPU: ████░░░░ 47%   CPU temp: 68°C   RAM: 681 MB      │
│  Podmínky: ✓ čistý systém (CPU idle před: 8%)          │
└─────────────────────────────────────────────────────────┘
```

**Po dokončení:**

- WER diff (vizualizace kde model chyboval)
- shrnutí runu + link na výsledky

---

### 3. Výsledky (`/results`)

Vizuální analýza a doporučení.

- Tabulka: videa × modely, hodnoty = WER/CER
- Scatter grafy: kvalita vs latence, RAM vs WER, stabilita mezi běhy
- Doporučení Top 3 s odůvodněním (hard limity → ranking)

---

### 4. Modely (`/models`)

Správa instalovaných STT modelů.

Pro každý model:

- stav (instalován / chybí), verze, velikost
- nastavení (low_latency / balanced / high_accuracy / memory_saver)
- log událostí: kdy nainstalován, kdy odinstalován, důvod
- tlačítko "Test připravenosti"

---

## Scénáře (reprodukovatelnost)

Pojmenovaná JSON konfigurace benchmarku. Stejný scénář = stejné podmínky = srovnatelné výsledky.

```json
{
  "scenario_id": "cs_dialog_120s_seed42",
  "description": "Standardní 2min dialog, deterministický výběr",
  "clip_seconds": 120,
  "clip_seed": 42,
  "models": ["whisper_cpp_small", "qwen3_asr_0_6b"],
  "settings": ["balanced", "high_accuracy"],
  "videos": ["R3BsjbDtWrY", "SQRKerJ22zw", "QyKlQLkaH0s"]
}
```

---

## Subprocess izolace (klíčová architektura)

Každý STT run běží jako **samostatný podproces** (ne vlákno backendu):

```
Backend (FastAPI)
    │
    ├── spustí subprocess: python worker.py --model whisper_cpp_small ...
    │       │
    │       ├── načte model
    │       ├── přijímá audio chunky (stdin / soubor)
    │       ├── zapisuje přepis + metriky (stdout / soubor)
    │       └── exituje (nebo crashuje — backend to zachytí)
    │
    ├── monitoruje subprocess přes psutil (CPU, RAM, teplota)
    └── čte progress ze souboru / pipe
```

Proč subprocess a ne vlákno:
- crash modelu (qwen bfloat16 → NTSTATUS 0xC0000005) nezabije backend
- přesné CPU/RAM měření jen pro STT proces
- kill/timeout bez vedlejších efektů
- čisté oddělení zodpovědností

---

## HW měření — protokol

```
Start runu
    ↓
[pre-sample 1] CPU, RAM, teplota
wait 2s
[pre-sample 2] CPU, RAM, teplota
    ↓
Zjisti zatížené procesy → pokud CPU > 20%, zobraz varování
    ↓
Spusť STT subprocess
    ↓
Monitoruj každých 500ms: CPU, RAM, teplota → ukládej do časové osy
    ↓
Subprocess dokončen
    ↓
wait 2s
[post-sample 1] CPU, RAM, teplota
wait 2s
[post-sample 2] CPU, RAM, teplota
    ↓
Clock audit: drift wall vs monotonic → pokud > 2s, označ run jako nespolehlivý
    ↓
Ulož do host_telemetry.json
```

Výsledek obsahuje `conditions_clean: true/false`.

---

## Reference tiery (ground truth)

| Tier | Zdroj | Kdy |
|------|-------|-----|
| A | YouTube VTT titulky | vždy, automaticky |
| A+ | Kalibrační STT model (pomalý, nejvyšší přesnost) | volitelně pro ověření |
| B/C | Lidsky korigovaná reference | budoucí |

---

## Metriky

### Kvalita textu
- `wer_raw`, `cer_raw` — bez normalizace
- `wer_norm`, `cer_norm` — po normalizaci (lowercase, bez interpunkce)
- `missing_word_rate`, `hallucination_rate`

### Real-time schopnost
- `rtf` — Real-Time Factor (< 1.0 = stíhá)
- `time_to_first_text_ms` — kdy přišel první výstup
- `avg_word_lag_ms` — průměrné zpoždění slova
- `segment_finalization_ms`

### Hardware
- `cpu_percent_avg`, `cpu_percent_p95`
- `ram_mb_avg`, `ram_mb_peak`
- `cpu_temp_avg`, `cpu_temp_peak` (pokud dostupná)

### Spolehlivost
- `run_success_rate`, `timeout_rate`, `conditions_clean`
- `clock_drift_ms`

---

## Scripts — volatelné testy

```bash
# Základní health
python scripts/check_health.py

# Připravenost modelu
python scripts/check_model.py whisper_cpp_small

# HW podmínky před benchmarkem
python scripts/preflight.py

# Zkopírovat VTT titulky ze starého projektu
python scripts/copy_subtitles.py

# Spustit benchmark z CLI
python scripts/run_benchmark.py --scenario cs_dialog_120s_seed42
```

---

## Automatická dokumentace každého runu

Po každém benchmarku se auto-generuje `docs/runs/{run_id}/README.md`:

```markdown
# Run cs_dialog_120s_seed42 — 2026-03-20 14:32 UTC

## Parametry
- Scénář: cs_dialog_120s_seed42
- Clip: 120s, seed=42
- Modely: whisper_cpp_small/balanced, qwen3_asr_0_6b/balanced
- Videa: R3BsjbDtWrY, SQRKerJ22zw

## HW podmínky
- CPU idle před: 8% ✓
- CPU peak během: 67%
- RAM peak: 796 MB
- Teplota peak: 72°C
- conditions_clean: true

## Výsledky
| Model | Nastavení | WER | CER | RTF | Latence |
|-------|-----------|-----|-----|-----|---------|
| whisper_cpp_small | balanced | 3.3% | 2.1% | 0.18 | 340ms |
| qwen3_asr_0_6b | balanced | 4.8% | 3.2% | 0.31 | 520ms |
```

---

## Testování

### Smoke testy (bez modelů, CI-friendly)
```bash
python scripts/check_health.py
python scripts/preflight.py --dry-run
pytest tests/smoke/ -v
```

### Integration testy (vyžadují instalovaný model)
```bash
python scripts/check_model.py whisper_cpp_small
pytest tests/integration/test_whisper.py -v
```

### Regression testy (porovnání s baseline runem)
```bash
pytest tests/regression/ -v
```

---

## Fáze implementace

| Fáze | Popis | Stav |
|------|-------|------|
| 1 | Scaffold + backend skeleton | ✅ hotovo |
| 2 | Library service + VTT integrace | 🔄 probíhá |
| 3 | Benchmark pipeline + subprocess izolace | ⏳ čeká |
| 4 | Frontend React | ⏳ čeká |
| 5 | Scripts + testy + auto-dokumentace | ⏳ čeká |
