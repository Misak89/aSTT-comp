# Tuning v7 - implementacni plan (CS Online Mic Orchestrator)

Doc-Meta:
- owner: engineering
- status: active
- doc_file: tuning_v7_implementacni_plan.md
- last_updated_utc: 2026-04-04T10:26:47Z
- review_due_utc: 2026-04-15T00:00:00Z

Navazuje na:
- `docs/PLAN_TRACKER.md`
- `docs/tuning_v6_implementacni_plan.md`
- `docs/ARCHITECTURE.md`
- `docs/RUNBOOK.md`

## Hlavni cil (v7)
- `CS Online Mic Orchestrator`: na jednom zivem mikrofonnim streamu sekvencne testovat vice modelu/profilu s kontinualni casovou osou a presnym logovanim `timestamp + metadata` pro vyhodnoceni `kvalita x latence x HW`.

## Scope v7
1. `online mic only` (`cs` first).
2. Sekvencni runner nad jednim streamem (`run_i stop -> run_i+1 start`) bez resetu globalniho casu.
3. Presne eventy: `model`, `settings`, `diag`, `start/end`, `reason`, `segment_id`, `run_id`, `sequence_id`.
4. Dvoji vystup: human transcript + machine event log (casove konzistentni).
5. KPI triada: kvalita, latence, HW.
6. Cil latency lane: idealne `5-8 s`, hard limit `12 s`.
7. Akceptacni minimum: `3-5` modelu v jednom online scenari + jednotna porovnavaci tabulka bez rucniho cisteni.

## Mimo scope v7
1. Simulacni flow vydavany za online stream (zakaz).
2. Batch transcript orchestrace pro dlouhe soubory (resi v6 lane).
3. Paralelni multi-stream orchestrator (v7 je sekvencni jeden stream).

## Implementacni faze

### v7-S0 - Data contract + event schema
**Cil**
- Uzamknout canonical schema pro casove auditovatelny online orchestrator.

**Kroky**
1. Definovat entity: `run_id`, `sequence_id`, `segment_id`, `global_timeline_ms`.
2. Definovat event contract vcetne `reason` kodu pro prechody stavu.
3. Definovat pravidla casove konzistence (`no time reset`, monotonie).

**DoD**
- Schema je stabilni a pouzitelne pro backend i dashboard tabulku.

### v7-S1 - Backend sekvencni orchestrator nad jednim live streamem
**Cil**
- Sekvencni prepinani modelu/profilu bez preruseni globalni casove osy.

**Kroky**
1. Implementovat orchestrator stavovy automat (`run_i stop -> run_i+1 start`).
2. Drzet jeden audio vstup, modely prepinat sekvencne podle planu.
3. Ukladat metadata o modelu/profilu a diagnostice pro kazdy run.

**DoD**
- Jeden online beh obsahuje vice runu bez resetu globalniho casu.

### v7-S2 - Presne timestampovane event logy
**Cil**
- Auditovatelny timeline kazdeho prechodu stavu.

**Kroky**
1. Emitovat eventy `start/end/state_transition/error/timeout` s `reason`.
2. Zahrnout `run_id`, `sequence_id`, `segment_id`, `model_id`, `profile_id`.
3. Validovat monotonii timestampu a chyby logovat explicitne.

**DoD**
- Kazdy prechod je dohledatelny jednim eventem s metadaty.

### v7-S3 - Dvoji vystup (human + machine) s casovou konzistenci
**Cil**
- Mit citelny transcript i strojovy event log ze stejne casove reality.

**Kroky**
1. Skladat human transcript po runech bez casoveho driftu.
2. Zapisovat machine event log paralelne s transcript pipeline.
3. Kontrolovat vazbu transcript useku na event timeline.

**DoD**
- Transcript a event log sedi v case (zadne nesoulady osy).

### v7-S4 - KPI vypocet + jednotna porovnavaci tabulka
**Cil**
- V jednom vystupu porovnat `kvalita x latence x HW` napric runy.

**Kroky**
1. Spocitat KPI per run a agregaci per model/profil.
2. Vynutit jednotny export format tabulky.
3. Oznacit runy prekrocujici latency hard limit `12 s`.

**DoD**
- Porovnavaci tabulka je exportovatelna bez rucnich uprav.

### v7-S5 - Validacni run 3-5 modelu + DoD kontrola
**Cil**
- Potvrdit realnou provozni pouzitelnost v7 orchestratoru.

**Kroky**
1. Provest jeden online scenar s 3-5 modely.
2. Overit auditovatelnost event timeline a casovou konzistenci vystupu.
3. Overit KPI tabulku a latency limit.

**DoD**
- V7 DoD body jsou splnene a reprodukovatelne.

## DoD v7
1. Jeden online beh obsahuje vice modelu sekvencne bez resetu casu.
2. Kazdy prechod ma auditovatelny event s metadaty.
3. Transcript a event log jsou casove konzistentni.
4. KPI tabulka je exportovatelna a porovnatelna bez manualnich zasahu.
5. Latence neprekroci hard limit `12 s` (cilove `5-8 s`).
