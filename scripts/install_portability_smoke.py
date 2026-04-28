#!/usr/bin/env python
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from packages.common.runtime_paths import runtime_subpath


def _cmd_display(cmd: list[str]) -> str:
    return " ".join(cmd)


def _run(cmd: list[str], *, env: dict[str, str] | None = None) -> None:
    resolved = shutil.which(cmd[0])
    actual = [resolved, *cmd[1:]] if resolved else cmd
    print(f"+ {_cmd_display(actual)}")
    subprocess.run(actual, cwd=ROOT, env=env, check=True)


def _require_path(path: Path) -> None:
    if not path.exists():
        raise SystemExit(f"Missing required path: {path.relative_to(ROOT)}")


def _require_cmd(name: str, *, required: bool) -> None:
    found = shutil.which(name)
    if found:
        print(f"OK  command {name}: {found}")
        return
    msg = f"Missing command: {name}"
    if required:
        raise SystemExit(msg)
    print(f"WARN {msg}")


def _runtime_override_smoke() -> None:
    override = runtime_subpath("_install_portability_runtime_override")
    env = os.environ.copy()
    env["ASTT_RUNTIME_ROOT"] = str(override)
    code = (
        "from backend.app import config; "
        "from pathlib import Path; "
        "expected = Path(__import__('os').environ['ASTT_RUNTIME_ROOT']).resolve(); "
        "assert config.RUNTIME_ROOT == expected, (config.RUNTIME_ROOT, expected); "
        "assert config.MODEL_STORE_ROOT == expected / 'model_store'; "
        "assert config.LOGGER_LOGS_ROOT == expected / 'logs'; "
        "print(config.RUNTIME_ROOT)"
    )
    _run([sys.executable, "-c", code], env=env)


def _check_repo_markers() -> None:
    for rel in (
        "README.md",
        "AGENTS.md",
        "backend/requirements.txt",
        "frontend/package.json",
        "frontend/package-lock.json",
        "scripts/install_astt_windows.ps1",
        "scripts/install_astt_macos.sh",
        "scripts/portability_audit.py",
        "scripts/webctl.py",
        "scripts/check_health.py",
    ):
        _require_path(ROOT / rel)


def _health_up() -> bool:
    try:
        with urllib.request.urlopen("http://127.0.0.1:8012/api/health", timeout=2) as response:
            return int(response.status) == 200
    except (OSError, urllib.error.URLError):
        return False


def _web_smoke() -> None:
    was_up = _health_up()
    _run([sys.executable, "-X", "utf8", "scripts/webctl.py", "down"])
    try:
        _run([sys.executable, "-X", "utf8", "scripts/webctl.py", "up-bg", "--wait-seconds", "45"])
        _run([sys.executable, "scripts/check_health.py"])
    finally:
        _run([sys.executable, "-X", "utf8", "scripts/webctl.py", "down"])
        if was_up:
            _run([sys.executable, "-X", "utf8", "scripts/webctl.py", "up-bg", "--wait-seconds", "45"])


def _model_smoke(models: list[str], *, run_transcribe_smoke: bool) -> None:
    for model in models:
        _run([sys.executable, "scripts/check_model.py", model])
    if run_transcribe_smoke and models:
        _run([sys.executable, "scripts/smoke_transcribe_13s.py", "--models", *models])


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Cross-platform install/portability smoke checks for clean Windows/macOS installs."
    )
    parser.add_argument(
        "--level",
        choices=("static", "ci", "post-install"),
        default="static",
        help="static=source/path checks, ci=static+build+tests, post-install=ci+web/model smoke.",
    )
    parser.add_argument("--require-ffmpeg", action="store_true", help="Fail when ffmpeg is not on PATH.")
    parser.add_argument("--skip-frontend-build", action="store_true", help="Skip npm frontend build.")
    parser.add_argument("--skip-pytest", action="store_true", help="Skip pytest.")
    parser.add_argument("--skip-web-smoke", action="store_true", help="Skip backend start/health smoke.")
    parser.add_argument("--model", action="append", default=[], help="Model id to validate with scripts/check_model.py.")
    parser.add_argument(
        "--transcribe-smoke",
        action="store_true",
        help="Run scripts/smoke_transcribe_13s.py for --model values. Requires installed model files.",
    )
    parser.add_argument(
        "--pytest-basetemp",
        default=str(runtime_subpath("pytest_basetemp_install_smoke")),
        help="Explicit pytest basetemp. Important on Windows machines with broken user TEMP ACLs.",
    )
    args = parser.parse_args()

    os.chdir(ROOT)
    print(f"Repo root: {ROOT}")
    print(f"Smoke level: {args.level}")

    _check_repo_markers()
    _require_cmd("git", required=True)
    _require_cmd("npm", required=args.level in {"ci", "post-install"})
    _require_cmd("ffmpeg", required=bool(args.require_ffmpeg))

    _run([sys.executable, "scripts/portability_audit.py", "--fail-on-warn"])
    _runtime_override_smoke()
    _run([sys.executable, "-m", "compileall", "backend", "packages", "scripts", "tests"])

    if args.level in {"ci", "post-install"}:
        if not args.skip_frontend_build:
            _run(["npm", "--prefix", "frontend", "run", "build"])
        if not args.skip_pytest:
            _run([sys.executable, "-m", "pytest", "tests", "-q", f"--basetemp={args.pytest_basetemp}"])

    if args.level == "post-install":
        if not args.skip_web_smoke:
            _web_smoke()
        if args.model:
            _model_smoke(list(args.model), run_transcribe_smoke=bool(args.transcribe_smoke))

    print("install-portability-smoke OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
