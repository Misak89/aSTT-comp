#!/usr/bin/env python
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.app.config import MODEL_STORE_ROOT, SUBTITLES_ROOT
from backend.app.services import library_service
from packages.adapters.faster_whisper_runner import (
    FasterWhisperRunConfig,
    resolve_faster_whisper_model_path,
    run_faster_whisper_source,
)
from packages.adapters.sherpa_onnx_runner import (
    SherpaRunConfig,
    resolve_sherpa_model_bundle,
    run_sherpa_source,
)
from packages.adapters.vosk_runner import VoskRunConfig, resolve_vosk_model_dir, run_vosk_source
from packages.adapters.whisper_cpp_runner import (
    WhisperRunConfig,
    resolve_whisper_cli,
    resolve_whisper_model_file,
    run_whisper_source,
)
from packages.benchmarks.ground_truth.vtt_reference import extract_vtt_clip_text
from packages.benchmarks.metrics.text_metrics import char_error_rate, word_error_rate, word_error_rate_soft
from packages.common.console_io import configure_console_io
from packages.ingest.source_resolver import SourceEntry

configure_console_io()


def _now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def _to_float(value: Any) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _evaluate_row(row: dict[str, Any], reference_text: str | None) -> dict[str, Any]:
    transcript = str(row.get("transcript_text") or "").strip()
    out: dict[str, Any] = {
        "engine": row.get("engine"),
        "rtf": row.get("rtf"),
        "latency_ms": row.get("latency_ms"),
        "first_word_latency_ms": row.get("first_word_latency_ms"),
        "first_word_wall_ms": row.get("first_word_wall_ms"),
        "first_word_audio_ms": row.get("first_word_audio_ms"),
        "ram_mb": row.get("ram_mb"),
        "cpu_percent": row.get("cpu_percent"),
        "transcript_words": len(transcript.split()),
        "error": row.get("error"),
    }

    if reference_text and transcript:
        out["wer"] = round(word_error_rate(reference_text, transcript), 4)
        out["wer_soft"] = round(word_error_rate_soft(reference_text, transcript), 4)
        out["cer"] = round(char_error_rate(reference_text, transcript), 4)
    else:
        out["wer"] = None
        out["wer_soft"] = None
        out["cer"] = None

    rtf = _to_float(out["rtf"])
    latency = _to_float(out["first_word_wall_ms"]) or _to_float(out["latency_ms"])
    wer_soft = _to_float(out["wer_soft"])
    out["pass_gate"] = bool(
        (rtf is not None and rtf <= 1.0)
        and (latency is not None and latency <= 15000.0)
        and (wer_soft is not None and wer_soft <= 0.30)
    )
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description="Smoke compare of CZ streaming-capable STT models.")
    parser.add_argument("--video-id", default="ls5MvHhjYGg")
    parser.add_argument("--start-s", type=int, default=60)
    parser.add_argument("--sample-s", type=int, default=30)
    parser.add_argument("--threads", type=int, default=4)
    parser.add_argument("--include-sherpa", action="store_true", help="Include sherpa parakeet run (can fail on incompatible runtime/model).")
    parser.add_argument("--include-vosk", action="store_true", help="Include VOSK fallback baseline.")
    parser.add_argument("--out-dir", default=str(ROOT / "runtime" / "mic_smoke"))
    args = parser.parse_args()

    video_id = str(args.video_id).strip()
    sample_s = max(10, int(args.sample_s))
    start_s = max(0, int(args.start_s))
    threads = max(1, int(args.threads))

    audio_path = library_service.resolve_audio_cache_file_for_library_item(video_id, extensions=(".wav",))
    if audio_path is None or not audio_path.exists():
        raise FileNotFoundError(f"Missing audio cache file for video_id={video_id}")

    source = SourceEntry(
        source_id=f"smoke-{video_id}",
        label=f"smoke {video_id}",
        origin_type="local_file",
        value=str(audio_path),
        exists=True,
        canonical_url=f"https://www.youtube.com/watch?v={video_id}",
        video_id=video_id,
    )

    out_root = Path(args.out_dir)
    out_root.mkdir(parents=True, exist_ok=True)
    run_dir = out_root / datetime.now(timezone.utc).strftime("smoke_%Y%m%d_%H%M%S")
    run_dir.mkdir(parents=True, exist_ok=True)

    reference_text = extract_vtt_clip_text(
        video_id=video_id,
        clip_start_s=float(start_s),
        clip_end_s=float(start_s + sample_s),
        subtitles_root=SUBTITLES_ROOT,
    )

    results: dict[str, dict[str, Any]] = {}

    whisper_bin = resolve_whisper_cli(MODEL_STORE_ROOT)
    whisper_model = resolve_whisper_model_file(MODEL_STORE_ROOT, "whisper_cpp_small")
    if whisper_bin and whisper_model:
        cfg = WhisperRunConfig(
            whisper_bin=str(whisper_bin),
            model_path=str(whisper_model),
            language="cs",
            threads=threads,
            beam_size=5,
            best_of=5,
            no_fallback=False,
            use_server_cache=True,
        )
        row = run_whisper_source(
            source=source,
            sample_seconds=sample_s,
            start_offset_seconds=start_s,
            output_dir=run_dir / "whisper_cpp_small",
            config=cfg,
        )
        results["whisper_cpp_small"] = _evaluate_row(row, reference_text)
    else:
        results["whisper_cpp_small"] = {
            "error": "whisper runtime or model missing",
            "pass_gate": False,
        }

    if args.include_sherpa:
        sherpa_bundle = resolve_sherpa_model_bundle(MODEL_STORE_ROOT, preferred_language="cs")
        if sherpa_bundle:
            cfg = SherpaRunConfig(
                tokens=sherpa_bundle.tokens,
                encoder=sherpa_bundle.encoder,
                decoder=sherpa_bundle.decoder,
                joiner=sherpa_bundle.joiner,
                provider="cpu",
                num_threads=threads,
                sample_rate=16000,
                decoding_method="greedy_search",
            )
            row = run_sherpa_source(
                source=source,
                sample_seconds=sample_s,
                start_offset_seconds=start_s,
                output_dir=run_dir / "sherpa_parakeet_cs_int8",
                config=cfg,
            )
            results["sherpa_onnx_parakeet_cs_int8"] = _evaluate_row(row, reference_text)
        else:
            results["sherpa_onnx_parakeet_cs_int8"] = {
                "error": "sherpa CZ bundle missing",
                "pass_gate": False,
            }

    faster_model_path = resolve_faster_whisper_model_path(MODEL_STORE_ROOT, "faster_whisper_small_cs_int8")
    if faster_model_path:
        cfg = FasterWhisperRunConfig(
            model_path=str(faster_model_path),
            language="cs",
            threads=threads,
            beam_size=3,
            best_of=3,
            device="cpu",
            compute_type="int8",
        )
        row = run_faster_whisper_source(
            source=source,
            sample_seconds=sample_s,
            start_offset_seconds=start_s,
            output_dir=run_dir / "faster_whisper_small_cs_int8",
            config=cfg,
        )
        results["faster_whisper_small_cs_int8"] = _evaluate_row(row, reference_text)
    else:
        results["faster_whisper_small_cs_int8"] = {
            "error": "faster-whisper model missing",
            "pass_gate": False,
        }

    faster_medium_model_path = resolve_faster_whisper_model_path(MODEL_STORE_ROOT, "faster_whisper_medium_cs_int8")
    if faster_medium_model_path:
        cfg = FasterWhisperRunConfig(
            model_path=str(faster_medium_model_path),
            language="cs",
            threads=max(4, threads),
            beam_size=3,
            best_of=3,
            device="cpu",
            compute_type="int8",
        )
        row = run_faster_whisper_source(
            source=source,
            sample_seconds=sample_s,
            start_offset_seconds=start_s,
            output_dir=run_dir / "faster_whisper_medium_cs_int8",
            config=cfg,
        )
        results["faster_whisper_medium_cs_int8"] = _evaluate_row(row, reference_text)
    else:
        results["faster_whisper_medium_cs_int8"] = {
            "error": "faster-whisper medium model missing",
            "pass_gate": False,
        }

    if args.include_vosk:
        vosk_model_dir = resolve_vosk_model_dir(MODEL_STORE_ROOT)
        if vosk_model_dir:
            cfg = VoskRunConfig(
                model_dir=str(vosk_model_dir),
                sample_rate=16000,
                chunk_seconds=0.12,
                set_words=False,
            )
            row = run_vosk_source(
                source=source,
                sample_seconds=sample_s,
                start_offset_seconds=start_s,
                output_dir=run_dir / "vosk_small_cs_0_4",
                config=cfg,
            )
            results["vosk_small_cs_0_4"] = _evaluate_row(row, reference_text)
        else:
            results["vosk_small_cs_0_4"] = {
                "error": "vosk model missing",
                "pass_gate": False,
            }

    payload = {
        "generated_at_utc": _now_utc(),
        "video_id": video_id,
        "audio_path": str(audio_path),
        "start_s": start_s,
        "sample_s": sample_s,
        "threads": threads,
        "reference_present": bool(reference_text),
        "reference_words": len(reference_text.split()) if reference_text else 0,
        "pass_gate_definition": {
            "rtf_max": 1.0,
            "latency_ms_max": 15000,
            "wer_soft_max": 0.30,
        },
        "results": results,
    }
    report_path = run_dir / "smoke_report.json"
    report_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"Report: {report_path}")
    for model_id, row in results.items():
        print(
            f"{model_id}: pass={row.get('pass_gate')} "
            f"| wer_soft={row.get('wer_soft')} | rtf={row.get('rtf')} "
            f"| first_wall_ms={row.get('first_word_wall_ms')} | error={row.get('error')}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
