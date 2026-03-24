#!/usr/bin/env python
"""
Tuning worker — spouští se jako subprocess.
Pro každý trial spustí streaming benchmark na VŠECH vybraných videích,
zprůměruje metriky a zapíše výsledky.

Cíl: najít nastavení whisper.cpp s nejlepším WER při RTF < 1.0
(model musí stíhat live přepis mikrofonu — nestíhá = nepoužitelné).

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


def _compute_pareto_and_best(results: list[dict]) -> tuple[int | None, list[int]]:
    """
    Vrátí (best_trial_idx, pareto_idxs).

    Pro live mikrofon:
    - Nejlepší = nejnižší WER mezi trialy s RTF < 1.0
    - Pokud žádný RTF < 1.0 → nejnižší RTF (alespoň nejrychlejší)
    - Pareto fronta: žádný jiný trial nemá zároveň nižší WER i nižší RTF
    """
    valid = [r for r in results if r.get("wer") is not None and r.get("rtf") is not None]

    # Pareto fronta (WER vs RTF — obojí minimalizujeme)
    pareto_idxs: list[int] = []
    for r in valid:
        dominated = False
        for other in valid:
            if other is r:
                continue
            if (other["wer"] <= r["wer"] and other["rtf"] <= r["rtf"]
                    and (other["wer"] < r["wer"] or other["rtf"] < r["rtf"])):
                dominated = True
                break
        if not dominated:
            pareto_idxs.append(r["trial_idx"])

    # Nejlepší trial — RTF < 1.0 priorita pro live použití
    viable = [r for r in valid if r["rtf"] < 1.0]
    if viable:
        best = min(viable, key=lambda r: r["wer"])
    elif valid:
        # Žádný nestíhá real-time → vyber nejrychlejší
        best = min(valid, key=lambda r: r["rtf"])
    else:
        return None, pareto_idxs

    return best["trial_idx"], pareto_idxs


def _run_one_video(
    video_id: str,
    model_id: str,
    model_params: dict,
    chunk_seconds: int,
    sample_seconds: int,
    job_dir: Path,
    trial_idx: int,
    subtitles_root: Path,
    model_store_root: Path,
    run_streaming_benchmark,
    StreamingRunConfig,
    stream_youtube_audio,
    extract_vtt_clip_text,
    word_error_rate, char_error_rate,
    word_error_rate_normalized, match_error_rate, word_information_lost, word_diff,
    progress_cb,
) -> dict:
    """Spustí benchmark pro jedno video a vrátí metriky."""
    yt_url = f"https://www.youtube.com/watch?v={video_id}"

    from packages.ingest.source_resolver import SourceEntry
    source = SourceEntry(
        source_id=f"src-{video_id}",
        label=video_id,
        origin_type="youtube",
        value=yt_url,
        exists=True,
        canonical_url=yt_url,
        video_id=video_id,
    )

    run_config = StreamingRunConfig(
        model_id=model_id,
        model_params=model_params,
        model_store_root=str(model_store_root),
        output_dir=str(job_dir / f"trial_{trial_idx:03d}"),
        sample_seconds=sample_seconds,
        chunk_seconds=chunk_seconds,
        progress_callback=progress_cb,
    )
    result = run_streaming_benchmark(
        source=source,
        audio_generator=stream_youtube_audio(
            yt_url,
            chunk_seconds=0.1,
            max_seconds=float(sample_seconds),
        ),
        config=run_config,
    )

    transcript = result.get("transcript") or result.get("transcript_text") or result.get("text") or ""
    ref_text = extract_vtt_clip_text(
        video_id=video_id,
        clip_start_s=0,
        clip_end_s=sample_seconds,
        subtitles_root=subtitles_root,
    )

    wer = cer = wer_norm = mer = wil = None
    wdiff = None
    if ref_text and transcript:
        wer = word_error_rate(ref_text, transcript)
        cer = char_error_rate(ref_text, transcript)
        wer_norm = word_error_rate_normalized(ref_text, transcript)
        mer = match_error_rate(ref_text, transcript)
        wil = word_information_lost(ref_text, transcript)
        try:
            wdiff = word_diff(ref_text, transcript)
        except Exception:
            pass

    return {
        "video_id": video_id,
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
        "transcript": transcript[:3000] if transcript else None,
        "reference_text": ref_text[:3000] if ref_text else None,
        "word_diff": wdiff,
        "chunk_metrics": result.get("chunk_metrics"),
    }


def _avg(values: list) -> float | None:
    vals = [v for v in values if v is not None]
    return round(sum(vals) / len(vals), 4) if vals else None


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
    subtitles_root = Path(config["subtitles_root"])
    model_store_root = Path(config["model_store_root"])

    if not video_ids:
        _update_status(status_file, {"status": "failed", "error": "Žádná video_ids v konfiguraci"})
        return 1

    _update_status(status_file, {"status": "running"})

    try:
        from packages.benchmarks.runners.streaming_runner import run_streaming_benchmark, StreamingRunConfig
        from packages.ingest.youtube.stream_pipe import stream_youtube_audio
        from packages.benchmarks.metrics.text_metrics import (
            word_error_rate, char_error_rate,
            word_error_rate_normalized, match_error_rate, word_information_lost,
            word_diff,
        )
        from packages.benchmarks.ground_truth.vtt_reference import extract_vtt_clip_text

        for trial_idx, trial_params_raw in enumerate(trials):
            # Extrahuj chunk_seconds PŘED smyčkou přes videa (pop mutuje dict)
            trial_params = dict(trial_params_raw)
            chunk_seconds = int(trial_params.pop("_chunk_seconds", trial_params.pop("chunk_seconds", 30)))

            param_str = ", ".join(f"{k}={v}" for k, v in trial_params.items() if k != "initial_prompt")
            if trial_params.get("initial_prompt"):
                param_str += f", prompt='{str(trial_params['initial_prompt'])[:30]}...'"
            _set_progress(status_file, f"Trial {trial_idx+1}/{len(trials)}: chunk={chunk_seconds}s | {param_str}")

            source_metrics: list[dict] = []
            trial_error: str | None = None

            for v_idx, video_id in enumerate(video_ids):
                _set_progress(
                    status_file,
                    f"Trial {trial_idx+1}/{len(trials)}: video {v_idx+1}/{len(video_ids)} ({video_id}) ⬇ audio..."
                )
                try:
                    vm = _run_one_video(
                        video_id=video_id,
                        model_id=model_id,
                        model_params=dict(trial_params),  # kopie — nesmí se mutovat
                        chunk_seconds=chunk_seconds,
                        sample_seconds=sample_seconds,
                        job_dir=job_dir,
                        trial_idx=trial_idx,
                        subtitles_root=subtitles_root,
                        model_store_root=model_store_root,
                        run_streaming_benchmark=run_streaming_benchmark,
                        StreamingRunConfig=StreamingRunConfig,
                        stream_youtube_audio=stream_youtube_audio,
                        extract_vtt_clip_text=extract_vtt_clip_text,
                        word_error_rate=word_error_rate,
                        char_error_rate=char_error_rate,
                        word_error_rate_normalized=word_error_rate_normalized,
                        match_error_rate=match_error_rate,
                        word_information_lost=word_information_lost,
                        word_diff=word_diff,
                        progress_cb=lambda msg: _set_progress(
                            status_file,
                            f"Trial {trial_idx+1}/{len(trials)}: video {v_idx+1}/{len(video_ids)}: {msg}"
                        ),
                    )
                    source_metrics.append(vm)
                    _set_progress(
                        status_file,
                        f"Trial {trial_idx+1}/{len(trials)}: video {v_idx+1}/{len(video_ids)} ✓ "
                        f"WER={vm['wer']} RTF={vm['rtf']}"
                    )
                except Exception as e:
                    source_metrics.append({"video_id": video_id, "error": str(e)})
                    trial_error = str(e)
                    print(f"Trial {trial_idx} video {video_id} FAILED: {e}", file=sys.stderr)
                    traceback.print_exc(file=sys.stderr)

            # Průměr metrik přes všechna videa
            trial_result = {
                "trial_idx": trial_idx,
                "params": {**trial_params, "chunk_seconds": chunk_seconds},
                "chunk_seconds": chunk_seconds,
                # Průměrované metriky (klíčové pro výběr nejlepšího trialu)
                "wer": _avg([m.get("wer") for m in source_metrics]),
                "cer": _avg([m.get("cer") for m in source_metrics]),
                "wer_normalized": _avg([m.get("wer_normalized") for m in source_metrics]),
                "mer": _avg([m.get("mer") for m in source_metrics]),
                "wil": _avg([m.get("wil") for m in source_metrics]),
                "rtf": _avg([m.get("rtf") for m in source_metrics]),
                "latency_ms": _avg([m.get("latency_ms") for m in source_metrics]),
                # Detail per video
                "source_metrics": source_metrics,
                # Pro UI — zobraz první video jako ukázku
                "transcript": source_metrics[0].get("transcript") if source_metrics else None,
                "reference_text": source_metrics[0].get("reference_text") if source_metrics else None,
                "word_diff": source_metrics[0].get("word_diff") if source_metrics else None,
                "chunk_metrics": source_metrics[0].get("chunk_metrics") if source_metrics else None,
                "word_count": sum(m.get("word_count", 0) for m in source_metrics),
                "error": trial_error,
                "is_pareto": False,  # přepočítá se po všech trialech
                "rtf_viable": False,  # RTF < 1.0 = použitelné pro live mikrofon
            }
            _append_result(status_file, trial_result)

        # Po dokončení všech trialů: spočti best + Pareto
        data = json.loads(status_file.read_text(encoding="utf-8"))
        results = data.get("results", [])

        # Označ RTF viabilitu
        for r in results:
            r["rtf_viable"] = bool(r.get("rtf") and r["rtf"] < 1.0)

        best_idx, pareto_idxs = _compute_pareto_and_best(results)
        for r in results:
            r["is_pareto"] = r["trial_idx"] in pareto_idxs

        data["best_trial_idx"] = best_idx
        data["results"] = results
        data["status"] = "completed"
        data["progress_message"] = (
            f"Hotovo: {len(results)} trialů, nejlepší #{best_idx} "
            f"(WER={results[best_idx]['wer']}, RTF={results[best_idx]['rtf']})"
            if best_idx is not None else f"Hotovo: {len(results)} trialů"
        )
        status_file.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        return 0

    except Exception as e:
        traceback.print_exc(file=sys.stderr)
        _update_status(status_file, {"status": "failed", "error": str(e)})
        return 1


if __name__ == "__main__":
    sys.exit(main())
