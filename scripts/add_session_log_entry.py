from __future__ import annotations

import argparse
import re
from datetime import datetime, timezone
from pathlib import Path


LAST_UPDATED_PATTERN = re.compile(r"^- last_updated_utc:\s*\S+\s*$", re.MULTILINE)


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _update_last_updated(content: str, timestamp: str) -> str:
    if "Doc-Meta:" not in content:
        raise SystemExit("session_log.md missing 'Doc-Meta:' block")

    replacement = f"- last_updated_utc: {timestamp}"
    updated, count = LAST_UPDATED_PATTERN.subn(replacement, content, count=1)
    if count == 0:
        raise SystemExit("session_log.md missing '- last_updated_utc: ...' metadata line")
    return updated


def _build_entry(timestamp: str, title: str, summary: str, impact: str) -> str:
    return (
        "\n---\n\n"
        f"## Session {timestamp} ({title})\n\n"
        "### Summary\n"
        f"- {summary}\n\n"
        "### Impact\n"
        f"- {impact}\n"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Append concise session entry with UTC timestamp.")
    parser.add_argument("--title", required=True, help="Short session title, e.g. docs-guard-fix.")
    parser.add_argument("--summary", required=True, help="One concise summary sentence.")
    parser.add_argument("--impact", required=True, help="One concise impact sentence.")
    parser.add_argument(
        "--path",
        default="docs/session_log.md",
        help="Path to session log markdown file.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Print output instead of writing file.")
    args = parser.parse_args()

    path = Path(args.path)
    if not path.exists():
        raise SystemExit(f"File not found: {path}")

    timestamp = _utc_now()
    content = path.read_text(encoding="utf-8")
    content = _update_last_updated(content, timestamp)
    entry = _build_entry(timestamp, args.title.strip(), args.summary.strip(), args.impact.strip())
    content += entry

    if args.dry_run:
        print(f"timestamp: {timestamp}")
        print(entry)
        return 0

    path.write_text(content, encoding="utf-8")
    print(f"session log updated: {path} ({timestamp})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
