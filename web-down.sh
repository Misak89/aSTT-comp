#!/bin/bash
# web-down.sh - ukončí backend na portu 8012 (Mac / Linux)
PIDS=$(lsof -ti:8012 2>/dev/null)
if [ -z "$PIDS" ]; then
  echo "[web] stop: nic neběželo"
else
  echo "$PIDS" | xargs kill -9
  echo "[web] stop: ukončeno (pid: $PIDS)"
fi
