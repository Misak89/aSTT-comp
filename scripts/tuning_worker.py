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
import os
import random
import sys
import threading
import traceback
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


_status_lock = threading.Lock()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _atomic_write(path: Path, text: str) -> None:
    """Atomický zápis: zapíše do .tmp, pak přejmenuje — zabrání částečnému čtení."""
    tmp = path.with_suffix(".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(str(tmp), str(path))


def _update_status(status_file: Path, updates: dict) -> None:
    with _status_lock:
        try:
            data = json.loads(status_file.read_text(encoding="utf-8"))
            data.update(updates)
            _atomic_write(status_file, json.dumps(data, ensure_ascii=False, indent=2))
        except Exception as e:
            print(f"WARN: status update failed: {e}", file=sys.stderr)


def _set_progress(status_file: Path, message: str) -> None:
    from datetime import datetime
    _update_status(status_file, {
        "progress_message": message,
        "updated_ts": datetime.now().isoformat(),
    })
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


def _wav_generator(wav_path: Path):
    """Generátor který přečte WAV a vrátí ho jako jeden chunk."""
    import wave
    from array import array as _array

    with wave.open(str(wav_path), "rb") as wf:
        sr = wf.getframerate()
        raw = wf.readframes(wf.getnframes())
    samples_int = _array("h", raw)
    samples_float = [s / 32768.0 for s in samples_int]
    yield samples_float, sr


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
    word_error_rate_normalized, match_error_rate, word_information_lost, word_diff,
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
        audio_generator=_wav_generator(audio_wav),
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
                        source_wav_path=str(tmp),
                    )
                    run_streaming_benchmark(source=src, audio_generator=_wav_generator(tmp), config=cfg)
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

    job_dir = Path(args.tuning_root) / args.job_id
    config_file = job_dir / "config.json"
    status_file = job_dir / "status.json"

    if not config_file.exists():
        print(f"ERROR: config not found: {config_file}", file=sys.stderr)
        return 2

    config = json.loads(config_file.read_text(encoding="utf-8"))
    trials: list[dict] = config["trials"]
    model_id: str = config["model_id"]          # fallback pro old-style joby (single model)
    video_ids: list[str] = config["video_ids"]
    sample_seconds: int = config["sample_seconds"]
    clip_seed: int | None = config.get("clip_seed")
    subtitles_root = Path(config["subtitles_root"])
    model_store_root = Path(config["model_store_root"])
    audio_cache_dir: Path | None = Path(config["audio_cache_dir"]) if config.get("audio_cache_dir") else None
    items_json = subtitles_root.parent / "items.json"

    # Pre-compute clip_start per video — stejný offset pro všechny trialy (férovné srovnání)
    clip_starts = _compute_clip_starts(video_ids, sample_seconds, clip_seed, items_json)

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

        # Pre-download audio pro každé video jednou — žádné opakované YouTube požadavky
        audio_wavs: dict[str, Path] = {}
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

        # Validace beam_size hodnot před spuštěním trialů — zabrání stovkám timeoutů
        # Testujeme per-model: (model_id, beam_size) — beam_size=5 může selhat jen na large_v3, ne na small
        model_beam_pairs: set[tuple[str, int]] = set()
        for t in trials:
            t_model_id = str(t.get("_model_id", model_id))
            bs = t.get("beam_size")
            if bs is not None:
                model_beam_pairs.add((t_model_id, int(bs)))

        if model_beam_pairs:
            validated_pairs = _validate_beam_sizes(
                model_beam_pairs=model_beam_pairs,
                audio_wavs=audio_wavs,
                model_store_root=model_store_root,
                status_file=status_file,
                run_streaming_benchmark=run_streaming_benchmark,
                StreamingRunConfig=StreamingRunConfig,
            )
        else:
            validated_pairs = None  # žádné beam_size v trialech — validaci přeskoč

        for trial_idx, trial_params_raw in enumerate(trials):
            # Kontrola cancel flagu
            if (job_dir / "cancel").exists():
                _update_status(status_file, {"status": "cancelled"})
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
                _set_progress(status_file, f"Trial {trial_idx+1}/{len(trials)}: SKIP — {trial_model_id} beam_size={beam_size} selhalo při validaci (timeout)")
                _append_result(status_file, {
                    "trial_idx": trial_idx,
                    "model_id": trial_model_id,
                    "params": {**trial_params, "chunk_seconds": chunk_seconds},
                    "chunk_seconds": chunk_seconds,
                    "wer": None, "cer": None, "wer_normalized": None, "mer": None, "wil": None,
                    "rtf": None, "latency_ms": None, "source_metrics": [],
                    "transcript": None, "reference_text": None, "word_diff": None,
                    "chunk_metrics": None, "word_count": 0,
                    "error": f"Přeskočeno: {trial_model_id} beam_size={beam_size} selhalo při validaci (timeout)",
                    "is_pareto": False, "rtf_viable": False, "perceived_delay_s": None,
                })
                continue

            # Validace: best_of nesmí být větší než beam_size
            if isinstance(best_of, int) and isinstance(beam_size, int) and best_of > beam_size:
                _set_progress(status_file, f"Trial {trial_idx+1}/{len(trials)}: SKIP — best_of={best_of} > beam_size={beam_size}")
                _append_result(status_file, {
                    "trial_idx": trial_idx,
                    "params": {**trial_params, "chunk_seconds": chunk_seconds},
                    "chunk_seconds": chunk_seconds,
                    "wer": None, "cer": None, "wer_normalized": None, "mer": None, "wil": None,
                    "rtf": None, "latency_ms": None, "source_metrics": [],
                    "transcript": None, "reference_text": None, "word_diff": None,
                    "chunk_metrics": None, "word_count": 0,
                    "error": f"Přeskočeno: best_of={best_of} > beam_size={beam_size}",
                    "is_pareto": False, "rtf_viable": False, "perceived_delay_s": None,
                })
                continue

            param_str = ", ".join(f"{k}={v}" for k, v in trial_params.items() if k != "initial_prompt")
            if trial_params.get("initial_prompt"):
                param_str += f", prompt='{str(trial_params['initial_prompt'])[:30]}...'"
            _set_progress(status_file, f"Trial {trial_idx+1}/{len(trials)} [{trial_model_id}]: chunk={chunk_seconds}s | {param_str}")

            source_metrics: list[dict] = []
            trial_error: str | None = None

            for v_idx, video_id in enumerate(video_ids):
                if video_id not in audio_wavs:
                    source_metrics.append({"video_id": video_id, "error": "Audio nebylo staženo"})
                    continue
                _set_progress(
                    status_file,
                    f"Trial {trial_idx+1}/{len(trials)}: video {v_idx+1}/{len(video_ids)} ({video_id}) ▶ přepisuji..."
                )
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
                        match_error_rate=match_error_rate,
                        word_information_lost=word_information_lost,
                        word_diff=word_diff,
                        progress_cb=lambda msg: _set_progress(
                            status_file,
                            f"Trial {trial_idx+1}/{len(trials)}: video {v_idx+1}/{len(video_ids)}: {msg}"
                        ),
                        source_wav_path=str(audio_wavs[video_id]),
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

            # Průměr metrik přes všechna videa (jen z videí kde přepis proběhl)
            avg_rtf = _avg([m.get("rtf") for m in source_metrics])
            avg_wer = _avg([m.get("wer") for m in source_metrics if not m.get("error")])
            # perceived_delay_s: pro batch model (whisper) = čekáš celý sample_seconds + zpracování
            # perceived_delay_s = sample_seconds * (1 + RTF) — fyzicky smysluplné pro batch
            perceived_delay_s = round(sample_seconds * (1.0 + avg_rtf), 3) if avg_rtf is not None else None
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
                "mer": _avg([m.get("mer") for m in source_metrics if not m.get("error")]),
                "wil": _avg([m.get("wil") for m in source_metrics if not m.get("error")]),
                "rtf": avg_rtf,
                "latency_ms": _avg([m.get("latency_ms") for m in source_metrics if not m.get("error")]),
                "perceived_delay_s": perceived_delay_s,
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
            }
            _append_result(status_file, trial_result)

        # Po dokončení všech trialů: spočti best + Pareto
        with _status_lock:
            data = json.loads(status_file.read_text(encoding="utf-8"))
            results = data.get("results", [])

            best_idx, pareto_idxs = _compute_pareto_and_best(results)
            for r in results:
                r["is_pareto"] = r["trial_idx"] in pareto_idxs

            data["best_trial_idx"] = best_idx
            data["results"] = results
            data["status"] = "completed"
            if best_idx is not None:
                best_result = next((r for r in results if r["trial_idx"] == best_idx), None)
                data["progress_message"] = (
                    f"Hotovo: {len(results)} trialů, nejlepší #{best_idx} "
                    f"(WER={best_result['wer']}, RTF={best_result['rtf']})"
                    if best_result is not None else f"Hotovo: {len(results)} trialů"
                )
            else:
                data["progress_message"] = f"Hotovo: {len(results)} trialů"
            _atomic_write(status_file, json.dumps(data, ensure_ascii=False, indent=2))
        return 0

    except Exception as e:
        traceback.print_exc(file=sys.stderr)
        _update_status(status_file, {"status": "failed", "error": str(e)})
        return 1


if __name__ == "__main__":
    sys.exit(main())
