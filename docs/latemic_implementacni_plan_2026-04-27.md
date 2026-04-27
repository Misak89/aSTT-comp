# LateMic implementacni plan

Doc-Meta:
- owner: engineering
- status: draft
- doc_file: latemic_implementacni_plan_2026-04-27.md
- source_of_truth: false
- last_updated_utc: 2026-04-27T21:59:43Z
- review_due_utc: 2026-05-11T00:00:00Z

## 1. Cil

`/benchmark/latemic` ma byt samostatny benchmarkovy rezim pro skutecny mikrofon, ale bez prisneho online streamu.

Cil LateMic:
- najit co nejkvalitnejsi prepis,
- pri co nejmensim praktickem zpozdeni,
- pro realny mikrofonni vstup,
- s jasnym auditnim dukazem, ze text vznikl pouze ze skutecne zachyceneho mic segmentu.

Vychozi test zpozdeni:
- `5 s` volitelne pro velmi rychle modely,
- hlavni rozsah `10-60 s`,
- krok `5 s`,
- kazdy vysledek musi ukazat, zda se vesel do zvoleneho zpozdeni.

LateMic nesmi byt oznaceny jako `live stream`. Spravne oznaceni je `delayed mic`, `late mic`, nebo `segmentovy mic prepis`.

## 2. Produktova definice

LateMic pracuje takto:

1. Prohlizec zachyti audio z mikrofonu.
2. Audio se sklada do kratkych docasnych segmentu.
3. Segment se ukonci idealne v pauze; pokud pauza neprijde, ukonci se hard limitem.
4. Backend ulozi segment jako docasny WAV/PCM artefakt.
5. Vybrany model prepise hotovy segment.
6. System zmeri kvalitu, rychlost a realne zpozdeni.
7. Docasne audio se smaze podle nastavene politiky.

To znamena:
- vstup je skutecny mikrofon,
- zpracovani je segmentove/batch nebo hybridni,
- vysledek neni online stream,
- text v historii smi pochazet jen z autoritativniho LateMic segmentu.

## 3. Co uz existuje a ma se pouzit

### Frontend

| Existujici kod | Pouziti pro LateMic |
|---|---|
| `frontend/src/App.tsx` | Pridat routu `benchmark/latemic`. |
| `frontend/src/pages/BenchmarkPage.tsx` | Rozsirit benchmark zalozky o `Little late Mic`, nebo oddelit novou page pri zachovani stejne navigace. |
| `frontend/src/components/MicSession.tsx` | Pouzit jako zdroj existujici UX/logiky, ale nerozsirovat donekonecna jednu velkou komponentu. |
| `computePcmStats()` | Reuse pro RMS dBFS, peak dBFS, VAD/ticho, clipping. |
| `recordMicAudioChunk()` | Reuse principu pro mic proof panel; u LateMic nebude pocitat WS bytes, ale segment bytes. |
| AudioWorklet/ScriptProcessor capture blok v `MicSession.tsx` | Reuse zachytavani PCM 16 kHz mono z prohlizece. |
| Model param UI a matice v `MicSession.tsx` | Vytahnout do sdilenych helperu/komponent pro mic i latemic. |
| `buildTuningSweepPlan()` | Reuse pro automaticke ladeni parametru nad modely/profily. |
| `api/client.ts` | Stejny styl REST klienta pro `/api/latemic/...`. |

Doporuceni: pred implementaci LateMic vytahnout sdilene casti z `MicSession.tsx`:
- `micAudioCapture.ts` nebo `useMicPcmCapture()`,
- `micInputProof.ts`,
- `micModelParams.ts`,
- `micTuningPlan.ts`.

Tim se snizi riziko, ze se `/benchmark/mic` rozbije pri vyvoji `/benchmark/latemic`.

### Backend

| Existujici kod | Pouziti pro LateMic |
|---|---|
| `packages/adapters/_registry.py` | Zdroj modelu, parametru, defaultu a schopnosti. |
| `backend/app/services/mic_service.py` | Reuse metrik, event log stylu, model param normalizace, mic proof myslenky; neprebirat WebSocket stream jako primarni cestu. |
| `backend/app/routers/mic.py` | Vzor pro Pydantic schema, REST endpointy, history/list/export pattern. |
| `packages/benchmarks/runners/streaming_runner.py` | Vhodny zdroj pro segmentovy dispatch: umi live-session adaptery i buffered adaptery pres WAV. |
| `packages/benchmarks/runners/matrix_benchmark_runner.py` | Ma real-mode cestu pro lokalni WAV a vic adapteru; pouzit jako inspiraci pro model compatibility a metriky. |
| `runtime/` konfigurace v `backend/app/config.py` | Pridat samostatny runtime root, napr. `runtime/late_mic/`. |

Dulezite: nepouzivat primo privatni funkce typu `_run_buffered()` jako dlouhodoby kontrakt. Lepsi je vytvorit verejny helper, napr.:

```python
transcribe_latemic_segment(
    wav_path: Path,
    model_id: str,
    model_params: dict,
    output_dir: Path,
) -> dict[str, Any]
```

Ten muze uvnitr znovu pouzit existujici runner logiku.

## 4. Navrzena struktura stranky

URL:

```text
http://127.0.0.1:8012/benchmark/latemic
```

Navigace v Benchmark:

```text
Benchmark | Mikrofon | Little late Mic
```

Navrh workflow:

```text
1. Mikrofon a dukaz vstupu
   - vyber mikrofonu
   - RMS/peak dBFS, VAD, ticho, clipping, sample rate
   - stav: skutecny mic vstup

2. Segmentace a zpozdeni
   - povolene zpozdeni: 5/10-60 s, krok 5 s
   - cilova delka segmentu
   - preferovat konec v pauze
   - max cekani na pauzu
   - prekryv segmentu
   - politika mazani docasneho audia

3. Modely a parametry
   - vyber modelu
   - spolecne parametry
   - matice parametru
   - profily a doporucene hodnoty

4. Testovaci plan
   - model x nastaveni x zpozdeni
   - odhad delky testu
   - odhad poctu segmentu
   - varovani pri nerealnem planu

5. Start a vysledky
   - prubeh segmentu
   - fronta prepisu
   - realny lag
   - kvalita textu
   - stav smazani audia
```

## 5. Definice zpozdeni

Je nutne merit vice typu zpozdeni, jinak budou zavery nejednoznacne.

| Metrika | Vyznam |
|---|---|
| `lag_budget_s` | Hodnota testu, kterou operator nastavil, napr. `20 s`. |
| `segment_audio_s` | Skutecna delka zachyceneho segmentu. |
| `queue_wait_s` | Jak dlouho segment cekal pred predanim modelu. |
| `decode_s` | Cisty cas prepisu modelem. |
| `tail_lag_s` | Cas od konce segmentu do hotoveho textu. |
| `first_audio_visible_lag_s` | Cas od zacatku segmentu do hotoveho textu. |
| `max_visible_lag_s` | Nejhorsi prakticke zpozdeni pro text v segmentu. Primarni limit pro "vesel/nevesel". |
| `over_budget_s` | O kolik vysledek prekrocil `lag_budget_s`. |

Doporucena primarni metrika:

```text
max_visible_lag_s = segment_audio_s + queue_wait_s + decode_s
```

Proc: pokud segment trva 20 s a prepis trva 4 s, prvni slova jsou videt az po cca 24 s. To je pro uzivatele realne zpozdeni.

Sekundarni metrika:

```text
tail_lag_s = queue_wait_s + decode_s
```

Ta ukazuje, jak rychle byl dopsan konec segmentu.

## 6. Delka segmentu

Delka segmentu neni `beam_size`.

`beam_size` je parametr dekodovani modelu. Segmentace je parametr audio workflow.

Doporucene vychozi mapovani:

| `lag_budget_s` | `target_segment_s` | `max_pause_wait_s` | `overlap_s` |
|---:|---:|---:|---:|
| 5 | 3 | 1 | 0.5 |
| 10 | 5 | 2 | 1 |
| 15 | 8 | 3 | 1.5 |
| 20 | 10 | 3 | 2 |
| 25 | 12 | 4 | 2 |
| 30 | 15 | 5 | 2 |
| 35 | 18 | 5 | 2 |
| 40 | 20 | 5 | 3 |
| 45 | 22 | 5 | 3 |
| 50 | 25 | 5 | 3 |
| 55 | 28 | 5 | 3 |
| 60 | 30 | 5 | 3 |

Pravidlo ukonceni segmentu:

```text
po target_segment_s:
  pokud je pauza >= min_pause_ms, segment ukonci
  jinak cekej max_pause_wait_s
  pokud pauza neprijde, ukonci hard stopem
```

Doporucene vychozi hodnoty:
- `min_pause_ms`: `500-800 ms`,
- `max_pause_wait_s`: podle tabulky,
- `overlap_s`: `0.5-3 s`,
- `sample_rate`: `16000 Hz`,
- `channels`: `1`,
- `sample_width`: `16-bit`.

## 7. Modely a adaptery

LateMic neni live stream, proto muze testovat i modely, ktere nejsou vhodne pro online WebSocket mikrofon, ale umi zpracovat WAV segment.

Model eligibility:

```text
model je vhodny pro LateMic, pokud:
  - ma lokalni runtime a model,
  - umi prepis z WAV segmentu nebo live-session adapter nad hotovym segmentem,
  - neni explicitne blokovany runtime readiness checkem.
```

Prakticky to znamena:
- `vosk_small_cs_0_4`: rychly kandidát pro male zpozdeni,
- `whisper_cpp_base`: kandidat pro lepsi kvalitu pri strednim zpozdeni,
- `whisper_cpp_large_v3_turbo`: kandidat pro vysokou kvalitu pri vyssim zpozdeni,
- `faster_whisper_small_cs_int8` / `faster_whisper_medium_cs_int8`: kandidati pro porovnani kvalita/rychlost,
- dalsi batch modely jen pokud runtime readiness projde.

Kazdy vysledek musi ulozit:
- `model_id`,
- `model_label`,
- `model_params_used`,
- `setting_profile`,
- `lag_budget_s`,
- `segment_config`,
- `runtime_config_snapshot`, pokud existuje.

## 8. Navrzeny backend

Novy router:

```text
backend/app/routers/latemic.py
```

Nova service:

```text
backend/app/services/latemic_service.py
```

Runtime layout:

```text
runtime/late_mic/
  runs/
    late_<timestamp>_<id>/
      run.json
      events.jsonl
      report.json
      report.csv
      segments/
        seg_0001.wav
        seg_0001.json
      transcripts/
        seg_0001__whisper_cpp_base.txt
```

Minimalni REST API:

| Endpoint | Ucel |
|---|---|
| `POST /api/latemic/runs` | Vytvori LateMic run a plan. |
| `GET /api/latemic/runs` | Historie LateMic runu. |
| `GET /api/latemic/runs/{run_id}` | Stav runu, metriky, report. |
| `POST /api/latemic/runs/{run_id}/segments` | Prijme hotovy mic segment jako PCM/WAV. |
| `POST /api/latemic/runs/{run_id}/stop` | Ukonci zachytavani a dopocita report. |
| `POST /api/latemic/runs/{run_id}/cleanup` | Vynuti mazani docasneho audia podle politiky. |
| `GET /api/latemic/runs/{run_id}/export.csv` | CSV export vysledku. |

Segment upload by mel byt REST binary/multipart, ne WebSocket stream.

Metadata segmentu:

```json
{
  "run_id": "late_...",
  "segment_id": "seg_0001",
  "capture_started_at": "...",
  "capture_ended_at": "...",
  "sample_rate": 16000,
  "channels": 1,
  "sample_width_bytes": 2,
  "duration_s": 10.24,
  "closed_by": "pause|hard_stop|manual",
  "rms_dbfs_p50": -28.4,
  "peak_dbfs": -4.1,
  "clipping_pct": 0.0,
  "silence_ms": 620,
  "sha256": "..."
}
```

## 9. Prepis segmentu

Backend scheduler ma oddelit tri casy:

```text
capture time  -> segment je hotovy
queue wait    -> segment ceka na model
decode time   -> model prepisuje
```

Pro spravedlive porovnani modelu existuji dve varianty:

### Varianta A: real-lag mode

Jeden aktivni model/profil prepisuje segmenty v poradi, jak prichazeji.

Vyhoda:
- nejlepe ukazuje realne uzivatelske zpozdeni.

Nevyhoda:
- pro vice modelu neni audio uplne stejne, pokud se segmenty znovu nahravaji.

### Varianta B: fair-benchmark mode

Jeden mic segment se pouzije pro vsechny vybrane modely/profily.

Vyhoda:
- stejne audio pro vsechny modely,
- lepsi porovnani kvality.

Nevyhoda:
- pokud se modely zpracovavaji sekvencne, `queue_wait_s` neni vlastnost modelu.

Doporuceni pro prvni verzi:
- implementovat fair-benchmark mode,
- reportovat zvlast `decode_s` a `queue_wait_s`,
- ranking delat primarne podle `max_visible_lag_s_without_queue = segment_audio_s + decode_s`,
- skutecny runtime report ukazat i s `queue_wait_s`.

## 10. Vysledky a ranking

LateMic ma radit modely podle dvojice:

```text
kvalita textu x nejmensi zpozdeni
```

Pokud je dostupna reference:
- WER,
- CER,
- normalized WER,
- pocet chybejicich slov,
- pocet navic slov.

Pokud reference neni:
- delka prepisu,
- stabilita segmentu,
- opakovatelnost,
- manual quality label,
- varovani `no_reference`.

Doporuceny ranking:

1. Vyhodit vysledky `over_budget_s > 0`, pokud operator chce strict limit.
2. Zbyvajici seradit podle kvality.
3. Pri podobne kvalite vybrat mensi `max_visible_lag_s`.
4. Pri podobnem lagu vybrat mensi CPU/RAM/RTF.

Report ma zobrazit:

| Model | Profil | Lag budget | Segment | Decode | Max lag | Over | WER | RTF | Stav |
|---|---|---:|---:|---:|---:|---:|---:|---:|---|

## 11. Mazani docasneho audia

Docasne audio je citlive runtime artefakt.

Politiky:

| Volba UI | Chovani |
|---|---|
| `Ihned po prepisu` | WAV se smaze hned po ulozeni textu a metrik. |
| `Po 1 minute` | Default pro fyzicke testy. |
| `Po 5 minutach` | Vhodne pro rychlou kontrolu problemu. |
| `Ponechat do konce runu` | Segmenty zustanou jen do stopu celeho testu. |
| `Ponechat pro debug` | Vyrazne oznacene; pouzit jen vedome. |

Po smazani zustava:
- hash segmentu,
- delka,
- mic proof statistiky,
- modelovy vystup,
- timing a metriky,
- priznak `audio_deleted=true`,
- `audio_deleted_at`.

## 12. Honest labeling pravidla

Zakazana tvrzeni:
- `live stream`,
- `online mic`,
- `mic_ws_final`,
- doplneni textu z reference,
- doplneni textu z historie.

Povolena tvrzeni:
- `late_mic_segment`,
- `delayed mic`,
- `prepis z docasneho mikrofonniho segmentu`,
- `transcript_source=latemic_segment_wav`.

Kazdy radek historie musi mit:

```json
{
  "mode": "late_mic",
  "transcript_source": "latemic_segment_wav",
  "segment_id": "seg_0001",
  "audio_deleted": true
}
```

## 13. Minimalni implementacni faze

### L0: Dokumentace a kontrakt

Hotovo, kdyz:
- existuje tento navrh,
- je odsouhlasena definice lagu,
- je jasne, co se nesmi michat s `/benchmark/mic`.

### L1: Sdilene frontend helpery

Hotovo, kdyz:
- mic capture z `MicSession.tsx` je vytazen do reusable hooku,
- mic proof metriky jsou sdilene,
- `/benchmark/mic` se chova stejne jako pred zmenou.

### L2: LateMic backend skeleton

Hotovo, kdyz:
- existuje `/api/latemic/runs`,
- segment upload ulozi WAV + manifest,
- cleanup smaze audio podle politiky,
- event log ma monotonnni casy.

### L3: Single model segment test

Hotovo, kdyz:
- jeden model prepise jeden mic segment,
- vysledek ulozi `decode_s`, `rtf`, `max_visible_lag_s`, `audio_deleted`,
- text je ulozen jen z tohoto segmentu.

### L4: Model x lag sweep

Hotovo, kdyz:
- UI umi rozsah `5/10-60 s` po `5 s`,
- plan ukaze pocet trialu,
- report porovna modely pro kazdy lag budget.

### L5: Automaticke ladeni parametru

Hotovo, kdyz:
- LateMic umi pouzit stejny tuning sweep princip jako `/benchmark/mic`,
- kazda varianta uklada zmenene parametry,
- report ukaze nejmensi lag, kde je kvalita jeste dobra.

### L6: Validace a Dashboard

Hotovo, kdyz:
- existuje smoke test bez fyzickeho mikrofonu pro service vrstvy,
- existuje manualni fyzicky test s mikrofonem,
- Dashboard rozlisuje `mic live` a `late mic`,
- cleanup job nema orphan WAV soubory.

## 14. Hlavni rizika

| Riziko | Protiopatreni |
|---|---|
| Zamena LateMic za live stream | Viditelne UI oznaceni + `mode=late_mic`. |
| Nespravedlive porovnani vice modelu | Oddelit `decode_s` od `queue_wait_s`; fair-benchmark mode pouzije stejny segment. |
| Prilis dlouhe segmenty pro maly lag | Validace: segment target nesmi byt vetsi nez lag budget minus rezerva. |
| Ztrata slov na hranici segmentu | Pause-aware konec + overlap. |
| Soukrome audio zustane na disku | Default delete po prepisu + 60 s, debug retain jen explicitne. |
| Model warm-up zkresli prvni segment | Reportovat `warmup=true`; prvni segment muze byt sync/warmup. |
| CPU kontence pri paralelnim porovnani | Prvni verze spousti modely sekvencne, paralelni mode az pozdeji. |

## 15. Doporucena prvni verze

Nejmensi rozumna verze:

1. `/benchmark/latemic` jako nova zalozka.
2. Mikrofonni capture z prohlizece do segmentu.
3. Segmenty: `5/10/15/20/30 s` preset, plus pause-aware konec.
4. Lag budgets: `10-60 s` po `5 s`; `5 s` jako advanced.
5. Jeden vybrany model nebo mala skupina modelu.
6. Fair-benchmark mode nad stejnym segmentem.
7. Default mazani: po prepisu + `60 s`.
8. Report: model, parametry, segment, decode, max lag, WER/CER pokud je reference.

To splni hlavni cil: rychle zjistit, ktery model ma nejlepsi kvalitu pri nejmensim zpozdeni, aniz by se vysledek mylne tvaril jako online stream.
