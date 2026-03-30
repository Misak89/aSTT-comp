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

from ..config import JOBS_ROOT, RUNS_ROOT, SUBTITLES_ROOT, SCENARIOS_ROOT, MODEL_STORE_ROOT
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

# Historie progress zpráv per job (req 1: zprávy nesmí zmizet)
_job_message_log: dict[str, list[str]] = {}
_MAX_LOG = 100

_WORKER = Path(__file__).parent.parent.parent.parent / "scripts" / "benchmark_worker.py"

DEFAULT_MODELS = [
    {"id": "whisper_cpp_base",      "label": "whisper.cpp base"},
    {"id": "whisper_cpp_small",     "label": "whisper.cpp small"},
    {"id": "whisper_cpp_large_v3",        "label": "whisper.cpp large-v3"},
    {"id": "whisper_cpp_large_v3_turbo", "label": "whisper.cpp large-v3-turbo"},
    {"id": "sherpa_onnx_small",     "label": "sherpa-onnx small"},
    {"id": "vosk_small_cs_0_4",     "label": "VOSK small cs-0.4"},
    {"id": "faster_whisper_small_cs_int8", "label": "faster-whisper small (CZ int8)"},
    {"id": "faster_whisper_medium_cs_int8", "label": "faster-whisper medium (CZ int8)"},
    {"id": "qwen3_asr_0_6b",        "label": "Qwen3-ASR 0.6B"},
    {"id": "qwen3_asr_1_7b",        "label": "Qwen3-ASR 1.7B"},
]

DEFAULT_SETTINGS = [
    {"id": "low_latency",   "label": "Low latency (15s)",   "chunk_seconds": 15,  "threads": 4, "beam_size": 1,  "no_fallback": True},
    {"id": "balanced",      "label": "Balanced (30s)",      "chunk_seconds": 30,  "threads": 4, "beam_size": 5,  "no_fallback": True},
    {"id": "high_accuracy", "label": "High accuracy (60s)", "chunk_seconds": 60,  "threads": 4, "beam_size": 5,  "no_fallback": False},
    {"id": "memory_saver",  "label": "Memory saver (30s)",  "chunk_seconds": 30,  "threads": 2, "beam_size": 1,  "no_fallback": True},
]

# Rychlý lookup id → params
SETTING_PARAMS: dict[str, dict] = {s["id"]: s for s in DEFAULT_SETTINGS}


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
        "model_params_used": req.model_params or {},
        "pre_cpu": None,
        "pre_ram_mb": None,
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
    """Vrátí live data z running jobu: progress.json + aktuální HW série + transcript."""
    with _lock:
        job = _jobs.get(job_id)
    if not job:
        return None

    percent = 0
    message = job.get("progress_message") or ""
    updated_at = None
    transcript = ""
    transcript_ts = ""
    progress_file = JOBS_ROOT / job_id / "progress.json"
    try:
        if progress_file.exists():
            data = json.loads(progress_file.read_text(encoding="utf-8"))
            percent = data.get("percent", 0)
            message = data.get("message", message)
            updated_at = data.get("updated_at")
            transcript = data.get("transcript", "")
            transcript_ts = data.get("transcript_ts", "")
    except Exception:
        pass

    # Po dokončení: transcript je uložen přímo v job dictu (nastaven v run_job při completion)
    if not transcript and job.get("status") == "completed":
        transcript = job.get("transcript", "")
    # Fallback pro starší joby (transcript nebyl v job dictu): čti z worker_result.json
    if not transcript and job.get("status") == "completed":
        result_file = JOBS_ROOT / job_id / "worker_result.json"
        if result_file.exists():
            rdata = json.loads(result_file.read_bytes().decode("utf-8", errors="replace"))
            for r in rdata.get("payload", {}).get("results", []):
                for sm in r.get("source_metrics", []):
                    t = sm.get("transcript_text", "")
                    if t:
                        transcript = t
                        break
                if not transcript:
                    transcript = r.get("transcript_text", "")
                if transcript:
                    break

    with _lock:
        hw_series = list(_job_hw_series.get(job_id, []))
        message_log = list(_job_message_log.get(job_id, []))

    return LiveJobProgress(
        job_id=job_id,
        status=job["status"],
        percent=percent,
        message=message,
        message_log=message_log,
        updated_at=updated_at,
        hw_series=hw_series,
        transcript=transcript,
        transcript_ts=transcript_ts,
        pre_cpu=job.get("pre_cpu"),
        pre_ram_mb=job.get("pre_ram_mb"),
        model_params_used=job.get("model_params_used") or {},
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

        # Sestaví settings s parametry — každé setting má vlastní chunk_seconds atd.
        settings_with_params = []
        for sid in setting_ids:
            base = SETTING_PARAMS.get(sid, {"id": sid, "label": sid, "chunk_seconds": 30, "threads": 4})
            settings_with_params.append(base)

        # Zapíše config pro worker subprocess
        job_dir = JOBS_ROOT / job_id
        job_dir.mkdir(parents=True, exist_ok=True)
        config = {
            "sources": sources,
            "model_ids": model_ids,
            "settings": settings_with_params,
            "sample_seconds": req_data.get("sample_seconds", 120),
            "evaluation_mode": req_data.get("evaluation_mode", "synthetic"),
            "clip_strategy": req_data.get("clip_strategy", "random"),
            "clip_seed": req_data.get("clip_seed"),
            "runs_root": str(RUNS_ROOT),
            "subtitles_root": str(SUBTITLES_ROOT),
            "model_store_root": str(MODEL_STORE_ROOT),
            "model_params": req_data.get("model_params") or {},
        }
        # Inicializuj message log pro tento job (req 1)
        with _lock:
            _job_message_log[job_id] = []
        (job_dir / "config.json").write_text(
            json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8"
        )

        # Preflight — HW podmínky + pre-sample (req 3)
        conditions_clean, pre_cpu, pre_ram_mb = _check_conditions_and_sample()
        _update_job(job_id, conditions_clean=conditions_clean,
                    pre_cpu=pre_cpu, pre_ram_mb=pre_ram_mb)

        # Spusť subprocess
        import os as _os
        env = {**_os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}
        proc = subprocess.Popen(
            [sys.executable, str(_WORKER), "--job-id", job_id, "--jobs-root", str(JOBS_ROOT)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
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
                sample = _sample_hw(job_id, proc.pid)
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

        # Extrahuj transcript z výsledků a ulož do job dictu (spolehlivější než číst soubor v get_live_progress)
        transcript_parts = []
        for r in matrix_payload.get("results", []):
            for sm in r.get("source_metrics", []):
                t = sm.get("transcript_text", "")
                if t:
                    transcript_parts.append(t)
            t = r.get("transcript_text", "")
            if t and t not in transcript_parts:
                transcript_parts.append(t)
        job_transcript = "\n\n---\n\n".join(transcript_parts)

        _update_job(job_id,
                    status="completed",
                    finished_at=datetime.now(timezone.utc).isoformat(),
                    run_id=run_id,
                    result_url=f"/api/runs/{run_id}",
                    progress_message="Hotovo",
                    transcript=job_transcript)

    except Exception as exc:
        _update_job(job_id,
                    status="failed",
                    finished_at=datetime.now(timezone.utc).isoformat(),
                    error=f"{type(exc).__name__}: {exc}\n{traceback.format_exc()[-500:]}")


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

_subprocess_pids: dict[str, int] = {}
# Cache psutil.Process objektů per job — cpu_percent(interval=None) vrací 0 na prvním volání
# na novém objektu; musíme reusovat stejný objekt
_job_hw_procs: dict = {}  # job_id -> psutil.Process (or None)


def _resolve_sources(req_data: dict) -> list[str]:
    video_ids = req_data.get("video_ids") or []
    if video_ids:
        items = {item.video_id: item.url for item in library_service.list_items()}
        return [items[vid] for vid in video_ids if vid in items]
    return req_data.get("sources") or []


def _check_conditions_and_sample() -> tuple[bool, Optional[float], Optional[float]]:
    """Vrátí (conditions_clean, pre_cpu%, pre_ram_mb) před startem benchmarku."""
    if not HAS_PSUTIL:
        return True, None, None
    try:
        cpu = psutil.cpu_percent(interval=1.0)
        ram_mb = psutil.virtual_memory().used / 1024 / 1024
        return cpu < 20.0, round(cpu, 1), round(ram_mb, 1)
    except Exception:
        return True, None, None


def _sample_hw(job_id: str, pid: int) -> dict:
    """Měří CPU% a RAM pro subprocess.
    psutil.cpu_percent(interval=None) VŽDY vrací 0.0 při prvním volání na novém Process objektu —
    proto cachujeme objekt a první volání slouží jen jako baseline."""
    sample: dict = {"ts": time.monotonic()}
    if not HAS_PSUTIL:
        return sample
    try:
        proc = _job_hw_procs.get(job_id)
        if proc is None or not proc.is_running() or proc.pid != pid:
            proc = psutil.Process(pid)
            _job_hw_procs[job_id] = proc
            proc.cpu_percent(interval=None)  # baseline — první volání vždy vrátí 0, zahodíme
            sample["ram_mb"] = proc.memory_info().rss / 1024 / 1024
            return sample  # cpu ještě nemáme, vrátíme jen RAM
        sample["cpu"] = proc.cpu_percent(interval=None)
        sample["ram_mb"] = proc.memory_info().rss / 1024 / 1024
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        _job_hw_procs.pop(job_id, None)
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
            msg = data.get("message")
            percent = data.get("percent", 0)
            kwargs: dict = {}
            if msg:
                kwargs["progress_message"] = msg
            if percent:
                kwargs["progress_percent"] = percent
            if kwargs:
                _update_job(job_id, **kwargs)
            if msg:
                # Req 1: přidej zprávu do logu s timestampem (deduplikuj po sobě jdoucí)
                ts = datetime.now().strftime("%H:%M:%S")
                stamped = f"[{ts}] {msg}"
                with _lock:
                    log = _job_message_log.setdefault(job_id, [])
                    last_raw = log[-1].split("] ", 1)[-1] if log else ""
                    if last_raw != msg:
                        log.append(stamped)
                        if len(log) > _MAX_LOG:
                            log.pop(0)
                        # Perzistuj log na disk
                        try:
                            log_path = JOBS_ROOT / job_id / "log.txt"
                            with log_path.open("a", encoding="utf-8") as lf:
                                lf.write(stamped + "\n")
                        except Exception:
                            pass
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
        progress_percent=job.get("progress_percent", 0),
        error=job.get("error"),
        run_id=job.get("run_id"),
        result_url=job.get("result_url"),
        conditions_clean=job.get("conditions_clean"),
        pre_cpu=job.get("pre_cpu"),
        pre_ram_mb=job.get("pre_ram_mb"),
        video_ids=job.get("video_ids"),
        evaluation_mode=job.get("request", {}).get("evaluation_mode"),
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
