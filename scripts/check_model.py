#!/usr/bin/env python
"""
Test připravenosti konkrétního STT modelu.
Použití: python scripts/check_model.py whisper_cpp_small
"""
from __future__ import annotations
import sys
import json
import urllib.request
import urllib.error
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from packages.common.console_io import configure_console_io
from packages.common.runtime_paths import first_existing_runtime_path, runtime_subpath

configure_console_io()

BASE_URL = "http://127.0.0.1:8012"


def check_via_api(model_id: str) -> int:
    url = f"{BASE_URL}/api/benchmark/options"
    try:
        with urllib.request.urlopen(url, timeout=5) as resp:
            data = json.loads(resp.read())
            models = [m["id"] for m in data.get("models", [])]
            if model_id in models:
                print(f"OK    Model '{model_id}' je registrován v backendu")
                return 0
            else:
                print(f"FAIL  Model '{model_id}' není v backendu")
                print(f"      Dostupné modely: {', '.join(models)}")
                return 1
    except urllib.error.URLError:
        print("FAIL  Backend neodpovídá — spusť check_health.py")
        return 2


def check_model_files(model_id: str) -> None:
    """Informativní kontrola souborů modelu v runtime/model_store (fallback i na legacy .runtime)."""
    model_dir = first_existing_runtime_path("model_store", model_id)
    if model_dir.exists():
        files = list(model_dir.rglob("*"))
        size_mb = sum(f.stat().st_size for f in files if f.is_file()) / 1024 / 1024
        print(f"INFO  Model store: {model_dir} ({size_mb:.0f} MB, {len(files)} souborů)")
    else:
        canonical = runtime_subpath("model_store", model_id)
        print(f"INFO  Model store: {canonical} — nenalezen (model nemusí být stažen)")


def main() -> int:
    if len(sys.argv) < 2:
        print("Použití: python scripts/check_model.py <model_id>")
        print("Příklad: python scripts/check_model.py whisper_cpp_small")
        return 1

    model_id = sys.argv[1]
    print(f"=== Test modelu: {model_id} ===\n")
    check_model_files(model_id)
    return check_via_api(model_id)


if __name__ == "__main__":
    sys.exit(main())
