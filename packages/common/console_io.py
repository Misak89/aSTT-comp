from __future__ import annotations

import os
import sys


def configure_console_io() -> None:
    """
    Nastaví robustní UTF-8 výstup pro CLI skripty na Windows.
    Cíl: zabránit UnicodeEncodeError na CP1250 konzoli (emoji/symboly).
    """
    os.environ.setdefault("PYTHONUTF8", "1")
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        if stream is None:
            continue
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            # Některé streamy (např. při redirectu) reconfigure nepodporují.
            pass

