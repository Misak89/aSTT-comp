# Tuning v5 - implementacni plan (post-audit stabilization)

Doc-Meta:
- owner: engineering
- status: active
- doc_file: tuning_v5_implementacni_plan.md
- last_updated_utc: 2026-04-02T07:11:11Z
- review_due_utc: 2026-04-15T00:00:00Z

Navazuje na:
- `docs/audit_conclusion_2026-04-02.md`
- `docs/reports/audit_conclusion_2026-04-02.json`
- `docs/reports/audit_conclusion_2026-04-02.jsonl`
- `docs/tuning_v4_implementacni_plan.md`
- `docs/tuning_v4_tasky.md`

## Kontext
- Audit potvrdil tri dominantni root causes:
  - monitoring overhead (frontend timery + backend heavy scan),
  - metodicka nehomogenita latency metrik (live/probe/proxy mix),
  - file-based polling orchestrace.
- Cilem v5 neni pridani dalsich modelu, ale stabilizace meritelnosti, reprodukovatelnosti a HW efektivity.

## V5 cile
1. Snizit monitoring overhead bez ztraty diagnostiky.
2. Zavest metodicky cisty benchmark lane pro live rozhodovani.
3. Nahradit file polling event-native runtime tokem.
4. Zvysit diagnostikovatelnost root-cause chyb pri dlouhych behach.
5. Pripravit podklad pro bezpecne rozsireni model matrix po stabilizaci.

## V5 mimo scope
1. Rozsireni na Android/iOS.
2. Velke UI redesigny bez meritelneho runtime prinosu.
3. Pridavani novych STT adapteru pred stabilizaci jadernych metrik.

## Workstreamy
1. WS-A Monitoring budget + scan policy.
2. WS-B Measurement discipline (strict_live vs batch_proxy).
3. WS-C Event-store orchestrace.
4. WS-D Test & validation harness (long-run, race, recovery).
5. WS-E Docs + governance synchronization.

## Dokumentacni formaty pro v5
1. Tento plan je triplet artefakt:
   - `docs/tuning_v5_implementacni_plan.md` (human read),
   - `docs/tuning_v5_implementacni_plan.json` (canonical structured snapshot),
   - `docs/tuning_v5_implementacni_plan.jsonl` (append-only timeline).
2. `MD` je pouze cteci vrstva pro lidi.
3. `JSON` je canonical strojova reprezentace.
4. `JSONL` drzi timeline/provenance eventy.
5. U vsech tri formatu musi metadata obsahovat `doc_file` shodne s nazvem souboru.
6. Snapshot/report soubory pouzivaji suffix data v nazvu (`_YYYY-MM-DD`), pokud nejde o stabilni living doc.

## Faze implementace

### Faze 0 - Baseline and instrumentation lock (1-2 dny)
**Cil**
- Zafixovat baseline pred zmenami a mereni impactu.

**Vystupy**
- Baseline snapshot CPU/RAM overhead monitoringu.
- Seznam endpointu/timeru, ktere tvori sampling budget.
- Jasna metricky sledovana "before" linie.

**DoD**
- Existuje baseline report v `docs/reports/`.
- Vsechny navazne faze maji stejny merici protokol.

### Faze 1 - Monitoring budget and phased scanning (2-4 dny)
**Cil**
- Zredukovat zbytecne wake-up a burst scanning.

**Implementace**
- Zavest centralni monitoring cadence.
- Default `/api/health/processes` prepnout na `fast`.
- `slow/full` scan jen explicitne nebo podle policy triggeru.
- TTL cache pro cmdline/exe a lehci PID metadata cestu.

**DoD**
- Mereny pokles CPU overhead monitoringu proti baseline.
- Beze zmeny diagnosticke hodnoty pro bezne provozni incidenty.

### Faze 2 - Latency discipline split (2-4 dny)
**Cil**
- Eliminovat metodicky mix live/proxy dat v jednom ranking lane.

**Implementace**
- Data schema rozsirit na tvrde oddelene rezimy:
  - `strict_live`,
  - `probe_online`,
  - `batch_proxy`.
- Scoring/ranking gating: never mix `strict_live` s proxy.
- UI explicitne zobrazi quality class + warningy.

**DoD**
- Rozhodovaci reporty neobsahuji smichane latency tridy.
- "Best model" pro live je odvozen pouze z live-valid metrik.

### Faze 3 - Event-store orchestration (4-7 dni)
**Cil**
- Nahradit file polling append-only event tokem.

**Implementace**
- Zavest event store (preferovane SQLite WAL).
- Worker zapisuje eventy inkrementalne.
- Backend agreguje snapshot z event streamu.
- Frontend cte inkrementalne snapshoty bez multipoll kolizi.

**DoD**
- `progress/status` polling neni primarni runtime kanal.
- Lepsi traceability: timeline + state reconstruction z event logu.

### Faze 4 - Runtime hardening and tests (3-5 dni)
**Cil**
- Potvrdit stabilitu po arch zmenach.

**Implementace**
- Integracni testy: long-run, cancel/retry, race, recovery.
- Soak testy pro scenare 1-4.
- Opravit environment-sensitive test setup (`tempfile` permissions path).

**DoD**
- Reproducibilni pass dlouhych testu.
- Dokumentovane limity + mitigace.

### Faze 5 - Release gate and rollout (2-3 dny)
**Cil**
- Uzavrit v5 jako stabilizacni baseline pro dalsi rozvoj.

**Implementace**
- Finalni validation report.
- Hard gate checklist (metodika, HW overhead, reprodukovatelnost, incident traceability).
- Aktualizace navaznych docs (`ARCHITECTURE`, `RUNBOOK`, `PLAN_TRACKER`).

**DoD**
- Schvalena v5 baseline.
- Jasne povolene dalsi kroky pro model expansion.

## Milniky
1. M0: Baseline report hotovy.
2. M1: Monitoring overhead snizen a scan policy stabilni.
3. M2: Strict_live ranking oddelen od proxy.
4. M3: Event-store orchestrace aktivni.
5. M4: Long-run validation pass.
6. M5: Release gate v5 uzavren.

## Rizika a mitigace
1. Riziko: zmena orchestrace zavede race/regrese.
Mitigace: phased rollout + canary + event replay testy.
2. Riziko: pokles diagnostiky po zlevneni monitoringu.
Mitigace: hard minimal signal contract a explicitni escalation path na full scan.
3. Riziko: nejasny ownership mezi backend/frontend.
Mitigace: workstream owner per faze + DoD gate per milnik.

## Akceptacni kriterium v5
- Monitoring overhead je meritelne nizsi.
- Live rozhodovani je metodicky ciste (bez proxy contamination).
- Runtime tok je event-native a auditovatelny.
- Reprodukovatelnost a diagnostika jsou lepsi nez v baseline.
