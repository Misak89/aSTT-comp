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


def _set_progress(status_file: Path, message: str) -> None:
    _update_status(status_file, {"progress_message": message})
    try:
        print(message, flush=True)
    except Exception:
        pass


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
        from packages.benchmarks.runners.streaming_runner import run_streaming_benchmark, StreamingRunConfig
        from packages.ingest.source_resolver import SourceEntry
        from packages.ingest.youtube.stream_pipe import stream_youtube_audio
        from packages.benchmarks.metrics.text_metrics import (
            word_error_rate, char_error_rate,
            word_error_rate_normalized, match_error_rate, word_information_lost,
            word_diff,
        )
        from packages.benchmarks.ground_truth.vtt_reference import extract_vtt_clip_text

        # Vyber první video (tuning typicky na jednom videu)
        if not video_ids:
            raise ValueError("Žádná video_ids v konfiguraci")
        video_id = video_ids[0]
        yt_url = f"https://www.youtube.com/watch?v={video_id}"
        source = SourceEntry(
            source_id=f"src-{video_id}",
            label=video_id,
            origin_type="youtube",
            value=yt_url,
            exists=True,
            canonical_url=yt_url,
            video_id=video_id,
        )

        for trial_idx, trial_params in enumerate(trials):
            chunk_seconds = int(trial_params.pop("_chunk_seconds", 30))
            # trial_params teď obsahuje jen model params

            _set_progress(status_file, f"Trial {trial_idx+1}/{len(trials)}: chunk={chunk_seconds}s | {', '.join(f'{k}={v}' for k,v in trial_params.items())}")

            try:
                _set_progress(status_file, f"Trial {trial_idx+1}/{len(trials)}: ⬇ stahuji audio z YouTube...")
                run_config = StreamingRunConfig(
                    model_id=model_id,
                    model_params=trial_params,
                    model_store_root=str(model_store_root),
                    output_dir=str(job_dir / f"trial_{trial_idx:03d}"),
                    sample_seconds=sample_seconds,
                    chunk_seconds=chunk_seconds,
                    progress_callback=lambda msg: _set_progress(status_file, f"Trial {trial_idx+1}/{len(trials)}: {msg}"),
                )
                result = run_streaming_benchmark(
                    source=source,
                    audio_generator=stream_youtube_audio(
                        source.canonical_url or source.value,
                        chunk_seconds=0.1,
                        max_seconds=float(sample_seconds),
                    ),
                    config=run_config,
                )

                # Extrahuj referenční text
                _set_progress(status_file, f"Trial {trial_idx+1}/{len(trials)}: ✓ přepis hotov, počítám WER...")
                ref_text = extract_vtt_clip_text(
                    video_id=video_id,
                    clip_start_s=0,
                    clip_end_s=sample_seconds,
                    subtitles_root=subtitles_root,
                )

                transcript = result.get("transcript") or result.get("transcript_text") or result.get("text") or ""
                wer = None
                cer = None
                wer_norm = None
                mer = None
                wil = None

                if ref_text and transcript:
                    wer = word_error_rate(ref_text, transcript)
                    cer = char_error_rate(ref_text, transcript)
                    wer_norm = word_error_rate_normalized(ref_text, transcript)
                    mer = match_error_rate(ref_text, transcript)
                    wil = word_information_lost(ref_text, transcript)

                wdiff = None
                if ref_text and transcript:
                    try:
                        wdiff = word_diff(ref_text, transcript)
                    except Exception:
                        pass

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
                    "elapsed_s": result.get("elapsed_s"),
                    "total_audio_s": result.get("total_audio_s"),
                    "word_count": len(transcript.split()) if transcript else 0,
                    "error": None,
                    "is_pareto": False,
                    "transcript": transcript[:3000] if transcript else None,
                    "reference_text": ref_text[:3000] if ref_text else None,
                    "word_diff": wdiff,
                    "chunk_metrics": result.get("chunk_metrics"),
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
