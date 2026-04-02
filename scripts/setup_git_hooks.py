from __future__ import annotations

import subprocess
from pathlib import Path


def _render_pre_commit_hook(python_exe: Path, repo_root: Path) -> str:
    py = python_exe.as_posix()
    repo = repo_root.as_posix()
    return f"""#!{py}
from __future__ import annotations

import subprocess
from pathlib import Path


def _run_step(cmd: list[str], cwd: Path) -> int:
    proc = subprocess.run(cmd, cwd=cwd, check=False)
    return int(proc.returncode)


def main() -> int:
    repo_root = Path(r"{repo}")
    python_exe = Path(r"{py}")
    if not python_exe.exists():
        print("[pre-commit] Missing .venv Python:", python_exe)
        print("[pre-commit] Skipping local checks (CI still enforces guards).")
        return 0

    steps = [
        [str(python_exe), "scripts/specstory_failure_learning.py"],
        [str(python_exe), "scripts/supply_chain_guard.py"],
    ]
    for step in steps:
        code = _run_step(step, cwd=repo_root)
        if code != 0:
            return code
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
"""


def main() -> int:
    repo_root = Path(__file__).resolve().parents[1]
    hooks_path = repo_root / ".git" / "hooks"
    if not hooks_path.exists():
        raise SystemExit(f"Missing git hooks directory: {hooks_path}")

    venv_python = repo_root / ".venv" / "Scripts" / "python.exe"
    if not venv_python.exists():
        raise SystemExit(f"Missing .venv Python for hook bootstrap: {venv_python}")

    pre_commit = hooks_path / "pre-commit"
    pre_commit.write_text(_render_pre_commit_hook(venv_python, repo_root), encoding="utf-8")

    # Ensure default .git/hooks path is used.
    subprocess.run(["git", "config", "--local", "--unset", "core.hooksPath"], cwd=repo_root, check=False)

    # Best effort chmod for POSIX users.
    try:
        current_mode = pre_commit.stat().st_mode
        pre_commit.chmod(current_mode | 0o111)
    except Exception:
        pass

    print(f"Installed local pre-commit hook -> {pre_commit}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
