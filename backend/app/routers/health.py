from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Query

router = APIRouter()

_STARTED_AT = datetime.now(timezone.utc).isoformat()
_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_SPECSTORY_STATUS_PATH = _PROJECT_ROOT / "runtime" / "specstory_live_status.json"
_LOGGER_SCRIPT_REL = "scripts/log_cmd.py"
_LOGGER_LOGS_DIR_REL = "logs"
_LOGGER_LOG_FILE_REL = "logs/cmd.jsonl"
_LOGGER_REPO_URL = "https://github.com/Misak89/process-logger"
_APP_MARKERS = (
    str(_PROJECT_ROOT).lower(),
    "backend.app.main:app",
    "start_web_app",
    "specstory_live_loop.py",
    "start_specstory_live_loop.ps1",
    "web-up-bg.cmd",
    "astt-comp",
)
_CPU_SAMPLE_LOCK = threading.Lock()
_CPU_SAMPLE_CACHE: dict[int, tuple[float, float]] = {}
_CPU_LOGICAL_CORES = max(1, os.cpu_count() or 1)
_PROC_META_LOCK = threading.Lock()
_PROC_META_CACHE: dict[int, dict[str, Any]] = {}
_PROC_META_TTL_FAST_S = 8.0
_PROC_META_TTL_SLOW_S = 20.0
_PROCESS_PROFILES: tuple[dict[str, Any], ...] = (
    {"id": "backend_uvicorn", "label": "Backend Uvicorn", "expected": True, "cmd_tokens": ("backend.app.main:app",)},
    {"id": "benchmark_worker", "label": "Benchmark Worker", "expected": True, "cmd_tokens": ("scripts\\benchmark_worker.py", "scripts/benchmark_worker.py")},
    {"id": "tuning_worker", "label": "Tuning Worker", "expected": True, "cmd_tokens": ("scripts\\tuning_worker.py", "scripts/tuning_worker.py")},
    {"id": "load_generator", "label": "Load Generator", "expected": True, "cmd_tokens": ("scripts\\load_generator.py", "scripts/load_generator.py")},
    {"id": "specstory_loop", "label": "SpecStory Loop", "expected": True, "cmd_tokens": ("scripts\\specstory_live_loop.py", "scripts/specstory_live_loop.py")},
    {"id": "specstory_learning", "label": "SpecStory Learning", "expected": True, "cmd_tokens": ("scripts\\specstory_failure_learning.py", "scripts/specstory_failure_learning.py")},
    {"id": "cmd_logger_windows", "label": "Process Logger (Windows)", "expected": True, "cmd_tokens": ("scripts\\log_cmd.py", "scripts/log_cmd.py")},
    {"id": "cmd_logger_unix", "label": "Process Logger (Linux/macOS)", "expected": True, "cmd_tokens": ("scripts\\log_cmd_unix.py", "scripts/log_cmd_unix.py")},
    {"id": "whisper_server", "label": "Whisper Server", "expected": True, "name_tokens": ("whisper-server.exe", "whisper-server"), "cmd_tokens": ("whisper-server",)},
    {"id": "whisper_cli", "label": "Whisper CLI", "expected": True, "name_tokens": ("whisper-cli.exe", "whisper-cli"), "cmd_tokens": ("whisper-cli",)},
    {"id": "ffmpeg", "label": "FFmpeg", "expected": True, "name_tokens": ("ffmpeg",), "cmd_tokens": ("ffmpeg",)},
    {"id": "yt_dlp", "label": "yt-dlp", "expected": True, "cmd_tokens": ("yt_dlp", "yt-dlp")},
    {"id": "frontend_build", "label": "Frontend Build (Node/Vite)", "expected": True, "name_tokens": ("node",), "cmd_tokens": ("vite", "esbuild", "npm --prefix frontend")},
    {"id": "shell_wrapper", "label": "Shell Wrapper (CMD/PowerShell)", "expected": True, "name_tokens": ("cmd.exe", "powershell.exe", "pwsh.exe"), "cmd_tokens": ("start_web_app", "web-up-bg.cmd", "web-down.cmd", "web-status.cmd")},
    {"id": "other_app_process", "label": "Other aSTT-comp process", "expected": False},
)
_PROCESS_PROFILE_BY_ID: dict[str, dict[str, Any]] = {
    str(profile["id"]): profile for profile in _PROCESS_PROFILES
}


@router.get("/api/health")
def health():
    ram = {}
    cpu = {}
    try:
        import psutil
        vm = psutil.virtual_memory()
        ram = {
            "ram_total_mb": round(vm.total / 1024**2),
            "ram_used_mb": round(vm.used / 1024**2),
            "ram_percent": vm.percent,
        }
        try:
            cpu = {
                "cpu_percent": float(psutil.cpu_percent(interval=None)),
            }
        except Exception:
            cpu = {}
    except Exception:
        pass
    return {
        "status": "ok",
        "utc": datetime.now(timezone.utc).isoformat(),
        "started_at": _STARTED_AT,
        "logger": {
            "repo_url": _LOGGER_REPO_URL,
            "script_rel_path": _LOGGER_SCRIPT_REL,
            "logs_dir_rel_path": _LOGGER_LOGS_DIR_REL,
            "log_file_rel_path": _LOGGER_LOG_FILE_REL,
            "open_logs_dir_api": "/api/open-dir/logger_logs",
        },
        **ram,
        **cpu,
    }


def _parse_utc(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
    except Exception:
        return None


def _to_utc_iso_from_ts(ts: float | None) -> str | None:
    if ts is None:
        return None
    try:
        return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    except Exception:
        return None


def _proc_matches_app(exe: str, cmdline: list[str]) -> bool:
    exe_low = (exe or "").lower()
    cmd_join = " ".join(cmdline).lower()
    for marker in _APP_MARKERS:
        if marker in exe_low or marker in cmd_join:
            return True
    return False


def _match_profile(name: str, exe: str, cmd_join: str) -> dict[str, Any] | None:
    name_low = (name or "").lower()
    exe_low = (exe or "").lower()
    cmd_low = (cmd_join or "").lower()
    for profile in _PROCESS_PROFILES:
        if profile["id"] == "other_app_process":
            continue
        cmd_tokens = tuple(str(t).lower() for t in profile.get("cmd_tokens", ()))
        name_tokens = tuple(str(t).lower() for t in profile.get("name_tokens", ()))
        cmd_match = bool(cmd_tokens) and any(token in cmd_low for token in cmd_tokens)
        name_match = bool(name_tokens) and any(token in name_low or token in exe_low for token in name_tokens)
        if cmd_match or name_match:
            return profile
    return None


def _compute_cpu_percent(pid: int, cpu_total_s: float | None, now_ts: float) -> float | None:
    if cpu_total_s is None:
        return None
    with _CPU_SAMPLE_LOCK:
        prev = _CPU_SAMPLE_CACHE.get(pid)
        _CPU_SAMPLE_CACHE[pid] = (cpu_total_s, now_ts)
    if prev is None:
        return None
    prev_cpu, prev_ts = prev
    dt = now_ts - prev_ts
    if dt <= 0:
        return None
    dcpu = max(0.0, cpu_total_s - prev_cpu)
    cpu_pct = (dcpu / dt) * 100.0 / _CPU_LOGICAL_CORES
    if cpu_pct < 0:
        return 0.0
    return round(min(cpu_pct, 100.0), 1)


def _cleanup_cpu_cache(current_pids: set[int]) -> None:
    with _CPU_SAMPLE_LOCK:
        stale = [pid for pid in _CPU_SAMPLE_CACHE.keys() if pid not in current_pids]
        for pid in stale:
            _CPU_SAMPLE_CACHE.pop(pid, None)


def _get_proc_meta_cache_ttl(mode: str) -> float:
    if mode == "fast":
        return _PROC_META_TTL_FAST_S
    return _PROC_META_TTL_SLOW_S


def _get_cached_process_meta(pid: int, created: float | None, now_ts: float) -> dict[str, Any] | None:
    with _PROC_META_LOCK:
        cached = _PROC_META_CACHE.get(pid)
        if not cached:
            return None
        expires_at = float(cached.get("expires_at") or 0.0)
        if expires_at <= now_ts:
            _PROC_META_CACHE.pop(pid, None)
            return None
        cached_created = cached.get("created")
        if created is not None and isinstance(cached_created, (int, float)) and abs(float(cached_created) - created) > 1e-3:
            _PROC_META_CACHE.pop(pid, None)
            return None
        return dict(cached)


def _put_cached_process_meta(
    *,
    pid: int,
    created: float | None,
    now_ts: float,
    ttl_s: float,
    cmdline: list[str],
    cmd_join: str,
    exe: str,
    profile_id: str,
) -> None:
    with _PROC_META_LOCK:
        _PROC_META_CACHE[pid] = {
            "created": created,
            "expires_at": now_ts + max(1.0, ttl_s),
            "cmdline": list(cmdline),
            "cmd_join": str(cmd_join),
            "exe": str(exe),
            "profile_id": str(profile_id),
        }


def _cleanup_proc_meta_cache(current_pids: set[int], now_ts: float) -> None:
    with _PROC_META_LOCK:
        stale = [
            pid
            for pid, cached in _PROC_META_CACHE.items()
            if pid not in current_pids or float(cached.get("expires_at") or 0.0) <= now_ts
        ]
        for pid in stale:
            _PROC_META_CACHE.pop(pid, None)


def _query_gpu_snapshot() -> dict[str, Any]:
    if shutil.which("nvidia-smi") is None:
        return {"provider": None, "total_util_percent": None, "pid_memory_mb": {}}

    provider = "nvidia-smi"
    total_util: float | None = None
    pid_memory_mb: dict[int, float] = {}

    try:
        util_proc = subprocess.run(
            ["nvidia-smi", "--query-gpu=utilization.gpu", "--format=csv,noheader,nounits"],
            capture_output=True,
            text=True,
            timeout=1.5,
            check=False,
        )
        if util_proc.returncode == 0:
            vals: list[float] = []
            for raw in (util_proc.stdout or "").splitlines():
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    vals.append(float(raw))
                except Exception:
                    continue
            if vals:
                total_util = round(sum(vals) / len(vals), 1)
    except Exception:
        total_util = None

    try:
        apps_proc = subprocess.run(
            ["nvidia-smi", "--query-compute-apps=pid,used_memory", "--format=csv,noheader,nounits"],
            capture_output=True,
            text=True,
            timeout=1.5,
            check=False,
        )
        if apps_proc.returncode == 0:
            for raw in (apps_proc.stdout or "").splitlines():
                parts = [p.strip() for p in raw.split(",")]
                if len(parts) < 2:
                    continue
                try:
                    pid = int(parts[0])
                    used_mb = float(parts[1])
                    pid_memory_mb[pid] = used_mb
                except Exception:
                    continue
    except Exception:
        pass

    return {"provider": provider, "total_util_percent": total_util, "pid_memory_mb": pid_memory_mb}


def _read_pid_file(path: Path) -> int | None:
    try:
        raw = path.read_text(encoding="utf-8").strip()
        if not raw or not raw.isdigit():
            return None
        return int(raw)
    except Exception:
        return None


def _pid_file_warnings() -> list[str]:
    warnings: list[str] = []
    try:
        import psutil
    except Exception:
        return warnings

    pid_files = (
        _PROJECT_ROOT / "runtime" / "specstory_live_loop.pid",
        _PROJECT_ROOT / "runtime" / "logs" / "cmd.pid",
        _PROJECT_ROOT / "logs" / "cmd.pid",
    )

    for pid_file in pid_files:
        if not pid_file.exists():
            continue
        pid = _read_pid_file(pid_file)
        if pid is None:
            warnings.append(f"stale pid file (invalid content): {pid_file}")
            continue
        if not psutil.pid_exists(pid):
            warnings.append(f"stale pid file (missing process): {pid_file} -> {pid}")
    return warnings


def _cleanup_stale_pid_files() -> dict[str, list[str]]:
    removed: list[str] = []
    kept: list[str] = []
    errors: list[str] = []

    try:
        import psutil
    except Exception:
        psutil = None

    pid_files = (
        _PROJECT_ROOT / "runtime" / "specstory_live_loop.pid",
        _PROJECT_ROOT / "runtime" / "logs" / "cmd.pid",
        _PROJECT_ROOT / "logs" / "cmd.pid",
    )

    for pid_file in pid_files:
        if not pid_file.exists():
            continue
        pid = _read_pid_file(pid_file)
        should_remove = False
        if pid is None:
            should_remove = True
        elif psutil is not None and not psutil.pid_exists(pid):
            should_remove = True

        if should_remove:
            try:
                pid_file.unlink(missing_ok=True)
                removed.append(str(pid_file))
            except Exception as exc:
                errors.append(f"{pid_file}: {exc}")
        else:
            kept.append(str(pid_file))

    return {"removed": removed, "kept": kept, "errors": errors}


def _backend_pid_on_port_8012() -> int | None:
    try:
        import psutil

        for conn in psutil.net_connections(kind="tcp"):
            if not conn.laddr:
                continue
            if int(conn.laddr.port) != 8012:
                continue
            if str(conn.status).upper() != "LISTEN":
                continue
            if conn.pid:
                return int(conn.pid)
    except Exception:
        return None
    return None


def _collect_root_pids() -> set[int]:
    try:
        import psutil
    except Exception:
        psutil = None

    roots: set[int] = set()
    backend_pid = _backend_pid_on_port_8012()
    if backend_pid:
        roots.add(backend_pid)

    for pid_file in (
        _PROJECT_ROOT / "runtime" / "specstory_live_loop.pid",
        _PROJECT_ROOT / "runtime" / "logs" / "cmd.pid",
        _PROJECT_ROOT / "logs" / "cmd.pid",
    ):
        pid = _read_pid_file(pid_file)
        if not pid:
            continue
        if psutil is None or not psutil.pid_exists(pid):
            continue
        try:
            proc = psutil.Process(pid)
            cmdline = [str(x) for x in (proc.cmdline() or [])]
            exe = str(proc.exe() or "")
            if _proc_matches_app(exe=exe, cmdline=cmdline):
                roots.add(pid)
        except Exception:
            continue
    return roots


def _collect_tree_pids(root_pids: set[int]) -> set[int]:
    try:
        import psutil
    except Exception:
        return set()
    all_pids = set(root_pids)
    for root_pid in list(root_pids):
        try:
            proc = psutil.Process(root_pid)
            for child in proc.children(recursive=True):
                all_pids.add(int(child.pid))
        except Exception:
            continue
    return all_pids


def _collect_parent_chain_pids(root_pids: set[int], *, max_depth: int = 6) -> set[int]:
    try:
        import psutil
    except Exception:
        return set()
    parents: set[int] = set()
    for root_pid in root_pids:
        current_pid = int(root_pid)
        depth = 0
        while current_pid > 0 and depth < max_depth:
            try:
                proc = psutil.Process(current_pid)
                ppid = int(proc.ppid())
            except Exception:
                break
            if ppid <= 0:
                break
            parents.add(ppid)
            current_pid = ppid
            depth += 1
    return parents


def _collect_marker_matched_pids() -> set[int]:
    """Najde procesy spojené s app mimo root-child strom (cílený scan podle jmen)."""
    try:
        import psutil
    except Exception:
        return set()

    matched: set[int] = set()
    candidate_names = {
        "python.exe",
        "pythonw.exe",
        "cmd.exe",
        "powershell.exe",
        "pwsh.exe",
        "node.exe",
        "ffmpeg.exe",
        "whisper-cli.exe",
        "whisper-server.exe",
    }

    for proc in psutil.process_iter(["pid", "name"]):
        try:
            pid = int(proc.info.get("pid") or 0)
            name = str(proc.info.get("name") or "").lower()
            if pid <= 0 or name not in candidate_names:
                continue
            cmdline = [str(x) for x in (proc.cmdline() or [])]
            if not cmdline:
                continue
            exe = ""
            try:
                exe = str(proc.exe() or "")
            except Exception:
                pass
            if _proc_matches_app(exe=exe, cmdline=cmdline):
                matched.add(pid)
        except Exception:
            continue
    return matched


@router.get("/api/health/processes")
def app_processes_health(mode: str = Query(default="fast")) -> dict[str, Any]:
    mode_norm = str(mode or "fast").strip().lower()
    if mode_norm not in {"fast", "slow", "full"}:
        mode_norm = "fast"
    include_marker_scan = mode_norm in {"slow", "full"}
    include_gpu_scan = mode_norm in {"slow", "full"}
    proc_meta_ttl_s = _get_proc_meta_cache_ttl(mode_norm)

    now = datetime.now(timezone.utc)
    now_ts = now.timestamp()
    try:
        import psutil
    except Exception:
        return {
            "status": "unavailable",
            "updated_at_utc": now.isoformat(),
            "count": 0,
            "processes": [],
            "message": "psutil not available",
        }

    root_pids = _collect_root_pids()
    scoped_pids = _collect_tree_pids(root_pids)
    scoped_pids |= _collect_parent_chain_pids(root_pids)
    if include_marker_scan:
        scoped_pids |= _collect_marker_matched_pids()
    processes: list[dict[str, Any]] = []
    gpu_snapshot = _query_gpu_snapshot() if include_gpu_scan else {"provider": None, "total_util_percent": None, "pid_memory_mb": {}}
    gpu_by_pid: dict[int, float] = gpu_snapshot.get("pid_memory_mb", {}) or {}
    zombie_count = 0
    warnings = _pid_file_warnings()
    current_observed_pids: set[int] = set()

    for pid in sorted(scoped_pids):
        try:
            proc = psutil.Process(pid)
            name = str(proc.name() or "")
            status = str(proc.status() or "")
            try:
                created = float(proc.create_time())
                if created <= 0:
                    created = None
            except Exception:
                created = None
            cmdline: list[str] = []
            cmd_join = ""
            exe = ""
            profile: dict[str, Any] | None = None
            cached_meta = _get_cached_process_meta(pid=pid, created=created, now_ts=now_ts)
            if cached_meta:
                cmdline_raw = cached_meta.get("cmdline") or []
                if isinstance(cmdline_raw, list):
                    cmdline = [str(x) for x in cmdline_raw]
                cmd_join = str(cached_meta.get("cmd_join") or "")
                exe = str(cached_meta.get("exe") or "")
                profile_id = str(cached_meta.get("profile_id") or "")
                profile = _PROCESS_PROFILE_BY_ID.get(profile_id)

            if profile is None:
                profile = _match_profile(name=name, exe=exe, cmd_join=cmd_join)
            lower_name = name.lower()
            needs_cmdline = (
                profile is None
                or lower_name.startswith("python")
                or lower_name in {"cmd.exe", "powershell.exe", "pwsh.exe", "node.exe", "node"}
            )
            if needs_cmdline and not cached_meta:
                try:
                    cmdline_raw = proc.cmdline() or []
                except Exception:
                    cmdline_raw = []
                cmdline = [str(x) for x in cmdline_raw] if isinstance(cmdline_raw, list) else [str(cmdline_raw)]
                cmd_join = " ".join(cmdline).strip()
                try:
                    exe = str(proc.exe() or "")
                except Exception:
                    exe = ""
                if profile is None:
                    profile = _match_profile(name=name, exe=exe, cmd_join=cmd_join)
                _put_cached_process_meta(
                    pid=pid,
                    created=created,
                    now_ts=now_ts,
                    ttl_s=proc_meta_ttl_s,
                    cmdline=cmdline,
                    cmd_join=cmd_join,
                    exe=exe,
                    profile_id=str(profile["id"]) if profile else "other_app_process",
                )
            running_for = None
            if created is not None:
                running_for = max(0, int(now_ts - created))

            cpu_total_s: float | None = None
            try:
                cpu_times = proc.cpu_times()
                cpu_total_s = float(getattr(cpu_times, "user", 0.0) + getattr(cpu_times, "system", 0.0))
            except Exception:
                cpu_total_s = None
            cpu_percent = _compute_cpu_percent(pid=pid, cpu_total_s=cpu_total_s, now_ts=now_ts)

            ram_mb: float | None = None
            try:
                mem = proc.memory_info()
                ram_mb = round(float(getattr(mem, "rss", 0.0)) / 1024**2, 1)
            except Exception:
                ram_mb = None

            cmd_preview = cmd_join
            if len(cmd_preview) > 240:
                cmd_preview = f"{cmd_preview[:237]}..."

            status_low = status.lower()
            zombie_candidate = status_low in {"zombie", "dead"}
            if zombie_candidate:
                zombie_count += 1

            profile_id = profile["id"] if profile else "other_app_process"
            profile_label = profile["label"] if profile else "Other aSTT-comp process"
            current_observed_pids.add(pid)
            processes.append(
                {
                    "pid": pid,
                    "name": name,
                    "status": status,
                    "started_at_utc": _to_utc_iso_from_ts(created),
                    "running_for_seconds": running_for,
                    "exe": exe or None,
                    "cmdline_preview": cmd_preview,
                    "is_root": pid in root_pids,
                    "profile_id": profile_id,
                    "profile_label": profile_label,
                    "cpu_percent": cpu_percent,
                    "ram_mb": ram_mb,
                    "gpu_memory_mb": gpu_by_pid.get(pid),
                    "zombie_candidate": zombie_candidate,
                }
            )
        except Exception:
            # NoSuchProcess / AccessDenied / race conditions
            continue

    _cleanup_cpu_cache(current_observed_pids)
    _cleanup_proc_meta_cache(current_observed_pids, now_ts)
    processes.sort(key=lambda p: (p.get("started_at_utc") or "", p.get("pid") or 0))
    profiles = [
        {
            "id": str(profile["id"]),
            "label": str(profile["label"]),
            "expected": bool(profile.get("expected", True)),
        }
        for profile in _PROCESS_PROFILES
    ]

    if zombie_count > 0:
        warnings.append(f"zombie candidates detected: {zombie_count}")
    return {
        "status": "ok",
        "updated_at_utc": now.isoformat(),
        "scan_phase": mode_norm,
        "root_pids": sorted(root_pids),
        "count": len(processes),
        "processes": processes,
        "profiles": profiles,
        "zombie_count": zombie_count,
        "warnings": warnings,
        "gpu": {
            "provider": gpu_snapshot.get("provider"),
            "total_util_percent": gpu_snapshot.get("total_util_percent"),
        },
    }


@router.post("/api/health/processes/cleanup-stale-pids")
def cleanup_stale_pid_files() -> dict[str, Any]:
    result = _cleanup_stale_pid_files()
    return {
        "status": "ok",
        "removed_count": len(result["removed"]),
        "kept_count": len(result["kept"]),
        "error_count": len(result["errors"]),
        **result,
    }


@router.get("/api/health/specstory")
def specstory_health() -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    if not _SPECSTORY_STATUS_PATH.exists():
        return {
            "status": "missing",
            "source": str(_SPECSTORY_STATUS_PATH),
            "generated_at_utc": now.isoformat(),
            "message": "status file not found",
        }

    try:
        raw = json.loads(_SPECSTORY_STATUS_PATH.read_text(encoding="utf-8"))
    except Exception as exc:
        return {
            "status": "error",
            "source": str(_SPECSTORY_STATUS_PATH),
            "generated_at_utc": now.isoformat(),
            "message": f"failed to parse status file: {exc}",
        }

    generated_at = _parse_utc(str(raw.get("generated_at_utc") or ""))
    interval_seconds = int(raw.get("interval_seconds") or 0)
    stale_threshold = max(180, interval_seconds * 3) if interval_seconds > 0 else 180
    stale = False
    if generated_at is not None:
        stale = (now - generated_at).total_seconds() > stale_threshold

    last_exit_code = raw.get("last_exit_code")
    if stale:
        resolved = "stale"
    elif isinstance(last_exit_code, int) and last_exit_code != 0:
        resolved = "error"
    else:
        resolved = "ok"

    return {
        "status": resolved,
        "source": str(_SPECSTORY_STATUS_PATH),
        "generated_at_utc": raw.get("generated_at_utc"),
        "loop_started_utc": raw.get("loop_started_utc"),
        "loop_pid": raw.get("loop_pid"),
        "interval_seconds": interval_seconds or None,
        "last_run_utc": raw.get("last_run_utc"),
        "last_success_utc": raw.get("last_success_utc"),
        "last_exit_code": last_exit_code,
        "last_error": raw.get("last_error"),
        "history_files_count": raw.get("history_files_count"),
        "runs_total": raw.get("runs_total"),
        "runs_failed": raw.get("runs_failed"),
        "stale_seconds_threshold": stale_threshold,
    }
