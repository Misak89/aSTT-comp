"""
log_cmd_unix.py - process logger for Linux/macOS (Python 3.14 compatible).

Features:
  - JSONL log with process start/end events
  - explicit logger self start/end lifecycle events
  - pause/resume via control file
  - optional shell history import (bash/zsh/fish)
  - autostart install/uninstall:
      Linux: systemd --user service
      macOS: launchd LaunchAgent

Usage:
  python3 scripts/log_cmd_unix.py
  python3 scripts/log_cmd_unix.py --status
  python3 scripts/log_cmd_unix.py --pause
  python3 scripts/log_cmd_unix.py --resume
  python3 scripts/log_cmd_unix.py --help-short
  python3 scripts/log_cmd_unix.py --install
  python3 scripts/log_cmd_unix.py --uninstall
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import plistlib
import shlex
import shutil
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from packages.common.console_io import configure_console_io

configure_console_io()

import psutil

SERVICE_NAME = 'astt-comp-log-cmd'
LAUNCHD_LABEL = 'com.asttcomp.logcmd'
DEFAULT_SHUTDOWN_REASON = 'normal_exit'


def _platform_tag() -> str:
    if sys.platform.startswith('linux'):
        return 'linux'
    if sys.platform == 'darwin':
        return 'macos'
    return 'unsupported'


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _write(out_file, entry: dict) -> None:
    out_file.write(json.dumps(entry, ensure_ascii=False) + '\n')
    out_file.flush()


def _entry_hash(entry: dict) -> str:
    key = f"{entry.get('source')}:{entry.get('cmd')}:{entry.get('ts')}"
    return hashlib.md5(key.encode()).hexdigest()


def _load_seen_hashes(path: Path) -> set[str]:
    seen: set[str] = set()
    if not path.exists():
        return seen
    with path.open(encoding='utf-8', errors='replace') as f:
        for line in f:
            try:
                seen.add(_entry_hash(json.loads(line)))
            except Exception:
                pass
    return seen


def _pid_path(out_path: Path) -> Path:
    return out_path.parent / 'cmd.pid'


def _pause_path(out_path: Path) -> Path:
    return out_path.parent / 'cmd.paused'


def _write_pid(out_path: Path) -> None:
    _pid_path(out_path).write_text(str(os.getpid()), encoding='utf-8')


def _clear_pid(out_path: Path) -> None:
    _pid_path(out_path).unlink(missing_ok=True)


def _running_pid(out_path: Path) -> int | None:
    pid_file = _pid_path(out_path)
    if not pid_file.exists():
        return None
    try:
        pid = int(pid_file.read_text(encoding='utf-8').strip())
        if not psutil.pid_exists(pid):
            pid_file.unlink(missing_ok=True)
            return None
        cmdline = ' '.join(psutil.Process(pid).cmdline())
        if 'log_cmd_unix.py' in cmdline:
            return pid
    except Exception:
        pass
    pid_file.unlink(missing_ok=True)
    return None


def _is_paused(out_path: Path) -> bool:
    return _pause_path(out_path).exists()


def _set_paused(out_path: Path) -> None:
    _pause_path(out_path).write_text(_now(), encoding='utf-8')


def _clear_paused(out_path: Path) -> None:
    _pause_path(out_path).unlink(missing_ok=True)


def _self_process_info() -> dict:
    proc = psutil.Process(os.getpid())
    cmdline = proc.cmdline()
    cmd = ' '.join(cmdline) if cmdline else f'{sys.executable} {" ".join(sys.argv)}'
    return {
        'pid': proc.pid,
        'name': proc.name(),
        'cmd': cmd,
        'started': datetime.fromtimestamp(proc.create_time(), tz=timezone.utc).isoformat(),
    }


def _write_self_lifecycle(out_file, event: str, self_info: dict, reason: str | None = None) -> None:
    now = _now()
    entry = {
        'ts': now,
        'event': event,
        'source': 'logger',
        'lifecycle': 'self',
        **self_info,
    }
    if event == 'end':
        entry['ended'] = now
    if reason:
        entry['reason'] = reason
    _write(out_file, entry)


def _print_short_help(out_path: Path) -> None:
    print('Short help:', flush=True)
    print('  Control: --status | --pause | --resume | --help-short', flush=True)
    print('  Output : --out /path/to/cmd.jsonl  (default: logs/cmd_unix.jsonl)', flush=True)
    print(f'  Active : {out_path.resolve()}', flush=True)


def request_pause(out_path: Path) -> None:
    _set_paused(out_path)
    pid = _running_pid(out_path)
    print('Logger paused (cmd.paused created).', flush=True)
    print(f'  Control file: {_pause_path(out_path)}', flush=True)
    print(f'  Running instance: {"YES (PID " + str(pid) + ")" if pid else "NO"}', flush=True)


def request_resume(out_path: Path) -> None:
    _clear_paused(out_path)
    pid = _running_pid(out_path)
    print('Logger resumed (cmd.paused removed).', flush=True)
    print(f'  Running instance: {"YES (PID " + str(pid) + ")" if pid else "NO"}', flush=True)


def _run_cmd(args: list[str]) -> tuple[int, str]:
    res = subprocess.run(args, capture_output=True, text=True)
    out = (res.stdout or '') + (res.stderr or '')
    return res.returncode, out.strip()


def _linux_service_path() -> Path:
    return Path.home() / '.config' / 'systemd' / 'user' / f'{SERVICE_NAME}.service'


def _linux_install_autostart(out_path: Path, interval: float) -> None:
    if shutil.which('systemctl') is None:
        print('Cannot install autostart: systemctl not found.', flush=True)
        return

    service_path = _linux_service_path()
    service_path.parent.mkdir(parents=True, exist_ok=True)
    python_exe = str(Path(sys.executable).resolve())
    script_path = str(Path(__file__).resolve())
    out_value = str(out_path.resolve())
    exec_start = (
        f"{shlex.quote(python_exe)} {shlex.quote(script_path)} "
        f"--no-history --interval {interval} --out {shlex.quote(out_value)}"
    )
    unit = (
        "[Unit]\n"
        "Description=aSTT-comp command logger (user)\n"
        "After=default.target\n\n"
        "[Service]\n"
        "Type=simple\n"
        f"ExecStart={exec_start}\n"
        "Restart=always\n"
        "RestartSec=2\n\n"
        "[Install]\n"
        "WantedBy=default.target\n"
    )
    service_path.write_text(unit, encoding='utf-8')

    rc, out = _run_cmd(['systemctl', '--user', 'daemon-reload'])
    if rc != 0:
        print(f'systemctl --user daemon-reload failed:\n{out}', flush=True)
        return
    rc, out = _run_cmd(['systemctl', '--user', 'enable', '--now', f'{SERVICE_NAME}.service'])
    if rc != 0:
        print(f'systemctl --user enable --now failed:\n{out}', flush=True)
        return

    print(f'Autostart installed (Linux systemd user): {service_path}', flush=True)


def _linux_uninstall_autostart() -> None:
    service_path = _linux_service_path()
    if shutil.which('systemctl') is not None:
        _run_cmd(['systemctl', '--user', 'disable', '--now', f'{SERVICE_NAME}.service'])
        _run_cmd(['systemctl', '--user', 'daemon-reload'])
    service_path.unlink(missing_ok=True)
    print('Autostart removed (Linux systemd user).', flush=True)


def _linux_autostart_status() -> str:
    service_path = _linux_service_path()
    if not service_path.exists():
        return 'not installed'
    if shutil.which('systemctl') is None:
        return f'installed at {service_path} (systemctl missing)'
    enabled_rc, _ = _run_cmd(['systemctl', '--user', 'is-enabled', f'{SERVICE_NAME}.service'])
    active_rc, _ = _run_cmd(['systemctl', '--user', 'is-active', f'{SERVICE_NAME}.service'])
    enabled = enabled_rc == 0
    active = active_rc == 0
    return f'installed, enabled={enabled}, active={active}'


def _mac_plist_path() -> Path:
    return Path.home() / 'Library' / 'LaunchAgents' / f'{LAUNCHD_LABEL}.plist'


def _mac_install_autostart(out_path: Path, interval: float) -> None:
    plist_path = _mac_plist_path()
    plist_path.parent.mkdir(parents=True, exist_ok=True)
    python_exe = str(Path(sys.executable).resolve())
    script_path = str(Path(__file__).resolve())
    out_value = str(out_path.resolve())
    std_out = str(out_path.parent / 'cmd-launchd.out.log')
    std_err = str(out_path.parent / 'cmd-launchd.err.log')

    payload = {
        'Label': LAUNCHD_LABEL,
        'ProgramArguments': [
            python_exe,
            script_path,
            '--no-history',
            '--interval',
            str(interval),
            '--out',
            out_value,
        ],
        'RunAtLoad': True,
        'KeepAlive': True,
        'WorkingDirectory': str(PROJECT_ROOT),
        'StandardOutPath': std_out,
        'StandardErrorPath': std_err,
    }
    with plist_path.open('wb') as f:
        plistlib.dump(payload, f)

    uid = str(os.getuid())
    _run_cmd(['launchctl', 'bootout', f'gui/{uid}', str(plist_path)])
    rc, out = _run_cmd(['launchctl', 'bootstrap', f'gui/{uid}', str(plist_path)])
    if rc != 0:
        rc_legacy, out_legacy = _run_cmd(['launchctl', 'load', '-w', str(plist_path)])
        if rc_legacy != 0:
            print(f'launchctl bootstrap/load failed:\n{out}\n{out_legacy}', flush=True)
            return

    _run_cmd(['launchctl', 'enable', f'gui/{uid}/{LAUNCHD_LABEL}'])
    print(f'Autostart installed (macOS launchd): {plist_path}', flush=True)


def _mac_uninstall_autostart() -> None:
    plist_path = _mac_plist_path()
    uid = str(os.getuid())
    _run_cmd(['launchctl', 'bootout', f'gui/{uid}', str(plist_path)])
    _run_cmd(['launchctl', 'unload', '-w', str(plist_path)])
    plist_path.unlink(missing_ok=True)
    print('Autostart removed (macOS launchd).', flush=True)


def _mac_autostart_status() -> str:
    plist_path = _mac_plist_path()
    if not plist_path.exists():
        return 'not installed'
    rc, _ = _run_cmd(['launchctl', 'list', LAUNCHD_LABEL])
    loaded = rc == 0
    return f'installed, loaded={loaded}'


def install_autostart(out_path: Path, interval: float) -> None:
    plat = _platform_tag()
    if plat == 'linux':
        _linux_install_autostart(out_path, interval)
        return
    if plat == 'macos':
        _mac_install_autostart(out_path, interval)
        return
    print(f'Autostart install is unsupported on this platform: {sys.platform}', flush=True)


def uninstall_autostart() -> None:
    plat = _platform_tag()
    if plat == 'linux':
        _linux_uninstall_autostart()
        return
    if plat == 'macos':
        _mac_uninstall_autostart()
        return
    print(f'Autostart uninstall is unsupported on this platform: {sys.platform}', flush=True)


def status(out_path: Path) -> None:
    pid = _running_pid(out_path)
    paused = _is_paused(out_path)
    print(f'Logger running:    {"YES (PID " + str(pid) + ")" if pid else "NO"}', flush=True)
    print(f'Pause mode:        {"YES (paused)" if paused else "NO (active)"}', flush=True)

    plat = _platform_tag()
    if plat == 'linux':
        auto = _linux_autostart_status()
    elif plat == 'macos':
        auto = _mac_autostart_status()
    else:
        auto = f'unsupported platform ({sys.platform})'
    print(f'Autostart:         {auto}', flush=True)

    print(f'Log file:          {out_path.resolve()}', flush=True)
    if out_path.exists():
        size_kb = out_path.stat().st_size // 1024
        mtime = datetime.fromtimestamp(out_path.stat().st_mtime).strftime('%Y-%m-%d %H:%M:%S')
        lines = sum(1 for _ in out_path.open(encoding='utf-8', errors='replace'))
        print(f'  Size:            {size_kb} KB, {lines} records', flush=True)
        print(f'  Last write:      {mtime}', flush=True)
    else:
        print('  (file does not exist yet)', flush=True)


def _history_entry_with_optional_ts(source: str, cmd: str, seq: int, ts: str | None = None) -> dict:
    if ts:
        return {'ts': ts, 'event': 'history', 'source': source, 'seq': seq, 'cmd': cmd}
    return {'ts': f'(unknown - {source} history)', 'event': 'history', 'source': source, 'seq': seq, 'cmd': cmd}


def import_bash_history(out_file, seen: set[str]) -> int:
    path = Path.home() / '.bash_history'
    if not path.exists():
        return 0
    count = 0
    for i, line in enumerate(path.read_text(encoding='utf-8', errors='replace').splitlines()):
        cmd = line.strip()
        if not cmd:
            continue
        entry = _history_entry_with_optional_ts('bash_history', cmd, i + 1)
        h = _entry_hash(entry)
        if h in seen:
            continue
        seen.add(h)
        _write(out_file, entry)
        count += 1
    return count


def import_zsh_history(out_file, seen: set[str]) -> int:
    path = Path.home() / '.zsh_history'
    if not path.exists():
        return 0
    count = 0
    for i, line in enumerate(path.read_text(encoding='utf-8', errors='replace').splitlines()):
        raw = line.strip()
        if not raw:
            continue
        ts_value: str | None = None
        cmd = raw
        # Extended zsh format: ": 1700000000:0;command"
        if raw.startswith(': ') and ';' in raw:
            meta, cmd_part = raw.split(';', 1)
            cmd = cmd_part.strip()
            parts = meta.split(':')
            if len(parts) >= 2:
                epoch_raw = parts[1].strip()
                if epoch_raw.isdigit():
                    ts_value = datetime.fromtimestamp(int(epoch_raw), tz=timezone.utc).isoformat()
        if not cmd:
            continue
        entry = _history_entry_with_optional_ts('zsh_history', cmd, i + 1, ts_value)
        h = _entry_hash(entry)
        if h in seen:
            continue
        seen.add(h)
        _write(out_file, entry)
        count += 1
    return count


def import_fish_history(out_file, seen: set[str]) -> int:
    path = Path.home() / '.local' / 'share' / 'fish' / 'fish_history'
    if not path.exists():
        return 0
    count = 0
    seq = 0
    for line in path.read_text(encoding='utf-8', errors='replace').splitlines():
        text = line.strip()
        if not text.startswith('- cmd:'):
            continue
        cmd = text.replace('- cmd:', '', 1).strip()
        if not cmd:
            continue
        seq += 1
        entry = _history_entry_with_optional_ts('fish_history', cmd, seq)
        h = _entry_hash(entry)
        if h in seen:
            continue
        seen.add(h)
        _write(out_file, entry)
        count += 1
    return count


def _process_key(pid: int, created_ts: float) -> tuple[int, float]:
    return (pid, created_ts)


def _safe_cmdline_text(pid: int) -> str:
    try:
        cmdline = psutil.Process(pid).cmdline()
        return ' '.join(cmdline) if cmdline else ''
    except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
        return ''


def snapshot_meta() -> dict[tuple[int, float], dict]:
    result: dict[tuple[int, float], dict] = {}
    for proc in psutil.process_iter(['pid', 'name', 'create_time']):
        try:
            pid = int(proc.info['pid'])
            created = float(proc.info['create_time'])
            result[_process_key(pid, created)] = {
                'pid': pid,
                'name': proc.info['name'],
                'started': datetime.fromtimestamp(created, tz=timezone.utc).isoformat(),
            }
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess, KeyError, TypeError, ValueError):
            pass
    return result


def live_monitor(out_file, interval: float, out_path: Path) -> None:
    print(f'Live monitoring (interval {interval}s, PID {os.getpid()}) - Ctrl+C to stop', flush=True)
    _write_pid(out_path)
    prev: dict[tuple[int, float], dict] = {}
    was_paused = False

    while True:
        now = _now()
        paused = _is_paused(out_path)
        if paused:
            if not was_paused:
                _write(
                    out_file,
                    {
                        'ts': now,
                        'event': 'pause',
                        'source': 'logger',
                        'lifecycle': 'control',
                        **_self_process_info(),
                        'reason': 'pause_file',
                    },
                )
                print('[||] Logger paused (--resume to continue)', flush=True)
            was_paused = True
            prev = snapshot_meta()
            time.sleep(interval)
            continue

        if was_paused:
            _write(
                out_file,
                {
                    'ts': now,
                    'event': 'resume',
                    'source': 'logger',
                    'lifecycle': 'control',
                    **_self_process_info(),
                    'reason': 'pause_file_removed',
                },
            )
            print('[>>] Logger resumed', flush=True)
            prev = snapshot_meta()
            was_paused = False
            time.sleep(interval)
            continue

        curr_meta = snapshot_meta()
        curr_keys = set(curr_meta.keys())
        prev_keys = set(prev.keys())

        for key in (prev_keys - curr_keys):
            info = prev[key]
            _write(
                out_file,
                {'ts': now, 'event': 'end', 'source': 'live', 'pid': info['pid'], 'ended': now, **info},
            )

        next_prev: dict[tuple[int, float], dict] = {}
        for key in (curr_keys & prev_keys):
            next_prev[key] = prev[key]

        for key in (curr_keys - prev_keys):
            meta = curr_meta[key]
            cmd_text = _safe_cmdline_text(meta['pid'])
            if not cmd_text:
                continue
            info = {**meta, 'cmd': cmd_text}
            next_prev[key] = info
            _write(out_file, {'ts': now, 'event': 'start', 'source': 'live', 'pid': info['pid'], **info})

        prev = next_prev
        time.sleep(interval)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--interval', type=float, default=2.0)
    parser.add_argument('--out', default=str(PROJECT_ROOT / 'logs' / 'cmd_unix.jsonl'))
    parser.add_argument('--no-history', action='store_true')
    parser.add_argument('--history-only', action='store_true')
    parser.add_argument('--pause', action='store_true')
    parser.add_argument('--resume', action='store_true')
    parser.add_argument('--help-short', action='store_true')
    parser.add_argument('--status', action='store_true')
    parser.add_argument('--install', action='store_true')
    parser.add_argument('--uninstall', action='store_true')
    args = parser.parse_args()

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    if args.help_short:
        _print_short_help(out_path)
        return

    if _platform_tag() == 'unsupported':
        print(f'Unsupported platform for this script: {sys.platform}', flush=True)
        sys.exit(2)

    if args.status:
        status(out_path)
        return
    if args.pause:
        request_pause(out_path)
        return
    if args.resume:
        request_resume(out_path)
        return
    if args.install:
        install_autostart(out_path, args.interval)
        return
    if args.uninstall:
        uninstall_autostart()
        return

    existing_pid = _running_pid(out_path)
    if existing_pid:
        print(f'Logger already running (PID {existing_pid}). Use --status.', flush=True)
        return

    seen = _load_seen_hashes(out_path)
    _print_short_help(out_path)
    print(f'Output: {out_path}  (existing records: {len(seen)})', flush=True)

    shutdown_reason = DEFAULT_SHUTDOWN_REASON

    def _signal_handler(signum, _frame):
        nonlocal shutdown_reason
        shutdown_reason = f'signal:{signal.Signals(signum).name}'
        raise KeyboardInterrupt

    signal.signal(signal.SIGINT, _signal_handler)
    if hasattr(signal, 'SIGTERM'):
        signal.signal(signal.SIGTERM, _signal_handler)

    try:
        with out_path.open('a', encoding='utf-8') as out_file:
            _write_self_lifecycle(out_file, 'start', _self_process_info(), reason='boot')
            if not args.no_history:
                n_bash = import_bash_history(out_file, seen)
                n_zsh = import_zsh_history(out_file, seen)
                n_fish = import_fish_history(out_file, seen)
                print(
                    f'History import: bash={n_bash}, zsh={n_zsh}, fish={n_fish}',
                    flush=True,
                )

            if args.history_only:
                return
            live_monitor(out_file, args.interval, out_path)
    except KeyboardInterrupt:
        if shutdown_reason == DEFAULT_SHUTDOWN_REASON:
            shutdown_reason = 'keyboard_interrupt'
        raise
    except Exception as exc:
        shutdown_reason = f'exception:{type(exc).__name__}'
        raise
    finally:
        try:
            with out_path.open('a', encoding='utf-8') as out_file:
                _write_self_lifecycle(out_file, 'end', _self_process_info(), reason=shutdown_reason)
        except Exception:
            pass
        _clear_pid(out_path)


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print('\nStopped.', flush=True)
        sys.exit(0)
