# Analýza selhání tuningu — TUNING_STT

**Datum:** 2026-03-26
**Čas:** ~11:30 CZ
**Job:** tune_20260326_030741_689128
**Modely:** whisper_cpp_small, whisper_cpp_large_v3_turbo
**Videa:** s9F5qXVK4uU, ls5MvHhjYGg

---

## CHAT 1 — Analýza časových intervalů a timeoutů

### Dotaz
Podívej se v jakém intervalu probíhá Tuning: tune_20260326_030741_689128, running 139/480 trialů.
A zda někde mezi jednotlivými chody není nepřiměřeně dlouhá mezera.

### Zjištění (první, chybná analýza)
- Job spuštěn 05:07 CZ, trial 120 začal ~06:55
- Trialy 120–140: každý timeout po 468s → celkem 2.7h ztraceno
- **Chybná hypotéza:** příčina = `no_fallback=False` + temperature fallback

### Proč byla analýza chybná
Trial 125–129 má `no_fallback=True` a STEJNĚ timeoutuje.
Vlastní chyba: postavil jsem hypotézu před data.

---

## CHAT 2 — "Udělej jiné 2-3 analýzy na jeden command"

### Dotaz
Udělej jiné 2-3 analýzy na jeden command aby si vyloučil chybu analýzy.

### Výsledky 3 analýz

**A1 — Params okolo trialu 120:**
```
[110-119]  beam=3  threads=8  no_fb=T/F  →  OK
[120-124]  beam=5  best_of=2  threads=2  no_fb=False  →  TIMEOUT
[125-129]  beam=5  best_of=2  threads=2  no_fb=True   →  TIMEOUT  ← no_fallback je JEDNO
[130]      beam=5  best_of=2  threads=4  no_fb=False  →  TIMEOUT  ← threads je JEDNO
```

**A2 — Error rate no_fallback:**
- no_fallback=True: 14% timeout
- no_fallback=False: 17% timeout
→ Rozdíl 3% = šum, žádná kauzalita

**A3 — Audio charakteristika:**
- s9F5qXVK4uU: RMS=4502, ticho=36.7%
- ls5MvHhjYGg: RMS=6140, ticho=30.2%, 8s vysoká energie

### Nový (stále neověřený) závěr
Viník: `beam_size=5 + best_of=2` — ale tato hypotéza nebyla ověřena historickými daty.

---

## CHAT 3 — "Nehledej chybu jinde ale u sebe"

### Dotaz
Nehledej chybu jinde ale u sebe.

### Přiznání chyby
- Tvrdil jsem `no_fallback=False` jako příčinu → A2 to vyvrátila
- Přesto jsem přešel na `beam=5 + best_of=2` bez ověření
- `beam=5` v dřívějších OK trialech? Nezkontrolováno
- Trial 111 MISSING — ignorováno

---

## CHAT 4 — "Máš udělat KVALITNÍ 101% analýzy"

### Dotaz
Máš udělat KVALITNÍ 101% analýzy. Až je budeš mít doufám vyvodíš neprůstřelné závěry. Command na ně napiš najednou.

### A1 — Kompletní tabulka params vs výsledek (0–145)

Klíčová data:
```
beam=2: 40 OK,  0 TIMEOUT  →   0%
beam=3: 79 OK,  0 TIMEOUT  →   0%
beam=5:  0 OK, 22 TIMEOUT  → 100%
```

```
best_of=2 s beam=2,3: 80 OK  →   0% timeout
best_of=2 s beam=5:   22 TIMEOUT → 100%
```

```
no_fallback=False: 59 OK, 12 TIMEOUT → 17%
no_fallback=True:  60 OK, 10 TIMEOUT → 14%
→ IRELEVANTNÍ
```

```
threads=8: 29 OK, 0 TIMEOUT → 0%
→ SPURIOUS — beam=5+threads=8 trialy ještě nenastaly
```

### A2 — beam=5 v OK trialech?
- beam=5 + OK: **0 trialů**
- beam=5 + TIMEOUT: **22 trialů**
- best_of=2 s beam=2,3: **80 OK trialů** — best_of=2 není příčina

### A3b — best_of=2 byl v OK trialech?
- beam=2,3 + best_of=2: 80 OK → problem není v best_of
- beam=5 + best_of=2: 22 TIMEOUT → problem je v beam=5

### A4 — source_metrics pro timeout trialy
Vždy stejný vzor:
- video s9F5qXVK4uU: WER vráceno, RTF normální ✓
- video ls5MvHhjYGg: wer=None, rtf=None ✗ → zabit po 468s

### A5 — Audio analýza
- s9: 0s vysoké energie (>8000 RMS)
- ls5: **8s vysoké energie** (5% audia)

### "Neprůstřelný závěr" (tehdy)
beam_size=5 je jediná příčina. — **Toto bylo stále předčasné.**

---

## CHAT 5 — "Velmi kriticky zhodnoť a napadni analýzy"

### Dotaz
Velmi kriticky zhodnoť a napadni analýzy.

### Kritika

**1. Trial ordering = konfound**
beam=2 → beam=3 → beam=5 jsou seřazeny chronologicky.
Nelze odlišit `beam_size=5` od "cokoliv co se stalo po trialu 119".
RAM akumulace, CPU throttling, teplota by korelovaly stejně 100%.

**2. threads=8 má 0% timeout — ale je spurious**
Přiznal jsem to, pak ignoroval v závěru.

**3. Proč selže POUZE ls5, ne s9?**
Pokud by beam=5 byl jediný viník, proč s9 prochází?
Správná hypotéza: `ls5MvHhjYGg × (něco) = hang`.

**4. A5 audio analýza slabá**
Threshold RMS 8000 arbitrární, nevím kde energie leží, neověřil jsem ls5 v OK trialech.

**5. Trial 111 nevysvětlen a ignorován**
beam=3, threads=8 — identické params jako 110, 112-119. MISSING bez erroru.
Narušuje předpoklad deterministického chování.

**6. "Bug v whisper.cpp" = nepodložená spekulace**

**Co bylo potřeba:**
- Zkontrolovat ls5MvHhjYGg v OK trialech (beam=2,3)
- Zkontrolovat chunk_metrics anomálii (1 záznam při chunk=5)
- Ověřit trial 111

---

## CHAT 6 — "Je to dostatečně kritická analýza?"

### Dotaz
Je to dostatečně kritická analýza? Byla provedena kritická analýza logiky?

### Odpověď — Ne, nebyla dostatečná

Chyby kritické analýzy:
1. Neprovedl jsem klíčový test: **ls5MvHhjYGg v OK trialech**
2. "Logicky nemožný" vs "potenciálně chybný" — byl jsem příliš měkký
3. chunk_metrics anomálii (1 záznam) jsem identifikoval ale nevysvětlil
4. Nezkoumal jsem timeout mechanismus
5. Závěr byl prezentován jako "neprůstřelný", pak v kritice stažen — samo o sobě selhání

---

## CHAT 7 — Klíčový test: ls5MvHhjYGg v OK trialech

### Výsledek

```
OK trialy kde ls5 MÁ wer:   119
OK trialy kde ls5 NEMÁ wer:   0

Beam distribuce OK trialů s ls5:
  beam=2: 40,  beam=3: 79
```

**ls5MvHhjYGg vrací WER ve VŠECH 119 OK trialech.**
Video není inherentně broken.

### chunk_metrics anomálie vysvětlena

`streaming_runner.py:286-294` — hardcoded single entry:
```python
chunk_metrics = [{
    "chunk_start_s": 0.0,
    "chunk_end_s": round(clip_duration, 1),   # vždy celé audio
    ...
}]
```
chunk_seconds nemá vliv na chunk_metrics — je to by design (batch zpracování).

---

## CHAT 8 — Komplexní analýza "Proveď vše"

### I. DESIGN — Fatální rozpor s cílem projektu

CLAUDE.md: *"Audio posíláno v real-time chuncích jako z mikrofonu"*
Realita: `whisper_cpp` → `_run_buffered()` → batch, ne streaming.

`streaming_simulator.py` implementuje správnou simulaci (real-time chunky, sleep, lag měření)
ale v tuningu se **vůbec nepoužívá**.

**chunk_seconds ovlivňuje pouze UI replay animaci** (timestamp milníky v `_run_buffered:244-276`).
Pro WER, RTF, latency = nulový efekt. 120/480 trialů = 25% jsou duplikáty.

**perceived_delay_s = chunk_seconds + chunk_seconds × RTF**
Obě proměnné jsou fiktivní pro batch whisper.

### II. KÓD — Konkrétní chyby

| # | Soubor | Chyba |
|---|--------|-------|
| 1 | tuning_worker.py:511 | rtf_viable pouze při completion, během jobu vždy False |
| 2 | streaming_runner.py:191 | zbytečná double WAV konverze (int16→float→int16) |
| 3 | streaming_runner.py:226 | heartbeat thread bez finally bloku |
| 4 | whisper_cpp_runner.py:104 | polling 50ms×9360 místo proc.wait(timeout) |
| 5 | tuning_worker.py:85 | timeout trialy vstupují do Pareto/best |
| 6 | tuning_worker.py:468 | chyba trial_111 bez fallback result záznamu |
| 7 | tuning_worker.py:476 | perceived_delay_s vzorec neplatí pro batch |

### III. DATA — Co je špatně na výsledcích

**beam=5 + ls5MvHhjYGg = 100% timeout — PROKÁZÁNO:**
- beam=2: 40/40 OK ✓
- beam=3: 79/79 OK ✓
- beam=5: 0/22 OK ✗

**WER průměrování nekonzistentní:**
- Timeout trial: WER = průměr z [0.2119, None] = 0.2119 (1 video)
- OK trial: WER = průměr z [0.2119, 0.4098] = 0.3109 (2 videa)
→ Timeout trialy vypadají systematicky lépe

**Pareto obsahuje neplatné výsledky** (timeout trialy s partial WER).

### IV. CO OPRAVIT A DOPLNIT

**Opravit (bugs):**
1. rtf_viable počítat po každém trialu
2. Eliminovat double WAV konverzi
3. heartbeat → finally
4. proc.wait() místo polling
5. Vyloučit error trialy z Pareto/best
6. Fallback result při selhání _append_result
7. Opravit/odstranit perceived_delay_s pro batch modely

**Opravit (design):**
8. chunk_seconds odebrat z whisper tuning space, nebo implementovat skutečný streaming přes streaming_simulator.py
9. Validace beam_size před generací trialů
10. Použít streaming_simulator.py pro whisper RTF měření

**Doplnit:**
11. CPU usage detekce "zamrzlého" whisper procesu
12. Varování v UI pro trialy s error + WER (misleading)
13. Ruční ověření: `whisper-cli -bs 5 -bo 2 -f audio_ls5.wav -m ggml-small.bin`
14. updated_ts do Pydantic modelu TuningJobStatus

---

## Chronologie selhání analýz

```
Hypotéza 1: no_fallback=False → ❌ vyvrácena A2 analýzou
Hypotéza 2: beam=5 + best_of=2 → ❌ best_of=2 je v 80 OK trialech
Hypotéza 3: beam=5 samotný → ✓ potvrzeno (0/22 vs 119/119)
             ale pouze s ls5MvHhjYGg — s9 prochází s beam=5 vždy
Příčina timeoutu → STÁLE HYPOTÉZA (audio + beam search degeneration)
                   Ověřit: 1 ruční spuštění whisper-cli
```

## Poučení

1. Hypotéza se staví AŽ PO datech, ne před nimi
2. 100% korelace v sekvenčních datech ≠ kauzalita (trial ordering confound)
3. Kritická analýza musí říci "logicky nemožné" ne "možná chybné"
4. Neprovedené klíčové testy = nekompletní analýza
5. "Neprůstřelný závěr" bez ověření klíčového testu je chyba
