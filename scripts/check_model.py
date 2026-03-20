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
    """Informativní kontrola souborů modelu v .runtime/model_store."""
    runtime = Path(__file__).parent.parent / ".runtime" / "model_store" / model_id
    if runtime.exists():
        files = list(runtime.rglob("*"))
        size_mb = sum(f.stat().st_size for f in files if f.is_file()) / 1024 / 1024
        print(f"INFO  Model store: {runtime} ({size_mb:.0f} MB, {len(files)} souborů)")
    else:
        print(f"INFO  Model store: {runtime} — nenalezen (model nemusí být stažen)")


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
