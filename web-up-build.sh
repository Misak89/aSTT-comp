#!/bin/bash
# web-up-build.sh - build frontendu + spustí backend (Mac / Linux)
set -e
cd "$(dirname "$0")"
if [ ! -d ".venv" ]; then
  echo "[web] chyba: .venv neexistuje. Spusť: python3 -m venv .venv && .venv/bin/pip install -r backend/requirements.txt"
  exit 1
fi
echo "[web] frontend build..."
npm --prefix frontend install && npm --prefix frontend run build
echo "[web] start: http://127.0.0.1:8012"
.venv/bin/python -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8012
