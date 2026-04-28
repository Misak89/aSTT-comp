#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"
if [[ ! -x ".venv/bin/python" ]]; then
  echo "[web] chyba: .venv/bin/python neexistuje"
  echo "[web] spusť: python3 -m venv .venv && .venv/bin/python -m pip install -r backend/requirements.txt"
  exit 2
fi

exec .venv/bin/python -X utf8 scripts/webctl.py up "$@"
