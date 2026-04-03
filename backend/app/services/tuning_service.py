"""
Tuning service — spravuje tuning joby.
Každý job = subprocess (tuning_worker.py), výsledky přes soubory.
"""
from __future__ import annotations

import json
import platform
import shutil
import subprocess
import sys
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

from ..config import TUNING_ROOT, ROOT
from ..models.tuning import (
    TuningJobStatus,
    TuningTrialResult,
    TuningJobRequest,
    TuningMicCalibration,
    TuningMicCalibrationCheckResponse,
)
from packages.adapters._registry import get_model
from packages.common.tuning_event_store import (
    event_db_path,
    get_event_stats,
    read_events,
    summarize_event_sequence,
)

try:
    import psutil
except ModuleNotFoundError:
    psutil = None  # type: ignore[assignment]

WEAK_MAX_LOGICAL_CORES = 4
WEAK_MAX_RAM_MB = 8192
MID_MAX_LOGICAL_CORES = 8
MID_MAX_RAM_MB = 16384
CONSTRAINTS_PROFILE_DEFAULTS: dict[str, dict[str, object]] = {
    "weak_cap": {"cpu_cores": 2, "ram_limit_mb": 4096, "priority": "idle"},
    "mid_cap": {"cpu_cores": 4, "ram_limit_mb": 8192, "priority": "below_normal"},
}
LOAD_PROFILE_DEFAULTS: dict[str, tuple[float, float]] = {
    "light": (25.0, 35.0),
    "medium": (45.0, 55.0),
    "heavy": (65.0, 75.0),
}
MIC_CALIBRATION_THRESHOLDS = {
    "rms_min_dbfs": -24.0,
    "rms_max_dbfs": -12.0,
    "clipping_max_pct": 0.1,
    "noise_floor_max_dbfs": -38.0,
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def evaluate_mic_calibration(
    *,
    rms_dbfs: float,
    clipping_rate_pct: float,
    noise_floor_dbfs: float,
) -> TuningMicCalibrationCheckResponse:
    reasons: list[str] = []
    t = MIC_CALIBRATION_THRESHOLDS

    rms = float(rms_dbfs)
    clipping = float(clipping_rate_pct)
    noise_floor = float(noise_floor_dbfs)

    if rms < float(t["rms_min_dbfs"]):
        reasons.append(f"RMS je příliš nízko ({rms:.2f} dBFS < {t['rms_min_dbfs']:.2f}).")
    if rms > float(t["rms_max_dbfs"]):
        reasons.append(f"RMS je příliš vysoko ({rms:.2f} dBFS > {t['rms_max_dbfs']:.2f}).")
    if clipping > float(t["clipping_max_pct"]):
        reasons.append(f"Clipping je příliš vysoký ({clipping:.3f}% > {t['clipping_max_pct']:.3f}%).")
    if noise_floor > float(t["noise_floor_max_dbfs"]):
        reasons.append(
            f"Noise floor je příliš vysoký ({noise_floor:.2f} dBFS > {t['noise_floor_max_dbfs']:.2f} dBFS)."
        )

    return TuningMicCalibrationCheckResponse(
        passed=len(reasons) == 0,
        reasons=reasons,
        thresholds=dict(MIC_CALIBRATION_THRESHOLDS),
        metrics={
            "rms_dbfs": rms,
            "clipping_rate_pct": clipping,
            "noise_floor_dbfs": noise_floor,
        },
    )


def _job_dir(job_id: str) -> Path:
    return TUNING_ROOT / job_id


def _classify_hardware_profile(logical_cores: int | None, ram_total_mb: float | None) -> str:
    # NOTE: HW profil je třída stroje podle celkových zdrojů, ne podle aktuálního zatížení CPU/RAM.
    if logical_cores is None and ram_total_mb is None:
        return "mid_office"
    if ((logical_cores is not None and logical_cores <= WEAK_MAX_LOGICAL_CORES)
            or (ram_total_mb is not None and ram_total_mb <= WEAK_MAX_RAM_MB)):
        return "weak_office"
    if ((logical_cores is not None and logical_cores <= MID_MAX_LOGICAL_CORES)
            or (ram_total_mb is not None and ram_total_mb <= MID_MAX_RAM_MB)):
        return "mid_office"
    return "strong_office"


def _clamp_pct(value: float | int | None) -> float | None:
    if value is None:
        return None
    try:
        v = float(value)
    except Exception:
        return None
    return max(0.0, min(95.0, round(v, 1)))


def _resolve_load_profile(
    load_profile: str | None,
    load_cpu_target_pct: float | None,
    load_ram_target_pct: float | None,
) -> tuple[str, float | None, float | None]:
    profile = (load_profile or "none").strip().lower()
    if profile not in {"none", "custom", *LOAD_PROFILE_DEFAULTS.keys()}:
        profile = "none"

    if profile == "none":
        return "none", None, None

    if profile in LOAD_PROFILE_DEFAULTS:
        default_cpu, default_ram = LOAD_PROFILE_DEFAULTS[profile]
        cpu_target = _clamp_pct(load_cpu_target_pct)
        ram_target = _clamp_pct(load_ram_target_pct)
        cpu_target = default_cpu if cpu_target is None else cpu_target
        ram_target = default_ram if ram_target is None else ram_target
    else:
        cpu_target = _clamp_pct(load_cpu_target_pct)
        ram_target = _clamp_pct(load_ram_target_pct)

    if (cpu_target or 0.0) <= 0.0 and (ram_target or 0.0) <= 0.0:
        return "none", None, None
    return profile, cpu_target, ram_target


def _resolve_constraints(
    profile: str | None,
    cpu_cores: int | None,
    ram_limit_mb: int | None,
    priority: str | None,
) -> tuple[str, int | None, int | None, str]:
    p = (profile or "none").strip().lower()
    if p not in {"none", "custom", *CONSTRAINTS_PROFILE_DEFAULTS.keys()}:
        p = "none"
    prio = (priority or "below_normal").strip().lower()
    if prio not in {"normal", "below_normal", "idle"}:
        prio = "below_normal"

    c = int(cpu_cores) if isinstance(cpu_cores, int) else None
    r = int(ram_limit_mb) if isinstance(ram_limit_mb, int) else None
    if c is not None:
        c = max(1, min(128, c))
    if r is not None:
        r = max(256, min(262144, r))

    if p in CONSTRAINTS_PROFILE_DEFAULTS:
        defaults = CONSTRAINTS_PROFILE_DEFAULTS[p]
        c = int(defaults["cpu_cores"]) if c is None else c
        r = int(defaults["ram_limit_mb"]) if r is None else r
        prio = str(defaults["priority"])

    if p == "none":
        return "none", None, None, prio
    if p == "custom" and c is None and r is None:
        return "none", None, None, prio
    return p, c, r, prio


def _detect_hardware_info() -> dict:
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

    cpu_model = (platform.processor() or "").strip()
    if not cpu_model:
        cpu_model = (platform.uname().processor or "").strip()
    return {
        "hostname": platform.node(),
        "os": platform.platform(),
        "python": platform.python_version(),
        "cpu_model": cpu_model or None,
        "logical_cores": logical_cores,
        "physical_cores": physical_cores,
        "ram_total_mb": ram_total_mb,
    }


def create_job(req: TuningJobRequest) -> TuningJobStatus:
    # Resolve effective model_ids (nové pole má přednost, fallback na starý model_id)
    effective_model_ids = req.model_ids if req.model_ids else ([req.model_id] if req.model_id else [])
    if not effective_model_ids:
        raise ValueError("Musí být vybrán alespoň jeden model.")

    input_mode = (req.input_mode or "replay").strip().lower()
    if input_mode not in {"replay", "real_mic"}:
        raise ValueError("input_mode musí být 'replay' nebo 'real_mic'.")

    mic_device: int | str | None = None
    mic_chunk_seconds: float | None = None
    mic_prepare_seconds: int | None = None

    if input_mode == "real_mic":
        if req.mic_protocol is None:
            raise ValueError("Pro real_mic režim chybí mic_protocol.")
        environment = (req.mic_protocol.environment or "").strip().lower()
        if environment not in {"quiet", "office_noise"}:
            raise ValueError("mic_protocol.environment musí být 'quiet' nebo 'office_noise'.")
        if req.mic_calibration is None:
            raise ValueError("Pro real_mic režim chybí mic_calibration.")
        calibration_check = evaluate_mic_calibration(
            rms_dbfs=req.mic_calibration.rms_dbfs,
            clipping_rate_pct=req.mic_calibration.clipping_rate_pct,
            noise_floor_dbfs=req.mic_calibration.noise_floor_dbfs,
        )
        if not req.mic_calibration.passed or not calibration_check.passed:
            details = "; ".join(calibration_check.reasons) if calibration_check.reasons else "kalibrace neprošla."
            raise ValueError(f"Kalibrace real_mic režimu neprošla: {details}")

        unsupported: list[str] = []
        for model_id in effective_model_ids:
            descriptor = get_model(str(model_id))
            if descriptor is None:
                unsupported.append(f"{model_id} (neznámý model)")
            elif not descriptor.supports_microphone:
                unsupported.append(f"{model_id} (supports_microphone=false)")
        if unsupported:
            raise ValueError(
                "Tyto modely nepodporují real_mic režim: "
                + ", ".join(unsupported)
            )

        mic_chunk_seconds = float(req.mic_chunk_seconds) if req.mic_chunk_seconds is not None else 0.20
        mic_prepare_seconds = int(req.mic_prepare_seconds) if req.mic_prepare_seconds is not None else 4
        if req.mic_device is not None:
            if isinstance(req.mic_device, int):
                mic_device = int(req.mic_device)
            else:
                raw_device = str(req.mic_device).strip()
                if raw_device and raw_device.lower() != "default":
                    mic_device = int(raw_device) if raw_device.lstrip("-").isdigit() else raw_device

    effective_validate_beam_preflight = bool(req.validate_beam_preflight) and input_mode == "replay"

    job_id = f"tune_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"
    job_dir = _job_dir(job_id)
    job_dir.mkdir(parents=True, exist_ok=True)

    hardware_info = _detect_hardware_info()
    hardware_profile = (req.hardware_profile or "").strip() or _classify_hardware_profile(
        hardware_info.get("logical_cores"),
        hardware_info.get("ram_total_mb"),
    )
    hardware_note = (req.hardware_note or "").strip() or None
    constraints_profile, constraints_cpu_cores, constraints_ram_limit_mb, constraints_priority = _resolve_constraints(
        req.constraints_profile,
        req.constraints_cpu_cores,
        req.constraints_ram_limit_mb,
        req.constraints_priority,
    )
    load_profile, load_cpu_target_pct, load_ram_target_pct = _resolve_load_profile(
        req.load_profile,
        req.load_cpu_target_pct,
        req.load_ram_target_pct,
    )

    # Vygeneruj trial kombinace (cross-product přes modely)
    trials = _generate_trials(req, effective_model_ids)
    repeat_top_k = max(0, int(req.repeat_top_k or 0))
    repeat_runs = max(1, int(req.repeat_runs or 1))
    planned_total_trials = len(trials) + (repeat_top_k * max(0, repeat_runs - 1))

    config = {
        "job_id": job_id,
        "model_id": effective_model_ids[0] if effective_model_ids else "",  # backward compat
        "model_ids": effective_model_ids,
        "input_mode": input_mode,
        "video_ids": req.video_ids,
        "sample_seconds": req.sample_seconds,
        "clip_seed": req.clip_seed,
        "random_seed": req.random_seed,
        "clip_start_seconds": req.clip_start_seconds,
        "strategy": req.strategy,
        "evaluation_mode": req.evaluation_mode,
        "label": req.label,
        "hardware_profile": hardware_profile,
        "hardware_note": hardware_note,
        "hardware_info": hardware_info,
        "constraints_profile": constraints_profile,
        "constraints_cpu_cores": constraints_cpu_cores,
        "constraints_ram_limit_mb": constraints_ram_limit_mb,
        "constraints_priority": constraints_priority,
        "load_profile": load_profile,
        "load_cpu_target_pct": load_cpu_target_pct,
        "load_ram_target_pct": load_ram_target_pct,
        "validate_beam_preflight": effective_validate_beam_preflight,
        "mic_protocol": req.mic_protocol.model_dump() if req.mic_protocol else None,
        "mic_calibration": req.mic_calibration.model_dump() if req.mic_calibration else None,
        "mic_device": mic_device,
        "mic_chunk_seconds": mic_chunk_seconds,
        "mic_prepare_seconds": mic_prepare_seconds,
        "repeat_top_k": repeat_top_k,
        "repeat_runs": repeat_runs,
        "baseline_params": req.baseline_params,
        "trials": trials,          # seznam dict s params + _chunk_seconds + _model_id
        "subtitles_root": str(ROOT / "runtime" / "library" / "subtitles"),
        "model_store_root": str(ROOT / "runtime" / "model_store"),
        "audio_cache_dir": str(ROOT / "runtime" / "audio_cache"),
    }
    (job_dir / "config.json").write_text(
        json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    status = TuningJobStatus(
        job_id=job_id,
        status="pending",
        model_id=effective_model_ids[0] if effective_model_ids else "",
        model_ids=effective_model_ids,
        input_mode=input_mode,
        strategy=req.strategy,
        label=req.label,
        hardware_profile=hardware_profile,
        hardware_note=hardware_note,
        hardware_info=hardware_info,
        constraints_profile=constraints_profile,
        constraints_cpu_cores=constraints_cpu_cores,
        constraints_ram_limit_mb=constraints_ram_limit_mb,
        constraints_priority=constraints_priority,
        constraints_applied=False,
        constraints_warnings=[],
        constraints_ram_mode="none",
        load_profile=load_profile,
        load_cpu_target_pct=load_cpu_target_pct,
        load_ram_target_pct=load_ram_target_pct,
        validate_beam_preflight=effective_validate_beam_preflight,
        mic_protocol=req.mic_protocol,
        mic_calibration=req.mic_calibration,
        mic_device=mic_device,
        mic_chunk_seconds=mic_chunk_seconds,
        mic_prepare_seconds=mic_prepare_seconds,
        created_at=_now(),
        total_trials=planned_total_trials,
        completed_trials=0,
    )
    _write_status(job_dir, status)

    # Spusť worker subprocess
    worker = ROOT / "scripts" / "tuning_worker.py"
    import os as _os
    env = {
        **_os.environ,
        "PYTHONIOENCODING": "utf-8",
        "PYTHONUTF8": "1",
        # Default: povolit whisper-server model cache v tuning workeru (výrazně zkrátí load modelu mezi trialy).
        "ASTT_WHISPER_SERVER_CACHE": _os.environ.get("ASTT_WHISPER_SERVER_CACHE", "1"),
        "ASTT_WHISPER_ONLINE_PROBE": _os.environ.get("ASTT_WHISPER_ONLINE_PROBE", "1"),
        "ASTT_REQUIRE_RESOURCE_METRICS": _os.environ.get("ASTT_REQUIRE_RESOURCE_METRICS", "1"),
    }
    has_large_whisper = any(str(mid).startswith("whisper_cpp_large_v3") for mid in effective_model_ids)
    if has_large_whisper:
        # large_v3* pod cap/load profilem potřebuje delší timeout, jinak končí falešným timeoutem ve validaci
        if constraints_profile in {"weak_cap", "mid_cap"}:
            env.setdefault("ASTT_WHISPER_TIMEOUT_FACTOR", "6")
            env.setdefault("ASTT_WHISPER_TIMEOUT_LARGE_MIN_S", "300")
        else:
            env.setdefault("ASTT_WHISPER_TIMEOUT_FACTOR", "4")
            env.setdefault("ASTT_WHISPER_TIMEOUT_LARGE_MIN_S", "240")
        env.setdefault("ASTT_WHISPER_TIMEOUT_MAX_S", "900")
    subprocess.Popen(
        [sys.executable, str(worker), "--job-id", job_id, "--tuning-root", str(TUNING_ROOT)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        cwd=str(ROOT),
        env=env,
    )

    return status


def get_job(job_id: str) -> Optional[TuningJobStatus]:
    job_dir = _job_dir(job_id)
    status_file = job_dir / "status.json"
    if not status_file.exists():
        return None
    try:
        data = json.loads(status_file.read_text(encoding="utf-8"))
        status = TuningJobStatus(**data)
        # Označ Pareto optimální body (live výpočet pro průběžné zobrazení)
        _mark_pareto(status.results)
        # best_trial_idx: pro completed job použij workerovu finální hodnotu,
        # pro running job počítej live (worker ji ještě nezapsal)
        if status.status != "completed" or status.best_trial_idx is None:
            candidates = [
                r for r in status.results
                if (not r.is_repeat) and r.wer is not None and not r.error and (r.rtf is None or r.rtf <= 1.2)
            ]
            if candidates:
                best = min(candidates, key=lambda r: r.wer)  # type: ignore
                status.best_trial_idx = best.trial_idx
        return status
    except Exception:
        return None


def cancel_job(job_id: str) -> Optional[TuningJobStatus]:
    job_dir = _job_dir(job_id)
    status_file = job_dir / "status.json"
    if not status_file.exists():
        return None
    # Zapsat cancel flag — worker ho přečte a skončí
    (job_dir / "cancel").write_text("1", encoding="utf-8")
    # Aktualizovat status
    try:
        data = json.loads(status_file.read_text(encoding="utf-8"))
        status = TuningJobStatus(**data)
        if status.status in ("pending", "running"):
            status.status = "cancelled"
            _write_status(job_dir, status)
        return status
    except Exception:
        return None


def list_jobs() -> list[TuningJobStatus]:
    out = []
    for d in sorted(TUNING_ROOT.iterdir(), reverse=True):
        s = get_job(d.name)
        if s:
            out.append(s)
    return out


def get_job_events(
    job_id: str,
    *,
    after_seq: int = 0,
    limit: int = 200,
    event_type: str | None = None,
) -> dict:
    job_dir = _job_dir(job_id)
    status_file = job_dir / "status.json"
    if not status_file.exists():
        return {}
    events = read_events(
        job_dir,
        after_seq=max(0, int(after_seq)),
        limit=max(1, min(int(limit), 2000)),
        event_type=(str(event_type).strip() if event_type else None),
    )
    max_seq = events[-1]["seq"] if events else max(0, int(after_seq))
    stats = get_event_stats(job_dir)
    validation = summarize_event_sequence(events)
    return {
        "job_id": job_id,
        "event_db": str(event_db_path(job_dir)),
        "after_seq": max(0, int(after_seq)),
        "next_after_seq": max_seq,
        "count": len(events),
        "events": events,
        "stats": stats,
        "validation": validation,
    }


def cleanup_old_trial_files(min_age_days: int = 10, min_newer_completed_jobs: int = 3) -> int:
    """Smaže trial_NNN/ adresáře ze starých dokončených jobů.

    Podmínky (obě musí být splněny):
    - Job starší než min_age_days dní
    - Existuje alespoň min_newer_completed_jobs novějších completed jobů

    Vrátí počet smazaných adresářů.
    """
    all_jobs = list_jobs()
    completed = [j for j in all_jobs if j.status == "completed"]
    completed.sort(key=lambda j: j.created_at, reverse=True)  # nejnovější první

    cutoff = datetime.now(timezone.utc) - timedelta(days=min_age_days)
    deleted = 0

    for i, job in enumerate(completed):
        try:
            created = datetime.fromisoformat(job.created_at.replace("Z", "+00:00"))
        except Exception:
            continue
        if created > cutoff:
            continue  # příliš nový
        # Kolik novějších completed jobů existuje?
        newer_count = sum(
            1 for j in completed[:i]
            if j.status == "completed"
        )
        if newer_count < min_newer_completed_jobs:
            continue  # nesplněna podmínka počtu novějších jobů

        job_dir = TUNING_ROOT / job.job_id
        for trial_dir in job_dir.glob("trial_*"):
            if trial_dir.is_dir():
                shutil.rmtree(trial_dir, ignore_errors=True)
                deleted += 1
        # Smaž i audio_cache z job_dir (clip WAVy) — plné WAVy jsou v runtime/audio_cache
        for audio_f in job_dir.glob("audio_*.wav"):
            audio_f.unlink(missing_ok=True)
            deleted += 1

    return deleted


def _write_status(job_dir: Path, status: TuningJobStatus) -> None:
    (job_dir / "status.json").write_text(
        status.model_dump_json(indent=2), encoding="utf-8"
    )


def _is_invalid_combo(params: dict) -> bool:
    """Vrátí True pokud je kombinace parametrů neplatná pro whisper.cpp."""
    best_of = params.get("best_of")
    beam_size = params.get("beam_size")
    if isinstance(best_of, int) and isinstance(beam_size, int) and best_of > beam_size:
        return True
    return False


def _generate_trials(req: TuningJobRequest, model_ids: list[str] | None = None) -> list[dict]:
    """Generuje seznam trial konfigurací dle strategie."""
    import itertools, random

    # Separuj chunk_seconds z param_space (speciální parametr)
    chunk_space = None
    model_param_space = []
    for ps in req.param_space:
        if ps.name == "chunk_seconds":
            chunk_space = sorted(ps.values)
        else:
            model_param_space.append(ps)

    chunk_values = chunk_space or [req.baseline_params.get("chunk_seconds", 30)]
    effective_model_ids = model_ids or [req.model_id or ""]

    if req.strategy == "grid":
        param_names = [ps.name for ps in model_param_space]
        # Seřadit hodnoty — zabrání náhodným pořadím ze Set
        param_values = [sorted(ps.values, key=lambda v: (str(type(v)), v if not isinstance(v, bool) else int(v))) for ps in model_param_space]
        combos = list(itertools.product(*param_values)) if param_values else [()]
        trials = []
        for mid in effective_model_ids:
            for combo in combos:
                params = dict(req.baseline_params)
                for name, val in zip(param_names, combo):
                    params[name] = val
                # Přeskoč neplatné kombinace: best_of nesmí být > beam_size
                if _is_invalid_combo(params):
                    continue
                for cs in chunk_values:
                    trials.append({**params, "_chunk_seconds": cs, "_model_id": mid})
        # Grid: max_trials neomezuje (všechny kombinace jsou zamýšleny)

    elif req.strategy == "ablation":
        # Baseline + vary každý parametr zvlášť
        baseline = dict(req.baseline_params)
        trials = []
        for mid in effective_model_ids:
            trials.append({**baseline, "_chunk_seconds": chunk_values[0], "_model_id": mid})
            for ps in model_param_space:
                for val in ps.values:
                    if val == baseline.get(ps.name):
                        continue
                    t = {**baseline, ps.name: val, "_chunk_seconds": chunk_values[0], "_model_id": mid}
                    if _is_invalid_combo(t):
                        continue
                    trials.append(t)
            for cs in chunk_values[1:]:
                trials.append({**baseline, "_chunk_seconds": cs, "_model_id": mid})

    elif req.strategy == "smart":
        # Smart: vygeneruj kandidátní pool, vlastní výběr/vyřazování dělá tuning_worker (successive halving).
        param_names = [ps.name for ps in model_param_space]
        param_values = [sorted(ps.values, key=lambda v: (str(type(v)), v if not isinstance(v, bool) else int(v))) for ps in model_param_space]
        combos = list(itertools.product(*param_values)) if param_values else [()]
        rnd = random.Random(req.random_seed if req.random_seed is not None else 42)
        trials = []
        max_per_model = max(1, int(req.max_trials or 20))
        for mid in effective_model_ids:
            model_candidates: list[dict] = []
            for combo in combos:
                params = dict(req.baseline_params)
                for name, val in zip(param_names, combo):
                    params[name] = val
                if _is_invalid_combo(params):
                    continue
                for cs in chunk_values:
                    model_candidates.append({**params, "_chunk_seconds": cs, "_model_id": mid})
            if len(model_candidates) > max_per_model:
                rnd.shuffle(model_candidates)
                model_candidates = model_candidates[:max_per_model]
            trials.extend(model_candidates)
        return trials

    else:  # random
        param_names = [ps.name for ps in model_param_space]
        param_values = [ps.values for ps in model_param_space]
        all_combos = list(itertools.product(*param_values, chunk_values)) if param_values else [(cs,) for cs in chunk_values]
        rnd = random.Random(req.random_seed if req.random_seed is not None else 42)
        rnd.shuffle(all_combos)
        trials = []
        for mid in effective_model_ids:
            for combo in all_combos:
                if len(trials) >= req.max_trials * len(effective_model_ids):
                    break
                params = dict(req.baseline_params)
                for name, val in zip(param_names, combo[:-1]):
                    params[name] = val
                if _is_invalid_combo(params):
                    continue
                params["_chunk_seconds"] = combo[-1]
                params["_model_id"] = mid
                trials.append(params)
        return trials  # random: max_trials aplikován výše

    return trials


def _mark_pareto(results: list[TuningTrialResult]) -> None:
    """Označí Pareto-optimální body (minimalizace WER a RTF). Error triály jsou vyloučeny."""
    valid = [(r, r.wer, r.rtf) for r in results if (not r.is_repeat) and r.wer is not None and not r.error]
    for r in results:
        r.is_pareto = False
    for r, wer, rtf in valid:
        dominated = any(
            (other_wer <= wer and other_rtf <= (rtf or 999))
            and (other_wer < wer or other_rtf < (rtf or 999))
            for _, other_wer, other_rtf in valid
            if other_wer is not None
        )
        if not dominated:
            r.is_pareto = True
