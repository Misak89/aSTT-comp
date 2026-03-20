from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
import math
import os
import platform
from pathlib import Path
import subprocess
import sys
import threading
import time
from typing import Any
from urllib import error as url_error
from urllib import request as url_request

try:
    import psutil
except ModuleNotFoundError:
    psutil = None  # type: ignore[assignment]


DEFAULT_TIME_REFERENCE_URLS: tuple[str, ...] = (
    "https://www.cloudflare.com",
    "https://www.google.com",
)


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _round_or_none(value: float | None, digits: int = 3) -> float | None:
    if value is None or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        return None
    return round(float(value), digits)


def _safe_disk_counters() -> dict[str, float] | None:
    if psutil is None:
        return None
    try:
        counters = psutil.disk_io_counters()
    except Exception:
        return None
    if counters is None:
        return None
    return {
        "read_bytes": float(getattr(counters, "read_bytes", 0.0) or 0.0),
        "write_bytes": float(getattr(counters, "write_bytes", 0.0) or 0.0),
        "read_count": float(getattr(counters, "read_count", 0.0) or 0.0),
        "write_count": float(getattr(counters, "write_count", 0.0) or 0.0),
    }


def _safe_process_tree_stats(root_pid: int) -> dict[str, float | int]:
    if psutil is None:
        return {"process_count": 0, "cpu_seconds_total": 0.0, "rss_mb_total": 0.0}

    process_list: list[Any] = []
    try:
        root = psutil.Process(root_pid)
        process_list = [root]
        process_list.extend(root.children(recursive=True))
    except Exception:
        process_list = []

    cpu_seconds_total = 0.0
    rss_mb_total = 0.0
    alive_count = 0
    for proc in process_list:
        try:
            cpu_times = proc.cpu_times()
            mem = proc.memory_info().rss
        except Exception:
            continue
        alive_count += 1
        cpu_seconds_total += float(cpu_times.user + cpu_times.system)
        rss_mb_total += float(mem) / (1024 * 1024)

    return {
        "process_count": int(alive_count),
        "cpu_seconds_total": round(cpu_seconds_total, 4),
        "rss_mb_total": round(rss_mb_total, 3),
    }


def _sample_snapshot(*, root_pid: int) -> dict[str, Any]:
    if psutil is None:
        return {
            "timestamp_utc": _utc_now_iso(),
            "status": "unavailable",
            "reason": "psutil_missing",
        }

    now = _utc_now_iso()
    vm = psutil.virtual_memory()
    swap = psutil.swap_memory()
    cpu_percent = None
    try:
        cpu_percent = float(psutil.cpu_percent(interval=None))
    except Exception:
        cpu_percent = None

    load_avg = None
    if hasattr(psutil, "getloadavg"):
        try:
            load1, load5, load15 = psutil.getloadavg()
            load_avg = {
                "load1": _round_or_none(float(load1), 4),
                "load5": _round_or_none(float(load5), 4),
                "load15": _round_or_none(float(load15), 4),
            }
        except Exception:
            load_avg = None

    disk = _safe_disk_counters()
    process_tree = _safe_process_tree_stats(root_pid)
    payload: dict[str, Any] = {
        "timestamp_utc": now,
        "status": "ok",
        "cpu_percent_total": _round_or_none(cpu_percent, 2),
        "memory_percent": _round_or_none(float(vm.percent), 2),
        "memory_used_mb": _round_or_none(float(vm.used) / (1024 * 1024), 3),
        "memory_available_mb": _round_or_none(float(vm.available) / (1024 * 1024), 3),
        "swap_percent": _round_or_none(float(swap.percent), 2),
        "process_tree": process_tree,
        "cpu_count_logical": int(psutil.cpu_count(logical=True) or 0),
        "cpu_count_physical": int(psutil.cpu_count(logical=False) or 0),
    }
    if load_avg is not None:
        payload["load_avg"] = load_avg
    if disk is not None:
        payload["disk_io"] = {
            "read_bytes": _round_or_none(disk["read_bytes"], 1),
            "write_bytes": _round_or_none(disk["write_bytes"], 1),
            "read_count": int(disk["read_count"]),
            "write_count": int(disk["write_count"]),
        }
    return payload


def _percentile(values: list[float], p: float) -> float | None:
    if not values:
        return None
    sorted_vals = sorted(values)
    if len(sorted_vals) == 1:
        return sorted_vals[0]
    position = max(0.0, min(1.0, p)) * (len(sorted_vals) - 1)
    lower = int(math.floor(position))
    upper = int(math.ceil(position))
    if lower == upper:
        return sorted_vals[lower]
    fraction = position - lower
    return sorted_vals[lower] + (sorted_vals[upper] - sorted_vals[lower]) * fraction


def _summary_from_samples(
    *,
    baseline: dict[str, Any] | None,
    postrun: dict[str, Any] | None,
    samples: list[dict[str, Any]],
    started_at_utc: str | None,
    stopped_at_utc: str | None,
) -> dict[str, Any]:
    cpu_vals = [
        float(item["cpu_percent_total"])
        for item in samples
        if isinstance(item, dict) and isinstance(item.get("cpu_percent_total"), (int, float))
    ]
    mem_vals = [
        float(item["memory_percent"])
        for item in samples
        if isinstance(item, dict) and isinstance(item.get("memory_percent"), (int, float))
    ]
    proc_rss_vals = [
        float(item.get("process_tree", {}).get("rss_mb_total"))
        for item in samples
        if isinstance(item, dict)
        and isinstance(item.get("process_tree"), dict)
        and isinstance(item.get("process_tree", {}).get("rss_mb_total"), (int, float))
    ]
    proc_count_vals = [
        float(item.get("process_tree", {}).get("process_count"))
        for item in samples
        if isinstance(item, dict)
        and isinstance(item.get("process_tree"), dict)
        and isinstance(item.get("process_tree", {}).get("process_count"), (int, float))
    ]

    started_dt = _parse_iso(started_at_utc)
    stopped_dt = _parse_iso(stopped_at_utc)
    duration_seconds = None
    if started_dt and stopped_dt and stopped_dt >= started_dt:
        duration_seconds = (stopped_dt - started_dt).total_seconds()

    baseline_proc_cpu = _extract_nested_float(baseline, "process_tree", "cpu_seconds_total")
    postrun_proc_cpu = _extract_nested_float(postrun, "process_tree", "cpu_seconds_total")
    proc_cpu_delta = None
    if baseline_proc_cpu is not None and postrun_proc_cpu is not None:
        proc_cpu_delta = max(0.0, postrun_proc_cpu - baseline_proc_cpu)

    disk_read_delta_mb = _extract_delta_mb(baseline, postrun, "disk_io", "read_bytes")
    disk_write_delta_mb = _extract_delta_mb(baseline, postrun, "disk_io", "write_bytes")

    return {
        "sample_count": len(samples),
        "duration_seconds": _round_or_none(duration_seconds, 3),
        "cpu_percent_avg": _round_or_none(sum(cpu_vals) / len(cpu_vals), 3) if cpu_vals else None,
        "cpu_percent_p95": _round_or_none(_percentile(cpu_vals, 0.95), 3),
        "cpu_percent_max": _round_or_none(max(cpu_vals), 3) if cpu_vals else None,
        "memory_percent_avg": _round_or_none(sum(mem_vals) / len(mem_vals), 3) if mem_vals else None,
        "memory_percent_max": _round_or_none(max(mem_vals), 3) if mem_vals else None,
        "process_tree_count_avg": _round_or_none(sum(proc_count_vals) / len(proc_count_vals), 3) if proc_count_vals else None,
        "process_tree_count_p95": _round_or_none(_percentile(proc_count_vals, 0.95), 3),
        "process_tree_count_max": int(max(proc_count_vals)) if proc_count_vals else None,
        "process_tree_rss_mb_peak": _round_or_none(max(proc_rss_vals), 3) if proc_rss_vals else None,
        "process_tree_cpu_seconds_delta": _round_or_none(proc_cpu_delta, 4),
        "disk_read_mb_delta": _round_or_none(disk_read_delta_mb, 3),
        "disk_write_mb_delta": _round_or_none(disk_write_delta_mb, 3),
    }


def _extract_nested_float(payload: dict[str, Any] | None, key: str, nested_key: str) -> float | None:
    if not isinstance(payload, dict):
        return None
    child = payload.get(key)
    if not isinstance(child, dict):
        return None
    value = child.get(nested_key)
    if not isinstance(value, (int, float)):
        return None
    return float(value)


def _extract_delta_mb(
    baseline: dict[str, Any] | None,
    postrun: dict[str, Any] | None,
    key: str,
    nested_key: str,
) -> float | None:
    start = _extract_nested_float(baseline, key, nested_key)
    end = _extract_nested_float(postrun, key, nested_key)
    if start is None or end is None:
        return None
    if end < start:
        return None
    return (end - start) / (1024 * 1024)


def _parse_iso(value: str | None) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw)
    except Exception:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def _collect_time_reference_offsets(
    *,
    urls: tuple[str, ...],
    timeout_seconds: float,
) -> dict[str, Any]:
    probes: list[dict[str, Any]] = []
    offsets: list[float] = []
    for url in urls:
        probes.append(_probe_http_date(url=url, timeout_seconds=timeout_seconds))
        offset_value = probes[-1].get("offset_ms")
        if isinstance(offset_value, (int, float)):
            offsets.append(float(offset_value))

    abs_offsets = [abs(value) for value in offsets]
    sorted_offsets = sorted(offsets)
    median_offset = None
    if sorted_offsets:
        middle = len(sorted_offsets) // 2
        if len(sorted_offsets) % 2 == 1:
            median_offset = sorted_offsets[middle]
        else:
            median_offset = (sorted_offsets[middle - 1] + sorted_offsets[middle]) / 2.0

    return {
        "status": "ok" if offsets else "unavailable",
        "probe_count": len(probes),
        "ok_count": len(offsets),
        "urls": list(urls),
        "median_offset_ms": _round_or_none(median_offset, 3),
        "max_abs_offset_ms": _round_or_none(max(abs_offsets), 3) if abs_offsets else None,
        "probes": probes,
    }


def _probe_http_date(*, url: str, timeout_seconds: float) -> dict[str, Any]:
    probe: dict[str, Any] = {
        "url": url,
        "timeout_seconds": float(max(0.1, timeout_seconds)),
    }
    for method in ("HEAD", "GET"):
        started_at = datetime.now(UTC)
        started_perf = time.perf_counter()
        req = url_request.Request(url, method=method, headers={"User-Agent": "aSTT-comparison/clock-audit"})
        try:
            with url_request.urlopen(req, timeout=max(0.1, timeout_seconds)) as response:
                finished_at = datetime.now(UTC)
                rtt_ms = (time.perf_counter() - started_perf) * 1000.0
                date_header = response.headers.get("Date")
                status_code = int(getattr(response, "status", 200))
                result = _build_http_probe_result(
                    method=method,
                    started_at=started_at,
                    finished_at=finished_at,
                    rtt_ms=rtt_ms,
                    status_code=status_code,
                    date_header=date_header,
                    error_message=None,
                )
                probe.update(result)
                if result.get("status") == "ok" or method == "GET":
                    return probe
        except url_error.HTTPError as exc:
            finished_at = datetime.now(UTC)
            rtt_ms = (time.perf_counter() - started_perf) * 1000.0
            date_header = exc.headers.get("Date") if exc.headers else None
            result = _build_http_probe_result(
                method=method,
                started_at=started_at,
                finished_at=finished_at,
                rtt_ms=rtt_ms,
                status_code=int(exc.code),
                date_header=date_header,
                error_message=f"HTTPError:{exc.code}",
            )
            probe.update(result)
            # Some endpoints reject HEAD; fallback to GET.
            if method == "HEAD" and int(exc.code) in {405, 501}:
                continue
            return probe
        except Exception as exc:
            finished_at = datetime.now(UTC)
            probe.update(
                {
                    "method": method,
                    "status": "error",
                    "status_code": None,
                    "local_request_utc": started_at.isoformat(),
                    "local_response_utc": finished_at.isoformat(),
                    "rtt_ms": _round_or_none((time.perf_counter() - started_perf) * 1000.0, 3),
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
            if method == "GET":
                return probe
    return probe


def _build_http_probe_result(
    *,
    method: str,
    started_at: datetime,
    finished_at: datetime,
    rtt_ms: float,
    status_code: int | None,
    date_header: str | None,
    error_message: str | None,
) -> dict[str, Any]:
    midpoint = started_at + ((finished_at - started_at) / 2)
    payload: dict[str, Any] = {
        "method": method,
        "status": "ok",
        "status_code": status_code,
        "local_request_utc": started_at.isoformat(),
        "local_response_utc": finished_at.isoformat(),
        "local_midpoint_utc": midpoint.isoformat(),
        "rtt_ms": _round_or_none(rtt_ms, 3),
        "error": error_message,
    }
    if not date_header:
        payload["status"] = "missing_date_header"
        return payload
    payload["date_header"] = date_header
    try:
        remote_dt = parsedate_to_datetime(date_header)
    except Exception:
        payload["status"] = "date_parse_failed"
        return payload
    if remote_dt is None:
        payload["status"] = "date_parse_failed"
        return payload
    if remote_dt.tzinfo is None:
        remote_utc = remote_dt.replace(tzinfo=UTC)
    else:
        remote_utc = remote_dt.astimezone(UTC)
    offset_ms = (remote_utc - midpoint).total_seconds() * 1000.0
    payload["remote_time_utc"] = remote_utc.isoformat()
    payload["offset_ms"] = _round_or_none(offset_ms, 3)
    return payload


def _build_clock_audit(
    *,
    started_at_utc: str | None,
    stopped_at_utc: str | None,
    started_monotonic_ns: int | None,
    stopped_monotonic_ns: int | None,
    time_reference_urls: tuple[str, ...],
    time_reference_timeout_seconds: float,
) -> dict[str, Any]:
    started_dt = _parse_iso(started_at_utc)
    stopped_dt = _parse_iso(stopped_at_utc)
    wall_elapsed_seconds = None
    if started_dt and stopped_dt and stopped_dt >= started_dt:
        wall_elapsed_seconds = (stopped_dt - started_dt).total_seconds()

    monotonic_elapsed_seconds = None
    if isinstance(started_monotonic_ns, int) and isinstance(stopped_monotonic_ns, int) and stopped_monotonic_ns >= started_monotonic_ns:
        monotonic_elapsed_seconds = (stopped_monotonic_ns - started_monotonic_ns) / 1_000_000_000.0

    drift_ms = None
    if isinstance(wall_elapsed_seconds, (int, float)) and isinstance(monotonic_elapsed_seconds, (int, float)):
        drift_ms = (float(wall_elapsed_seconds) - float(monotonic_elapsed_seconds)) * 1000.0

    time_reference = _collect_time_reference_offsets(
        urls=time_reference_urls,
        timeout_seconds=max(0.1, float(time_reference_timeout_seconds)),
    )

    return {
        "started_at_utc": started_at_utc,
        "stopped_at_utc": stopped_at_utc,
        "started_monotonic_ns": started_monotonic_ns,
        "stopped_monotonic_ns": stopped_monotonic_ns,
        "wall_elapsed_seconds": _round_or_none(float(wall_elapsed_seconds), 6) if isinstance(wall_elapsed_seconds, (int, float)) else None,
        "monotonic_elapsed_seconds": _round_or_none(float(monotonic_elapsed_seconds), 6) if isinstance(monotonic_elapsed_seconds, (int, float)) else None,
        "wall_vs_monotonic_drift_ms": _round_or_none(float(drift_ms), 3) if isinstance(drift_ms, (int, float)) else None,
        "drift_within_2s": bool(abs(float(drift_ms)) <= 2000.0) if isinstance(drift_ms, (int, float)) else None,
        "time_reference": time_reference,
    }


def _snapshot_static_host_info() -> dict[str, Any]:
    """Capture once-per-run static hardware and environment info."""
    info: dict[str, Any] = {
        "os_system": platform.system(),
        "os_release": platform.release(),
        "os_version": platform.version(),
        "machine": platform.machine(),
        "processor": platform.processor() or None,
        "python_version": sys.version,
        "python_version_short": platform.python_version(),
    }

    # CPU logical/physical count
    if psutil is not None:
        try:
            info["cpu_count_logical"] = int(psutil.cpu_count(logical=True) or 0)
            info["cpu_count_physical"] = int(psutil.cpu_count(logical=False) or 0)
        except Exception:
            pass
        try:
            vm = psutil.virtual_memory()
            info["ram_total_gb"] = round(float(vm.total) / (1024 ** 3), 2)
        except Exception:
            pass

    # GPU: try torch.cuda first, then nvidia-smi as fallback
    gpu_info: list[dict[str, Any]] = []
    try:
        import torch  # type: ignore[import-untyped]
        if torch.cuda.is_available():
            for i in range(torch.cuda.device_count()):
                props = torch.cuda.get_device_properties(i)
                gpu_info.append({
                    "index": i,
                    "name": props.name,
                    "vram_total_gb": round(props.total_memory / (1024 ** 3), 2),
                    "source": "torch.cuda",
                })
    except Exception:
        pass

    if not gpu_info:
        try:
            result = subprocess.run(
                ["nvidia-smi", "--query-gpu=index,name,memory.total", "--format=csv,noheader,nounits"],
                capture_output=True, text=True, timeout=5, check=False,
            )
            if result.returncode == 0:
                for line in result.stdout.strip().splitlines():
                    parts = [p.strip() for p in line.split(",")]
                    if len(parts) >= 3:
                        try:
                            vram_mb = float(parts[2])
                        except ValueError:
                            vram_mb = 0.0
                        gpu_info.append({
                            "index": int(parts[0]),
                            "name": parts[1],
                            "vram_total_gb": round(vram_mb / 1024, 2),
                            "source": "nvidia-smi",
                        })
        except Exception:
            pass

    info["gpu"] = gpu_info if gpu_info else None
    return info


@dataclass
class HostTelemetryRecorder:
    run_id: str
    evaluation_mode: str
    sampling_interval_seconds: float = 1.0
    time_reference_urls: tuple[str, ...] = DEFAULT_TIME_REFERENCE_URLS
    time_reference_timeout_seconds: float = 0.8
    output_path: str | Path | None = None
    root_pid: int = field(default_factory=os.getpid)
    pre_baseline_interval_seconds: float = 2.0
    _stop_event: threading.Event = field(default_factory=threading.Event, init=False)
    _thread: threading.Thread | None = field(default=None, init=False)
    _samples: list[dict[str, Any]] = field(default_factory=list, init=False)
    _pre_baseline_1: dict[str, Any] | None = field(default=None, init=False)
    _pre_baseline_2: dict[str, Any] | None = field(default=None, init=False)
    _baseline: dict[str, Any] | None = field(default=None, init=False)
    _started_at_utc: str | None = field(default=None, init=False)
    _stopped_at_utc: str | None = field(default=None, init=False)
    _started_monotonic_ns: int | None = field(default=None, init=False)
    _stopped_monotonic_ns: int | None = field(default=None, init=False)
    _status: str = field(default="idle", init=False)
    _notes: list[str] = field(default_factory=list, init=False)

    def start(self) -> None:
        self._status = "running"
        if psutil is None:
            self._status = "unavailable"
            self._notes.append("psutil neni dostupny; host telemetry je vypnuta.")
            self._started_at_utc = _utc_now_iso()
            self._started_monotonic_ns = time.monotonic_ns()
            return

        # Prime psutil CPU counters to avoid unstable first sample.
        try:
            psutil.cpu_percent(interval=None)
        except Exception:
            pass

        # Two pre-run snapshots separated by pre_baseline_interval_seconds each.
        # This measures the idle system load before the transcription begins.
        interval = max(0.5, float(self.pre_baseline_interval_seconds))
        self._pre_baseline_1 = _sample_snapshot(root_pid=self.root_pid)
        time.sleep(interval)
        self._pre_baseline_2 = _sample_snapshot(root_pid=self.root_pid)
        time.sleep(interval)

        # Transcription starts here — record the official start timestamp.
        self._started_at_utc = _utc_now_iso()
        self._started_monotonic_ns = time.monotonic_ns()
        self._baseline = _sample_snapshot(root_pid=self.root_pid)
        self._thread = threading.Thread(target=self._sampling_loop, name=f"host-telemetry-{self.run_id}", daemon=True)
        self._thread.start()

    def stop(self, *, run_status: str, error: str | None = None) -> dict[str, Any]:
        self._stopped_at_utc = _utc_now_iso()
        self._stopped_monotonic_ns = time.monotonic_ns()
        if self._thread is not None:
            self._stop_event.set()
            self._thread.join(timeout=max(1.0, float(self.sampling_interval_seconds) * 3.0))
        # Two post-run snapshots separated by pre_baseline_interval_seconds each,
        # mirroring the pre-run measurement to capture HW cooldown after transcription.
        interval = max(0.5, float(self.pre_baseline_interval_seconds))
        postrun_1 = _sample_snapshot(root_pid=self.root_pid) if psutil is not None else None
        if psutil is not None:
            time.sleep(interval)
        postrun_2 = _sample_snapshot(root_pid=self.root_pid) if psutil is not None else None
        postrun = postrun_2  # keep legacy field pointing to the later snapshot
        status = "ok" if psutil is not None else "unavailable"
        clock_audit = _build_clock_audit(
            started_at_utc=self._started_at_utc,
            stopped_at_utc=self._stopped_at_utc,
            started_monotonic_ns=self._started_monotonic_ns,
            stopped_monotonic_ns=self._stopped_monotonic_ns,
            time_reference_urls=self.time_reference_urls,
            time_reference_timeout_seconds=self.time_reference_timeout_seconds,
        )
        payload: dict[str, Any] = {
            "telemetry_version": 1,
            "run_id": self.run_id,
            "evaluation_mode": self.evaluation_mode,
            "collector": "psutil" if psutil is not None else "none",
            "status": status,
            "run_status": run_status,
            "started_at_utc": self._started_at_utc,
            "stopped_at_utc": self._stopped_at_utc,
            "sampling_interval_seconds": float(max(0.2, self.sampling_interval_seconds)),
            "host_static": _snapshot_static_host_info(),
            "pre_baseline_1": self._pre_baseline_1,
            "pre_baseline_2": self._pre_baseline_2,
            "baseline": self._baseline,
            "postrun_1": postrun_1,
            "postrun_2": postrun_2,
            "postrun": postrun,
            "samples": self._samples,
            "clock_audit": clock_audit,
            "summary": _summary_from_samples(
                baseline=self._baseline,
                postrun=postrun,
                samples=self._samples,
                started_at_utc=self._started_at_utc,
                stopped_at_utc=self._stopped_at_utc,
            ),
            "notes": self._notes,
        }
        if error:
            payload["error"] = error
        if self.output_path:
            path = Path(self.output_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json_dumps(payload), encoding="utf-8")
        return payload

    def _sampling_loop(self) -> None:
        while not self._stop_event.is_set():
            sample = _sample_snapshot(root_pid=self.root_pid)
            self._samples.append(sample)
            if self._stop_event.wait(timeout=max(0.2, float(self.sampling_interval_seconds))):
                break


def json_dumps(payload: dict[str, Any]) -> str:
    import json

    return json.dumps(payload, indent=2, ensure_ascii=False)
