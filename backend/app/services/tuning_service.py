"""
Tuning service — spravuje tuning joby.
Každý job = subprocess (tuning_worker.py), výsledky přes soubory.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

from ..config import TUNING_ROOT, ROOT
from ..models.tuning import TuningJobStatus, TuningTrialResult, TuningJobRequest


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _job_dir(job_id: str) -> Path:
    return TUNING_ROOT / job_id


def create_job(req: TuningJobRequest) -> TuningJobStatus:
    job_id = f"tune_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"
    job_dir = _job_dir(job_id)
    job_dir.mkdir(parents=True, exist_ok=True)

    # Resolve effective model_ids (nové pole má přednost, fallback na starý model_id)
    effective_model_ids = req.model_ids if req.model_ids else ([req.model_id] if req.model_id else [])

    # Vygeneruj trial kombinace (cross-product přes modely)
    trials = _generate_trials(req, effective_model_ids)

    config = {
        "job_id": job_id,
        "model_id": effective_model_ids[0] if effective_model_ids else "",  # backward compat
        "model_ids": effective_model_ids,
        "video_ids": req.video_ids,
        "sample_seconds": req.sample_seconds,
        "clip_seed": req.clip_seed,
        "clip_start_seconds": req.clip_start_seconds,
        "evaluation_mode": req.evaluation_mode,
        "label": req.label,
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
        label=req.label,
        created_at=_now(),
        total_trials=len(trials),
        completed_trials=0,
    )
    _write_status(job_dir, status)

    # Spusť worker subprocess
    worker = ROOT / "scripts" / "tuning_worker.py"
    import os as _os
    env = {**_os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}
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
                if r.wer is not None and not r.error and (r.rtf is None or r.rtf <= 1.2)
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

    else:  # random
        param_names = [ps.name for ps in model_param_space]
        param_values = [ps.values for ps in model_param_space]
        all_combos = list(itertools.product(*param_values, chunk_values)) if param_values else [(cs,) for cs in chunk_values]
        random.shuffle(all_combos)
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
    valid = [(r, r.wer, r.rtf) for r in results if r.wer is not None and not r.error]
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
