#!/bin/bash
# web-restart.sh - restart backendu (Mac / Linux)
cd "$(dirname "$0")"
bash web-down.sh
bash web-up.sh
