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

from packages.common.console_io import configure_console_io
from packages.common.runtime_paths import runtime_subpath

configure_console_io()


def _now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def _write_progress(progress_file: Path, message: str, percent: int = 0, transcript: str = "", transcript_ts: str = "") -> None:
    try:
        data: dict = {"message": message, "percent": percent, "updated_at": _now_utc()}
        if transcript:
            data["transcript"] = transcript
        if transcript_ts:
            data["transcript_ts"] = transcript_ts
        progress_file.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
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
    model_store_root = Path(config.get("model_store_root", str(runtime_subpath("model_store"))))

    # Mapování clip_strategy (frontend) → clip_selection_strategy (runner)
    _STRATEGY_MAP = {"random": "deterministic_v1", "uniform": "start_zero"}
    clip_strategy_raw = config.get("clip_strategy", "random")
    clip_selection_strategy = _STRATEGY_MAP.get(clip_strategy_raw, "deterministic_v1")

    _write_progress(progress_file, "Worker spuštěn, inicializace...", 5)

    try:
        from packages.benchmarks.runners.matrix_benchmark_runner import run_benchmark_matrix
        from packages.ingest.source_resolver import parse_source_entries

        # Konverze URL strings → SourceEntry objekty
        source_entries = parse_source_entries(
            config["sources"],
            max_sources=len(config["sources"]),
        )
        if not source_entries:
            raise ValueError(f"Žádné validní zdroje z: {config['sources']}")
        missing_files = [
            entry for entry in source_entries
            if entry.origin_type == "local_file" and not entry.exists
        ]
        if missing_files:
            missing = ", ".join(entry.value for entry in missing_files[:3])
            raise FileNotFoundError(f"Lokální audio soubor neexistuje: {missing}")

        current_pct = [10]
        live_transcript = [""]
        live_transcript_ts = [""]

        def progress_cb(msg: str, pct: int | None = None) -> None:
            p = pct if pct is not None else current_pct[0]
            _write_progress(progress_file, msg, p, transcript=live_transcript[0], transcript_ts=live_transcript_ts[0])

        def transcript_cb(text: str, pct: int, text_ts: str = "") -> None:
            live_transcript[0] = text
            live_transcript_ts[0] = text_ts
            _write_progress(progress_file, f"Přepisuji... ({pct}%)", pct, transcript=text, transcript_ts=text_ts)

        _write_progress(progress_file, "Načítám runner...", 10)

        evaluation_mode = config.get("evaluation_mode", "synthetic")

        if evaluation_mode == "streaming":
            matrix_payload = _run_streaming_matrix(
                config=config,
                source_entries=source_entries,
                runs_root=runs_root,
                subtitles_root=subtitles_root,
                model_store_root=model_store_root,
                progress_cb=progress_cb,
                transcript_cb=transcript_cb,
            )
        else:
            matrix_payload = run_benchmark_matrix(
                sources=source_entries,
                model_ids=config["model_ids"],
                setting_ids=config["setting_ids"],
                sample_seconds=config.get("sample_seconds", 120),
                evaluation_mode=evaluation_mode,
                clip_selection_strategy=clip_selection_strategy,
                clip_selection_seed=config.get("clip_seed"),
                segment_start_seconds=config.get("segment_start_seconds"),
                run_root=str(runs_root),
                model_store_root=str(model_store_root),
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


def _build_matrix(run_id, sample_seconds, source_entries, by_model_setting) -> dict:
    """Sestaví benchmark_matrix dict ze shromážděných výsledků."""
    import json as _json
    from datetime import datetime, timezone
    matrix = {
        "run_id": run_id,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "evaluation_mode": "streaming",
        "sample_seconds": sample_seconds,
        "source_count": len(source_entries),
        "sources": [
            {"source_id": s.source_id, "canonical_url": getattr(s, "canonical_url", None) or getattr(s, "value", None)}
            for s in source_entries
        ],
        "results": [],
    }
    for (model_id, setting_id, setting_label), model_results in by_model_setting.items():
        rtf_vals = [r["rtf"] for r in model_results if r.get("rtf") is not None]
        latency_vals = [r.get("latency_ms") for r in model_results if r.get("latency_ms") is not None]
        wer_vals = [r["wer"] for r in model_results if r.get("wer") is not None]
        cer_vals = [r["cer"] for r in model_results if r.get("cer") is not None]
        source_metrics = [{
            "video_id": r.get("video_id") or r.get("source_id"),
            "canonical_url": r.get("canonical_url"),
            "clip_start_seconds": 0,
            "clip_seconds": sample_seconds,
            "transcript": r.get("transcript"),
            "reference_text": r.get("reference_text"),
            "wer": r.get("wer"),
            "cer": r.get("cer"),
            "wer_normalized": r.get("wer_normalized"),
            "mer": r.get("mer"),
            "wil": r.get("wil"),
            "segment_metrics": r.get("segment_metrics"),
            "latency_ms": r.get("latency_ms"),
            "rtf": r.get("rtf"),
            "engine_elapsed_seconds": r.get("engine_elapsed_seconds") or r.get("elapsed_s"),
            "chunk_metrics": r.get("chunk_metrics"),
            "error": r.get("error"),
        } for r in model_results]
        matrix["results"].append({
            "model_id": model_id,
            "model_label": model_id,
            "setting_id": setting_id,
            "setting_label": setting_label,
            "aggregate": {
                "rtf": round(sum(rtf_vals) / len(rtf_vals), 4) if rtf_vals else None,
                "latency_ms": round(sum(latency_vals) / len(latency_vals), 1) if latency_vals else None,
                "wer": round(sum(wer_vals) / len(wer_vals), 4) if wer_vals else None,
                "cer": round(sum(cer_vals) / len(cer_vals), 4) if cer_vals else None,
                "cpu_percent": None, "ram_mb": None,
            },
            "source_metrics": source_metrics,
        })
    return matrix


def _write_partial_matrix(run_dir, run_id, sample_seconds, source_entries, by_model_setting):
    """Zapíše průběžný benchmark_matrix.json po každém dokončeném runu."""
    import json as _json
    matrix = _build_matrix(run_id, sample_seconds, source_entries, by_model_setting)
    (run_dir / "benchmark_matrix.json").write_text(
        _json.dumps(matrix, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _audio_cache_video_ids(repo_root: Path) -> list[str]:
    try:
        items_path = repo_root / "runtime" / "library" / "items.json"
        items = json.loads(items_path.read_text(encoding="utf-8"))
        return [str(item.get("video_id") or "").strip() for item in items if str(item.get("video_id") or "").strip()]
    except Exception:
        return []


def _infer_audio_cache_video_id(path: Path, repo_root: Path) -> str | None:
    if path.suffix.lower() != ".wav" or path.parent.name != "audio_cache":
        return None
    stem = path.stem.strip()
    for video_id in _audio_cache_video_ids(repo_root):
        if stem == video_id or stem.endswith(f"_{video_id}"):
            return video_id
    return stem or None


def _resolve_audio_cache_wav(audio_cache_root: Path, video_id: str | None) -> Path | None:
    clean_video_id = str(video_id or "").strip()
    if not clean_video_id:
        return None
    legacy = audio_cache_root / f"{clean_video_id}.wav"
    if legacy.exists():
        return legacy
    suffix = f"_{clean_video_id}.wav"
    try:
        for path in sorted(audio_cache_root.iterdir(), key=lambda p: p.name.lower()):
            if path.is_file() and path.name.endswith(suffix):
                return path
    except FileNotFoundError:
        return None
    return None


def _run_streaming_matrix(*, config, source_entries, runs_root, subtitles_root, model_store_root, progress_cb, transcript_cb=None):
    """Spustí benchmark v streaming módu: iteruje source × model × setting."""
    from datetime import datetime, timezone
    from collections import defaultdict
    from packages.benchmarks.runners.streaming_runner import StreamingRunConfig, run_streaming_benchmark
    from packages.ingest.youtube.stream_pipe import stream_audio_chunks, stream_youtube_audio
    from packages.benchmarks.ground_truth.vtt_reference import extract_vtt_clip_text
    from packages.benchmarks.metrics.text_metrics import word_error_rate, char_error_rate

    model_ids = config["model_ids"]
    sample_seconds = config.get("sample_seconds", 120)
    segment_start_seconds = int(max(0, int(config.get("segment_start_seconds") or 0)))
    model_params_cfg = config.get("model_params") or {}

    # Zjisti cestu k audio_cache — použije se pro fallback na lokální WAV
    from pathlib import Path as _Path
    _repo_root = _Path(__file__).parent.parent
    _audio_cache_root = _repo_root / "runtime" / "audio_cache"

    # Settings s parametry — každé má vlastní chunk_seconds, threads, beam_size atd.
    settings = config.get("settings") or [{"id": "balanced", "label": "Balanced (30s)", "chunk_seconds": 30, "threads": 4}]

    run_id = f"run_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"
    run_dir = runs_root / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    # Výsledky indexované (model_id, setting_id) → list výsledků per source
    by_model_setting: dict = defaultdict(list)

    total_runs = len(source_entries) * len(model_ids) * len(settings)
    run_num = 0

    for source in source_entries:
        # Benchmark service může pro library video předat přímo cached WAV cestu
        # (runtime/audio_cache/<video_id>.wav). V takovém případě parse_source_entries
        # nastaví origin_type=local_file a video_id ztratíme; zkusíme ho obnovit
        # z názvu souboru, aby šlo načíst VTT reference_text a počítat diff/WER.
        source_video_id = source.video_id
        if not source_video_id and source.origin_type == "local_file" and source.value:
            try:
                src_path = Path(str(source.value))
                if src_path.suffix.lower() == ".wav" and src_path.parent.name == "audio_cache":
                    inferred = _infer_audio_cache_video_id(src_path, _repo_root)
                    if inferred:
                        source_video_id = inferred
            except Exception:
                source_video_id = source.video_id

        # VTT reference načti jednou per source
        ref_text = (
            extract_vtt_clip_text(source_video_id, segment_start_seconds, sample_seconds, subtitles_root)
            if source_video_id
            else None
        )

        for model_id in model_ids:
            # Per-model params
            if model_id in model_params_cfg:
                base_params = {**{k: v for k, v in model_params_cfg.items() if not isinstance(v, dict)},
                               **model_params_cfg[model_id]}
            else:
                base_params = {k: v for k, v in model_params_cfg.items() if not isinstance(v, dict)}

            for setting in settings:
                setting_id = setting["id"]
                setting_label = setting.get("label", setting_id)
                chunk_seconds = int(setting.get("chunk_seconds", 30))

                # Merge: explicit model params mají prioritu před preset settingem.
                params = {**base_params}
                for k in ("threads", "beam_size", "best_of", "no_fallback", "language"):
                    if k in setting and k not in params:
                        params[k] = setting[k]

                run_num += 1
                pct_start = int(10 + (run_num - 1) / total_runs * 80)
                pct_end   = int(10 + run_num / total_runs * 80)
                progress_cb(f"[{run_num}/{total_runs}] {model_id} × {setting_label} | video: {source.video_id or source.source_id}", pct_start)

                out_dir = run_dir / "streaming_artifacts" / model_id / setting_id / source.source_id
                out_dir.mkdir(parents=True, exist_ok=True)

                # Progress callback s fixním procentem pro tento run
                def _run_progress_cb(msg: str, _pct: int | None = None, _p=pct_start):
                    progress_cb(msg, _pct if _pct is not None else _p)

                # Lokální WAV: buď zdroj je přímo .wav soubor,
                # nebo máme cached audio pro toto video_id.
                # Pokud je požadovaný start offset > 0, jedeme přes stream_audio_chunks(start_offset),
                # aby se opravdu přepisoval jen daný úsek.
                _cached_wav = _resolve_audio_cache_wav(_audio_cache_root, source_video_id)
                local_media_path: str | None = None
                effective_source = source
                if source.origin_type == "local_file" and source.value:
                    local_media_path = source.value
                elif _cached_wav is not None and _cached_wav.exists():
                    local_media_path = str(_cached_wav)
                    from packages.ingest.source_resolver import SourceEntry as _SE
                    effective_source = _SE(
                        source_id=source.source_id,
                        label=source.label,
                        origin_type="local_file",
                        value=str(_cached_wav),
                        exists=True,
                        canonical_url=source.canonical_url,
                        video_id=source_video_id,
                    )

                # Direct WAV bypass je validní jen pro buffered adaptery
                # (whisper_cpp, qwen). Live adaptery (vosk/sherpa/faster_whisper)
                # potřebují audio_generator.
                _is_buffered_model = model_id.startswith("whisper_cpp") or model_id.startswith("qwen")
                use_direct_wav = (
                    _is_buffered_model
                    and
                    local_media_path is not None
                    and str(local_media_path).lower().endswith(".wav")
                    and segment_start_seconds <= 0
                )
                cfg = StreamingRunConfig(
                    model_id=model_id,
                    model_params=params,
                    model_store_root=str(model_store_root),
                    output_dir=str(out_dir),
                    sample_seconds=sample_seconds,
                    chunk_seconds=chunk_seconds,
                    progress_callback=_run_progress_cb,
                    transcript_callback=transcript_cb,
                    source_wav_path=local_media_path if use_direct_wav else None,
                )

                try:
                    if local_media_path and not use_direct_wav:
                        audio_gen = stream_audio_chunks(
                            local_media_path,
                            chunk_seconds=0.1,
                            max_seconds=float(sample_seconds),
                            start_offset_seconds=float(segment_start_seconds),
                        )
                    elif local_media_path:
                        audio_gen = None
                    else:
                        audio_gen = stream_youtube_audio(
                            source.canonical_url or source.value,
                            chunk_seconds=0.1,
                            max_seconds=float(sample_seconds),
                            start_offset_seconds=float(segment_start_seconds),
                        )
                    result = run_streaming_benchmark(
                        source=effective_source,
                        audio_generator=audio_gen,
                        config=cfg,
                    )
                    result["model_id"] = model_id
                    result["setting_id"] = setting_id
                    result["setting_label"] = setting_label
                    result["video_id"] = source_video_id or source.source_id

                    if "transcript" not in result and "transcript_text" in result:
                        result["transcript"] = result["transcript_text"]

                    if result.get("transcript") and ref_text:
                        result["reference_text"] = ref_text
                        result["wer"] = round(word_error_rate(ref_text, result["transcript"]), 4)
                        result["cer"] = round(char_error_rate(ref_text, result["transcript"]), 4)
                        from packages.benchmarks.metrics.text_metrics import (
                            match_error_rate, word_information_lost, word_error_rate_normalized,
                            segment_level_wer,
                        )
                        result["wer_normalized"] = round(word_error_rate_normalized(ref_text, result["transcript"]), 4)
                        result["mer"] = round(match_error_rate(ref_text, result["transcript"]), 4)
                        result["wil"] = round(word_information_lost(ref_text, result["transcript"]), 4)
                        # Segment-level WER pokud máme Whisper segmenty
                        _segs = result.get("_segments", [])
                        if _segs and source_video_id:
                            result["segment_metrics"] = segment_level_wer(
                                _segs, source_video_id, 0, subtitles_root
                            )

                except Exception as exc:
                    result = {
                        "model_id": model_id,
                        "setting_id": setting_id,
                        "setting_label": setting_label,
                        "video_id": source_video_id or source.source_id,
                        "error": str(exc),
                    }

                by_model_setting[(model_id, setting_id, setting_label)].append(result)
                progress_cb(f"✓ [{run_num}/{total_runs}] hotovo: {model_id} × {setting_label}", pct_end)

                # Průběžný zápis — aby partial výsledky nebyly ztraceny při crashu
                _write_partial_matrix(run_dir, run_id, sample_seconds, source_entries, by_model_setting)

    # Finální zápis benchmark_matrix.json
    _write_partial_matrix(run_dir, run_id, sample_seconds, source_entries, by_model_setting)

    return {
        "run_id": run_id,
        "evaluation_mode": "streaming",
        "sample_seconds": sample_seconds,
    }


if __name__ == "__main__":
    sys.exit(main())
