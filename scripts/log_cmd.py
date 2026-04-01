"""
log_cmd.py — loguje všechny cmd/procesy do JSONL.

Zdroje:
  1. PowerShell history — jednorázový import při každém startu
  2. bash / git-bash history (pokud existuje)
  3. Live monitoring — každých N sekund zachytí nové/ukončené procesy

Výstup: logs/cmd.jsonl  (append, deduplikace)
PID soubor: logs/cmd.pid  (pro detekci běžící instance)

Spuštění:
  python scripts/log_cmd.py                     # history import + live od teď
  python scripts/log_cmd.py --no-history        # jen live
  python scripts/log_cmd.py --history-only      # jen import history, pak konec
  python scripts/log_cmd.py --pause             # pozastaví běžící logger
  python scripts/log_cmd.py --resume            # zruší pozastavení loggeru
  python scripts/log_cmd.py --help-short        # stručná nápověda
  python scripts/log_cmd.py --status            # je logger spuštěn?
  python scripts/log_cmd.py --install           # zaregistruj do Task Scheduler (spustí se po přihlášení)
  python scripts/log_cmd.py --uninstall         # odstraň z Task Scheduler
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
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

TASK_NAME = 'aSTT-comp-log-cmd'
DEFAULT_SHUTDOWN_REASON = 'normal_exit'


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _write(f, entry: dict) -> None:
    f.write(json.dumps(entry, ensure_ascii=False) + '\n')
    f.flush()


def _self_process_info() -> dict:
    """Vrátí základní metadata aktuálního logger procesu."""
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
    """Zapíše explicitní start/end záznam samotného loggeru."""
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


def _entry_hash(entry: dict) -> str:
    key = f"{entry.get('source')}:{entry.get('cmd')}:{entry.get('ts')}"
    return hashlib.md5(key.encode()).hexdigest()


def _load_seen_hashes(path: Path) -> set[str]:
    """Přečte existující JSONL a vrátí sadu hash klíčů — pro deduplikaci."""
    seen: set[str] = set()
    if not path.exists():
        return seen
    with path.open(encoding='utf-8') as f:
        for line in f:
            try:
                e = json.loads(line)
                seen.add(_entry_hash(e))
            except Exception:
                pass
    return seen


# ---------------------------------------------------------------------------
# PID soubor — detekce běžící instance
# ---------------------------------------------------------------------------

def _pid_path(out_path: Path) -> Path:
    return out_path.parent / 'cmd.pid'


def _pause_path(out_path: Path) -> Path:
    return out_path.parent / 'cmd.paused'


def _write_pid(out_path: Path) -> None:
    _pid_path(out_path).write_text(str(os.getpid()), encoding='utf-8')


def _clear_pid(out_path: Path) -> None:
    _pid_path(out_path).unlink(missing_ok=True)


def _running_pid(out_path: Path) -> int | None:
    """Vrátí PID pokud logger běží, jinak None."""
    pid_file = _pid_path(out_path)
    if not pid_file.exists():
        return None
    try:
        pid = int(pid_file.read_text().strip())
        if psutil.pid_exists(pid):
            p = psutil.Process(pid)
            # Ověř že jde skutečně o náš skript
            cmd = ' '.join(p.cmdline())
            if 'log_cmd' in cmd:
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


def _print_short_help(out_path: Path) -> None:
    resolved = out_path.resolve()
    print('Stručná nápověda:', flush=True)
    print('  Ovládání: --status | --pause | --resume | --help-short', flush=True)
    print('  Ukládání: --out C:\\cesta\\cmd.jsonl  (výchozí: logs\\cmd.jsonl)', flush=True)
    print(f'  Aktivní výstup: {resolved}', flush=True)


def request_pause(out_path: Path) -> None:
    _set_paused(out_path)
    pid = _running_pid(out_path)
    print('Logger je POZASTAVEN (set cmd.paused).', flush=True)
    print(f'  Soubor: {_pause_path(out_path)}', flush=True)
    print(f'  Běžící instance: {"ANO (PID " + str(pid) + ")" if pid else "NE"}', flush=True)


def request_resume(out_path: Path) -> None:
    _clear_paused(out_path)
    pid = _running_pid(out_path)
    print('Logger je OBNOVEN (cmd.paused odstraněn).', flush=True)
    print(f'  Běžící instance: {"ANO (PID " + str(pid) + ")" if pid else "NE"}', flush=True)


# ---------------------------------------------------------------------------
# Task Scheduler — install / uninstall
# ---------------------------------------------------------------------------

def install_task(out_path: Path, interval: float) -> None:
    python = str(Path(sys.executable).resolve())
    script = str(Path(__file__).resolve())
    out = str(out_path.resolve())
    user = os.environ.get('USERNAME', os.environ.get('USER', ''))

    ps_cmd = f"""
$action   = New-ScheduledTaskAction -Execute '{python}' -Argument '"{script}" --no-history --interval {interval} --out "{out}"'
$trigger  = New-ScheduledTaskTrigger -AtLogOn -User '{user}'
$settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit 0
$principal = New-ScheduledTaskPrincipal -UserId '{user}' -LogonType Interactive -RunLevel Limited
Register-ScheduledTask -TaskName '{TASK_NAME}' -Action $action -Trigger $trigger -Settings $settings -Principal $principal -Force
Write-Host 'OK'
"""
    result = subprocess.run(
        ['powershell', '-NoProfile', '-Command', ps_cmd],
        capture_output=True, text=True
    )
    if 'OK' in result.stdout or result.returncode == 0:
        print(f'Task Scheduler: uloha "{TASK_NAME}" vytvorena.')
        print(f'  Spusti se: pri kazdem prihlaseni uzivatele')
        print(f'  Vystup: {out}')
        print(f'\nSpustit hned bez restartu:')
        print(f'  schtasks /Run /TN {TASK_NAME}')
    else:
        print(f'Chyba: {result.stderr or result.stdout}')


def uninstall_task() -> None:
    result = subprocess.run(
        ['schtasks', '/Delete', '/F', '/TN', TASK_NAME],
        capture_output=True, text=True
    )
    if result.returncode == 0:
        print(f'Task Scheduler: úloha "{TASK_NAME}" odstraněna.')
    else:
        print(f'Chyba (nebo úloha neexistuje): {result.stderr or result.stdout}')


def status(out_path: Path) -> None:
    pid = _running_pid(out_path)
    paused = _is_paused(out_path)
    task_result = subprocess.run(
        ['schtasks', '/Query', '/TN', TASK_NAME, '/FO', 'LIST'],
        capture_output=True, text=True
    )
    task_installed = task_result.returncode == 0

    print(f'Logger běží:       {"ANO (PID " + str(pid) + ")" if pid else "NE"}')
    print(f'Pause režim:       {"ANO (pozastaveno)" if paused else "NE (aktivní)"}')
    print(f'Task Scheduler:    {"zaregistrován — spustí se po přihlášení" if task_installed else "NENÍ zaregistrován"}')
    print(f'Log soubor:        {out_path.resolve()}')
    if out_path.exists():
        size_kb = out_path.stat().st_size // 1024
        mtime = datetime.fromtimestamp(out_path.stat().st_mtime).strftime('%Y-%m-%d %H:%M:%S')
        # Počet záznamů
        lines = sum(1 for _ in out_path.open(encoding='utf-8', errors='replace'))
        print(f'  Velikost:        {size_kb} KB, {lines} záznamů')
        print(f'  Poslední zápis:  {mtime}')
    else:
        print(f'  (soubor neexistuje)')

    if not task_installed:
        print()
        print('Pro auto-start: python scripts/log_cmd.py --install')


# ---------------------------------------------------------------------------
# Historické zdroje
# ---------------------------------------------------------------------------

def import_powershell_history(out_file, seen: set[str]) -> int:
    """Importuje PowerShell PSReadLine history."""
    ps_path = Path(os.environ.get('APPDATA', '')) / \
        'Microsoft' / 'Windows' / 'PowerShell' / 'PSReadLine' / 'ConsoleHost_history.txt'
    if not ps_path.exists():
        return 0

    lines = ps_path.read_text(encoding='utf-8', errors='replace').splitlines()
    count = 0
    # PS history nemá timestamps — použijeme index jako pořadí
    ts_unknown = '(unknown — PS history má jen pořadí, ne čas)'
    for i, line in enumerate(lines):
        line = line.strip()
        if not line:
            continue
        entry = {
            'ts': ts_unknown,
            'event': 'history',
            'source': 'powershell_history',
            'seq': i + 1,
            'cmd': line,
        }
        h = _entry_hash(entry)
        if h in seen:
            continue
        seen.add(h)
        _write(out_file, entry)
        count += 1
    return count


def import_bash_history(out_file, seen: set[str]) -> int:
    """Importuje bash / git-bash history pokud existuje."""
    candidates = [
        Path.home() / '.bash_history',
        Path(os.environ.get('USERPROFILE', '')) / '.bash_history',
    ]
    count = 0
    for path in candidates:
        if not path.exists():
            continue
        lines = path.read_text(encoding='utf-8', errors='replace').splitlines()
        for i, line in enumerate(lines):
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            entry = {
                'ts': '(unknown — bash history)',
                'event': 'history',
                'source': 'bash_history',
                'seq': i + 1,
                'cmd': line,
            }
            h = _entry_hash(entry)
            if h in seen:
                continue
            seen.add(h)
            _write(out_file, entry)
            count += 1
        break  # stačí první nalezený
    return count


# ---------------------------------------------------------------------------
# Live monitoring
# ---------------------------------------------------------------------------

def snapshot() -> dict[int, dict]:
    result: dict[int, dict] = {}
    for p in psutil.process_iter(['pid', 'name', 'cmdline', 'create_time']):
        try:
            cmd = p.info['cmdline']
            if not cmd:
                continue
            result[p.pid] = {
                'name': p.info['name'],
                'cmd': ' '.join(cmd),
                'started': datetime.fromtimestamp(
                    p.info['create_time'], tz=timezone.utc
                ).isoformat(),
            }
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    return result


def live_monitor(out_file, interval: float, out_path: Path) -> None:
    print(f'Live monitoring (interval {interval}s, PID {os.getpid()}) — Ctrl+C pro stop', flush=True)
    _write_pid(out_path)
    prev: dict[int, dict] = {}
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
                print('[||] Logger pozastaven (--resume pro obnovení)', flush=True)
            was_paused = True
            prev = snapshot()
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
            print('[>>] Logger obnoven', flush=True)
            prev = snapshot()
            was_paused = False
            time.sleep(interval)
            continue

        curr = snapshot()

        for pid, info in curr.items():
            if pid not in prev:
                entry = {'ts': now, 'event': 'start', 'source': 'live',
                         'pid': pid, **info}
                _write(out_file, entry)
                print(f"[+] {pid:>6}  {info['name']:<22}  {info['cmd'][:90]}", flush=True)

        for pid, info in prev.items():
            if pid not in curr:
                entry = {'ts': now, 'event': 'end', 'source': 'live',
                         'pid': pid, 'ended': now, **info}
                _write(out_file, entry)
                print(f"[-] {pid:>6}  {info['name']:<22}  {info['cmd'][:90]}", flush=True)

        prev = curr
        time.sleep(interval)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--interval', type=float, default=2.0)
    parser.add_argument('--out', default=str(PROJECT_ROOT / 'logs' / 'cmd.jsonl'))
    parser.add_argument('--no-history', action='store_true')
    parser.add_argument('--history-only', action='store_true')
    parser.add_argument('--pause', action='store_true', help='Pozastav běžící logger')
    parser.add_argument('--resume', action='store_true', help='Obnov pozastavený logger')
    parser.add_argument('--help-short', action='store_true', help='Stručná nápověda')
    parser.add_argument('--status', action='store_true', help='Zkontroluj zda logger běží')
    parser.add_argument('--install', action='store_true', help='Zaregistruj do Task Scheduler')
    parser.add_argument('--uninstall', action='store_true', help='Odstraň z Task Scheduler')
    args = parser.parse_args()

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    if args.status:
        status(out_path)
        return

    if args.help_short:
        _print_short_help(out_path)
        return

    if args.pause:
        request_pause(out_path)
        return

    if args.resume:
        request_resume(out_path)
        return

    if args.install:
        install_task(out_path, args.interval)
        return

    if args.uninstall:
        uninstall_task()
        return

    # Pokud już běží, nevytvářej druhou instanci
    existing_pid = _running_pid(out_path)
    if existing_pid:
        print(f'Logger již běží (PID {existing_pid}). Použij --status pro info.')
        sys.exit(0)

    seen = _load_seen_hashes(out_path)
    _print_short_help(out_path)
    print(f'Výstup: {out_path}  (existující záznamy: {len(seen)})', flush=True)

    shutdown_reason = DEFAULT_SHUTDOWN_REASON

    def _signal_handler(signum, _frame):
        nonlocal shutdown_reason
        shutdown_reason = f'signal:{signal.Signals(signum).name}'
        raise KeyboardInterrupt

    signal.signal(signal.SIGINT, _signal_handler)
    if hasattr(signal, 'SIGTERM'):
        signal.signal(signal.SIGTERM, _signal_handler)
    if hasattr(signal, 'SIGBREAK'):
        signal.signal(signal.SIGBREAK, _signal_handler)

    try:
        with out_path.open('a', encoding='utf-8') as f:
            self_info = _self_process_info()
            _write_self_lifecycle(f, 'start', self_info, reason='boot')
            if not args.no_history:
                n = import_powershell_history(f, seen)
                print(f'PowerShell history: importováno {n} příkazů', flush=True)
                n = import_bash_history(f, seen)
                if n:
                    print(f'Bash history: importováno {n} příkazů', flush=True)

            if args.history_only:
                return

            live_monitor(f, args.interval, out_path)
    except KeyboardInterrupt:
        if shutdown_reason == DEFAULT_SHUTDOWN_REASON:
            shutdown_reason = 'keyboard_interrupt'
        raise
    except Exception as exc:
        shutdown_reason = f'exception:{type(exc).__name__}'
        raise
    finally:
        # Self end je nejlepší možné "vypnutí sebe"; kill -9 / tvrdé ukončení nelze zachytit.
        try:
            with out_path.open('a', encoding='utf-8') as f:
                _write_self_lifecycle(f, 'end', _self_process_info(), reason=shutdown_reason)
        except Exception:
            pass
        _clear_pid(out_path)


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print('\nZastaveno.', flush=True)
        sys.exit(0)
