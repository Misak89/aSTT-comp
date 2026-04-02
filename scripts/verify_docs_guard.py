from __future__ import annotations

import argparse
import json
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

LARGE_CHANGE_FILE_THRESHOLD = 25
LARGE_CHANGE_LINE_THRESHOLD = 1800

PLAN_DOC_PREFIXES = (
    "docs/tuning_",
    "docs/mic_sequence_",
)

DOCS_GUARDED_PREFIX = "docs/"
DOCS_IGNORE_PREFIXES = (
    "docs/backups/",
    "docs/Chat__Lost_in_Codex--private/",
)
DOCS_FORMAT_EXTS = (".md", ".json", ".jsonl")
DOC_DATE_SUFFIX_RE = re.compile(r"_\d{4}-\d{2}-\d{2}$")
DOC_DATE_SUFFIX_EXEMPT_BASENAMES = {
    "PLAN_TRACKER.md",
    "plan.md",
    "ARCHITECTURE.md",
    "RUNBOOK.md",
    "DOCS_GOVERNANCE.md",
    "KNOWN_FAILURES.md",
    "session_log.md",
    "test_log.md",
    "SECURITY_SUPPLY_CHAIN.md",
    "oss_intake_register.json",
    "specstory_failures.json",
    "specstory_pattern_state.json",
}
DOC_DATE_SUFFIX_EXEMPT_PREFIXES = (
    "docs/tuning_",
    "docs/mic_sequence_",
)
DOC_TRIPLET_REQUIRED_EXTS = {".md", ".json", ".jsonl"}
DOC_TRIPLET_EXEMPT_PREFIXES = (
    "docs/backups/",
    "docs/Chat__Lost_in_Codex--private/",
    "docs/models/",
    "docs/runs/",
)
DOC_TRIPLET_EXEMPT_BASENAMES = {
    "README.md",
    "AGENTS.md",
    "CLAUDE.md",
    "CONTRIBUTING.md",
    "PLAN_TRACKER.md",
    "session_log.md",
    "ARCHITECTURE.md",
    "RUNBOOK.md",
    "DOCS_GOVERNANCE.md",
    "KNOWN_FAILURES.md",
    "SECURITY_SUPPLY_CHAIN.md",
    "oss_intake_register.json",
    "specstory_failures.json",
    "specstory_pattern_state.json",
}


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


def _is_guarded_doc_format_path(path: str) -> bool:
    low = path.lower()
    if not path.startswith(DOCS_GUARDED_PREFIX):
        return False
    if any(path.startswith(prefix) for prefix in DOCS_IGNORE_PREFIXES):
        return False
    return low.endswith(DOCS_FORMAT_EXTS)


def _is_date_suffix_exempt(path: str) -> bool:
    if any(path.startswith(prefix) for prefix in DOC_DATE_SUFFIX_EXEMPT_PREFIXES):
        return True
    basename = path.rsplit("/", 1)[-1]
    return basename in DOC_DATE_SUFFIX_EXEMPT_BASENAMES


def _has_doc_date_suffix(path: str) -> bool:
    basename = path.rsplit("/", 1)[-1]
    stem = basename.rsplit(".", 1)[0]
    return bool(DOC_DATE_SUFFIX_RE.search(stem))


def _doc_ext(path: str) -> str:
    basename = path.rsplit("/", 1)[-1]
    if "." not in basename:
        return ""
    return "." + basename.rsplit(".", 1)[1].lower()


def _doc_stem(path: str) -> str:
    basename = path.rsplit("/", 1)[-1]
    if "." not in basename:
        return basename.lower()
    return basename.rsplit(".", 1)[0].lower()


def _is_triplet_exempt(path: str) -> bool:
    if any(path.startswith(prefix) for prefix in DOC_TRIPLET_EXEMPT_PREFIXES):
        return True
    basename = path.rsplit("/", 1)[-1]
    return basename in DOC_TRIPLET_EXEMPT_BASENAMES


def _get_head_doc_exts_by_stem(head: str) -> dict[str, set[str]]:
    paths = _run(["git", "ls-tree", "-r", "--name-only", head, "--", "docs"])
    by_stem: dict[str, set[str]] = {}
    for path in paths:
        if not _is_guarded_doc_format_path(path):
            continue
        stem = _doc_stem(path)
        ext = _doc_ext(path)
        if not stem or not ext:
            continue
        by_stem.setdefault(stem, set()).add(ext)
    return by_stem


def _get_changed(base: str | None, head: str) -> list[str]:
    if base and base != ZERO_SHA:
        return _run(["git", "diff", "--name-only", f"{base}...{head}"])
    return _run(["git", "show", "--pretty=", "--name-only", head])


def _get_numstat(base: str | None, head: str) -> list[tuple[str, int, int]]:
    if base and base != ZERO_SHA:
        raw = _run(["git", "diff", "--numstat", f"{base}...{head}"])
    else:
        raw = _run(["git", "show", "--numstat", "--pretty=", head])

    result: list[tuple[str, int, int]] = []
    for line in raw:
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        add_raw, del_raw, path = parts[0], parts[1], parts[2]
        add_n = int(add_raw) if add_raw.isdigit() else 0
        del_n = int(del_raw) if del_raw.isdigit() else 0
        result.append((path, add_n, del_n))
    return result


def _get_name_status(base: str | None, head: str) -> dict[str, str]:
    if base and base != ZERO_SHA:
        raw = _run(["git", "diff", "--name-status", f"{base}...{head}"])
    else:
        raw = _run(["git", "show", "--name-status", "--pretty=", head])

    result: dict[str, str] = {}
    for line in raw:
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        status_raw = parts[0].strip()
        status = status_raw[:1] if status_raw else ""
        if status in {"R", "C"} and len(parts) >= 3:
            path = parts[2].strip()
        else:
            path = parts[1].strip()
        if not status or not path:
            continue
        result[path.replace("\\", "/")] = status
    return result


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


def _validate_new_doc_file_contract(*, path: str, content: str | None) -> list[str]:
    failures: list[str] = []
    if content is None:
        return failures
    basename = path.rsplit("/", 1)[-1]
    low = path.lower()

    if low.endswith(".md"):
        if "Doc-Meta:" not in content:
            return failures
        meta = _parse_doc_meta(content)
        doc_file = str(meta.get("doc_file", "")).strip()
        if doc_file != basename:
            failures.append(
                f"- {path}: new markdown doc with Doc-Meta must set '- doc_file: {basename}'."
            )
        return failures

    if low.endswith(".json"):
        try:
            payload = json.loads(content)
        except Exception:
            failures.append(f"- {path}: JSON parse failed (invalid JSON).")
            return failures
        if isinstance(payload, dict):
            meta = payload.get("doc_meta")
            if isinstance(meta, dict):
                doc_file = str(meta.get("doc_file", "")).strip()
                if doc_file != basename:
                    failures.append(
                        f"- {path}: new JSON doc with doc_meta must set 'doc_meta.doc_file: {basename}'."
                    )
        return failures

    if low.endswith(".jsonl"):
        raw_lines = [line.strip() for line in content.splitlines() if line.strip()]
        if not raw_lines:
            failures.append(f"- {path}: JSONL must not be empty.")
            return failures
        parsed_lines: list[object] = []
        for idx, line in enumerate(raw_lines, start=1):
            try:
                parsed_lines.append(json.loads(line))
            except Exception:
                failures.append(f"- {path}: invalid JSONL at line {idx}.")
                return failures
        first = parsed_lines[0]
        if not isinstance(first, dict):
            failures.append(
                f"- {path}: JSONL first line must be JSON object with doc_meta.doc_file."
            )
            return failures
        meta = first.get("doc_meta")
        if not isinstance(meta, dict):
            failures.append(f"- {path}: JSONL first line must include doc_meta object.")
            return failures
        doc_file = str(meta.get("doc_file", "")).strip()
        if doc_file != basename:
            failures.append(
                f"- {path}: new JSONL doc must set first-line 'doc_meta.doc_file: {basename}'."
            )
        return failures

    return failures


def main() -> int:
    parser = argparse.ArgumentParser(description="Docs guard for pull requests and pushes.")
    parser.add_argument("--base", default=None, help="Base SHA (optional).")
    parser.add_argument("--head", required=True, help="Head SHA.")
    args = parser.parse_args()

    changed = _get_changed(args.base, args.head)
    name_status = _get_name_status(args.base, args.head)
    numstat = _get_numstat(args.base, args.head)
    changed_set = set(changed)
    code_changes = [p for p in changed if _is_code(p)]
    code_change_set = set(code_changes)
    head_doc_exts_by_stem = _get_head_doc_exts_by_stem(args.head)
    code_add = sum(add for path, add, _ in numstat if path in code_change_set)
    code_del = sum(dele for path, _, dele in numstat if path in code_change_set)
    code_churn = code_add + code_del
    large_change_notice = (
        len(code_change_set) >= LARGE_CHANGE_FILE_THRESHOLD
        or code_churn >= LARGE_CHANGE_LINE_THRESHOLD
    )
    failures: list[str] = []
    triplet_targets: dict[str, tuple[str, str]] = {}

    # Always validate core docs at head. This prevents silent drift.
    for doc in CORE_DOCS:
        content = _show_file(args.head, doc)
        if content is None:
            failures.append(f"- Missing required core doc in head: {doc}")
            continue
        failures.extend(_validate_core_doc(doc, content))

    # New docs format contract (guarded docs formats only: .md/.json/.jsonl):
    # - new governed docs should have date suffix in filename unless explicitly exempt
    # - new markdown/json/jsonl docs with metadata should carry doc_file matching the filename
    # - new non-exempt docs artifacts should have the full triplet (.md/.json/.jsonl)
    for path, status in name_status.items():
        if status != "A":
            continue
        if not _is_guarded_doc_format_path(path):
            continue
        if not _is_date_suffix_exempt(path) and not _has_doc_date_suffix(path):
            failures.append(
                f"- {path}: new docs snapshot/report file should end with '_YYYY-MM-DD' before extension."
            )
        content = _show_file(args.head, path)
        failures.extend(_validate_new_doc_file_contract(path=path, content=content))
        if not _is_triplet_exempt(path):
            ext = _doc_ext(path)
            if ext in DOC_TRIPLET_REQUIRED_EXTS:
                key = _doc_stem(path)
                display = path.rsplit("/", 1)[-1].rsplit(".", 1)[0]
                if key and key not in triplet_targets:
                    triplet_targets[key] = (display, path)

    for stem_key, (family_display, sample_path) in sorted(triplet_targets.items()):
        existing_exts = head_doc_exts_by_stem.get(stem_key, set())
        missing_exts = sorted(DOC_TRIPLET_REQUIRED_EXTS - existing_exts)
        if missing_exts:
            missing_names = ", ".join(f"{family_display}{ext}" for ext in missing_exts)
            failures.append(
                f"- {sample_path}: missing required doc triplet companion file(s): {missing_names}."
            )

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
        if large_change_notice:
            print(
                f"docs-guard: NOTICE large change-set (code files={len(code_change_set)}, "
                f"line churn={code_churn}, threshold files>={LARGE_CHANGE_FILE_THRESHOLD}, "
                f"lines>={LARGE_CHANGE_LINE_THRESHOLD}). Consider checkpoint branch and smaller commits."
            )
            print("")
        print("Required documentation updates were not found:")
        for msg in failures:
            print(msg)
        return 1

    if not changed:
        print("docs-guard: OK (no changed files; core docs validated)")
        return 0

    print("docs-guard: OK")
    if large_change_notice:
        print(
            f"docs-guard: NOTICE large change-set (code files={len(code_change_set)}, "
            f"line churn={code_churn}, threshold files>={LARGE_CHANGE_FILE_THRESHOLD}, "
            f"lines>={LARGE_CHANGE_LINE_THRESHOLD}). Consider checkpoint branch and smaller commits."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
