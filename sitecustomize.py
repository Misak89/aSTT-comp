from __future__ import annotations

import os
import sys

# Project-wide UTF-8 console safety for Windows terminals (cmd/PowerShell).
# This module is auto-imported by Python when available on sys.path.
os.environ.setdefault("PYTHONUTF8", "1")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")

for _stream_name in ("stdout", "stderr"):
    _stream = getattr(sys, _stream_name, None)
    if _stream is None:
        continue
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        # Some redirected streams do not support reconfigure.
        pass
