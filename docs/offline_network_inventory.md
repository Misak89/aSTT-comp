# Offline Network Inventory (2026-03-27)

Tento dokument mapuje, které části projektu mohou sahat na internet, proč, a jak se chovají v offline režimu.

## Globální logging

- Modul: `packages/common/network_access.py`
- Log soubor: `runtime/network_access/network_access_log.jsonl`
- Každý záznam obsahuje:
  - `ts_utc`, `component`, `action`, `reason`, `target`, `outcome`, `strict_offline`

## Strict offline režim

- Zapnutí: `ASTT_STRICT_OFFLINE=1`
- Production default:
  - pokud `ASTT_STRICT_OFFLINE` není nastaveno a `ASTT_APP_ENV=production` (nebo `ASTT_ENV/APP_ENV/ENV/FASTAPI_ENV=production`), strict offline se zapne automaticky
- Chování:
  - síťově závislé části vyhodí chybu před pokusem o přístup
  - do logu se zapíše `outcome=blocked_offline_mode`

## Přehled síťových míst

1. YouTube ingest (`yt-dlp`, titulky, audio stream)
- Soubory:
  - `packages/ingest/youtube/stream_pipe.py`
  - `packages/ingest/youtube/yt_dlp_fetcher.py`
  - `backend/app/services/library_service.py`
  - `scripts/download_audio.py`
  - `scripts/tuning_worker.py` (fallback při chybějící lokální cache)
- Důvod:
  - vyhledání videí
  - stažení titulků
  - stažení/stream audio pro benchmark
- Offline alternativa:
  - používat lokální `runtime/audio_cache/*.wav`
  - mít předem lokální titulky v `runtime/library/subtitles`
  - nepoužívat YouTube search/download endpointy

2. Host clock audit (externí HTTP Date)
- Soubor: `packages/benchmarks/runners/host_telemetry.py`
- Důvod:
  - porovnání lokálního času vůči externím serverům (Cloudflare/Google)
- Offline chování:
  - při `ASTT_STRICT_OFFLINE=1` se probe přeskočí (`status=skipped_offline_mode`)

3. Moonshine auto-download modelu
- Soubor: `packages/adapters/moonshine_runner.py`
- Důvod:
  - když lokální moonshine model chybí, knihovna ho může stáhnout
- Offline alternativa:
  - mít model předem lokálně v `runtime/model_store/moonshine_*`

4. Frontend YouTube embed
- Soubor: `frontend/src/components/LiveJobPanel.tsx`
- Důvod:
  - `<iframe>` přehrávač z `youtube.com`
- Poznámka:
  - toto je browser-side síť, ne backend Python runtime

## Whisper a internet

- `whisper_cpp` samotný při inferenci internet nepotřebuje.
- `whisper-server.exe` také při lokálním běhu internet nepotřebuje.
- Potřebuje jen lokální:
  - `whisper-cli(.exe)`
  - lokální `.bin` model
  - lokální audio vstup
- Síť je potřeba jen pro ingest dat (typicky YouTube), ne pro samotný whisper výpočet.

## Doporučený postup pro 100% offline běh

1. Předzásobit lokální data:
- audio (`runtime/audio_cache`)
- titulky (`runtime/library/subtitles`)
- modely (`runtime/model_store`)
- doporučený batch příkaz:
  - `python scripts/offline_preseed.py --only-cs --download-audio --download-subtitles`

2. Spouštět s:
- `ASTT_STRICT_OFFLINE=1`
- pro produkci nastav `ASTT_APP_ENV=production` (strict offline se aktivuje i bez explicitního ASTT_STRICT_OFFLINE)

3. Nepoužívat endpointy/funkce:
- YouTube search/download
- cloud clock audit (automaticky se přeskočí)
- moonshine auto-download fallback (musí být lokální model)

4. Průběžně kontrolovat log:
- `python scripts/network_audit_report.py`
- doporučení: po každém benchmark/tuning běhu
