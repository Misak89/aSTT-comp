# Security Supply Chain Policy

Doc-Meta:
- owner: engineering
- status: active
- last_updated_utc: 2026-04-01T16:07:44Z
- review_due_utc: 2026-04-15T00:00:00Z

## Cíl
- Zabránit zavlečení škodlivého kódu při inspiraci, stahování a build procesu.
- Držet pouze licence vhodné pro možné komerční nasazení.

## 1) Inspirace (research gate)
- Každý nový OSS kandidát zapsat do `docs/reports/oss_intake_register.json`.
- Povinně evidovat:
  - zdroj URL,
  - verze/tag/commit,
  - SPDX licence,
  - threat notes (proč je to bezpečné / rizikové).
- Bez zápisu v intake registru se kandidát nesmí adoptovat.

## 2) Stahování (download gate)
- Povolené zdroje jen přes HTTPS a jen z allowlist hostů:
  - `github.com`, `raw.githubusercontent.com`, `pypi.org`, `files.pythonhosted.org`, `registry.npmjs.org`, `npmjs.com`.
- Python:
  - `backend/requirements.txt` musí mít pinned verze (`==`),
  - `backend/requirements.lock` musí mít `--hash=sha256`.
- Node:
  - `frontend/package-lock.json` je povinný,
  - v `package.json` zákaz `latest`, `*`, `git+`, `file:`, `http(s):`.

## 3) Build (build gate)
- Build provádět pouze z lock souborů.
- Každá změna dependency souborů vyžaduje update intake registru.
- Guardy:
  - `python scripts/supply_chain_guard.py`
  - `python scripts/verify_docs_guard.py --base <sha> --head <sha>`

## 4) Licence policy
- Allowlist: `MIT`, `Apache-2.0`, `BSD-2-Clause`, `BSD-3-Clause`, `ISC`, `PSF-2.0`
- Conditional: `MPL-2.0` (jen po explicitním schválení)
- Denylist: `AGPL`, `GPL`, `LGPL`, `SSPL`

## 5) CI vynucení
- `supply-chain-guard` musí projít v CI.
- Bez průchodu guardu se merge blokuje.
