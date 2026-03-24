"""
Tuning service — spravuje tuning joby.
Každý job = subprocess (tuning_worker.py), výsledky přes soubory.
"""
from __future__ import annotations

import json
import subprocess
import sys
import uuid
from datetime import datetime, timezone
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

    # Vygeneruj trial kombinace
    trials = _generate_trials(req)

    config = {
        "job_id": job_id,
        "model_id": req.model_id,
        "video_ids": req.video_ids,
        "sample_seconds": req.sample_seconds,
        "clip_seed": req.clip_seed,
        "label": req.label,
        "baseline_params": req.baseline_params,
        "trials": trials,          # seznam dict s params + chunk_seconds
        "subtitles_root": str(ROOT / "runtime" / "library" / "subtitles"),
        "model_store_root": str(ROOT / "runtime" / "model_store"),
    }
    (job_dir / "config.json").write_text(
        json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    status = TuningJobStatus(
        job_id=job_id,
        status="pending",
        model_id=req.model_id,
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
        # Označ Pareto optimální body
        _mark_pareto(status.results)
        # Najdi nejlepší trial (nejnižší WER s RTF < 1.2)
        candidates = [r for r in status.results if r.wer is not None and (r.rtf is None or r.rtf <= 1.2)]
        if candidates:
            best = min(candidates, key=lambda r: r.wer)  # type: ignore
            status.best_trial_idx = best.trial_idx
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


def _write_status(job_dir: Path, status: TuningJobStatus) -> None:
    (job_dir / "status.json").write_text(
        status.model_dump_json(indent=2), encoding="utf-8"
    )


def _generate_trials(req: TuningJobRequest) -> list[dict]:
    """Generuje seznam trial konfigurací dle strategie."""
    import itertools, random

    # Separuj chunk_seconds z param_space (speciální parametr)
    chunk_space = None
    model_param_space = []
    for ps in req.param_space:
        if ps.name == "chunk_seconds":
            chunk_space = ps.values
        else:
            model_param_space.append(ps)

    chunk_values = chunk_space or [req.baseline_params.get("chunk_seconds", 30)]

    if req.strategy == "grid":
        param_names = [ps.name for ps in model_param_space]
        param_values = [ps.values for ps in model_param_space]
        combos = list(itertools.product(*param_values)) if param_values else [()]
        trials = []
        for combo in combos:
            params = dict(req.baseline_params)
            for name, val in zip(param_names, combo):
                params[name] = val
            for cs in chunk_values:
                trials.append({**params, "_chunk_seconds": cs})

    elif req.strategy == "ablation":
        # Baseline + vary každý parametr zvlášť
        baseline = dict(req.baseline_params)
        trials = [{**baseline, "_chunk_seconds": chunk_values[0]}]
        for ps in model_param_space:
            for val in ps.values:
                if val == baseline.get(ps.name):
                    continue
                t = {**baseline, ps.name: val, "_chunk_seconds": chunk_values[0]}
                trials.append(t)
        for cs in chunk_values[1:]:
            trials.append({**baseline, "_chunk_seconds": cs})

    else:  # random
        param_names = [ps.name for ps in model_param_space]
        param_values = [ps.values for ps in model_param_space]
        all_combos = list(itertools.product(*param_values, chunk_values)) if param_values else [(cs,) for cs in chunk_values]
        random.shuffle(all_combos)
        trials = []
        for combo in all_combos[:req.max_trials]:
            params = dict(req.baseline_params)
            for name, val in zip(param_names, combo[:-1]):
                params[name] = val
            params["_chunk_seconds"] = combo[-1]
            trials.append(params)

    return trials[:req.max_trials]


def _mark_pareto(results: list[TuningTrialResult]) -> None:
    """Označí Pareto-optimální body (minimalizace WER a RTF)."""
    valid = [(r, r.wer, r.rtf) for r in results if r.wer is not None]
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
