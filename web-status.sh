#!/bin/bash
# web-status.sh - zobrazí stav backendu (Mac / Linux)
if curl -sf http://127.0.0.1:8012/api/health > /dev/null 2>&1; then
  echo "[web] UP:   http://127.0.0.1:8012"
else
  echo "[web] DOWN: backend neodpovídá na portu 8012"
fi
PID=$(lsof -ti:8012 2>/dev/null | head -1)
[ -n "$PID" ] && echo "[web] pid:  $PID" || echo "[web] pid:  -"
