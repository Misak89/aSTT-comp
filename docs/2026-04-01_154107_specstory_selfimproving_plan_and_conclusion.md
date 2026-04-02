# SpecStory Self-Improving Plan And Conclusion

Doc-Meta:
- owner: engineering
- status: active
- last_updated_utc: 2026-04-02T07:11:11Z
- review_due_utc: 2026-04-15T00:00:00Z

## Strucny plan
1. Automaticky extrahovat opakovane chyby ze `.specstory/history/*.md`.
2. Normalizovat je na sablony (maskovani path/url/id/cisel), aby slo mereni opakovani.
3. Pocitat prioritu podle jednotne matice (dopad, blokace, frekvence, detekovatelnost, narocnost).
4. Generovat 2 vystupy:
   - `docs/KNOWN_FAILURES.md` (kratke pouceni + akce),
   - `docs/reports/specstory_failures.json` (strojove statistiky).
5. Udrzovat stav v `docs/reports/specstory_pattern_state.json` (trend + learned rules), aby se system pri dalsich behach zlepsoval.

## Zaver
- Implementace je nasazena skriptem `scripts/specstory_failure_learning.py`.
- Vystupy jsou aktualne generovany a pripraveny pro pravidelny beh po kazde session.
- Self-improving mechanika bezi pres persistent state:
  - porovnani trendu mezi behy,
  - auto-promoce opakovanych neklasifikovanych vzoru do learned rules.
