#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"
if [[ ! -x ".venv/bin/python" ]]; then
  echo "[web] chyba: .venv/bin/python neexistuje"
  exit 2
fi

exec .venv/bin/python -X utf8 scripts/webctl.py restart "$@"
