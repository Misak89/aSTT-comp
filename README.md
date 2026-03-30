# aSTT-comp
`web-up.cmd`
Open: `http://127.0.0.1:8012/benchmark`
`web-status.cmd` (v druhem okne)
`web-down.cmd`

## Stabilni spousteni webu
- `web-up.cmd`: doporuceny stabilni start v aktualnim okne (backend bezi, okno nezavirat).
- `web-up-build.cmd`: stejny stabilni start, ale predem vynuti frontend build.
- `web-up-bg.cmd`: volitelny start do noveho okna `aSTT-web`.
- `web-status.cmd`: zobrazi health a proces na portu 8012.
- `web-down.cmd`: ukonci proces, ktery posloucha na portu 8012.
- `web-restart.cmd`: stop + start v jednom kroku.

## Logika
- Hlavni produkcni vstup jsou root `web-*.cmd` soubory.
- Backend bezi na `127.0.0.1:8012`, frontend se servira z backendu.
- Pokud je potreba plny rebuild frontendu, pouzij `web-up-build.cmd`.
