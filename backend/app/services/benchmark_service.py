"""
Benchmark job management: create, track, run (subprocess), cancel.

Architektura:
  Každý run = samostatný subprocess (scripts/benchmark_worker.py)
  Crash modelu (qwen bfloat16 → NTSTATUS 0xC0000005) nezabije backend
  CPU/RAM měřeno jen pro STT subprocess přes psutil
  Komunikace přes soubory: {jobs_root}/{job_id}/config.json + progress.json + worker_result.json
"""
from __future__ import annotations

import json
import subprocess
import sys
import threading
import time
import traceback
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from ..config import JOBS_ROOT, RUNS_ROOT, SUBTITLES_ROOT, SCENARIOS_ROOT
from ..models.benchmark import BenchmarkJobRequest, BenchmarkJobStatus, LiveJobProgress
from . import library_service

try:
    import psutil
    HAS_PSUTIL = True
except ImportError:
    HAS_PSUTIL = False

_jobs: dict[str, dict] = {}
_lock = threading.Lock()

# HW série per job — posledních 120 vzorků (60s při 0.5s intervalu)
_job_hw_series: dict[str, list[dict]] = {}
_MAX_HW_SERIES = 120

_WORKER = Path(__file__).parent.parent.parent.parent / "scripts" / "benchmark_worker.py"

DEFAULT_MODELS = [
    {"id": "whisper_cpp_base",      "label": "whisper.cpp base"},
    {"id": "whisper_cpp_small",     "label": "whisper.cpp small"},
    {"id": "whisper_cpp_large_v3",  "label": "whisper.cpp large-v3"},
    {"id": "sherpa_onnx_small",     "label": "sherpa-onnx small"},
    {"id": "vosk_small_cs_0_4",     "label": "VOSK small cs-0.4"},
    {"id": "qwen3_asr_0_6b",        "label": "Qwen3-ASR 0.6B"},
    {"id": "qwen3_asr_1_7b",        "label": "Qwen3-ASR 1.7B"},
]

DEFAULT_SETTINGS = [
    {"id": "low_latency",   "label": "Low latency"},
    {"id": "balanced",      "label": "Balanced"},
    {"id": "high_accuracy", "label": "High accuracy"},
    {"id": "memory_saver",  "label": "Memory saver"},
]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_options() -> dict:
    return {"models": DEFAULT_MODELS, "settings": DEFAULT_SETTINGS}


def create_job(req: BenchmarkJobRequest) -> BenchmarkJobStatus:
    job_id = f"job_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"
    now = datetime.now(timezone.utc).isoformat()
    job = {
        "job_id": job_id,
        "status": "pending",
        "label": req.label,
        "created_at": now,
        "started_at": None,
        "finished_at": None,
        "progress_message": None,
        "error": None,
        "run_id": None,
        "result_url": None,
        "conditions_clean": None,
        "video_ids": req.video_ids,
        "request": req.model_dump(),
    }
    with _lock:
        _jobs[job_id] = job
    _persist_job(job)
    return _to_status(job)


def list_jobs() -> list[BenchmarkJobStatus]:
    with _lock:
        jobs = list(_jobs.values())
    return [_to_status(j) for j in sorted(jobs, key=lambda j: j["created_at"], reverse=True)]


def get_job(job_id: str) -> Optional[BenchmarkJobStatus]:
    with _lock:
        job = _jobs.get(job_id)
    return _to_status(job) if job else None


def get_live_progress(job_id: str) -> Optional[LiveJobProgress]:
    """Vrátí live data z running jobu: progress.json + aktuální HW série."""
    with _lock:
        job = _jobs.get(job_id)
    if not job:
        return None

    percent = 0
    message = job.get("progress_message") or ""
    updated_at = None
    progress_file = JOBS_ROOT / job_id / "progress.json"
    try:
        if progress_file.exists():
            data = json.loads(progress_file.read_text(encoding="utf-8"))
            percent = data.get("percent", 0)
            message = data.get("message", message)
            updated_at = data.get("updated_at")
    except Exception:
        pass

    with _lock:
        hw_series = list(_job_hw_series.get(job_id, []))

    return LiveJobProgress(
        job_id=job_id,
        status=job["status"],
        percent=percent,
        message=message,
        updated_at=updated_at,
        hw_series=hw_series,
    )


def cancel_job(job_id: str) -> Optional[BenchmarkJobStatus]:
    with _lock:
        job = _jobs.get(job_id)
        if job and job["status"] in ("pending", "running"):
            job["status"] = "cancelled"
            job["finished_at"] = datetime.now(timezone.utc).isoformat()
            # Subprocess kill je uložen v _subprocess_pids
            pid = _subprocess_pids.pop(job_id, None)
            if pid and HAS_PSUTIL:
                try:
                    psutil.Process(pid).kill()
                except Exception:
                    pass
    if job:
        _persist_job(job)
    return _to_status(job) if job else None


def run_job(job_id: str) -> None:
    """Spouští benchmark jako izolovaný subprocess. Voláno z BackgroundTasks."""
    with _lock:
        job = _jobs.get(job_id)
    if not job or job["status"] == "cancelled":
        return

    _update_job(job_id, status="running",
                started_at=datetime.now(timezone.utc).isoformat(),
                progress_message="Inicializace subprocess...")
    try:
        req_data = job["request"]
        sources = _resolve_sources(req_data)
        if not sources:
            raise ValueError("Žádné zdroje — zkontroluj video_ids nebo sources v požadavku")

        model_ids = req_data.get("model_ids") or [m["id"] for m in DEFAULT_MODELS]
        setting_ids = req_data.get("setting_ids") or [s["id"] for s in DEFAULT_SETTINGS]

        # Zapíše config pro worker subprocess
        job_dir = JOBS_ROOT / job_id
        job_dir.mkdir(parents=True, exist_ok=True)
        config = {
            "sources": sources,
            "model_ids": model_ids,
            "setting_ids": setting_ids,
            "sample_seconds": req_data.get("sample_seconds", 120),
            "evaluation_mode": req_data.get("evaluation_mode", "real"),
            "clip_strategy": req_data.get("clip_strategy", "random"),
            "clip_seed": req_data.get("clip_seed"),
            "runs_root": str(RUNS_ROOT),
            "subtitles_root": str(SUBTITLES_ROOT),
        }
        (job_dir / "config.json").write_text(
            json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8"
        )

        # Preflight — HW podmínky
        conditions_clean = _check_conditions_clean()
        _update_job(job_id, conditions_clean=conditions_clean)

        # Spusť subprocess
        proc = subprocess.Popen(
            [sys.executable, str(_WORKER), "--job-id", job_id, "--jobs-root", str(JOBS_ROOT)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        _subprocess_pids[job_id] = proc.pid
        _update_job(job_id, progress_message=f"Worker subprocess PID {proc.pid}")

        # Monitoruj progress + CPU/RAM v polling smyčce
        progress_file = job_dir / "progress.json"
        hw_samples: list[dict] = []
        _job_hw_series[job_id] = []
        while proc.poll() is None:
            _poll_progress(job_id, progress_file)
            if HAS_PSUTIL:
                sample = _sample_hw(proc.pid)
                hw_samples.append(sample)
                with _lock:
                    series = _job_hw_series.setdefault(job_id, [])
                    series.append({"cpu": sample.get("cpu"), "ram_mb": sample.get("ram_mb")})
                    if len(series) > _MAX_HW_SERIES:
                        series.pop(0)
            time.sleep(0.5)

        _subprocess_pids.pop(job_id, None)

        # Přečti výsledek
        result_file = job_dir / "worker_result.json"
        if not result_file.exists():
            raise RuntimeError("Worker skončil bez výsledku (pravděpodobný crash)")

        result = json.loads(result_file.read_text(encoding="utf-8"))
        if result["status"] != "completed":
            raise RuntimeError(result.get("error", "worker failed"))

        matrix_payload = result["payload"]

        # Přidej HW souhrn
        if hw_samples:
            matrix_payload["hw_summary"] = _summarize_hw(hw_samples)

        run_id = matrix_payload.get("run_id", "")

        # Auto-save výsledků do knihovny
        try:
            library_service.save_run_results(matrix_payload)
        except Exception:
            pass

        _update_job(job_id,
                    status="completed",
                    finished_at=datetime.now(timezone.utc).isoformat(),
                    run_id=run_id,
                    result_url=f"/api/runs/{run_id}",
                    progress_message="Hotovo")

    except Exception as exc:
        _update_job(job_id,
                    status="failed",
                    finished_at=datetime.now(timezone.utc).isoformat(),
                    error=f"{type(exc).__name__}: {exc}\n{traceback.format_exc()[-500:]}")


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

_subprocess_pids: dict[str, int] = {}


def _resolve_sources(req_data: dict) -> list[str]:
    video_ids = req_data.get("video_ids") or []
    if video_ids:
        items = {item.video_id: item.url for item in library_service.list_items()}
        return [items[vid] for vid in video_ids if vid in items]
    return req_data.get("sources") or []


def _check_conditions_clean() -> bool:
    if not HAS_PSUTIL:
        return True
    try:
        cpu = psutil.cpu_percent(interval=1.0)
        return cpu < 20.0
    except Exception:
        return True


def _sample_hw(pid: int) -> dict:
    sample: dict = {"ts": time.monotonic()}
    if not HAS_PSUTIL:
        return sample
    try:
        proc = psutil.Process(pid)
        sample["cpu"] = proc.cpu_percent(interval=None)
        sample["ram_mb"] = proc.memory_info().rss / 1024 / 1024
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        pass
    return sample


def _summarize_hw(samples: list[dict]) -> dict:
    cpus = [s["cpu"] for s in samples if "cpu" in s]
    rams = [s["ram_mb"] for s in samples if "ram_mb" in s]
    return {
        "cpu_avg": round(sum(cpus) / len(cpus), 1) if cpus else None,
        "cpu_peak": round(max(cpus), 1) if cpus else None,
        "ram_mb_avg": round(sum(rams) / len(rams), 1) if rams else None,
        "ram_mb_peak": round(max(rams), 1) if rams else None,
        "sample_count": len(samples),
    }


def _poll_progress(job_id: str, progress_file: Path) -> None:
    try:
        if progress_file.exists():
            data = json.loads(progress_file.read_text(encoding="utf-8"))
            _update_job(job_id, progress_message=data.get("message"))
    except Exception:
        pass


def _to_status(job: dict) -> BenchmarkJobStatus:
    return BenchmarkJobStatus(
        job_id=job["job_id"],
        status=job["status"],
        label=job.get("label"),
        created_at=job["created_at"],
        started_at=job.get("started_at"),
        finished_at=job.get("finished_at"),
        progress_message=job.get("progress_message"),
        error=job.get("error"),
        run_id=job.get("run_id"),
        result_url=job.get("result_url"),
        conditions_clean=job.get("conditions_clean"),
        video_ids=job.get("video_ids"),
    )


def _update_job(job_id: str, **kwargs) -> None:
    with _lock:
        job = _jobs.get(job_id)
        if job:
            job.update(kwargs)
    if job:
        _persist_job(job)


def _persist_job(job: dict) -> None:
    try:
        path = JOBS_ROOT / f"{job['job_id']}.json"
        path.write_text(json.dumps(job, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass


def _load_persisted_jobs() -> None:
    for f in JOBS_ROOT.glob("job_*.json"):
        try:
            job = json.loads(f.read_text(encoding="utf-8"))
            if job.get("status") in ("pending", "running"):
                job["status"] = "failed"
                job["error"] = "Server restarted during run"
            _jobs[job["job_id"]] = job
        except Exception:
            pass


_load_persisted_jobs()
