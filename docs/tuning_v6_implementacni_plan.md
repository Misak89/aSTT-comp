# Tuning v6 - implementacni plan (long transcript + segmentace)

Doc-Meta:
- owner: engineering
- status: active
- doc_file: tuning_v6_implementacni_plan.md
- last_updated_utc: 2026-04-03T11:10:30Z
- review_due_utc: 2026-04-15T00:00:00Z

Navazuje na:
- `docs/tuning_v5_implementacni_plan.md`
- `docs/reports/refactor_v5_closure_2026-04-03.md`
- `docs/ARCHITECTURE.md`
- `docs/SECURITY_SUPPLY_CHAIN.md`

## Kontext
- V5 uzavrela stabilizacni cast P0-P3 a vedome odlozila long-run validation gate.
- V6 navazuje presne na odlozenou oblast: dlouhe prepisy a segmentace bez metodickeho mixu s online mic flow.
- V6 nepise znovu to, co uz je hotove ve v5 (event-store, lane discipline, monitoring policy), ale explicitne to reuseuje.

## V6 cile
1. Stabilni dlouhy prepis (30-240 min) na `/transcript`.
2. Segmentace importovanych dlouhych nahravek na `/library`:
   - preset: 5, 10, 15, 30, 45, 60 min,
   - manual body: max 21 bodu.
3. Pause-aware hranice segmentu (default tolerance +-2s, rozsiritelne).
4. Jeden logicky blok segmentu pod jednim radkem v `/transcript`.
5. Stabilni beh alespon 3 CZ modelu v long transcript rezimu.

## V6 mimo scope
1. Zmena priorit online mic pipeline (zustava v `real-mic-v4`).
2. Velky UI redesign mimo segmentacni a transcript tok.
3. Nova modelova integrace mimo existujici registr, dokud neprojde long-run gate.

## Encoding kontrakt (Windows)
1. V6 workflow je UTF-8 only.
2. `cp1250` / `windows-1250` se nesmi pouzit pro zadny runtime ani dokumentacni tok.
3. Pokud terminal neni UTF-8-safe, pouzit UTF-8 nativni cestu z `README.md` (`webctl.py` / `web.nu`).

## One-command kontrakt (non-physical gate)
- V6 implementace bezi po fazich, ale ne-fyzicky gate je definovan jednim prikazem:

```powershell
C:\Program Files\PowerShell\7\pwsh.exe -Command "$ErrorActionPreference='Stop'; .venv\Scripts\python scripts/supply_chain_guard.py; .venv\Scripts\python scripts/verify_docs_guard.py --head (git rev-parse HEAD); npm --prefix frontend run build; .venv\Scripts\python -m pytest tests/unit/test_benchmark_request_model.py tests/unit/test_library_segment_bundle.py tests/unit/test_tuning_event_store.py tests/unit/test_tuning_events_service.py tests/unit/test_tuning_event_validation.py -q"
```

- Tento prikaz pokryva guard/test cast bez fyzickeho hands-on audia.
- Fyzicke testy (realny poslech + ovladani hranic mysi/klavesami) provadi owner po kompletni implementaci.

## Stav realizace (2026-04-03)
1. `V6-S0 done`: intake + UTF-8 kontrakt + one-command gate.
2. `V6-S1 done`: backend segment bundle API + pause-aware snapping + validace + unit testy.
3. `V6-S2 done`: `/library` slicer UI (preset 5/10/15/30/45/60, manual body max 21, mysi timeline, micro-shift sipkami, audio preview).
4. `V6-S3 done`: `/transcript` umi segment bundle jako jeden logicky prepis (sekvencni segment processing + agregovany vystup).
5. `V6-S4 planned`: fyzicka long-run validace 30/120/240 min pro 3 CZ modely zustava na navazujici run.

## Follow-up poznamky (operacni)
1. Transient artefakt `Zdroj` v rootu repo:
   - muze byt vytvaren bezicim externim procesem/shellem,
   - neprovadet automaticke mazani behem aktivnich behu,
   - nejdriv overit puvod (PID/proces, cas vzniku) a teprve potom cistit.

## Implementacni faze

### Faze 0 - Intake and contract lock (0.5 dne)
**Cil**
- Uzamknout zavislosti a workflow pred samotnou implementaci.

**Kroky**
1. Dopsat OSS intake pro nove kandidaty (segmentace/VAD) v souladu s `SECURITY_SUPPLY_CHAIN`.
2. Potvrdit one-command non-physical gate.
3. Potvrdit reuse existujicich komponent (`wavesurfer`, benchmark/transcribe tok).

**DoD**
- Intake je aktualni.
- One-command gate je zdokumentovany a opakovatelny.

### Faze 1 - Backend segment domain (1-2 dny)
**Cil**
- Zavest canonical segment bundle model.

**Kroky**
1. Datovy model segmentu: body, segmenty, snap metadata, zdroj audio.
2. API pro preview/create/update/get segment bundle.
3. Validace: max 21 bodu, monotonicita casu, hranice v rozsahu nahravky.

**DoD**
- Bundle lze reproducibilne nacist a upravit.
- API vraci jasne chyby pro invalidni body.

### Faze 2 - Pause-aware snapping engine (1 den)
**Cil**
- Automaticky posun hranice do nejblizsi pauzy v toleranci.

**Kroky**
1. VAD/silence analyza audia.
2. Lokalni hledani pouze v okne tolerance kolem kazde hranice.
3. Ulozeni `snap_delta_ms`, `snap_reason`, `snapped=true/false`.

**DoD**
- Hranice se posunou jen pokud je validni pauza.
- Bez nalezu pauzy zustava original hranice.

### Faze 3 - `/library` slicer UI (1-2 dny)
**Cil**
- Kompaktni a ovladatelny kracec dlouhe nahravky.

**Kroky**
1. Preset rezim + manual rezim (max 21 bodu).
2. Interaktivni body na waveform timeline.
3. Micro shift klavesami (leva/prava sipka) a player preview.

**DoD**
- Uzivatel vytvori segmenty bez editace ciselnych poli.
- UI a API drzi identicky segment bundle.

### Faze 4 - `/transcript` orchestrace bundle (1-2 dny)
**Cil**
- Zpracovat segmenty jako jeden logicky transcript job.

**Kroky**
1. Predani bundle do transcript workflow.
2. Sekvencni zpracovani segmentu s agregovanym progressem.
3. Slouceny vysledek do jednoho transcript artefaktu s provenance po segmentech.

**DoD**
- V UI je jeden radek jobu + detail segmentu.
- Pri chybe segmentu je jasna diagnostika a resumable stav.

### Faze 5 - 3x CZ model stability gate (1-2 dny)
**Cil**
- Potvrdit stabilni provoz min. 3 CZ modelu pro long transcript.

**Kroky**
1. Test matrix: `faster_whisper_small_cs_int8`, `faster_whisper_medium_cs_int8`, `vosk_small_cs_0_4`.
2. Long-run scenare: 30, 120, 240 min + recovery/restart case.
3. Finalni report do docs + tracker status update.

**DoD**
- Vsechny 3 modely projdou minimalnim stability kriteriem.
- Long-run a recovery scenare jsou reprodukovatelne.

## Test checkpointy (kde + cca casy)

| Checkpoint | Cca cas od startu iterace | Misto testu | Typ |
|---|---:|---|---|
| CP0 contract check | T+0.5h | `scripts/supply_chain_guard.py`, `scripts/verify_docs_guard.py` | non-physical |
| CP1 backend segment API | T+3h | `/api/library/...segment...`, `runtime/library/...` | non-physical |
| CP2 pause-aware quality | T+5h | service unit testy + sample WAV fixtures | non-physical |
| CP3 slicer UX smoke | T+7h | `http://127.0.0.1:8012/library` | physical UI |
| CP4 transcript bundle smoke | T+9h | `http://127.0.0.1:8012/transcript` | physical UI + audio |
| CP5 model matrix 30/120/240 | T+12h az T+20h | `/transcript`, `runtime/jobs`, `runtime/transcripts` | physical long-run |
| CP6 release gate | T+20h+ | one-command non-physical gate + docs sync | mixed |

## Primarni soubory pro zmenu (bez duplicit vuci v5)
1. Backend:
   - `backend/app/models/library.py`
   - `backend/app/routers/library.py`
   - `backend/app/services/library_service.py`
   - `backend/app/routers/transcribe.py`
2. Frontend:
   - `frontend/src/pages/LibraryPage.tsx`
   - `frontend/src/components/transcribe/TranscribeJobPanel.tsx`
   - `frontend/src/api/client.ts`
   - `frontend/src/types/index.ts`
3. Testy:
   - `tests/unit/...` (segmentace + snapping)
   - `tests/integration/...` (bundle -> transcript tok)

## Akceptacni kriterium v6
1. Long transcript 30-240 min je funkcni a stabilni.
2. Segmentace v `/library` podporuje preset + max 21 manual bodu.
3. Pause-aware snapping funguje deterministicky podle tolerance.
4. `/transcript` zobrazi jeden logicky blok segmentoveho jobu.
5. 3 CZ modely projdou stability gate.
