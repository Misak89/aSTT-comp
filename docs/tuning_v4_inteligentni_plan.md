# Tuning v4: inteligentni strategie (online mic, CZ)

## Cil
- Vybrat model + parametry pro realny online prepis pres mikrofon na starsim kancelarskem HW.
- Minimalizovat cas hledani proti plnemu brute-force gridu.
- Zachovat rozhodovaci jistotu (presnost + latence + stabilita + reprodukovatelnost).

## Problem v3
- Plny grid testuje mnoho slabych kombinaci zbytecne.
- Dlouha doba behu brzdi iterace.
- U casti mereni chybi prima vazba na realny live mic provoz.

## Navrh v4 (3 faze)

### Faze 0: Quick sanity gate (kratky smoke)
- 2 CZ videa, kratke useky (napr. 25-40 s), bez dlouhych preflight validaci.
- Vyradit konfigurace, ktere:
  - failuji stabilitu,
  - maji RTF vyrazne nad limitem,
  - porusuji RAM/CPU limity profilu.
- Vystup: kandidati `alive_set`.

### Faze 1: Inteligentni hledani (misto plneho gridu)
- Start z `alive_set` + mala pocatecni ruznorodost (beam/chunk/threads).
- Iterativni vyber dalsich trialu podle score:
  - `score = w1*WER_soft + w2*latence + w3*RTF + w4*RAM_peak + w5*stability_penalty`
  - vahy podle ciloveho HW profilu (`weak_office`, `mid_office`, `strong_office`).
- Pravidla predcasneho ukonceni:
  - pokud po N krocich neni zlepseni o min. delta, stop vetve,
  - pokud kandidat porusuje hard limit (RTF/RAM/timeout), okamzite prune.
- Doporuceny algoritmus:
  - jednoduche: Successive Halving (rychle zavest),
  - pokrocile: TPE/Bayesian optimizer (volitelne v2 kroku v4).

### Faze 2: Potvrzeni top kandidatu
- Top 3-5 kandidatu opakovat `n>=3` na vice videich.
- Reportovat median + p90 + rozptyl (CI).
- Zaradit kratky mic replay + real mic run (mobil -> mikrofon) pro finalni validaci latence.

## Metriky a rozhodovaci pravidla
- Povinne: `WER`, `WER_soft`, `CER`, `RTF`, `latency_ms`, `perceived_delay_s`, `RAM_peak`, `CPU_avg`, `stability`.
- Rozhodovaci filtry:
  - hard gate: `RTF <= 1.0` (nebo profilovy limit), bez crash/timeout,
  - kvalita: `WER_soft` pod prah,
  - UX: `perceived_delay_s` pod prah.
- Finalni ranking: Pareto + agregovane score.

## Realny mic rezim (nutne pro cil projektu)
- Vedle replay benchmarku zavest `mic_scenario`:
  - fixni skript testu (stejne prostredi, hlasitost, vzdalenost mikrofonu),
  - log first-token latency, finalization latency, dropy segmentu.
- Replay zustava pro rychle porovnani, mic run je finalni potvrzeni.

## Datovy model (rozsireni)
- U trialu drzet:
  - `search_phase` (`gate|search|confirm`),
  - `selection_reason` (proc byl trial vybran),
  - `pruned_reason` (proc byl vyraden),
  - `score_components` (rozpad score),
  - `profile_target` (weak/mid/strong).

## UI/UX doplneni
- Novy typ jobu: `Inteligentni tuning (v4)`.
- V prubehu behu zobrazit:
  - aktualni fazi,
  - pocet prune vs executed,
  - ETA podle dosavadni rychlosti,
  - top kandidaty a jejich trend score.
- Po dobehu: jasne `doporuceni pro live mic` per HW profil.

## Implementacni plan (kratky)
1. Pridat `search_mode` do tuning config (`grid|smart`), default `grid`.
2. Implementovat Fazi 0 gate do `scripts/tuning_worker.py`.
3. Pridat planner (`scripts/tuning_smart_planner.py`) se Successive Halving.
4. Napojit score + prune pravidla + audit log.
5. Dodelat potvrzovaci fazi `n>=3` a report variability.
6. Dodelat mic confirmation pipeline (kratky protokol + zapis metrik).

## Co v4 prinese
- Vyrazne kratsi cas hledani oproti plnemu gridu.
- Mene zbytecnych trialu na slabych kombinacich.
- Lepsi rozhodnuti pro realny live mic provoz na starsim HW.
