# Tuning v3 - popis zmen

Branch: `feature/tuning-v3`  
Base commit: `0f5afc2`

## Co je v teto vetvi

1. Tuning UI a workflow
- trial tabulka: sloupec `Konec` (cas dokonceni trialu)
- ETA odhad podle dosavadni rychlosti jobu
- vicekriterialni razeni trialu (priorita podle poradi zatrzeni)
- historie jobu: stabilnejsi razeni, cislovani, zkracene zobrazeni poslednich jobu
- ovladani textoveho detailu (`Zobrazit/Skryt texty`) s jednotnou ikonou

2. Word diff a textova diagnostika
- dvouradkove zarovnani prepisu a reference
- rozsirene znaceni typu chyb (vcetne drobnych zamen)
- upravy zobrazeni detailu per video

3. Backend metriky a tuning data
- doplneno `trial_finished_at` do modelu a vystupu
- rozsirena podpora RAM/RSS metrik v tuning pipeline
- upravy workeru pro robustnejsi zapis trial resultu

4. Offline a provozni tooling
- `scripts/network_audit_report.py`
- `scripts/offline_preseed.py`
- `scripts/start_web_app.ps1`
- `scripts/start_web_app_background.ps1`
- `start_web_app.cmd`
- doplnky pro smoke/soak/hw-matrix/decision report

5. Rozhodovaci vrstva
- pridana sluzba `backend/app/services/tuning_decision.py`
- navazne utility pro report doporuceni

6. Dokumentace
- `docs/offline_network_inventory.md`
- `docs/tuning_v4_inteligentni_plan.md` (navrh dalsi generace tuningu)

## Co jeste neni soucasti
- v4 inteligentni tuner je zatim plan, ne hotova implementace planneru.
