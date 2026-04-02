# aSTT-comp

Nástroj pro měření a porovnání STT (Speech-to-Text) modelů pro online přepis češtiny v reálném čase.  
Testuje skutečnou použitelnost pro živý rozhovor — audio jde do modelu v real-time chuncích, ne dávkově.

---

## Instalace

### Prerekvizity

| Co | Kde stáhnout | Poznámka |
| --- | --- | --- |
| **Git** | [git-scm.com](https://git-scm.com/downloads) | |
| **Python 3.13** | [python.org](https://www.python.org/downloads/) | Windows: zaškrtni „Add Python to PATH" |
| **Node.js 18+** | [nodejs.org](https://nodejs.org/en/download) | LTS verze |
| **ffmpeg** | [ffmpeg.org](https://ffmpeg.org/download.html) | Musí být v PATH |

> **ffmpeg na Windows:** Rozbal do `C:\ffmpeg\`, přidej `C:\ffmpeg\bin` do systémové PATH. Restart terminálu.

---

### Windows

```bat
git clone https://github.com/Misak89/aSTT-comp.git
cd aSTT-comp

py -3.13 -m venv .venv
.venv\Scripts\pip install -r backend/requirements.txt

npm --prefix frontend install
npm --prefix frontend run build

web-up.cmd
```

Otevři prohlížeč: **[http://127.0.0.1:8012](http://127.0.0.1:8012)**

> Okno terminálu nechej otevřené — v něm běží backend.

---

### Mac / Linux

```bash
git clone https://github.com/Misak89/aSTT-comp.git
cd aSTT-comp

python3.13 -m venv .venv
.venv/bin/pip install -r backend/requirements.txt

npm --prefix frontend install
npm --prefix frontend run build

chmod +x web-up.sh && ./web-up.sh
```

Otevři prohlížeč: **[http://127.0.0.1:8012](http://127.0.0.1:8012)**

---

### STT modely

Modely se instalují přímo z webového rozhraní — žádné ruční stahování při instalaci není potřeba.

Po spuštění přejdi na **[http://127.0.0.1:8012/models](http://127.0.0.1:8012/models)** a nainstaluj vybraný model dle instrukcí na stránce.

Doporučený první model pro CZ: `whisper.cpp small` nebo `vosk small cs`.

---

## Rychlé příkazy (po instalaci)

| Windows | Mac / Linux | Funkce |
| --- | --- | --- |
| `web-up.cmd` | `./web-up.sh` | Spustit |
| `web-down.cmd` | `./web-down.sh` | Zastavit |
| `web-status.cmd` | `./web-status.sh` | Stav backendu |
| `web-up-build.cmd` | `./web-up-build.sh` | Rebuild frontendu + spustit |

## Aktualizace

```bash
git pull
npm --prefix frontend run build
# pak web-up
```
