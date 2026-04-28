#!/usr/bin/env python
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from packages.common.console_io import configure_console_io
from packages.common.runtime_paths import project_root, runtime_root

configure_console_io()

SKIP_DIRS = {
    ".claude",
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".pytest_tmp",
    ".ruff_cache",
    ".venv",
    "__pycache__",
    "dist",
    "node_modules",
    "logs",
    "runtime",
}
SKIP_PREFIXES = (
    ("docs", "backups"),
    ("docs", "Chat__Lost_in_Codex--private"),
)
TEXT_SUFFIXES = {
    ".cmd",
    ".css",
    ".html",
    ".js",
    ".json",
    ".jsonl",
    ".md",
    ".mjs",
    ".ps1",
    ".py",
    ".sh",
    ".toml",
    ".ts",
    ".tsx",
    ".txt",
    ".yaml",
    ".yml",
}
SOURCE_TOPS = {
    "backend",
    "frontend",
    "packages",
    "scripts",
    "tests",
}
ROOT_SOURCE_SUFFIXES = {".cmd", ".sh", ".ps1", ".py"}


@dataclass(frozen=True)
class Finding:
    severity: str
    rule: str
    path: str
    line: int
    text: str


def _rel_parts(path: Path) -> tuple[str, ...]:
    return path.relative_to(ROOT).parts


def _is_skipped(path: Path) -> bool:
    parts = _rel_parts(path)
    if any(part in SKIP_DIRS for part in parts):
        return True
    return any(parts[: len(prefix)] == prefix for prefix in SKIP_PREFIXES)


def _is_text_candidate(path: Path) -> bool:
    if _is_skipped(path):
        return False
    return path.suffix.lower() in TEXT_SUFFIXES


def _is_source_file(path: Path) -> bool:
    rel = path.relative_to(ROOT)
    parts = rel.parts
    if not parts:
        return False
    if parts[0] in SOURCE_TOPS:
        return True
    return len(parts) == 1 and path.suffix.lower() in ROOT_SOURCE_SUFFIXES


def _iter_files() -> Iterable[Path]:
    for path in ROOT.rglob("*"):
        if not path.is_file():
            continue
        if _is_text_candidate(path):
            yield path


def _host_path_patterns() -> list[tuple[str, re.Pattern[str]]]:
    escaped_root_win = re.escape(str(project_root()))
    escaped_root_posix = re.escape(project_root().as_posix())
    return [
        ("current_repo_absolute_path", re.compile(f"{escaped_root_win}|{escaped_root_posix}", re.IGNORECASE)),
        ("windows_user_absolute_path", re.compile(r"\b[A-Za-z]:[\\/]+Users[\\/]+", re.IGNORECASE)),
        ("posix_user_absolute_path", re.compile(r"(^|[\"'=:\s])/(Users|home)/[^\"'\s]+", re.IGNORECASE)),
        ("onedrive_local_path_hint", re.compile(r"\bOneDrive\b|\\Dokumenty\\|/Dokumenty/", re.IGNORECASE)),
    ]


RUNTIME_BYPASS_PATTERNS = [
    ("runtime_root_bypass", re.compile(r"\b(ROOT|PROJECT_ROOT|_PROJECT_ROOT)\s*/\s*['\"]runtime['\"]")),
    ("root_logs_bypass", re.compile(r"\b(ROOT|PROJECT_ROOT|_PROJECT_ROOT)\s*/\s*['\"]logs['\"]")),
]
DANGEROUS_PROCESS_PATTERNS = [
    ("port_kill_without_app_identity", re.compile(r"lsof\s+-ti:8012|kill\s+-9", re.IGNORECASE)),
]


def _severity_for(path: Path, default: str = "warn") -> str:
    return "error" if _is_source_file(path) else default


def audit(*, include_docs: bool = False) -> list[Finding]:
    findings: list[Finding] = []
    host_patterns = _host_path_patterns()

    for path in _iter_files():
        if not include_docs and not _is_source_file(path):
            continue
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        rel = path.relative_to(ROOT).as_posix()
        source_file = _is_source_file(path)

        for line_no, line in enumerate(lines, start=1):
            stripped = line.strip()
            if not stripped:
                continue
            if source_file and stripped.startswith("#"):
                continue

            for rule, pattern in host_patterns:
                if path.name == "portability_audit.py":
                    continue
                if pattern.search(line):
                    findings.append(Finding(_severity_for(path), rule, rel, line_no, stripped[:220]))

            for rule, pattern in RUNTIME_BYPASS_PATTERNS:
                if pattern.search(line):
                    findings.append(Finding("warn", rule, rel, line_no, stripped[:220]))

            if source_file:
                for rule, pattern in DANGEROUS_PROCESS_PATTERNS:
                    if pattern.search(line):
                        findings.append(Finding("error", rule, rel, line_no, stripped[:220]))

    return findings


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Audit portability: hardcoded local paths, runtime-root bypasses, and unsafe web process control."
    )
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    parser.add_argument("--fail-on-warn", action="store_true", help="Treat warnings as failures.")
    parser.add_argument("--include-docs", action="store_true", help="Also scan documentation and other non-source text files.")
    args = parser.parse_args()

    findings = audit(include_docs=bool(args.include_docs))
    errors = [finding for finding in findings if finding.severity == "error"]
    warnings = [finding for finding in findings if finding.severity == "warn"]

    if args.json:
        print(json.dumps({
            "project_root": str(project_root()),
            "runtime_root": str(runtime_root()),
            "errors": len(errors),
            "warnings": len(warnings),
            "findings": [asdict(finding) for finding in findings],
        }, ensure_ascii=False, indent=2))
    else:
        print("portability-audit")
        print(f"project_root={project_root()}")
        print(f"runtime_root={runtime_root()}")
        print(f"errors={len(errors)} warnings={len(warnings)}")
        for finding in findings[:200]:
            print(
                f"{finding.severity.upper():5} {finding.rule}: "
                f"{finding.path}:{finding.line}: {finding.text}"
            )
        if len(findings) > 200:
            print(f"... truncated {len(findings) - 200} finding(s)")

    if errors or (args.fail_on_warn and warnings):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
