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
import copy
import ctypes
import json
import math
import os
import platform
import random
import statistics
import subprocess
import sys
import threading
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path
from ctypes import wintypes

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from packages.common.console_io import configure_console_io
from packages.common.network_access import ensure_online_allowed
from packages.common.tuning_event_store import append_event, ensure_event_store

try:
    import psutil
except ModuleNotFoundError:
    psutil = None  # type: ignore[assignment]


_status_lock = threading.Lock()
_event_job_dir: Path | None = None
configure_console_io()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _emit_event(event_type: str, payload: dict | None = None) -> None:
    if _event_job_dir is None:
        return
    try:
        append_event(_event_job_dir, event_type=event_type, payload=payload or {})
    except Exception as e:
        print(f"WARN: event emit failed ({event_type}): {e}", file=sys.stderr)


def _atomic_write(path: Path, text: str) -> None:
    """Atomický zápis: zapíše do .tmp, pak přejmenuje — zabrání částečnému čtení."""
    tmp = path.with_suffix(".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(str(tmp), str(path))


def _detect_worker_hardware_info() -> dict:
    logical_cores = None
    physical_cores = None
    ram_total_mb = None
    if psutil is not None:
        try:
            logical_cores = int(psutil.cpu_count(logical=True) or 0) or None
        except Exception:
            logical_cores = None
        try:
            physical_cores = int(psutil.cpu_count(logical=False) or 0) or None
        except Exception:
            physical_cores = None
        try:
            ram_total_mb = round(float(psutil.virtual_memory().total) / (1024 * 1024), 1)
        except Exception:
            ram_total_mb = None
    cpu_model = (platform.processor() or "").strip() or (platform.uname().processor or "").strip() or None
    return {
        "hostname": platform.node(),
        "os": platform.platform(),
        "python": platform.python_version(),
        "cpu_model": cpu_model,
        "logical_cores": logical_cores,
        "physical_cores": physical_cores,
        "ram_total_mb": ram_total_mb,
    }


def _update_status(status_file: Path, updates: dict) -> None:
    with _status_lock:
        try:
            data = json.loads(status_file.read_text(encoding="utf-8"))
            data.update(updates)
            _atomic_write(status_file, json.dumps(data, ensure_ascii=False, indent=2))
            if "status" in updates:
                _emit_event(
                    "status_changed",
                    {
                        "status": updates.get("status"),
                        "error": updates.get("error"),
                    },
                )
        except Exception as e:
            print(f"WARN: status update failed: {e}", file=sys.stderr)


def _set_progress(status_file: Path, message: str) -> None:
    from datetime import datetime
    _update_status(status_file, {
        "progress_message": message,
        "updated_ts": datetime.now().isoformat(),
    })
    _emit_event("progress", {"message": message})
    try:
        print(message, flush=True)
    except Exception:
        pass


def _append_result(status_file: Path, result: dict) -> None:
    with _status_lock:
        for attempt in range(3):
            try:
                data = json.loads(status_file.read_text(encoding="utf-8"))
                data.setdefault("results", []).append(result)
                data["completed_trials"] = len(data["results"])
                _atomic_write(status_file, json.dumps(data, ensure_ascii=False, indent=2))
                _emit_event(
                    "trial_result",
                    {
                        "trial_idx": result.get("trial_idx"),
                        "model_id": result.get("model_id"),
                        "is_repeat": bool(result.get("is_repeat")),
                        "repeat_no": result.get("repeat_no"),
                        "wer": result.get("wer"),
                        "rtf": result.get("rtf"),
                        "latency_lane": result.get("latency_lane"),
                        "error": result.get("error"),
                    },
                )
                return
            except Exception as e:
                if attempt == 2:
                    print(f"ERROR: result append failed after 3 attempts (trial {result.get('trial_idx')}): {e}", file=sys.stderr)
                else:
                    import time as _time
                    _time.sleep(0.1 * (attempt + 1))


def _compute_pareto_and_best(results: list[dict]) -> tuple[int | None, list[int]]:
    """
    Vrátí (best_trial_idx, pareto_idxs).

    Pro live mikrofon:
    - Nejlepší = nejnižší WER mezi trialy s RTF < 1.0
    - Pokud žádný RTF < 1.0 → nejnižší RTF (alespoň nejrychlejší)
    - Pareto fronta: žádný jiný trial nemá zároveň nižší WER i nižší RTF
    - Trialy s chybou (error != None) jsou vyloučeny — mají neúplná data
    """
    valid = [
        r for r in results
        if r.get("wer") is not None and r.get("rtf") is not None and not r.get("error")
    ]

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


def _candidate_sort_key(result: dict) -> tuple[float, float, float]:
    """Řazení kandidátů pro výběr top-K k opakování."""
    wer = float(result.get("wer")) if isinstance(result.get("wer"), (int, float)) else float("inf")
    rtf = float(result.get("rtf")) if isinstance(result.get("rtf"), (int, float)) else float("inf")
    rtf_penalty = 0.0 if rtf <= 1.2 else 1.0
    return (rtf_penalty, wer, rtf)


def _select_repeat_seed_trials(results: list[dict], repeat_top_k: int) -> list[dict]:
    if repeat_top_k <= 0:
        return []
    seeds = [
        r for r in results
        if not r.get("is_repeat")
        and not r.get("error")
        and r.get("wer") is not None
        and r.get("rtf") is not None
    ]
    seeds.sort(key=_candidate_sort_key)
    return seeds[:repeat_top_k]


def _smart_candidate_key(*, model_id: str, params_raw: dict) -> str:
    clean = dict(params_raw)
    chunk = int(clean.pop("_chunk_seconds", clean.pop("chunk_seconds", 30)))
    clean.pop("_model_id", None)
    frozen = tuple(
        sorted((str(k), json.dumps(v, ensure_ascii=False, sort_keys=True)) for k, v in clean.items())
    )
    return json.dumps(
        {
            "model_id": str(model_id),
            "chunk_seconds": chunk,
            "params": frozen,
        },
        ensure_ascii=False,
        sort_keys=True,
    )


def _smart_score_tuple(result: dict) -> tuple[float, float, float, float, float]:
    wer_soft = result.get("wer_soft")
    wer = result.get("wer")
    quality = (
        float(wer_soft) if isinstance(wer_soft, (int, float))
        else (float(wer) if isinstance(wer, (int, float)) else float("inf"))
    )
    rtf = float(result.get("rtf")) if isinstance(result.get("rtf"), (int, float)) else float("inf")
    rtf_penalty = 0.0 if rtf <= 1.2 else 1.0
    delay = (
        float(result.get("perceived_delay_s"))
        if isinstance(result.get("perceived_delay_s"), (int, float))
        else float("inf")
    )
    ram_peak = (
        float(result.get("ram_peak_mb"))
        if isinstance(result.get("ram_peak_mb"), (int, float))
        else float("inf")
    )
    return (rtf_penalty, quality, rtf, delay, ram_peak)


def _metric_stats(values: list[float]) -> dict | None:
    vals = [float(v) for v in values if isinstance(v, (int, float))]
    if not vals:
        return None
    mean_v = sum(vals) / len(vals)
    if len(vals) >= 2:
        std_v = statistics.stdev(vals)
        ci95 = 1.96 * (std_v / math.sqrt(len(vals)))
    else:
        std_v = 0.0
        ci95 = 0.0
    return {
        "n": len(vals),
        "mean": round(mean_v, 6),
        "std": round(std_v, 6),
        "ci95_low": round(mean_v - ci95, 6),
        "ci95_high": round(mean_v + ci95, 6),
    }


def _build_reproducibility(results: list[dict]) -> list[dict]:
    """
    Agreguje repeat běhy po seed trialu.
    Do skupiny patří seed trial + trialy s repeat_of_trial_idx == seed_idx.
    """
    by_seed_idx: dict[int, list[dict]] = {}
    by_idx = {int(r["trial_idx"]): r for r in results if isinstance(r.get("trial_idx"), int)}

    for r in results:
        if r.get("is_repeat"):
            seed_idx = r.get("repeat_of_trial_idx")
            if isinstance(seed_idx, int):
                by_seed_idx.setdefault(seed_idx, []).append(r)

    summary: list[dict] = []
    for seed_idx, repeats in by_seed_idx.items():
        seed = by_idx.get(seed_idx)
        if seed is None:
            continue
        group = [seed, *repeats]
        ok = [g for g in group if not g.get("error")]
        if len(ok) < 2:
            continue

        wer_stats = _metric_stats([g.get("wer") for g in ok if isinstance(g.get("wer"), (int, float))])
        rtf_stats = _metric_stats([g.get("rtf") for g in ok if isinstance(g.get("rtf"), (int, float))])
        latency_stats = _metric_stats([g.get("latency_ms") for g in ok if isinstance(g.get("latency_ms"), (int, float))])
        perceived_stats = _metric_stats([g.get("perceived_delay_s") for g in ok if isinstance(g.get("perceived_delay_s"), (int, float))])
        ram_stats = _metric_stats([g.get("ram_mb") for g in ok if isinstance(g.get("ram_mb"), (int, float))])
        viable_rate = None
        viable_vals = [1.0 if bool(g.get("rtf_viable")) else 0.0 for g in ok]
        if viable_vals:
            viable_rate = round(sum(viable_vals) / len(viable_vals), 4)

        summary.append({
            "seed_trial_idx": seed_idx,
            "model_id": seed.get("model_id"),
            "params": seed.get("params"),
            "chunk_seconds": seed.get("chunk_seconds"),
            "runs_total": len(group),
            "runs_ok": len(ok),
            "runs_error": len(group) - len(ok),
            "rtf_viable_rate": viable_rate,
            "wer": wer_stats,
            "rtf": rtf_stats,
            "latency_ms": latency_stats,
            "perceived_delay_s": perceived_stats,
            "ram_mb": ram_stats,
            "repeat_trial_idxs": [int(g["trial_idx"]) for g in repeats if isinstance(g.get("trial_idx"), int)],
        })

    summary.sort(
        key=lambda s: (
            0.0 if (s.get("wer") and isinstance(s["wer"].get("mean"), (int, float))) else 1.0,
            s.get("wer", {}).get("mean", float("inf")) if isinstance(s.get("wer"), dict) else float("inf"),
            s.get("rtf", {}).get("mean", float("inf")) if isinstance(s.get("rtf"), dict) else float("inf"),
        )
    )
    return summary


def _validate_top_candidates_n(
    results: list[dict],
    repro_summary: list[dict],
    *,
    required_n: int = 3,
    top_k: int = 3,
) -> dict:
    req = max(1, int(required_n))
    tk = max(1, int(top_k))
    base = [
        r for r in results
        if not r.get("is_repeat")
        and not r.get("error")
        and isinstance(r.get("wer"), (int, float))
    ]
    base.sort(
        key=lambda r: (
            float(r.get("wer", 999.0)),
            float(r.get("rtf", 999.0)) if isinstance(r.get("rtf"), (int, float)) else 999.0,
        )
    )
    checked = base[:tk]
    repro_by_seed: dict[int, int] = {}
    for item in repro_summary:
        if isinstance(item, dict) and isinstance(item.get("seed_trial_idx"), int):
            repro_by_seed[int(item["seed_trial_idx"])] = int(item.get("runs_ok", 0) or 0)

    per_trial: list[dict] = []
    missing: list[int] = []
    for r in checked:
        idx = int(r["trial_idx"])
        runs_ok = repro_by_seed.get(idx, 1)  # seed run samotný = 1
        ok = runs_ok >= req
        if not ok:
            missing.append(idx)
        per_trial.append({"trial_idx": idx, "runs_ok": runs_ok, "ok": ok})

    if not checked:
        return {
            "required_n": req,
            "checked_top_k": 0,
            "passed": False,
            "missing_trial_idxs": [],
            "per_trial": [],
            "reason": "no_valid_seed_trials",
        }

    return {
        "required_n": req,
        "checked_top_k": len(checked),
        "passed": len(missing) == 0,
        "missing_trial_idxs": missing,
        "per_trial": per_trial,
        "reason": "missing_repeats" if missing else "ok",
    }


def _compute_clip_starts(video_ids: list[str], sample_seconds: int, clip_seed: int | None, items_json: Path) -> dict[str, float]:
    """Pro každé video spočítá clip_start_s z clip_seed. Konzistentní pro všechny trialy."""
    durations: dict[str, float | None] = {}
    if items_json.exists():
        try:
            items = json.loads(items_json.read_text(encoding="utf-8"))
            for item in items:
                vid = item.get("video_id")
                if vid:
                    durations[vid] = item.get("duration_seconds")
        except Exception:
            pass

    rng = random.Random(clip_seed if clip_seed is not None else 42)
    result: dict[str, float] = {}
    for video_id in video_ids:
        duration = durations.get(video_id)
        if duration and duration > sample_seconds + 60:
            max_start = duration - sample_seconds - 30
            clip_start = rng.uniform(60.0, max_start)
        else:
            clip_start = 0.0
        result[video_id] = clip_start
    return result


def _slice_wav_clip(full_wav: Path, clip_start_s: float, sample_seconds: int, out_path: Path) -> None:
    """Vyřízne clip z plného WAV souboru (bez ffmpeg, čistě Python)."""
    import wave
    with wave.open(str(full_wav), "rb") as wf:
        sr = wf.getframerate()
        start_frame = int(clip_start_s * sr)
        n_frames = int(sample_seconds * sr)
        wf.setpos(min(start_frame, wf.getnframes()))
        raw = wf.readframes(n_frames)
        sampwidth = wf.getsampwidth()
        nchannels = wf.getnchannels()
    with wave.open(str(out_path), "wb") as wf_out:
        wf_out.setnchannels(nchannels)
        wf_out.setsampwidth(sampwidth)
        wf_out.setframerate(sr)
        wf_out.writeframes(raw)


def _predownload_audio(
    video_id: str,
    clip_start_s: float,
    sample_seconds: int,
    job_dir: Path,
    stream_youtube_audio,
    progress_cb,
    audio_cache_dir: Path | None = None,
) -> Path:
    """Vrátí cestu k WAV souboru s clipem. Pořadí zdrojů:
    1. Lokální job cache (už staženo v tomto jobu)
    2. Globální audio cache (staženo skriptem download_audio.py)
    3. Stažení z YouTube
    """
    import wave
    from array import array as _array

    wav_path = job_dir / f"audio_{video_id}.wav"
    if wav_path.exists():
        return wav_path  # Už staženo v tomto jobu

    # Zkontroluj globální cache: runtime/audio_cache/{video_id}.wav
    if audio_cache_dir is not None:
        full_wav = Path(audio_cache_dir) / f"{video_id}.wav"
        if full_wav.exists():
            progress_cb(f"✂ Vyřezávám clip {video_id} ({sample_seconds}s od {int(clip_start_s)}s) z lokálního souboru...")
            _slice_wav_clip(full_wav, clip_start_s, sample_seconds, wav_path)
            progress_cb(f"✓ Audio {video_id} připraveno z cache ({wav_path.stat().st_size // 1024} kB)")
            return wav_path

    # Fallback: stažení z YouTube
    yt_url = f"https://www.youtube.com/watch?v={video_id}"
    ensure_online_allowed(
        component="scripts.tuning_worker",
        action="_predownload_audio",
        reason="fallback stažení audio klipu z YouTube pro tuning trial",
        target=yt_url,
        details={"video_id": video_id, "sample_seconds": sample_seconds, "clip_start_s": clip_start_s},
    )
    progress_cb(f"⬇ Stahuji audio {video_id} ({sample_seconds}s od {int(clip_start_s)}s) z YouTube...")

    all_pcm: list[int] = []
    sample_rate = 16000
    for samples, sr in stream_youtube_audio(
        yt_url,
        chunk_seconds=10.0,
        max_seconds=float(sample_seconds),
        start_offset_seconds=clip_start_s,
    ):
        sample_rate = sr
        for s in samples:
            clamped = max(-1.0, min(1.0, s))
            all_pcm.append(int(clamped * 32767))

    with wave.open(str(wav_path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(_array("h", all_pcm).tobytes())

    progress_cb(f"✓ Audio {video_id} uloženo ({len(all_pcm)/sample_rate:.1f}s)")
    return wav_path



def _run_one_video(
    video_id: str,
    model_id: str,
    model_params: dict,
    chunk_seconds: int,
    sample_seconds: int,
    audio_wav: Path,
    job_dir: Path,
    trial_idx: int,
    subtitles_root: Path,
    model_store_root: Path,
    clip_start_s: float,
    run_streaming_benchmark,
    StreamingRunConfig,
    extract_vtt_clip_text,
    word_error_rate, char_error_rate,
    word_error_rate_normalized, word_error_rate_soft, match_error_rate, word_information_lost, word_diff,
    progress_cb,
    source_wav_path: str | None = None,
) -> dict:
    """Spustí benchmark pro jedno video a vrátí metriky (audio z lokálního WAV)."""
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
        source_wav_path=source_wav_path,  # přímá WAV — žádná double konverze
    )
    result = run_streaming_benchmark(
        source=source,
        audio_generator=None,  # WAV jde přímo přes source_wav_path — generator zbytečný
        config=run_config,
    )

    transcript = result.get("transcript") or result.get("transcript_text") or result.get("text") or ""

    # Skutečná délka audia z WAV — může být kratší než sample_seconds (konec videa, výpadek YT)
    # Bez toho by reference text zahrnoval slova která model nikdy neslyšel → nafouklé WER
    try:
        import wave as _wave
        with _wave.open(str(audio_wav), "rb") as _wf:
            actual_audio_s = _wf.getnframes() / max(1, _wf.getframerate())
    except Exception:
        actual_audio_s = float(sample_seconds)

    ref_text = extract_vtt_clip_text(
        video_id=video_id,
        clip_start_s=clip_start_s,
        clip_end_s=clip_start_s + min(actual_audio_s, float(sample_seconds)),
        subtitles_root=subtitles_root,
    )

    wer = cer = wer_norm = wer_soft = mer = wil = None
    wdiff = None
    if ref_text and transcript:
        wer = word_error_rate(ref_text, transcript)
        cer = char_error_rate(ref_text, transcript)
        wer_norm = word_error_rate_normalized(ref_text, transcript)
        wer_soft = word_error_rate_soft(ref_text, transcript)
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
        "wer_soft": round(wer_soft, 4) if wer_soft is not None else None,
        "mer": round(mer, 4) if mer is not None else None,
        "wil": round(wil, 4) if wil is not None else None,
        "cpu_percent": result.get("cpu_percent"),
        "ram_mb": result.get("ram_mb"),
        "rtf": result.get("rtf"),
        "latency_ms": result.get("latency_ms"),
        "first_word_latency_ms": result.get("first_word_latency_ms"),
        "first_word_audio_ms": result.get("first_word_audio_ms"),
        "latency_mode": result.get("latency_mode"),
        "model_cached": result.get("model_cached"),
        "elapsed_s": result.get("engine_elapsed_seconds"),
        "total_audio_s": result.get("total_audio_s"),
        "word_count": len(transcript.split()) if transcript else 0,
        "transcript": transcript[:3000] if transcript else None,
        "reference_text": ref_text[:3000] if ref_text else None,
        "word_diff": wdiff,
        "chunk_metrics": result.get("chunk_metrics"),
    }


def _run_one_video_real_mic(
    *,
    video_id: str,
    model_id: str,
    model_params: dict,
    sample_seconds: int,
    clip_start_s: float,
    trial_idx: int,
    job_dir: Path,
    subtitles_root: Path,
    extract_vtt_clip_text,
    word_error_rate,
    char_error_rate,
    word_error_rate_normalized,
    word_error_rate_soft,
    match_error_rate,
    word_information_lost,
    word_diff,
    progress_cb,
    mic_chunk_seconds: float = 0.20,
    mic_prepare_seconds: int = 4,
    mic_device: int | str | None = None,
) -> dict:
    """
    Reálný mic běh pro jedno video:
    - nahraje sample_seconds audia z mikrofonu,
    - průběžně transkribuje přes backend mic_service (live session),
    - porovná transcript s referencí z VTT pro zadaný clip_start.

    Pozn.: Operátor musí pustit na mobilu správný úsek videa ve chvíli nahrávání.
    """
    from array import array as _array
    import wave as _wave

    from backend.app.services import mic_service
    from packages.ingest.youtube.stream_pipe import stream_mic_audio

    prep_s = max(0, int(mic_prepare_seconds))
    if prep_s > 0:
        progress_cb(
            f"🎤 Real mic {video_id}: připrav přehrání od {int(clip_start_s)}s. "
            f"Nahrávání startuje za {prep_s}s."
        )
        time.sleep(float(prep_s))

    target_sample_rate = 16000
    target_samples = max(1, int(max(1, sample_seconds) * target_sample_rate))
    mic_chunk_s = max(0.05, float(mic_chunk_seconds))

    session_id = mic_service.create_session(model_id, dict(model_params))
    mic_started = False
    mic_error: str | None = None
    reason_code: str | None = None
    final: dict = {}

    captured_pcm = _array("h")
    total_samples = 0
    sample_rate = target_sample_rate
    chunk_metrics: list[dict] = []
    started_perf = time.perf_counter()
    last_progress_audio_s = -10.0

    try:
        mic_service.start_recording(session_id)
        mic_started = True

        for samples, sr in stream_mic_audio(
            chunk_seconds=mic_chunk_s,
            sample_rate=target_sample_rate,
            device=mic_device,
            reconnect_attempts=2,
            reconnect_delay_s=1.0,
        ):
            sample_rate = int(sr) if isinstance(sr, int) else target_sample_rate
            remaining = target_samples - total_samples
            if remaining <= 0:
                break
            clipped_samples = samples[:remaining]
            if not clipped_samples:
                continue

            for s in clipped_samples:
                captured_pcm.append(int(max(-1.0, min(1.0, float(s))) * 32767.0))
            total_samples += len(clipped_samples)

            chunk_started = time.perf_counter()
            partial = mic_service.process_audio_chunk(
                session_id=session_id,
                samples=clipped_samples,
                sample_rate=sample_rate,
            )
            chunk_processing_s = max(0.0, time.perf_counter() - chunk_started)
            chunk_audio_s = len(clipped_samples) / max(1, sample_rate)
            elapsed_s = max(0.0, time.perf_counter() - started_perf)
            partial_text = str(partial.get("text") or "").strip()

            chunk_metrics.append({
                "chunk_duration_s": round(chunk_audio_s, 3),
                "processing_s": round(chunk_processing_s, 4),
                "rtf": round(chunk_processing_s / max(0.001, chunk_audio_s), 4),
                "total_elapsed_s": round(elapsed_s, 4),
                "words": len(partial_text.split()) if partial_text else 0,
                "dropped_by_backpressure": bool(partial.get("dropped_by_backpressure")),
                "processing_debt_ms": (
                    round(float(partial.get("processing_debt_ms")), 1)
                    if isinstance(partial.get("processing_debt_ms"), (int, float))
                    else None
                ),
                "queue_depth_peak_ms": (
                    round(float(partial.get("queue_depth_peak_ms")), 1)
                    if isinstance(partial.get("queue_depth_peak_ms"), (int, float))
                    else None
                ),
            })

            if partial.get("error"):
                mic_error = str(partial.get("error"))
                reason_code = str(partial.get("reason_code") or "adapter_error")
                break

            audio_s = total_samples / max(1, sample_rate)
            if (audio_s - last_progress_audio_s) >= 10.0:
                last_progress_audio_s = audio_s
                progress_cb(
                    f"🎤 Real mic {video_id}: nahráno {audio_s:.1f}s / {float(sample_seconds):.1f}s"
                )

            if total_samples >= target_samples:
                break

    except Exception as exc:
        mic_error = str(exc)
        if not reason_code:
            reason_code = "mic_capture_error"
    finally:
        if mic_started:
            try:
                final = mic_service.stop_recording(session_id)
            except Exception as exc:
                if mic_error is None:
                    mic_error = str(exc)
                if not reason_code:
                    reason_code = "mic_finalize_error"

    if final.get("error") and mic_error is None:
        mic_error = str(final.get("error"))
    if final.get("reason_code") and not reason_code:
        reason_code = str(final.get("reason_code"))

    transcript = str(final.get("text") or "").strip()
    actual_audio_s = (total_samples / max(1, sample_rate)) if total_samples > 0 else 0.0
    elapsed_s = float(final.get("elapsed_s")) if isinstance(final.get("elapsed_s"), (int, float)) else max(0.001, time.perf_counter() - started_perf)
    rtf = float(final.get("rtf")) if isinstance(final.get("rtf"), (int, float)) else (elapsed_s / max(0.1, actual_audio_s if actual_audio_s > 0 else float(sample_seconds)))

    first_word_latency_ms = (
        float(final.get("first_word_latency_ms"))
        if isinstance(final.get("first_word_latency_ms"), (int, float))
        else None
    )
    latency_ms = first_word_latency_ms if first_word_latency_ms is not None else round(elapsed_s * 1000.0, 1)

    # Ulož raw mic capture (audit/replay).
    trial_dir = job_dir / f"trial_{trial_idx:03d}"
    trial_dir.mkdir(parents=True, exist_ok=True)
    capture_wav = trial_dir / f"mic_{video_id}.wav"
    try:
        with _wave.open(str(capture_wav), "wb") as wf_out:
            wf_out.setnchannels(1)
            wf_out.setsampwidth(2)
            wf_out.setframerate(max(8000, int(sample_rate)))
            wf_out.writeframes(captured_pcm.tobytes())
    except Exception:
        # Capture je pomocný artefakt; chyba zápisu nesmí shodit trial.
        pass

    ref_text = extract_vtt_clip_text(
        video_id=video_id,
        clip_start_s=clip_start_s,
        clip_end_s=clip_start_s + min(actual_audio_s, float(sample_seconds)),
        subtitles_root=subtitles_root,
    )

    wer = cer = wer_norm = wer_soft = mer = wil = None
    wdiff = None
    if ref_text and transcript:
        wer = word_error_rate(ref_text, transcript)
        cer = char_error_rate(ref_text, transcript)
        wer_norm = word_error_rate_normalized(ref_text, transcript)
        wer_soft = word_error_rate_soft(ref_text, transcript)
        mer = match_error_rate(ref_text, transcript)
        wil = word_information_lost(ref_text, transcript)
        try:
            wdiff = word_diff(ref_text, transcript)
        except Exception:
            pass

    worker_rss_peak_mb = (
        float(final.get("worker_rss_peak_mb"))
        if isinstance(final.get("worker_rss_peak_mb"), (int, float))
        else _get_rss_mb(psutil.Process() if psutil is not None else None)
    )
    if mic_error is None and not transcript:
        mic_error = "Real mic přepis nevrátil text (no_tokens)."
        if not reason_code:
            reason_code = "no_tokens"

    return {
        "video_id": video_id,
        "wer": round(wer, 4) if wer is not None else None,
        "cer": round(cer, 4) if cer is not None else None,
        "wer_normalized": round(wer_norm, 4) if wer_norm is not None else None,
        "wer_soft": round(wer_soft, 4) if wer_soft is not None else None,
        "mer": round(mer, 4) if mer is not None else None,
        "wil": round(wil, 4) if wil is not None else None,
        "cpu_percent": None,
        "ram_mb": round(float(worker_rss_peak_mb), 1) if isinstance(worker_rss_peak_mb, (int, float)) else None,
        "rtf": round(float(rtf), 4) if isinstance(rtf, (int, float)) else None,
        "latency_ms": round(float(latency_ms), 1) if isinstance(latency_ms, (int, float)) else None,
        "first_word_latency_ms": round(float(first_word_latency_ms), 1) if isinstance(first_word_latency_ms, (int, float)) else None,
        "first_word_audio_ms": None,
        "latency_mode": "online_first_text_or_elapsed_ms",
        "model_cached": None,
        "elapsed_s": round(float(elapsed_s), 4) if isinstance(elapsed_s, (int, float)) else None,
        "total_audio_s": round(float(actual_audio_s), 3),
        "word_count": len(transcript.split()) if transcript else 0,
        "transcript": transcript[:3000] if transcript else None,
        "reference_text": ref_text[:3000] if ref_text else None,
        "word_diff": wdiff,
        "chunk_metrics": chunk_metrics if chunk_metrics else None,
        "first_token_ms_p50": (
            round(float(final.get("first_token_ms_p50")), 1)
            if isinstance(final.get("first_token_ms_p50"), (int, float))
            else (round(float(first_word_latency_ms), 1) if first_word_latency_ms is not None else None)
        ),
        "first_token_ms_p95": (
            round(float(final.get("first_token_ms_p95")), 1)
            if isinstance(final.get("first_token_ms_p95"), (int, float))
            else (round(float(first_word_latency_ms), 1) if first_word_latency_ms is not None else None)
        ),
        "segment_finalize_ms_p50": (
            round(float(final.get("segment_finalize_ms_p50")), 1)
            if isinstance(final.get("segment_finalize_ms_p50"), (int, float))
            else None
        ),
        "segment_finalize_ms_p95": (
            round(float(final.get("segment_finalize_ms_p95")), 1)
            if isinstance(final.get("segment_finalize_ms_p95"), (int, float))
            else None
        ),
        "processing_ms_p50": (
            round(float(final.get("processing_ms_p50")), 1)
            if isinstance(final.get("processing_ms_p50"), (int, float))
            else None
        ),
        "processing_ms_p95": (
            round(float(final.get("processing_ms_p95")), 1)
            if isinstance(final.get("processing_ms_p95"), (int, float))
            else None
        ),
        "capture_jitter_ms_p50": (
            round(float(final.get("capture_jitter_ms_p50")), 1)
            if isinstance(final.get("capture_jitter_ms_p50"), (int, float))
            else None
        ),
        "capture_jitter_ms_p95": (
            round(float(final.get("capture_jitter_ms_p95")), 1)
            if isinstance(final.get("capture_jitter_ms_p95"), (int, float))
            else None
        ),
        "capture_lag_ms_p50": (
            round(float(final.get("capture_lag_ms_p50")), 1)
            if isinstance(final.get("capture_lag_ms_p50"), (int, float))
            else None
        ),
        "capture_lag_ms_p95": (
            round(float(final.get("capture_lag_ms_p95")), 1)
            if isinstance(final.get("capture_lag_ms_p95"), (int, float))
            else None
        ),
        "drop_rate": (
            round(float(final.get("drop_rate")), 4)
            if isinstance(final.get("drop_rate"), (int, float))
            else None
        ),
        "backpressure_events": (
            int(final.get("backpressure_events"))
            if isinstance(final.get("backpressure_events"), int)
            else None
        ),
        "queue_depth_peak_s": (
            round(float(final.get("queue_depth_peak_s")), 3)
            if isinstance(final.get("queue_depth_peak_s"), (int, float))
            else None
        ),
        "session_resets": (
            int(final.get("session_resets"))
            if isinstance(final.get("session_resets"), int)
            else None
        ),
        "reason_code": reason_code,
        "error": mic_error,
    }


def _avg(values: list) -> float | None:
    vals = [v for v in values if v is not None]
    return round(sum(vals) / len(vals), 4) if vals else None


def _percentile(values: list[float], p: float) -> float | None:
    vals = sorted(float(v) for v in values if v is not None)
    if not vals:
        return None
    if len(vals) == 1:
        return round(vals[0], 4)
    rank = (len(vals) - 1) * (p / 100.0)
    low = math.floor(rank)
    high = math.ceil(rank)
    if low == high:
        return round(vals[low], 4)
    frac = rank - low
    interp = vals[low] * (1.0 - frac) + vals[high] * frac
    return round(interp, 4)


def _is_live_latency_mode(mode: str | None) -> bool:
    if not mode:
        return False
    return mode.startswith("online_")


def _is_probe_latency_mode(mode: str | None) -> bool:
    if not mode:
        return False
    return str(mode).startswith("online_probe_")


def _derive_latency_quality(source_metrics: list[dict]) -> str:
    modes = [str(m.get("latency_mode")) for m in source_metrics if not m.get("error") and m.get("latency_mode")]
    if not modes:
        return "unknown"
    if all(_is_probe_latency_mode(m) for m in modes):
        return "probe_online"
    if all(_is_live_latency_mode(m) for m in modes):
        return "measured_live"
    if all(("proxy" in m or "single_batch" in m) for m in modes):
        return "proxy_offline"
    return "mixed"


def _latency_lane_from_quality(latency_quality: str | None) -> str:
    q = str(latency_quality or "").strip().lower()
    if q == "measured_live":
        return "strict_live"
    if q == "probe_online":
        return "probe_online"
    if q == "proxy_offline":
        return "batch_proxy"
    if q == "mixed":
        return "mixed"
    return "unknown"


def _compute_perceived_delay(
    source_metrics: list[dict],
    chunk_seconds: int,
) -> tuple[float | None, str | None, str]:
    """Vrátí (perceived_delay_s, method, quality)."""
    ok = [m for m in source_metrics if not m.get("error")]
    if not ok:
        return None, None, "unknown"

    # Preferuj skutečné first-word latence z live režimu.
    first_word_ms = [
        float(m.get("first_word_latency_ms"))
        for m in ok
        if isinstance(m.get("first_word_latency_ms"), (int, float))
        and _is_live_latency_mode(str(m.get("latency_mode") or ""))
        and not _is_probe_latency_mode(str(m.get("latency_mode") or ""))
    ]
    if first_word_ms:
        return round(sum(first_word_ms) / len(first_word_ms) / 1000.0, 3), "first_word_live_ms", "high"

    # Online probe režim (first chunk) — měřené, ale aproximované.
    probe_latency_ms = [
        float(m.get("latency_ms"))
        for m in ok
        if isinstance(m.get("latency_ms"), (int, float))
        and _is_probe_latency_mode(str(m.get("latency_mode") or ""))
    ]
    if probe_latency_ms:
        return round(sum(probe_latency_ms) / len(probe_latency_ms) / 1000.0, 3), "online_probe_latency_ms", "medium"

    # Druhý nejlepší fallback: online latency_ms.
    online_latency_ms = [
        float(m.get("latency_ms"))
        for m in ok
        if isinstance(m.get("latency_ms"), (int, float))
        and _is_live_latency_mode(str(m.get("latency_mode") or ""))
    ]
    if online_latency_ms:
        return round(sum(online_latency_ms) / len(online_latency_ms) / 1000.0, 3), "online_latency_ms", "medium"

    # Proxy pro batch/replay režim.
    avg_rtf = _avg([m.get("rtf") for m in ok])
    if avg_rtf is not None:
        return round(chunk_seconds * (1.0 + float(avg_rtf)), 3), "chunk_plus_chunk_rtf_proxy", "low"

    elapsed_s = [float(m.get("elapsed_s")) for m in ok if isinstance(m.get("elapsed_s"), (int, float))]
    if elapsed_s:
        return round(sum(elapsed_s) / len(elapsed_s), 3), "elapsed_proxy", "low"

    return None, None, "unknown"


def _get_rss_mb(proc) -> float | None:
    """Vrátí RSS v MB pro daný process handle."""
    if proc is None:
        return None
    try:
        return round(proc.memory_info().rss / (1024 * 1024), 1)
    except Exception:
        return None


def _soft_ram_violation(
    *,
    limit_mb: int | None,
    measured_ram_mb: float | None,
    tolerance_ratio: float = 0.03,
) -> str | None:
    if limit_mb is None or measured_ram_mb is None:
        return None
    lim = float(limit_mb)
    allowed = lim * (1.0 + max(0.0, float(tolerance_ratio)))
    if float(measured_ram_mb) > allowed:
        return (
            f"RAM soft cap překročen: {float(measured_ram_mb):.1f} MB > "
            f"{allowed:.1f} MB (limit {lim:.0f} MB)"
        )
    return None


def _trial_cache_key(
    *,
    model_id: str,
    model_params: dict,
    video_id: str,
    sample_seconds: int,
    chunk_seconds: int,
    clip_start_s: float,
    audio_wav: Path,
) -> tuple:
    """
    Cache key pro per-video výsledek.
    Pozn.: chunk_seconds je součást key — latency/perceived_delay může být chunk-dependent.
    """
    frozen_params = tuple(sorted((str(k), json.dumps(v, ensure_ascii=False, sort_keys=True)) for k, v in model_params.items()))
    return (
        model_id,
        frozen_params,
        video_id,
        int(sample_seconds),
        int(chunk_seconds),
        round(float(clip_start_s), 3),
        str(audio_wav.resolve()),
    )


def _coerce_pct(value) -> float | None:
    if value is None:
        return None
    try:
        v = float(value)
    except Exception:
        return None
    return max(0.0, min(95.0, round(v, 1)))


def _load_result_fields(
    *,
    load_profile: str,
    load_cpu_target_pct: float | None,
    load_ram_target_pct: float | None,
    load_summary: dict | None = None,
) -> dict:
    out = {
        "load_profile": load_profile,
        "load_cpu_target_pct": load_cpu_target_pct,
        "load_ram_target_pct": load_ram_target_pct,
        "load_cpu_actual_avg_pct": None,
        "load_cpu_actual_p95_pct": None,
        "load_ram_actual_avg_pct": None,
        "load_ram_actual_p95_pct": None,
        "load_samples": None,
        "load_control_ok": None,
    }
    if load_summary:
        out.update({
            "load_cpu_actual_avg_pct": load_summary.get("load_cpu_actual_avg_pct"),
            "load_cpu_actual_p95_pct": load_summary.get("load_cpu_actual_p95_pct"),
            "load_ram_actual_avg_pct": load_summary.get("load_ram_actual_avg_pct"),
            "load_ram_actual_p95_pct": load_summary.get("load_ram_actual_p95_pct"),
            "load_samples": load_summary.get("load_samples"),
            "load_control_ok": load_summary.get("load_control_ok"),
        })
    return out


def _constraints_result_fields(
    *,
    constraints_profile: str,
    constraints_cpu_cores: int | None,
    constraints_ram_limit_mb: int | None,
    constraints_priority: str | None,
    constraints_applied: bool | None,
    constraints_warnings: list[str] | None,
    constraints_ram_mode: str | None,
    constraints_cpu_applied: bool | None,
    constraints_priority_applied: bool | None,
    constraints_ram_hard_cap_applied: bool | None,
    constraints_ram_hard_cap_error: str | None,
) -> dict:
    return {
        "constraints_profile": constraints_profile,
        "constraints_cpu_cores": constraints_cpu_cores,
        "constraints_ram_limit_mb": constraints_ram_limit_mb,
        "constraints_priority": constraints_priority,
        "constraints_applied": constraints_applied,
        "constraints_warnings": constraints_warnings or [],
        "constraints_ram_mode": constraints_ram_mode,
        "constraints_cpu_applied": constraints_cpu_applied,
        "constraints_priority_applied": constraints_priority_applied,
        "constraints_ram_hard_cap_applied": constraints_ram_hard_cap_applied,
        "constraints_ram_hard_cap_error": constraints_ram_hard_cap_error,
    }


_JOB_OBJECT_HANDLE = None


def _apply_process_constraints(
    *,
    profile: str,
    cpu_cores: int | None,
    ram_limit_mb: int | None,
    priority: str | None,
) -> tuple[bool, list[str], str, dict[str, bool | str | None]]:
    warnings: list[str] = []
    ram_mode = "none"
    details: dict[str, bool | str | None] = {
        "cpu_applied": None,
        "priority_applied": None,
        "ram_hard_cap_applied": None,
        "ram_hard_cap_error": None,
        "any_applied": False,
    }
    if profile == "none":
        return False, warnings, ram_mode, details

    requested_any = False
    fully_applied = True
    applied_any = False
    current_proc = psutil.Process() if psutil is not None else None

    # CPU affinity cap
    if cpu_cores is not None:
        requested_any = True
        if current_proc is None or not hasattr(current_proc, "cpu_affinity"):
            details["cpu_applied"] = False
            fully_applied = False
            warnings.append("CPU cap nelze aplikovat (cpu_affinity není dostupné).")
        else:
            try:
                available = list(current_proc.cpu_affinity())
                if available:
                    keep_n = max(1, min(int(cpu_cores), len(available)))
                    current_proc.cpu_affinity(available[:keep_n])
                    details["cpu_applied"] = True
                    applied_any = True
                else:
                    details["cpu_applied"] = False
                    fully_applied = False
                    warnings.append("CPU cap nelze aplikovat (prázdná CPU affinity maska).")
            except Exception as e:
                details["cpu_applied"] = False
                fully_applied = False
                warnings.append(f"CPU cap selhal: {e}")

    # Process priority
    if priority:
        requested_any = True
        if current_proc is None:
            details["priority_applied"] = False
            fully_applied = False
            warnings.append("Priorita se nepodařila nastavit: psutil proces není dostupný.")
        else:
            try:
                prio_norm = str(priority).strip().lower()
                if os.name == "nt":
                    if prio_norm == "idle":
                        current_proc.nice(psutil.IDLE_PRIORITY_CLASS)
                    elif prio_norm == "below_normal":
                        current_proc.nice(psutil.BELOW_NORMAL_PRIORITY_CLASS)
                    else:
                        current_proc.nice(psutil.NORMAL_PRIORITY_CLASS)
                    details["priority_applied"] = True
                    applied_any = True
                else:
                    # POSIX fallback: nicer process for lower priority.
                    if prio_norm == "idle":
                        os.nice(10)
                        details["priority_applied"] = True
                        applied_any = True
                    elif prio_norm == "below_normal":
                        os.nice(5)
                        details["priority_applied"] = True
                        applied_any = True
                    else:
                        # "normal" = bez změny niceness (bere se jako aplikováno/no-op).
                        details["priority_applied"] = True
                        applied_any = True
            except Exception as e:
                details["priority_applied"] = False
                fully_applied = False
                warnings.append(f"Priorita se nepodařila nastavit: {e}")

    # RAM hard cap (Windows Job Object)
    if ram_limit_mb is not None:
        requested_any = True
        if os.name != "nt":
            warnings.append("RAM hard cap je implementován jen pro Windows.")
            details["ram_hard_cap_applied"] = False
            details["ram_hard_cap_error"] = "non_windows"
            fully_applied = False
            ram_mode = "soft"
        else:
            try:
                ok, hard_cap_error = _apply_windows_job_memory_limit(int(ram_limit_mb))
                if ok:
                    applied_any = True
                    details["ram_hard_cap_applied"] = True
                    details["ram_hard_cap_error"] = None
                    ram_mode = "hard"
                else:
                    details["ram_hard_cap_applied"] = False
                    details["ram_hard_cap_error"] = hard_cap_error
                    fully_applied = False
                    if hard_cap_error:
                        warnings.append(f"RAM hard cap se nepodařilo aplikovat: {hard_cap_error}")
                    else:
                        warnings.append("RAM hard cap se nepodařilo aplikovat.")
                    ram_mode = "soft"
            except Exception as e:
                details["ram_hard_cap_applied"] = False
                details["ram_hard_cap_error"] = str(e)
                fully_applied = False
                warnings.append(f"RAM hard cap selhal: {e}")
                ram_mode = "soft"

    details["any_applied"] = applied_any
    if not requested_any:
        warnings.append("Žádný cap nebyl požadován.")
        return False, warnings, ram_mode, details
    if not applied_any:
        warnings.append("Žádný cap nebyl aplikován.")
    return bool(fully_applied), warnings, ram_mode, details


def _windows_last_error_text(prefix: str) -> str:
    code = ctypes.get_last_error()
    if not code:
        return prefix
    try:
        msg = ctypes.FormatError(code).strip()
    except Exception:
        msg = "unknown error"
    return f"{prefix} (WinError {code}: {msg})"


def _apply_windows_job_memory_limit(ram_limit_mb: int) -> tuple[bool, str | None]:
    global _JOB_OBJECT_HANDLE
    if os.name != "nt":
        return False, "non_windows"
    limit_bytes = int(max(256, ram_limit_mb)) * 1024 * 1024

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    CreateJobObjectW = kernel32.CreateJobObjectW
    CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    CreateJobObjectW.restype = wintypes.HANDLE
    SetInformationJobObject = kernel32.SetInformationJobObject
    SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    SetInformationJobObject.restype = wintypes.BOOL
    AssignProcessToJobObject = kernel32.AssignProcessToJobObject
    AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    AssignProcessToJobObject.restype = wintypes.BOOL
    IsProcessInJob = kernel32.IsProcessInJob
    IsProcessInJob.argtypes = [wintypes.HANDLE, wintypes.HANDLE, ctypes.POINTER(wintypes.BOOL)]
    IsProcessInJob.restype = wintypes.BOOL
    GetCurrentProcess = kernel32.GetCurrentProcess
    GetCurrentProcess.restype = wintypes.HANDLE
    CloseHandle = kernel32.CloseHandle
    CloseHandle.argtypes = [wintypes.HANDLE]
    CloseHandle.restype = wintypes.BOOL

    class IO_COUNTERS(ctypes.Structure):
        _fields_ = [
            ("ReadOperationCount", ctypes.c_uint64),
            ("WriteOperationCount", ctypes.c_uint64),
            ("OtherOperationCount", ctypes.c_uint64),
            ("ReadTransferCount", ctypes.c_uint64),
            ("WriteTransferCount", ctypes.c_uint64),
            ("OtherTransferCount", ctypes.c_uint64),
        ]

    class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_int64),
            ("PerJobUserTimeLimit", ctypes.c_int64),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION),
            ("IoInfo", IO_COUNTERS),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    JOB_OBJECT_LIMIT_PROCESS_MEMORY = 0x00000100
    JOB_OBJECT_LIMIT_JOB_MEMORY = 0x00000200
    JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
    JobObjectExtendedLimitInformation = 9

    hjob = CreateJobObjectW(None, None)
    if not hjob:
        return False, _windows_last_error_text("CreateJobObjectW selhal")

    info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
    info.BasicLimitInformation.LimitFlags = (
        JOB_OBJECT_LIMIT_PROCESS_MEMORY
        | JOB_OBJECT_LIMIT_JOB_MEMORY
        | JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    )
    info.ProcessMemoryLimit = ctypes.c_size_t(limit_bytes)
    info.JobMemoryLimit = ctypes.c_size_t(limit_bytes)

    if not SetInformationJobObject(
        hjob,
        JobObjectExtendedLimitInformation,
        ctypes.byref(info),
        ctypes.sizeof(info),
    ):
        err = _windows_last_error_text("SetInformationJobObject selhal")
        CloseHandle(hjob)
        return False, err

    current = GetCurrentProcess()
    in_job_flag = False
    in_job_raw = wintypes.BOOL(False)
    if IsProcessInJob(current, None, ctypes.byref(in_job_raw)):
        in_job_flag = bool(in_job_raw.value)

    if not AssignProcessToJobObject(hjob, current):
        err = _windows_last_error_text("AssignProcessToJobObject selhal")
        if in_job_flag:
            err = f"{err}; proces už běží v jiném JobObjectu (nested job omezení)."
        CloseHandle(hjob)
        return False, err

    _JOB_OBJECT_HANDLE = hjob
    return True, None


class _TrialLoadController:
    def __init__(self, load_profile: str, cpu_target_pct: float | None, ram_target_pct: float | None):
        self.load_profile = (load_profile or "none").strip().lower()
        self.cpu_target_pct = _coerce_pct(cpu_target_pct)
        self.ram_target_pct = _coerce_pct(ram_target_pct)
        self.cpu_samples: list[float] = []
        self.ram_samples: list[float] = []
        self._proc: subprocess.Popen | None = None
        self._stop_evt = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def enabled(self) -> bool:
        return self.load_profile != "none" and (
            (self.cpu_target_pct or 0.0) > 0.0 or (self.ram_target_pct or 0.0) > 0.0
        )

    def _sample_once(self) -> None:
        if psutil is None:
            return
        try:
            self.cpu_samples.append(float(psutil.cpu_percent(interval=None)))
        except Exception:
            pass
        try:
            self.ram_samples.append(float(psutil.virtual_memory().percent))
        except Exception:
            pass

    def _sample_loop(self) -> None:
        while not self._stop_evt.wait(1.0):
            self._sample_once()

    def start(self) -> None:
        if not self.enabled or psutil is None:
            return
        load_script = ROOT / "scripts" / "load_generator.py"
        if not load_script.exists():
            return
        # Priming call: první cpu_percent bývá nespolehlivý.
        try:
            psutil.cpu_percent(interval=None)
        except Exception:
            pass
        args = [
            sys.executable,
            str(load_script),
            "--cpu-target-pct", str(self.cpu_target_pct or 0.0),
            "--ram-target-pct", str(self.ram_target_pct or 0.0),
        ]
        env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}
        try:
            self._proc = subprocess.Popen(
                args,
                cwd=str(ROOT),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                env=env,
            )
        except Exception:
            self._proc = None
            return
        self._thread = threading.Thread(target=self._sample_loop, daemon=True)
        self._thread.start()
        time.sleep(0.6)
        self._sample_once()

    def stop(self) -> dict:
        self._stop_evt.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
        self._sample_once()

        if self._proc is not None:
            try:
                if self._proc.poll() is None:
                    self._proc.terminate()
                    self._proc.wait(timeout=3.0)
            except Exception:
                try:
                    if self._proc.poll() is None:
                        self._proc.kill()
                except Exception:
                    pass

        cpu_avg = _avg(self.cpu_samples)
        cpu_p95 = _percentile(self.cpu_samples, 95)
        ram_avg = _avg(self.ram_samples)
        ram_p95 = _percentile(self.ram_samples, 95)
        samples = max(len(self.cpu_samples), len(self.ram_samples))

        cpu_ok = None
        ram_ok = None
        if self.cpu_target_pct is not None and cpu_avg is not None:
            cpu_ok = abs(float(cpu_avg) - float(self.cpu_target_pct)) <= 10.0
        if self.ram_target_pct is not None and ram_avg is not None:
            ram_ok = abs(float(ram_avg) - float(self.ram_target_pct)) <= 8.0

        checks = [x for x in [cpu_ok, ram_ok] if x is not None]
        control_ok = all(checks) if checks else None

        return {
            "load_cpu_actual_avg_pct": cpu_avg,
            "load_cpu_actual_p95_pct": cpu_p95,
            "load_ram_actual_avg_pct": ram_avg,
            "load_ram_actual_p95_pct": ram_p95,
            "load_samples": samples if samples > 0 else None,
            "load_control_ok": control_ok,
        }


def _validate_beam_sizes(
    model_beam_pairs: set[tuple[str, int]],
    audio_wavs: dict[str, Path],
    model_store_root: Path,
    status_file: Path,
    run_streaming_benchmark,
    StreamingRunConfig,
) -> set[tuple[str, int]]:
    """
    Před spuštěním trialů ověří každou unikátní (model_id, beam_size) kombinaci na 5s klipu.
    Vrátí množinu dvojic (model_id, beam_size), které prošly validací.

    Testuje per-model: beam_size=5 může selhat na large_v3 ale fungovat na small —
    proto se nevylučuje beam_size globálně, ale konkrétní kombinace.
    Zabrání stovkám timeoutů při degenerovaném chování whisper-cli.
    """
    import wave
    import tempfile
    from collections import defaultdict

    validated: set[tuple[str, int]] = set()

    # Seskup beam_sizes podle model_id — testujeme každý model zvlášť
    by_model: dict[str, list[int]] = defaultdict(list)
    for m_id, bs in model_beam_pairs:
        by_model[m_id].append(bs)

    for m_id, beam_list in by_model.items():
        for bs in sorted(beam_list):
            _set_progress(status_file, f"Validace {m_id} beam_size={bs} (5s test)...")
            all_ok = True
            for video_id, wav_path in list(audio_wavs.items())[:2]:  # max 2 videa
                tmp = Path(tempfile.mktemp(suffix=".wav"))
                try:
                    with wave.open(str(wav_path), "rb") as wf:
                        sr = wf.getframerate()
                        raw = wf.readframes(min(5 * sr, wf.getnframes()))
                        sw = wf.getsampwidth()
                        nc = wf.getnchannels()
                    with wave.open(str(tmp), "wb") as wf_out:
                        wf_out.setnchannels(nc)
                        wf_out.setsampwidth(sw)
                        wf_out.setframerate(sr)
                        wf_out.writeframes(raw)

                    from packages.ingest.source_resolver import SourceEntry
                    src = SourceEntry(
                        source_id=f"val_{video_id}",
                        label=video_id,
                        origin_type="youtube",
                        value=f"https://www.youtube.com/watch?v={video_id}",
                        exists=True,
                        canonical_url=f"https://www.youtube.com/watch?v={video_id}",
                        video_id=video_id,
                    )
                    cfg = StreamingRunConfig(
                        model_id=m_id,
                        model_params={"beam_size": bs, "best_of": 1, "threads": 4, "no_fallback": True},
                        model_store_root=str(model_store_root),
                        output_dir=str(tmp.parent),
                        sample_seconds=5,
                        chunk_seconds=5,
                        progress_callback=lambda msg, _m=m_id, _b=bs, _v=video_id: _set_progress(
                            status_file,
                            f"Validace {_m} beam_size={_b} ({_v}): {msg}",
                        ),
                        source_wav_path=str(tmp),
                    )
                    run_streaming_benchmark(source=src, audio_generator=None, config=cfg)
                except Exception as e:
                    err = str(e)
                    if "timeout" in err.lower() or "zabit" in err.lower():
                        _set_progress(status_file, f"WARN: {m_id} beam_size={bs} TIMEOUT na {video_id} — vynecháno")
                        all_ok = False
                        break
                    # Jiná chyba (model path atd.) — nevylučuj, může být jen setup issue
                finally:
                    # Maž input WAV i whisper output soubory (json, txt) aby nezůstaly v temp
                    for _cleanup in [
                        tmp,
                        tmp.parent / f"val_{video_id}_whisper.json",
                        tmp.parent / f"val_{video_id}_whisper.txt",
                    ]:
                        try:
                            _cleanup.unlink(missing_ok=True)
                        except Exception:
                            pass

            if all_ok:
                validated.add((m_id, bs))
                _set_progress(status_file, f"{m_id} beam_size={bs} OK")
            else:
                _set_progress(status_file, f"{m_id} beam_size={bs} FAIL — trialy s touto kombinací budou přeskočeny")

    return validated


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--job-id", required=True)
    parser.add_argument("--tuning-root", required=True)
    args = parser.parse_args()

    global _event_job_dir
    job_dir = Path(args.tuning_root) / args.job_id
    config_file = job_dir / "config.json"
    status_file = job_dir / "status.json"
    _event_job_dir = job_dir

    try:
        ensure_event_store(job_dir)
        _emit_event("worker_started", {"job_id": args.job_id})
    except Exception as e:
        print(f"WARN: event-store init failed: {e}", file=sys.stderr)

    if not config_file.exists():
        print(f"ERROR: config not found: {config_file}", file=sys.stderr)
        return 2

    require_resource_metrics = os.environ.get("ASTT_REQUIRE_RESOURCE_METRICS", "0") == "1"
    if require_resource_metrics and psutil is None:
        _update_status(
            status_file,
            {
                "status": "failed",
                "error": "Chybi psutil (povinne pro RAM/CPU metriky). Nainstaluj do .venv: .venv\\Scripts\\pip install psutil",
            },
        )
        print("ERROR: psutil missing while ASTT_REQUIRE_RESOURCE_METRICS=1", file=sys.stderr)
        return 1

    config = json.loads(config_file.read_text(encoding="utf-8"))
    trials: list[dict] = config["trials"]
    model_id: str = config["model_id"]          # fallback pro old-style joby (single model)
    strategy: str = str(config.get("strategy") or "grid").strip().lower()
    input_mode: str = str(config.get("input_mode") or "replay").strip().lower()
    video_ids: list[str] = config["video_ids"]
    sample_seconds: int = config["sample_seconds"]
    clip_seed: int | None = config.get("clip_seed")
    clip_start_seconds: int | None = config.get("clip_start_seconds")
    evaluation_mode: str = config.get("evaluation_mode", "heuristic")
    hardware_profile: str | None = config.get("hardware_profile")
    hardware_note: str | None = config.get("hardware_note")
    config_hw_info: dict = config.get("hardware_info") or {}
    constraints_profile = str(config.get("constraints_profile") or "none").strip().lower()
    constraints_cpu_cores = config.get("constraints_cpu_cores")
    constraints_ram_limit_mb = config.get("constraints_ram_limit_mb")
    constraints_priority = str(config.get("constraints_priority") or "below_normal").strip().lower()
    load_profile = str(config.get("load_profile") or "none").strip().lower()
    load_cpu_target_pct = _coerce_pct(config.get("load_cpu_target_pct"))
    load_ram_target_pct = _coerce_pct(config.get("load_ram_target_pct"))
    validate_beam_preflight = bool(config.get("validate_beam_preflight", True))
    mic_chunk_seconds = max(
        0.05,
        float(
            config.get("mic_chunk_seconds")
            if config.get("mic_chunk_seconds") is not None
            else os.environ.get("ASTT_REAL_MIC_CHUNK_SECONDS", "0.20")
        ),
    )
    mic_prepare_seconds = max(
        0,
        int(
            config.get("mic_prepare_seconds")
            if config.get("mic_prepare_seconds") is not None
            else os.environ.get("ASTT_REAL_MIC_PREPARE_SECONDS", "4")
        ),
    )
    mic_device_raw = config.get("mic_device")
    mic_device = mic_device_raw if isinstance(mic_device_raw, (int, str)) else None
    if load_profile == "none":
        load_cpu_target_pct = None
        load_ram_target_pct = None
    if evaluation_mode == "heuristic+llm":
        print("[tuning_worker] evaluation_mode=heuristic+llm — LLM hodnocení zatím není implementováno, používám heuristiku", flush=True)
    subtitles_root = Path(config["subtitles_root"])
    model_store_root = Path(config["model_store_root"])
    audio_cache_dir: Path | None = Path(config["audio_cache_dir"]) if config.get("audio_cache_dir") else None
    items_json = subtitles_root.parent / "items.json"
    whisper_server_cache_enabled = os.environ.get("ASTT_WHISPER_SERVER_CACHE", "0") == "1"
    _emit_event(
        "job_loaded",
        {
            "job_id": args.job_id,
            "input_mode": input_mode,
            "strategy": strategy,
            "trials_planned": len(trials),
            "video_count": len(video_ids),
            "load_profile": load_profile,
            "constraints_profile": constraints_profile,
        },
    )

    if input_mode not in {"replay", "real_mic"}:
        _update_status(status_file, {"status": "failed", "error": f"Neplatný input_mode: {input_mode}"})
        return 1

    # Pre-compute clip_start per video — stejný offset pro všechny trialy (férovné srovnání)
    if clip_start_seconds is not None:
        clip_starts = {vid: float(clip_start_seconds) for vid in video_ids}
    else:
        clip_starts = _compute_clip_starts(video_ids, sample_seconds, clip_seed, items_json)

    if not video_ids:
        _update_status(status_file, {"status": "failed", "error": "Žádná video_ids v konfiguraci"})
        return 1

    worker_hw_info = _detect_worker_hardware_info()
    merged_hw_info = {**config_hw_info, **{k: v for k, v in worker_hw_info.items() if v is not None}}
    constraints_applied, constraints_warnings, constraints_ram_mode, constraints_detail = _apply_process_constraints(
        profile=constraints_profile,
        cpu_cores=(int(constraints_cpu_cores) if isinstance(constraints_cpu_cores, int) else None),
        ram_limit_mb=(int(constraints_ram_limit_mb) if isinstance(constraints_ram_limit_mb, int) else None),
        priority=constraints_priority,
    )
    constraints_cpu_applied = (
        bool(constraints_detail.get("cpu_applied"))
        if isinstance(constraints_detail.get("cpu_applied"), bool)
        else None
    )
    constraints_priority_applied = (
        bool(constraints_detail.get("priority_applied"))
        if isinstance(constraints_detail.get("priority_applied"), bool)
        else None
    )
    constraints_ram_hard_cap_applied = (
        bool(constraints_detail.get("ram_hard_cap_applied"))
        if isinstance(constraints_detail.get("ram_hard_cap_applied"), bool)
        else None
    )
    constraints_ram_hard_cap_error = (
        str(constraints_detail.get("ram_hard_cap_error"))
        if constraints_detail.get("ram_hard_cap_error")
        else None
    )
    constraints_ram_soft_limit_mb = (
        int(constraints_ram_limit_mb)
        if constraints_ram_mode == "soft" and isinstance(constraints_ram_limit_mb, int)
        else None
    )
    if constraints_ram_soft_limit_mb is not None:
        constraints_warnings = [
            *constraints_warnings,
            f"RAM soft guard aktivní: limit {constraints_ram_soft_limit_mb} MB (trial FAIL při překročení).",
        ]
    _update_status(
        status_file,
        {
            "status": "running",
            "hardware_profile": hardware_profile,
            "hardware_note": hardware_note,
            "hardware_info": merged_hw_info,
            "constraints_profile": constraints_profile,
            "constraints_cpu_cores": constraints_cpu_cores,
            "constraints_ram_limit_mb": constraints_ram_limit_mb,
            "constraints_priority": constraints_priority,
            "constraints_applied": constraints_applied,
            "constraints_warnings": constraints_warnings,
            "constraints_ram_mode": constraints_ram_mode,
            "constraints_cpu_applied": constraints_cpu_applied,
            "constraints_priority_applied": constraints_priority_applied,
            "constraints_ram_hard_cap_applied": constraints_ram_hard_cap_applied,
            "constraints_ram_hard_cap_error": constraints_ram_hard_cap_error,
            "load_profile": load_profile,
            "load_cpu_target_pct": load_cpu_target_pct,
            "load_ram_target_pct": load_ram_target_pct,
        },
    )
    if whisper_server_cache_enabled:
        _set_progress(status_file, "Whisper model-cache: ASTT_WHISPER_SERVER_CACHE=1 (preferuji persistentní whisper-server).")
    if load_profile != "none":
        _set_progress(
            status_file,
            f"Load control: {load_profile} (CPU target {load_cpu_target_pct if load_cpu_target_pct is not None else '-'}% | RAM target {load_ram_target_pct if load_ram_target_pct is not None else '-'}%).",
        )
    if constraints_profile != "none":
        _set_progress(
            status_file,
            f"Process cap: {constraints_profile} (cores {constraints_cpu_cores if constraints_cpu_cores is not None else '-'} | RAM {constraints_ram_limit_mb if constraints_ram_limit_mb is not None else '-'} MB | prio {constraints_priority})"
            + (" ✓" if constraints_applied else " ⚠ neaplikováno")
            + (f" | RAM mode={constraints_ram_mode}" if constraints_ram_mode != "none" else ""),
        )

    try:
        from packages.benchmarks.runners.streaming_runner import run_streaming_benchmark, StreamingRunConfig
        from packages.benchmarks.metrics.text_metrics import (
            word_error_rate, char_error_rate,
            word_error_rate_normalized, word_error_rate_soft, match_error_rate, word_information_lost,
            word_diff,
        )
        from packages.benchmarks.ground_truth.vtt_reference import extract_vtt_clip_text

        audio_wavs: dict[str, Path] = {}
        if input_mode == "replay":
            from packages.ingest.youtube.stream_pipe import stream_youtube_audio

            # Pre-download audio pro každé video jednou — žádné opakované YouTube požadavky
            audio_ready: list[str] = []
            for video_id in video_ids:
                try:
                    wav = _predownload_audio(
                        video_id=video_id,
                        clip_start_s=clip_starts.get(video_id, 0.0),
                        sample_seconds=sample_seconds,
                        job_dir=job_dir,
                        stream_youtube_audio=stream_youtube_audio,
                        progress_cb=lambda msg: _set_progress(status_file, msg),
                        audio_cache_dir=audio_cache_dir,
                    )
                    audio_wavs[video_id] = wav
                    audio_ready.append(video_id)
                    _update_status(status_file, {"audio_ready": audio_ready})
                except Exception as e:
                    _set_progress(status_file, f"⚠ Nelze stáhnout audio {video_id}: {e}")

            if not audio_wavs:
                _update_status(status_file, {"status": "failed", "error": "Žádné audio se nepodařilo stáhnout"})
                return 1
        else:
            _update_status(status_file, {"audio_ready": list(video_ids)})
            _set_progress(
                status_file,
                "Real mic režim: přeskakuji audio predownload/YouTube fetch, trialy se měří přímo z mikrofonu.",
            )

        # Validace beam_size hodnot před spuštěním trialů — zabrání stovkám timeoutů
        # Testujeme per-model: (model_id, beam_size) — beam_size=5 může selhat jen na large_v3, ne na small
        model_beam_pairs: set[tuple[str, int]] = set()
        for t in trials:
            t_model_id = str(t.get("_model_id", model_id))
            bs = t.get("beam_size")
            if bs is not None:
                model_beam_pairs.add((t_model_id, int(bs)))

        if input_mode != "replay":
            validated_pairs = None
            if model_beam_pairs:
                _set_progress(status_file, "Real mic: pre-flight validace beam_size se přeskočí (validní jen pro replay).")
        elif model_beam_pairs and validate_beam_preflight:
            validated_pairs = _validate_beam_sizes(
                model_beam_pairs=model_beam_pairs,
                audio_wavs=audio_wavs,
                model_store_root=model_store_root,
                status_file=status_file,
                run_streaming_benchmark=run_streaming_benchmark,
                StreamingRunConfig=StreamingRunConfig,
            )
        elif model_beam_pairs:
            validated_pairs = None
            _set_progress(status_file, "Pre-flight validace beam_size je vypnuta — spouštím trialy přímo.")
        else:
            validated_pairs = None  # žádné beam_size v trialech — validaci přeskoč

        # Runtime cache per (model + params + video + clip) — eliminuje redundantní běhy mezi trialy.
        trial_video_cache: dict[tuple, dict] = {}
        worker_proc = psutil.Process() if psutil is not None else None
        if psutil is None:
            _set_progress(status_file, "WARN: psutil není dostupný — RAM/RSS metriky budou prázdné.")
        repeat_top_k = max(0, int(config.get("repeat_top_k", 0) or 0))
        repeat_runs = max(1, int(config.get("repeat_runs", 1) or 1))
        smart_mode = strategy == "smart"
        if smart_mode and (repeat_top_k > 0 or repeat_runs > 1):
            _set_progress(status_file, "Smart režim: reproducibility repeat_top_k/repeat_runs se použijí až na finální shortlist (nyní vypnuto).")
            repeat_top_k = 0
            repeat_runs = 1

        all_results: list[dict] = []
        next_trial_idx = 0
        execution_queue: list[dict] = []
        smart_state: dict | None = None
        if smart_mode:
            candidate_by_key: dict[str, dict] = {}
            for trial_params_raw in trials:
                tr = dict(trial_params_raw)
                cand_model_id = str(tr.get("_model_id", model_id))
                cand_key = _smart_candidate_key(model_id=cand_model_id, params_raw=tr)
                if cand_key in candidate_by_key:
                    continue
                candidate_by_key[cand_key] = {
                    "trial_params_raw": tr,
                    "model_id": cand_model_id,
                }

            candidate_keys = list(candidate_by_key.keys())
            if not candidate_keys:
                _update_status(status_file, {"status": "failed", "error": "Smart režim: žádní kandidáti po validaci prostoru parametrů."})
                return 1

            rounds: list[dict] = []
            rounds.append({"round_idx": 1, "label": "gate", "video_ids": video_ids[:1], "keep_ratio": 0.5})
            if len(video_ids) >= 2 and len(candidate_keys) > 1:
                rounds.append({"round_idx": 2, "label": "search", "video_ids": video_ids[:2], "keep_ratio": 0.5})
            rounds.append({"round_idx": len(rounds) + 1, "label": "confirm", "video_ids": list(video_ids), "keep_ratio": 0.5})

            dedup_rounds: list[dict] = []
            for rd in rounds:
                vids = tuple(rd.get("video_ids") or [])
                if dedup_rounds and tuple(dedup_rounds[-1].get("video_ids") or []) == vids:
                    dedup_rounds[-1]["label"] = rd.get("label") or dedup_rounds[-1].get("label")
                    continue
                dedup_rounds.append(rd)
            rounds = dedup_rounds
            total_rounds = len(rounds)
            first_round = rounds[0]
            prefilter = total_rounds > 1

            for cand_key in candidate_keys:
                payload = candidate_by_key[cand_key]
                execution_queue.append({
                    "trial_idx": next_trial_idx,
                    "trial_params_raw": payload["trial_params_raw"],
                    "is_repeat": prefilter,
                    "repeat_of_trial_idx": None,
                    "repeat_no": 1,
                    "allow_cache": input_mode == "replay",
                    "smart_round": 1,
                    "smart_total_rounds": total_rounds,
                    "smart_stage": first_round.get("label"),
                    "smart_candidate_key": cand_key,
                    "trial_video_ids": list(first_round.get("video_ids") or video_ids),
                })
                next_trial_idx += 1

            smart_state = {
                "enabled": True,
                "rounds": rounds,
                "current_round": 1,
                "total_rounds": total_rounds,
                "remaining_in_round": len(candidate_keys),
                "candidate_by_key": candidate_by_key,
                "results_by_round": {1: []},
            }
            _set_progress(
                status_file,
                f"Smart search: {len(candidate_keys)} kandidátů, {total_rounds} kola (gate/search/confirm).",
            )
            _update_status(status_file, {"total_trials": len(execution_queue)})
        else:
            for trial_params_raw in trials:
                execution_queue.append({
                    "trial_idx": next_trial_idx,
                    "trial_params_raw": trial_params_raw,
                    "is_repeat": False,
                    "repeat_of_trial_idx": None,
                    "repeat_no": 1,
                    "allow_cache": input_mode == "replay",
                })
                next_trial_idx += 1

        repeats_enqueued = False
        if (not smart_mode) and repeat_top_k > 0 and repeat_runs > 1:
            _update_status(status_file, {
                "total_trials": len(trials) + (repeat_top_k * (repeat_runs - 1)),
            })

        queue_pos = 0
        def _enqueue_repeats_if_ready() -> None:
            nonlocal repeats_enqueued, next_trial_idx
            if repeats_enqueued:
                return
            if queue_pos != len(trials):
                return
            if repeat_top_k <= 0 or repeat_runs <= 1:
                repeats_enqueued = True
                return

            repeats_enqueued = True
            seed_trials = _select_repeat_seed_trials(all_results, repeat_top_k)
            if seed_trials:
                _set_progress(
                    status_file,
                    f"Reproducibility: opakuji top {len(seed_trials)} kandidáty ({repeat_runs} běhy na kandidáta).",
                )
            for seed in seed_trials:
                seed_idx = int(seed["trial_idx"])
                seed_params = dict(seed.get("params") or {})
                seed_chunk = int(seed_params.pop("chunk_seconds", seed.get("chunk_seconds", 30)))
                seed_model = str(seed.get("model_id") or model_id)
                for rep_no in range(2, repeat_runs + 1):
                    execution_queue.append({
                        "trial_idx": next_trial_idx,
                        "trial_params_raw": {**seed_params, "_chunk_seconds": seed_chunk, "_model_id": seed_model},
                        "is_repeat": True,
                        "repeat_of_trial_idx": seed_idx,
                        "repeat_no": rep_no,
                        "allow_cache": False,  # repeat musí znovu proběhnout, ne jen cache hit
                    })
                    next_trial_idx += 1

            # Uprav total_trials na skutečný počet (může být < plán, když je málo validních seedů).
            _update_status(status_file, {"total_trials": len(execution_queue)})

        def _enqueue_smart_next_round_if_ready() -> None:
            nonlocal next_trial_idx
            if not smart_state or not smart_state.get("enabled"):
                return

            remaining = int(smart_state.get("remaining_in_round") or 0)
            if remaining > 0:
                return

            current_round = int(smart_state.get("current_round") or 1)
            total_rounds = int(smart_state.get("total_rounds") or 1)
            if current_round >= total_rounds:
                return

            rounds: list[dict] = list(smart_state.get("rounds") or [])
            results_by_round: dict = smart_state.get("results_by_round") or {}
            candidate_by_key: dict = smart_state.get("candidate_by_key") or {}
            round_results = list(results_by_round.get(current_round) or [])
            if not round_results:
                _set_progress(status_file, f"Smart round {current_round}/{total_rounds}: žádná validní data, job končí.")
                return

            valid = [
                r for r in round_results
                if not r.get("error")
                and isinstance(r.get("wer"), (int, float))
                and isinstance(r.get("rtf"), (int, float))
                and isinstance(r.get("_smart_candidate_key"), str)
            ]
            ranked = sorted(valid, key=_smart_score_tuple)
            if not ranked:
                fallback = [r for r in round_results if isinstance(r.get("_smart_candidate_key"), str)]
                ranked = sorted(
                    fallback,
                    key=lambda r: (
                        0.0 if isinstance(r.get("rtf"), (int, float)) else 1.0,
                        float(r.get("rtf")) if isinstance(r.get("rtf"), (int, float)) else float("inf"),
                        float(r.get("wer")) if isinstance(r.get("wer"), (int, float)) else float("inf"),
                    ),
                )
            if not ranked:
                _set_progress(status_file, f"Smart round {current_round}/{total_rounds}: bez přeživších kandidátů.")
                return

            next_round = current_round + 1
            next_def = rounds[next_round - 1]
            keep_ratio = float(next_def.get("keep_ratio") or 1.0)
            keep_n = max(1, int(math.ceil(len(ranked) * keep_ratio)))
            survivors = ranked[:keep_n]
            survivor_keys = [str(r.get("_smart_candidate_key")) for r in survivors if r.get("_smart_candidate_key")]
            stage = str(next_def.get("label") or f"round_{next_round}")
            trial_video_ids = list(next_def.get("video_ids") or video_ids)
            final_round = next_round == total_rounds

            _set_progress(
                status_file,
                f"Smart round {current_round}/{total_rounds} done: postupuje {len(survivor_keys)}/{len(ranked)} → {stage}.",
            )

            for cand_key in survivor_keys:
                payload = candidate_by_key.get(cand_key)
                if not payload:
                    continue
                execution_queue.append({
                    "trial_idx": next_trial_idx,
                    "trial_params_raw": dict(payload.get("trial_params_raw") or {}),
                    "is_repeat": not final_round,
                    "repeat_of_trial_idx": None,
                    "repeat_no": 1,
                    "allow_cache": input_mode == "replay",
                    "smart_round": next_round,
                    "smart_total_rounds": total_rounds,
                    "smart_stage": stage,
                    "smart_candidate_key": cand_key,
                    "trial_video_ids": trial_video_ids,
                })
                next_trial_idx += 1

            smart_state["current_round"] = next_round
            smart_state["remaining_in_round"] = len(survivor_keys)
            smart_state["results_by_round"][next_round] = []
            _update_status(status_file, {"total_trials": len(execution_queue)})

        def _on_trial_finished(result_item: dict) -> None:
            all_results.append(result_item)
            if smart_state and smart_state.get("enabled"):
                round_idx = int(result_item.get("smart_round") or smart_state.get("current_round") or 1)
                rb = smart_state.get("results_by_round")
                if isinstance(rb, dict):
                    rb.setdefault(round_idx, []).append(result_item)
                remaining = int(smart_state.get("remaining_in_round") or 0)
                smart_state["remaining_in_round"] = max(0, remaining - 1)
                _enqueue_smart_next_round_if_ready()
            else:
                _enqueue_repeats_if_ready()

        while queue_pos < len(execution_queue):
            run_item = execution_queue[queue_pos]
            queue_pos += 1
            trial_idx = int(run_item["trial_idx"])
            trial_params_raw = dict(run_item["trial_params_raw"])
            is_repeat = bool(run_item.get("is_repeat"))
            repeat_of_trial_idx = run_item.get("repeat_of_trial_idx")
            repeat_no = int(run_item.get("repeat_no", 1) or 1)
            allow_cache = bool(run_item.get("allow_cache", True))
            smart_round = int(run_item.get("smart_round", 0) or 0)
            smart_total_rounds = int(run_item.get("smart_total_rounds", 0) or 0)
            smart_stage = str(run_item.get("smart_stage") or "")
            smart_candidate_key = str(run_item.get("smart_candidate_key") or "") or None
            trial_video_ids = list(run_item.get("trial_video_ids") or video_ids)
            trial_pos = queue_pos
            # Kontrola cancel flagu
            if (job_dir / "cancel").exists():
                _update_status(status_file, {"status": "cancelled"})
                _emit_event("job_cancelled", {"job_id": args.job_id, "completed_trials": queue_pos - 1})
                print("Job cancelled.", flush=True)
                return 0

            # Extrahuj chunk_seconds a model_id PŘED smyčkou přes videa (pop mutuje dict)
            trial_params = dict(trial_params_raw)
            chunk_seconds = int(trial_params.pop("_chunk_seconds", trial_params.pop("chunk_seconds", 30)))
            trial_model_id = str(trial_params.pop("_model_id", model_id))  # per-trial model, fallback na job-level

            # Validace: (model_id, beam_size) musí projít pre-flight testem
            beam_size = trial_params.get("beam_size", 5)
            best_of = trial_params.get("best_of", 1)
            if (validated_pairs is not None
                    and isinstance(beam_size, int)
                    and (trial_model_id, beam_size) not in validated_pairs):
                _set_progress(status_file, f"Trial {trial_pos}/{len(execution_queue)}: SKIP — {trial_model_id} beam_size={beam_size} selhalo při validaci (timeout)")
                skip_result = {
                    "trial_idx": trial_idx,
                    "model_id": trial_model_id,
                    "params": {**trial_params, "chunk_seconds": chunk_seconds},
                    "chunk_seconds": chunk_seconds,
                    "wer": None, "cer": None, "wer_normalized": None, "wer_soft": None, "wer_llm": None, "mer": None, "wil": None,
                    "rtf": None, "latency_ms": None, "source_metrics": [],
                    "ram_mb": None, "ram_peak_mb": None,
                    "worker_rss_before_mb": None, "worker_rss_after_mb": None, "worker_rss_peak_mb": None,
                    **_load_result_fields(
                        load_profile=load_profile,
                        load_cpu_target_pct=load_cpu_target_pct,
                        load_ram_target_pct=load_ram_target_pct,
                    ),
                    **_constraints_result_fields(
                        constraints_profile=constraints_profile,
                        constraints_cpu_cores=(int(constraints_cpu_cores) if isinstance(constraints_cpu_cores, int) else None),
                        constraints_ram_limit_mb=(int(constraints_ram_limit_mb) if isinstance(constraints_ram_limit_mb, int) else None),
                        constraints_priority=constraints_priority,
                        constraints_applied=constraints_applied,
                        constraints_warnings=constraints_warnings,
                        constraints_ram_mode=constraints_ram_mode,
                        constraints_cpu_applied=constraints_cpu_applied,
                        constraints_priority_applied=constraints_priority_applied,
                        constraints_ram_hard_cap_applied=constraints_ram_hard_cap_applied,
                        constraints_ram_hard_cap_error=constraints_ram_hard_cap_error,
                    ),
                    "transcript": None, "reference_text": None, "word_diff": None,
                    "chunk_metrics": None, "word_count": 0,
                    "error": f"Přeskočeno: {trial_model_id} beam_size={beam_size} selhalo při validaci (timeout)",
                    "is_pareto": False, "rtf_viable": False, "perceived_delay_s": None,
                    "is_repeat": is_repeat,
                    "repeat_of_trial_idx": repeat_of_trial_idx,
                    "repeat_no": repeat_no,
                    "repeat_group_key": f"trial_{repeat_of_trial_idx if repeat_of_trial_idx is not None else trial_idx}",
                    "smart_round": smart_round if smart_round > 0 else None,
                    "smart_stage": smart_stage or None,
                    "_smart_candidate_key": smart_candidate_key,
                    "trial_finished_at": _now(),
                }
                _append_result(status_file, skip_result)
                _on_trial_finished(skip_result)
                continue

            # Validace: best_of nesmí být větší než beam_size
            if isinstance(best_of, int) and isinstance(beam_size, int) and best_of > beam_size:
                _set_progress(status_file, f"Trial {trial_pos}/{len(execution_queue)}: SKIP — best_of={best_of} > beam_size={beam_size}")
                skip_result = {
                    "trial_idx": trial_idx,
                    "model_id": trial_model_id,
                    "params": {**trial_params, "chunk_seconds": chunk_seconds},
                    "chunk_seconds": chunk_seconds,
                    "wer": None, "cer": None, "wer_normalized": None, "wer_soft": None, "wer_llm": None, "mer": None, "wil": None,
                    "rtf": None, "latency_ms": None, "source_metrics": [],
                    "ram_mb": None, "ram_peak_mb": None,
                    "worker_rss_before_mb": None, "worker_rss_after_mb": None, "worker_rss_peak_mb": None,
                    **_load_result_fields(
                        load_profile=load_profile,
                        load_cpu_target_pct=load_cpu_target_pct,
                        load_ram_target_pct=load_ram_target_pct,
                    ),
                    **_constraints_result_fields(
                        constraints_profile=constraints_profile,
                        constraints_cpu_cores=(int(constraints_cpu_cores) if isinstance(constraints_cpu_cores, int) else None),
                        constraints_ram_limit_mb=(int(constraints_ram_limit_mb) if isinstance(constraints_ram_limit_mb, int) else None),
                        constraints_priority=constraints_priority,
                        constraints_applied=constraints_applied,
                        constraints_warnings=constraints_warnings,
                        constraints_ram_mode=constraints_ram_mode,
                        constraints_cpu_applied=constraints_cpu_applied,
                        constraints_priority_applied=constraints_priority_applied,
                        constraints_ram_hard_cap_applied=constraints_ram_hard_cap_applied,
                        constraints_ram_hard_cap_error=constraints_ram_hard_cap_error,
                    ),
                    "transcript": None, "reference_text": None, "word_diff": None,
                    "chunk_metrics": None, "word_count": 0,
                    "error": f"Přeskočeno: best_of={best_of} > beam_size={beam_size}",
                    "is_pareto": False, "rtf_viable": False, "perceived_delay_s": None,
                    "is_repeat": is_repeat,
                    "repeat_of_trial_idx": repeat_of_trial_idx,
                    "repeat_no": repeat_no,
                    "repeat_group_key": f"trial_{repeat_of_trial_idx if repeat_of_trial_idx is not None else trial_idx}",
                    "smart_round": smart_round if smart_round > 0 else None,
                    "smart_stage": smart_stage or None,
                    "_smart_candidate_key": smart_candidate_key,
                    "trial_finished_at": _now(),
                }
                _append_result(status_file, skip_result)
                _on_trial_finished(skip_result)
                continue

            param_str = ", ".join(f"{k}={v}" for k, v in trial_params.items() if k != "initial_prompt")
            if trial_params.get("initial_prompt"):
                param_str += f", prompt='{str(trial_params['initial_prompt'])[:30]}...'"
            repeat_suffix = (
                f" | repeat {repeat_no}/{repeat_runs} of #{repeat_of_trial_idx}"
                if is_repeat and repeat_of_trial_idx is not None
                else ""
            )
            smart_suffix = (
                f" | smart {smart_round}/{smart_total_rounds} {smart_stage}"
                if smart_round > 0 and smart_total_rounds > 0
                else ""
            )
            _set_progress(
                status_file,
                f"Trial {trial_pos}/{len(execution_queue)} [{trial_model_id}]: chunk={chunk_seconds}s | {param_str}{repeat_suffix}{smart_suffix}"
            )

            source_metrics: list[dict] = []
            trial_error: str | None = None
            worker_rss_before_mb = _get_rss_mb(worker_proc)
            worker_rss_peak_mb = worker_rss_before_mb
            load_controller = _TrialLoadController(
                load_profile=load_profile,
                cpu_target_pct=load_cpu_target_pct,
                ram_target_pct=load_ram_target_pct,
            )
            load_controller.start()
            load_summary: dict | None = None
            try:
                for v_idx, video_id in enumerate(trial_video_ids):
                    if input_mode == "replay":
                        if video_id not in audio_wavs:
                            source_metrics.append({"video_id": video_id, "error": "Audio nebylo staženo"})
                            continue
                        cache_key = _trial_cache_key(
                            model_id=trial_model_id,
                            model_params=trial_params,
                            video_id=video_id,
                            sample_seconds=sample_seconds,
                            chunk_seconds=chunk_seconds,
                            clip_start_s=clip_starts.get(video_id, 0.0),
                            audio_wav=audio_wavs[video_id],
                        )
                        if allow_cache and cache_key in trial_video_cache:
                            vm = copy.deepcopy(trial_video_cache[cache_key])
                            vm["cached"] = True
                            soft_ram_err = _soft_ram_violation(
                                limit_mb=constraints_ram_soft_limit_mb,
                                measured_ram_mb=(float(vm.get("ram_mb")) if isinstance(vm.get("ram_mb"), (int, float)) else None),
                            )
                            if soft_ram_err:
                                vm["error"] = soft_ram_err
                                trial_error = soft_ram_err
                            source_metrics.append(vm)
                            _set_progress(
                                status_file,
                                f"Trial {trial_pos}/{len(execution_queue)}: video {v_idx+1}/{len(trial_video_ids)} ({video_id}) ♻ cache hit "
                                f"WER={vm.get('wer')} RTF={vm.get('rtf')}"
                            )
                            rss_now = _get_rss_mb(worker_proc)
                            if rss_now is not None:
                                worker_rss_peak_mb = max(v for v in [worker_rss_peak_mb, rss_now] if v is not None)
                            if soft_ram_err:
                                break
                            continue

                        _set_progress(status_file, f"Trial {trial_pos}/{len(execution_queue)}: video {v_idx+1}/{len(trial_video_ids)} ({video_id}) ▶ přepisuji...")
                        try:
                            vm = _run_one_video(
                                video_id=video_id,
                                model_id=trial_model_id,
                                model_params=dict(trial_params),  # kopie — nesmí se mutovat
                                chunk_seconds=chunk_seconds,
                                sample_seconds=sample_seconds,
                                audio_wav=audio_wavs[video_id],
                                clip_start_s=clip_starts.get(video_id, 0.0),
                                job_dir=job_dir,
                                trial_idx=trial_idx,
                                subtitles_root=subtitles_root,
                                model_store_root=model_store_root,
                                run_streaming_benchmark=run_streaming_benchmark,
                                StreamingRunConfig=StreamingRunConfig,
                                extract_vtt_clip_text=extract_vtt_clip_text,
                                word_error_rate=word_error_rate,
                                char_error_rate=char_error_rate,
                                word_error_rate_normalized=word_error_rate_normalized,
                                word_error_rate_soft=word_error_rate_soft,
                                match_error_rate=match_error_rate,
                                word_information_lost=word_information_lost,
                                word_diff=word_diff,
                                progress_cb=lambda msg: _set_progress(
                                    status_file,
                                    f"Trial {trial_pos}/{len(execution_queue)}: video {v_idx+1}/{len(trial_video_ids)}: {msg}"
                                ),
                                source_wav_path=str(audio_wavs[video_id]),
                            )
                            if allow_cache:
                                trial_video_cache[cache_key] = copy.deepcopy(vm)
                            soft_ram_err = _soft_ram_violation(
                                limit_mb=constraints_ram_soft_limit_mb,
                                measured_ram_mb=(float(vm.get("ram_mb")) if isinstance(vm.get("ram_mb"), (int, float)) else None),
                            )
                            if soft_ram_err:
                                vm["error"] = soft_ram_err
                                trial_error = soft_ram_err
                                source_metrics.append(vm)
                                _set_progress(
                                    status_file,
                                    f"Trial {trial_pos}/{len(execution_queue)}: video {v_idx+1}/{len(trial_video_ids)} ⚠ {soft_ram_err}",
                                )
                                break
                            source_metrics.append(vm)
                            _set_progress(
                                status_file,
                                f"Trial {trial_pos}/{len(execution_queue)}: video {v_idx+1}/{len(trial_video_ids)} ✓ "
                                f"WER={vm['wer']} RTF={vm['rtf']}"
                            )
                            rss_now = _get_rss_mb(worker_proc)
                            if rss_now is not None:
                                worker_rss_peak_mb = max(v for v in [worker_rss_peak_mb, rss_now] if v is not None)
                        except Exception as e:
                            source_metrics.append({"video_id": video_id, "error": str(e)})
                            trial_error = str(e)
                            print(f"Trial {trial_idx} video {video_id} FAILED: {e}", file=sys.stderr)
                            traceback.print_exc(file=sys.stderr)
                    else:
                        _set_progress(
                            status_file,
                            f"Trial {trial_pos}/{len(execution_queue)}: video {v_idx+1}/{len(trial_video_ids)} ({video_id}) 🎤 real mic capture...",
                        )
                        try:
                            vm = _run_one_video_real_mic(
                                video_id=video_id,
                                model_id=trial_model_id,
                                model_params=dict(trial_params),
                                sample_seconds=sample_seconds,
                                clip_start_s=clip_starts.get(video_id, 0.0),
                                trial_idx=trial_idx,
                                job_dir=job_dir,
                                subtitles_root=subtitles_root,
                                extract_vtt_clip_text=extract_vtt_clip_text,
                                word_error_rate=word_error_rate,
                                char_error_rate=char_error_rate,
                                word_error_rate_normalized=word_error_rate_normalized,
                                word_error_rate_soft=word_error_rate_soft,
                                match_error_rate=match_error_rate,
                                word_information_lost=word_information_lost,
                                word_diff=word_diff,
                                progress_cb=lambda msg: _set_progress(
                                    status_file,
                                    f"Trial {trial_pos}/{len(execution_queue)}: video {v_idx+1}/{len(trial_video_ids)}: {msg}"
                                ),
                                mic_chunk_seconds=mic_chunk_seconds,
                                mic_prepare_seconds=mic_prepare_seconds,
                                mic_device=mic_device,
                            )
                            soft_ram_err = _soft_ram_violation(
                                limit_mb=constraints_ram_soft_limit_mb,
                                measured_ram_mb=(float(vm.get("ram_mb")) if isinstance(vm.get("ram_mb"), (int, float)) else None),
                            )
                            if soft_ram_err:
                                vm["error"] = soft_ram_err
                                trial_error = soft_ram_err
                                source_metrics.append(vm)
                                _set_progress(
                                    status_file,
                                    f"Trial {trial_pos}/{len(execution_queue)}: video {v_idx+1}/{len(trial_video_ids)} ⚠ {soft_ram_err}",
                                )
                                break
                            source_metrics.append(vm)
                            _set_progress(
                                status_file,
                                f"Trial {trial_pos}/{len(execution_queue)}: video {v_idx+1}/{len(trial_video_ids)} ✓ "
                                f"WER={vm.get('wer')} RTF={vm.get('rtf')}"
                            )
                            if vm.get("error") and trial_error is None:
                                trial_error = str(vm.get("error"))
                            rss_now = _get_rss_mb(worker_proc)
                            if rss_now is not None:
                                worker_rss_peak_mb = max(v for v in [worker_rss_peak_mb, rss_now] if v is not None)
                        except Exception as e:
                            source_metrics.append({"video_id": video_id, "error": str(e)})
                            trial_error = str(e)
                            print(f"Trial {trial_idx} video {video_id} FAILED: {e}", file=sys.stderr)
                            traceback.print_exc(file=sys.stderr)
            finally:
                load_summary = load_controller.stop()
                if load_summary.get("load_control_ok") is False:
                    _set_progress(
                        status_file,
                        f"WARN trial {trial_idx}: load target netrefen "
                        f"(CPU {load_summary.get('load_cpu_actual_avg_pct')}%, RAM {load_summary.get('load_ram_actual_avg_pct')}%).",
                    )

            # Průměr metrik přes všechna videa (jen z videí kde přepis proběhl)
            avg_rtf = _avg([m.get("rtf") for m in source_metrics])
            avg_wer = _avg([m.get("wer") for m in source_metrics if not m.get("error")])
            latency_quality = _derive_latency_quality(source_metrics)
            latency_lane = _latency_lane_from_quality(latency_quality)
            perceived_delay_s, perceived_delay_method, perceived_delay_quality = _compute_perceived_delay(
                source_metrics=source_metrics,
                chunk_seconds=chunk_seconds,
            )
            worker_rss_after_mb = _get_rss_mb(worker_proc)
            if worker_rss_after_mb is not None:
                worker_rss_peak_mb = max(v for v in [worker_rss_peak_mb, worker_rss_after_mb] if v is not None)

            ram_values = [m.get("ram_mb") for m in source_metrics if not m.get("error") and m.get("ram_mb") is not None]
            ram_avg_mb = _avg(ram_values)
            ram_peak_mb = round(max(ram_values), 1) if ram_values else None
            ram_p95_mb = _percentile([float(v) for v in ram_values if isinstance(v, (int, float))], 95)
            latency_values = [float(m.get("latency_ms")) for m in source_metrics if not m.get("error") and isinstance(m.get("latency_ms"), (int, float))]
            rtf_values = [float(m.get("rtf")) for m in source_metrics if not m.get("error") and isinstance(m.get("rtf"), (int, float))]
            first_token_values = [
                float(m.get("first_token_ms_p50"))
                for m in source_metrics
                if not m.get("error") and isinstance(m.get("first_token_ms_p50"), (int, float))
            ]
            first_token_p95_values = [
                float(m.get("first_token_ms_p95"))
                for m in source_metrics
                if not m.get("error") and isinstance(m.get("first_token_ms_p95"), (int, float))
            ]
            segment_finalize_values = [
                float(m.get("segment_finalize_ms_p50"))
                for m in source_metrics
                if not m.get("error") and isinstance(m.get("segment_finalize_ms_p50"), (int, float))
            ]
            segment_finalize_p95_values = [
                float(m.get("segment_finalize_ms_p95"))
                for m in source_metrics
                if not m.get("error") and isinstance(m.get("segment_finalize_ms_p95"), (int, float))
            ]
            drop_rate_values = [
                float(m.get("drop_rate"))
                for m in source_metrics
                if not m.get("error") and isinstance(m.get("drop_rate"), (int, float))
            ]
            session_reset_values = [
                int(m.get("session_resets"))
                for m in source_metrics
                if not m.get("error") and isinstance(m.get("session_resets"), int)
            ]
            reason_codes = [
                str(m.get("reason_code"))
                for m in source_metrics
                if m.get("reason_code")
            ]
            source_success_count = len([m for m in source_metrics if not m.get("error")])
            source_error_count = len(source_metrics) - source_success_count
            success_rate = round(source_success_count / len(source_metrics), 4) if source_metrics else None
            # rtf_viable: počítáme hned, ne až na konci — UI ukazuje správně i během jobu
            rtf_viable = bool(avg_rtf is not None and avg_rtf < 1.0 and not trial_error)
            trial_result = {
                "trial_idx": trial_idx,
                "model_id": trial_model_id,
                "params": {**trial_params, "chunk_seconds": chunk_seconds},
                "chunk_seconds": chunk_seconds,
                # Průměrované metriky — pouze z videí bez chyby pro konzistenci
                "wer": avg_wer,
                "cer": _avg([m.get("cer") for m in source_metrics if not m.get("error")]),
                "wer_normalized": _avg([m.get("wer_normalized") for m in source_metrics if not m.get("error")]),
                "wer_soft": _avg([m.get("wer_soft") for m in source_metrics if not m.get("error")]),
                "wer_llm": None,  # TODO: heuristic+llm (Ollama) zatím není implementováno
                "mer": _avg([m.get("mer") for m in source_metrics if not m.get("error")]),
                "wil": _avg([m.get("wil") for m in source_metrics if not m.get("error")]),
                "rtf": avg_rtf,
                "rtf_p50": _percentile(rtf_values, 50),
                "rtf_p95": _percentile(rtf_values, 95),
                "latency_ms": _avg([m.get("latency_ms") for m in source_metrics if not m.get("error")]),
                "latency_p50_ms": _percentile(latency_values, 50),
                "latency_p95_ms": _percentile(latency_values, 95),
                "latency_quality": latency_quality,
                "latency_lane": latency_lane,
                "first_token_ms_p50": _percentile(first_token_values, 50),
                "first_token_ms_p95": _percentile(first_token_p95_values, 95),
                "segment_finalize_ms_p50": _percentile(segment_finalize_values, 50),
                "segment_finalize_ms_p95": _percentile(segment_finalize_p95_values, 95),
                "drop_rate": _avg(drop_rate_values),
                "session_resets": int(sum(session_reset_values)) if session_reset_values else None,
                "reason_code": ",".join(sorted(set(reason_codes))) if reason_codes else None,
                "ram_mb": ram_avg_mb,
                "ram_peak_mb": ram_peak_mb,
                "ram_p95_mb": ram_p95_mb,
                "worker_rss_before_mb": worker_rss_before_mb,
                "worker_rss_after_mb": worker_rss_after_mb,
                "worker_rss_peak_mb": worker_rss_peak_mb,
                **_load_result_fields(
                    load_profile=load_profile,
                    load_cpu_target_pct=load_cpu_target_pct,
                    load_ram_target_pct=load_ram_target_pct,
                    load_summary=load_summary,
                ),
                **_constraints_result_fields(
                    constraints_profile=constraints_profile,
                    constraints_cpu_cores=(int(constraints_cpu_cores) if isinstance(constraints_cpu_cores, int) else None),
                    constraints_ram_limit_mb=(int(constraints_ram_limit_mb) if isinstance(constraints_ram_limit_mb, int) else None),
                    constraints_priority=constraints_priority,
                    constraints_applied=constraints_applied,
                    constraints_warnings=constraints_warnings,
                    constraints_ram_mode=constraints_ram_mode,
                    constraints_cpu_applied=constraints_cpu_applied,
                    constraints_priority_applied=constraints_priority_applied,
                    constraints_ram_hard_cap_applied=constraints_ram_hard_cap_applied,
                    constraints_ram_hard_cap_error=constraints_ram_hard_cap_error,
                ),
                "elapsed_s": _avg([m.get("elapsed_s") for m in source_metrics if not m.get("error")]),
                "total_audio_s": _avg([m.get("total_audio_s") for m in source_metrics if not m.get("error")]),
                "perceived_delay_s": perceived_delay_s,
                "perceived_delay_method": perceived_delay_method,
                "perceived_delay_quality": perceived_delay_quality,
                "source_success_count": source_success_count,
                "source_error_count": source_error_count,
                "success_rate": success_rate,
                "resource_metrics_available": psutil is not None,
                # Detail per video
                "source_metrics": source_metrics,
                # Pro UI — zobraz první video jako ukázku
                "transcript": source_metrics[0].get("transcript") if source_metrics else None,
                "reference_text": source_metrics[0].get("reference_text") if source_metrics else None,
                "word_diff": source_metrics[0].get("word_diff") if source_metrics else None,
                "chunk_metrics": source_metrics[0].get("chunk_metrics") if source_metrics else None,
                "word_count": sum(m.get("word_count", 0) for m in source_metrics),
                "error": trial_error,
                "is_pareto": False,      # přepočítá se po všech trialech
                "rtf_viable": rtf_viable, # počítáme okamžitě, ne až na konci
                "is_repeat": is_repeat,
                "repeat_of_trial_idx": repeat_of_trial_idx,
                "repeat_no": repeat_no,
                "repeat_group_key": f"trial_{repeat_of_trial_idx if repeat_of_trial_idx is not None else trial_idx}",
                "smart_round": smart_round if smart_round > 0 else None,
                "smart_stage": smart_stage or None,
                "_smart_candidate_key": smart_candidate_key,
                "trial_finished_at": _now(),
            }
            _append_result(status_file, trial_result)
            _on_trial_finished(trial_result)

        # Po dokončení všech trialů: spočti best + Pareto
        with _status_lock:
            data = json.loads(status_file.read_text(encoding="utf-8"))
            results = data.get("results", [])

            rankable = [r for r in results if not r.get("is_repeat")]
            best_idx, pareto_idxs = _compute_pareto_and_best(rankable)
            for r in results:
                r["is_pareto"] = (not r.get("is_repeat")) and (r["trial_idx"] in pareto_idxs)

            data["best_trial_idx"] = best_idx
            data["results"] = results
            data["completed_trials"] = len(results)
            data["total_trials"] = len(results)
            repro_summary = _build_reproducibility(results)
            data["reproducibility"] = repro_summary
            data["repro_validation"] = _validate_top_candidates_n(
                results,
                repro_summary,
                required_n=3,
                top_k=3,
            )
            data["status"] = "completed"
            base_count = len([r for r in results if not r.get("is_repeat")])
            repeat_count = len(results) - base_count
            if best_idx is not None:
                best_result = next((r for r in results if r["trial_idx"] == best_idx), None)
                repro_val = data.get("repro_validation") or {}
                repro_tag = (
                    " | n>=3 OK"
                    if bool(repro_val.get("passed"))
                    else f" | n>=3 MISS {len(repro_val.get('missing_trial_idxs') or [])}"
                )
                data["progress_message"] = (
                    f"Hotovo: {len(results)} trialů ({base_count} + {repeat_count} repeat), nejlepší #{best_idx} "
                    f"(WER={best_result['wer']}, RTF={best_result['rtf']}){repro_tag}"
                    if best_result is not None else f"Hotovo: {len(results)} trialů"
                )
            else:
                repro_val = data.get("repro_validation") or {}
                if int(repro_val.get("checked_top_k") or 0) == 0:
                    data["progress_message"] = (
                        f"Hotovo: {len(results)} trialů ({base_count} + {repeat_count} repeat) — "
                        "žádný validní seed trial (všechny fail/skip)"
                    )
                else:
                    data["progress_message"] = f"Hotovo: {len(results)} trialů ({base_count} + {repeat_count} repeat)"
            _atomic_write(status_file, json.dumps(data, ensure_ascii=False, indent=2))
        _emit_event(
            "job_completed",
            {
                "job_id": args.job_id,
                "completed_trials": len(results),
                "best_trial_idx": best_idx,
            },
        )
        return 0

    except Exception as e:
        traceback.print_exc(file=sys.stderr)
        _update_status(status_file, {"status": "failed", "error": str(e)})
        _emit_event("job_failed", {"job_id": args.job_id, "error": str(e)})
        return 1


if __name__ == "__main__":
    sys.exit(main())
