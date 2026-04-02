# Runbook (Operations)

## 1. Stabilni start webu
Pouzivej root skripty:
- `web-up.cmd` - stabilni start v aktualnim okne
- `web-up-build.cmd` - stejny start + frontend build
- `web-up-bg.cmd` - start do noveho okna
- `web-status.cmd` - health + proces na portu `8012`
- `web-down.cmd` - stop procesu na portu `8012`
- `web-restart.cmd` - stop + start

URL:
- aplikace: `http://127.0.0.1:8012/benchmark`
- health: `http://127.0.0.1:8012/api/health`

## 2. Bezna diagnostika
1. Over health endpoint.
2. Over listener na portu 8012.
3. Zkontroluj posledni backend log.
4. Pri stale chybe udelej `web-restart.cmd`.

## 3. Kde jsou logy a runtime data
- Mic session: `runtime/mic_sessions/`
- Mic event stream: `runtime/logs/mic_sequence_events.jsonl`
- Tuning jobs: `runtime/tuning/`
- Network audit: `runtime/network_access/network_access_log.jsonl`

## 4. Offline provoz
- Pro strict offline nastav `ASTT_STRICT_OFFLINE=1`.
- Pro produkcni beh drzet offline inventory aktualni:
  - `docs/offline_network_inventory.md`
  - `python scripts/network_audit_report.py`

## 5. Incident pravidlo
Pri oprave incidentu:
1. zapsat co se stalo a fix do `docs/session_log.md`,
2. pokud se meni start/provozni postup, aktualizovat tento `docs/RUNBOOK.md`.
