# Tuning systém pro STT modely — architektura a roadmap

*Vytvořeno: 2026-03-24*

---

## Cíl

Najít optimální kombinaci parametrů pro každý STT model na konkrétním typu obsahu (česky mluvený dialog, lékařský rozhovor, podcast...). Systém musí být:
- **efektivní** — neprohledávat zbytečně celý prostor
- **reprodukovatelný** — stejný seed = stejné výsledky
- **přehledný** — výsledky vizualizovat, ne jen tabulkovat

---

## Architektura backendu

### Optimalizační strategie

| Strategie | Kdy použít | Jak funguje |
|---|---|---|
| **Ablace** | První průzkum | Mění jeden param najednou od baseline |
| **Grid search** | Malý prostor (≤ 3 params, ≤ 5 hodnot) | Vyčerpávající kombinace |
| **Random search** | Střední prostor | Náhodné vzorkování, quick wins |
| **Bayesian (Optuna)** | Velký prostor (5+ params) | TPE sampler, konverguje 3–5× rychleji |

### Optuna integrace (priorita 1)

```python
import optuna

study = optuna.create_study(
    directions=["minimize", "minimize"],  # WER + RTF
    sampler=optuna.samplers.TPESampler(seed=config.clip_seed),
)

def objective(trial):
    params = {
        "beam_size": trial.suggest_categorical("beam_size", [1, 2, 3, 5, 8]),
        "best_of":   trial.suggest_categorical("best_of", [1, 2, 3, 5]),
        "threads":   trial.suggest_categorical("threads", [2, 4, 6, 8]),
    }
    wer, rtf = run_single_trial(params)
    return wer, rtf

study.optimize(objective, n_trials=config.max_trials)
```

Výstup: `study.best_trials` = Pareto-frontální řešení (multi-objective).

### Cross-validace přes videa (priorita 5)

Každý trial se spustí na N videích, výsledky se průměrují:

```python
wer_scores = []
for video_id in config.video_ids:
    wer = run_trial_on_video(params, video_id)
    wer_scores.append(wer)
aggregate_wer = mean(wer_scores)
```

Zabraňuje overfittingu na jedno video.

### Správa experimentů

- Každý tuning job = adresář `runtime/tuning/{job_id}/`
- `config.json` — vstupní parametry
- `status.json` — průběžný stav + výsledky trials (inkrementální zápis)
- Porovnání jobů: `GET /api/tuning/jobs?compare=id1,id2` → diff tabulka

---

## Architektura frontendu

### Stránka Tuning (existuje, rozšíření)

**Hotové:**
- Výběr modelu + videí
- Strategie (ablace/grid/random)
- Parametrický prostor (toggle buttons)
- Knihovna promptů (7 skupin vč. medicínských odborností)
- Odhad počtu trials
- Pareto scatter chart (WER vs RTF)
- Výsledková tabulka

**Plánovaná rozšíření:**

#### 1. Sensitivity analysis (priorita 2)

Bar chart: každý parametr na ose X, průměrný vliv na WER na ose Y.

```
beam_size  ████████████  -3.2pp WER
best_of    ████          -1.1pp WER
threads    ██            -0.4pp WER
prompt     ██████████    -2.8pp WER
```

Implementace: pro každý param spočítat variance WER při fixních ostatních.

#### 2. Parallel coordinates plot (priorita 4)

Každá osa = jeden parametr, každá čára = jeden trial, barva = WER.
Umožňuje vizuálně identifikovat "dobré regiony" parametrického prostoru.

Knihovna: Recharts nemá parallel coords → použít `react-vis` nebo vlastní SVG.

#### 3. Prompt heatmap

Grid: osa X = prompt kategorie, osa Y = model, hodnoty = WER.
Ukazuje jaký prompt funguje pro jaký model.

#### 4. Experiment manager

Tabulka porovnání jobů:
- Řádky: experimenty (job_id, datum, strategie)
- Sloupce: best WER, best RTF, total trials, model
- Akce: "Porovnat vybrané" → overlay Pareto chartů

---

## Sledování nových modelů (HuggingFace Watcher)

### Problém

Nové open-source STT modely vychází každý týden. Ruční sledování je pomalé.

### Řešení — background service

```python
# backend/app/services/model_watcher.py

HF_API = "https://huggingface.co/api/models"

async def fetch_new_models():
    params = {
        "filter": "automatic-speech-recognition",
        "language": "cs",
        "sort": "lastModified",
        "direction": -1,
        "limit": 20,
    }
    r = requests.get(HF_API, params=params)
    models = r.json()
    # Porovnat s known_models.json, najít nové
    new_models = [m for m in models if m["id"] not in known_ids]
    return new_models
```

### Model card reader

```python
# Stáhne README.md z HuggingFace a parsuje metriky
def parse_model_card(model_id: str) -> dict:
    url = f"https://huggingface.co/{model_id}/raw/main/README.md"
    readme = requests.get(url).text

    # Extrahuje z YAML frontmatter nebo textu:
    # - WER na různých datasetech
    # - velikost modelu
    # - framework (whisper, faster-whisper, wav2vec2...)
    # - licence
    # - jazyky
```

### API endpointy

```
GET /api/models/alerts          → seznam nových modelů od poslední kontroly
POST /api/models/alerts/dismiss → označit jako viděné
GET /api/models/registry/refresh → ruční refresh z HF
```

### UI — notification badge

```
[Modely 🔴3]  ← badge s počtem nových modelů v navigaci
```

Po kliknutí: drawer s kartami nových modelů (název, WER, velikost, odkaz na HF).

---

## Prioritní roadmap implementace

| Priorita | Feature | Effort | Impact |
|---|---|---|---|
| 1 | **Optuna integrace** v tuning_worker.py | M | Vysoký — rychlejší konvergence |
| 2 | **Sensitivity analysis** chart | S | Střední — přehled o parametrech |
| 3 | **HuggingFace model watcher** | M | Vysoký — automatické discovery |
| 4 | **Parallel coordinates** plot | L | Střední — vizualizace prostoru |
| 5 | **Cross-validace** přes videa | S | Vysoký — robustnost výsledků |
| 6 | **Experiment manager** (porovnání jobů) | L | Střední — long-term tracking |

Velikosti: S = 1-2h, M = 3-5h, L = 6-10h

---

## Knihovna promptů — kompletní seznam

### Skupiny
- **Žádný** — prázdný prompt
- **Obecná čeština** — standardní český projev, výslovnost, interpunkce
- **Téma dle názvu videa** — auto-generovaný z titulku (uživatel klikne)
- **Medicína** — 8 odborností:
  - Obecné lékařské termíny
  - Kardiologie
  - Neurologie
  - Psychiatrie
  - Praktický lékař
  - Onkologie
  - Chirurgie
  - Alergologie a imunologie
- **Technologie** — hardware, software, AI, gaming
- **Sport** — fotbal, esport, atletika
- **Podcast** — konverzační styl, reklamy, hovorová čeština

### Vliv na WER
Experimentálně ověřeno na Whisper modelech: správný domain prompt snižuje WER o **2–5 procentních bodů** oproti prázdnému promptu, zejména u odborné terminologie.

---

## Datový model (rozšíření)

### TuningJob

```json
{
  "job_id": "tun_20260324_abc123",
  "status": "completed",
  "model_id": "whisper_cpp_small",
  "label": "small — medicína — beam tuning",
  "strategy": "bayesian",
  "created_at": "2026-03-24T10:00:00Z",
  "total_trials": 50,
  "completed_trials": 50,
  "best_trial_idx": 23,
  "pareto_trial_idxs": [12, 23, 31, 44],
  "results": [...]
}
```

### TuningTrialResult

```json
{
  "trial_idx": 23,
  "params": {"beam_size": 5, "best_of": 3, "threads": 6},
  "chunk_seconds": 20,
  "initial_prompt": "Kardiologické vyšetření srdce...",
  "wer": 0.082,
  "cer": 0.031,
  "wer_normalized": 0.071,
  "mer": 0.078,
  "wil": 0.142,
  "rtf": 0.43,
  "latency_ms": 312,
  "error": null,
  "is_pareto": true
}
```
