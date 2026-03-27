# Analýza tuningu — tune_20260327_025443_6200bb

**Datum:** 2026-03-27
**Job:** `tune_20260327_025443_6200bb`
**Label:** `TestTurbo_FullDoubleCZ-NEW`
**Stav při analýze:** running, 150/160 triálů dokončeno
**Celková doba:** ~9 h reálného času (start 03:54 CET)

---

## Konfigurace testu

| Parametr | Hodnota |
|---|---|
| Modely | `whisper_cpp_small`, `whisper_cpp_large_v3_turbo` |
| Videa | `s9F5qXVK4uU` (médium), `ls5MvHhjYGg` (nové) |
| Sample | 156 s / video |
| Strategie | grid |
| Parametrický prostor | beam_size {1,2,3,5} × best_of ≤ beam × threads {2,4,6,8} × no_fallback {T,F} |
| Celkem triálů | 160 (80 per model) |

---

## Výsledky — přehled per model

| Model | Triálů | WER min | WER avg | WER soft min | RTF viable |
|---|---|---|---|---|---|
| whisper_cpp_small | 80 | 0.276 | 0.294 | 0.202 | 100 % (80/80) |
| whisper_cpp_large_v3_turbo | 80 | **0.176** | **0.178** | **0.137** | 46 % (37/80) |

---

## Pareto fronta (minimalizace WER + RTF)

Čtyři body — všechny s threads=8:

| WER | RTF | Model | beam | Threads | Použití |
|---|---|---|---|---|---|
| **0.176** | 0.691 | large_v3_turbo | 2 | 8 | Nejlepší přesnost, viable live |
| 0.276 | 0.282 | small | 5 | 8 | Rychlý, přijatelná přesnost |
| 0.286 | 0.255 | small | 3 | 8 | Kompromis |
| 0.312 | 0.206 | small | 1 | 8 | Maximální rychlost |

---

## Detailní zjištění

### beam_size — překvapivý výsledek (large model)

| beam | WER avg (viable) | RTF avg |
|---|---|---|
| 1 | 0.1827 | 0.811 |
| **2** | **0.1757** | **0.814** |
| 3 | 0.1771 | 0.821 |
| 5 | 0.1782 | 0.876 |

Beam=2 je nejlepší. Vyšší beam nezlepšuje přesnost a zpomaluje model.

### Threads — podmínka použitelnosti large modelu

| Threads | Large viable? | RTF range | Doporučení |
|---|---|---|---|
| 2 | ✗ | 1.52–1.76 | Pouze small |
| 4 | ⚠ | 0.96–0.99 | Hraniční, může nestíhat |
| 6 | ✓ | 0.77–0.93 | OK pro live |
| 8 | ✓ | 0.69–0.87 | Ideální |

### no_fallback a best_of — irelevantní

Nulový měřitelný vliv na WER i RTF. `best_of` je při `beam_size > 1` pravděpodobně interně ignorován whisper.cpp. `no_fallback` nemá vliv na přesnost ani rychlost.

### Per-video rozdíl (trial #94, large, beam=2, threads=8)

| Video | WER | WER soft | RTF | Obtížnost |
|---|---|---|---|---|
| s9F5qXVK4uU | 0.143 | 0.119 | 0.690 | nižší |
| ls5MvHhjYGg | **0.208** | **0.155** | 0.702 | **+45 % WER** |

Jedno video dramaticky ovlivňuje průměr. Nelze hodnotit model z jediného videa.

---

## ✅ Silná místa

### 1. Large_v3_turbo je jasný vítěz v přesnosti
WER 0.176 vs 0.276 (small) = **36% zlepšení**. WER soft 0.137 — drtivá většina chyb jsou drobné záměny (diakritika, morfologie), nikoli úplné nesrozumitelnosti.

### 2. Beam=2 odhaluje nelineární chování modelu
Systematický test prokázal, že větší beam neznamená lepší výsledek. Tento poznatek by nebyl dostupný bez grid search — je přímo použitelný pro nastavení produkce.

### 3. RTF pod 1.0 na threads≥4 (large)
Large model je reálně použitelný pro live mikrofon i na středně silném HW (4 vlákna). Threads=6 poskytuje pohodlný bezpečnostní odstup (RTF ~0.78).

### 4. no_fallback a best_of lze zafixovat
Prokázaná irelevance těchto dvou parametrů výrazně zjednodušuje konfigurační prostor pro příští tuning. Není potřeba je dále testovat.

### 5. WER soft jako doplňková metrika funguje
Rozdíl WER vs WER soft (0.176 vs 0.137 = 22 % chyb jsou "drobné") ukazuje, že model je v praxi o dost použitelnější než naznačuje surový WER.

---

## ❌ Kritická slabá místa

### 1. Parametrický prostor nafouknutý zbytečnými parametry
`no_fallback` (×2) a `best_of` (×2–5) nemají vliv → **50–80 % triálů bylo zbytečných**. Místo 160 triálů stačilo ~32. Ztraceno ~7 hodin výpočetního času.

**Dopad:** Příští grid search musí nejprve provést ablaci na malém vzorku a odfiltrovat irelevantní parametry.

### 2. elapsed_s nezahrnuje načítání modelu — chybný odhad trvání

| Model | Čas transkripce | Overhead (loading) | Reálný čas |
|---|---|---|---|
| small | 56 s/trial | +59 s | 115 s |
| large_v3_turbo | 169 s/trial | **+177 s** | 346 s |

Worker znovu načítá model před každým triálem. Pro large model je to **3 minuty overhead per trial**. `elapsed_s` měří jen transkripci, proto odhad délky jobu byl o ~50 % podhodnocen.

**Dopad:** Uživatel nemá realistický odhad doby trvání. Bug v kódu — nutné opravit.
**Fix:** Cacheovat model mezi triály stejného modelu, nebo měřit celkový wall-clock čas triálu.

### 3. Pouze 2 videa — výsledky nejsou robustní

Rozdíl mezi videi (WER 0.143 vs 0.208) je 45 %. Průměr ze dvou videí má velkou statistickou nejistotu — jedno obtížnější video může celkové pořadí změnit.

**Dopad:** Nelze s jistotou říci, že beam=2 je obecně nejlepší — může být artefakt těchto dvou videí.
**Doporučení:** Příští tuning: min. 4–6 videí, různé žánry.

### 4. RAM není měřena per trial

Filtry v UI pracují s aproximacemi (small ~600 MB, large ~1 600 MB), nikoliv s naměřenými hodnotami. Skutečná spotřeba RAM se liší dle délky audia, beam size a vnitřní alokace modelu.

**Dopad:** Při nasazení na stroji s omezenou RAM může model selhat, přestože filtr "povolil" danou konfiguraci.
**Fix:** Přidat `psutil.Process().memory_info().rss` měření do workeru před a po každém triálu.

### 5. Pareto body existují pouze s threads=8

Žádná konfigurace s threads<8 není na Pareto frontě — vždy existuje konfigurace s threads=8, která je v obou kritériích (WER i RTF) stejně dobrá nebo lepší. To znamená, že na strojích s méně vlákny uživatel nemá dobrou volbu — musí přijmout výrazně horší WER (small model) nebo nedostatečný RTF (large, threads<6).

**Dopad:** Výsledky jsou silně závislé na konkrétním testovacím HW. Přenositelnost doporučení je omezená.

### 6. Perceived delay — hodnoty vypadají podezřele

Nejlepší viable large (threads=8): perceived_delay ~265 s pro 156s audio. Pokud je to v sekundách, hodnota je nesmyslná. Buď jednotka, nebo výpočet `perceived_delay_s` v workeru vyžaduje ověření.

**Fix:** Zkontrolovat výpočet `perceived_delay_s` v `tuning_worker.py`.

---

## Doporučení pro produkci

| Scénář | Model | beam | threads | Očekávaný WER | Očekávaný RTF |
|---|---|---|---|---|---|
| Nejlepší přesnost (≥6 vláken) | large_v3_turbo | 2 | 6–8 | 0.176 | 0.77–0.70 |
| Slabý HW (≤4 vlákna) | small | 5 | 4 | 0.276 | 0.38 |
| Minimální latence | small | 1 | 8 | 0.312 | 0.21 |

`no_fallback=True`, `best_of=1` — bez vlivu na výsledek, zjednodušují konfiguraci.

---

## Návrhy pro příští tuning

1. **Vynechat:** `no_fallback`, `best_of` z parametrového prostoru
2. **Přidat:** měření RAM per trial (`psutil`)
3. **Přidat:** caching modelu mezi triály stejného modelu → 3–4× kratší trvání
4. **Rozšířit:** min. 4 videa (různé žánry, různá obtížnost)
5. **Ověřit:** výpočet `perceived_delay_s` — pravděpodobně chybné jednotky nebo logika
6. **Přidat:** chunk_seconds jako parametr pro large model (30 s vs 15 s)
