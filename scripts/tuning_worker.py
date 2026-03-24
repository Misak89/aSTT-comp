#!/usr/bin/env python
"""
Tuning worker — spouští se jako subprocess.
Pro každý trial spustí streaming benchmark a zapíše výsledky.

Použití (interní):
  python scripts/tuning_worker.py --job-id <id> --tuning-root <path>
"""
from __future__ import annotations

import argparse
import json
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _update_status(status_file: Path, updates: dict) -> None:
    try:
        data = json.loads(status_file.read_text(encoding="utf-8"))
        data.update(updates)
        status_file.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        print(f"WARN: status update failed: {e}", file=sys.stderr)


def _append_result(status_file: Path, result: dict) -> None:
    try:
        data = json.loads(status_file.read_text(encoding="utf-8"))
        data.setdefault("results", []).append(result)
        data["completed_trials"] = len(data["results"])
        status_file.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        print(f"WARN: result append failed: {e}", file=sys.stderr)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--job-id", required=True)
    parser.add_argument("--tuning-root", required=True)
    args = parser.parse_args()

    job_dir = Path(args.tuning_root) / args.job_id
    config_file = job_dir / "config.json"
    status_file = job_dir / "status.json"

    if not config_file.exists():
        print(f"ERROR: config not found: {config_file}", file=sys.stderr)
        return 2

    config = json.loads(config_file.read_text(encoding="utf-8"))
    trials: list[dict] = config["trials"]
    model_id: str = config["model_id"]
    video_ids: list[str] = config["video_ids"]
    sample_seconds: int = config["sample_seconds"]
    clip_seed = config.get("clip_seed")
    subtitles_root = Path(config["subtitles_root"])
    model_store_root = Path(config["model_store_root"])

    _update_status(status_file, {"status": "running"})

    try:
        from packages.benchmarks.runners.streaming_runner import StreamingRunner, StreamingRunConfig
        from packages.ingest.source_resolver import parse_source_entries
        from packages.benchmarks.metrics.text_metrics import (
            word_error_rate, character_error_rate,
            word_error_rate_normalized, match_error_rate, word_information_lost,
        )
        from packages.benchmarks.ground_truth.vtt_reference import extract_vtt_clip_text

        # Vyber první video (tuning typicky na jednom videu)
        sources_raw = [f"yt:{vid}" for vid in video_ids]
        source_entries = parse_source_entries(sources_raw, max_sources=len(sources_raw))
        if not source_entries:
            raise ValueError(f"Žádné validní zdroje: {video_ids}")
        source = source_entries[0]
        video_id = video_ids[0]

        for trial_idx, trial_params in enumerate(trials):
            chunk_seconds = int(trial_params.pop("_chunk_seconds", 30))
            # trial_params teď obsahuje jen model params

            print(f"Trial {trial_idx+1}/{len(trials)}: chunk={chunk_seconds}s params={trial_params}", flush=True)

            try:
                run_config = StreamingRunConfig(
                    model_id=model_id,
                    model_params=trial_params,
                    model_store_root=str(model_store_root),
                    output_dir=str(job_dir / f"trial_{trial_idx:03d}"),
                    sample_seconds=sample_seconds,
                    chunk_seconds=chunk_seconds,
                )
                runner = StreamingRunner(run_config)
                result = runner.run(source)

                # Extrahuj referenční text
                ref_text = extract_vtt_clip_text(
                    video_id=video_id,
                    clip_start_s=0,
                    clip_end_s=sample_seconds,
                    subtitles_root=subtitles_root,
                )

                transcript = result.get("transcript") or result.get("text") or ""
                wer = None
                cer = None
                wer_norm = None
                mer = None
                wil = None

                if ref_text and transcript:
                    wer = word_error_rate(ref_text, transcript)
                    cer = character_error_rate(ref_text, transcript)
                    wer_norm = word_error_rate_normalized(ref_text, transcript)
                    mer = match_error_rate(ref_text, transcript)
                    wil = word_information_lost(ref_text, transcript)

                trial_result = {
                    "trial_idx": trial_idx,
                    "params": {**trial_params, "chunk_seconds": chunk_seconds},
                    "chunk_seconds": chunk_seconds,
                    "wer": round(wer, 4) if wer is not None else None,
                    "cer": round(cer, 4) if cer is not None else None,
                    "wer_normalized": round(wer_norm, 4) if wer_norm is not None else None,
                    "mer": round(mer, 4) if mer is not None else None,
                    "wil": round(wil, 4) if wil is not None else None,
                    "rtf": result.get("rtf"),
                    "latency_ms": result.get("latency_ms"),
                    "error": None,
                    "is_pareto": False,
                }

            except Exception as e:
                trial_result = {
                    "trial_idx": trial_idx,
                    "params": {**trial_params, "chunk_seconds": chunk_seconds},
                    "chunk_seconds": chunk_seconds,
                    "wer": None, "cer": None, "wer_normalized": None,
                    "mer": None, "wil": None,
                    "rtf": None, "latency_ms": None,
                    "error": str(e),
                    "is_pareto": False,
                }
                print(f"Trial {trial_idx} FAILED: {e}", file=sys.stderr)
                traceback.print_exc(file=sys.stderr)

            _append_result(status_file, trial_result)

        _update_status(status_file, {"status": "completed"})
        return 0

    except Exception as e:
        traceback.print_exc(file=sys.stderr)
        _update_status(status_file, {"status": "failed", "error": str(e)})
        return 1


if __name__ == "__main__":
    sys.exit(main())
