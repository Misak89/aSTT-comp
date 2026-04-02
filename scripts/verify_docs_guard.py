from __future__ import annotations

import argparse
import re
import subprocess
import sys
from typing import Iterable


ZERO_SHA = "0000000000000000000000000000000000000000"

SESSION_LOG = "docs/session_log.md"
ARCH_DOC = "docs/ARCHITECTURE.md"
RUNBOOK_DOC = "docs/RUNBOOK.md"
PLAN_TRACKER_DOC = "docs/PLAN_TRACKER.md"
KNOWN_FAILURES_DOC = "docs/KNOWN_FAILURES.md"
SPECSTORY_REPORT_JSON = "docs/reports/specstory_failures.json"
SPECSTORY_STATE_JSON = "docs/reports/specstory_pattern_state.json"
SPECSTORY_SCRIPT = "scripts/specstory_failure_learning.py"
SUPPLY_CHAIN_DOC = "docs/SECURITY_SUPPLY_CHAIN.md"
OSS_INTAKE_JSON = "docs/reports/oss_intake_register.json"
SUPPLY_CHAIN_SCRIPT = "scripts/supply_chain_guard.py"

DEPENDENCY_PATH_PREFIXES = (
    "backend/requirements",
    "frontend/package.json",
    "frontend/package-lock.json",
)

CORE_DOCS = (
    "README.md",
    "AGENTS.md",
    "CLAUDE.md",
    "CONTRIBUTING.md",
    PLAN_TRACKER_DOC,
    SESSION_LOG,
    ARCH_DOC,
    RUNBOOK_DOC,
)

META_KEYS_REQUIRED = (
    "owner",
    "status",
    "last_updated_utc",
    "review_due_utc",
)
META_STATUS_ALLOWED = {"active", "stub", "deprecated", "archive"}
UTC_TS_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
SESSION_HEADER_ADD_RE = re.compile(r"^\+## Session \d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z\b")

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
    proc = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if proc.returncode != 0:
        print(proc.stdout)
        print(proc.stderr, file=sys.stderr)
        raise SystemExit(proc.returncode)
    return [line.strip().replace("\\", "/") for line in proc.stdout.splitlines() if line.strip()]


def _run_text(cmd: list[str]) -> str:
    proc = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if proc.returncode != 0:
        print(proc.stdout)
        print(proc.stderr, file=sys.stderr)
        raise SystemExit(proc.returncode)
    return proc.stdout


def _show_file(ref: str, path: str) -> str | None:
    proc = subprocess.run(
        ["git", "show", f"{ref}:{path}"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if proc.returncode != 0:
        return None
    return proc.stdout


def _is_code(path: str) -> bool:
    if path.startswith(CODE_PREFIXES):
        return True
    if path.startswith(ROOT_CODE_PREFIXES):
        return True
    if path.lower().endswith(CODE_SUFFIXES) and not path.startswith("docs/"):
        return True
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


def _needs_oss_intake_update(changed: Iterable[str]) -> bool:
    for path in changed:
        if any(path.startswith(prefix) for prefix in DEPENDENCY_PATH_PREFIXES):
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
        alnum = sum(ch.isalnum() for ch in content)
        if alnum < 3:
            continue
        return True
    return False


def _has_added_prefix_line(base: str | None, head: str, path: str, prefix: str) -> bool:
    patch = _get_patch(base, head, path)
    for line in patch.splitlines():
        if line.startswith(("+++", "---", "@@", "diff ", "index ")):
            continue
        if not line.startswith("+"):
            continue
        content = line[1:].strip().lower()
        if content.startswith(prefix.lower()):
            return True
    return False


def _has_new_session_header(base: str | None, head: str, path: str) -> bool:
    patch = _get_patch(base, head, path)
    return any(SESSION_HEADER_ADD_RE.match(line) for line in patch.splitlines())


def _parse_doc_meta(content: str) -> dict[str, str]:
    lines = content.splitlines()
    meta_idx = None
    for idx, line in enumerate(lines[:60]):
        if line.strip() == "Doc-Meta:":
            meta_idx = idx
            break
    if meta_idx is None:
        return {}

    meta: dict[str, str] = {}
    for line in lines[meta_idx + 1 : meta_idx + 20]:
        stripped = line.strip()
        if not stripped:
            continue
        if not stripped.startswith("- "):
            break
        rest = stripped[2:]
        if ":" not in rest:
            continue
        key, value = rest.split(":", 1)
        meta[key.strip()] = value.strip()
    return meta


def _validate_core_doc(path: str, content: str) -> list[str]:
    failures: list[str] = []
    meta = _parse_doc_meta(content)
    if not meta:
        failures.append(f"- {path}: missing 'Doc-Meta:' block.")
        return failures

    for key in META_KEYS_REQUIRED:
        if key not in meta or not meta[key]:
            failures.append(f"- {path}: missing Doc-Meta key '{key}'.")

    status = meta.get("status", "")
    if status and status not in META_STATUS_ALLOWED:
        failures.append(
            f"- {path}: invalid status '{status}', allowed={sorted(META_STATUS_ALLOWED)}."
        )

    for key in ("last_updated_utc", "review_due_utc"):
        value = meta.get(key)
        if value and not UTC_TS_RE.match(value):
            failures.append(
                f"- {path}: invalid {key}='{value}' (expected YYYY-MM-DDTHH:MM:SSZ)."
            )

    if path == "AGENTS.md":
        if meta.get("source_of_truth", "").lower() != "true":
            failures.append("- AGENTS.md: expected Doc-Meta 'source_of_truth: true'.")

    if path == "CLAUDE.md":
        low = content.lower()
        if "stub" not in low or "agents.md" not in low:
            failures.append("- CLAUDE.md: must remain stub pointing to AGENTS.md.")
        if len(content.splitlines()) > 20:
            failures.append("- CLAUDE.md: too long for stub (max 20 lines).")

    return failures


def main() -> int:
    parser = argparse.ArgumentParser(description="Docs guard for pull requests and pushes.")
    parser.add_argument("--base", default=None, help="Base SHA (optional).")
    parser.add_argument("--head", required=True, help="Head SHA.")
    args = parser.parse_args()

    changed = _get_changed(args.base, args.head)
    changed_set = set(changed)
    code_changes = [p for p in changed if _is_code(p)]
    failures: list[str] = []

    # Always validate core docs at head. This prevents silent drift.
    for doc in CORE_DOCS:
        content = _show_file(args.head, doc)
        if content is None:
            failures.append(f"- Missing required core doc in head: {doc}")
            continue
        failures.extend(_validate_core_doc(doc, content))

    # If any core doc changed, require last_updated_utc line change in the patch.
    for doc in CORE_DOCS:
        if doc in changed_set and not _has_added_prefix_line(
            args.base, args.head, doc, "- last_updated_utc:"
        ):
            failures.append(
                f"- {doc}: changed but without updated '- last_updated_utc:' line."
            )

    if code_changes and SESSION_LOG not in changed_set:
        failures.append(
            f"- Missing {SESSION_LOG}: code changed in {len(code_changes)} file(s)."
        )
    if code_changes and SESSION_LOG in changed_set and not _has_substantive_added_lines(
        args.base, args.head, SESSION_LOG
    ):
        failures.append(f"- {SESSION_LOG} changed but without substantive added content.")
    if code_changes and SESSION_LOG in changed_set and not _has_new_session_header(
        args.base, args.head, SESSION_LOG
    ):
        failures.append(
            "- docs/session_log.md must include a new '## Session YYYY-MM-DDTHH:MM:SSZ ...' heading for code changes."
        )

    if _needs_arch_update(code_changes) and ARCH_DOC not in changed_set:
        failures.append(f"- Missing {ARCH_DOC}: architecture/core logic paths changed.")
    if _needs_arch_update(code_changes) and ARCH_DOC in changed_set and not _has_substantive_added_lines(
        args.base, args.head, ARCH_DOC
    ):
        failures.append(f"- {ARCH_DOC} changed but without substantive added content.")

    if _needs_runbook_update(changed) and RUNBOOK_DOC not in changed_set:
        failures.append(f"- Missing {RUNBOOK_DOC}: run/start/ops paths changed.")
    if _needs_runbook_update(changed) and RUNBOOK_DOC in changed_set and not _has_substantive_added_lines(
        args.base, args.head, RUNBOOK_DOC
    ):
        failures.append(f"- {RUNBOOK_DOC} changed but without substantive added content.")

    if _needs_plan_tracker_update(changed) and PLAN_TRACKER_DOC not in changed_set:
        failures.append(f"- Missing {PLAN_TRACKER_DOC}: plan documents changed.")
    if _needs_plan_tracker_update(changed) and PLAN_TRACKER_DOC in changed_set and not _has_substantive_added_lines(
        args.base, args.head, PLAN_TRACKER_DOC
    ):
        failures.append(f"- {PLAN_TRACKER_DOC} changed but without substantive added content.")

    if SPECSTORY_SCRIPT in changed_set:
        if KNOWN_FAILURES_DOC not in changed_set:
            failures.append(
                f"- Missing {KNOWN_FAILURES_DOC}: specstory analytics script changed."
            )
        if SPECSTORY_REPORT_JSON not in changed_set:
            failures.append(
                f"- Missing {SPECSTORY_REPORT_JSON}: specstory analytics script changed."
            )
        if SPECSTORY_STATE_JSON not in changed_set:
            failures.append(
                f"- Missing {SPECSTORY_STATE_JSON}: specstory analytics script changed."
            )

    if _needs_oss_intake_update(changed):
        if OSS_INTAKE_JSON not in changed_set:
            failures.append(
                f"- Missing {OSS_INTAKE_JSON}: dependency files changed."
            )

    if SUPPLY_CHAIN_SCRIPT in changed_set:
        if SUPPLY_CHAIN_DOC not in changed_set:
            failures.append(
                f"- Missing {SUPPLY_CHAIN_DOC}: supply-chain guard script changed."
            )
        if OSS_INTAKE_JSON not in changed_set:
            failures.append(
                f"- Missing {OSS_INTAKE_JSON}: supply-chain guard script changed."
            )

    if failures:
        print("docs-guard: FAILED")
        print("Changed files:")
        if changed:
            for path in changed:
                print(f"  - {path}")
        else:
            print("  - <none>")
        print("")
        print("Required documentation updates were not found:")
        for msg in failures:
            print(msg)
        return 1

    if not changed:
        print("docs-guard: OK (no changed files; core docs validated)")
        return 0

    print("docs-guard: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
