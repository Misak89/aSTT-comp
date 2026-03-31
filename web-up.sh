#!/bin/bash
# web-up.sh - spustí backend (Mac / Linux)
set -e
cd "$(dirname "$0")"
if [ ! -d ".venv" ]; then
  echo "[web] chyba: .venv neexistuje. Spusť: python3 -m venv .venv && .venv/bin/pip install -r backend/requirements.txt"
  exit 1
fi
if [ ! -d "frontend/dist" ]; then
  echo "[web] frontend/dist chybí - spouštím build..."
  npm --prefix frontend install && npm --prefix frontend run build
fi
echo "[web] start: http://127.0.0.1:8012"
.venv/bin/python -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8012
