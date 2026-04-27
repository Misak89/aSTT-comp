# Chat Export Pro Tvorbu Dokumentace (2026-04-21)

Exportovano: 2026-04-21
Zdroj: viditelna user-assistant konverzace v tomto vlakne.
Poznamka: Tento export neobsahuje interni system/developer instrukce, pouze viditelny chat.

## Turn 1 — User
```
popis strucne dokumentaci projektu
```

## Turn 1 — Assistant
```
Dokumentace je v projektu navrzená jako rizeny „single source of truth“ system:

1. Startovaci poradi je pevne dane: README.md -> AGENTS.md -> docs/PLAN_TRACKER.md.
2. AGENTS.md je nejvyssi autorita pravidel (cile produktu, priority, hard rules, DoD).
3. README.md resi quickstart: instalace, spusteni, zakladni governance dokumentace.
4. docs/PLAN_TRACKER.md je jediny zdroj aktualniho planu/stavu milniku (v4–v7, readiness).
5. Detailni oblasti jsou oddelene do specializovanych dokumentu: docs/ARCHITECTURE.md, docs/RUNBOOK.md, CONTRIBUTING.md, docs/DOCS_GOVERNANCE.md.
6. Kazda zmena ma byt dohledatelna pres docs/session_log.md a kontrolovana CI guardem.
```

## Turn 2 — User
```
popis hyrearchii, logiku ...
```

## Turn 2 — Assistant
```
Jasne, tady je strucna hierarchie + logika dokumentace:

1. Ridici vrstva (autorita pravidel)
   AGENTS.md = nejvyssi autorita.
   Pokud je konflikt mezi dokumenty, rozhoduje AGENTS.

2. Vstupni/orientacni vrstva
   README.md = jak projekt chapat a spustit.
   Povinne cteni zacina tady.

3. Ridici stav projektu (single source of truth pro plan)
   docs/PLAN_TRACKER.md = jediny platny aktualni stav planu/milniku.

4. Specializovane provozni dokumenty
   docs/ARCHITECTURE.md, docs/RUNBOOK.md, CONTRIBUTING.md, docs/DOCS_GOVERNANCE.md = detaily podle tematu.

5. Evidence a audit stopa zmen
   docs/session_log.md + reporty v docs/reports/ = co se realne provedlo a s jakym dopadem.

Logika prace se zmenou (workflow):
1. Zmenis scope v roadmap/task docs.
2. Aktualizujes docs/PLAN_TRACKER.md (last_updated_utc, statusy).
3. Zapises dopad do docs/session_log.md.
4. Kdyz zmena zasahuje provoz/architekturu, upravis i ARCHITECTURE/RUNBOOK.
5. CI docs-guard overi konzistenci.
```

## Turn 3 — User
```
jak prenest kvalitne cela pravidla
pro dokumentaci na desktop uzivatelskou aplikaci
je potreba neco doplnit?
```

## Turn 3 — Assistant
```
Ano, doplnit je potreba.
Soucasna pravidla v docs/DOCS_GOVERNANCE.md a CONTRIBUTING.md jsou silna pro interni vyvoj,
ale pro desktop user app chybi explicitni user-facing vrstva.

Doporucene minimum:
1. Pridat povinne desktop docs:
   - docs/USER_GUIDE.md
   - docs/TROUBLESHOOTING_DESKTOP.md
   - docs/DESKTOP_RELEASE_POLICY.md
   - docs/PRIVACY_TELEMETRY.md
2. Pridat compatibility contract:
   - OS/verze, HW minimum, mic opravneni, offline/online rezimy.
3. Rozsirit mapovaci pravidla:
   - desktop runtime/installer/update => update runbook + release policy.
   - zmena UI textu/flow => update user guide + screenshot evidence.
4. Rozsirit CI guard:
   - zmena desktop/installer/updater souboru => vyzadovat docs update.
5. Pridat support/logging standard:
   - umisteni logu per OS, export diagnostiky, redakce citlivych dat.

Co nemenit:
- Autoritativni hierarchii README -> AGENTS -> PLAN_TRACKER.
- Triplet MD+JSON+JSONL pro nove non-core artefakty.
```

## Turn 4 — User
```
Uloz tento cely chat do MD ke stazeni
Pro tvorbu dokumentace
```
