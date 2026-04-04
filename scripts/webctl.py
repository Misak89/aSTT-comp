#!/usr/bin/env python
from __future__ import annotations

import argparse
import os
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Iterable


ROOT = Path(__file__).resolve().parent.parent
PORT = 8012
HEALTH_URL = f"http://127.0.0.1:{PORT}/api/health"
WEB_URL = f"http://127.0.0.1:{PORT}"

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from packages.common.console_io import configure_console_io

configure_console_io()

try:
    import psutil
except Exception:
    psutil = None  # type: ignore[assignment]


def _venv_python() -> Path:
    if os.name == "nt":
        return ROOT / ".venv" / "Scripts" / "python.exe"
    return ROOT / ".venv" / "bin" / "python"


def _uvicorn_cmd(*, reload: bool) -> list[str]:
    py = _venv_python()
    if not py.exists():
        raise SystemExit(f"[web] chyba: chybí {py}")
    cmd = [
        str(py),
        "-X",
        "utf8",
        "-m",
        "uvicorn",
        "backend.app.main:app",
        "--host",
        "127.0.0.1",
        "--port",
        str(PORT),
    ]
    if reload:
        cmd.append("--reload")
    return cmd


def _utf8_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    env = dict(os.environ)
    env.setdefault("PYTHONUTF8", "1")
    env.setdefault("PYTHONIOENCODING", "utf-8")
    if extra:
        env.update(extra)
    return env


def _frontend_build_needed() -> bool:
    dist_index = ROOT / "frontend" / "dist" / "index.html"
    if not dist_index.exists():
        return True
    dist_mtime = dist_index.stat().st_mtime
    watch_paths = [
        ROOT / "frontend" / "src",
        ROOT / "frontend" / "index.html",
        ROOT / "frontend" / "vite.config.ts",
        ROOT / "frontend" / "package.json",
    ]
    for p in watch_paths:
        if not p.exists():
            continue
        if p.is_file():
            if p.stat().st_mtime > dist_mtime:
                return True
            continue
        for f in p.rglob("*"):
            if f.is_file() and f.stat().st_mtime > dist_mtime:
                return True
    return False


def _run_frontend_build() -> int:
    print("[web] frontend build...")
    proc = subprocess.run(
        ["npm", "--prefix", "frontend", "run", "build"],
        cwd=str(ROOT),
        env=_utf8_env(),
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    return int(proc.returncode)


def _is_health_up(timeout_s: float = 2.0) -> bool:
    try:
        with urllib.request.urlopen(HEALTH_URL, timeout=timeout_s) as resp:
            return int(getattr(resp, "status", 0)) == 200
    except (urllib.error.URLError, TimeoutError, ValueError, OSError, ConnectionError):
        return False


def _safe_cmdline(proc: "psutil.Process") -> list[str]:
    try:
        return [str(v) for v in proc.cmdline() if v is not None]
    except Exception:
        return []


def _is_uvicorn_token(token: str) -> bool:
    low = str(token).strip().lower().replace("\\", "/")
    return low == "uvicorn" or low.endswith("/uvicorn") or low.endswith("/uvicorn.exe")


def _port_matches_cmdline(cmdline: list[str], port: int) -> bool:
    expected = str(int(port))
    for idx, token in enumerate(cmdline):
        low = str(token).strip().lower()
        if low == "--port" and (idx + 1) < len(cmdline) and str(cmdline[idx + 1]).strip() == expected:
            return True
        if low.startswith("--port=") and low.split("=", 1)[1].strip() == expected:
            return True
    return False


def _is_app_process(proc: "psutil.Process", port: int) -> bool:
    if psutil is None:
        return False
    cmdline = _safe_cmdline(proc)
    if not cmdline:
        return False
    lower = [c.lower() for c in cmdline]
    has_app = any("backend.app.main:app" in c for c in lower)
    has_uvicorn = "uvicorn" in lower or any(_is_uvicorn_token(c) for c in cmdline)
    if not (has_app and has_uvicorn):
        return False
    return _port_matches_cmdline(cmdline, port)


def _app_process_pids(port: int) -> list[int]:
    pids: set[int] = set()
    if psutil is None:
        return []
    try:
        for proc in psutil.process_iter(attrs=["pid"]):
            if _is_app_process(proc, port):
                pids.add(int(proc.pid))
    except Exception:
        return []
    return sorted(pids)


def _listener_pids(port: int) -> list[int]:
    pids: set[int] = set()
    if psutil is None:
        return []
    try:
        for conn in psutil.net_connections(kind="tcp"):
            if not conn.laddr:
                continue
            if int(getattr(conn.laddr, "port", 0)) != int(port):
                continue
            if str(conn.status).upper() != "LISTEN":
                continue
            pid = int(conn.pid) if conn.pid is not None else 0
            if pid > 0:
                pids.add(pid)
    except Exception:
        return []
    return sorted(pids)


def _expand_with_related_app_pids(seed_pids: set[int], port: int) -> set[int]:
    targets = {int(pid) for pid in seed_pids if int(pid) > 0}
    if psutil is None:
        return targets

    queue = list(targets)
    visited: set[int] = set()
    while queue:
        pid = int(queue.pop())
        if pid <= 0 or pid in visited:
            continue
        visited.add(pid)
        try:
            proc = psutil.Process(pid)
        except Exception:
            continue

        try:
            for child in proc.children(recursive=True):
                child_pid = int(child.pid)
                if child_pid > 0 and child_pid not in targets:
                    targets.add(child_pid)
                    queue.append(child_pid)
        except Exception:
            pass

        try:
            parent = proc.parent()
        except Exception:
            parent = None
        while parent is not None:
            try:
                if not _is_app_process(parent, port):
                    break
                parent_pid = int(parent.pid)
            except Exception:
                break
            if parent_pid > 0 and parent_pid not in targets:
                targets.add(parent_pid)
                queue.append(parent_pid)
            try:
                parent = parent.parent()
            except Exception:
                break
    return targets


def _termination_targets(port: int) -> tuple[list[int], list[int], list[int]]:
    listeners = _listener_pids(port)
    app_pids = _app_process_pids(port)
    if app_pids:
        targets = _expand_with_related_app_pids(set(listeners) | set(app_pids), port)
    else:
        targets = set(listeners)
    return sorted(targets), listeners, app_pids


def _terminate_pids(pids: Iterable[int]) -> list[int]:
    target_pids = sorted(set(int(p) for p in pids if int(p) > 0))
    if not target_pids:
        return []

    if psutil is None:
        killed: list[int] = []
        for pid in target_pids:
            try:
                os.kill(pid, signal.SIGTERM)
                killed.append(pid)
            except Exception:
                continue
        return killed

    procs: list["psutil.Process"] = []
    for pid in target_pids:
        try:
            procs.append(psutil.Process(pid))
        except Exception:
            continue

    if not procs:
        return []

    for proc in procs:
        try:
            proc.terminate()
        except Exception:
            pass

    gone, alive = psutil.wait_procs(procs, timeout=2.5)
    if alive:
        for proc in alive:
            try:
                proc.kill()
            except Exception:
                pass
        gone2, _ = psutil.wait_procs(alive, timeout=2.5)
        gone = list(gone) + list(gone2)
    return sorted({int(proc.pid) for proc in gone})


def _wait_until_stopped(timeout_s: float = 6.0) -> tuple[bool, list[int], list[int]]:
    deadline = time.perf_counter() + max(1.0, float(timeout_s))
    remaining_listeners: list[int] = []
    remaining_app: list[int] = []
    while time.perf_counter() < deadline:
        remaining_listeners = _listener_pids(PORT)
        remaining_app = _app_process_pids(PORT)
        if not remaining_listeners and not remaining_app:
            return True, [], []
        _terminate_pids(set(remaining_listeners) | set(remaining_app))
        time.sleep(0.35)
    remaining_listeners = _listener_pids(PORT)
    remaining_app = _app_process_pids(PORT)
    return (not remaining_listeners and not remaining_app), remaining_listeners, remaining_app


def _wait_for_health_up(timeout_s: float = 20.0) -> bool:
    deadline = time.perf_counter() + max(1.0, float(timeout_s))
    while time.perf_counter() < deadline:
        if _is_health_up(timeout_s=1.5):
            return True
        time.sleep(0.4)
    return False


def _wait_for_health_down(timeout_s: float = 5.0) -> bool:
    deadline = time.perf_counter() + max(1.0, float(timeout_s))
    while time.perf_counter() < deadline:
        if not _is_health_up(timeout_s=1.0):
            return True
        time.sleep(0.3)
    return not _is_health_up(timeout_s=1.0)


def _format_pid_list(pids: Iterable[int]) -> str:
    normalized = sorted({int(p) for p in pids if int(p) > 0})
    return ",".join(str(pid) for pid in normalized) if normalized else "-"


def _ensure_ready_for_start() -> int | None:
    listeners: list[int] = []
    app_pids: list[int] = []
    health_up = False
    for _ in range(3):
        health_up = _is_health_up(timeout_s=1.5)
        listeners = _listener_pids(PORT)
        app_pids = _app_process_pids(PORT)
        if health_up or listeners or app_pids:
            break
        time.sleep(0.2)
    if health_up:
        print(f"[web] start: backend uz bezi ({WEB_URL}), start preskocen")
        return 0
    if listeners or app_pids:
        stale = ",".join(str(pid) for pid in sorted(set(listeners) | set(app_pids)))
        print(f"[web] start: stale proces(y) detekovany ({stale}), provadim cleanup")
        rc = _cmd_down()
        if rc != 0:
            return rc
    return None


def _cmd_status() -> int:
    health = "UP" if _is_health_up(timeout_s=2.0) else "DOWN"
    listeners = _listener_pids(PORT)
    app_pids = _app_process_pids(PORT)
    print("[web] status")
    print(f"  health:     {health}")
    print(f"  listeners:  {_format_pid_list(listeners)}")
    print(f"  app_pids:   {_format_pid_list(app_pids)}")
    print(f"  url:        {WEB_URL}")
    return 0


def _cmd_down() -> int:
    targets, _, _ = _termination_targets(PORT)
    if not targets:
        print("[web] stop: nic nebezelo")
        return 0

    killed = _terminate_pids(targets)
    stopped, remaining_listeners, remaining_app = _wait_until_stopped(timeout_s=6.0)

    if killed:
        print(f"[web] stop: ukonceno pids={_format_pid_list(killed)}")
    else:
        print("[web] stop: procesy nereagovaly na terminate/kill")

    if not _wait_for_health_down(timeout_s=3.0):
        print("[web] stop: health endpoint stale odpovida")

    if not stopped:
        print(
            "[web] stop: nepodarilo se uplne zastavit "
            f"(listeners={_format_pid_list(remaining_listeners)}, app={_format_pid_list(remaining_app)})"
        )
        return 1
    return 0


def _prepare_build(build_mode: str) -> int:
    if build_mode == "skip":
        print("[web] frontend build skipped by mode.")
        return 0
    if build_mode == "auto":
        if not _frontend_build_needed():
            print("[web] frontend dist is up to date.")
            return 0
        return _run_frontend_build()
    if build_mode == "force":
        return _run_frontend_build()
    raise SystemExit(f"Unsupported build mode: {build_mode}")


def _cmd_up(*, build_mode: str, reload: bool) -> int:
    preflight = _ensure_ready_for_start()
    if preflight is not None:
        return int(preflight)

    build_rc = _prepare_build(build_mode)
    if build_rc != 0:
        return build_rc

    cmd = _uvicorn_cmd(reload=reload)
    print(f"[web] start: {WEB_URL}")
    return int(
        subprocess.call(
            cmd,
            cwd=str(ROOT),
            env=_utf8_env(),
        )
    )


def _cmd_up_bg(*, build_mode: str, reload: bool, wait_s: float) -> int:
    preflight = _ensure_ready_for_start()
    if preflight is not None:
        return int(preflight)

    build_rc = _prepare_build(build_mode)
    if build_rc != 0:
        return build_rc

    logs_dir = ROOT / "runtime" / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    out_path = logs_dir / "web-up-bg.out.log"
    err_path = logs_dir / "web-up-bg.err.log"
    for path in (out_path, err_path):
        try:
            if path.exists():
                path.unlink(missing_ok=True)
        except PermissionError:
            # File can be locked by viewer/tailer. Fall back to timestamped logs below.
            pass

    def _open_log(path: Path, suffix: str):
        try:
            return path.open("w", encoding="utf-8"), path
        except PermissionError:
            alt = logs_dir / f"web-up-bg.{int(time.time())}.{suffix}.log"
            return alt.open("w", encoding="utf-8"), alt

    cmd = _uvicorn_cmd(reload=reload)
    out_fh, out_path_actual = _open_log(out_path, "out")
    err_fh, err_path_actual = _open_log(err_path, "err")
    popen_kwargs: dict[str, object] = {
        "cwd": str(ROOT),
        "env": _utf8_env(),
        "stdout": out_fh,
        "stderr": err_fh,
    }
    if os.name == "nt":
        # Keep bg server alive even when launcher exits from a managed/job shell.
        creationflags = 0
        for flag_name in ("CREATE_NEW_PROCESS_GROUP", "DETACHED_PROCESS", "CREATE_BREAKAWAY_FROM_JOB"):
            flag_value = getattr(subprocess, flag_name, 0)
            creationflags |= int(flag_value)
        popen_kwargs["creationflags"] = creationflags
    else:
        popen_kwargs["start_new_session"] = True
    with out_fh as out_f, err_fh as err_f:
        proc = subprocess.Popen(cmd, **popen_kwargs)

    if _wait_for_health_up(timeout_s=float(wait_s)):
        listeners = _listener_pids(PORT)
        print(f"[web] start-bg: OK {WEB_URL} (pid={proc.pid}, listeners={_format_pid_list(listeners)})")
        return 0

    print(f"[web] start-bg: backend zatim neodpovida (viz {out_path_actual} a {err_path_actual})")
    return 1


def _cmd_restart(*, build_mode: str, reload: bool) -> int:
    down_rc = _cmd_down()
    if down_rc != 0:
        return down_rc
    time.sleep(0.4)
    return _cmd_up(build_mode=build_mode, reload=reload)


def _cmd_legacy(argv: list[str]) -> int:
    normalized = {str(a).strip().lower() for a in argv}
    skip_build = "-skipfrontendbuild" in normalized or "--skipfrontendbuild" in normalized
    reload = "-reload" in normalized or "--reload" in normalized
    mode = "skip" if skip_build else "auto"
    return _cmd_up(build_mode=mode, reload=reload)


def main() -> int:
    parser = argparse.ArgumentParser(description="UTF-8-safe web app control without PowerShell/CMD logic.")
    parser.add_argument(
        "command",
        nargs="?",
        default="status",
        choices=["status", "down", "up", "up-build", "up-bg", "restart", "legacy"],
        help="Command to execute.",
    )
    parser.add_argument("--reload", action="store_true", help="Run uvicorn with --reload (up/up-build/up-bg/restart).")
    parser.add_argument(
        "--wait-seconds",
        type=float,
        default=20.0,
        help="Health wait timeout for up-bg.",
    )
    args, extras = parser.parse_known_args()

    if args.command == "status":
        return _cmd_status()
    if args.command == "down":
        return _cmd_down()
    if args.command == "up":
        return _cmd_up(build_mode="skip", reload=bool(args.reload))
    if args.command == "up-build":
        return _cmd_up(build_mode="force", reload=bool(args.reload))
    if args.command == "up-bg":
        return _cmd_up_bg(build_mode="skip", reload=bool(args.reload), wait_s=float(args.wait_seconds))
    if args.command == "restart":
        return _cmd_restart(build_mode="skip", reload=bool(args.reload))
    if args.command == "legacy":
        return _cmd_legacy(extras)
    raise SystemExit(f"Unknown command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
