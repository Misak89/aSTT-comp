#!/usr/bin/env python
"""
Benchmark worker — spouští se jako samostatný subprocess.

Architektura subprocess izolace:
  Backend spawní tento skript → crash modelu nezabije backend
  Parent monitoruje přes psutil (CPU/RAM jen tohoto procesu)
  Komunikace přes soubory: {jobs_root}/{job_id}/progress.json + result.json

Použití (interní, volá benchmark_service.py):
  python scripts/benchmark_worker.py --job-id <id> --jobs-root <path>
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

# Přidej root projektu do path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def _write_progress(progress_file: Path, message: str, percent: int = 0) -> None:
    try:
        progress_file.write_text(
            json.dumps({"message": message, "percent": percent, "updated_at": _now_utc()},
                       ensure_ascii=False),
            encoding="utf-8",
        )
    except Exception:
        pass


def _write_result(result_file: Path, payload: dict) -> None:
    result_file.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="aSTT-comp benchmark worker subprocess")
    parser.add_argument("--job-id", required=True)
    parser.add_argument("--jobs-root", required=True)
    args = parser.parse_args()

    job_dir = Path(args.jobs_root) / args.job_id
    config_file = job_dir / "config.json"
    progress_file = job_dir / "progress.json"
    result_file = job_dir / "worker_result.json"

    if not config_file.exists():
        print(f"ERROR: config not found: {config_file}", file=sys.stderr)
        return 2

    config = json.loads(config_file.read_text(encoding="utf-8"))
    runs_root = Path(config["runs_root"])
    subtitles_root = Path(config["subtitles_root"])

    _write_progress(progress_file, "Worker spuštěn, inicializace...", 5)

    try:
        from packages.benchmarks.runners.matrix_benchmark_runner import run_benchmark_matrix

        call_count = [0]

        def progress_cb(msg: str) -> None:
            call_count[0] += 1
            pct = min(10 + call_count[0] * 3, 90)
            _write_progress(progress_file, msg, pct)

        _write_progress(progress_file, "Načítám runner...", 10)

        matrix_payload = run_benchmark_matrix(
            sources=config["sources"],
            model_ids=config["model_ids"],
            setting_ids=config["setting_ids"],
            sample_seconds=config.get("sample_seconds", 120),
            evaluation_mode=config.get("evaluation_mode", "real"),
            clip_strategy=config.get("clip_strategy", "random"),
            clip_seed=config.get("clip_seed"),
            runs_root=runs_root,
            subtitles_root=subtitles_root,
            progress_callback=progress_cb,
        )

        _write_progress(progress_file, "Hotovo", 100)
        _write_result(result_file, {"status": "completed", "payload": matrix_payload})
        return 0

    except Exception as exc:
        tb = traceback.format_exc()
        _write_progress(progress_file, f"Chyba: {exc}", 0)
        _write_result(result_file, {
            "status": "failed",
            "error": f"{type(exc).__name__}: {exc}",
            "traceback": tb[-1000:],
        })
        print(tb, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
