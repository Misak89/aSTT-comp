# aSTT-comp

Nastroj pro mereni a porovnani STT (Speech-to-Text) modelu pro online prepis cestiny v realnem case.

---

## Instalace (prvni spusteni)

### 1. Nainstaluj prerekvizity

| Co | Kde stáhnout | Poznamka |
| --- | --- | --- |
| **Git** | [git-scm.com/downloads](https://git-scm.com/downloads) | Nutny pro `git clone` |
| **Python 3.13** | [python.org/downloads](https://www.python.org/downloads/) | Windows: zaklikni "Add Python to PATH" |
| **Node.js 18+** | [nodejs.org/en/download](https://nodejs.org/en/download) | Stahni LTS verzi |
| **ffmpeg** | [ffmpeg.org/download.html](https://ffmpeg.org/download.html) | Windows: rozbal, pridej `bin/` slozku do PATH |

> **ffmpeg a PATH na Windows:** Rozbal ffmpeg, napriklad do `C:\ffmpeg\`. Pak v System Properties → Environment Variables → PATH pridej `C:\ffmpeg\bin`. Restart terminalu.

### 2. Stáhnout projekt

Otevri **Command Prompt** (Windows) nebo **Terminal** (Mac/Linux) a spust:

```bat
git clone https://github.com/TVOJE-JMENO/aSTT-comp.git
cd aSTT-comp
```

> Nahrad `TVOJE-JMENO` skutecnym GitHub URL po prvnim pushu.

### 3. Vytvorit Python prostredi

**Windows** (Command Prompt):

```bat
py -3.13 -m venv .venv
.venv\Scripts\pip install -r backend/requirements.txt
```

> Instalace zavislosti trva 3-10 minut (stahuje ML knihovny ~1 GB). Pockat.

**Mac / Linux** (Terminal):

```bash
python3.13 -m venv .venv
.venv/bin/pip install -r backend/requirements.txt
```

### 4. Sestavit frontend

```bat
npm --prefix frontend install
npm --prefix frontend run build
```

> Prvni `npm install` trva 1-2 minuty.

### 5. Spustit aplikaci

**Windows:**

```bat
web-up.cmd
```

**Mac / Linux:**

```bash
chmod +x web-up.sh
./web-up.sh
```

> **Dulezite:** Terminal okno musi zustat otevrene - v nem bezi backend. Nezavirat!

### 6. Otevrit v prohlizeci

```txt
http://127.0.0.1:8012/benchmark
```

### 7. Nainstalovat STT model

Bez modelu aplikace neprepisuje. Jdi na:

```txt
http://127.0.0.1:8012/models
```

Tam najdes instrukce ke stazeni pro kazdy model (vosk, whisper.cpp, faster-whisper, Qwen, moonshine).
**Doporuceny prvni model pro CZ:** `whisper.cpp small` nebo `vosk small cs-0.4` (nejnizsi HW naroky).

---

## Rychle spusteni (po instalaci)

**Windows:** `web-up.cmd` / `web-status.cmd` / `web-down.cmd`

**Mac / Linux:** `./web-up.sh` / `./web-status.sh` / `./web-down.sh`

## Aktualizace

```bat
git pull
npm --prefix frontend run build
web-up.cmd
```

---

## Povinne dokumenty (cti v tomto poradi)

1. [CONTRIBUTING.md](./CONTRIBUTING.md) - zavazna pravidla prace v repu
2. [docs/ARCHITECTURE.md](./docs/ARCHITECTURE.md) - architektura a logika
3. [docs/RUNBOOK.md](./docs/RUNBOOK.md) - provozni navody a incidenty
4. [docs/PLAN_TRACKER.md](./docs/PLAN_TRACKER.md) - kam se zapisuje aktivni plan a jeho stav
5. [docs/session_log.md](./docs/session_log.md) - historie implementacnich session

## Aktivni pokracovani (roadmapa)
- [docs/mic_sequence_vibe_coding_short_2026-03-30.md](./docs/mic_sequence_vibe_coding_short_2026-03-30.md)
- [docs/mic_sequence_rychla_vs_orchestracni_prestavba_2026-03-30.md](./docs/mic_sequence_rychla_vs_orchestracni_prestavba_2026-03-30.md)
- [docs/tuning_v4_implementacni_plan.md](./docs/tuning_v4_implementacni_plan.md)
- [docs/tuning_v4_tasky.md](./docs/tuning_v4_tasky.md)

## Zavazna pravidla dokumentace
- Kazda zmena kodu musi mit zapis v `docs/session_log.md`.
- Zmena architektury (`backend/app/services`, `backend/app/routers`, `packages/*`) musi mit update `docs/ARCHITECTURE.md`.
- Zmena provozu/startu (`web-*.cmd`, `start_web_app*.cmd`, health/preflight skripty) musi mit update `docs/RUNBOOK.md`.
- Plan/roadmapa se aktualizuje pres `docs/PLAN_TRACKER.md` (single source of truth).
- Push se dela pouze po explicitnim souhlasu maintainera.

Kontrola je v CI (`.github/workflows/docs-guard.yml`) a v PR checklistu (`.github/pull_request_template.md`).

## Stabilni spousteni webu
- `web-up.cmd`: doporuceny stabilni start v aktualnim okne (backend bezi, okno nezavirat).
- `web-up-build.cmd`: stejny stabilni start, ale predem vynuti frontend build.
- `web-up-bg.cmd`: volitelny start do noveho okna `aSTT-web`.
- `web-status.cmd`: zobrazi health a proces na portu 8012.
- `web-down.cmd`: ukonci proces, ktery posloucha na portu 8012.
- `web-restart.cmd`: stop + start v jednom kroku.

## Logika
- Hlavni produkcni vstup jsou root `web-*.cmd` soubory.
- Backend bezi na `127.0.0.1:8012`, frontend se servira z backendu.
- Pokud je potreba plny rebuild frontendu, pouzij `web-up-build.cmd`.
