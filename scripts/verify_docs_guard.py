from __future__ import annotations

import argparse
import subprocess
import sys
from typing import Iterable


ZERO_SHA = "0000000000000000000000000000000000000000"

SESSION_LOG = "docs/session_log.md"
ARCH_DOC = "docs/ARCHITECTURE.md"
RUNBOOK_DOC = "docs/RUNBOOK.md"
PLAN_TRACKER_DOC = "docs/PLAN_TRACKER.md"

ARCH_PREFIXES = (
    "backend/app/services/",
    "backend/app/routers/",
    "packages/",
)
OPS_PREFIXES = (
    "scripts/check_health.py",
    "scripts/preflight.py",
    "web-",
    "start_web_app",
    "backend/app/config.py",
)
CODE_PREFIXES = (
    "backend/",
    "frontend/",
    "packages/",
    "scripts/",
    "tests/",
    ".github/workflows/",
)

ROOT_CODE_PREFIXES = (
    "web-",
    "start_web_app",
)

CODE_SUFFIXES = (
    ".py",
    ".ts",
    ".tsx",
    ".js",
    ".jsx",
    ".cmd",
    ".ps1",
    ".sh",
    ".yml",
    ".yaml",
    ".json",
)

PLAN_DOC_PREFIXES = (
    "docs/tuning_",
    "docs/mic_sequence_",
)


def _run(cmd: list[str]) -> list[str]:
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        print(proc.stdout)
        print(proc.stderr, file=sys.stderr)
        raise SystemExit(proc.returncode)
    return [line.strip().replace("\\", "/") for line in proc.stdout.splitlines() if line.strip()]


def _run_text(cmd: list[str]) -> str:
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        # For "no diff" cases git may return 0 with empty stdout. Non-zero is a real failure.
        print(proc.stdout)
        print(proc.stderr, file=sys.stderr)
        raise SystemExit(proc.returncode)
    return proc.stdout


def _is_code(path: str) -> bool:
    if path.startswith(CODE_PREFIXES):
        return True
    if path.startswith(ROOT_CODE_PREFIXES):
        return True
    if path.lower().endswith(CODE_SUFFIXES) and not path.startswith("docs/"):
        return True
    # Root-level config/code files without extension (rare) are intentionally excluded.
    return False


def _needs_arch_update(changed: Iterable[str]) -> bool:
    return any(path.startswith(ARCH_PREFIXES) for path in changed)


def _needs_runbook_update(changed: Iterable[str]) -> bool:
    for path in changed:
        if path in OPS_PREFIXES:
            return True
        if path.startswith(("web-", "start_web_app")):
            return True
    return False


def _needs_plan_tracker_update(changed: Iterable[str]) -> bool:
    for path in changed:
        low = path.lower()
        if path.startswith(PLAN_DOC_PREFIXES):
            return True
        if low.startswith("docs/") and low.endswith(".md") and "plan" in low:
            return True
        if low.startswith("docs/") and low.endswith(".md") and "roadmap" in low:
            return True
    return False


def _get_changed(base: str | None, head: str) -> list[str]:
    if base and base != ZERO_SHA:
        return _run(["git", "diff", "--name-only", f"{base}...{head}"])
    return _run(["git", "show", "--pretty=", "--name-only", head])


def _get_patch(base: str | None, head: str, path: str) -> str:
    if base and base != ZERO_SHA:
        return _run_text(["git", "diff", "--unified=0", f"{base}...{head}", "--", path])
    return _run_text(["git", "show", "--unified=0", head, "--", path])


def _has_substantive_added_lines(base: str | None, head: str, path: str) -> bool:
    patch = _get_patch(base, head, path)
    if not patch.strip():
        return False
    for line in patch.splitlines():
        if line.startswith(("+++", "---", "@@", "diff ", "index ")):
            continue
        if not line.startswith("+"):
            continue
        content = line[1:].strip()
        if not content:
            continue
        # Ignore trivial punctuation-only edits.
        alnum = sum(ch.isalnum() for ch in content)
        if alnum < 3:
            continue
        return True
    return False


def main() -> int:
    parser = argparse.ArgumentParser(description="Docs guard for pull requests and pushes.")
    parser.add_argument("--base", default=None, help="Base SHA (optional).")
    parser.add_argument("--head", required=True, help="Head SHA.")
    args = parser.parse_args()

    changed = _get_changed(args.base, args.head)
    if not changed:
        print("docs-guard: no changed files")
        return 0

    changed_set = set(changed)
    code_changes = [p for p in changed if _is_code(p)]
    failures: list[str] = []

    if code_changes and SESSION_LOG not in changed_set:
        failures.append(
            f"- Missing {SESSION_LOG}: code changed in {len(code_changes)} file(s)."
        )
    if code_changes and SESSION_LOG in changed_set and not _has_substantive_added_lines(args.base, args.head, SESSION_LOG):
        failures.append(
            f"- {SESSION_LOG} changed but without substantive added content."
        )

    if _needs_arch_update(code_changes) and ARCH_DOC not in changed_set:
        failures.append(
            f"- Missing {ARCH_DOC}: architecture/core logic paths changed."
        )
    if _needs_arch_update(code_changes) and ARCH_DOC in changed_set and not _has_substantive_added_lines(args.base, args.head, ARCH_DOC):
        failures.append(
            f"- {ARCH_DOC} changed but without substantive added content."
        )

    if _needs_runbook_update(changed) and RUNBOOK_DOC not in changed_set:
        failures.append(
            f"- Missing {RUNBOOK_DOC}: run/start/ops paths changed."
        )
    if _needs_runbook_update(changed) and RUNBOOK_DOC in changed_set and not _has_substantive_added_lines(args.base, args.head, RUNBOOK_DOC):
        failures.append(
            f"- {RUNBOOK_DOC} changed but without substantive added content."
        )

    if _needs_plan_tracker_update(changed) and PLAN_TRACKER_DOC not in changed_set:
        failures.append(
            f"- Missing {PLAN_TRACKER_DOC}: plan documents changed."
        )
    if _needs_plan_tracker_update(changed) and PLAN_TRACKER_DOC in changed_set and not _has_substantive_added_lines(args.base, args.head, PLAN_TRACKER_DOC):
        failures.append(
            f"- {PLAN_TRACKER_DOC} changed but without substantive added content."
        )

    if failures:
        print("docs-guard: FAILED")
        print("Changed files:")
        for path in changed:
            print(f"  - {path}")
        print("")
        print("Required documentation updates were not found:")
        for msg in failures:
            print(msg)
        return 1

    print("docs-guard: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
